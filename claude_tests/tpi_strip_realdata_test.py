# Wildlife and Forestry Tools for ArcGIS Pro
# Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version, together with the additional terms
# in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
# Memorial Award notice, marking of modified versions, and
# use of names).
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Strip-engine real-data test (2026-11-10) on windows of Jeff's All_US
1-arc-second NAD83 DEM (D:/GIS_Data/DEM_Stuff/Full_DEM_Data.gdb/All_US_NoNull)
and on the whole 21.2-billion-cell raster.

  --window <dem>   both engines (frozen whole-raster vs strips), Landform
                   with METER radii on the geographic DEM (per-row varying
                   cell radii, several row groups) and the TPI tool; outputs
                   compared cell by cell, attribute tables and metadata.
  --big <dem>      strip engine only: Landform with Shay's parameters
                   (3 / 15 cells, SD TPI, slope in degrees); wall time and
                   peak working set.
  --full           the same on the whole All_US raster (hours).
  --out <folder>   where outputs go (default claude_strip_test/out beside
                   the repo).

Run with ArcGIS Pro's Python.
"""
import json
import os
import sys
import time

import numpy as np
import psutil

HERE = os.path.dirname(os.path.abspath(__file__))
TB = os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes")
sys.path.insert(0, TB)
sys.path.insert(0, HERE)

import arcpy                                   # noqa: E402
import mahalanobis as _mh                      # noqa: E402
_mh._confirm = lambda *a, **k: True            # headless: no Yes/No dialog on the large-raster warning
import tpi_tools as new                        # noqa: E402
import tpi_tools_whole_ref as ref              # noqa: E402
import tpi_flat_check as fc                    # noqa: E402

FULL = r"D:\GIS_Data\DEM_Stuff\Full_DEM_Data.gdb\All_US_NoNull"
passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok    " + label + (("  [" + detail + "]") if detail else ""), flush=True)
    else:
        failed += 1
        print("  FAIL  " + label + (("  [" + detail + "]") if detail else ""), flush=True)


class Msgs(object):
    def __init__(self, echo=False):
        self.info, self.warn = [], []
        self.echo = echo

    def addMessage(self, s):
        self.info.append(str(s))
        if self.echo:
            print("    msg: " + str(s)[:220], flush=True)

    def addWarningMessage(self, s):
        self.warn.append(str(s))
        if self.echo:
            print("    WARN: " + str(s)[:220], flush=True)

    def addErrorMessage(self, s):
        self.warn.append("ERROR " + str(s))
        if self.echo:
            print("    ERR: " + str(s)[:220], flush=True)


def arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else default
    return default


def read(path):
    return arcpy.RasterToNumPyArray(path, nodata_to_value=np.nan).astype(np.float64)


def read_int(path):
    return arcpy.RasterToNumPyArray(path, nodata_to_value=0).astype(np.int32)


def rat_rows(path):
    fields = [f.name for f in arcpy.ListFields(path)]
    want = [f for f in ("Value", "Count", "Class_Name") if f in fields]
    with arcpy.da.SearchCursor(path, want) as cur:
        return sorted(tuple(r) for r in cur)


def md_of(path):
    """Summary, description and tags (the title is the output's own file
    name, which carries the engine tag)."""
    md = arcpy.metadata.Metadata(path)
    return (md.summary or "", md.description or "", md.tags or "")


def compare_float(label, a, b, exact, flat=None):
    """``flat`` = (dem, (rxo, ryo, rxi, ryi)) for an SD-based output: cells
    beyond the tolerance are accepted only where the engine reported 0 and
    the brute-force SD over the engine's kernel is below the quantization
    floor (tpi_flat_check)."""
    A, B = read(a), read(b)
    na, nb = np.isnan(A), np.isnan(B)
    if A.shape != B.shape or not np.array_equal(na, nb):
        check(label + " NoData masks identical", False)
        return
    fa, fb = A[~na], B[~nb]
    ndiff = int(np.sum(fa != fb))
    scale = max(1.0, float(np.max(np.abs(fa)))) if fa.size else 1.0
    d = float(np.max(np.abs(fa - fb))) / scale if fa.size else 0.0
    if exact:
        check(label + " bit-identical", ndiff == 0, "{} cells differ, max rel {:.3g}".format(ndiff, d))
    elif d <= 1e-6 or flat is None:
        check(label + " within float32 rounding", d <= 1e-6,
              "{} of {} cells differ, max rel {:.3g}".format(ndiff, fa.size, d))
    else:
        z, valid = flat[0]
        ex = fc.explain_mismatch(A, B, z, valid, flat[1], 1e-6)
        check(label + " within float32 rounding except verified quantization-flat cells", ex["ok"],
              ("{} of {} cells differ; {} beyond 1e-6, {} checked by brute force: {} floored, {} match exact "
               "arithmetic, {} within the near-floor error bound (worst {:.2g} = {:.0%} of the bound); "
               "max rel elsewhere {:.3g}{}").format(
                  ndiff, fa.size, ex["n_mismatch"], ex["n_checked"], ex["n_floored"], ex["n_exact"],
                  ex["n_near"], ex["worst_rel_near_floor"], ex["worst_bound_use"], ex["max_rel_elsewhere"],
                  "" if ex["ok"] else "; unexplained: {}".format(ex["bad"][:3])))
    check(label + " metadata identical", md_of(a) == md_of(b))


def compare_class(label, a, b, flat=None):
    """``flat`` = ((z, valid), [radii, ...]): a differing class is accepted
    only where the neighborhood is quantization-flat under at least one of
    the TPI radii that feed the classification (brute force)."""
    A, B = read_int(a), read_int(b)
    ndiff = int(np.sum(A != B))
    if ndiff == 0 or flat is None:
        check(label + " classes identical", ndiff == 0, "{} of {} cells differ".format(ndiff, A.size))
    else:
        (z, valid), radii_list = flat
        bad = []
        cells = np.argwhere(A != B)
        for r, c in cells[:2000]:
            r, c = int(r), int(c)
            if not any(fc.is_floored_cell(z, valid, rad, r, c)[0] for rad in radii_list):
                bad.append((r, c, int(A[r, c]), int(B[r, c])))
        check(label + " classes identical except verified quantization-flat cells", not bad,
              "{} of {} cells differ, {} checked by brute force{}".format(
                  ndiff, A.size, min(len(cells), 2000), "" if not bad else "; unexplained: {}".format(bad[:3])))
    if ndiff == 0:
        check(label + " attribute table identical", rat_rows(a) == rat_rows(b))
    else:
        # the verified cells moved between classes, so the counts differ by
        # construction; the class values and names must still match
        ra, rb = rat_rows(a), rat_rows(b)
        strip = lambda rows: sorted(tuple(x for x in r if not isinstance(x, (int, float)) or x == r[0]) for r in rows)  # noqa: E731
        check(label + " attribute table identical apart from the counts of the verified cells",
              strip(ra) == strip(rb) and sum(r[1] for r in ra) == sum(r[1] for r in rb),
              "{} vs {}".format(ra, rb) if strip(ra) != strip(rb) else "")
    check(label + " metadata identical", md_of(a) == md_of(b))


def landform_sources(dem, small, large, units, saves=None):
    saves = saves or {}
    s = {"tpi_small": {"kind": "tpi", "dem": dem, "shape": "Circle", "outer": small, "inner": 0.0,
                       "units": units, "tpi_type": "dev"},
         "tpi_large": {"kind": "tpi", "dem": dem, "shape": "Circle", "outer": large, "inner": 0.0,
                       "units": units, "tpi_type": "dev"},
         "slope": {"kind": "slope", "dem": dem, "elev_units": "Meters", "slope_units": "Degrees"}}
    for k, p in saves.items():
        s[k]["save"] = p
    return s


def timed(label, fn, echo_msgs=False):
    proc = psutil.Process()
    m = Msgs(echo=echo_msgs)
    t0 = time.time()
    fn(m)
    dt = time.time() - t0
    peak = proc.memory_info().peak_wset / 2 ** 30
    print("  {:<40} {:8.1f} s   peak working set so far {:.2f} GB".format(label, dt, peak), flush=True)
    return m, dt


def window_compare(dem, out):
    system = json.load(open(os.path.join(TB, "classification_seeds",
                                         "weiss_10_class_landform.json"), encoding="utf-8"))
    tag = os.path.splitext(os.path.basename(dem))[0]
    d = arcpy.Describe(dem)
    print("\n=== {}: {} x {} cells ({:.1f} M), {} ===".format(tag, d.width, d.height,
                                                              d.width * d.height / 1e6, d.spatialReference.name))
    runs = {}
    for eng, etag in ((ref, "ref"), (new, "new")):
        def p(n):
            return os.path.join(out, "{}_{}_{}.tif".format(tag, etag, n))
        paths = {"class": p("lf"), "tpi_small": p("lf_ts"), "tpi_large": p("lf_tl"), "slope": p("lf_sl")}
        sources = landform_sources(dem, 100.0, 500.0, "Meters",
                                   {"tpi_small": paths["tpi_small"], "tpi_large": paths["tpi_large"],
                                    "slope": paths["slope"]})
        m, _dt = timed("{} Landform 100 m / 500 m".format(etag),
                       lambda m, pp=paths, ss=sources: eng.run_classification(
                           "landform", dem, pp["class"], system, ss, messages=m))
        tp = {"raw": p("tpi_raw"), "pctl": p("tpi_pctl"), "demstd": p("tpi_demstd")}
        m2, _dt = timed("{} TPI tool raw/pctl/demstd 3 cells".format(etag),
                        lambda m, tp=tp: eng.run_tpi(dem, out_raw=tp["raw"], out_pctl=tp["pctl"],
                                                     out_demstd=tp["demstd"], outer=3.0, units="Cells",
                                                     messages=m))
        paths.update({"tpi_" + k: v for k, v in tp.items()})
        runs[etag] = (paths, m, m2)
    a, b = runs["ref"][0], runs["new"][0]
    for s in runs["new"][1].info:
        if s.startswith("Processing in") or s.startswith("Processing the raster"):
            print("  strip note: " + s)
    check("same Landform warnings", sorted(runs["ref"][1].warn) == sorted(runs["new"][1].warn),
          "{} vs {}".format(runs["ref"][1].warn, runs["new"][1].warn))
    zv = fc.read_dem(dem)
    rad_small = fc.radii_for_raster(dem, 100.0, 0.0, "Meters")
    rad_large = fc.radii_for_raster(dem, 500.0, 0.0, "Meters")
    compare_class(tag + " Landform", a["class"], b["class"], flat=(zv, [rad_small, rad_large]))
    compare_float(tag + " saved small TPI (100 m, direct, centered SD)", a["tpi_small"], b["tpi_small"],
                  exact=False, flat=(zv, rad_small))
    compare_float(tag + " saved large TPI (500 m, FFT)", a["tpi_large"], b["tpi_large"], exact=False,
                  flat=(zv, rad_large))
    compare_float(tag + " saved slope", a["slope"], b["slope"], exact=True)
    compare_float(tag + " TPI raw 3 cells", a["tpi_raw"], b["tpi_raw"], exact=True)
    compare_float(tag + " TPI pctl 3 cells", a["tpi_pctl"], b["tpi_pctl"], exact=True)
    compare_float(tag + " TPI demstd", a["tpi_demstd"], b["tpi_demstd"], exact=False)


def big_run(dem, out, label):
    system = json.load(open(os.path.join(TB, "classification_seeds",
                                         "weiss_10_class_landform.json"), encoding="utf-8"))
    d = arcpy.Describe(dem)
    print("\n=== {}: {} x {} cells ({:.2f} G) ===".format(label, d.width, d.height,
                                                          d.width * d.height / 1e9), flush=True)
    outp = os.path.join(out, label + "_landform.tif")
    sources = landform_sources(dem, 3.0, 15.0, "Cells")
    m, dt = timed("strip engine Landform 3 / 15 cells",
                  lambda m: new.run_classification("landform", dem, outp, system, sources, messages=m),
                  echo_msgs=True)
    check(label + " completed", arcpy.Exists(outp), "{:.1f} min".format(dt / 60.0))
    try:
        print("  attribute table:", rat_rows(outp), flush=True)
    except Exception as e:
        print("  attribute table: ?", e)


def main():
    out = arg("--out", os.path.join(os.path.dirname(os.path.dirname(HERE)), "claude_strip_test", "out"))
    os.makedirs(out, exist_ok=True)
    arcpy.env.overwriteOutput = True
    arcpy.env.scratchWorkspace = out
    budget = arg("--budget")
    if budget:
        new.STRIP_BUDGET_BYTES = int(float(budget) * 2 ** 30)
        print("strip budget {} GiB".format(budget))
    w = arg("--window")
    if w:
        window_compare(w, out)
    b = arg("--big")
    if b:
        big_run(b, out, os.path.splitext(os.path.basename(b))[0])
    if "--full" in sys.argv:
        big_run(FULL, out, "all_us")
    print("\n{} passed, {} failed".format(passed, failed), flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
