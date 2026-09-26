---
name: digitize-firm-map
description: Turns scanned historic FEMA Flood Insurance Rate Map (FIRM) panels - JPG/PNG/TIFF images with no embedded geospatial metadata - into real GIS deliverables (a georeferenced GeoTIFF plus a GeoPackage with flood zone polygons and Base Flood Elevation lines), with a local browser-based review UI for correcting results instead of requiring QGIS. Use this whenever the user has scanned/photographed paper flood maps, historic FIRM panels, old flood insurance rate maps, or similar maps and wants them georeferenced, digitized, vectorized, or turned into a shapefile/GeoPackage/GeoTIFF - even if they just say "digitize this map" or "make this map usable in GIS" without naming the format. Also use when the user asks to extract flood zones, BFE lines, or zone boundaries from a scanned map image.
---

# Digitize historic FIRM map panels to GIS

## What this skill produces, and what it can't

Given one or more scanned FIRM panel images, this skill produces, per panel:
a georeferenced GeoTIFF and a GeoPackage (`flood_zones` polygons,
`bfe_lines`, `gcps_used`). It does this by combining deterministic scripts
(cropping, geocoding, affine fitting, GDAL-free raster warping via rasterio,
polygon/line cleanup) with **your own vision** reading text and tracing
features directly off the scan - these old sheets have no printed
coordinates and no reliable pixel-level way to tell a zone boundary line from
the street grid, so there's no way to fully automate either georeferencing or
zone/BFE extraction. Your vision doing the reading/tracing, backed by
deterministic scripts for the geocoding/math/file-format parts, is the
approach the user has chosen for this project - don't try to replace the
tracing steps with pure image-processing heuristics, that was tried and
didn't hold up on real scans.

Treat the result as a strong first draft, not survey-grade truth: the
georeferencing is a simple least-squares affine (good enough to place the
panel and its zones correctly for reference/context use, not engineering
precision), and your traced polygons/lines are only as good as the trace.
That's why step 9 (review) exists - always run it and tell the user about
it, don't skip straight to "done" after generating first-draft output.

## One-time setup: find a working Python interpreter

Plain `python`/`python3` frequently resolve to a non-functional stub on
Windows. Before running anything, find an interpreter that has everything
this skill needs by running, for each candidate in turn, until one succeeds:

```
<candidate> scripts/env_check.py
```

Try, in order: `python`, `python3`, `py`, then common full paths such as
`/c/ProgramData/anaconda3/python.exe` or `~/anaconda3/python.exe` /
`~/miniconda3/python.exe`. The first candidate whose `env_check.py` reports
`"ok": true` is the interpreter to use for **every** script invocation for
the rest of this session - remember its path, don't re-probe per script. If
none work, show the user the `missing` list from the closest candidate and
ask them to `pip install` those packages (never install packages yourself
without asking).

All commands below assume `$PY` is that chosen interpreter and that your
working directory is this skill's folder (so `scripts/...` resolves).

## Processing one panel: the pipeline

Work through panels **one at a time**, in conversation with the user -
propose, show them the preview images, get the review UI in front of them
before moving to the next panel. Don't batch-process a whole folder
unattended; the whole point of the review step is a human checkpoint, and
plowing through 30 panels before anyone looks at the first one wastes effort
if your GCP descriptions or tracing approach need adjusting for this
particular scan batch.

Create a per-panel work directory, e.g. `work/<panel_stem>/`, to hold all
intermediate files for that panel.

### 1. Crop the map area out of the legend/title sidebar

```
$PY scripts/crop_neatline.py <source_image> work/<panel_stem>/
```

Look at `work/<panel_stem>/preview.png` (green box = detected map area,
orange = sidebar, red = full page). If the green box is wrong, re-run with
`--override x0,y0,x1,y1` using pixel coordinates you read off the preview.
Everything downstream uses `work/<panel_stem>/cropped.png`.

### 2. Read the legend (once per panel set, not per panel)

Look at the sidebar region (or the original image if you haven't cropped it
out yet) and read the "KEY TO MAP" / "EXPLANATION OF ZONE DESIGNATIONS"
boxes. See `reference/firm_legend_notes.md` for what these normally contain
and how to interpret them. Keep the zone-code list in mind (or jot it into a
`legend.json` at the panel-set root) - you'll use it as a sanity check for
the zone labels you read in step 6, and you don't need to re-read the legend
for every panel from the same edition/community.

### 3. Propose ground control points (GCPs)

Look at `cropped.png` (tile it into a few overlapping views if it's too
large to read street names clearly at once - these scans are commonly
5000-15000px on a side). Pick **6-10 well-distributed, uniquely identifiable
points**: named street intersections, a distinctive shoreline point, a
parish/county boundary corner. Spread them across the whole panel, not
clustered in one corner - that's what makes the affine fit meaningful rather
than just accurate near one spot.

For each, write down its pixel coordinate (in `cropped.png`'s pixel space)
and a plain-text description good enough for a geocoder, e.g. `"Canal St &
N Claiborne Ave, New Orleans, LA"` - include the city/parish/state, since a
street name alone is often ambiguous. Save these as
`work/<panel_stem>/gcp_candidates.json`:

```json
[{"id": "gcp1", "pixel_x": 1234, "pixel_y": 567, "description": "..."}]
```

### 4. Geocode

```
$PY scripts/geocode_points.py work/<panel_stem>/gcp_candidates.json work/<panel_stem>/gcps.json
```

Check the output: any point with `"confidence": "low"` or `"failed"` needs
attention before proceeding - either make its description more specific
(add the parish/neighborhood) and re-run, or drop it and propose a
replacement point elsewhere on the panel. Don't proceed to fitting with
fewer than ~5 good points if you can help it; 3 is the bare minimum and
gives you no way to sanity-check the fit via residuals.

### 5. Fit the georeferencing transform

```
$PY scripts/fit_georeference.py work/<panel_stem>/gcps.json work/<panel_stem>/cropped.png \
    work/<panel_stem>/georeferenced.tif --transform-out work/<panel_stem>/transform.json
```

Check the `residuals` in the output: a point with `"likely_bad": true` (or
just a residual much larger than the others) is almost always a bad GCP, not
real map distortion - go back to step 3/4 for that point rather than
accepting a sloppy fit.

### 6. Trace zone boundaries and label them

Automatic wall-detection/flood-fill for zone boundaries doesn't hold up on
these scans - the zone boundary line is often the same stroke width as the
street grid, so there's no reliable pixel-level way to tell them apart. Trace
each zone polygon by eye instead, the same way you'll trace BFE lines in the
next step: for each zone instance (remember a zone code can appear more than
once on one panel for disjoint areas - each occurrence needs its own entry),
follow its boundary line (per the sheet's own "KEY TO MAP" legend - not the
street grid) around and record a reasonable number of points along it. You
don't need to close the ring yourself or be pixel-perfect - points are
snapped onto the nearest line ink automatically. Save as
`work/<panel_stem>/zone_polygons.json`:

```json
[{"id": "z1", "zone_code": "A0", "points": [[2200,900],[2260,880],[2300,950], ...]}]
```

Then:

```
$PY scripts/vectorize_zones.py work/<panel_stem>/cropped.png work/<panel_stem>/zone_polygons.json \
    work/<panel_stem>/transform.json work/<panel_stem>/
```

Look at `zones_preview.png` to confirm each polygon actually followed the
boundary you traced (the auto-snap can occasionally jump to a nearby stronger
line if a traced point was far from the real boundary) - retrace any point
that landed on the wrong line rather than trying to hand-edit the geometry.

### 7. Trace BFE lines (if the panel has any)

For each Base Flood Elevation line/label, trace a handful of points along its
path (doesn't need to be exact - a snap-to-ink step cleans it up) and note
its elevation value. Save as `work/<panel_stem>/bfe_lines.json`:

```json
[{"id": "bfe1", "elevation": 513, "points": [[100,200],[150,210],[300,250]]}]
```

Then:

```
$PY scripts/vectorize_bfe.py work/<panel_stem>/cropped.png work/<panel_stem>/bfe_lines.json \
    work/<panel_stem>/transform.json work/<panel_stem>/
```

Check `bfe_preview.png`. Skip this step for panels with no BFE lines (many
Zone A/B/C-only panels won't have any).

### 8. Assemble the GeoPackage

```
$PY scripts/build_geopackage.py work/<panel_stem>/<panel_id>.gpkg \
    --zones work/<panel_stem>/zones.geojson --bfe work/<panel_stem>/bfe.geojson \
    --gcps work/<panel_stem>/gcps.json --panel-id "<panel_id>"
```

### 9. Hand it to the user for review - don't skip this

```
$PY scripts/review_server.py work/<panel_stem>/ --panel-id "<panel_id>"
```

Tell the user to open the printed `http://127.0.0.1:<port>` URL in their
browser. There they can drag GCPs (or delete/add one via the draw toolbar),
reshape/relabel/delete zone polygons and BFE lines, add missing ones, and hit
**Save & Rebuild** - which re-fits the transform, rewrites the GeoTIFF, and
rebuilds the GeoPackage in place, no QGIS involved. Wait for them to confirm they're satisfied (or tell you what
still looks wrong) before considering the panel done or moving to the next
one. The server keeps running in the foreground - run it in the background
or in a separate terminal if you need to keep working while it's up.

Final deliverables live in `work/<panel_stem>/`: `georeferenced.tif` and
`<panel_id>.gpkg`. Once the user confirms a panel, consider copying/moving
just those two files to wherever they want the finished output (e.g. an
`output/` folder) - the rest of `work/<panel_stem>/` is scratch/intermediate
state.

## Batches of many panels

If the user hands you a whole folder (like the sample
`22071C_HISTORIC_FIRM_PANEL_.../` set), process the index sheet first if
there is one (it has no coordinates either, but it tells you the panel
layout and community name, useful context for your GCP descriptions), then
go panel by panel through the pipeline above. Reuse the legend you parsed in
step 2 for every panel from the same edition rather than re-reading it each
time.
