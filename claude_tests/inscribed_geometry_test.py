# -*- coding: utf-8 -*-
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

"""Tests for the Maximum Inscribed Geometry tool
(inscribed_geometry.py).

Run with ArcGIS Pro's python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" inscribed_geometry_test.py
"""
import math
import os
import shutil
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE),
                                "WildlifeTools", "Toolboxes"))

import inscribed_geometry as ig  # noqa: E402
import patch_analysis as _pa  # noqa: E402

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS  {}".format(name))
    else:
        FAIL += 1
        print("  FAIL  {}  {}".format(name, detail))


def ring(pts):
    return np.asarray(pts, dtype=np.float64)


def cw(pts):
    """Esri exterior orientation: clockwise (negative shoelace)."""
    a = ring(pts)
    if _pa._signed_area(a) > 0:
        a = a[::-1]
    return a


def ccw(pts):
    """Esri hole orientation: counter-clockwise."""
    a = ring(pts)
    if _pa._signed_area(a) < 0:
        a = a[::-1]
    return a


def contained(chain, rings_, tol=1e-6):
    """Every chain vertex inside/near the free space and no edge
    properly crossing the boundary."""
    edges = _pa.edges_of(rings_)
    bands = _pa.y_bands(edges)
    pts = np.asarray(chain)
    ok_pts = ig._points_in_free(pts, edges, bands, tol).all()
    segs = np.hstack([pts, np.roll(pts, -1, axis=0)])
    return bool(ok_pts and not ig._segs_cross(segs, edges).any())


def is_convex(chain, tol=1e-7):
    c = np.asarray(chain)
    prev = np.roll(c, 1, axis=0)
    nxt = np.roll(c, -1, axis=0)
    d1 = c - prev
    d2 = nxt - c
    cross = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
    scale = max(1.0, float(np.abs(cross).max()))
    return bool((cross >= -tol * scale).all()
                or (cross <= tol * scale).all())


# ---------------------------------------------------------------------------
print("== square 100 x 100 ==")
SQ = [cw([(0, 0), (100, 0), (100, 100), (0, 100)])]
c = ig.max_inscribed_circle(SQ, 2.0)
check("circle: r = 50 at the center",
      abs(c[2] - 50.0) < 0.2 and abs(c[0] - 50) < 0.5
      and abs(c[1] - 50) < 0.5, str(c))
r = ig.max_inscribed_rect(SQ, 4.0)
check("rectangle: fills the square (area 10000)",
      abs(r[2] * r[3] - 10000.0) < 50.0, str(r))
p = ig.max_inscribed_potato(SQ, 5.0)
check("potato: convex input returns the square itself",
      p is not None and abs(ig._poly_area(p) - 10000.0) < 1e-6,
      str(None if p is None else ig._poly_area(p)))
e = ig.max_inscribed_ellipse(SQ, 2.0, circle=c, rect=r, potato=p)
check("ellipse: the inscribed circle (a = b = 50)",
      abs(e[2] - 50.0) < 1.0 and abs(e[3] - 50.0) < 1.0, str(e))

# ---------------------------------------------------------------------------
print("== rectangle 200 x 100 ==")
RC = [cw([(0, 0), (200, 0), (200, 100), (0, 100)])]
c = ig.max_inscribed_circle(RC, 2.0)
check("circle: r = 50", abs(c[2] - 50.0) < 0.2, str(c))
r = ig.max_inscribed_rect(RC, 4.0)
check("rectangle: 200 x 100 recovered",
      abs(r[2] - 200.0) < 1.0 and abs(r[3] - 100.0) < 1.0
      and (abs(r[4] - 90.0) < 3 or abs(r[4] - 270.0) < 3), str(r))
e = ig.max_inscribed_ellipse(RC, 2.0, circle=c, rect=r,
                             potato=ig.max_inscribed_potato(RC, 5.0))
check("ellipse: semi-axes 100 and 50 (John ellipse of the box)",
      abs(e[2] - 100.0) < 2.0 and abs(e[3] - 50.0) < 1.5, str(e))
check("ellipse: major axis east-west",
      abs(((90.0 - math.degrees(e[4])) % 180.0) - 90.0) < 3.0,
      str(math.degrees(e[4])))

# ---------------------------------------------------------------------------
print("== rotated rectangle (30 degrees) ==")
th = math.radians(30.0)
ct, st = math.cos(th), math.sin(th)


def rot(x, y):
    return (x * ct - y * st, x * st + y * ct)


RR = [cw([rot(0, 0), rot(200, 0), rot(200, 100), rot(0, 100)])]
r = ig.max_inscribed_rect(RR, 4.0)
check("rotated rectangle recovered (area within 2%)",
      r is not None and abs(r[2] * r[3] - 20000.0) < 400.0, str(r))

# ---------------------------------------------------------------------------
print("== L-shape ==")
LS = [cw([(0, 0), (200, 0), (200, 90), (90, 90), (90, 200),
          (0, 200)])]
r = ig.max_inscribed_rect(LS, 3.0)
check("rectangle: the long arm (200 x 90)",
      abs(r[2] * r[3] - 18000.0) < 300.0, str(r))
p = ig.max_inscribed_potato(LS, 6.0)
check("potato: convex, contained, finds the full arm (18000)",
      p is not None and is_convex(p) and contained(p, LS)
      and ig._poly_area(p) >= 17800.0,
      str(None if p is None else ig._poly_area(p)))
c = ig.max_inscribed_circle(LS, 2.0)
# the optimum nestles in the corner block, touching both outer walls
# and the reflex corner (90, 90): r = 90 * sqrt(2) / (1 + sqrt(2))
r_exp = 90.0 * math.sqrt(2.0) / (1.0 + math.sqrt(2.0))
check("circle: corner pocket, r = 90*sqrt2/(1+sqrt2) = 52.72",
      abs(c[2] - r_exp) < 0.3, str(c))

# ---------------------------------------------------------------------------
print("== square with a center hole ==")
HS = [cw([(0, 0), (300, 0), (300, 300), (0, 300)]),
      ccw([(120, 120), (180, 120), (180, 180), (120, 180)])]
c = ig.max_inscribed_circle(HS, 2.0)
# the optimum sits in a corner pocket, touching two outer walls and
# the hole corner (120, 120): r = 120 * sqrt(2) / (1 + sqrt(2))
r_exp = 120.0 * math.sqrt(2.0) / (1.0 + math.sqrt(2.0))
check("circle avoids the hole (corner pocket, r = 70.29)",
      abs(c[2] - r_exp) < 0.8, str(c))
r = ig.max_inscribed_rect(HS, 4.0)
check("rectangle avoids the hole (300 x 120 side slab)",
      abs(r[2] - 300.0) < 2.0 and abs(r[3] - 120.0) < 2.0, str(r))
p = ig.max_inscribed_potato(HS, 8.0)
seeds = ig._hole_seeds(HS)
inside_hole = False
if p is not None and len(seeds):
    edges_p = np.hstack([p, np.roll(p, -1, axis=0)])
    inside_hole = bool(_pa.points_inside(
        seeds, edges_p).any())
check("potato: convex, contained, does NOT swallow the hole",
      p is not None and is_convex(p) and contained(p, HS)
      and not inside_hole and ig._poly_area(p) >= 34000.0,
      str(None if p is None else (ig._poly_area(p), inside_hole)))
e = ig.max_inscribed_ellipse(HS, 3.0, circle=c, rect=r, potato=p)
ell_ring = ig._ellipse_ring(*e)
check("ellipse: contained and hole-free",
      e is not None and contained(ell_ring, HS)
      and e[2] * e[3] * math.pi >= math.pi * 60 * 60 * 0.98,
      str(e))

# ---------------------------------------------------------------------------
print("== potato beats both rectangle and circle on a triangle ==")
TR = [cw([(0, 0), (300, 0), (150, 260)])]
p = ig.max_inscribed_potato(TR, 6.0)
check("potato: convex input -> the whole triangle",
      p is not None and abs(ig._poly_area(p) - 0.5 * 300 * 260)
      < 1e-6, str(None if p is None else ig._poly_area(p)))
r = ig.max_inscribed_rect(TR, 3.0)
# largest rectangle in a triangle has half the triangle's area
check("rectangle in triangle: half the area",
      abs(r[2] * r[3] - 0.25 * 300 * 260) < 500.0,
      str((r, r[2] * r[3])))

# ---------------------------------------------------------------------------
print("== nonconvex potato: dumbbell picks a lobe ==")
DB = [cw([(0, 0), (100, 0), (100, 40), (160, 40), (160, 0),
          (260, 0), (260, 100), (160, 100), (160, 60), (100, 60),
          (100, 100), (0, 100)])]
p = ig.max_inscribed_potato(DB, 5.0)
check("potato: one full lobe (area 10000)",
      p is not None and is_convex(p) and contained(p, DB)
      and ig._poly_area(p) >= 9600.0,
      str(None if p is None else ig._poly_area(p)))

# ---------------------------------------------------------------------------
print("== fixed-azimuth rectangle ==")
r = ig.max_inscribed_rect(RR, 4.0, fixed_azimuth=0.0)
r_free = ig.max_inscribed_rect(RR, 4.0)
check("fixed north-south rectangle is smaller than free-angle",
      r is not None and r_free is not None
      and r[2] * r[3] < r_free[2] * r_free[3] * 0.9,
      str((r, r_free)))

# ---------------------------------------------------------------------------
print("== driver: feature classes, all four shapes ==")
import arcpy  # noqa: E402

TMP = tempfile.mkdtemp(prefix="ig_test_")
arcpy.env.overwriteOutput = True
GDB = arcpy.management.CreateFileGDB(TMP, "t.gdb").getOutput(0)
SR = arcpy.SpatialReference(32612)
X0, Y0 = 500000.0, 3800000.0


class Msgs:
    def addMessage(self, t):
        pass

    def addWarningMessage(self, t):
        print("!", t)


fc = arcpy.management.CreateFeatureclass(
    GDB, "poly", "POLYGON", spatial_reference=SR).getOutput(0)
arcpy.management.AddField(fc, "Site", "TEXT", field_length=24)
outer = [(0, 0), (300, 0), (300, 300), (0, 300)]
hole = [(120, 120), (180, 120), (180, 180), (120, 180)]
with arcpy.da.InsertCursor(fc, ["SHAPE@", "Site"]) as ins:
    ins.insertRow([arcpy.Polygon(arcpy.Array(
        [arcpy.Array([arcpy.Point(X0 + x, Y0 + y)
                      for x, y in outer + [outer[0]]]),
         arcpy.Array([arcpy.Point(X0 + x, Y0 + y)
                      for x, y in hole + [hole[0]]])]), SR),
        "HoleSquare"])

out = os.path.join(GDB, "shapes")
import inscribed_geometry as ig2  # noqa: E402

got = ig2.run_inscribed_geometry(
    fc, ig2.GEOM_ALL, out, transfer_fields=["Site"],
    messages=Msgs())
rows = {}
with arcpy.da.SearchCursor(out, ["Shape_Kind", "Area_m2",
                                 "Radius_m", "Length_m", "Width_m",
                                 "MajAxis_m", "MinAxis_m",
                                 "NumVerts", "Site"]) as cur:
    for row in cur:
        rows[row[0]] = row
check("driver: four rows, one per shape",
      set(rows) == {ig2.GEOM_CIRCLE, ig2.GEOM_RECT, ig2.GEOM_MCP,
                    ig2.GEOM_ELLIPSE}, str(set(rows)))
r_exp = 120.0 * math.sqrt(2.0) / (1.0 + math.sqrt(2.0))
check("driver: circle radius ~70.29 m",
      abs(rows[ig2.GEOM_CIRCLE][2] - r_exp) < 1.0,
      str(rows.get(ig2.GEOM_CIRCLE)))
check("driver: rectangle ~300 x 120",
      abs(rows[ig2.GEOM_RECT][3] - 300.0) < 3.0
      and abs(rows[ig2.GEOM_RECT][4] - 120.0) < 3.0,
      str(rows.get(ig2.GEOM_RECT)))
check("driver: potato area >= 34000 sq m, vertex count set",
      rows[ig2.GEOM_MCP][1] >= 34000.0
      and rows[ig2.GEOM_MCP][7] >= 3, str(rows.get(ig2.GEOM_MCP)))
check("driver: ellipse axes recorded, attrs transferred",
      rows[ig2.GEOM_ELLIPSE][5] is not None
      and all(r[8] == "HoleSquare" for r in rows.values()),
      str(rows.get(ig2.GEOM_ELLIPSE)))

print("== driver: geographic circle in true meters ==")
GCS = arcpy.SpatialReference(4326)
gfc = arcpy.management.CreateFeatureclass(
    GDB, "gpoly", "POLYGON", spatial_reference=GCS).getOutput(0)
# ~2 km x 2 km square at 35 N
dlat = 1000.0 / 110967.0
dlon = 1000.0 / (111319.49 * math.cos(math.radians(35.0)))
with arcpy.da.InsertCursor(gfc, ["SHAPE@"]) as ins:
    ins.insertRow([arcpy.Polygon(arcpy.Array(
        [arcpy.Point(-111.0 + s * dlon, 35.0 + t * dlat)
         for s, t in [(-1, -1), (1, -1), (1, 1), (-1, 1),
                      (-1, -1)]]), GCS)])
gout = os.path.join(GDB, "gshapes")
ig2.run_inscribed_geometry(gfc, ig2.GEOM_CIRCLE, gout,
                           messages=Msgs())
with arcpy.da.SearchCursor(gout, ["Radius_m"]) as cur:
    gr = next(cur)[0]
check("geographic: circle radius ~1000 true meters",
      abs(gr - 1000.0) < 15.0, str(gr))

print("== driver: simplify tolerance accepted ==")
ig2.run_inscribed_geometry(
    fc, ig2.GEOM_CIRCLE, os.path.join(GDB, "simp"),
    simplify_tolerance=2.0, linear_units="Meters",
    messages=Msgs())
with arcpy.da.SearchCursor(os.path.join(GDB, "simp"),
                           ["Radius_m"]) as cur:
    sr_ = next(cur)[0]
check("simplified run still finds the circle",
      abs(sr_ - r_exp) < 3.0, str(sr_))

try:
    arcpy.management.Delete(GDB)
    shutil.rmtree(TMP, ignore_errors=True)
except Exception:
    pass

print()
print("{} passed, {} failed".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
