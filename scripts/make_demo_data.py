#!/usr/bin/env python3
"""
Make SYNTHETIC placeholder shorelines so the web viewer can be tried before
the real ArcGIS output exists. Not bathymetry: the present coast of northern
Australia and southern New Guinea is crudely outlined and buffered outwards
as sea level falls. Replace with the ArcGIS output as soon as it exists.

    pip install shapely
    python make_demo_data.py sea_level_1ka.csv ../docs/data
"""
import csv
import json
import os
import sys

from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

# Very rough present-day coastlines (lon, lat)
AUSTRALIA = [(121.5, -19.0), (122.2, -17.0), (123.5, -16.3), (125.0, -14.5),
             (126.8, -13.8), (128.2, -15.0), (129.6, -14.8), (130.2, -12.8),
             (131.0, -12.2), (132.6, -11.6), (133.5, -11.9), (135.0, -12.2),
             (136.6, -12.0), (136.0, -13.5), (135.5, -15.0), (137.5, -16.3),
             (139.5, -17.5), (140.8, -17.4), (141.5, -15.0), (141.6, -12.6),
             (142.4, -10.8), (143.5, -12.8), (145.4, -15.0), (146.2, -19.0),
             (121.5, -19.0)]
NEW_GUINEA = [(131.0, -1.5), (134.0, -3.8), (137.5, -4.8), (138.8, -7.4),
              (140.5, -8.3), (142.6, -9.3), (144.0, -7.8), (146.0, -8.1),
              (147.5, -10.0), (149.0, -10.5), (149.0, -1.5), (131.0, -1.5)]
SHELF_EDGE_M = 130.0  # depth at which the demo buffer reaches its widest
MAX_BUFFER_DEG = 4.2


def buffer_for(depth_m):
    f = max(0.0, min(depth_m / SHELF_EDGE_M, 1.2))
    return MAX_BUFFER_DEG * f ** 1.4


def main(csv_path, out_dir):
    with open(csv_path, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("sea_level_m")]
    cdir = os.path.join(out_dir, "contours")
    os.makedirs(cdir, exist_ok=True)
    base = [Polygon(AUSTRALIA), Polygon(NEW_GUINEA)]
    ages = []
    for r in rows:
        age = float(r["age_ka"])
        sl = float(r["sea_level_m"])
        d = buffer_for(-sl)
        land = unary_union([p.buffer(d, quad_segs=8) for p in base]) if d > 0 else unary_union(base)
        land = land.simplify(0.02)
        tag = "{:03d}ka".format(int(round(age)))
        props = {"age_ka": age, "sea_level_m": sl}
        with open(os.path.join(cdir, "land_" + tag + ".geojson"), "w") as f:
            json.dump({"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": props, "geometry": mapping(land)}]}, f)
        with open(os.path.join(cdir, "shoreline_" + tag + ".geojson"), "w") as f:
            json.dump({"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": props, "geometry": mapping(land.boundary)}]}, f)
        entry = {k: float(v) for k, v in r.items() if v not in ("", None)}
        entry["shoreline"] = "contours/shoreline_" + tag + ".geojson"
        entry["land"] = "contours/land_" + tag + ".geojson"
        ages.append(entry)
    manifest = {"demo": True, "source_dem": "synthetic demo (not real bathymetry)",
                "sea_level_table": os.path.basename(csv_path),
                "bounds": [104.5, -21.5, 141.5, 0], "ages": ages}
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print("Wrote {} demo ages to {}".format(len(ages), out_dir))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
