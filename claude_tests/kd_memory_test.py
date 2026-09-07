import sys

import numpy as np
import arcpy

sys.path.insert(0, r"D:\ArcGIS_stuff\general_tools\Pro_Wildlife"
                   r"\Wildlife_Tools\WildlifeTools\Toolboxes")
import kernel_density as kd

arcpy.env.overwriteOutput = True
SR = arcpy.SpatialReference(32612)


class Msgs:
    def addMessage(self, t):
        print("   msg:", t)

    def addWarningMessage(self, t):
        print("   WARN:", t)


# points in memory
pts = arcpy.management.CreateFeatureclass(
    "memory", "pts", "POINT", spatial_reference=SR).getOutput(0)
rng = np.random.default_rng(5)
with arcpy.da.InsertCursor(pts, ["SHAPE@XY"]) as ins:
    for x, y in rng.normal((500000, 3800000), 300, (60, 2)):
        ins.insertRow([(float(x), float(y))])

# Jeff's crash case: memory OUTPUT, proportion surface
out = kd.run_kernel_density(
    points=pts, pop_field=None,
    out_raster=r"memory\kd_mem_test",
    cell_size="30", search_radius="500 Meters",
    kernel="Quartic (Esri KD method)", distance_method="Planar",
    out_value="Proportion under curve", area_unit="Square map units",
    messages=Msgs())
print("memory proportion run OK ->", out)

# poisoned-interpreter probe: the next dict call must not explode
probe = {"a": 1}
assert probe.get("a") == 1
print("interpreter state clean after run")

# memory contours over the memory raster (marker absent -> density path)
outs = kd.run_probability_contours(
    kd=r"memory\kd_mem_test",
    out_lines=r"memory\kd_mem_lines", out_polys=None, messages=Msgs())
print("memory contours run OK ->", outs)
probe2 = {"b": 2}
assert probe2.get("b") == 2
print("interpreter state clean after contours")
print()
print("MEMORY-WORKSPACE CRASH FIXED")
