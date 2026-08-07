"""Hidden helper for the Land Facet Clustering tool -- NOT a geoprocessing tool.

The C# dialog runs this as a one-shot subprocess (via ArcGIS Pro's bundled
python.exe) to turn the classified cluster-ID array it computed in memory into a
proper georeferenced raster. It uses GDAL only -- no arcpy, no license checkout
-- reusing the same GeoTIFF-writing approach the Jenness toolbox already uses
(TILED, LZW, BIGTIFF=IF_SAFER, self-computed statistics, so ArcGIS Pro never has
to re-run the slow Calculate Statistics). It reads one JSON "job" file that
points at a raw binary array and describes the target grid, and prints
``PROGRESS <fraction>`` lines that the C# dialog parses to drive its progress
meter, then ``OK <path>`` on success.

Job JSON schema:
    {
      "data": "<path to raw row-major binary array>",
      "out":  "<output raster path>",
      "width": int, "height": int,
      "dtype": "int32" | "float32" | "float64",
      "nodata": number,
      "geotransform": [originX, pixelW, 0, originY, 0, -pixelH],
      "wkt": "<projection WKT, or empty>"
    }
"""
import json
import sys

import numpy as np
from osgeo import gdal

gdal.UseExceptions()

_GDT = {
    "int32": gdal.GDT_Int32,
    "float32": gdal.GDT_Float32,
    "float64": gdal.GDT_Float64,
}


def write_raster(job):
    w = int(job["width"])
    h = int(job["height"])
    dtype = job.get("dtype", "int32")
    nodata = job.get("nodata", -9999)
    gt = [float(v) for v in job["geotransform"]]
    wkt = job.get("wkt", "") or ""

    arr = np.fromfile(job["data"], dtype=np.dtype(dtype))
    if arr.size != w * h:
        raise ValueError("array has {} values, expected {}x{}={}".format(
            arr.size, w, h, w * h))
    arr = arr.reshape(h, w)

    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(job["out"], w, h, 1, _GDT[dtype],
                    options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"])
    try:
        ds.SetGeoTransform(gt)
        if wkt:
            ds.SetProjection(wkt)
        band = ds.GetRasterBand(1)
        band.SetNoDataValue(float(nodata))

        # Write in row strips, self-computing statistics over valid cells and
        # reporting progress. Never call the (slow) Calculate Statistics.
        strip = max(1, 4_000_000 // max(1, w))
        smin = smax = None
        ssum = ssq = 0.0
        count = 0
        for y0 in range(0, h, strip):
            hh = min(strip, h - y0)
            block = arr[y0:y0 + hh, :]
            band.WriteArray(block, 0, y0)
            valid = block[block != nodata].astype(np.float64)
            if valid.size:
                bmin = float(valid.min())
                bmax = float(valid.max())
                smin = bmin if smin is None else min(smin, bmin)
                smax = bmax if smax is None else max(smax, bmax)
                ssum += float(valid.sum())
                ssq += float((valid * valid).sum())
                count += int(valid.size)
            sys.stdout.write("PROGRESS %.4f\n" % (min(y0 + hh, h) / h))
            sys.stdout.flush()

        if count > 0:
            mean = ssum / count
            var = max(0.0, ssq / count - mean * mean)
            band.SetStatistics(smin, smax, mean, var ** 0.5)

        # Optional attribute table: label each facet value with its source category
        # and local cluster number (Value/Count/Category/Cluster).
        rat = job.get("rat")
        if rat:
            grat = gdal.RasterAttributeTable()
            grat.CreateColumn("Value", gdal.GFT_Integer, gdal.GFU_MinMax)
            grat.CreateColumn("Count", gdal.GFT_Integer, gdal.GFU_PixelCount)
            grat.CreateColumn("Category", gdal.GFT_String, gdal.GFU_Name)
            grat.CreateColumn("Cluster", gdal.GFT_Integer, gdal.GFU_Generic)
            grat.SetRowCount(len(rat))
            for r, entry in enumerate(rat):
                val, cnt, cat, clu = entry
                grat.SetValueAsInt(r, 0, int(val))
                grat.SetValueAsInt(r, 1, int(cnt))
                grat.SetValueAsString(r, 2, str(cat))
                grat.SetValueAsInt(r, 3, int(clu))
            band.SetDefaultRAT(grat)

        band.FlushCache()
        ds.FlushCache()
    finally:
        ds = None
    return job["out"]


def main(argv):
    if len(argv) < 2:
        sys.stderr.write("usage: corridor_write_raster.py <job.json>\n")
        return 2
    with open(argv[1], "r") as f:
        job = json.load(f)
    out = write_raster(job)
    sys.stdout.write("OK %s\n" % out)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as exc:                       # noqa: BLE001
        sys.stderr.write("ERROR: {}\n".format(exc))
        sys.exit(1)
