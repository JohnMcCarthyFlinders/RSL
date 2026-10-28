"""Crude model of post-LGM (since ~19 ka) sediment thickness and erosion on the
shelf between today's coast and the lowest (LGM) shoreline.

Inputs
  docs/data/manifest.json + land_XXXka.geojson   shoreline stack from arcgis_contours.py
  data/sediment/control_points.csv               literature control points (see that file)

Model, per grid cell inside the domain (sea today, land at the lowest sea level):
  thickness = background (IDW of open-shelf points)
            + coastal wedge (IDW of nearshore points) * exp(-distance to today's coast / WEDGE_KM)
            + deltas / depocentres (Gaussian bumps)
            + carbonate bank caps (local seabed highs on the Australian shelf)
            - erosion zones (Gaussian, can drive the cell negative = net erosion)
Seabed depth is not available here, so it is bracketed from the shoreline stack:
a cell that is land at sea level s1 and sea at s2 lies between s1 and s2.

Outputs (data/sediment/ and docs/data/overlays/)
  sediment_thickness.tif/.tfw/.prj   Float32 grid for ArcGIS (m; negative = erosion; -9999 nodata)
  sediment_thickness.asc/.prj        same as an ESRI ASCII grid
  docs/data/overlays/sediment.png    classified RGBA image for the web viewer
  docs/data/overlays/sediment.json   image bounds, classes and colours
"""
import csv
import datetime
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"
OUT = ROOT / "data" / "sediment"
RES = 0.025                     # degrees (~2.8 km)
WEDGE_KM = 15.0                 # e-folding distance of the nearshore wedge
BANK_RISE_M = 15.0              # a cell this much shallower than its ~30 km surroundings counts as a bank
BANK_CAP_M = 3.0                # Holocene carbonate added on such banks (Australian shelf only)
IDW_POWER = 2.0
IDW_SMOOTH_KM = 60.0          # softens IDW so single points do not make bullseyes

# Classes: (upper bound, label, colour). Negative = erosion.
CLASSES = [
    (-5.0, "Erosion >5 m", "#4b2378"),
    (-2.0, "Erosion 2–5", "#7d4fae"),
    (-0.5, "Erosion 0.5–2", "#b99ad8"),
    (0.5, "<0.5 m (relict)", "#ece7dc"),
    (1.0, "0.5–1 m", "#fff2b2"),
    (2.0, "1–2 m", "#fed976"),
    (5.0, "2–5 m", "#feb24c"),
    (10.0, "5–10 m", "#fd8d3c"),
    (20.0, "10–20 m", "#e8562a"),
    (30.0, "20–30 m", "#b3261e"),
    (1e9, ">30 m", "#6e1010"),
]


def rasterize(geojson_path, shape, west, north):
    h, w = shape
    img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(img)
    to_px = lambda ring: [((x - west) / RES, (north - y) / RES) for x, y in ring]
    gj = json.loads(geojson_path.read_text())
    for f in gj["features"]:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            draw.polygon(to_px(poly[0]), fill=1)
            for hole in poly[1:]:
                draw.polygon(to_px(hole), fill=0)
    return np.asarray(img, dtype=bool)


def main():
    manifest = json.loads((DATA / "manifest.json").read_text())
    west, south, east, north = manifest["bounds"]
    w = int(round((east - west) / RES))
    h = int(round((north - south) / RES))
    lon = west + (np.arange(w) + 0.5) * RES
    lat = north - (np.arange(h) + 0.5) * RES
    LON, LAT = np.meshgrid(lon, lat)

    # ---- seabed elevation bracket from the shoreline stack
    ages = [a for a in manifest["ages"] if a.get("land")]
    levels = sorted({a["sea_level_m"]: a for a in ages}.items())       # unique sea levels
    lo = np.full((h, w), -9999.0)   # highest sea level at which the cell was land (elev > lo)
    hi = np.full((h, w), 9999.0)    # lowest sea level at which the cell was sea (elev < hi)
    for sl, a in levels:
        land = rasterize(DATA / a["land"], (h, w), west, north)
        lo = np.where(land, np.maximum(lo, sl), lo)
        hi = np.where(~land, np.minimum(hi, sl), hi)
    lowest = levels[0][0]
    land0 = rasterize(DATA / next(a for a in ages if a["age_ka"] == 0)["land"], (h, w), west, north)
    landmin = rasterize(DATA / levels[0][1]["land"], (h, w), west, north)
    domain = landmin & ~land0
    elev = np.where((lo > -9999) & (hi < 9999), (lo + hi) / 2, np.where(lo > -9999, lo, lowest))
    elev = np.clip(elev, lowest, 0)
    print(f"grid {w}x{h}, domain cells {domain.sum()}, levels {len(levels)} ({lowest} to {levels[-1][0]} m)")

    # ---- distance to today's coast (km)
    kmy = RES * 111.32
    kmx = RES * 111.32 * math.cos(math.radians((north + south) / 2))
    dcoast, (iy, ix) = ndimage.distance_transform_edt(~land0, sampling=(kmy, kmx), return_indices=True)
    # small islands shed little sediment: scale the wedge by the size of the nearest landmass
    lab, n = ndimage.label(land0)
    area_km2 = ndimage.sum(np.ones_like(lab), lab, range(n + 1)) * kmx * kmy
    near_area = area_km2[lab[iy, ix]]
    supply = np.clip(np.sqrt(near_area / 20000.0), 0.15, 1.0)

    # ---- control points
    rows = [r for r in csv.DictReader(l for l in open(OUT / "control_points.csv") if not l.startswith("#"))]
    def xy(lo_, la_):
        return np.asarray(lo_) * 111.32 * np.cos(np.radians(np.asarray(la_))), np.asarray(la_) * 111.32
    GX, GY = xy(LON, LAT)

    def idw(kind):
        pts = [r for r in rows if r["kind"] == kind]
        px, py = xy([float(r["lon"]) for r in pts], [float(r["lat"]) for r in pts])
        v = np.array([float(r["value_m"]) for r in pts])
        num = np.zeros_like(GX); den = np.zeros_like(GX)
        for x0, y0, vi in zip(px, py, v):
            d2 = (GX - x0) ** 2 + (GY - y0) ** 2 + IDW_SMOOTH_KM ** 2
            wgt = d2 ** (-IDW_POWER / 2)
            num += wgt * vi; den += wgt
        return num / den

    def bumps(kind):
        """Sum of Gaussian bumps, and the strongest normalised footprint (0-1)."""
        out = np.zeros_like(GX); foot = np.zeros_like(GX)
        for r in (r for r in rows if r["kind"] == kind):
            x0, y0 = xy(float(r["lon"]), float(r["lat"]))
            R = float(r["radius_km"])
            g = np.exp(-((GX - x0) ** 2 + (GY - y0) ** 2) / (2 * R * R))
            out += float(r["value_m"]) * g
            foot = np.maximum(foot, g)
        return out, foot

    background = idw("background")
    wedge = idw("coastal") * supply * np.exp(-dcoast / WEDGE_KM)
    deltas, _ = bumps("source")
    erosion, scour = bumps("erosion")   # in scour zones currents also strip the post-LGM cover

    # carbonate caps on local seabed highs, Australian shelf (south and east of Wallacea)
    surround = ndimage.uniform_filter(np.where(domain, elev, lowest), size=int(30 / kmy) | 1)
    aus = (LON > 116) & (LAT < -9.5) & ~((LON > 137.5) & (LAT > -9.8))
    banks = domain & aus & (elev - surround > BANK_RISE_M) & (elev < -15) & (dcoast > 20)
    lab, n = ndimage.label(banks)
    sizes = ndimage.sum(banks, lab, range(1, n + 1))
    banks = np.isin(lab, 1 + np.flatnonzero(sizes >= 4))      # drop single-cell artefacts of the contour stack
    carbonate = np.where(banks, BANK_CAP_M, 0.0)
    carbonate = ndimage.gaussian_filter(carbonate, 1.0)

    thick = (background + wedge + deltas + carbonate) * (1 - scour) - erosion
    thick = ndimage.gaussian_filter(thick, 0.8)
    thick = np.where(domain, thick, np.nan)
    v = thick[domain]
    print(f"thickness: min {v.min():.1f}  p5 {np.percentile(v,5):.1f}  median {np.median(v):.2f}  p95 {np.percentile(v,95):.1f}  max {v.max():.1f}")
    print(f"net erosion cells: {(v < -0.5).mean()*100:.1f}%   relict (|t|<0.5): {(np.abs(v) < 0.5).mean()*100:.1f}%")

    # ---- write grids
    OUT.mkdir(parents=True, exist_ok=True)
    nod = np.where(np.isnan(thick), -9999.0, np.round(thick, 2)).astype(np.float32)
    Image.fromarray(nod, mode="F").save(OUT / "sediment_thickness.tif")
    (OUT / "sediment_thickness.tfw").write_text(f"{RES}\n0\n0\n{-RES}\n{west + RES/2}\n{north - RES/2}\n")
    prj = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
           'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]')
    for n in ("sediment_thickness.prj",):
        (OUT / n).write_text(prj)
    with open(OUT / "sediment_thickness.asc", "w") as fh:
        fh.write(f"ncols {w}\nnrows {h}\nxllcorner {west}\nyllcorner {south}\ncellsize {RES}\nNODATA_value -9999\n")
        np.savetxt(fh, nod, fmt="%.2f")

    # ---- classified RGBA image for the viewer
    bounds = [c[0] for c in CLASSES]
    idx = np.digitize(np.nan_to_num(thick, nan=0), bounds[:-1], right=True)
    rgba = np.zeros((h, w, 4), np.uint8)
    for i, (_, _, col) in enumerate(CLASSES):
        m = (idx == i) & domain
        rgba[m] = [int(col[1:3], 16), int(col[3:5], 16), int(col[5:7], 16), 255]
    # MapLibre stretches an image source linearly in Web Mercator, so resample rows from
    # equal-latitude spacing to equal-Mercator spacing (nearest row) before saving
    merc = lambda la: np.log(np.tan(np.pi / 4 + np.radians(la) / 2))
    ym = np.linspace(merc(north), merc(south), h * 2)             # output rows, top to bottom
    lat_rows = np.degrees(2 * np.arctan(np.exp(ym)) - np.pi / 2)
    src = np.clip(np.floor((north - lat_rows) / RES).astype(int), 0, h - 1)
    rgba = rgba[src]
    ov = DATA / "overlays"
    Image.fromarray(rgba, "RGBA").save(ov / "sediment.png", optimize=True)
    meta = {
        "bounds": [west, south, east, north],
        "coordinates": [[west, north], [east, north], [east, south], [west, south]],
        "classes": [{"max": (None if c[0] > 1e8 else c[0]), "label": c[1], "color": c[2]} for c in CLASSES],
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "Modelled post-LGM sediment thickness (m) and net erosion, from published control points. Crude: treat as indicative only.",
    }
    (ov / "sediment.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    # control points for the map
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(r["lon"]), float(r["lat"])]},
              "properties": {k: r[k] for k in ("name", "kind", "value_m", "basis", "source")}} for r in rows]
    (ov / "sediment_controls.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False))


if __name__ == "__main__":
    main()
