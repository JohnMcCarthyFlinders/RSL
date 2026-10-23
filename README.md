# Sahul Shelf Shorelines

Palaeo-shorelines of the Arafura and Timor Sea (Sahul shelf) for 0–100 ka,
shown in a static web map with a time slider.

## Pipeline

1. **Sea level curve → 1 kyr steps**

   ```
   python scripts/rebin_sea_level.py rsl_composite_0-100ka.csv -o docs/data/sea_level_1ka.csv \
       --age-col age_ka --sl-col sea_level_m --extra-cols err_lo err_up --method linear
   ```

   Accepts any CSV/TSV/NOAA text table. Methods: `linear`, `pchip` (smooth,
   no overshoot), `gaussian --bandwidth 2` (for noisy scattered points).
   `--zero-at-present` shifts the curve so 0 ka = 0 m (needed for raw
   Spratt & Lisiecki 2016, which sits at about +8 m at 0 ka).
   Output columns: `age_ka, sea_level_m, contour_elev_m, nearest_obs_kyr, …`.

2. **ArcGIS Pro: contours at those elevations**

   - Load GEBCO and clip it to the shelf (Clip Raster, extent `104.5 -21.5 141.5 0`).
   - Open `scripts/arcgis_contours.py`, set `DEM`, `SEA_LEVEL_CSV` and `OUT_DIR`
     in `CONFIG`, and run it from the Python window:
     `exec(open(r"C:\path\to\arcgis_contours.py").read())`
   - Needs Spatial Analyst. Output: `shorelines.gdb`, shapefiles in `shp/`, and
     web files in `web/` (`manifest.json` + one shoreline and land GeoJSON per age).

3. **Viewer**

   Copy everything in `OUT_DIR/web/` into `docs/data/` (replacing the demo
   files), commit and push. In the repo's Settings → Pages, serve from the
   `main` branch, `/docs` folder.

   Point overlays: edit `docs/data/overlays/sites.geojson`. Every property is
   shown in the site's popup.

   To preview locally: `cd docs && python -m http.server` then open
   http://localhost:8000.

## Demo data

The files currently in `docs/data/contours/` are synthetic (buffered outlines
of the modern coast), made by `scripts/make_demo_data.py` so the viewer can be
tried before the ArcGIS run. The viewer shows a banner while they are in use.
