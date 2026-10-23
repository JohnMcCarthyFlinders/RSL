# -*- coding: utf-8 -*-
"""
ArcGIS Pro: palaeo-shorelines from GEBCO at the elevations in a rebinned
sea level table, tagged by age, exported for the web viewer.

Run it one of three ways:
  1. ArcGIS Pro Python window / Notebook: edit CONFIG below, then
         exec(open(r"C:\\path\\to\\arcgis_contours.py").read())
  2. Python Command Prompt (arcgispro-py3):
         python arcgis_contours.py
  3. As a script tool: add it to a toolbox with the 9 parameters listed in
     read_parameters() (same order); values given there override CONFIG.

Inputs
  - GEBCO grid (GeoTIFF or netCDF->raster), elevations in metres, WGS84.
  - sea_level_1ka.csv from rebin_sea_level.py (age_ka, sea_level_m,
    contour_elev_m, ...).

Outputs (in OUT_DIR)
  - shorelines.gdb/shorelines        all shoreline lines, one set per age
  - shorelines.gdb/land              exposed land polygons per age (optional)
  - shp/shorelines.shp, shp/land.shp shapefile copies
  - web/manifest.json                ages, sea levels and file list for the viewer
  - web/contours/shoreline_XXXka.geojson, land_XXXka.geojson

Copy the contents of OUT_DIR/web into the viewer's docs/data/ folder.

Needs the Spatial Analyst extension (ContourList, Con, FocalStatistics).
"""
import csv
import datetime
import json
import os

import arcpy

# ---------------------------------------------------------------------------
# CONFIG: edit these, or override them with script tool parameters
# ---------------------------------------------------------------------------
CONFIG = {
    # GEBCO raster (GeoTIFF, or a raster dataset in a gdb)
    "DEM": r"C:\data\GEBCO\gebco_2024_sahul.tif",
    # Output of rebin_sea_level.py
    "SEA_LEVEL_CSV": r"C:\data\sea_level_1ka.csv",
    # Output folder (created if missing)
    "OUT_DIR": r"C:\data\shorelines_out",
    # Clip extent "xmin ymin xmax ymax" in DEM units, or "" for the whole DEM.
    # Arafura / Timor Sea / Sahul shelf:
    "EXTENT": "118 -20 145 -5",
    # Mean filter size (cells) to soften GEBCO artefacts before contouring; 0 = off
    "SMOOTH_CELLS": 3,
    # Drop shoreline pieces shorter than this (km). Removes pits and tiny islets.
    "MIN_LINE_KM": 5.0,
    # Make filled "exposed land" polygons as well as lines (slower)
    "MAKE_LAND": True,
    # Drop land polygons smaller than this (km2)
    "MIN_LAND_KM2": 10.0,
    # Simplification tolerance for the web copies (metres); 0 = off
    "SIMPLIFY_M": 150,
    # Decimal places kept in GeoJSON coordinates (4 = ~11 m)
    "COORD_DECIMALS": 4,
}


def read_parameters(cfg):
    """Script-tool parameters, if run from a toolbox (same order as CONFIG)."""
    keys = ["DEM", "SEA_LEVEL_CSV", "OUT_DIR", "EXTENT", "SMOOTH_CELLS",
            "MIN_LINE_KM", "MAKE_LAND", "MIN_LAND_KM2", "SIMPLIFY_M"]
    casts = [str, str, str, str, int, float,
             lambda s: s.lower() in ("true", "1", "yes"), float, float]
    for i, (k, cast) in enumerate(zip(keys, casts)):
        try:
            v = arcpy.GetParameterAsText(i)
        except Exception:
            v = ""
        if v not in ("", None, "#"):
            cfg[k] = cast(v)
    return cfg


def log(msg):
    print(msg)
    arcpy.AddMessage(msg)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def read_sea_levels(path):
    """Return list of dict rows with a numeric contour_elev_m."""
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("contour_elev_m", "") == "":
                log("  skipping {} ka (no sea level value)".format(r["age_ka"]))
                continue
            out = {}
            for k, v in r.items():
                try:
                    out[k] = float(v)
                except (TypeError, ValueError):
                    out[k] = None
            rows.append(out)
    if not rows:
        raise RuntimeError("No usable rows in " + path)
    return rows


def prepare_dem(cfg, gdb):
    dem = cfg["DEM"]
    if cfg["EXTENT"].strip():
        log("Clipping DEM to " + cfg["EXTENT"])
        clipped = os.path.join(gdb, "dem_clip")
        arcpy.management.Clip(dem, cfg["EXTENT"], clipped, "#", "#", "NONE",
                              "NO_MAINTAIN_EXTENT")
        dem = clipped
    if cfg["SMOOTH_CELLS"] and cfg["SMOOTH_CELLS"] > 1:
        n = int(cfg["SMOOTH_CELLS"])
        log("Smoothing DEM with {0}x{0} mean filter".format(n))
        sm = arcpy.sa.FocalStatistics(dem, arcpy.sa.NbrRectangle(n, n, "CELL"), "MEAN", "DATA")
        dem = os.path.join(gdb, "dem_smooth")
        sm.save(dem)
    return dem


def make_contours(dem, levels, gdb):
    raw = os.path.join(gdb, "contours_raw")
    log("Contouring {} unique elevations".format(len(levels)))
    arcpy.sa.ContourList(dem, raw, sorted(levels))
    return raw


def tag_lines_with_age(raw, rows, gdb, min_km, decimals):
    """Copy each contour to every age that uses its elevation."""
    sr = arcpy.Describe(raw).spatialReference
    out = os.path.join(gdb, "shorelines")
    arcpy.management.CreateFeatureclass(gdb, "shorelines", "POLYLINE", spatial_reference=sr)
    for name, ftype in [("age_ka", "DOUBLE"), ("sea_level_m", "DOUBLE"),
                        ("elev_m", "DOUBLE"), ("length_km", "DOUBLE")]:
        arcpy.management.AddField(out, name, ftype)

    by_elev = {}
    dropped = 0
    with arcpy.da.SearchCursor(raw, ["SHAPE@", "Contour"]) as cur:
        for geom, z in cur:
            if geom is None:
                continue
            km = geom.getLength("GEODESIC", "KILOMETERS")
            if km < min_km:
                dropped += 1
                continue
            by_elev.setdefault(round(z, decimals), []).append((geom, km))
    log("  kept {} contour pieces, dropped {} shorter than {} km".format(
        sum(len(v) for v in by_elev.values()), dropped, min_km))

    fields = ["SHAPE@", "age_ka", "sea_level_m", "elev_m", "length_km"]
    with arcpy.da.InsertCursor(out, fields) as ins:
        for r in rows:
            z = round(r["contour_elev_m"], decimals)
            pieces = by_elev.get(z, [])
            if not pieces:
                log("  note: no shoreline inside the extent for {:g} ka ({} m)".format(r["age_ka"], z))
            for geom, km in pieces:
                ins.insertRow([geom, r["age_ka"], r["sea_level_m"], z, km])
    return out


def make_land(dem, rows, gdb, min_km2, decimals):
    """Polygons of everything at or above sea level, one set per age."""
    sr = arcpy.Describe(dem).spatialReference
    out = os.path.join(gdb, "land")
    arcpy.management.CreateFeatureclass(gdb, "land", "POLYGON", spatial_reference=sr)
    for name in ["age_ka", "sea_level_m", "elev_m", "area_km2"]:
        arcpy.management.AddField(out, name, "DOUBLE")

    dem_r = arcpy.Raster(dem)
    cache = {}
    tmp = os.path.join(gdb, "land_tmp")
    for z in sorted({round(r["contour_elev_m"], decimals) for r in rows}):
        log("  land polygons at {} m".format(z))
        land = arcpy.sa.SetNull(dem_r < z, 1)
        if arcpy.Exists(tmp):
            arcpy.management.Delete(tmp)
        arcpy.conversion.RasterToPolygon(land, tmp, "SIMPLIFY", "Value",
                                         "SINGLE_OUTER_PART")
        polys = []
        with arcpy.da.SearchCursor(tmp, ["SHAPE@"]) as cur:
            for (geom,) in cur:
                a = geom.getArea("GEODESIC", "SQUAREKILOMETERS")
                if a >= min_km2:
                    polys.append((geom, a))
        cache[z] = polys
    if arcpy.Exists(tmp):
        arcpy.management.Delete(tmp)

    with arcpy.da.InsertCursor(out, ["SHAPE@", "age_ka", "sea_level_m", "elev_m", "area_km2"]) as ins:
        for r in rows:
            z = round(r["contour_elev_m"], decimals)
            for geom, a in cache[z]:
                ins.insertRow([geom, r["age_ka"], r["sea_level_m"], z, a])
    return out


def simplify(fc, tol_m, kind):
    if not tol_m:
        return fc
    out = fc + "_web"
    if arcpy.Exists(out):
        arcpy.management.Delete(out)
    tol = "{} Meters".format(tol_m)
    if kind == "line":
        arcpy.cartography.SimplifyLine(fc, out, "POINT_REMOVE", tol,
                                       collapsed_point_option="NO_KEEP")
    else:
        arcpy.cartography.SimplifyPolygon(fc, out, "POINT_REMOVE", tol,
                                          collapsed_point_option="NO_KEEP")
    return out


def _round_coords(c, nd):
    if isinstance(c[0], (int, float)):
        return [round(c[0], nd), round(c[1], nd)]
    return [_round_coords(x, nd) for x in c]


def export_geojson(fc, rows, folder, prefix, nd):
    """One GeoJSON per age, in WGS84, with rounded coordinates."""
    wgs84 = arcpy.SpatialReference(4326)
    feats = {}
    fields = ["SHAPE@", "age_ka", "sea_level_m", "elev_m"]
    with arcpy.da.SearchCursor(fc, fields, spatial_reference=wgs84) as cur:
        for geom, age, sl, z in cur:
            if geom is None:
                continue
            g = geom.__geo_interface__
            g = {"type": g["type"], "coordinates": _round_coords(g["coordinates"], nd)}
            feats.setdefault(age, []).append({
                "type": "Feature",
                "properties": {"age_ka": age, "sea_level_m": round(sl, 2), "elev_m": z},
                "geometry": g,
            })
    files = {}
    for r in rows:
        age = r["age_ka"]
        name = "{}_{:03d}ka.geojson".format(prefix, int(round(age)))
        with open(os.path.join(folder, name), "w") as f:
            json.dump({"type": "FeatureCollection", "features": feats.get(age, [])},
                      f, separators=(",", ":"))
        files[age] = "contours/" + name
    return files


def main(cfg):
    if arcpy.CheckExtension("Spatial") != "Available":
        raise RuntimeError("Spatial Analyst licence is not available")
    arcpy.CheckOutExtension("Spatial")
    arcpy.env.overwriteOutput = True

    out_dir = cfg["OUT_DIR"]
    web = os.path.join(out_dir, "web")
    for d in [out_dir, os.path.join(out_dir, "shp"), web, os.path.join(web, "contours")]:
        if not os.path.isdir(d):
            os.makedirs(d)
    gdb = os.path.join(out_dir, "shorelines.gdb")
    if not arcpy.Exists(gdb):
        arcpy.management.CreateFileGDB(out_dir, "shorelines.gdb")
    arcpy.env.workspace = gdb
    arcpy.env.scratchWorkspace = gdb

    rows = read_sea_levels(cfg["SEA_LEVEL_CSV"])
    # Decimal places of contour_elev_m, used to match contour values to ages
    with open(cfg["SEA_LEVEL_CSV"], newline="") as f:
        sample = [r["contour_elev_m"] for r in csv.DictReader(f) if r.get("contour_elev_m")]
    decimals = max((len(s.split(".")[1]) if "." in s else 0) for s in sample)
    levels = sorted({round(r["contour_elev_m"], decimals) for r in rows})
    log("{} ages, {} distinct elevations from {} to {} m".format(
        len(rows), len(levels), levels[0], levels[-1]))

    dem = prepare_dem(cfg, gdb)
    raw = make_contours(dem, levels, gdb)
    lines = tag_lines_with_age(raw, rows, gdb, cfg["MIN_LINE_KM"], decimals)
    arcpy.conversion.FeatureClassToShapefile(lines, os.path.join(out_dir, "shp"))

    land = None
    if cfg["MAKE_LAND"]:
        log("Building exposed land polygons")
        land = make_land(dem, rows, gdb, cfg["MIN_LAND_KM2"], decimals)
        arcpy.conversion.FeatureClassToShapefile(land, os.path.join(out_dir, "shp"))

    log("Exporting GeoJSON for the web viewer")
    nd = int(cfg.get("COORD_DECIMALS", 4))
    contours_dir = os.path.join(web, "contours")
    line_files = export_geojson(simplify(lines, cfg["SIMPLIFY_M"], "line"),
                                rows, contours_dir, "shoreline", nd)
    land_files = {}
    if land:
        land_files = export_geojson(simplify(land, cfg["SIMPLIFY_M"], "poly"),
                                    rows, contours_dir, "land", nd)

    ext = arcpy.Describe(dem).extent.projectAs(arcpy.SpatialReference(4326))
    manifest = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "source_dem": os.path.basename(cfg["DEM"]),
        "sea_level_table": os.path.basename(cfg["SEA_LEVEL_CSV"]),
        "bounds": [round(ext.XMin, 3), round(ext.YMin, 3), round(ext.XMax, 3), round(ext.YMax, 3)],
        "ages": [],
    }
    for r in sorted(rows, key=lambda r: r["age_ka"]):
        entry = {k: v for k, v in r.items() if v is not None}
        entry["shoreline"] = line_files[r["age_ka"]]
        if land_files:
            entry["land"] = land_files[r["age_ka"]]
        manifest["ages"].append(entry)
    with open(os.path.join(web, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)

    arcpy.CheckInExtension("Spatial")
    log("Done. Copy everything in {} into the viewer's docs/data/ folder.".format(web))


if __name__ == "__main__":
    main(read_parameters(dict(CONFIG)))
