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

"""Find Raster Extreme Values engine tests (2026-09-23).

Covers the original validations (single pit, mesa, bench, twin equal bottoms,
edge plateaus) and the nested-extremes bug found while writing the help page:
a subsidiary summit inside a larger massif, or a small pit inside a larger
bowl, must get its own point with prominence measured to its own saddle. If
the Grand Canyon test DEM is present, also checks that every strict local
maximum/minimum of the real DEM is reported. Run with ArcGIS Pro's python."""
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TBX = os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes")
sys.path.insert(0, TBX)

import raster_extremes as rx  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS  {}".format(name))
    else:
        FAIL += 1
        print("  FAIL  {}  {}".format(name, detail))


def lows(z, valid=None):
    valid = np.ones(z.shape, bool) if valid is None else valid
    return sorted(rx.find_extremes(z, valid), key=lambda r: (r["row"], r["col"]))


def peaks(z, valid=None):
    valid = np.ones(z.shape, bool) if valid is None else valid
    out = rx.find_extremes(np.where(valid, -z, np.inf), valid)
    for r in out:
        r["value"] = -r["value"]
    return sorted(out, key=lambda r: (r["row"], r["col"]))


def plane(h=7, w=7):
    # falls from 60 on the west edge to 30 on the east edge, 5 per column
    return np.tile(60.0 - 5.0 * np.arange(w), (h, 1))


def one(items, **kw):
    hits = [r for r in items if all(r[k] == v for k, v in kw.items())]
    return hits[0] if len(hits) == 1 else None


# ---------------------------------------------------------------- the help-page grids
g = plane()
g[1, 1] = 70.0
g[4, 4] = 30.0
g[3, 2:5] = 45.0
L = lows(g)
P = peaks(g)
check("grid 1: pit (4,4) depth 5, interior",
      one(L, row=4, col=4, value=30.0, prominence=5.0, edge=False) is not None, L)
check("grid 1: east-edge flat -> one point at (3,6), depth 0, edge",
      one(L, row=3, col=6, value=30.0, prominence=0.0, edge=True) is not None, L)
check("grid 1: exactly two low points (the bench emits nothing)", len(L) == 2, L)
check("grid 1: peak 70 with prominence 10 (to the west edge at 60)",
      P == [{"row": 1, "col": 1, "value": 70.0, "prominence": 10.0, "edge": False}], P)

g = plane()
g[1, 1] = 70.0
g[1, 2] = 58.0
g[1, 3] = 66.0
P = peaks(g)
check("grid 2: two summits, 70 -> 10 and 66 -> 8 (saddle 58)",
      one(P, value=70.0, prominence=10.0) is not None
      and one(P, value=66.0, prominence=8.0) is not None and len(P) == 2, P)

# ---------------------------------------------------------------- nested extremes (the bug)
B = np.full((7, 7), 10.0)
B[2:5, 1:6] = 30.0
B[3, 1] = 70.0
B[3, 2] = 58.0
B[3, 3] = 66.0
B[3, 5] = 50.0
P = peaks(B)
check("nested peaks: three points (70, 66, 50)", sorted(r["value"] for r in P) == [50.0, 66.0, 70.0], P)
check("nested peaks: 70 -> 60 (to the 10 edge), 66 -> 8, 50 -> 20",
      one(P, value=70.0, prominence=60.0) is not None
      and one(P, value=66.0, prominence=8.0) is not None
      and one(P, value=50.0, prominence=20.0) is not None, P)

C = np.full((7, 7), 50.0)
C[1:6, 1:6] = 40.0
C[3, 2] = 10.0
C[3, 3] = 25.0
C[3, 4] = 20.0
L = lows(C)
check("nested pits: two points (10, 20)", sorted(r["value"] for r in L) == [10.0, 20.0], L)
check("nested pits: 10 -> depth 40 (spill at the 50 edge), 20 -> 5 (saddle 25)",
      one(L, value=10.0, prominence=40.0) is not None
      and one(L, value=20.0, prominence=5.0) is not None, L)

# ---------------------------------------------------------------- the original validations
D = np.full((9, 9), 100.0)
D[4, 4] = 90.1
L = lows(D)
check("single pit: depth 9.9 exact", L == [{"row": 4, "col": 4, "value": 90.1, "prominence": 100.0 - 90.1, "edge": False}], L)

M = np.full((9, 9), 20.0)
M[2:7, 2:7] = 47.7 + 20.0       # a 5 x 5 mesa
M[1, 1] = 25.0                  # highest rim cell
P = peaks(M)
check("mesa: ONE point at the plateau centre", len(P) == 1 and (P[0]["row"], P[0]["col"]) == (4, 4), P)
check("mesa: prominence 47.7 = summit - the 20 surround every path to the edge must cross (the 25 rim cell is not on the edge)",
      len(P) == 1 and abs(P[0]["prominence"] - 47.7) < 1e-9, P)

Bn = plane()
Bn[3, 2:5] = 45.0
check("bench: emits nothing as a low point (only the east-edge flat)",
      [r for r in lows(Bn) if r["prominence"] > 0 or r["col"] != 6] == [], lows(Bn))

T = np.full((9, 9), 50.0)
T[2:7, 2:7] = 40.0
T[4, 3] = 10.0
T[4, 5] = 10.0
T[4, 4] = 15.0                  # saddle between two equal pits
L = lows(T)
check("twin equal bottoms: two points", sorted((r["row"], r["col"]) for r in L) == [(4, 3), (4, 5)], L)
check("twin equal bottoms: one measured to the saddle (5), the other carries the full depth (40)",
      sorted(r["prominence"] for r in L) == [5.0, 40.0], L)

E = np.tile(np.arange(9, dtype=float), (5, 1))     # rises to the east, flat columns
E[:, 0] = 0.0
E[:, 8] = 8.0
L = lows(E)
P = peaks(E)
check("edge plateaus: the west column is one flagged zero-depth low point",
      len(L) == 1 and L[0]["col"] == 0 and L[0]["prominence"] == 0.0 and L[0]["edge"], L)
check("edge plateaus: the east column is one flagged zero-prominence peak",
      len(P) == 1 and P[0]["col"] == 8 and P[0]["prominence"] == 0.0 and P[0]["edge"], P)

# ---------------------------------------------------------------- NoData
N = np.full((9, 9), 100.0)
N[4, 4] = 80.0
N[2, 2] = 60.0                  # a pit beside a NoData hole
valid = np.ones((9, 9), bool)
valid[1, 1] = False
L = lows(N, valid)
check("NoData: pit beside the hole is flagged edge with depth 0 (drains into the unknown)",
      one(L, row=2, col=2, prominence=0.0, edge=True) is not None, L)
check("NoData: interior pit keeps depth 20 and is not flagged",
      one(L, row=4, col=4, prominence=20.0, edge=False) is not None, L)

# ---------------------------------------------------------------- consistency on random terrain
rng = np.random.default_rng(7)
R = rng.normal(size=(60, 80)).cumsum(axis=0).cumsum(axis=1) + rng.normal(scale=0.5, size=(60, 80))
R = np.round(R, 1)
from scipy import ndimage  # noqa: E402
P = peaks(R)
RING = np.ones((3, 3), bool)
RING[1, 1] = False              # the eight neighbors, not the cell itself
nbmax = ndimage.maximum_filter(R, footprint=RING, mode="constant", cval=-np.inf)
mx = np.maximum(R, nbmax)
strict = np.argwhere(R > nbmax)  # strictly higher than all eight neighbors
pset = {(r["row"], r["col"]) for r in P}
check("random terrain: every strict local maximum is reported as a peak",
      all((int(a), int(b)) in pset for a, b in strict), (len(strict), len(pset)))
check("random terrain: no reported peak has a higher valid neighbor",
      all(R[r["row"], r["col"]] == mx[r["row"], r["col"]] for r in P))
check("random terrain: prominences are non-negative and the highest summit's equals summit - min(edge max path)",
      all(r["prominence"] >= 0 for r in P)
      and max(P, key=lambda r: r["value"])["prominence"] > 0)
pr = sorted(r["prominence"] for r in P)
check("random terrain: exactly one peak per strict maximum plus plateaus (no duplicates)",
      len(pset) == len(P))

# ---------------------------------------------------------------- the real DEM, if present
GDB = r"D:\ArcGIS_stuff\general_tools\Pro_Wildlife\Pro_Test_Project\Pro Test Tools.gdb"
if os.path.isdir(GDB):
    from osgeo import gdal
    ds = gdal.Open('OpenFileGDB:"{}":Extract_AZ_D1'.format(GDB))
    if ds is not None:
        b = ds.GetRasterBand(1)
        z = b.ReadAsArray().astype(np.float64)
        nd = b.GetNoDataValue()
        v = np.isfinite(z) & ((z != nd) if nd is not None else True)
        t0 = time.time()
        P = rx.find_extremes(np.where(v, -z, np.inf), v)
        dt = time.time() - t0
        zz = np.where(v, z, -np.inf)
        nbmax = ndimage.maximum_filter(zz, footprint=RING, mode="constant", cval=-np.inf)
        strict = np.argwhere(v & (zz > nbmax))
        pset = {(r["row"], r["col"]) for r in P}
        check("Grand Canyon: every strict local maximum is a reported peak ({:,} maxima, {:,} peaks, {:.0f} s)"
              .format(len(strict), len(P), dt), all((int(a), int(b)) in pset for a, b in strict))
        check("Grand Canyon: the top summit's prominence is 564.08 m (unchanged from the old engine)",
              abs(max(P, key=lambda r: r["prominence"])["prominence"] - 564.0777587890625) < 1e-6,
              max(P, key=lambda r: r["prominence"]))

print("\n{} passed, {} failed".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
