"""Turn Claude's rough hand-traced Base Flood Elevation (BFE) line points into
clean LineString features.

Unlike zone polygons (one seed point + flood fill is enough), a line can't be
recovered from a single point - Claude must visually trace each BFE line by
reading a handful of points along it directly off the map image (doesn't need
to be pixel-perfect; a handful of vertices following the line's path is
enough). This script then snaps each point to the nearest locally-darkest
pixel (the actual line ink) within a small search window, which cleans up the
inevitable small placement error from reading pixel coordinates visually.

Usage:
    <python> vectorize_bfe.py CROPPED_IMAGE BFE_LINES_JSON TRANSFORM_JSON OUTPUT_DIR \
        [--snap-radius 6]

BFE_LINES_JSON: list of
    {"id": "bfe1", "elevation": 513, "points": [[x0,y0], [x1,y1], ...]}
one entry per continuous line segment/label Claude finds on the map (a single
elevation value, e.g. "513", may appear on more than one disconnected line
segment - give each its own id).

Writes to OUTPUT_DIR:
    bfe_pixel.geojson  - LineStrings in pixel coordinates (source of truth)
    bfe.geojson         - LineStrings reprojected to EPSG:4326
    bfe_preview.png     - downsized overlay for a quick visual sanity check
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from firm_common import load_json, reproject_feature_collection, save_json, snap_to_ridge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cropped_image")
    parser.add_argument("bfe_lines_json")
    parser.add_argument("transform_json")
    parser.add_argument("output_dir")
    parser.add_argument("--snap-radius", type=int, default=6)
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    gray = np.array(Image.open(args.cropped_image).convert("L"))
    lines = load_json(args.bfe_lines_json)
    transform_report = load_json(args.transform_json)
    transform = transform_report.get("transform", transform_report)

    pixel_fc = {"type": "FeatureCollection", "features": []}
    skipped = []
    for line in lines:
        pts = line.get("points", [])
        if len(pts) < 2:
            skipped.append(line.get("id"))
            continue
        snapped = [list(snap_to_ridge(gray, int(x), int(y), args.snap_radius)) for x, y in pts]
        pixel_fc["features"].append(
            {
                "type": "Feature",
                "properties": {"id": line.get("id"), "elevation_ft": line.get("elevation")},
                "geometry": {"type": "LineString", "coordinates": snapped},
            }
        )

    save_json(out_dir / "bfe_pixel.geojson", pixel_fc)
    geo_fc = reproject_feature_collection(pixel_fc, transform)
    save_json(out_dir / "bfe.geojson", geo_fc)

    preview = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for feat in pixel_fc["features"]:
        pts = np.array(feat["geometry"]["coordinates"], dtype=np.int32)
        cv2.polylines(preview, [pts], False, (0, 0, 255), 3)
        mx, my = pts[len(pts) // 2]
        cv2.putText(
            preview, str(feat["properties"]["elevation_ft"]), (int(mx), int(my)),
            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3,
        )
    h, w = preview.shape[:2]
    scale = min(1.0, 1600 / max(h, w))
    preview = cv2.resize(preview, (int(w * scale), int(h * scale)))
    cv2.imwrite(str(out_dir / "bfe_preview.png"), preview)

    report = {
        "n_lines_in": len(lines),
        "n_lines_out": len(pixel_fc["features"]),
        "skipped_ids": skipped,
        "bfe_pixel_geojson": str((out_dir / "bfe_pixel.geojson").resolve()),
        "bfe_geojson": str((out_dir / "bfe.geojson").resolve()),
        "preview": str((out_dir / "bfe_preview.png").resolve()),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
