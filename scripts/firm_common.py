"""Shared helpers for the digitize-firm-map pipeline scripts.

Everything in this pipeline works with two coordinate spaces:
  - "pixel" space: (col, row) / (x, y) in the cropped map raster, origin top-left.
  - "geo" space: (lon, lat) in EPSG:4326.

A georeferencing "transform" is a 6-parameter affine mapping pixel -> geo:
    lon = a*x + b*y + c
    lat = d*x + e*y + f

We keep pixel-space geometry as the source of truth for anything drawn/derived
from the raster (zone polygons, BFE lines), and re-derive geo-space geometry
from it whenever the transform changes (e.g. after a GCP correction). This
means correcting a GCP never requires re-running computer vision extraction.
"""
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image as _PILImage

# FIRM scans are routinely 5000-15000px on a side (a large panel can exceed
# 150 megapixels). These are trusted local files we generated/were handed,
# not untrusted uploads, so disable Pillow's decompression-bomb guard here -
# once, at import time - rather than risk a DecompressionBombError deep in
# some script's Image.open() call. Every script in this skill imports
# firm_common before touching PIL, so this always takes effect first.
_PILImage.MAX_IMAGE_PIXELS = None


REQUIRED_PACKAGES = [
    "rasterio",
    "geopandas",
    "shapely",
    "fiona",
    "cv2",
    "skimage",
    "numpy",
    "PIL",
    "scipy",
    "requests",
    "flask",
]


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def fit_affine(gcps):
    """Least-squares fit of a pixel->geo affine transform.

    gcps: list of dicts with pixel_x, pixel_y, lon, lat.
    Returns (transform_dict, residuals) where transform_dict has keys
    a,b,c,d,e,f and residuals is a list of per-point distances (in degrees)
    between the fitted and actual geo position, aligned by gcp "id".

    Needs at least 3 points (affine has 6 unknowns, 2 equations per point).
    With exactly 3 points the fit is exact (residuals ~0); with more, it's a
    least-squares fit and residuals reveal misreads or bad geocodes.
    """
    n = len(gcps)
    if n < 3:
        raise ValueError(f"Need at least 3 GCPs to fit an affine transform, got {n}")

    # Solve two independent least-squares systems:
    #   lon = a*x + b*y + c
    #   lat = d*x + e*y + f
    # using the normal equations for [x, y, 1] . [a,b,c]^T = lon (and lat).
    sx = sy = sxx = syy = sxy = 0.0
    slon = slat = sxlon = sylon = sxlat = sylat = 0.0
    for g in gcps:
        x, y = g["pixel_x"], g["pixel_y"]
        lon, lat = g["lon"], g["lat"]
        sx += x
        sy += y
        sxx += x * x
        syy += y * y
        sxy += x * y
        slon += lon
        slat += lat
        sxlon += x * lon
        sylon += y * lon
        sxlat += x * lat
        sylat += y * lat

    # Normal equations matrix (shared by both fits):
    # [ sxx sxy sx ] [a]   [sxlon]
    # [ sxy syy sy ] [b] = [sylon]
    # [ sx  sy  n  ] [c]   [slon ]
    M = [
        [sxx, sxy, sx],
        [sxy, syy, sy],
        [sx, sy, float(n)],
    ]

    a, b, c = _solve3(M, [sxlon, sylon, slon])
    d, e, f = _solve3(M, [sxlat, sylat, slat])

    transform = {"a": a, "b": b, "c": c, "d": d, "e": e, "f": f}

    residuals = []
    for g in gcps:
        lon_fit, lat_fit = apply_transform(transform, g["pixel_x"], g["pixel_y"])
        dx = lon_fit - g["lon"]
        dy = lat_fit - g["lat"]
        dist_deg = math.hypot(dx, dy)
        # rough meters-per-degree at this latitude, for a human-readable residual
        meters = dist_deg * 111_320 * math.cos(math.radians(g["lat"]))
        residuals.append(
            {
                "id": g.get("id"),
                "residual_deg": dist_deg,
                "residual_m_approx": abs(meters) if not math.isnan(meters) else None,
            }
        )

    return transform, residuals


def flag_bad_residuals(residuals):
    """Mark each residual dict with likely_bad=True if its distance is more
    than 3x the median - a much larger-than-its-peers residual almost always
    means that one GCP is wrong, not that the map itself is locally distorted.
    Mutates and returns `residuals` for convenient chaining."""
    dists = sorted(r["residual_deg"] for r in residuals)
    median = dists[len(dists) // 2] if dists else 0
    for r in residuals:
        r["likely_bad"] = median > 0 and r["residual_deg"] > 3 * median
    return residuals


def _solve3(M, rhs):
    """Solve a 3x3 linear system via Cramer's rule (no numpy dependency needed here)."""
    def det3(m):
        return (
            m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
        )

    d = det3(M)
    if abs(d) < 1e-12:
        raise ValueError(
            "GCPs are degenerate (e.g. collinear or duplicated) - cannot fit an affine transform"
        )

    result = []
    for col in range(3):
        Mc = [row[:] for row in M]
        for r in range(3):
            Mc[r][col] = rhs[r]
        result.append(det3(Mc) / d)
    return result


def apply_transform(transform, x, y):
    """pixel (x, y) -> geo (lon, lat)"""
    lon = transform["a"] * x + transform["b"] * y + transform["c"]
    lat = transform["d"] * x + transform["e"] * y + transform["f"]
    return lon, lat


def invert_transform(transform):
    """Return a transform mapping geo (lon, lat) -> pixel (x, y)."""
    a, b, c = transform["a"], transform["b"], transform["c"]
    d, e, f = transform["d"], transform["e"], transform["f"]
    det = a * e - b * d
    if abs(det) < 1e-15:
        raise ValueError("Transform is not invertible")
    ia = e / det
    ib = -b / det
    id_ = -d / det
    ie = a / det
    ic = -(ia * c + ib * f)
    if_ = -(id_ * c + ie * f)
    return {"a": ia, "b": ib, "c": ic, "d": id_, "e": ie, "f": if_}


def reproject_geometry_coords(coords, transform):
    """Recursively map GeoJSON-style nested coordinate lists through `transform`.

    Works for Point ([x,y]), LineString/MultiPoint ([[x,y],...]), Polygon
    ([[[x,y],...],...]), etc. - nesting depth is inferred, not tracked.
    """
    if len(coords) > 0 and isinstance(coords[0], (int, float)):
        x, y = coords[0], coords[1]
        lon, lat = apply_transform(transform, x, y)
        return [lon, lat]
    return [reproject_geometry_coords(c, transform) for c in coords]


def reproject_feature_collection(pixel_fc, transform):
    """Take a GeoJSON FeatureCollection in pixel coords, return one in lon/lat."""
    geo_fc = {"type": "FeatureCollection", "features": []}
    for feat in pixel_fc.get("features", []):
        geom = feat["geometry"]
        new_geom = {
            "type": geom["type"],
            "coordinates": reproject_geometry_coords(geom["coordinates"], transform),
        }
        geo_fc["features"].append(
            {"type": "Feature", "properties": feat.get("properties", {}), "geometry": new_geom}
        )
    return geo_fc


def pixel_from_geo_feature_collection(geo_fc, transform):
    """Inverse of reproject_feature_collection - used when the review UI sends
    back edited lon/lat geometry and we need to store it in pixel space too."""
    inv = invert_transform(transform)
    return reproject_feature_collection(geo_fc, inv)


def remove_file_if_exists(path):
    """Delete `path` if present, with a clear error instead of a raw
    traceback if it's locked (e.g. open in QGIS or another viewer) - a real
    scenario for a .gpkg/.tif someone is actively inspecting while Claude
    tries to rebuild it after a review-UI correction."""
    path = Path(path)
    if not path.exists():
        return
    try:
        path.unlink()
    except PermissionError as e:
        raise RuntimeError(
            f"Can't overwrite {path} - it looks like it's open in another program "
            "(a GIS viewer, Explorer preview, etc). Close it there and try again."
        ) from e


def snap_to_ridge(gray, x, y, radius):
    """Snap a hand-traced pixel to the darkest (most ink-like) pixel within
    `radius` of it. Used to clean up Claude's visually-estimated trace points
    for both zone boundaries and BFE lines - a rough trace is enough because
    this pulls each point onto the actual line."""
    h, w = gray.shape
    x0, x1 = max(0, x - radius), min(w, x + radius + 1)
    y0, y1 = max(0, y - radius), min(h, y + radius + 1)
    window = gray[y0:y1, x0:x1]
    if window.size == 0:
        return x, y
    min_idx = np.unravel_index(np.argmin(window), window.shape)
    return x0 + int(min_idx[1]), y0 + int(min_idx[0])
