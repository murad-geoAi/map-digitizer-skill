"""Local review/correction web app for a single FIRM panel's digitization
work-in-progress - the "no QGIS needed" piece of this skill.

Serves a Leaflet map showing the (approximately placed) raster, the proposed
GCPs as draggable markers, and the zone/BFE vector layers as editable
features. Saving in the browser POSTs corrected data back here, which
re-fits the georeferencing transform, re-projects the vector layers, rewrites
the georeferenced GeoTIFF, and rebuilds the GeoPackage - all before replying,
so the map the browser then redraws reflects the corrected, finalized data.

Usage:
    <python> review_server.py WORK_DIR [--port 5000] [--panel-id "225203 0019 B"]

WORK_DIR must contain (relative paths, produced by the earlier pipeline steps):
    cropped.png            - the map-area raster (required)
    gcps.json              - list of GCPs (required)
    transform.json         - {"transform": {...}, "residuals": [...]} (required;
                              output of fit_georeference.py)
    zones_pixel.geojson    - optional
    bfe_pixel.geojson      - optional

Writes back into WORK_DIR on every save:
    gcps.json, transform.json, zones_pixel.geojson, zones.geojson,
    bfe_pixel.geojson, bfe.geojson, georeferenced.tif, <panel-id>.gpkg

Known limitation: the browser preview places the raster as an axis-aligned
image overlay (bounding box of the four corners), so if the fitted transform
has meaningful rotation/shear the on-screen raster and vector layers may look
slightly offset from each other in the review UI even though the actual
exported GeoTIFF (written with the full affine, including rotation/shear) is
correct. This only affects the review preview, not the deliverable files.
"""
import argparse
import json
import sys
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from firm_common import (
    apply_transform,
    fit_affine,
    flag_bad_residuals,
    load_json,
    pixel_from_geo_feature_collection,
    remove_file_if_exists,
    reproject_feature_collection,
    save_json,
)

UI_DIR = Path(__file__).parent.parent / "review-ui"

app = Flask(__name__, static_folder=None)

WORK_DIR = None
PANEL_ID = None


def wp(name):
    return WORK_DIR / name


def empty_fc():
    return {"type": "FeatureCollection", "features": []}


def read_fc(name):
    p = wp(name)
    return load_json(p) if p.exists() else empty_fc()


def compute_bounds(image_size, transform):
    w, h = image_size
    corners = [(0, 0), (w, 0), (0, h), (w, h)]
    lons, lats = [], []
    for x, y in corners:
        lon, lat = apply_transform(transform, x, y)
        lons.append(lon)
        lats.append(lat)
    return [[min(lats), min(lons)], [max(lats), max(lons)]]  # [[south, west], [north, east]]


def current_state():
    gcps = load_json(wp("gcps.json"))
    transform_report = load_json(wp("transform.json"))
    transform = transform_report.get("transform", transform_report)
    residuals = transform_report.get("residuals", [])

    with Image.open(wp("cropped.png")) as im:
        size = im.size

    zones_geo = read_fc("zones.geojson")
    bfe_geo = read_fc("bfe.geojson")

    return {
        "panel_id": PANEL_ID,
        "image_url": "/api/image",
        "image_size": {"width": size[0], "height": size[1]},
        "bounds": compute_bounds(size, transform),
        "gcps": gcps,
        "residuals": residuals,
        "zones": zones_geo,
        "bfe": bfe_geo,
    }


@app.route("/")
def index():
    return send_from_directory(UI_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(UI_DIR, filename)


@app.route("/api/image")
def api_image():
    return send_from_directory(WORK_DIR, "cropped.png")


@app.route("/api/state")
def api_state():
    return jsonify(current_state())


@app.route("/api/save", methods=["POST"])
def api_save():
    # Everything below can fail in ways a browser user can actually trigger -
    # dragging two GCPs onto each other (degenerate fit), a locked output
    # file, a malformed shape from an editing glitch. Always answer with
    # JSON, even on failure: the frontend calls response.json() unconditionally,
    # and Flask's default error page is HTML, which would break that call and
    # leave the user looking at a cryptic "Unexpected token '<'" instead of
    # the actual problem.
    try:
        body = request.get_json(force=True)

        gcps = body.get("gcps", [])
        zones_geo_in = body.get("zones", empty_fc())
        bfe_geo_in = body.get("bfe", empty_fc())

        # the transform in effect BEFORE this save, used to convert any
        # user-reshaped vector geometry (sent back in lon/lat) into our
        # pixel-space source of truth
        prev_report = load_json(wp("transform.json"))
        prev_transform = prev_report.get("transform", prev_report)

        save_json(wp("gcps.json"), gcps)

        zones_pixel = pixel_from_geo_feature_collection(zones_geo_in, prev_transform)
        bfe_pixel = pixel_from_geo_feature_collection(bfe_geo_in, prev_transform)
        save_json(wp("zones_pixel.geojson"), zones_pixel)
        save_json(wp("bfe_pixel.geojson"), bfe_pixel)

        usable = [g for g in gcps if g.get("confidence") != "failed" and "lon" in g and "lat" in g]
        if len(usable) < 3:
            return jsonify({"error": f"Need >= 3 usable GCPs, have {len(usable)}"}), 400

        new_transform, residuals = fit_affine(usable)
        flag_bad_residuals(residuals)
        save_json(wp("transform.json"), {"transform": new_transform, "residuals": residuals})

        zones_geo_out = reproject_feature_collection(zones_pixel, new_transform)
        bfe_geo_out = reproject_feature_collection(bfe_pixel, new_transform)
        save_json(wp("zones.geojson"), zones_geo_out)
        save_json(wp("bfe.geojson"), bfe_geo_out)

        _rewrite_geotiff(new_transform)
        _rebuild_geopackage()

        return jsonify(current_state())

    except ValueError as e:
        # e.g. fit_affine's degenerate-GCPs check - two GCPs dragged onto (or
        # very near) each other, or all remaining ones collinear
        return jsonify({"error": f"Can't fit georeferencing: {e}"}), 400
    except RuntimeError as e:
        # e.g. the .tif/.gpkg is open in another program - already a clear
        # message from remove_file_if_exists
        return jsonify({"error": str(e)}), 500
    except Exception as e:  # noqa: BLE001 - last-resort safety net, see comment above
        return jsonify({"error": f"Unexpected error: {e}"}), 500


def _rewrite_geotiff(transform):
    import rasterio
    from rasterio.transform import Affine

    with rasterio.open(wp("cropped.png")) as src:
        data = src.read()

    rio_transform = Affine(
        transform["a"], transform["b"], transform["c"],
        transform["d"], transform["e"], transform["f"],
    )
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
    out_tif = wp("georeferenced.tif")
    remove_file_if_exists(out_tif)
    with rasterio.open(out_tif, "w", **profile) as dst:
        dst.write(data)


def _rebuild_geopackage():
    import geopandas as gpd
    from shapely.geometry import Point, shape

    out_path = wp(f"{PANEL_ID or 'panel'}.gpkg")
    remove_file_if_exists(out_path)

    zones_fc = read_fc("zones.geojson")
    if zones_fc["features"]:
        recs = [{**f["properties"], "geometry": shape(f["geometry"])} for f in zones_fc["features"]]
        gpd.GeoDataFrame(recs, geometry="geometry", crs="EPSG:4326").to_file(
            out_path, layer="flood_zones", driver="GPKG"
        )

    bfe_fc = read_fc("bfe.geojson")
    if bfe_fc["features"]:
        recs = [{**f["properties"], "geometry": shape(f["geometry"])} for f in bfe_fc["features"]]
        gpd.GeoDataFrame(recs, geometry="geometry", crs="EPSG:4326").to_file(
            out_path, layer="bfe_lines", driver="GPKG"
        )

    gcps = load_json(wp("gcps.json"))
    gcp_recs = [
        {
            "id": g.get("id"),
            "description": g.get("description"),
            "confidence": g.get("confidence"),
            "geometry": Point(g["lon"], g["lat"]),
        }
        for g in gcps
        if "lon" in g and "lat" in g
    ]
    if gcp_recs:
        gpd.GeoDataFrame(gcp_recs, geometry="geometry", crs="EPSG:4326").to_file(
            out_path, layer="gcps_used", driver="GPKG"
        )


def main():
    global WORK_DIR, PANEL_ID
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work_dir")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--panel-id", default=None)
    args = parser.parse_args()

    WORK_DIR = Path(args.work_dir).resolve()
    PANEL_ID = args.panel_id or WORK_DIR.name

    required = ["cropped.png", "gcps.json", "transform.json"]
    missing = [f for f in required if not (WORK_DIR / f).exists()]
    if missing:
        print(json.dumps({"error": f"WORK_DIR is missing required file(s): {missing}"}))
        sys.exit(1)

    print(f"Review UI: http://127.0.0.1:{args.port}  (panel: {PANEL_ID}, work dir: {WORK_DIR})")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
