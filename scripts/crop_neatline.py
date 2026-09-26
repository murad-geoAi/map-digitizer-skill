"""Detect and crop the map area of a scanned FIRM panel, separating it from
the legend/title sidebar that most panels carry on the right edge.

FIRM sheets are typically laid out as one outer neatline (border) around the
whole page, with a vertical rule dividing the wide map area (left) from a
narrower "KEY TO MAP" / title-block sidebar (right). Panels without a legend
(e.g. some inset/continuation panels) may have no sidebar at all - that's
handled as a fallback.

Usage:
    <python> crop_neatline.py INPUT_IMAGE OUTPUT_DIR [--override x0,y0,x1,y1]

Writes to OUTPUT_DIR:
    crop_meta.json   - outer_box, map_box, sidebar_box (or null), in FULL-
                        RESOLUTION pixel coords
    cropped.png      - just the map_box region, full resolution
    preview.png      - the whole page downsized (max dim 1600px) with the
                        detected boxes drawn on it, for Claude to visually
                        sanity-check before proceeding

--override takes coordinates in the DOWNSIZED PREVIEW's pixel space (i.e.
exactly what you'd read by eye off preview.png) and scales them up to full
resolution internally - so you can look at preview.png, see the green box is
wrong, and pass corrected corners straight off that image without doing any
scale math yourself.
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from firm_common import save_json


def load_grayscale(path):
    """Load via PIL (handles compressed/scanned TIFFs cv2 chokes on), return
    an 8-bit grayscale numpy array."""
    im = Image.open(path).convert("L")
    return np.array(im)


def find_outer_box(gray, dark_thresh=200, min_ink_fraction=0.002):
    """Bounding box of all 'ink' (anything darker than dark_thresh), i.e. the
    full printed extent of the page including neatline, map content, and
    sidebar. Falls back to the whole image if nothing qualifies.

    dark_thresh=200: these scans are white/cream paper (near 255) with black
    ink - 200 sits well below typical paper-grain/scan-noise brightness while
    still catching faded print. min_ink_fraction=0.002: a row/column counts as
    part of the printed page if at least ~0.2% of it is ink - enough to catch
    a thin neatline rule, low enough to ignore stray scan speckle.
    """
    dark = gray < dark_thresh
    cols_ink = dark.mean(axis=0)
    rows_ink = dark.mean(axis=1)

    col_idx = np.where(cols_ink > min_ink_fraction)[0]
    row_idx = np.where(rows_ink > min_ink_fraction)[0]

    if len(col_idx) == 0 or len(row_idx) == 0:
        h, w = gray.shape
        return (0, 0, w, h)

    return (int(col_idx[0]), int(row_idx[0]), int(col_idx[-1]) + 1, int(row_idx[-1]) + 1)


def find_vertical_divider(gray, outer_box, search_frac=(0.55, 0.95), line_thresh=0.6, dark_thresh=150):
    """Look for a strong vertical rule in the right portion of the outer box,
    which typically separates the map area from the legend/title sidebar.

    Returns (divider_x, best_frac): divider_x is the divider's x coordinate
    (absolute, in full-image pixels), or None if nothing reached line_thresh;
    best_frac is the strongest column's dark-fraction score regardless, so a
    near-miss (e.g. a real divider that only reached 0.59 against a busy/dark
    map background) is visible in crop_meta.json instead of silently
    indistinguishable from "no divider-like column existed at all".

    search_frac=(0.55, 0.95): the sidebar is a title-block/legend, always the
    narrower right portion of these panels in every sample layout seen so far
    - search doesn't start until well past the map's own centerline so a
    normal street-grid line can't be mistaken for it. line_thresh=0.6: the
    divider is a solid rule spanning the full page height, so a genuine one
    lights up almost the whole column; dark_thresh=150 is stricter than
    find_outer_box's 200 because this is a same-column-vs-rest-of-column
    comparison, not a page-vs-blank-margin one, so a lower bar for "ink" here
    would let heavily inked/shaded map backgrounds (see 2252030019C) trip it.
    These are tuned CV heuristics, not guaranteed to generalize - that's why
    SKILL.md has Claude visually check preview.png and re-crop with --override
    when the sidebar box is missing or wrong, rather than trusting this blind.
    """
    x0, y0, x1, y1 = outer_box
    width = x1 - x0
    height = y1 - y0
    if width <= 0 or height <= 0:
        return None, 0.0

    search_x0 = x0 + int(width * search_frac[0])
    search_x1 = x0 + int(width * search_frac[1])
    region = gray[y0:y1, search_x0:search_x1]
    dark = region < dark_thresh

    # fraction of the box height that is 'dark' for each column in the search band
    col_dark_frac = dark.mean(axis=0)
    best_frac = float(col_dark_frac.max()) if col_dark_frac.size else 0.0
    candidates = np.where(col_dark_frac > line_thresh)[0]
    if len(candidates) == 0:
        return None, best_frac

    # take the leftmost strong candidate (the actual divider rule), not the
    # outer-neatline's own right edge which would show up near the far right
    divider_local_x = int(candidates[0])
    return search_x0 + divider_local_x, best_frac


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_image")
    parser.add_argument("output_dir")
    parser.add_argument(
        "--override",
        help="Explicit map_box as x0,y0,x1,y1 in PREVIEW pixel coords (read off preview.png), skips detection",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    gray = load_grayscale(args.input_image)
    h, w = gray.shape
    preview_max_dim = 1600
    scale = min(1.0, preview_max_dim / max(h, w))
    preview_w, preview_h = w * scale, h * scale

    outer_box = find_outer_box(gray)
    divider_best_frac = None

    if args.override:
        # coordinates come in PREVIEW pixel space - scale up to full resolution
        px0, py0, px1, py1 = (int(v) for v in args.override.split(","))
        if px1 <= px0 or py1 <= py0:
            print(json.dumps({
                "error": f"--override x0,y0,x1,y1 must have x1>x0 and y1>y0, got {args.override}"
            }))
            sys.exit(1)
        if px0 < 0 or py0 < 0 or px1 > preview_w or py1 > preview_h:
            print(json.dumps({
                "error": f"--override {args.override} falls outside preview.png's own "
                f"{preview_w:.0f}x{preview_h:.0f} pixel space - read the corners off preview.png, not "
                "the full-resolution source image"
            }))
            sys.exit(1)
        map_box = (int(px0 / scale), int(py0 / scale), int(px1 / scale), int(py1 / scale))
        sidebar_box = None
        divider_x = None
    else:
        divider_x, divider_best_frac = find_vertical_divider(gray, outer_box)
        if divider_x is not None:
            map_box = (outer_box[0], outer_box[1], divider_x, outer_box[3])
            sidebar_box = (divider_x, outer_box[1], outer_box[2], outer_box[3])
        else:
            map_box = outer_box
            sidebar_box = None

    # full-resolution crop of just the map area
    cropped = gray[map_box[1] : map_box[3], map_box[0] : map_box[2]]
    if cropped.size == 0:
        print(json.dumps({
            "error": f"map_box {map_box} produces an empty crop (0 width or height) - "
            "check the --override coordinates or source image"
        }))
        sys.exit(1)
    Image.fromarray(cropped).save(out_dir / "cropped.png")

    # downsized annotated preview of the WHOLE page for a quick human/Claude sanity check
    preview = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    preview = cv2.resize(preview, (int(w * scale), int(h * scale)))

    def scaled_box(box):
        return tuple(int(v * scale) for v in box)

    ob = scaled_box(outer_box)
    mb = scaled_box(map_box)
    cv2.rectangle(preview, (ob[0], ob[1]), (ob[2], ob[3]), (0, 0, 255), 2)  # red = outer
    cv2.rectangle(preview, (mb[0], mb[1]), (mb[2], mb[3]), (0, 200, 0), 3)  # green = chosen map area
    if sidebar_box:
        sb = scaled_box(sidebar_box)
        cv2.rectangle(preview, (sb[0], sb[1]), (sb[2], sb[3]), (255, 128, 0), 2)  # orange = sidebar

    cv2.imwrite(str(out_dir / "preview.png"), preview)

    meta = {
        "source_image": str(Path(args.input_image).resolve()),
        "source_size": {"width": w, "height": h},
        "outer_box": list(outer_box),
        "map_box": list(map_box),
        "sidebar_box": list(sidebar_box) if sidebar_box else None,
        "divider_x": divider_x,
        "divider_best_frac": divider_best_frac,
        "used_override": bool(args.override),
    }
    save_json(out_dir / "crop_meta.json", meta)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
