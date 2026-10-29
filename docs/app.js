/* Sahul Shelf Shorelines viewer.
 * Reads data/manifest.json (written by arcgis_contours.py) and loads one
 * shoreline + land GeoJSON per age on demand. Optional point overlays come
 * from data/overlays/sites.geojson.
 */
(function () {
  "use strict";

  const DATA = "data/";
  const LAND_COLOR = "#e8c27a";
  const SHORE_COLOR = "#FFD300";

  // All Esri basemaps: free, no API key, served from the same host
  const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/";
  const esri = (label, service, maxzoom, attribution) =>
    ({ label, tiles: [ESRI + service + "/MapServer/tile/{z}/{y}/{x}"], maxzoom, attribution });
  const BASEMAPS = {
    imagery: esri("Satellite imagery", "World_Imagery", 18, "Esri, Maxar, Earthstar Geographics and the GIS User Community"),
    ocean: esri("Ocean (bathymetry)", "Ocean/World_Ocean_Base", 13, "Esri, GEBCO, NOAA, Garmin, HERE and other contributors"),
    topo: esri("Topographic", "World_Topo_Map", 18, "Esri, HERE, Garmin, USGS, NGA and other contributors"),
    natgeo: esri("National Geographic", "NatGeo_World_Map", 16, "National Geographic, Esri, Garmin, HERE, UNEP-WCMC, USGS, NASA"),
    dark: esri("Dark grey", "Canvas/World_Dark_Gray_Base", 16, "Esri, HERE, Garmin, OpenStreetMap contributors"),
  };

  const EMPTY = { type: "FeatureCollection", features: [] };
  const cache = new Map();
  let manifest = null;
  let version = "";       // cache-buster from manifest.generated, so re-exports never show stale files
  let ages = [];          // sorted ascending
  let current = 0;        // index into ages
  let playing = null;

  const $ = (id) => document.getElementById(id);

  // ---------------------------------------------------------------- map
  const map = new maplibregl.Map({
    container: "map",
    style: { version: 8, sources: {}, layers: [] },
    center: [133, -11],
    zoom: 4,
    attributionControl: { compact: true },
    hash: false,
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");

  // ------------------------------------------------------------ measure
  // Click points on the map; shows great-circle distance in km.
  const RULER = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="1.5" y="7" width="21" height="10" rx="1.5" fill="#FFD300" stroke="#151b33" stroke-width="1.4"/>' +
    '<path d="M5 7v4M8.5 7v2.5M12 7v4M15.5 7v2.5M19 7v4" stroke="#151b33" stroke-width="1.4"/></svg>';
  const measure = { on: false, pts: [] };

  function haversineKm(a, b) {
    const R = 6371.0088, rad = Math.PI / 180;
    const dLat = (b[1] - a[1]) * rad, dLon = (b[0] - a[0]) * rad;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * rad) * Math.cos(b[1] * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }
  const fmtKm = (km) => (km < 10 ? km.toFixed(2) : km < 100 ? km.toFixed(1) : Math.round(km).toLocaleString()) + " km";

  function drawMeasure() {
    const pts = measure.pts;
    const feats = pts.map((c) => ({ type: "Feature", geometry: { type: "Point", coordinates: c } }));
    if (pts.length > 1) feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: pts } });
    map.getSource("measure").setData({ type: "FeatureCollection", features: feats });
    let total = 0, last = 0;
    for (let i = 1; i < pts.length; i++) { last = haversineKm(pts[i - 1], pts[i]); total += last; }
    $("measure-total").textContent = pts.length > 1 ? fmtKm(total) : "0 km";
    $("measure-hint").textContent = pts.length === 0 ? "Click the map to start measuring"
      : pts.length === 1 ? "Click the next point"
      : `${pts.length - 1} segment${pts.length > 2 ? "s" : ""} · last ${fmtKm(last)}`;
  }

  function setMeasure(on) {
    measure.on = on;
    measure.pts = [];
    $("measure-box").hidden = !on;
    measureBtn.classList.toggle("active", on);
    measureBtn.setAttribute("aria-pressed", String(on));
    map.getCanvas().style.cursor = on ? "crosshair" : "";
    on ? map.doubleClickZoom.disable() : map.doubleClickZoom.enable();
    if (map.getSource("measure")) drawMeasure();
  }

  const measureBtn = document.createElement("button");
  measureBtn.type = "button";
  measureBtn.className = "measure-btn";
  measureBtn.title = "Measure distance";
  measureBtn.setAttribute("aria-pressed", "false");
  measureBtn.innerHTML = RULER + "<span>Measure</span>";
  measureBtn.addEventListener("click", () => setMeasure(!measure.on));
  map.addControl({
    onAdd() { const c = document.createElement("div"); c.className = "maplibregl-ctrl measure-ctrl"; c.appendChild(measureBtn); return c; },
    onRemove() {},
  }, "top-right");

  function addMeasureLayers() {
    map.addSource("measure", { type: "geojson", data: EMPTY });
    map.addLayer({ id: "measure-line", type: "line", source: "measure", filter: ["==", "$type", "LineString"],
      paint: { "line-color": "#FFD300", "line-width": 3, "line-dasharray": [2, 1.5] } });
    map.addLayer({ id: "measure-pts", type: "circle", source: "measure", filter: ["==", "$type", "Point"],
      paint: { "circle-radius": 5, "circle-color": "#FFD300", "circle-stroke-color": "#151b33", "circle-stroke-width": 2 } });
    map.on("click", (e) => {
      if (!measure.on) return;
      measure.pts.push([e.lngLat.lng, e.lngLat.lat]);
      drawMeasure();
    });
    $("measure-clear").addEventListener("click", () => { measure.pts = []; drawMeasure(); });
    $("measure-close").addEventListener("click", () => setMeasure(false));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && measure.on) setMeasure(false); });
  }

  function setBasemap(key) {
    const b = BASEMAPS[key];
    if (map.getLayer("basemap")) map.removeLayer("basemap");
    if (map.getSource("basemap")) map.removeSource("basemap");
    map.addSource("basemap", { type: "raster", tiles: b.tiles, tileSize: 256,
      attribution: b.attribution, maxzoom: b.maxzoom });
    const first = map.getStyle().layers.find((l) => l.id !== "basemap");
    map.addLayer({ id: "basemap", type: "raster", source: "basemap" }, first && first.id);
    try { localStorage.setItem("basemap-v2", key); } catch (e) { /* storage blocked */ }
  }

  function addDataLayers() {
    map.addSource("land", { type: "geojson", data: EMPTY });
    map.addSource("shore", { type: "geojson", data: EMPTY });
    map.addSource("lgm", { type: "geojson", data: EMPTY });
    map.addLayer({ id: "land-fill", type: "fill", source: "land",
      paint: { "fill-color": LAND_COLOR, "fill-opacity": 0.35 } });
    map.addLayer({ id: "lgm-line", type: "line", source: "lgm",
      layout: { visibility: "none" },
      paint: { "line-color": "#ffffff", "line-width": 1.2, "line-dasharray": [3, 2], "line-opacity": 0.8 } });
    map.addLayer({ id: "shore-casing", type: "line", source: "shore",
      paint: { "line-color": "#000000", "line-opacity": 0.6,
        "line-width": ["interpolate", ["linear"], ["zoom"], 3, 3, 8, 5] } });
    map.addLayer({ id: "shore-line", type: "line", source: "shore",
      paint: { "line-color": SHORE_COLOR, "line-width": ["interpolate", ["linear"], ["zoom"], 3, 1.5, 8, 3] } });
  }

  // Line overlays with DOM labels, a key in the layers panel and click popups:
  // faunal boundary lines (Wallace, Huxley, Weber, Lydekker) and colonisation routes (Bird et al. 2019)
  const overlayLabels = {};
  async function addLineOverlay(id, file, opts) {
    let data;
    try {
      const r = await fetch(DATA + "overlays/" + file, { cache: "no-cache" });
      if (!r.ok) return;
      data = await r.json();
    } catch (e) { return; }
    map.addSource(id, { type: "geojson", data });
    const before = map.getLayer("measure-line") ? "measure-line" : undefined;
    const vis = $("lyr-" + id).checked ? "visible" : "none";
    const line = { "line-color": ["get", "color"], "line-width": ["interpolate", ["linear"], ["zoom"], 3, 2.5, 8, 4] };
    if (opts.dash) line["line-dasharray"] = opts.dash;
    map.addLayer({ id: id + "-casing", type: "line", source: id, layout: { visibility: vis, "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#000", "line-opacity": 0.55, "line-width": ["interpolate", ["linear"], ["zoom"], 3, 4.5, 8, 7] } }, before);
    map.addLayer({ id: id + "-line", type: "line", source: id, layout: { visibility: vis, "line-join": "round" }, paint: line }, before);
    const key = $(id + "-key");
    const labels = overlayLabels[id] = [];
    for (const f of data.features) {
      const p = f.properties || {};
      key.insertAdjacentHTML("beforeend", `<li><i style="background:${esc(p.color)};color:${esc(p.color)}"></i>${esc(p.short || p.name)}${p.year ? ` (${esc(p.year)})` : ""}</li>`);
      if (!p.label) continue;
      const el = document.createElement("div");
      el.className = "faunal-label";
      el.style.color = p.color;
      el.innerHTML = `<b>${esc(p.name)}</b>`;
      el.style.display = vis === "visible" ? "" : "none";
      labels.push(new maplibregl.Marker({ element: el, anchor: "left", offset: [8, 0] }).setLngLat(p.label).addTo(map));
    }
    key.hidden = vis !== "visible";
    map.on("click", id + "-line", (e) => {
      if (measure.on) return;
      new maplibregl.Popup({ offset: 6, maxWidth: "300px" }).setLngLat(e.lngLat)
        .setHTML(`<h3>${esc(e.features[0].properties.name)}</h3><table>${opts.rows(e.features[0].properties)}</table>`)
        .addTo(map);
    });
    map.on("mouseenter", id + "-line", () => { if (!measure.on) map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", id + "-line", () => { if (!measure.on) map.getCanvas().style.cursor = ""; });
  }
  const row = (k, v) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`;
  const addFaunal = () => addLineOverlay("faunal", "faunal_lines.geojson", {
    rows: (p) => row("proposed", `${p.author}, ${p.year}`) + row("what", p.what) + row("position", "Schematic") });
  const addRoutes = () => addLineOverlay("routes", "routes.geojson", { dash: [2.5, 1.5],
    rows: (p) => row("what", p.what) + row("source", p.reference) + row("position", "Traced from the published map (about ±10 km)") });

  // Sites are DOM markers (no glyph server needed for labels)
  const siteMarkers = [];
  async function addSites() {
    let data;
    try {
      const r = await fetch(DATA + "overlays/sites.geojson", { cache: "no-cache" });
      if (!r.ok) return;
      data = await r.json();
    } catch (e) { return; }
    for (const f of data.features) {
      if (!f.geometry || f.geometry.type !== "Point") continue;
      const p = f.properties || {};
      const el = document.createElement("button");
      el.className = "site";
      el.innerHTML = `<span class="pin"></span><span class="name">${esc(p.name || "")}</span>`;
      const rows = Object.entries(p).filter(([k]) => !["name", "image", "image_alt", "image_caption"].includes(k))
        .map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("");
      const pic = p.image ? `<figure class="site-pic"><img src="${esc(p.image)}" alt="${esc(p.image_alt || "")}" width="64" height="64">`
        + (p.image_caption ? `<figcaption>${esc(p.image_caption)}</figcaption>` : "") + "</figure>" : "";
      const popup = new maplibregl.Popup({ offset: 12 })
        .setHTML(`<div class="site-head">${pic}<h3>${esc(p.name || "Site")}</h3></div><table>${rows}</table>`);
      siteMarkers.push(new maplibregl.Marker({ element: el, anchor: "left", offset: [-7, 0] })
        .setLngLat(f.geometry.coordinates).setPopup(popup).addTo(map));
    }
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // --------------------------------------------------------------- data
  function load(path) {
    if (!path) return Promise.resolve(EMPTY);
    if (!cache.has(path)) {
      cache.set(path, fetch(DATA + path + version).then((r) => (r.ok ? r.json() : EMPTY)).catch(() => EMPTY));
    }
    return cache.get(path);
  }

  function prefetch(i) {
    for (const j of [i - 1, i + 1, i - 2, i + 2]) {
      const a = ages[j];
      if (a) { load(a.shoreline); load(a.land); }
    }
  }

  let token = 0;
  async function show(i) {
    i = Math.max(0, Math.min(ages.length - 1, i));
    current = i;
    const a = ages[i];
    $("slider").value = String(sliderFromIndex(i));
    $("age").textContent = fmtAge(a.age_ka);
    $("sl").textContent = fmtSL(a.sea_level_m);
    drawCursor();
    try { history.replaceState(null, "", "#age=" + a.age_ka); } catch (e) { /* file:// */ }

    const t = ++token;
    const [shore, land] = await Promise.all([load(a.shoreline), load(a.land)]);
    if (t !== token) return;            // a newer age was requested meanwhile
    map.getSource("shore").setData(shore);
    map.getSource("land").setData(land);
    prefetch(i);
  }

  const fmtAge = (v) => (Math.round(v * 10) / 10).toLocaleString();
  const fmtSL = (v) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(0);

  // Slider runs old (left) to young (right); ages[] is ascending.
  const sliderFromIndex = (i) => ages.length - 1 - i;
  const indexFromSlider = (v) => ages.length - 1 - Number(v);

  // -------------------------------------------------------------- chart
  const chart = { w: 0, h: 0, pad: { l: 34, r: 6, t: 6, b: 16 }, x: null, y: null };

  function bandKeys() {
    const keys = Object.keys(ages[0] || {});
    const lo = keys.find((k) => /(_lo|_low|lower|min)$/i.test(k));
    const hi = keys.find((k) => /(_hi|_up|_high|upper|max)$/i.test(k));
    return lo && hi ? [lo, hi] : null;
  }

  function drawChart() {
    const svg = $("chart");
    const box = svg.getBoundingClientRect();
    chart.w = box.width; chart.h = box.height;
    const { l, r, t, b } = chart.pad;
    const minAge = ages[0].age_ka, maxAge = ages[ages.length - 1].age_ka;
    const band = bandKeys();
    let lo = Infinity, hi = -Infinity;
    for (const a of ages) {
      const vals = [a.sea_level_m, ...(band ? [a[band[0]], a[band[1]]] : [])];
      for (const v of vals) if (Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    }
    lo = Math.floor(lo / 20) * 20; hi = Math.max(0, Math.ceil(hi / 20) * 20);
    chart.x = (age) => l + (chart.w - l - r) * (1 - (age - minAge) / (maxAge - minAge || 1));
    chart.y = (v) => t + (chart.h - t - b) * (hi - v) / (hi - lo || 1);
    chart.ageAt = (px) => minAge + (1 - (px - l) / (chart.w - l - r)) * (maxAge - minAge);

    let out = "";
    for (let v = lo; v <= hi; v += 40) {
      out += `<line class="grid" x1="${l}" x2="${chart.w - r}" y1="${chart.y(v)}" y2="${chart.y(v)}"/>`;
      out += `<text class="axis" x="${l - 4}" y="${chart.y(v) + 3}" text-anchor="end">${v}</text>`;
    }
    for (let age = Math.ceil(minAge / 20) * 20; age <= maxAge; age += 20) {
      out += `<text class="axis" x="${chart.x(age)}" y="${chart.h - 3}" text-anchor="middle">${age}</text>`;
    }
    if (band) {
      const top = ages.map((a) => `${chart.x(a.age_ka)},${chart.y(a[band[1]])}`);
      const bot = ages.slice().reverse().map((a) => `${chart.x(a.age_ka)},${chart.y(a[band[0]])}`);
      out += `<polygon class="band" points="${top.concat(bot).join(" ")}"/>`;
    }
    if (lo < 0 && hi > 0) out += `<line class="zero" x1="${l}" x2="${chart.w - r}" y1="${chart.y(0)}" y2="${chart.y(0)}"/>`;
    out += `<polyline class="curve" points="${ages.map((a) => `${chart.x(a.age_ka)},${chart.y(a.sea_level_m)}`).join(" ")}"/>`;
    out += `<line class="cursor" id="cursor" y1="${t}" y2="${chart.h - b}"/><circle class="dot" id="dot" r="4"/>`;
    svg.innerHTML = out;
    drawCursor();
  }

  function drawCursor() {
    const c = $("cursor"), d = $("dot"), a = ages[current];
    if (!c || !a) return;
    const x = chart.x(a.age_ka);
    c.setAttribute("x1", x); c.setAttribute("x2", x);
    d.setAttribute("cx", x); d.setAttribute("cy", chart.y(a.sea_level_m));
  }

  function nearestIndex(age) {
    let best = 0;
    ages.forEach((a, i) => { if (Math.abs(a.age_ka - age) < Math.abs(ages[best].age_ka - age)) best = i; });
    return best;
  }

  // ----------------------------------------------------------- controls
  function stop() {
    clearTimeout(playing); playing = null;
    $("play").textContent = "▶"; $("play").setAttribute("aria-label", "Play");
  }

  function play() {
    if (current === 0) current = ages.length;   // restart from 100 ka
    $("play").textContent = "❚❚"; $("play").setAttribute("aria-label", "Pause");
    const step = async () => {
      await show(current - 1);
      if (current === 0) return stop();
      playing = setTimeout(step, Number($("speed").value));
    };
    playing = setTimeout(step, 0);
  }

  function wireControls() {
    $("slider").max = String(ages.length - 1);
    $("slider").addEventListener("input", (e) => { stop(); show(indexFromSlider(e.target.value)); });
    $("play").addEventListener("click", () => (playing ? stop() : play()));
    $("chart").addEventListener("click", (e) => {
      const r = $("chart").getBoundingClientRect();
      stop(); show(nearestIndex(chart.ageAt(e.clientX - r.left)));
    });
    document.addEventListener("keydown", (e) => {
      if (e.target.tagName === "SELECT" || e.target.id === "slider") return;
      if (e.key === "ArrowLeft") { stop(); show(current + 1); e.preventDefault(); }
      if (e.key === "ArrowRight") { stop(); show(current - 1); e.preventDefault(); }
      if (e.key === " " && e.target.tagName !== "BUTTON") { playing ? stop() : play(); e.preventDefault(); }
    });

    const toggle = (id, layers) => $(id).addEventListener("change", (e) => {
      for (const l of layers) if (map.getLayer(l)) map.setLayoutProperty(l, "visibility", e.target.checked ? "visible" : "none");
    });
    toggle("lyr-land", ["land-fill"]);
    toggle("lyr-shore", ["shore-casing", "shore-line"]);
    toggle("lyr-lgm", ["lgm-line"]);
    for (const id of ["faunal", "routes"]) $("lyr-" + id).addEventListener("change", (e) => {
      const on = e.target.checked;
      for (const l of [id + "-casing", id + "-line"]) if (map.getLayer(l)) map.setLayoutProperty(l, "visibility", on ? "visible" : "none");
      for (const m of overlayLabels[id] || []) m.getElement().style.display = on ? "" : "none";
      $(id + "-key").hidden = !on;
    });
    $("lyr-sites").addEventListener("change", (e) => {
      for (const m of siteMarkers) m.getElement().style.display = e.target.checked ? "" : "none";
    });

    window.addEventListener("resize", drawChart);
    $("land-opacity").addEventListener("input", (e) => map.setPaintProperty("land-fill", "fill-opacity", Number(e.target.value)));
    // Phones: start with the layers menu folded so it doesn't cover the map
    if (window.matchMedia("(max-width: 760px)").matches) $("layers-d").open = false;
    $("about-btn").addEventListener("click", () => $("about").showModal());
  }

  function initBasemapPicker() {
    const sel = $("basemap");
    for (const [k, b] of Object.entries(BASEMAPS)) sel.add(new Option(b.label, k));
    let key = "imagery";
    try { key = localStorage.getItem("basemap-v2") || key; } catch (e) { /* storage blocked */ }
    if (!BASEMAPS[key]) key = "imagery";
    sel.value = key;
    setBasemap(key);
    sel.addEventListener("change", () => setBasemap(sel.value));
  }

  // --------------------------------------------------------------- boot
  map.on("load", async () => {
    addDataLayers();
    addMeasureLayers();
    initBasemapPicker();
    try {
      const r = await fetch(DATA + "manifest.json", { cache: "no-cache" });
      manifest = await r.json();
    } catch (e) {
      $("demo-banner").textContent = "Could not load map data";
      $("demo-banner").hidden = false;
      console.error("Could not load data/manifest.json. Serve this folder over http (not file://).", e);
      return;
    }
    version = "?v=" + encodeURIComponent(manifest.generated || Date.now());
    ages = manifest.ages.slice().sort((a, b) => a.age_ka - b.age_ka);
    $("demo-banner").hidden = !manifest.demo;
    if (!ages.some((a) => a.land)) {
      // Lines-only export: hide the land fill controls
      $("lyr-land").closest("label").hidden = true;
      $("land-opacity").closest("label").hidden = true;
    }
    $("source").textContent = [manifest.sea_level_table && "Sea level table: " + manifest.sea_level_table,
      manifest.source_dem && "Elevation: " + manifest.source_dem].filter(Boolean).join(" · ") + ".";
    if (manifest.bounds) map.fitBounds([[manifest.bounds[0], manifest.bounds[1]], [manifest.bounds[2], manifest.bounds[3]]], { padding: { top: 30, bottom: 260, left: 30, right: 30 }, duration: 0 });

    // Reference: the lowest shoreline in the record (usually the LGM)
    const lowest = ages.reduce((m, a) => (a.sea_level_m < m.sea_level_m ? a : m), ages[0]);
    load(lowest.shoreline).then((d) => map.getSource("lgm").setData(d));
    $("lgm-label").textContent = `Lowest shoreline (${fmtAge(lowest.age_ka)} ka, ${fmtSL(lowest.sea_level_m)} m)`;

    addFaunal();
    addRoutes();
    await addSites();
    wireControls();
    drawChart();
    const m = location.hash.match(/age=([\d.]+)/);
    show(m ? nearestIndex(Number(m[1])) : 0);
  });
})();
