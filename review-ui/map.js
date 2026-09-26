// Review UI for a single FIRM panel's digitization work-in-progress.
//
// Data model while working in the browser:
//   - GCPs are draggable Leaflet markers. Each marker.gcpMeta holds
//     {id, description, confidence, pixel_x, pixel_y}.
//   - GCP markers, zone polygons, and BFE lines all live in ONE shared
//     `editableItems` FeatureGroup so Leaflet.draw's single edit/delete
//     toolbar can operate on all of them - a marker left outside that group
//     would be draggable but not deletable through the toolbar, which is a
//     real capability the UI's own help text promises. Zone/BFE layers carry
//     `layer.feature.properties.kind` ("zone" | "bfe"); GCP markers are
//     distinguished at collection time by `instanceof L.Marker` instead,
//     since giving every marker a synthetic GeoJSON `feature` just to tag it
//     would be more bookkeeping for no benefit.
//   - `gcpMarkers` (id -> marker) is kept only as a convenience lookup for
//     popup binding; `editableItems` remains the source of truth for what's
//     actually on the map.
//
// The raster is shown as a plain axis-aligned L.imageOverlay using the
// bounding box of the current transform's four corner points - see the
// "Known limitation" note in review_server.py for why this can look
// slightly offset from the vector layers if the fit has real rotation/shear.

let map;
let imageOverlay = null;
let editableItems; // L.FeatureGroup: GCP markers + zone polygons + BFE polylines
let gcpMarkers = {}; // id -> L.Marker (lookup convenience only, see note above)
let drawControl;
let currentBounds = null;
let currentImageSize = null; // {w, h} of cropped.png, from the server's own state

function uid(prefix) {
  return `${prefix}_${Date.now()}_${Math.floor(Math.random() * 1e6)}`;
}

function setStatus(msg, kind) {
  const el = document.getElementById("status");
  el.textContent = msg;
  el.className = kind || "";
}

// Approximate inverse of the axis-aligned preview overlay: given a point the
// user clicked ON THE DISPLAYED IMAGE, estimate which source pixel that is.
// This is only used to seed a *new* GCP's pixel_x/pixel_y - dragging the
// marker afterwards corrects the geo position, but the pixel location itself
// should be double-checked (e.g. against zones_preview.png) since this
// linear estimate ignores any rotation/shear in the real transform.
function latLngToApproxPixel(latlng) {
  const [south, west] = currentBounds[0];
  const [north, east] = currentBounds[1];
  const fracX = (latlng.lng - west) / (east - west);
  const fracY = (north - latlng.lat) / (north - south);
  return {
    x: Math.round(fracX * currentImageSize.w),
    y: Math.round(fracY * currentImageSize.h),
  };
}

function gcpPopupHtml(meta) {
  return `
    <div class="popup-form">
      <b>${meta.id}</b>
      <label>Description</label>
      <input type="text" data-field="description" value="${(meta.description || "").replace(/"/g, "&quot;")}" />
      <div class="help" style="padding-left:0">confidence: ${meta.confidence || "n/a"}</div>
    </div>`;
}

function bindGcpPopup(marker) {
  marker.bindPopup(gcpPopupHtml(marker.gcpMeta));
  marker.on("popupopen", (e) => {
    const input = e.popup.getElement().querySelector('input[data-field="description"]');
    input.addEventListener("change", () => {
      marker.gcpMeta.description = input.value;
    });
  });
}

function addGcpMarker(gcp) {
  if (gcp.lon === undefined || gcp.lat === undefined) return;
  const marker = L.marker([gcp.lat, gcp.lon], {
    draggable: true,
    icon: L.divIcon({
      className: "gcp-icon",
      html: '<div style="width:14px;height:14px;border-radius:50%;background:#d00;border:2px solid white;box-shadow:0 0 2px #000;"></div>',
      iconSize: [14, 14],
      iconAnchor: [7, 7],
    }),
  });
  marker.gcpMeta = {
    id: gcp.id,
    description: gcp.description,
    confidence: gcp.confidence,
    pixel_x: gcp.pixel_x,
    pixel_y: gcp.pixel_y,
  };
  marker.on("dragend", () => {
    marker.gcpMeta.confidence = "user_corrected";
  });
  bindGcpPopup(marker);
  editableItems.addLayer(marker);
  gcpMarkers[gcp.id] = marker;
}

function zonePopupHtml(props) {
  return `
    <div class="popup-form">
      <b>${props.id}</b>
      <label>Zone code</label>
      <input type="text" data-field="zone_code" value="${(props.zone_code || "").replace(/"/g, "&quot;")}" />
    </div>`;
}

function bfePopupHtml(props) {
  return `
    <div class="popup-form">
      <b>${props.id}</b>
      <label>Elevation (ft)</label>
      <input type="text" data-field="elevation_ft" value="${props.elevation_ft ?? ""}" />
    </div>`;
}

function bindEditablePopup(layer) {
  const props = layer.feature.properties;
  const html = props.kind === "zone" ? zonePopupHtml(props) : bfePopupHtml(props);
  layer.bindPopup(html);
  layer.on("popupopen", (e) => {
    const input = e.popup.getElement().querySelector("input[data-field]");
    input.addEventListener("change", () => {
      if (props.kind === "zone") props.zone_code = input.value;
      else props.elevation_ft = input.value;
    });
  });
}

function loadFeatureCollection(fc, kind) {
  (fc.features || []).forEach((feat) => {
    const layer = L.geoJSON(feat, {
      style: kind === "zone" ? { color: "#2a9d3f", weight: 3, fillOpacity: 0.15 } : { color: "#2266cc", weight: 3 },
    }).getLayers()[0];
    layer.feature = { type: "Feature", properties: { ...feat.properties, kind } };
    bindEditablePopup(layer);
    editableItems.addLayer(layer);
  });
}

async function loadState() {
  const resp = await fetch("/api/state");
  const state = await resp.json();

  document.getElementById("panel-title").textContent = `FIRM Panel Review — ${state.panel_id}`;

  currentBounds = state.bounds;

  if (!map) {
    map = L.map("map");
    editableItems = new L.FeatureGroup();
    map.addLayer(editableItems);

    drawControl = new L.Control.Draw({
      edit: { featureGroup: editableItems },
      draw: {
        polygon: { showArea: false },
        polyline: true,
        marker: true,
        rectangle: false,
        circle: false,
        circlemarker: false,
      },
    });
    map.addControl(drawControl);

    map.on(L.Draw.Event.CREATED, (e) => {
      const layer = e.layer;
      if (e.layerType === "marker") {
        const pix = latLngToApproxPixel(layer.getLatLng());
        const id = uid("gcp");
        const description = window.prompt("Description for this new GCP (e.g. street intersection):", "") || "";
        layer.gcpMeta = { id, description, confidence: "user_added", pixel_x: pix.x, pixel_y: pix.y };
        bindGcpPopup(layer);
        gcpMarkers[id] = layer;
        layer.on("dragend", () => {
          layer.gcpMeta.confidence = "user_corrected";
        });
        editableItems.addLayer(layer);
      } else if (e.layerType === "polygon") {
        const zone_code = window.prompt("Zone code for this new polygon (e.g. AE, X, V10):", "") || "UNKNOWN";
        layer.feature = { type: "Feature", properties: { id: uid("zone"), zone_code, kind: "zone" } };
        bindEditablePopup(layer);
        editableItems.addLayer(layer);
      } else if (e.layerType === "polyline") {
        const elevation_ft = window.prompt("Elevation (ft) for this new BFE line:", "") || "";
        layer.feature = { type: "Feature", properties: { id: uid("bfe"), elevation_ft, kind: "bfe" } };
        bindEditablePopup(layer);
        editableItems.addLayer(layer);
      }
    });

    map.on(L.Draw.Event.DELETED, (e) => {
      e.layers.eachLayer((layer) => {
        for (const [id, m] of Object.entries(gcpMarkers)) {
          if (m === layer) delete gcpMarkers[id];
        }
      });
    });
  }

  // reset dynamic layers - GCP markers live inside editableItems too (see
  // note at the top of this file), so clearing it removes everything at once
  editableItems.clearLayers();
  gcpMarkers = {};
  if (imageOverlay) map.removeLayer(imageOverlay);

  imageOverlay = L.imageOverlay(state.image_url, state.bounds, { opacity: 0.85 });
  imageOverlay.addTo(map);
  map.fitBounds(state.bounds);

  // the server already knows the raster's pixel size (it read the file) -
  // use that directly instead of racing the browser's own image load
  currentImageSize = { w: state.image_size.width, h: state.image_size.height };

  state.gcps.forEach(addGcpMarker);
  loadFeatureCollection(state.zones, "zone");
  loadFeatureCollection(state.bfe, "bfe");

  renderResiduals(state.residuals || []);
}

function renderResiduals(residuals) {
  const tbody = document.querySelector("#residuals-table tbody");
  tbody.innerHTML = "";
  residuals.forEach((r) => {
    const tr = document.createElement("tr");
    if (r.likely_bad) tr.className = "likely-bad";
    const meters = r.residual_m_approx !== null && r.residual_m_approx !== undefined ? r.residual_m_approx.toFixed(1) : "n/a";
    tr.innerHTML = `<td>${r.id}</td><td>${meters}</td>`;
    tbody.appendChild(tr);
  });
}

function collectPayload() {
  const gcps = Object.values(gcpMarkers).map((m) => {
    const latlng = m.getLatLng();
    return {
      id: m.gcpMeta.id,
      description: m.gcpMeta.description,
      confidence: m.gcpMeta.confidence,
      pixel_x: m.gcpMeta.pixel_x,
      pixel_y: m.gcpMeta.pixel_y,
      lon: latlng.lng,
      lat: latlng.lat,
    };
  });

  const zones = { type: "FeatureCollection", features: [] };
  const bfe = { type: "FeatureCollection", features: [] };
  editableItems.eachLayer((layer) => {
    if (layer instanceof L.Marker) return; // GCPs - already collected above
    const gj = layer.toGeoJSON();
    gj.properties = layer.feature.properties;
    if (layer.feature.properties.kind === "zone") zones.features.push(gj);
    else bfe.features.push(gj);
  });

  return { gcps, zones, bfe };
}

async function save() {
  const btn = document.getElementById("save-btn");
  btn.disabled = true;
  setStatus("Saving and rebuilding...", "");
  try {
    const payload = collectPayload();
    const resp = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!resp.ok) {
      setStatus(`Error: ${data.error || resp.statusText}`, "error");
      return;
    }
    setStatus("Saved. GeoTIFF and GeoPackage rebuilt.", "ok");
    await loadState();
  } catch (err) {
    setStatus(`Error: ${err}`, "error");
  } finally {
    btn.disabled = false;
  }
}

document.getElementById("save-btn").addEventListener("click", save);
loadState();
