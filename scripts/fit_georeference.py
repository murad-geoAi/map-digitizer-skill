"""Fit a pixel->lon/lat affine transform from ground control points, report
per-point residuals, and write a georeferenced GeoTIFF from the cropped map
raster.

Usage:
    <python> fit_georeference.py GCPS_JSON CROPPED_IMAGE OUTPUT_TIF [--transform-out transform.json]

GCPS_JSON: list of {id, pixel_x, pixel_y, lon, lat, confidence, ...} - only
entries with confidence != "failed" are used. Typically this is the output of
geocode_points.py, possibly after Claude/the user dropped or fixed bad points.

Prints a JSON report with the fitted transform and residuals. A large
residual on one point (relative to the others) almost always means that GCP
is wrong (misread street name, wrong geocode match) rather than that the map
itself is distorted - check that point first before assuming the fit is as
good as it gets.
"""
import argparse
import json
import sys
from pathlib import Path

import rasterio
from rasterio.transform import Affine

sys.path.insert(0, str(Path(__file__).parent))
from firm_common import fit_affine, flag_bad_residuals, load_json, remove_file_if_exists, save_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gcps_json")
    parser.add_argument("cropped_image")
    parser.add_argument("output_tif")
    parser.add_argument("--transform-out", default=None)
    args = parser.parse_args()

    all_gcps = load_json(args.gcps_json)
    usable = [g for g in all_gcps if g.get("confidence") != "failed" and "lon" in g and "lat" in g]

    if len(usable) < 3:
        print(
            json.dumps(
                {
                    "error": f"Need >= 3 usable GCPs, have {len(usable)}. "
                    "Fix/drop low-confidence or failed points first."
                }
            )
        )
        sys.exit(1)

    transform, residuals = fit_affine(usable)
    flag_bad_residuals(residuals)

    # rasterio's Affine maps (col, row) -> (x, y) as:
    #   x = a*col + b*row + c
    #   y = d*col + e*row + f
    # which matches our transform dict exactly.
    rio_transform = Affine(
        transform["a"], transform["b"], transform["c"],
        transform["d"], transform["e"], transform["f"],
    )

    with rasterio.open(args.cropped_image) as src:
        data = src.read()  # keep all bands as-is (usually 1, grayscale)

    profile = {
        "driver": "GTiff",
        "height": data.shape[1],
        "width": data.shape[2],
        "count": data.shape[0],
        "dtype": data.dtype,
        "crs": "EPSG:4326",
        "transform": rio_transform,
        "compress": "deflate",
    }
    Path(args.output_tif).parent.mkdir(parents=True, exist_ok=True)
    try:
        remove_file_if_exists(args.output_tif)
        with rasterio.open(args.output_tif, "w", **profile) as dst:
            dst.write(data)
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    report = {
        "transform": transform,
        "n_gcps_used": len(usable),
        "n_gcps_dropped": len(all_gcps) - len(usable),
        "residuals": residuals,
        "output_tif": str(Path(args.output_tif).resolve()),
    }

    if args.transform_out:
        save_json(args.transform_out, report)

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
