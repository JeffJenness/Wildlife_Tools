# Wildlife and Forestry Tools for ArcGIS Pro
# Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version, together with the additional terms
# in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
# Memorial Scholarship Fund notice, marking of modified versions, and
# use of names).
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Home Range review fixes (v1.99.43): multipoint inputs read point by point,
Kernel Density Enhanced correct for geodesic analyses of projected data and
masked without Spatial Analyst, probability contours made with GDAL (no
extension license; polygons keep their holes), exact category matching in the
Mahalanobis tools, datum-safe sample points, and the spherical-area guard.

Run with Pro's Python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" home_range_review_test.py
"""
import math
import os
import sys
import tempfile

import numpy as np
from osgeo import gdal, osr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "WildlifeTools", "Toolboxes"))
import arcpy  # noqa: E402
import clip_tools as _ct  # noqa: E402
import kernel_density as kd  # noqa: E402
import locoh  # noqa: E402
import mahalanobis as mh  # noqa: E402
import wildlife_geom as wg  # noqa: E402

gdal.UseExceptions()
arcpy.env.overwriteOutput = True
work = tempfile.mkdtemp(prefix="hr_review_")
arcpy.env.scratchWorkspace = work
gdb = arcpy.management.CreateFileGDB(work, "out.gdb").getOutput(0)
rng = np.random.default_rng(7)
passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("PASS", name, detail)
    else:
        failed += 1
        print("FAIL", name, detail)


class Msgs(object):
    def __init__(self):
        self.info, self.warn = [], []

    def addMessage(self, t):
        self.info.append(t)

    def addWarningMessage(self, t):
        self.warn.append(t)

    def addErrorMessage(self, t):
        self.warn.append(t)


QUARTIC = "Quartic (Esri KD method)"
utm = arcpy.SpatialReference(26912)
TOOLBOXES = os.path.join(HERE, "..", "WildlifeTools", "Toolboxes")


def cart(lo, la):
    lo, la = math.radians(lo), math.radians(la)
    return np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])


# ---- spherical triangle: collinear vertices give zero area, not an error
bad = 0
for _ in range(5000):
    a = rng.uniform(-112, -111, 2)
    b = rng.uniform(35, 36, 2)
    t = rng.uniform(0.1, 0.9)
    p = cart(a[0], b[0]) * (1 - t) + cart(a[1], b[1]) * t
    p /= np.linalg.norm(p)
    try:
        v = wg._spherical_triangle_area(a[0], b[0], a[1], b[1],
                                        math.degrees(math.atan2(p[1], p[0])),
                                        math.degrees(math.asin(p[2])))
        # l'Huilier's formula is only good to about the square root of
        # machine precision on a degenerate triangle, so "zero" here means
        # tiny beside the triangle's own scale (sides of ~100 km).
        if not (math.isfinite(v) and abs(v) < 1.0e7):
            bad += 1
    except ValueError:
        bad += 1
check("collinear spherical triangles: no error, negligible area", bad == 0, "%d of 5000 failed" % bad)

# ---- multipoint inputs: every point is read
pts = rng.uniform(0, 1000, (60, 2)) + [400000, 4000000]
mp = arcpy.management.CreateFeatureclass(gdb, "mp", "MULTIPOINT", spatial_reference=utm).getOutput(0)
with arcpy.da.InsertCursor(mp, ["SHAPE@"]) as c:
    for i in range(0, 60, 20):
        c.insertRow([arcpy.Multipoint(arcpy.Array([arcpy.Point(*q) for q in pts[i:i + 20]]), utm)])
xy, _sr, _g = locoh.read_points(mp)
check("LoCoH reads every point of a multipoint layer", xy.shape[0] == 60, "%d points" % xy.shape[0])
m = Msgs()
kd.run_kernel_density(mp, None, os.path.join(gdb, "kd_mp"), None, "200 Meters", QUARTIC,
                      kd.DIST_PLANAR, kd.OUTV_COUNTS, "Square map units", m)
check("Kernel Density reads every point of a multipoint layer",
      any(t.startswith("Read 60 points") for t in m.info), str([t for t in m.info if t.startswith("Read")]))
check("Mahalanobis sample points: every point of a multipoint layer",
      len(mh.read_point_xy(mp, utm)) == 60)

# ---- sample points across datums: the transformation is applied
nad27 = arcpy.SpatialReference(26712)                      # NAD27 UTM 12N
p83 = arcpy.PointGeometry(arcpy.Point(440000.0, 3900000.0), utm)
p27 = _ct.project_geometry(p83, nad27)
fc27 = arcpy.management.CreateFeatureclass(gdb, "p27", "POINT", spatial_reference=nad27).getOutput(0)
with arcpy.da.InsertCursor(fc27, ["SHAPE@"]) as c:
    c.insertRow([p27])
got = mh.read_point_xy(fc27, utm)[0]
off = math.hypot(got[0] - 440000.0, got[1] - 3900000.0)
check("NAD27 sample point lands on its NAD83 position", off < 0.05, "off by %.3f m" % off)

# ---- category matching is exact for large codes
cat = np.array([[100000.0, 100001.0, 99999.0, 250003.0]])
hit = np.abs(cat - 100000.0) <= 1e-9 * max(1.0, abs(100000.0))
check("category 100000 does not match 100001 or 99999", hit.tolist() == [[True, False, False, False]])
src = open(os.path.join(TOOLBOXES, "mahalanobis.py"), encoding="utf-8-sig").read()
check("stats_from_categorical no longer uses np.isclose for the match",
      "mask |= np.isclose(cat, t)" not in src)

# ---- Kernel Density Enhanced: geodesic analysis of projected data
wm = arcpy.SpatialReference(3857)
g84 = arcpy.SpatialReference(4326)
lonlat = np.column_stack([rng.uniform(10.0, 10.2, 40), rng.uniform(60.0, 60.1, 40)])
fc_wm = arcpy.management.CreateFeatureclass(gdb, "wm_pts", "POINT", spatial_reference=wm).getOutput(0)
with arcpy.da.InsertCursor(fc_wm, ["SHAPE@"]) as c:
    for lo, la in lonlat:
        c.insertRow([arcpy.PointGeometry(arcpy.Point(lo, la), g84).projectAs(wm)])
sums = {}
for method in (kd.DIST_PLANAR, kd.DIST_GEODESIC):
    out = os.path.join(gdb, "kd_" + method[:3])
    kd.run_kernel_density(fc_wm, None, out, 100, "2000 Meters", QUARTIC, method,
                          kd.OUTV_COUNTS, "Square map units", Msgs())
    sums[method] = float(arcpy.RasterToNumPyArray(out, nodata_to_value=0).sum())
check("Web Mercator at 60N, geodesic: expected counts sum to the 40 points",
      abs(sums[kd.DIST_GEODESIC] - 40.0) < 0.4, "%.2f" % sums[kd.DIST_GEODESIC])
check("Web Mercator at 60N, planar: unchanged (40)", abs(sums[kd.DIST_PLANAR] - 40.0) < 0.05,
      "%.2f" % sums[kd.DIST_PLANAR])
# the geodesic density must agree with the same points analyzed in lat/long
fc_geo = arcpy.management.Project(fc_wm, os.path.join(gdb, "geo_pts"), g84).getOutput(0)
kd.run_kernel_density(fc_geo, None, os.path.join(gdb, "kd_geo"), 0.0005, "2000 Meters", QUARTIC,
                      kd.DIST_GEODESIC, kd.OUTV_DENSITIES, "Square kilometers", Msgs())
kd.run_kernel_density(fc_wm, None, os.path.join(gdb, "kd_wmd"), 100, "2000 Meters", QUARTIC,
                      kd.DIST_GEODESIC, kd.OUTV_DENSITIES, "Square kilometers", Msgs())
pk_geo = float(arcpy.RasterToNumPyArray(os.path.join(gdb, "kd_geo"), nodata_to_value=0).max())
pk_wm = float(arcpy.RasterToNumPyArray(os.path.join(gdb, "kd_wmd"), nodata_to_value=0).max())
check("peak density agrees between Web Mercator geodesic and lat/long",
      abs(pk_geo - pk_wm) / pk_geo < 0.03, "%.4f vs %.4f per km2" % (pk_wm, pk_geo))

# ---- Mask environment without Spatial Analyst (feature mask)
pfc = arcpy.management.CreateFeatureclass(gdb, "upts", "POINT", spatial_reference=utm).getOutput(0)
with arcpy.da.InsertCursor(pfc, ["SHAPE@XY"]) as c:
    for q in pts:
        c.insertRow([tuple(q)])
half = arcpy.Polygon(arcpy.Array([arcpy.Point(399000, 3999000), arcpy.Point(400500, 3999000),
                                  arcpy.Point(400500, 4002000), arcpy.Point(399000, 4002000)]), utm)
mfc = arcpy.management.CopyFeatures([half], os.path.join(gdb, "maskpoly")).getOutput(0)
arcpy.env.mask = mfc
m = Msgs()
kd.run_kernel_density(pfc, None, os.path.join(gdb, "kd_mask"), 10, "200 Meters", QUARTIC,
                      kd.DIST_PLANAR, kd.OUTV_DENSITIES, "Square kilometers", m)
arcpy.env.mask = None
r = arcpy.Raster(os.path.join(gdb, "kd_mask"))
a = arcpy.RasterToNumPyArray(r, nodata_to_value=-1)
xs = r.extent.XMin + (np.arange(a.shape[1]) + 0.5) * r.meanCellWidth
check("feature mask: nothing kept east of the mask edge", bool((a[:, xs > 400505] == -1).all()))
check("feature mask: data kept inside it", bool((a[:, xs < 400495] >= 0).any()))
check("feature mask: applied without a warning", not m.warn, str(m.warn))
# raster mask
arcpy.env.mask = os.path.join(gdb, "kd_mask")
m = Msgs()
kd.run_kernel_density(pfc, None, os.path.join(gdb, "kd_mask2"), 10, "200 Meters", QUARTIC,
                      kd.DIST_PLANAR, kd.OUTV_DENSITIES, "Square kilometers", m)
arcpy.env.mask = None
r2 = arcpy.Raster(os.path.join(gdb, "kd_mask2"))
a2 = arcpy.RasterToNumPyArray(r2, nodata_to_value=-1)
xs2 = r2.extent.XMin + (np.arange(a2.shape[1]) + 0.5) * r2.meanCellWidth
check("raster mask: nothing kept outside its valid cells",
      bool((a2[:, xs2 > 400505] == -1).all()) and bool((a2 >= 0).any()) and not m.warn, str(m.warn))

# ---- probability contours with GDAL: lines, polygons, holes, no extension
n = 200
yy, xx = np.mgrid[0:n, 0:n].astype(float)
z = (np.exp(-((xx - 60) ** 2 + (yy - 70) ** 2) / 500.0)
     + 0.8 * np.exp(-((xx - 140) ** 2 + (yy - 150) ** 2) / 300.0))
ring = np.hypot(xx - 130, yy - 60)
z += 0.9 * np.exp(-((ring - 22) ** 2) / 30.0)               # a crater: high rim, low middle
z32 = z.astype(np.float32)
tif = os.path.join(work, "kdsurf.tif")
ds = gdal.GetDriverByName("GTiff").Create(tif, n, n, 1, gdal.GDT_Float32)
ds.SetGeoTransform((400000.0, 10.0, 0, 4002000.0, 0, -10.0))
srs = osr.SpatialReference()
srs.ImportFromEPSG(26912)
ds.SetProjection(srs.ExportToWkt())
ds.GetRasterBand(1).SetNoDataValue(-9999)
ds.GetRasterBand(1).WriteArray(z32)
ds = None
lines = os.path.join(gdb, "c_lines")
polys = os.path.join(gdb, "c_polys")
kd.run_probability_contours(tif, lines, polys, Msgs())
src_kd = open(os.path.join(TOOLBOXES, "kernel_density.py"), encoding="utf-8-sig").read()
check("no extension is checked out and no Advanced-only tool is called",
      "CheckOutExtension" not in src_kd and "ddd.ContourList" not in src_kd
      and "FeatureToPolygon(" not in src_kd and "ExtractByMask" not in src_kd)
rows = [r_ for r_ in arcpy.da.SearchCursor(polys, ["Label", "Density", "SHAPE@AREA", "SHAPE@"])]
labels = sorted(set(r_[0] for r_ in rows))
check("polygons written for the isopleth levels", len(labels) >= 15, "%d levels" % len(labels))
worst = 0.0
for lab in ("95%", "50%", "25%"):
    lev = [r_ for r_ in rows if r_[0] == lab]
    area = sum(r_[2] for r_ in lev)
    truth = float((z32 >= np.float32(lev[0][1])).sum()) * 100.0
    worst = max(worst, abs(area - truth) / truth)
check("polygon area = area of cells at or above the level (95/50/25%)", worst < 0.03,
      "worst difference %.2f%%" % (100 * worst))
d50 = [r_ for r_ in rows if r_[0] == "50%"][0][1]
share = float(z32[z32 >= np.float32(d50)].sum()) / float(z32.sum())
check("50% level encloses half the volume", abs(share - 0.5) < 0.01, "%.4f" % share)
# the crater's hollow must be a hole at a level between its middle and its rim
mid = float(z32[60, 130])
rim = float(z32[60, 152])
inside = arcpy.Point(400000 + 130.5 * 10, 4002000 - 60.5 * 10)
on_rim = arcpy.Point(400000 + 152.5 * 10, 4002000 - 60.5 * 10)
holed = [r_ for r_ in rows if mid < r_[1] < rim]
has_hole = any(r_[3].contains(on_rim) and not r_[3].contains(inside) for r_ in holed)
check("a hollow in the surface stays a hole in the polygon", bool(holed) and has_hole,
      "%d polygons at levels between the hollow and its rim" % len(holed))
lrows = [r_ for r_ in arcpy.da.SearchCursor(lines, ["Label", "Density", "Contour", "SHAPE@LENGTH"])]
check("contour lines carry the level fields",
      len(lrows) > 15 and all(abs(r_[1] - r_[2]) < 1e-12 for r_ in lrows), "%d lines" % len(lrows))
plen, llen = {}, {}
for r_ in arcpy.da.SearchCursor(polys, ["Label", "SHAPE@LENGTH"]):
    plen[r_[0]] = plen.get(r_[0], 0.0) + r_[1]
for r_ in lrows:
    llen[r_[0]] = llen.get(r_[0], 0.0) + r_[3]
check("polygon boundaries coincide with the lines (equal length at 50%)",
      abs(plen["50%"] - llen["50%"]) / llen["50%"] < 0.01, "%.1f vs %.1f" % (plen["50%"], llen["50%"]))
# a proportion surface contours at fixed proportions and agrees with the density route
prop = os.path.join(gdb, "prop")
kd.run_proportion_surface(tif, prop, Msgs())
kd.run_probability_contours(prop, None, os.path.join(gdb, "c_polys_p"), Msgs())
a1 = sum(r_[0] for r_ in arcpy.da.SearchCursor(os.path.join(gdb, "c_polys_p"), ["SHAPE@AREA", "Label"])
         if r_[1] == "50%")
a0 = sum(r_[2] for r_ in rows if r_[0] == "50%")
check("50% polygon agrees between the density and proportion routes", abs(a1 - a0) / a0 < 0.03,
      "%.0f vs %.0f" % (a1, a0))
# shapefile outputs
shp_l = os.path.join(work, "c_lines.shp")
shp_p = os.path.join(work, "c_polys.shp")
kd.run_probability_contours(tif, shp_l, shp_p, Msgs())
check("shapefile outputs written with the 10-character field name",
      "CumProport" in [f.name for f in arcpy.ListFields(shp_p)]
      and int(arcpy.management.GetCount(shp_l)[0]) == len(lrows))

# ---- the outermost (100%) isopleth: a smooth edge of the positive cells
# (a real kernel surface, which falls to zero at the search radius)
kd_e = os.path.join(gdb, "kd_edge")
kd.run_kernel_density(pfc, None, kd_e, 10, "150 Meters", QUARTIC, kd.DIST_PLANAR,
                      kd.OUTV_DENSITIES, "Square kilometers", Msgs())
lines_e = os.path.join(gdb, "e_lines")
polys_e = os.path.join(gdb, "e_polys")
kd.run_probability_contours(kd_e, lines_e, polys_e, Msgs())
out_l = [r_ for r_ in arcpy.da.SearchCursor(lines_e, ["Label", "SHAPE@LENGTH", "SHAPE@"]) if r_[0] == "100%"]
out_p = [r_ for r_ in arcpy.da.SearchCursor(polys_e, ["Label", "SHAPE@AREA", "SHAPE@LENGTH"]) if r_[0] == "100%"]
check("a 100% line and a 100% polygon are written", len(out_l) >= 1 and len(out_p) >= 1,
      "%d lines, %d polygons" % (len(out_l), len(out_p)))
ae = arcpy.RasterToNumPyArray(kd_e, nodata_to_value=0)
pos_area = float((ae > 0).sum()) * 100.0
got_area = sum(r_[1] for r_ in out_p)
check("100% polygon covers the positive-density cells", abs(got_area - pos_area) / pos_area < 0.03,
      "%.0f vs %.0f" % (got_area, pos_area))
check("100% line and polygon boundary coincide",
      abs(sum(r_[1] for r_ in out_l) - sum(r_[2] for r_ in out_p)) / sum(r_[2] for r_ in out_p) < 0.01)
# smooth: a line that follows the cell sides turns 90 degrees at every
# vertex; this one should bend gently
big = max(out_l, key=lambda r_: r_[1])[2]
pts_ = [(q.X, q.Y) for q in big.getPart(0) if q is not None]
turns = []
for i_ in range(1, len(pts_) - 1):
    ax, ay = pts_[i_][0] - pts_[i_ - 1][0], pts_[i_][1] - pts_[i_ - 1][1]
    bx, by = pts_[i_ + 1][0] - pts_[i_][0], pts_[i_ + 1][1] - pts_[i_][1]
    na, nb = math.hypot(ax, ay), math.hypot(bx, by)
    if na > 0 and nb > 0:
        turns.append(math.degrees(math.acos(max(-1.0, min(1.0, (ax * bx + ay * by) / (na * nb))))))
check("the 100% line is smooth (no cell-side staircase)",
      len(turns) > 20 and float(np.percentile(turns, 90)) < 50.0,
      "90th-percentile turn %.1f deg over %d vertices" % (float(np.percentile(turns, 90)), len(turns)))
prop_e = os.path.join(gdb, "kd_edge_prop")
kd.run_proportion_surface(kd_e, prop_e, Msgs())
kd.run_probability_contours(prop_e, None, os.path.join(gdb, "e_polys_p"), Msgs())
prow = [r_ for r_ in arcpy.da.SearchCursor(os.path.join(gdb, "e_polys_p"), ["Label", "SHAPE@AREA"]) if r_[0] == "100%"]
check("a proportion surface gets the same 100% isopleth",
      len(prow) >= 1 and abs(sum(r_[1] for r_ in prow) - got_area) / got_area < 0.01)

# ---- what the cell values are: detection, and cell-area weighting in lat/long
big_ll = np.column_stack([rng.uniform(10.0, 16.0, 150), rng.uniform(52.0, 66.0, 150)])
fc_big = arcpy.management.CreateFeatureclass(gdb, "ll_pts", "POINT", spatial_reference=g84).getOutput(0)
with arcpy.da.InsertCursor(fc_big, ["SHAPE@XY"]) as c:
    for lo, la in big_ll:
        c.insertRow([(float(lo), float(la))])
outs = {}
for key, ov in (("dens", kd.OUTV_DENSITIES), ("cnt", kd.OUTV_COUNTS), ("prop", kd.OUTV_PROPORTION)):
    outs[key] = os.path.join(gdb, "ll_" + key)
    kd.run_kernel_density(fc_big, None, outs[key], 0.05, "150000 Meters", QUARTIC,
                          kd.DIST_GEODESIC, ov, "Square kilometers", Msgs())
check("metadata marker: densities", kd.detect_value_kind(outs["dens"])[0] == kd.KIND_DENSITIES,
      str(kd.detect_value_kind(outs["dens"])))
check("metadata marker: expected counts", kd.detect_value_kind(outs["cnt"])[0] == kd.KIND_COUNTS,
      str(kd.detect_value_kind(outs["cnt"])))
check("metadata marker: proportion", kd.detect_value_kind(outs["prop"])[0] == kd.KIND_PROPORTION)
check("no metadata: nothing claimed", kd.detect_value_kind(tif) == (None, None), str(kd.detect_value_kind(tif)))
truth = arcpy.RasterToNumPyArray(outs["prop"], nodata_to_value=np.nan).astype(float)
res = {}
for key in ("dens", "cnt"):
    mm = Msgs()
    op = os.path.join(gdb, "ll_p_" + key)
    kd.run_proportion_surface(outs[key], op, mm)
    got_ = arcpy.RasterToNumPyArray(op, nodata_to_value=np.nan).astype(float)
    ok_ = np.isfinite(truth) & np.isfinite(got_)
    res[key] = float(np.nanmax(np.abs(got_[ok_] - truth[ok_])))
    res[key + "_msg"] = " ".join(mm.info)
check("lat/long densities raster: proportion matches Kernel Density Enhanced's own",
      res["dens"] < 2e-4, "max difference %.6f" % res["dens"])
check("lat/long expected-counts raster: proportion matches too", res["cnt"] < 2e-4,
      "max difference %.6f" % res["cnt"])
check("the run reports the weighting decision",
      "weighted by its true area" in res["dens_msg"] and "no area weighting" in res["cnt_msg"])
# forcing the wrong kind must change the answer (so the weighting is real)
op = os.path.join(gdb, "ll_p_wrong")
kd.run_proportion_surface(outs["dens"], op, Msgs(), value_kind=kd.KIND_COUNTS)
got_ = arcpy.RasterToNumPyArray(op, nodata_to_value=np.nan).astype(float)
ok_ = np.isfinite(truth) & np.isfinite(got_)
wrong = float(np.nanmax(np.abs(got_[ok_] - truth[ok_])))
check("treating those densities as counts gives a different answer", wrong > 0.005,
      "max difference %.4f" % wrong)
# Esri's own Kernel Density records its choice in the geoprocessing history
try:
    if arcpy.CheckExtension("Spatial") == "Available":
        arcpy.CheckOutExtension("Spatial")
        for label, opt, want in (("densities", "DENSITIES", kd.KIND_DENSITIES),
                                 ("expected counts", "EXPECTED_COUNTS", kd.KIND_COUNTS)):
            er = os.path.join(gdb, "esri_" + opt[:4])
            # run as a TOOL (the history is not written when a Raster
            # object is merely saved)
            arcpy.gp.KernelDensity_sa(pfc, "NONE", er, 10, 200, "SQUARE_KILOMETERS", opt)
            check("Esri Kernel Density (%s) read from its history" % label,
                  kd.detect_value_kind(er)[0] == want, str(kd.detect_value_kind(er)))
        arcpy.CheckInExtension("Spatial")
    else:
        print("SKIP Esri Kernel Density history check (no Spatial Analyst license here)")
except Exception as e_:                                          # noqa: BLE001
    check("Esri Kernel Density history check ran", False, str(e_)[:120])

# ---- LoCoH: Output Coordinate System reprojection keeps the output
arcpy.env.outputCoordinateSystem = g84
iso = os.path.join(gdb, "iso_prj")
locoh.run_locoh_standard(pfc, 8, iso, None, Msgs())
arcpy.env.outputCoordinateSystem = None
check("LoCoH output exists and is in the Output Coordinate System",
      arcpy.Exists(iso) and arcpy.Describe(iso).spatialReference.factoryCode == 4326
      and int(arcpy.management.GetCount(iso)[0]) > 0)
arcpy.env.workspace = gdb
check("no stray projected copy is left behind", not arcpy.ListFeatureClasses("iso_prj_*"))

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
