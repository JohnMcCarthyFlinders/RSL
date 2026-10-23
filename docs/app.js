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

  const BASEMAPS = {
    imagery: {
      label: "Esri satellite imagery",
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
      attribution: "Esri, Maxar, Earthstar Geographics and the GIS User Community",
      maxzoom: 18,
    },
    sentinel: {
      label: "Sentinel-2 cloudless (EOX)",
      tiles: ["https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg"],
      attribution: "Sentinel-2 cloudless 2020 by EOX IT Services GmbH (contains modified Copernicus Sentinel data 2020)",
      maxzoom: 15,
    },
    ocean: {
      label: "Esri Ocean (bathymetry)",
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}"],
      attribution: "Esri, GEBCO, NOAA, Garmin, HERE and other contributors",
      maxzoom: 13,
    },
    osm: {
      label: "OpenStreetMap",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      attribution: "© OpenStreetMap contributors",
      maxzoom: 19,
    },
    dark: {
      label: "CARTO dark",
      tiles: ["https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"],
      attribution: "© OpenStreetMap contributors © CARTO",
      maxzoom: 19,
    },
  };

  const EMPTY = { type: "FeatureCollection", features: [] };
  const cache = new Map();
  let manifest = null;
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

  // Sites are DOM markers (no glyph server needed for labels)
  const siteMarkers = [];
  async function addSites() {
    let data;
    try {
      const r = await fetch(DATA + "overlays/sites.geojson");
      if (!r.ok) return;
      data = await r.json();
    } catch (e) { return; }
    for (const f of data.features) {
      if (!f.geometry || f.geometry.type !== "Point") continue;
      const p = f.properties || {};
      const el = document.createElement("button");
      el.className = "site";
      el.innerHTML = `<span class="pin"></span><span class="name">${esc(p.name || "")}</span>`;
      const rows = Object.entries(p).filter(([k]) => k !== "name")
        .map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("");
      const popup = new maplibregl.Popup({ offset: 12 })
        .setHTML(`<h3>${esc(p.name || "Site")}</h3><table>${rows}</table>`);
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
      cache.set(path, fetch(DATA + path).then((r) => (r.ok ? r.json() : EMPTY)).catch(() => EMPTY));
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
    $("lyr-sites").addEventListener("change", (e) => {
      for (const m of siteMarkers) m.getElement().style.display = e.target.checked ? "" : "none";
    });

    window.addEventListener("resize", drawChart);
    $("land-opacity").addEventListener("input", (e) => map.setPaintProperty("land-fill", "fill-opacity", Number(e.target.value)));
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
    initBasemapPicker();
    try {
      const r = await fetch(DATA + "manifest.json");
      manifest = await r.json();
    } catch (e) {
      $("demo-banner").textContent = "Could not load map data";
      $("demo-banner").hidden = false;
      console.error("Could not load data/manifest.json. Serve this folder over http (not file://).", e);
      return;
    }
    ages = manifest.ages.slice().sort((a, b) => a.age_ka - b.age_ka);
    $("demo-banner").hidden = !manifest.demo;
    $("source").textContent = [manifest.sea_level_table && "Sea level table: " + manifest.sea_level_table,
      manifest.source_dem && "Elevation: " + manifest.source_dem].filter(Boolean).join(" · ") + ".";
    if (manifest.bounds) map.fitBounds([[manifest.bounds[0], manifest.bounds[1]], [manifest.bounds[2], manifest.bounds[3]]], { padding: { top: 30, bottom: 260, left: 30, right: 30 }, duration: 0 });

    // Reference: the lowest shoreline in the record (usually the LGM)
    const lowest = ages.reduce((m, a) => (a.sea_level_m < m.sea_level_m ? a : m), ages[0]);
    load(lowest.shoreline).then((d) => map.getSource("lgm").setData(d));
    $("lgm-label").textContent = `Lowest shoreline (${fmtAge(lowest.age_ka)} ka, ${fmtSL(lowest.sea_level_m)} m)`;

    await addSites();
    wireControls();
    drawChart();
    const m = location.hash.match(/age=([\d.]+)/);
    show(m ? nearestIndex(Number(m[1])) : 0);
  });
})();
