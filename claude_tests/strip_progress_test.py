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

"""Strip-by-strip progress: every strip engine drives the geoprocessing
progressor once per strip (and once per part of a strip where the tool has
parts), with a label naming the strip, its rows and the time left in the
phase (raster_strips.Progress).

The test captures arcpy.SetProgressor / SetProgressorPosition /
SetProgressorLabel while each engine runs on a small synthetic raster forced
into several strips, and checks that (1) the bar never moves backwards,
(2) every planned strip is named in a label with its exact rows, (3) a run
that fits in one strip still reports the parts of that strip, (4) the
time-left estimate appears once a run has lasted long enough, and (5)
Progress.text and eta_text format as documented. Outputs are not compared
here; the equivalence suites do that.

    python claude_tests/strip_progress_test.py
"""
import os
import re
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
TB = os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes")
sys.path.insert(0, TB)
sys.path.insert(0, HERE)

import arcpy                                   # noqa: E402
import mahalanobis as _mh                      # noqa: E402
import raster_strips as rs                     # noqa: E402
import tpi_tools as tpi                        # noqa: E402
import topographic_tools as tt                 # noqa: E402
import hillshade as hs                         # noqa: E402
import aspect_tools as asp                     # noqa: E402
import lsri                                    # noqa: E402
import hsm_tools as hsm                        # noqa: E402
import diversity_tools as div                  # noqa: E402
import land_facet_density as den               # noqa: E402
import invert_raster as inv                    # noqa: E402
from tpi_strip_equivalence_test import make_dem, Msgs, load_seed      # noqa: E402
from strip_focal_equivalence_test import make_aspect                  # noqa: E402
from strip_facet_equivalence_test import make_categorical             # noqa: E402

passed = failed = 0
STRIP_RE = re.compile(r"strip ([\d,]+) of ([\d,]+) \((?:DEM )?rows ([\d,]+) to ([\d,]+)\)")


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok    " + label + (("  [" + detail + "]") if detail else ""), flush=True)
    else:
        failed += 1
        print("  FAIL  " + label + (("  [" + detail + "]") if detail else ""), flush=True)


class Capture(object):
    """Records every progressor call the engines make through arcpy."""

    def __init__(self):
        self.events = []

    def install(self):
        self.saved = (arcpy.SetProgressor, arcpy.SetProgressorPosition, arcpy.SetProgressorLabel)
        arcpy.SetProgressor = lambda *a, **k: self.events.append(("begin", a[1] if len(a) > 1 else ""))
        arcpy.SetProgressorPosition = lambda p: self.events.append(("pos", int(p)))
        arcpy.SetProgressorLabel = lambda t: self.events.append(("label", str(t)))
        self.stamps = []
        orig = arcpy.SetProgressorLabel
        arcpy.SetProgressorLabel = lambda t: (self.stamps.append(time.time()), orig(t))

    def strip_span(self):
        """Seconds between the first and the last strip label."""
        ts = [s for s, t in zip(self.stamps, self.labels()) if STRIP_RE.search(t)]
        return (ts[-1] - ts[0]) if len(ts) > 1 else 0.0

    def restore(self):
        arcpy.SetProgressor, arcpy.SetProgressorPosition, arcpy.SetProgressorLabel = self.saved

    def labels(self):
        return [v for k, v in self.events if k == "label"]

    def position_runs(self):
        """Position sequences between begin_phases calls (each one restarts
        the bar at zero by design)."""
        runs, cur = [], []
        for k, v in self.events:
            if k == "begin":
                if cur:
                    runs.append(cur)
                cur = []
            elif k == "pos":
                cur.append(v)
        if cur:
            runs.append(cur)
        return runs

    def monotonic(self):
        return all(all(b >= a for a, b in zip(run, run[1:])) for run in self.position_runs())

    def strip_labels(self):
        out = []
        for t in self.labels():
            m = STRIP_RE.search(t)
            if m:
                out.append(tuple(int(g.replace(",", "")) for g in m.groups()))
        return out


def num(s):
    return int(str(s).replace(",", ""))


def unit_checks():
    print("\n--- Progress.text and eta_text ---", flush=True)
    check("eta 45 s", rs.eta_text(45) == "about 45 s", rs.eta_text(45))
    check("eta 89 s stays in seconds", rs.eta_text(89) == "about 89 s", rs.eta_text(89))
    check("eta 90 s -> minutes", rs.eta_text(90) == "about 2 min", rs.eta_text(90))
    check("eta 10 min", rs.eta_text(600) == "about 10 min", rs.eta_text(600))
    check("eta 1 h 05 min", rs.eta_text(3900) == "about 1 h 05 min", rs.eta_text(3900))
    check("eta rounds 59.98 min up to the next hour", rs.eta_text(7199) == "about 2 h 00 min", rs.eta_text(7199))
    check("eta never below 1 s", rs.eta_text(0.2) == "about 1 s", rs.eta_text(0.2))

    p = rs.Progress("Computing TPI", [(0, 10, 0, 12), (10, 20, 8, 22), (20, 30, 18, 30)])
    t = p.text(1, 0.0, 10.0)
    check("three strips, second starting", t == "Computing TPI: strip 2 of 3 (rows 11 to 20), about 20 s remaining", t)
    t = p.text(0, 0.5, 5.0)
    check("part of a strip", t == "Computing TPI: strip 1 of 3 (rows 1 to 10), 50%, about 25 s remaining", t)
    t = p.text(0, 0.0, 0.0)
    check("no estimate before any work", t == "Computing TPI: strip 1 of 3 (rows 1 to 10)", t)
    t = p.text(2, 1.0, 30.0)
    check("last strip done: no estimate", t == "Computing TPI: strip 3 of 3 (rows 21 to 30)", t)
    one = rs.Progress("Calculating hillshade", [(0, 100)])
    t = one.text(0, 0.25, 4.0)
    check("one strip, a quarter done", t == "Calculating hillshade: 25% done, about 12 s remaining", t)
    t = one.text(0, 1.0, 9.0)
    check("one strip finished", t == "Calculating hillshade: done", t)

    # update(): bar forward only, partial labels at most once a second
    cap = Capture()
    cap.install()
    try:
        _mh.begin_phases([("Test...", 1)])
        p = rs.Progress("L", [(0, 10), (10, 20), (20, 30), (30, 40)])
        p.update(0, 0.0)
        p.update(0, 0.5)          # within a second of the last label: bar only
        p.update(0, 1.0)
        p.update(1, 0.0)
        p.update(0, 0.2)          # backwards: ignored
        p.update(1, 1.0)
        _mh.end_phases()
    finally:
        cap.restore()
    pos = cap.position_runs()[-1]
    check("update positions forward only", all(b >= a for a, b in zip(pos, pos[1:])), str(pos))
    check("update positions follow the strips", pos[:4] == [0, 125, 250, 250] and pos[-2] == 500, str(pos))
    check("partial-strip label throttled", len(cap.labels()) == 4, "; ".join(cap.labels()))


def run_case(name, fn, planned, rows_check=True, strips_check=True):
    cap = Capture()
    cap.install()
    t0 = time.time()
    err = None
    try:
        _mh.begin_phases([("Test phase...", 1)])
        fn(Msgs())
        _mh.end_phases()
    except Exception:
        err = traceback.format_exc()
    finally:
        cap.restore()
    wall = time.time() - t0
    if err:
        check("{} ran".format(name), False, err.strip().splitlines()[-1])
        print(err)
        return
    check("{} ran".format(name), True, "{:.1f} s, {} positions, {} labels".format(
        wall, sum(len(r) for r in cap.position_runs()), len(cap.labels())))
    check("{} bar never moves backwards".format(name), cap.monotonic(),
          "; ".join(str(r[:12]) for r in cap.position_runs()))
    sl = cap.strip_labels()
    n = len(planned)
    if strips_check:
        check("{} labels count {} strips".format(name, n), bool(sl) and all(s[1] == n for s in sl),
              "counts seen: {}".format(sorted(set(s[1] for s in sl))))
        check("{} every strip named".format(name), set(s[0] for s in sl) == set(range(1, n + 1)),
              "strips named: {}".format(sorted(set(s[0] for s in sl))))
    if rows_check:
        want = set((y0 + 1, y1) for y0, y1 in planned)
        got = set((s[2], s[3]) for s in sl)
        check("{} rows match the plan".format(name), want <= got,
              "missing: {}".format(sorted(want - got)) if not want <= got else "{} ranges".format(len(want)))
    span = cap.strip_span()
    if span > 2.5:
        check("{} time-left estimate shown".format(name), any("remaining" in t for t in cap.labels()),
              next((t for t in cap.labels() if "remaining" in t), "no label with 'remaining'"))
    else:
        print("        ({} strips took {:.1f} s of {:.1f} s: too fast for an estimate)".format(name, span, wall))
    print("        last strip label: {}".format(
        next((t for t in reversed(cap.labels()) if STRIP_RE.search(t)), "(none)")))


def run_single(name, fn):
    """A raster that fits in one strip: the parts of the strip must still
    move the bar and the label must say how far along the strip is."""
    cap = Capture()
    cap.install()
    err = None
    try:
        _mh.begin_phases([("Test phase...", 1)])
        fn(Msgs())
        _mh.end_phases()
    except Exception:
        err = traceback.format_exc()
    finally:
        cap.restore()
    if err:
        check("{} (one strip) ran".format(name), False, err.strip().splitlines()[-1])
        print(err)
        return
    labels = cap.labels()
    parts = [t for t in labels if "% done" in t or t.endswith(": done")]
    check("{} (one strip) reports parts of the strip".format(name), len(parts) >= 2,
          "; ".join(parts[:4]))
    check("{} (one strip) bar never moves backwards".format(name), cap.monotonic())
    distinct = len(set(v for run in cap.position_runs() for v in run))
    check("{} (one strip) bar moves in several steps".format(name), distinct >= 4, "{} distinct positions".format(distinct))


def main():
    t0 = time.time()
    unit_checks()

    work = tempfile.mkdtemp(prefix="stripprog_")
    arcpy.env.overwriteOutput = True
    arcpy.env.scratchWorkspace = work
    outdir = os.path.join(work, "out")
    os.makedirs(outdir)
    H, W = 200, 160
    sr = arcpy.SpatialReference(26912)
    dem = make_dem(os.path.join(work, "dem.tif"), H, W, sr, 400000.0, 3900000.0, 30.0, 71)
    aspect = make_aspect(os.path.join(work, "aspect.tif"), dem, 72)
    cat = make_categorical(os.path.join(work, "cat.tif"), H, W, 400000.0, 3900000.0, 30.0, sr, 73)
    r = arcpy.Raster(dem)
    z_lo, z_hi = float(r.minimum), float(r.maximum)
    del r
    ci = max((z_hi - z_lo) / 20.0, 0.01)

    def o(name):
        return os.path.join(outdir, name + ".tif")

    sys10 = load_seed("weiss_10_class_landform.json")
    lf_sources = {"tpi_small": {"kind": "tpi", "dem": dem, "shape": "Circle", "outer": 2.0,
                                "inner": 0.0, "units": "Cells", "tpi_type": "dev"},
                  "tpi_large": {"kind": "tpi", "dem": dem, "shape": "Circle", "outer": 5.0,
                                "inner": 0.0, "units": "Cells", "tpi_type": "dev"},
                  "slope": {"kind": "slope", "dem": dem, "elev_units": "Meters",
                            "slope_units": "Degrees"}}
    keys = [k for k, _l in asp.ASPECT_TRANSFORMS]

    cases = [
        ("TPI", lambda m: tpi.run_tpi(dem, out_raw=o("tpi_raw"), out_dev=o("tpi_dev"), out_pctl=o("tpi_pctl"),
                                      shape="Circle", outer=3.0, inner=0.0, units="Cells",
                                      elev_units="Meters", messages=m), True, True),
        ("Landform", lambda m: tpi.run_classification("landform", dem, o("lf"), sys10, lf_sources,
                                                      elev_units="Meters", messages=m), True, True),
        ("VRM", lambda m: tt.run_vrm(dem, o("vrm"), elev_units="Meters", radius=1.5, radius_units="Cells",
                                     messages=m), True, True),
        ("ACR", lambda m: tt.run_acr(dem, o("acr"), elev_units="Meters", messages=m), True, True),
        ("TRI", lambda m: tt.run_tri(dem, o("tri"), elev_units="Meters", method=tt.TRI_METHODS[0],
                                     radius=1.5, radius_units="Cells", messages=m), True, True),
        ("Fractal", lambda m: tt.run_fractal_dimension(dem, o("fd"), elev_units="Meters",
                                                       method=tt.FD_METHODS[0], window_size=9,
                                                       messages=m), True, True),
        ("SurfaceArea", lambda m: tt.run_surface_area(dem, out_ratio=o("sar"), elev_units="Meters",
                                                      messages=m), False, False),
        ("EnhancedShadows", lambda m: hs.run_enhanced_hillshade(dem, o("hs_sh"), azimuth=315.0, altitude=25.0,
                                                                model_shadows=True, shadow_dim=20.0,
                                                                messages=m), True, True),
        ("Blurred", lambda m: hs.run_blurred_hillshade(dem, o("blur"), elev_units="Meters",
                                                       blur_target=hs.BLUR_TARGETS[0], kernel="Average",
                                                       blur_radius=3.0, radius_units="Cells",
                                                       messages=m), True, True),
        ("AspectFocal", lambda m: asp.run_aspect_focal(aspect, o("asp_mean"), statistic="MEAN",
                                                       neighborhood="Circle", radius=3.0,
                                                       messages=m), True, True),
        ("AspectTransform", lambda m: asp.run_aspect_transform(aspect, outdir, keys, base_name="asp_tr",
                                                               messages=m), True, True),
        ("LSRI", lambda m: lsri.run_lsri_on_dem(dem, o("lsri"), ci, radius=3.0, radius_units="Cells",
                                                out_contours=os.path.join(outdir, "lsri_contours.shp"),
                                                messages=m), True, True),
        ("Normalize", lambda m: hsm.run_normalize(dem, z_lo, z_hi, o("norm"), clamp=True, messages=m), True, True),
        ("Invert", lambda m: inv.run_invert(dem, out_raster=o("inv"), add_constant=0.1, messages=m), True, True),
        ("Diversity", lambda m: div.run_diversity(cat, o("div"), index="SHANNON", neighborhood="Circle",
                                                  radius=3.0, messages=m), True, True),
        ("Density", lambda m: den.run_density(cat, o("den"), neighborhood="Circle", radius=3.0,
                                              messages=m), True, True),
    ]

    STRIP = 50
    planned = rs.strip_ranges(H, STRIP)
    print("\n--- {} x {} rasters forced into strips of {} rows ({} strips) ---".format(
        H, W, STRIP, len(planned)), flush=True)
    rs.STRIP_ROWS_OVERRIDE = STRIP
    tpi.STRIP_ROWS_OVERRIDE = STRIP
    try:
        for name, fn, rows_check, strips_check in cases:
            print("\n  [{}]".format(name), flush=True)
            run_case(name, fn, planned, rows_check=rows_check, strips_check=strips_check)
    finally:
        rs.STRIP_ROWS_OVERRIDE = None
        tpi.STRIP_ROWS_OVERRIDE = None

    print("\n--- the same rasters as one strip ---", flush=True)
    run_single("TPI", lambda m: tpi.run_tpi(dem, out_raw=o("tpi1_raw"), out_dev=o("tpi1_dev"),
                                            out_pctl=o("tpi1_pctl"), shape="Circle", outer=3.0, inner=0.0,
                                            units="Cells", elev_units="Meters", messages=m))
    run_single("Landform", lambda m: tpi.run_classification("landform", dem, o("lf1"), sys10, lf_sources,
                                                            elev_units="Meters", messages=m))
    run_single("VRM", lambda m: tt.run_vrm(dem, o("vrm1"), elev_units="Meters", radius=1.5,
                                           radius_units="Cells", messages=m))
    run_single("TRI", lambda m: tt.run_tri(dem, o("tri1"), elev_units="Meters", method=tt.TRI_METHODS[0],
                                           radius=2.0, radius_units="Cells", messages=m))

    print("\n{} passed, {} failed; {:.0f} s".format(passed, failed, time.time() - t0))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
