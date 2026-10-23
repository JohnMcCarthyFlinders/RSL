#!/usr/bin/env python3
"""
Rebin an irregularly sampled relative sea level (RSL) curve onto a regular
age grid (default 0-100 ka in 1 kyr steps).

Works with plain numpy (pandas/matplotlib optional), so it runs in a normal
Python install or in ArcGIS Pro's arcgispro-py3 environment.

Input: any text table (NOAA paleo .txt, CSV, TSV, whitespace). Lines starting
with '#' are skipped. One column holds age, one holds sea level in metres
(negative = below present). Extra columns (e.g. uncertainty bounds) can be
interpolated too.

Methods
  linear    piecewise linear between data points (honest, no overshoot)
  pchip     shape-preserving cubic (smooth, no overshoot between points)
  gaussian  Gaussian kernel-weighted mean with bandwidth --bandwidth ka.
            Use for noisy, scattered data (many samples, multiple per age).

Examples
  # Spratt & Lisiecki 2016, shift so 0 ka = 0 m
  python rebin_sea_level.py spratt2016-noaa.txt -o sea_level_1ka.csv \
      --age-col age_calkaBP --sl-col SeaLev_shortPC1 --zero-at-present \
      --extra-cols SeaLev_shortPC1_err_lo SeaLev_shortPC1_err_hi

  # Scattered sample ages in years BP, smoothed with a 2 kyr kernel
  python rebin_sea_level.py samples.csv -o sea_level_1ka.csv \
      --age-units yr --method gaussian --bandwidth 2
"""
import argparse
import csv
import math
import re
import sys

import numpy as np


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def _is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def _split(line, delim):
    if delim == "whitespace":
        return line.split()
    return [c.strip() for c in line.split(delim)]


def read_table(path):
    """Return (header, rows) from a commented, delimited text file."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        lines = [ln.rstrip("\r\n") for ln in f]
    body = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    if not body:
        sys.exit(f"No data lines found in {path}")

    sample = "\n".join(body[:20])
    if "\t" in sample:
        delim = "\t"
    elif "," in sample:
        delim = ","
    elif ";" in sample:
        delim = ";"
    else:
        delim = "whitespace"

    first = _split(body[0], delim)
    if all(_is_number(c) for c in first if c != ""):
        header = [f"col{i}" for i in range(len(first))]
        data_lines = body
    else:
        header = first
        data_lines = body[1:]

    rows = []
    for ln in data_lines:
        parts = _split(ln, delim)
        if not any(_is_number(p) for p in parts):
            continue  # stray text line
        rows.append(parts)
    return header, rows


def pick_column(header, wanted, patterns, label):
    if wanted is not None:
        if wanted in header:
            return header.index(wanted)
        if wanted.isdigit() and int(wanted) < len(header):
            return int(wanted)
        sys.exit(f"{label} column '{wanted}' not found. Columns: {header}")
    for pat in patterns:
        for i, h in enumerate(header):
            if re.search(pat, h, re.IGNORECASE):
                return i
    sys.exit(f"Could not guess the {label} column; pass it explicitly. Columns: {header}")


def column(rows, idx):
    out = []
    for r in rows:
        try:
            v = float(r[idx])
            out.append(v if math.isfinite(v) else np.nan)
        except (ValueError, IndexError):
            out.append(np.nan)
    return np.asarray(out, dtype=float)


# --------------------------------------------------------------------------
# Interpolators
# --------------------------------------------------------------------------

def collapse_duplicates(x, y):
    """Average y at duplicate x so interpolators get strictly increasing x."""
    order = np.argsort(x, kind="mergesort")
    x, y = x[order], y[order]
    ux, inv = np.unique(x, return_inverse=True)
    uy = np.bincount(inv, weights=y) / np.bincount(inv)
    return ux, uy


def interp_linear(x, y, xi):
    return np.interp(xi, x, y, left=np.nan, right=np.nan)


def _pchip_slopes(x, y):
    h = np.diff(x)
    delta = np.diff(y) / h
    n = len(x)
    d = np.zeros(n)
    if n == 2:
        d[:] = delta[0]
        return d
    for k in range(1, n - 1):
        if delta[k - 1] * delta[k] <= 0:
            d[k] = 0.0
        else:
            w1 = 2 * h[k] + h[k - 1]
            w2 = h[k] + 2 * h[k - 1]
            d[k] = (w1 + w2) / (w1 / delta[k - 1] + w2 / delta[k])

    def end(h0, h1, d0, d1):
        s = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
        if np.sign(s) != np.sign(d0):
            return 0.0
        if np.sign(d0) != np.sign(d1) and abs(s) > abs(3 * d0):
            return 3 * d0
        return s

    d[0] = end(h[0], h[1], delta[0], delta[1])
    d[-1] = end(h[-1], h[-2], delta[-1], delta[-2])
    return d


def interp_pchip(x, y, xi):
    """Fritsch-Carlson monotone cubic (same as scipy PchipInterpolator)."""
    if len(x) < 3:
        return interp_linear(x, y, xi)
    d = _pchip_slopes(x, y)
    out = np.full(len(xi), np.nan)
    inside = (xi >= x[0]) & (xi <= x[-1])
    xs = xi[inside]
    k = np.clip(np.searchsorted(x, xs, side="right") - 1, 0, len(x) - 2)
    h = x[k + 1] - x[k]
    t = (xs - x[k]) / h
    h00 = (1 + 2 * t) * (1 - t) ** 2
    h10 = t * (1 - t) ** 2
    h01 = t ** 2 * (3 - 2 * t)
    h11 = t ** 2 * (t - 1)
    out[inside] = h00 * y[k] + h10 * h * d[k] + h01 * y[k + 1] + h11 * h * d[k + 1]
    return out


def interp_gaussian(x, y, xi, bandwidth, min_weight=0.05):
    """Kernel-weighted mean. NaN where there is too little nearby data."""
    out = np.full(len(xi), np.nan)
    for j, a in enumerate(xi):
        w = np.exp(-0.5 * ((x - a) / bandwidth) ** 2)
        sw = w.sum()
        if sw >= min_weight:
            out[j] = (w * y).sum() / sw
    return out


def interpolate(x, y, xi, method, bandwidth):
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 2:
        return np.full(len(xi), np.nan)
    if method == "gaussian":
        return interp_gaussian(x, y, xi, bandwidth)
    x, y = collapse_duplicates(x, y)
    if method == "pchip":
        return interp_pchip(x, y, xi)
    return interp_linear(x, y, xi)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", help="sea level table (txt/csv/tsv)")
    p.add_argument("-o", "--output", default="sea_level_1ka.csv")
    p.add_argument("--age-col", help="age column name or 0-based index (default: guess)")
    p.add_argument("--sl-col", help="sea level column name or index (default: guess)")
    p.add_argument("--extra-cols", nargs="*", default=[],
                   help="other columns to interpolate too (e.g. error bounds)")
    p.add_argument("--age-units", choices=["ka", "yr"], default="ka",
                   help="units of the input age column (yr = years BP)")
    p.add_argument("--method", choices=["linear", "pchip", "gaussian"], default="pchip")
    p.add_argument("--bandwidth", type=float, default=1.5,
                   help="Gaussian kernel sigma in kyr (method=gaussian)")
    p.add_argument("--start", type=float, default=0.0, help="first age, ka")
    p.add_argument("--end", type=float, default=100.0, help="last age, ka")
    p.add_argument("--step", type=float, default=1.0, help="step, kyr")
    p.add_argument("--zero-at-present", action="store_true",
                   help="shift the curve so the value at 0 ka is exactly 0 m")
    p.add_argument("--offset", type=float, default=0.0,
                   help="constant (m) added to sea level after any zeroing")
    p.add_argument("--extrapolate", choices=["none", "nearest"], default="none",
                   help="fill ages outside the data range (default leaves them blank)")
    p.add_argument("--round", type=int, default=1,
                   help="decimals for the contour_elev_m column used by ArcGIS")
    p.add_argument("--plot", help="optional PNG comparing raw and rebinned curves")
    a = p.parse_args(argv)

    header, rows = read_table(a.input)
    ai = pick_column(header, a.age_col, [r"^age", r"age", r"ka", r"bp"], "age")
    si = pick_column(header, a.sl_col, [r"sealev", r"\brsl\b", r"sea.?level", r"^sl"], "sea level")
    age = column(rows, ai)
    if a.age_units == "yr":
        age = age / 1000.0
    sl = column(rows, si)
    extras = {name: column(rows, pick_column(header, name, [], name)) for name in a.extra_cols}

    n = int(round((a.end - a.start) / a.step)) + 1
    grid = np.round(a.start + a.step * np.arange(n), 6)

    sl_i = interpolate(age, sl, grid, a.method, a.bandwidth)
    ex_i = {k: interpolate(age, v, grid, a.method, a.bandwidth) for k, v in extras.items()}

    if a.extrapolate == "nearest":
        for arr in [sl_i, *ex_i.values()]:
            good = np.where(np.isfinite(arr))[0]
            if len(good):
                arr[: good[0]] = arr[good[0]]
                arr[good[-1] + 1:] = arr[good[-1]]

    shift = a.offset
    if a.zero_at_present:
        at0 = interpolate(age, sl, np.array([0.0]), a.method, a.bandwidth)[0]
        if not np.isfinite(at0):
            at0 = sl[np.nanargmin(np.abs(age))]
        shift -= at0
        print(f"Zeroing: curve value at 0 ka was {at0:+.2f} m; shifting by {-at0:+.2f} m")
    sl_i = sl_i + shift
    for k in ex_i:
        ex_i[k] = ex_i[k] + shift

    # Distance (kyr) from each grid age to the nearest real observation:
    # a quick way to flag where the curve is interpolated across long gaps.
    obs = np.sort(age[np.isfinite(age) & np.isfinite(sl)])
    gap = np.array([np.min(np.abs(obs - g)) for g in grid])

    with open(a.output, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["age_ka", "sea_level_m", "contour_elev_m", "nearest_obs_kyr", *ex_i.keys()])
        for j, g in enumerate(grid):
            v = sl_i[j]
            if not np.isfinite(v):
                w.writerow([f"{g:g}", "", "", f"{gap[j]:.2f}", *["" for _ in ex_i]])
                continue
            w.writerow([f"{g:g}", f"{v:.2f}", f"{round(v, a.round):.{a.round}f}",
                        f"{gap[j]:.2f}", *[f"{ex_i[k][j]:.2f}" for k in ex_i]])

    valid = np.isfinite(sl_i)
    print(f"Read {len(rows)} rows ({header[ai]!r} vs {header[si]!r}); "
          f"data span {np.nanmin(age):g}-{np.nanmax(age):g} ka")
    print(f"Wrote {a.output}: {valid.sum()}/{n} ages filled, method={a.method}, "
          f"sea level {np.nanmin(sl_i):.1f} to {np.nanmax(sl_i):.1f} m")
    if (~valid).any():
        print(f"  Blank (outside data range): {', '.join(f'{g:g}' for g in grid[~valid])} ka")

    if a.plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            print("matplotlib not available, skipping plot")
            return
        fig, ax = plt.subplots(figsize=(9, 4))
        m = (age >= a.start - 5) & (age <= a.end + 5)
        ax.plot(age[m], sl[m] + shift, ".", color="0.6", ms=4, label="input")
        ax.plot(grid, sl_i, "-o", ms=3, lw=1.5, label=f"rebinned ({a.method})")
        ax.set_xlabel("Age (ka)")
        ax.set_ylabel("Sea level (m rel. present)")
        ax.set_xlim(a.start, a.end)
        ax.grid(alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(a.plot, dpi=150)
        print(f"Plot saved to {a.plot}")


if __name__ == "__main__":
    main()
