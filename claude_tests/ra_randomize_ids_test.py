# -*- coding: utf-8 -*-
"""randomize_ids option (Jeff, 2026-09-02): each array's Point_ID
becomes a random permutation of 1..m -- for use as a randomized
treatment ID (randomized complete block design). Checks: valid
permutations per array; not all arrays in sequential order; seed
reproducibility; Row/Col stay tied to geometry; default (off) still
sequential."""
import os
import sys
import shutil
import tempfile
from collections import defaultdict

sys.path.insert(0, r"D:\ArcGIS_stuff\general_tools\Pro_Wildlife"
                   r"\Wildlife_Tools\WildlifeTools\Toolboxes")
import arcpy
import random_arrays as RA

tmp = tempfile.mkdtemp(prefix="ra_ids_")
arcpy.management.CreateFileGDB(tmp, "t.gdb")
gdb = os.path.join(tmp, "t.gdb")

sr = arcpy.SpatialReference(26912)
arcpy.management.CreateFeatureclass(gdb, "bnd", "POLYGON",
                                    spatial_reference=sr)
ring = [(600000, 3600000), (600000, 3610000),
        (612000, 3610000), (612000, 3600000), (600000, 3600000)]
with arcpy.da.InsertCursor(os.path.join(gdb, "bnd"), ["SHAPE@"]) as ic:
    ic.insertRow([arcpy.Polygon(
        arcpy.Array([arcpy.Point(*p) for p in ring]), sr)])


def run(out_name, seed, randomize):
    out = os.path.join(gdb, out_name)
    RA.run_random_arrays(
        out_fc=out, boundary=os.path.join(gdb, "bnd"),
        strata_method="No stratification "
                      "(the whole area is one stratum)",
        alloc_method="Fixed total number of arrays", n_arrays=10,
        array_shape="Grid (rows and columns of points)",
        n_rows=5, n_cols=5, point_spacing=50.0,
        orientation_mode="Fixed orientation angle",
        orientation_angle=0.0, must_fit=True,
        linear_units="Meters", random_seed=seed,
        randomize_ids=randomize, messages=None)
    per = defaultdict(list)
    for aid, pid, r, c in arcpy.da.SearchCursor(
            out, ["Array_ID", "Point_ID", "Row", "Col"]):
        per[aid].append((pid, r, c))
    return per


# 1. randomized: valid permutation per array, geometry fields intact
a = run("r1", 42, True)
assert len(a) == 10
n_sequential = 0
for aid, recs in a.items():
    pids = [p for p, _, _ in recs]
    assert sorted(pids) == list(range(1, 26)), \
        "array {} Point_IDs not a permutation of 1..25".format(aid)
    rcs = {(r, c) for _, r, c in recs}
    assert len(rcs) == 25, "Row/Col no longer unique per station"
    if pids == sorted(pids):
        n_sequential += 1
assert n_sequential <= 1, \
    "{} of 10 arrays still sequential -- not randomized".format(
        n_sequential)
print("1. permutations valid; {}/10 arrays sequential by chance"
      .format(n_sequential))

# 2. same seed reproduces exactly
b = run("r2", 42, True)
assert {k: sorted(v) for k, v in a.items()} \
    == {k: sorted(v) for k, v in b.items()}
assert a == b, "same seed did not reproduce the same numbering"
print("2. seed-reproducible")

# 3. off (default): sequential numbering preserved
c = run("r3", 42, False)
for aid, recs in c.items():
    pids = [p for p, _, _ in recs]
    assert pids == sorted(pids) == list(range(1, 26)), \
        "default numbering no longer sequential"
print("3. default (off) stays sequential 1..25")

arcpy.management.Delete(gdb)
shutil.rmtree(tmp, ignore_errors=True)
print("ALL OK")
