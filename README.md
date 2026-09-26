# map-digitizer-skill

A [Claude Code](https://claude.com/claude-code) skill that turns **scanned historic FEMA Flood Insurance Rate Map (FIRM) panels** — JPG/PNG/TIFF images with no embedded geospatial metadata — into real GIS deliverables: a georeferenced GeoTIFF and a GeoPackage with flood zone polygons and Base Flood Elevation (BFE) lines. It includes a small local browser-based review UI so you can inspect and correct the result without needing QGIS or any other GIS software installed.

Point Claude Code at a folder of scanned FIRM panels and ask it to digitize them — it walks the pipeline itself (crop, georeference, vectorize) using its own vision to read street names and trace features off the scan, calls out to deterministic scripts for the geocoding/math/file-format work, and hands you a browser page to fix anything that looks off before finalizing.

## Why this exists

Historic FIRM panels (roughly 1970s-90s) are common source material for flood-risk research, insurance history, and hazard mitigation planning, but they were never digitized — they exist only as scanned paper maps with **no printed coordinates at all**. Turning one into something usable in a GIS is normally a fully manual, hours-per-panel job in QGIS/ArcGIS: hand-picking control points, hand-tracing zone boundaries, hand-digitizing BFE lines. This skill automates the mechanical parts of that and uses Claude's own vision for the parts that genuinely require reading and interpreting the map, cutting the manual work down to reviewing and correcting a first draft.

## What it produces

Per panel:
- A **georeferenced GeoTIFF** (EPSG:4326)
- A **GeoPackage** with up to three layers: `flood_zones` (polygons with a `zone_code` attribute), `bfe_lines` (lines with an `elevation_ft` attribute), and `gcps_used` (the control points, for auditability)

Treat the output as a strong first draft, not survey-grade truth — see [Accuracy & limitations](#accuracy--limitations) below.

## How it works

1. **Crop** the map area out of the sheet's legend/title sidebar (OpenCV contour detection).
2. **Propose ground control points** — Claude reads named street intersections, shorelines, or parish boundaries directly off the scan.
3. **Geocode** those descriptions to real-world coordinates via OpenStreetMap's Nominatim.
4. **Fit** a least-squares affine georeferencing transform and check per-point residuals.
5. **Trace** flood zone boundaries and BFE lines by eye (the same way you'd read them - a bordering line, a text label) and let a script snap the trace onto the actual ink and clean up the geometry.
6. **Assemble** everything into a GeoPackage next to the georeferenced GeoTIFF.
7. **Review** in a local Flask + Leaflet web app: drag control points, reshape/relabel/delete zone polygons and BFE lines, add missing ones - saving re-fits the transform and rebuilds the output files in place.

Full step-by-step detail lives in [`SKILL.md`](./SKILL.md).

### Why not fully automatic?

Two things were tried and empirically didn't hold up on real scans, so don't be surprised the pipeline leans on Claude's vision rather than pure image processing:
- **Georeferencing from printed coordinates** — historic FIRM panels don't have any (no lat/long ticks, no grid), unlike modern DFIRM panels. There's nothing to detect.
- **Automatic zone-boundary detection** — a flood-fill bounded by a pixel-intensity threshold was tried first; on real scans the zone boundary line is often the same stroke width as the street grid, so no threshold cleanly separates them. Tracing by eye turned out to be both more reliable and simpler.

## Requirements

- [Claude Code](https://claude.com/claude-code)
- Python 3.9+ with: `rasterio`, `geopandas`, `shapely`, `fiona`, `opencv-python`, `scikit-image`, `numpy`, `pillow`, `scipy`, `requests`, `flask`

No GDAL command-line tools and no OCR engine required — georeferencing is done with `rasterio`'s own API, and text/label reading is done by Claude's vision rather than an OCR library (which tends to struggle on noisy microfiche scans anyway).

```bash
pip install rasterio geopandas shapely fiona opencv-python scikit-image numpy pillow scipy requests flask
```

If you're on Windows and plain `python`/`python3` resolve to the Microsoft Store stub rather than a real interpreter, point Claude at a full install (e.g. an Anaconda/Miniconda `python.exe`) — the skill's setup step handles finding a working interpreter for you.

## Installation

Clone this repo directly into a `.claude/skills/` directory. Either:

**Per-project** (only available in that project):
```bash
git clone https://github.com/murad-geoAi/map-digitizer-skill.git .claude/skills/digitize-firm-map
```

**For your whole account** (available in every project):
```bash
git clone https://github.com/murad-geoAi/map-digitizer-skill.git ~/.claude/skills/digitize-firm-map
```
(on Windows, that's `%USERPROFILE%\.claude\skills\digitize-firm-map`)

Claude Code picks up skills automatically from either location - no further configuration needed.

## Usage

In Claude Code, point it at your scanned panel(s) and ask for what you want, e.g.:

> "Digitize the FIRM panels in ./scans/ into GeoTIFFs and a GeoPackage"

> "This scanned flood map has no coordinates - georeference it and extract the flood zones"

Claude will work through the panels one at a time, showing you preview images along the way, and open a local review page once each panel has a first-draft result. Nothing is "done" until you've had a chance to look at that review page and confirm it, or tell Claude what to fix.

### Where to get sample scans

This repo doesn't ship sample data (historic scans are large binary files that don't belong in a git repo). FEMA's [Map Service Center](https://msc.fema.gov/) has historic FIRM panels available to download for free for most U.S. communities - search by address or community name and look for "Historic" or superseded panels.

## Accuracy & limitations

- Georeferencing is a **simple least-squares affine fit**, not a full polynomial/thin-plate-spline warp - good enough to place a panel and its zones correctly for reference/context use, not for engineering-grade precision.
- Zone and BFE geometry is only as good as the trace - review the preview images and the browser review UI before treating any panel as final.
- The review UI's raster preview is shown as an axis-aligned image overlay; if a panel's fit has meaningful rotation/shear, the on-screen raster and vector layers can look slightly offset from each other in the browser even though the actual exported GeoTIFF (written with the full affine transform) is correct.
- Geocoding uses the public Nominatim instance, which has light-use rate limits - fine for digitizing panels a few at a time, not for large unattended batch runs.

## Contributing

Issues and PRs welcome - especially real-world test cases (different FIRM editions, non-U.S. historic flood maps, other historic map series entirely) that reveal cases the pipeline doesn't handle well yet.

## License

[MIT](./LICENSE)
