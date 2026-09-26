# Reading a historic FIRM panel's legend

Historic (pre-DFIRM, roughly 1970s-80s) Flood Insurance Rate Map panels carry
their own legend on every sheet, usually in a "KEY TO MAP" box plus an
"EXPLANATION OF ZONE DESIGNATIONS" table, in the right-hand sidebar next to
the map area. The exact wording and included zone codes vary by community and
map edition, so always read the legend on the specific sheet(s) you're
working with rather than assuming this reference is authoritative for a given
panel - use it as a guide to what to look for, not a substitute for looking.

## "KEY TO MAP" symbols to expect

- **500-Year Flood Boundary** - typically a distinct dashed line style.
- **100-Year Flood Boundary** - typically a solid line, often bolder/thicker
  than the street grid. This is usually the most important boundary to
  detect for zone polygon extraction, since it separates the shaded/hatched
  Zone A/V areas from Zone B/C/X areas outside the special flood hazard area.
- **Zone Designation with Date of Identification** - a small diagram showing
  how a zone label and its identification date are printed together (e.g.
  "ZONE A0" over a date like "1/8/78"). When reading zone labels off the map,
  the zone code is what matters for the polygon's attribute - the date is
  metadata about the map revision, not needed for the vector output.
- **Base Flood Elevation Line, With Elevation in Feet** - a line symbol
  paired with a number (e.g. "513"). These lines cross the zones that have
  determined base flood elevations (typically A1-A30, AH, AO, V1-V30) and
  each needs its elevation value captured as an attribute.
- **Base Flood Elevation in Feet Where Uniform Within Zone** - a single
  elevation value applies to an entire zone polygon rather than a line
  crossing it; if you see this, attach the elevation to the zone's own
  properties instead of (or in addition to) a BFE line.
- **Elevation Reference Mark**, **River Mile** - survey reference points;
  not part of this skill's target output (flood zones + BFE lines), safe to
  ignore for vectorization.

## "EXPLANATION OF ZONE DESIGNATIONS" - typical zone codes

| Code | Meaning (paraphrased from standard NFIP legends) |
|------|----------------------------------------------------|
| A | 100-year flood; base flood elevations and flood hazard factors not determined |
| A0 | 100-year shallow flooding (depths 1-3 ft); depth shown |
| A1-A30 | 100-year flood; base flood elevations and flood hazard factors determined |
| A99 | Area to be protected by a flood protection system under construction; BFEs and hazard factors not determined |
| B | Areas between limits of the 100-year and 500-year flood, or minimal (100-year, <1 ft depth) shallow flooding areas (medium shading) |
| C | Areas of minimal flooding (no shading) |
| D | Areas of undetermined, but possible, flood hazard |
| V | 100-year coastal flood with velocity (wave action); BFEs and hazard factors not determined |
| V1-V30 | 100-year coastal flood with velocity (wave action); BFEs and hazard factors determined |

Later revisions sometimes rename/consolidate these (e.g. modern DFIRMs mostly
use A, AE, AH, AO, AR, A99, V, VE, B/X-shaded, C/X, D) - always defer to what
the specific sheet's own legend says a code means, and use exactly the code
text as printed on the map (e.g. "A0" not "AO") as the `zone_code` attribute
so it matches the source document.

## Practical tips for this skill's pipeline

- Parse the legend **once per panel set / map edition**, not per panel - it's
  identical across all panels sharing the same "MAP REVISED" date in a
  community, and re-reading it every time wastes effort. Cache the zone code
  list you find as `legend.json` at the panel-set folder level and reuse it
  when sanity-checking zone labels on subsequent panels.
- Don't rely on the zone boundary line being visibly bolder than the street
  grid to auto-detect it - on real scans tested for this skill it was often
  the same stroke width, so `vectorize_zones.py` doesn't try to detect
  boundaries automatically at all. Trace the boundary by eye instead (see
  step 6 in SKILL.md); the script only snaps your traced points onto the
  nearest ink and closes the polygon.
- A single zone code (e.g. "ZONE A3") can appear multiple times on one panel
  for disjoint areas of the city - each occurrence is a separate polygon and
  needs its own traced entry in `zone_polygons.json`, even though they share
  the same `zone_code`.
