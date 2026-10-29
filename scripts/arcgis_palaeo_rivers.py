# -*- coding: utf-8 -*-
"""
ArcGIS Pro: palaeo-river network and palaeolakes on the exposed shelf, from
GEBCO, for the web viewer.

The drainage is routed on the seabed as it stood at the lowest sea level
(LGM, -121 m): cells below that level are the sea and act as outlets. Every
river piece carries the seabed elevation at its midpoint, so the viewer shows
only the pieces that are dry land at the sea level of each age. Lakes are
closed depressions on the shelf (e.g. Lake Carpentaria behind the -53 m
Arafura Sill); each carries its spill (overflow) elevation, so the viewer
shows a lake only while the sea stands below its sill.

Run it like arcgis_contours.py:
  1. ArcGIS Pro Python window / Notebook: edit CONFIG, then
         exec(open(r"C:\\path\\to\\arcgis_palaeo_rivers.py").read())
  2. Python Command Prompt (arcgispro-py3):
         python arcgis_palaeo_rivers.py

Outputs (in OUT_DIR)
  - rivers.gdb/rivers, rivers.gdb/lakes      full-detail feature classes
  - shp/rivers.shp, shp/lakes.shp            shapefile copies
  - web/overlays/rivers.geojson, lakes.geojson
Copy web/overlays/*.geojson into the viewer's docs/data/overlays/ folder.

Needs Spatial Analyst. Routing on GEBCO (15 arc-second, ~460 m) on a flat
shelf is approximate: channel positions are indicative, not mapped valleys.
"""
import datetime
import json
import math
import os

import arcpy

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
CONFIG = {
    # Same GEBCO raster as arcgis_contours.py (WGS84, metres)
    "DEM": r"C:\data\GEBCO\gebco_2024_sahul.tif",
    "OUT_DIR": r"C:\data\rivers_out",
    # Clip extent "xmin ymin xmax ymax" or "" for the whole DEM
    "EXTENT": "104.5 -21.5 141.5 0",
    # Sea level used for routing (lowest in the record); cells below are sea
    "LOWSTAND_M": -121.1,
    # Mean filter (cells) to soften GEBCO artefacts; 0 = off
    "SMOOTH_CELLS": 3,
    # A channel starts where the upstream catchment exceeds this (km2).
    # 1000 keeps major rivers only; 250 gives a denser network (bigger files)
    "MIN_CATCHMENT_KM2": 1000,
    # Export only pieces below this elevation (m): 0 = shelf only (rivers that
    # are underwater today); e.g. 50 to include the lower reaches on land
    "MAX_EXPORT_ELEV_M": 0,
    # River pieces are cut to this length so each can be shown or hidden by age
    "PIECE_KM": 3,
    # Palaeolakes: closed depressions at least this deep (m) and this big (km2)
    "LAKE_MIN_DEPTH_M": 2.0,
    "LAKE_MIN_KM2": 200,
    # Simplification for the web copies (m) and coordinate decimals
    "SIMPLIFY_M": 200,
    "COORD_DECIMALS": 4,
}


def log(msg):
    print(msg)
    arcpy.AddMessage(msg)


def cell_km2(dem):
    """Mean cell area in km2 (geographic DEM: shrink by cos(latitude))."""
    d = arcpy.Describe(dem)
    cs = float(d.meanCellWidth)
    if d.spatialReference.type == "Geographic":
        lat = (d.extent.YMin + d.extent.YMax) / 2
        return (cs * 111.32) ** 2 * math.cos(math.radians(lat))
    return (cs / 1000.0) ** 2


def prepare_dem(cfg, gdb):
    dem = cfg["DEM"]
    if cfg["EXTENT"].strip():
        log("Clipping DEM to " + cfg["EXTENT"])
        clipped = os.path.join(gdb, "dem_clip")
        arcpy.management.Clip(dem, cfg["EXTENT"], clipped, "#", "#", "NONE", "NO_MAINTAIN_EXTENT")
        dem = clipped
    if cfg["SMOOTH_CELLS"] and cfg["SMOOTH_CELLS"] > 1:
        n = int(cfg["SMOOTH_CELLS"])
        log("Smoothing DEM with {0}x{0} mean filter".format(n))
        sm = arcpy.sa.FocalStatistics(dem, arcpy.sa.NbrRectangle(n, n, "CELL"), "MEAN", "DATA")
        dem = os.path.join(gdb, "dem_smooth")
        sm.save(dem)
    return dem


def make_lakes(dem_land, filled, gdb, cfg, ckm2):
    """Closed depressions -> polygons with floor and spill elevations."""
    log("Finding palaeolakes (depressions > {} m deep, > {} km2)".format(
        cfg["LAKE_MIN_DEPTH_M"], cfg["LAKE_MIN_KM2"]))
    depth = arcpy.sa.Minus(filled, dem_land)
    wet = arcpy.sa.Con(depth > cfg["LAKE_MIN_DEPTH_M"], 1)
    regions = arcpy.sa.RegionGroup(wet, "EIGHT", "WITHIN", "NO_LINK")
    regions.save(os.path.join(gdb, "lake_regions"))
    zs = os.path.join(gdb, "lake_stats")
    arcpy.sa.ZonalStatisticsAsTable(regions, "Value", filled, zs, "DATA", "MIN_MAX_MEAN")
    zf = os.path.join(gdb, "lake_floor")
    arcpy.sa.ZonalStatisticsAsTable(regions, "Value", dem_land, zf, "DATA", "MINIMUM")
    spill, floor, count = {}, {}, {}
    with arcpy.da.SearchCursor(zs, ["Value", "MAX", "COUNT"]) as cur:
        for v, mx, n in cur:
            spill[v], count[v] = mx, n
    with arcpy.da.SearchCursor(zf, ["Value", "MIN"]) as cur:
        for v, mn in cur:
            floor[v] = mn
    keep = [v for v in spill if count[v] * ckm2 >= cfg["LAKE_MIN_KM2"]]
    log("  {} lakes kept of {} depressions".format(len(keep), len(spill)))
    raw = os.path.join(gdb, "lakes_raw")
    arcpy.conversion.RasterToPolygon(regions, raw, "SIMPLIFY", "Value")
    lakes = os.path.join(gdb, "lakes")
    arcpy.management.Dissolve(raw, lakes, "gridcode")
    for f, t in (("spill_m", "DOUBLE"), ("floor_m", "DOUBLE"), ("area_km2", "DOUBLE")):
        arcpy.management.AddField(lakes, f, t)
    keep = set(keep)
    with arcpy.da.UpdateCursor(lakes, ["gridcode", "spill_m", "floor_m", "area_km2", "SHAPE@AREA"]) as cur:
        for row in cur:
            v = row[0]
            if v not in keep:
                cur.deleteRow()
                continue
            row[1], row[2], row[3] = round(spill[v], 1), round(floor[v], 1), round(count[v] * ckm2)
            cur.updateRow(row)
    return lakes


def make_rivers(dem_land, filled, gdb, cfg, ckm2):
    log("Routing flow on the lowstand land surface")
    fdir = arcpy.sa.FlowDirection(filled, "FORCE", None, "D8")
    fdir.save(os.path.join(gdb, "flowdir"))
    acc = arcpy.sa.FlowAccumulation(fdir, None, "FLOAT", "D8")
    acc.save(os.path.join(gdb, "flowacc"))
    thr = cfg["MIN_CATCHMENT_KM2"] / ckm2
    log("  channels where catchment > {} km2 ({:.0f} cells)".format(cfg["MIN_CATCHMENT_KM2"], thr))
    streams = arcpy.sa.Con(acc > thr, 1)
    order = arcpy.sa.StreamOrder(streams, fdir, "STRAHLER")
    order.save(os.path.join(gdb, "stream_order"))
    link = arcpy.sa.StreamLink(streams, fdir)
    link.save(os.path.join(gdb, "stream_link"))
    lines = os.path.join(gdb, "river_links")
    arcpy.sa.StreamToFeature(link, fdir, lines, "SIMPLIFY")

    # per-link Strahler order and catchment (taken at the link's downstream end = max)
    def zonal_max(raster, name):
        t = os.path.join(gdb, name)
        arcpy.sa.ZonalStatisticsAsTable(link, "Value", raster, t, "DATA", "MAXIMUM")
        with arcpy.da.SearchCursor(t, ["Value", "MAX"]) as cur:
            return {v: m for v, m in cur}
    ordmax = zonal_max(order, "link_order")
    accmax = zonal_max(acc, "link_acc")

    log("Cutting rivers into {} km pieces and sampling seabed elevation".format(cfg["PIECE_KM"]))
    simple = os.path.join(gdb, "river_simple")
    if cfg["SIMPLIFY_M"]:
        arcpy.cartography.SimplifyLine(lines, simple, "POINT_REMOVE", "{} Meters".format(cfg["SIMPLIFY_M"]),
                                       collapsed_point_option="NO_KEEP")
    else:
        arcpy.management.CopyFeatures(lines, simple)
    cuts = os.path.join(gdb, "river_cuts")
    arcpy.management.GeneratePointsAlongLines(simple, cuts, "DISTANCE", "{} Kilometers".format(cfg["PIECE_KM"]))
    pieces = os.path.join(gdb, "rivers")
    arcpy.management.SplitLineAtPoint(simple, cuts, pieces, "1 Meters")
    mids = os.path.join(gdb, "river_mids")
    arcpy.management.FeatureVerticesToPoints(pieces, mids, "MID")
    midz = os.path.join(gdb, "river_mids_z")
    arcpy.sa.ExtractValuesToPoints(mids, dem_land, midz, "NONE", "VALUE_ONLY")
    z = {}
    with arcpy.da.SearchCursor(midz, ["ORIG_FID", "RASTERVALU"]) as cur:
        for fid, v in cur:
            z[fid] = v
    for f, t in (("z", "DOUBLE"), ("order", "SHORT"), ("area_km2", "DOUBLE")):
        arcpy.management.AddField(pieces, f, t)
    oid = arcpy.Describe(pieces).OIDFieldName
    kept = 0
    with arcpy.da.UpdateCursor(pieces, [oid, "grid_code", "z", "order", "area_km2"]) as cur:
        for row in cur:
            zz = z.get(row[0])
            if zz is None or zz < -9000 or zz > cfg["MAX_EXPORT_ELEV_M"]:
                cur.deleteRow()
                continue
            row[2] = round(zz, 1)
            row[3] = int(ordmax.get(row[1], 1))
            row[4] = round(accmax.get(row[1], 0) * ckm2)
            cur.updateRow(row)
            kept += 1
    log("  {} river pieces kept".format(kept))
    return pieces


def _round(c, nd):
    if isinstance(c[0], (int, float)):
        return [round(c[0], nd), round(c[1], nd)]
    return [_round(x, nd) for x in c]


def export(fc, fields, path, nd, simplify_m, kind):
    if simplify_m:
        out = fc + "_web"
        tol = "{} Meters".format(simplify_m)
        if kind == "line":
            arcpy.cartography.SimplifyLine(fc, out, "POINT_REMOVE", tol, collapsed_point_option="NO_KEEP")
        else:
            arcpy.cartography.SimplifyPolygon(fc, out, "POINT_REMOVE", tol, collapsed_point_option="NO_KEEP")
        fc = out
    feats = []
    with arcpy.da.SearchCursor(fc, ["SHAPE@"] + fields, spatial_reference=arcpy.SpatialReference(4326)) as cur:
        for row in cur:
            if row[0] is None:
                continue
            g = row[0].__geo_interface__
            feats.append({"type": "Feature",
                          "properties": dict(zip(fields, row[1:])),
                          "geometry": {"type": g["type"], "coordinates": _round(g["coordinates"], nd)}})
    with open(path, "w") as f:
        json.dump({"type": "FeatureCollection",
                   "generated": datetime.datetime.now().isoformat(timespec="seconds"),
                   "features": feats}, f, separators=(",", ":"))
    log("  wrote {} ({} features)".format(path, len(feats)))


def main(cfg):
    if arcpy.CheckExtension("Spatial") != "Available":
        raise RuntimeError("Spatial Analyst licence is not available")
    arcpy.CheckOutExtension("Spatial")
    arcpy.env.overwriteOutput = True
    out_dir = cfg["OUT_DIR"]
    web = os.path.join(out_dir, "web", "overlays")
    for d in (out_dir, os.path.join(out_dir, "shp"), web):
        if not os.path.isdir(d):
            os.makedirs(d)
    gdb = os.path.join(out_dir, "rivers.gdb")
    if not arcpy.Exists(gdb):
        arcpy.management.CreateFileGDB(out_dir, "rivers.gdb")
    arcpy.env.workspace = gdb
    arcpy.env.scratchWorkspace = gdb

    dem = prepare_dem(cfg, gdb)
    arcpy.env.snapRaster = dem
    arcpy.env.cellSize = dem
    ckm2 = cell_km2(dem)
    log("Cell area about {:.3f} km2".format(ckm2))

    # Lowstand land surface: the sea (below LOWSTAND_M) is NoData, so rivers end at the LGM coast
    dem_land = arcpy.sa.Con(arcpy.Raster(dem) > cfg["LOWSTAND_M"], dem)
    dem_land.save(os.path.join(gdb, "dem_lowstand"))
    filled = arcpy.sa.Fill(dem_land)
    filled.save(os.path.join(gdb, "dem_filled"))

    lakes = make_lakes(dem_land, filled, gdb, cfg, ckm2)
    rivers = make_rivers(dem_land, filled, gdb, cfg, ckm2)
    arcpy.conversion.FeatureClassToShapefile([rivers, lakes], os.path.join(out_dir, "shp"))

    log("Exporting GeoJSON for the web viewer")
    nd = int(cfg["COORD_DECIMALS"])
    export(rivers, ["z", "order", "area_km2"], os.path.join(web, "rivers.geojson"), nd, 0, "line")
    export(lakes, ["spill_m", "floor_m", "area_km2"], os.path.join(web, "lakes.geojson"), nd, cfg["SIMPLIFY_M"], "poly")
    arcpy.CheckInExtension("Spatial")
    log("Done. Copy {} into the viewer's docs/data/overlays/ folder.".format(web))


if __name__ == "__main__":
    main(dict(CONFIG))
