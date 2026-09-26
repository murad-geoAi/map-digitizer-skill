"""Resolve plain-text place descriptions (read off the scan by Claude) to
real-world lon/lat via OpenStreetMap's Nominatim geocoder.

Input JSON (a list of GCP candidates Claude proposed while looking at the
scanned map):
    [
      {"id": "gcp1", "pixel_x": 1234, "pixel_y": 567,
       "description": "Canal St & N Claiborne Ave, New Orleans, LA"},
      ...
    ]

Usage:
    <python> geocode_points.py candidates.json gcps_out.json

Output JSON: the same list, each entry augmented with:
    "lon", "lat"        - if geocoding succeeded
    "confidence"        - "high" | "low" | "failed"
    "match_display_name" - what Nominatim actually matched, for a sanity check
    "error"             - present only if geocoding failed

Points with confidence "low" or "failed" should be treated as unusable for
fit_georeference.py until Claude re-describes them more precisely (e.g. add
the city/parish, fix a misread street name) or drops them - a bad GCP quietly
poisons the whole affine fit.

Nominatim's usage policy requires a descriptive User-Agent and no more than
~1 request/second - this script sets both.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from firm_common import load_json, save_json

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "digitize-firm-map-skill/1.0 (Claude Code skill; local GIS digitization tool)"
REQUEST_DELAY_S = 1.1  # stay comfortably under Nominatim's 1 req/sec policy
MAX_RETRIES = 3


def _get_with_retry(params):
    """GET with retry/backoff on rate-limit (429) and transient server errors.

    The public Nominatim instance is meant for light use - a batch of GCPs
    across many panels can occasionally get a 429, and that's a temporary
    "slow down", not a real failure. Respect a Retry-After header if given,
    otherwise back off with increasing delay; only give up after
    MAX_RETRIES, at which point the caller treats it as a normal failure.
    """
    delay = REQUEST_DELAY_S
    for attempt in range(MAX_RETRIES):
        resp = requests.get(
            NOMINATIM_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=15
        )
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == MAX_RETRIES - 1:
                resp.raise_for_status()
            retry_after = resp.headers.get("Retry-After")
            time.sleep(float(retry_after) if retry_after else delay)
            delay *= 2
            continue
        resp.raise_for_status()
        return resp
    return resp  # unreachable, keeps linters happy


def geocode_one(description):
    params = {"q": description, "format": "jsonv2", "limit": 3}
    resp = _get_with_retry(params)
    results = resp.json()
    if not results:
        return {"confidence": "failed", "error": "no results"}

    top = results[0]
    confidence = "high"
    # Nominatim returns multiple plausible matches for ambiguous queries (e.g.
    # a street name that also exists in another city) - if the top two results
    # are far apart, treat this as low-confidence and let Claude disambiguate
    # by adding more context to the description (city, parish, nearby landmark).
    if len(results) > 1:
        try:
            d_lat = abs(float(results[0]["lat"]) - float(results[1]["lat"]))
            d_lon = abs(float(results[0]["lon"]) - float(results[1]["lon"]))
            # ~0.01 degree is roughly 1km at these latitudes - a plausible
            # street-level ambiguity (same name, different neighborhood) is
            # usually much farther apart than that, while two legitimate
            # matches for the same real intersection are usually much closer
            if d_lat > 0.01 or d_lon > 0.01:
                confidence = "low"
        except (KeyError, ValueError):
            pass

    return {
        "lon": float(top["lon"]),
        "lat": float(top["lat"]),
        "confidence": confidence,
        "match_display_name": top.get("display_name"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_json", help="list of {id, pixel_x, pixel_y, description}")
    parser.add_argument("output_json")
    args = parser.parse_args()

    candidates = load_json(args.input_json)

    results = []
    for i, cand in enumerate(candidates):
        if i > 0:
            time.sleep(REQUEST_DELAY_S)
        entry = dict(cand)
        try:
            entry.update(geocode_one(cand["description"]))
        except Exception as e:  # noqa: BLE001 - one bad response (network hiccup,
            # rate-limit page returned as 200, unexpected JSON shape) must not
            # discard every already-geocoded point in this batch; record it as
            # a failure for this point only and keep going
            entry.update({"confidence": "failed", "error": str(e)})
        results.append(entry)

    save_json(args.output_json, results)
    print(json.dumps(results, indent=2))

    n_bad = sum(1 for r in results if r["confidence"] != "high")
    if n_bad:
        print(
            f"\n{n_bad} of {len(results)} point(s) need attention (low confidence or failed).",
            file=sys.stderr,
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
