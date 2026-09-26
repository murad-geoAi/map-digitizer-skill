"""Turn Claude's hand-traced zone boundary points into clean zone polygons.

An earlier version of this script tried to fully automate this with a
flood-fill bounded by a thresholded "ink" mask, on the assumption that zone
boundary lines are printed noticeably bolder than the street grid. Testing on
real FIRM scans showed that's not reliably true - the boundary line is often
the same stroke width as the street grid, so flood fill either leaked through
the real boundary or got stopped by the very first street block. Rather than
fight an unreliable pixel heuristic, this mirrors vectorize_bfe.py's approach:
Claude traces the polygon by eye (the thing vision is actually good at here),
and this script only does the mechanical cleanup - snapping vertices onto the
actual line ink, closing the ring, and reprojecting to geo coordinates.

Usage:
    <python> vectorize_zones.py CROPPED_IMAGE ZONE_POLYGONS_JSON TRANSFORM_JSON OUTPUT_DIR \
        [--snap-radius 6]

ZONE_POLYGONS_JSON: list of
    {"id": "z1", "zone_code": "A0", "points": [[x0,y0], [x1,y1], ...]}
one entry per zone polygon instance - trace points around the zone's actual
boundary line (the bold/dashed line from the sheet's own "KEY TO MAP" legend,
not the street grid), following it all the way around back near the start.
You don't need to close the ring yourself (don't repeat the first point) and
you don't need every vertex pixel-perfect - a reasonable number of points
following the boundary's shape is enough, since each is snapped onto the
nearest ink pixel afterward. A zone code that appears more than once on one
panel for disjoint areas needs a separate entry per occurrence.

Writes to OUTPUT_DIR:
    zones_pixel.geojson  - polygons in pixel coordinates (source of truth)
    zones.geojson         - polygons reprojected to EPSG:4326
    zones_preview.png    - downsized overlay for a quick visual sanity check
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from shapely.geometry import Polygon
from shapely.validation import make_valid

sys.path.insert(0, str(Path(__file__).parent))
from firm_common import load_json, reproject_feature_collection, save_json, snap_to_ridge


def clean_polygon(points):
    """Close the ring, fix self-intersections, return exterior coords or None."""
    ring = list(points)
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    if len(ring) < 4:
        return None
    poly = Polygon(ring)
    if not poly.is_valid:
        poly = make_valid(poly)
    if poly.is_empty:
        return None
    # make_valid can return a GeometryCollection/MultiPolygon on bad input - take the largest polygon part
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    elif poly.geom_type != "Polygon":
        return None
    return [list(map(list, poly.exterior.coords))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cropped_image")
    parser.add_argument("zone_polygons_json")
    parser.add_argument("transform_json")
    parser.add_argument("output_dir")
    parser.add_argument("--snap-radius", type=int, default=6)
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    gray = np.array(Image.open(args.cropped_image).convert("L"))
    zones = load_json(args.zone_polygons_json)
    transform_report = load_json(args.transform_json)
    transform = transform_report.get("transform", transform_report)

    pixel_fc = {"type": "FeatureCollection", "features": []}
    skipped = []
    for zone in zones:
        pts = zone.get("points", [])
        if len(pts) < 3:
            skipped.append(zone.get("id"))
            continue
        snapped = [list(snap_to_ridge(gray, int(x), int(y), args.snap_radius)) for x, y in pts]
        coords = clean_polygon(snapped)
        if coords is None:
            skipped.append(zone.get("id"))
            continue
        pixel_fc["features"].append(
            {
                "type": "Feature",
                "properties": {"id": zone.get("id"), "zone_code": zone.get("zone_code")},
                "geometry": {"type": "Polygon", "coordinates": coords},
            }
        )

    save_json(out_dir / "zones_pixel.geojson", pixel_fc)
    geo_fc = reproject_feature_collection(pixel_fc, transform)
    save_json(out_dir / "zones.geojson", geo_fc)

    preview = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    rng = np.random.default_rng(42)
    for feat in pixel_fc["features"]:
        color = tuple(int(c) for c in rng.integers(60, 255, size=3))
        pts = np.array(feat["geometry"]["coordinates"][0], dtype=np.int32)
        cv2.polylines(preview, [pts], True, color, 4)
        cx, cy = pts[:, 0].mean(), pts[:, 1].mean()
        cv2.putText(
            preview, str(feat["properties"]["zone_code"]), (int(cx), int(cy)),
            cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3,
        )
    h, w = preview.shape[:2]
    scale = min(1.0, 1600 / max(h, w))
    preview = cv2.resize(preview, (int(w * scale), int(h * scale)))
    cv2.imwrite(str(out_dir / "zones_preview.png"), preview)

    report = {
        "n_zones_in": len(zones),
        "n_polygons_out": len(pixel_fc["features"]),
        "skipped_ids": skipped,
        "zones_pixel_geojson": str((out_dir / "zones_pixel.geojson").resolve()),
        "zones_geojson": str((out_dir / "zones.geojson").resolve()),
        "preview": str((out_dir / "zones_preview.png").resolve()),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
