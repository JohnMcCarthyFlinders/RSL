#!/usr/bin/env python3
"""
Shrink the per-age GeoJSON written by arcgis_contours.py for the web viewer:
simplify geometry, drop short lines / small polygons, round coordinates.

    pip install shapely
    python shrink_geojson.py out/web/contours docs/data/contours --tol 0.007 --min-km 15

--tol is in degrees (0.007 deg is about 800 m). Files are rewritten into the
output folder with the same names; manifest.json is not touched.
"""
import argparse
import glob
import json
import os

from shapely.geometry import mapping, shape


def round_coords(c, nd):
    if isinstance(c[0], (int, float)):
        return [round(c[0], nd), round(c[1], nd)]
    return [round_coords(x, nd) for x in c]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("--tol", type=float, default=0.007, help="simplify tolerance, degrees")
    p.add_argument("--min-km", type=float, default=15.0, help="drop lines shorter than this")
    p.add_argument("--min-km2", type=float, default=20.0, help="drop polygons smaller than this")
    p.add_argument("--decimals", type=int, default=3)
    p.add_argument("--merge", action="store_true",
                   help="merge each file into one multi-part feature (smaller, faster to draw)")
    a = p.parse_args()

    os.makedirs(a.dst, exist_ok=True)
    km_per_deg = 111.0
    before = after = 0
    for f in sorted(glob.glob(os.path.join(a.src, "*.geojson"))):
        before += os.path.getsize(f)
        fc = json.load(open(f))
        out = []
        for feat in fc["features"]:
            if not feat.get("geometry"):
                continue
            g = shape(feat["geometry"]).simplify(a.tol, preserve_topology=True)
            if g.is_empty:
                continue
            if g.geom_type.endswith("LineString") and g.length * km_per_deg < a.min_km:
                continue
            if g.geom_type.endswith("Polygon") and g.area * km_per_deg ** 2 < a.min_km2:
                continue
            gj = mapping(g)
            out.append({"type": "Feature", "properties": feat.get("properties", {}),
                        "geometry": {"type": gj["type"],
                                     "coordinates": round_coords(gj["coordinates"], a.decimals)}})
        if a.merge and out:
            kind = out[0]["geometry"]["type"].replace("Multi", "")
            parts = []
            for ft in out:
                c = ft["geometry"]["coordinates"]
                parts.extend(c if ft["geometry"]["type"].startswith("Multi") else [c])
            out = [{"type": "Feature", "properties": out[0]["properties"],
                    "geometry": {"type": "Multi" + kind, "coordinates": parts}}]
        dst = os.path.join(a.dst, os.path.basename(f))
        with open(dst, "w") as fh:
            json.dump({"type": "FeatureCollection", "features": out}, fh, separators=(",", ":"))
        after += os.path.getsize(dst)
    print("{:.1f} MB -> {:.1f} MB".format(before / 1e6, after / 1e6))


if __name__ == "__main__":
    main()
