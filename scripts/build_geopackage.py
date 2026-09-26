"""Assemble the final deliverable: a GeoPackage with flood zone polygons, BFE
lines, and the GCPs used (for auditability), sitting next to the
georeferenced GeoTIFF.

Usage:
    <python> build_geopackage.py OUTPUT_GPKG \
        --zones zones.geojson --bfe bfe.geojson --gcps gcps.json \
        [--panel-id 225203 0019 B]

Any of --zones/--bfe/--gcps may be omitted if that layer isn't ready yet -
the script writes whatever layers it's given. Re-run it after each
correction round in the review UI to regenerate the GeoPackage from the
latest data (it overwrites the output file).
"""
import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import shape, Point

sys.path.insert(0, str(Path(__file__).parent))
from firm_common import load_json, remove_file_if_exists


def geojson_to_gdf(geojson_path):
    """Returns None (not an empty GeoDataFrame) when there are zero features -
    e.g. a panel with no BFE lines still gets a valid, empty bfe.geojson from
    vectorize_bfe.py, and GeoDataFrame(geometry="geometry") on a record list
    with zero records has no "geometry" column to find, which raises
    ValueError: Unknown column geometry instead of just meaning "no layer"."""
    fc = load_json(geojson_path)
    records = []
    for feat in fc["features"]:
        props = dict(feat.get("properties", {}))
        props["geometry"] = shape(feat["geometry"])
        records.append(props)
    if not records:
        return None
    return gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")


def gcps_to_gdf(gcps_path):
    """Returns None when no GCP has a resolved lon/lat (e.g. every geocode
    attempt failed) - see geojson_to_gdf for why an empty record list can't
    just become an empty GeoDataFrame here."""
    gcps = load_json(gcps_path)
    records = []
    for g in gcps:
        if "lon" not in g or "lat" not in g:
            continue
        records.append(
            {
                "id": g.get("id"),
                "description": g.get("description"),
                "confidence": g.get("confidence"),
                "pixel_x": g.get("pixel_x"),
                "pixel_y": g.get("pixel_y"),
                "geometry": Point(g["lon"], g["lat"]),
            }
        )
    if not records:
        return None
    return gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_gpkg")
    parser.add_argument("--zones", default=None)
    parser.add_argument("--bfe", default=None)
    parser.add_argument("--gcps", default=None)
    parser.add_argument("--panel-id", default=None)
    args = parser.parse_args()

    out_path = Path(args.output_gpkg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # geopandas appends layers to an existing gpkg; start clean each build
        remove_file_if_exists(out_path)
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    layers_written = []

    if args.zones and Path(args.zones).exists():
        gdf = geojson_to_gdf(args.zones)
        if gdf is not None:
            gdf.to_file(out_path, layer="flood_zones", driver="GPKG")
            layers_written.append(("flood_zones", len(gdf)))

    if args.bfe and Path(args.bfe).exists():
        gdf = geojson_to_gdf(args.bfe)
        if gdf is not None:
            gdf.to_file(out_path, layer="bfe_lines", driver="GPKG")
            layers_written.append(("bfe_lines", len(gdf)))

    if args.gcps and Path(args.gcps).exists():
        gdf = gcps_to_gdf(args.gcps)
        if gdf is not None:
            gdf.to_file(out_path, layer="gcps_used", driver="GPKG")
            layers_written.append(("gcps_used", len(gdf)))

    report = {
        "output_gpkg": str(out_path.resolve()),
        "panel_id": args.panel_id,
        "layers_written": layers_written,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
