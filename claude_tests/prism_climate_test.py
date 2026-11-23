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

"""PRISM Climate Data (prism_climate.py). Nothing here touches PRISM: the
grids are made up, with values that say which variable, date, row and
column they belong to, and are handed to the engine by a stand-in for
PRISM's web service. The engine's own download code is run against a web
server on this computer.

Run with Pro's Python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" prism_climate_test.py
"""
import datetime
import http.server
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile

import numpy as np
from osgeo import gdal, osr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "WildlifeTools", "Toolboxes"))
import prism_climate as P  # noqa: E402

gdal.UseExceptions()
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print("FAIL  %s  %s" % (name, detail))


def raises(name, kind, fn, has=None):
    try:
        fn()
        check(name, False, "nothing was raised")
    except kind as e:
        check(name, has is None or has in str(e), str(e)[:160])


work = tempfile.mkdtemp(prefix="prism_test_")
TODAY = datetime.date(2026, 11, 21)
ORDER = [v.code for v in P.VARIABLES]

# ---- the settings file and the default folder ------------------------------------------------------------
# The engine remembers the folder of downloaded files in a file of the Windows user, and has a default
# folder in the user's own folder. The tests must touch neither, so both are pointed into the work folder
# here, after a look at where the real ones are. The last check of this file confirms the real settings
# file was left as it was.
REAL_SETTINGS = P.settings_path()
REAL_DEFAULT = P.default_folder()


def _state(path):
    return (os.path.isfile(path), os.path.getmtime(path) if os.path.isfile(path) else None)


REAL_BEFORE = _state(REAL_SETTINGS)
check("settings: the file belongs to the Windows user, beside the add-in's other settings",
      REAL_SETTINGS.endswith(os.path.join("JennessEnterprises", "WildlifeTools", "prism_settings.json"))
      and os.path.normcase(REAL_SETTINGS).startswith(os.path.normcase(
          os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"))), REAL_SETTINGS)
check("settings: the default folder is PRISM_downloads in the user's own folder",
      os.path.basename(REAL_DEFAULT) == "PRISM_downloads"
      and P.same_folder(os.path.dirname(REAL_DEFAULT), os.path.expanduser("~")), REAL_DEFAULT)
TEST_SETTINGS = os.path.join(work, "appdata", "JennessEnterprises", "WildlifeTools", "prism_settings.json")
TEST_DEFAULT = os.path.join(work, "home", "PRISM_downloads")
P.settings_path = lambda: TEST_SETTINGS
P.default_folder = lambda: TEST_DEFAULT


def build_beside(out, *args, **kwargs):
    """The engine's build with the downloaded files beside the output, so that each test has a folder of
    its own, and with nothing remembered. The tests of the remembered folder call P.build themselves."""
    kwargs.setdefault("download_folder", os.path.join(os.path.dirname(out), "PRISM_downloads"))
    kwargs.setdefault("remember", False)
    return P.build(out, *args, **kwargs)


# ---- made-up grids ---------------------------------------------------------------------------------------
def value_grid(resolution, code, token):
    """A whole PRISM grid whose values name the variable, the date, the row and the column."""
    g = P.GRIDS[resolution]
    rows, cols = np.mgrid[0:g["rows"], 0:g["cols"]]
    a = (ORDER.index(code) * 1000.0 + (int(token) % 97) + (rows % 50) * 0.5 + (cols % 40) * 0.01).astype("float32")
    a[:40, :60] = -9999.0                                    # the grid's north-west corner is no-data
    return a


def write_zip(path, resolution, code, token, kind="an81/r1812", made="20200817-0147", nodata=-9999.0):
    g = P.GRIDS[resolution]
    name = "prism_{}_us_{}_{}".format(code, g["tag"], token)
    tif = os.path.join(work, name + ".tif")
    ds = gdal.GetDriverByName("GTiff").Create(tif, g["cols"], g["rows"], 1, gdal.GDT_Float32,
                                              ["COMPRESS=LZW", "TILED=YES"])
    cell = 1.0 / g["per_degree"]
    ds.SetGeoTransform((P.WEST, cell, 0, P.NORTH, 0, -cell))
    sr = osr.SpatialReference(); sr.ImportFromEPSG(4269)
    ds.SetProjection(sr.ExportToWkt())
    a = value_grid(resolution, code, token)
    if nodata != -9999.0:
        a[a == -9999.0] = nodata
    ds.GetRasterBand(1).WriteArray(a)
    ds.GetRasterBand(1).SetNoDataValue(nodata)
    ds = None
    if not os.path.isdir(os.path.dirname(path)):
        os.makedirs(os.path.dirname(path))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(tif, name + ".tif")
        z.writestr(name + ".info.txt", "PRISM_DATASET_FILENAME: {}.tif\nPRISM_DATASET_CREATE_DATE: {}\n"
                   "PRISM_DATASET_TYPE: {}\nPRISM_DATASET_VERSION: M3\n".format(name, made, kind))
    os.remove(tif)
    return os.path.getsize(path)


class FakeService(object):
    """Stands in for PRISM's web service."""
    pause = 0.0

    def __init__(self, not_issued=(), releases=None, kind="an81/r1812"):
        self.not_issued = set(not_issued)        # (code, token)
        self.releases = releases or {}           # (code, token) -> date
        self.kind = kind
        self.fetched = []
        self.asked_releases = []

    def fetch(self, set_code, resolution, code, token, dest, cancelled=None):
        if (code, token) in self.not_issued:
            raise P.NotIssued("Invalid date: " + token)
        self.fetched.append((set_code, resolution, code, token))
        return write_zip(dest, resolution, code, token, kind=self.kind)

    def release_dates(self, resolution, code, token0, token1, cancelled=None):
        self.asked_releases.append((code, token0, token1))
        return {t: d for (c, t), d in self.releases.items() if c == code and token0 <= t <= token1}


class Log(object):
    def __init__(self):
        self.said, self.warned, self.steps = [], [], []

    def kw(self):
        return dict(say=self.said.append, warn=self.warned.append,
                    step=lambda pct, text: self.steps.append((pct, text)), today=TODAY)


# ---- the dates -------------------------------------------------------------------------------------------
per, notes = P.plan_periods(P.STEP_MONTHLY, 2019, 2020, today=TODAY)
check("monthly: 24 months", len(per) == 24 and per[0].token == "201901" and per[-1].token == "202012")
check("monthly: a month ends where the next begins",
      per[11].start == datetime.date(2019, 12, 1) and per[11].end == datetime.date(2020, 1, 1))
per, notes = P.plan_periods(P.STEP_MONTHLY, 2025, 2027, months=[6, 7, 11, 12], today=TODAY)
check("monthly: months not over are left out",
      [p.token for p in per] == ["202506", "202507", "202511", "202512", "202606", "202607"], [p.token for p in per])
check("monthly: and the run is told", len(notes) == 1 and "November 2026" in notes[0], notes)
per, notes = P.plan_periods(P.STEP_ANNUAL, 1890, 2026, today=TODAY)
check("annual: 1895 to last year", per[0].token == "1895" and per[-1].token == "2025" and len(notes) == 2, notes)
per, notes = P.plan_periods(P.STEP_DAILY, date_from="2026-11-18", date_to=datetime.datetime(2026, 11, 25), today=TODAY)
check("daily: ends yesterday", [p.token for p in per] == ["20261118", "20261119", "20261120"] and len(notes) == 1)
per, notes = P.plan_periods(P.STEP_DAILY, date_from="1980-12-30", date_to="1981-01-02", today=TODAY)
check("daily: begins 1 January 1981", [p.token for p in per] == ["19810101", "19810102"] and len(notes) == 1)
per, notes = P.plan_periods(P.STEP_DAILY, date_from="2020-05-30", date_to="2020-07-02", months=[5, 7], today=TODAY)
check("daily: months kept", [p.token for p in per] == ["20200530", "20200531", "20200701", "20200702"])
raises("daily needs both days", P.PrismError, lambda: P.plan_periods(P.STEP_DAILY, date_from="2020-01-01"))
raises("years in order", P.PrismError, lambda: P.plan_periods(P.STEP_MONTHLY, 2020, 2019), "later")
raises("months 1 to 12", P.PrismError, lambda: P.plan_periods(P.STEP_MONTHLY, 2020, 2020, months=[13]))
raises("a date that is not one", P.PrismError, lambda: P.plan_periods(P.STEP_DAILY, date_from="soon", date_to="later"))

# ---- the window ------------------------------------------------------------------------------------------
# PRISM's cell CENTERS fall on whole 24ths of a degree, so its cell edges fall half a cell (1/48 degree) off them
ALIGNED = (-112.0 - 1 / 48.0, 35.0 + 1 / 48.0, -111.0 - 1 / 48.0, 36.0 + 1 / 48.0)
w = P.window_for_box(P.RES_4KM, ALIGNED, margin=0)
check("window: one degree between cell edges is 24 cells", (w.cols, w.rows) == (24, 24), (w.cols, w.rows))
check("window: its edges are the box's", abs(w.west - ALIGNED[0]) < 1e-9 and abs(w.north - ALIGNED[3]) < 1e-9
      and abs(w.east - ALIGNED[2]) < 1e-9 and abs(w.south - ALIGNED[1]) < 1e-9, (w.west, w.south, w.east, w.north))
wu = P.window_for_box(P.RES_4KM, (-112.0, 35.0, -111.0, 36.0), margin=0)
check("window: a box between cell edges takes the cells it touches", (wu.cols, wu.rows) == (25, 25)
      and wu.west <= -112.0 and wu.east >= -111.0 and wu.south <= 35.0 and wu.north >= 36.0, (wu.cols, wu.rows))
w1 = P.window_for_box(P.RES_4KM, ALIGNED, margin=1)
check("window: a margin of one cell", (w1.cols, w1.rows, w1.col0, w1.row0) == (w.cols + 2, w.rows + 2, w.col0 - 1, w.row0 - 1))
edge = P.WEST + 100 / 24.0
we = P.window_for_box(P.RES_4KM, (edge, 40.0, edge + 2 / 24.0, 40.01), margin=0)
check("window: a box that ends on cell edges takes no cell beyond them", (we.col0, we.cols) == (100, 2), (we.col0, we.cols))
wp = P.window_for_box(P.RES_4KM, (-111.65, 35.2, -111.65, 35.2), margin=1)
check("window: a point is its cell and the eight round it", (wp.cols, wp.rows) == (3, 3), (wp.cols, wp.rows))
w8 = P.window_for_box(P.RES_800M, ALIGNED, margin=0)
check("window: 800 m cells nest five by five", (w8.cols, w8.rows) == (5 * w.cols, 5 * w.rows)
      and abs(w8.west - w.west) < 1e-9 and abs(w8.north - w.north) < 1e-9, (w8.cols, w8.rows, w8.west, w.west))
wc = P.window_for_box(P.RES_4KM, (-130.0, 20.0, -124.5, 25.0), margin=1)
check("window: cut at the grid's edge", wc is not None and wc.col0 == 0 and wc.row0 + wc.rows == 621, None)
check("window: outside the grid", P.window_for_box(P.RES_4KM, (-160.0, 60.0, -150.0, 65.0)) is None)
check("window: whole grid", (lambda a: (a.cols, a.rows))(P.window_for_box(P.RES_4KM, (-140, 10, -50, 60))) == (1405, 621))

# ---- chunks ----------------------------------------------------------------------------------------------
ct, cy, cx = P.chunk_shape(480, 26, 26)
check("chunks: a small window keeps a long block of dates", cy == 26 and cx == 26 and 100 <= ct <= 480, (ct, cy, cx))
ct, cy, cx = P.chunk_shape(14600, 300, 300)
check("chunks: a large window", (cy, cx) == (48, 48) and ct == 56, (ct, cy, cx))
ct, cy, cx = P.chunk_shape(12, 3105, 7025)
check("chunks: the whole 800 m grid stays inside memory", ct * 3105 * 7025 <= 48000000 * 2 and ct >= 1, ct)
check("chunks: never more dates than there are", P.chunk_shape(3, 10, 10)[0] == 3)

# ---- the run ---------------------------------------------------------------------------------------------
BOX = (-112.1, 34.9, -111.2, 35.8)
out_nc = os.path.join(work, "run1", "prism.nc")
os.makedirs(os.path.dirname(out_nc))
svc = FakeService()
log = Log()
r = build_beside(out_nc, BOX, variables=["Precipitation (ppt)", "tmean"], time_step=P.STEP_MONTHLY, year_from=2019,
            year_to=2020, months=[1, 6, 7], service=svc, **log.kw())
check("run: six grids of each variable fetched", len(svc.fetched) == 12 and r["downloaded"] == 12 and r["reused"] == 0,
      (len(svc.fetched), r["downloaded"]))
check("run: file written, no part file left", os.path.isfile(out_nc) and not os.path.exists(out_nc + ".part"))
check("run: nothing is remembered when the caller says not to", not os.path.exists(TEST_SETTINGS)
      and r["folder_how"] == "given" and r["remembered"] is False, (r["folder_how"], r["remembered"]))
check("run: kept files in the folder that was given",
      os.path.isfile(os.path.join(work, "run1", "PRISM_downloads", "an_4km", "ppt", "prism_ppt_us_25m_201901.zip")))
check("run: progress ends at 100", log.steps and log.steps[-1][0] == 100 and "Grid 12 of 12" in log.steps[-2][1],
      log.steps[-2:])
check("run: the size of the download is said before it starts",
      any("12 grids to download" in s and "whole conterminous United States" in s for s in log.said), log.said)

import netCDF4  # noqa: E402
ds = netCDF4.Dataset(out_nc)
win = r["window"]
check("file: dimensions", (len(ds.dimensions["time"]), len(ds.dimensions["lat"]), len(ds.dimensions["lon"]))
      == (6, win.rows, win.cols), {k: len(v) for k, v in ds.dimensions.items()})
check("file: time is unlimited", ds.dimensions["time"].isunlimited())
when = netCDF4.num2date(ds.variables["time"][:], ds.variables["time"].units, only_use_cftime_datetimes=False)
check("file: dates", [d.strftime("%Y%m%d") for d in when] == ["20190101", "20190601", "20190701", "20200101",
                                                              "20200601", "20200701"], [str(d) for d in when])
tb = netCDF4.num2date(ds.variables["time_bnds"][:], ds.variables["time"].units, only_use_cftime_datetimes=False)
check("file: each month's bounds", tb[1][0].strftime("%Y%m%d") == "20190601" and tb[1][1].strftime("%Y%m%d") == "20190701")
# time must NOT name time_bnds in a bounds attribute: ArcGIS Pro then reads each step as a span, and its time
# slider, which moves a layer only when the map's time equals a time step, can no longer move it
t_var = ds.variables["time"]
check("file: time names no bounds, and says where each step's span is",
      "bounds" not in t_var.ncattrs() and "time_bnds" in t_var.comment
      and "ArcGIS Pro" in ds.variables["time_bnds"].comment, t_var.ncattrs())
# the marker by which the add-in knows a file of this tool (it opens the map's time slider on a single date
# for layers of such a file, and of no other): the same text in the engine and in the add-in, written into
# the file as an attribute, and to be found as plain bytes within the part of the file the add-in reads
import re  # noqa: E402
cs_path = os.path.join(HERE, "..", "WildlifeTools", "OnlineData", "Prism", "PrismLayerFacts.cs")
with open(cs_path, encoding="utf-8") as f:
    cs_text = f.read()
cs_marker = re.search(r'public const string Marker = "([^"]+)";', cs_text)
cs_limit = re.search(r"public const int MarkerSearchBytes = (\d+) \* (\d+);", cs_text)
check("marker: the engine and the add-in hold the same text",
      cs_marker is not None and cs_marker.group(1) == P.MADE_WITH, (cs_marker and cs_marker.group(1), P.MADE_WITH))
check("marker: in the file as its made_with attribute", ds.getncattr("made_with") == P.MADE_WITH)
with open(out_nc, "rb") as f:
    head = f.read(int(cs_limit.group(1)) * int(cs_limit.group(2)) if cs_limit else 0)
check("marker: as plain bytes within the part of the file the add-in reads",
      cs_limit is not None and 0 <= head.find(P.MADE_WITH.encode("ascii")) < 200000,
      head.find(P.MADE_WITH.encode("ascii")))
check("marker: not the wording of the file's history, which older files of the tool also hold",
      P.MADE_WITH not in ds.getncattr("history"))
lat, lon = ds.variables["lat"][:], ds.variables["lon"][:]
check("file: cell centers", abs(lon[0] - (win.west + win.cell / 2)) < 1e-9 and abs(lat[0] - (win.north - win.cell / 2)) < 1e-9
      and lat[0] > lat[-1])
lb, ob = ds.variables["lat_bnds"][:], ds.variables["lon_bnds"][:]
check("file: cell edges", abs(lb[0][1] - win.north) < 1e-9 and abs(lb[-1][0] - win.south) < 1e-9
      and abs(ob[0][0] - win.west) < 1e-9 and abs(ob[-1][1] - win.east) < 1e-9
      and np.allclose(ob[1:, 0], ob[:-1, 1]) and np.allclose(lb[1:, 1], lb[:-1, 0]))
ok = True
for code in ("ppt", "tmean"):
    for k, token in enumerate(["201901", "201906", "201907", "202001", "202006", "202007"]):
        src = value_grid(P.RES_4KM, code, token)[win.row0:win.row0 + win.rows, win.col0:win.col0 + win.cols]
        got = ds.variables[code][k].filled(-9999.0)
        ok = ok and np.array_equal(got, src)
check("file: every value is PRISM's own cell", ok)

# each variable's values over ALL its time steps: in the run's answer, in the file, and in the statistics
# file beside it, from which ArcGIS Pro takes the stretch of a layer
for code in ("ppt", "tmean"):
    stack = np.stack([value_grid(P.RES_4KM, code, token)[win.row0:win.row0 + win.rows, win.col0:win.col0 + win.cols]
                      for token in ["201901", "201906", "201907", "202001", "202006", "202007"]]).astype("float64")
    good = stack[stack != -9999.0]
    rr = r["ranges"][code]
    check("range: %s over all the time steps" % code,
          abs(rr["min"] - good.min()) < 1e-6 and abs(rr["max"] - good.max()) < 1e-6
          and abs(rr["mean"] - good.mean()) < 1e-6 and abs(rr["std"] - good.std()) < 1e-6 and rr["n"] == good.size,
          (rr, good.min(), good.max(), good.mean(), good.std(), good.size))
    ar = ds.variables[code].actual_range
    check("file: actual_range of %s" % code, abs(float(ar[0]) - good.min()) < 1e-3
          and abs(float(ar[1]) - good.max()) < 1e-3, ar)
side = P.statistics_path(out_nc)
side_text = open(side, encoding="utf-8").read() if os.path.isfile(side) else ""
check("statistics file: beside the netCDF file, one entry for each variable",
      side == out_nc + ".aux.xml" and "Sliced view of /ppt ([0])" in side_text
      and "Sliced view of /tmean ([0])" in side_text and side_text.count("<Array ") == 2, side_text[:300])
check("statistics file: holds the values of all the time steps, not of the first",
      "<Minimum>%r</Minimum>" % r["ranges"]["ppt"]["min"] in side_text
      and "<Maximum>%r</Maximum>" % r["ranges"]["tmean"]["max"] in side_text
      and r["ranges"]["tmean"]["max"] > float(ds.variables["tmean"][0].max()), side_text[:400])
check("statistics file: none where there are no values, and an old one is removed",
      P.write_statistics(out_nc + ".none", {}) is None and not os.path.exists(out_nc + ".none.aux.xml"))
with open(out_nc + ".old.aux.xml", "w") as f:
    f.write("left by an earlier file")
P.write_statistics(out_nc + ".old", {})
check("statistics file: an old one goes even when no new one is written", not os.path.exists(out_nc + ".old.aux.xml"))
check("range: none without a value", P.value_range({"n": 0}) is None)

# evenly spaced time steps, which the map's time slider can step through, and the others
check("run: January, June and July of two years are not evenly spaced",
      r["even_steps"] is False and r["time_step"] == P.STEP_MONTHLY, (r["even_steps"], r["time_step"]))
per, _n = P.plan_periods(P.STEP_MONTHLY, 2019, 2020, today=TODAY)
check("even steps: every month of two years", P.even_steps(per) and P.even_steps(per[:1]))
per, _n = P.plan_periods(P.STEP_MONTHLY, 2019, 2020, months=[6, 7, 8], today=TODAY)
check("even steps: summers only are not", not P.even_steps(per))
per, _n = P.plan_periods(P.STEP_DAILY, date_from="2020-02-27", date_to="2020-03-02", today=TODAY)
check("even steps: days across the end of February", P.even_steps(per) and len(per) == 5)
notes = P.viewing_notes(r)
check("notes: uneven steps, the stretch and adding the file to another map",
      len(notes) == 4 and "Multidimensional tab" in notes[0] and "not evenly spaced" in notes[0]
      and "some months are left out" in notes[0] and "all the time steps" in notes[1] and "ppt " in notes[1]
      and " mm" in notes[1] and "tmean " in notes[1] and "choose Multidimensional Data. " in notes[2], notes)
check("notes: where the notes that go with the data are",
      "are in the metadata: right-click a layer in the Contents pane and choose View Metadata" in notes[3]
      and "on the Source tab, under Multidimensional Info" in notes[3], notes[3])
notes = P.viewing_notes(dict(r, even_steps=True))
check("notes: even steps, the time slider too, on a single date",
      "time slider moves the layers too" in notes[0] and "a span one month long" in notes[0]
      and "set Span to 0" in notes[0] and "from one month to the next" in notes[0]
      and "add-in closes the span" in notes[0] and "no other layer that uses time" in notes[0]
      and "not evenly spaced" not in notes[0], notes)
notes = P.viewing_notes(dict(r, even_steps=True, time_step=P.STEP_ANNUAL))
check("notes: by one year", "a span one year long" in notes[0] and "from one year to the next" in notes[0], notes)
notes = P.viewing_notes(dict(r, periods=1))
check("notes: one time step, nothing about stepping", len(notes) == 2 and "another map" in notes[0]
      and "View Metadata" in notes[1], notes)
v = ds.variables["ppt"]
check("file: variable attributes", v.units == "mm" and v.grid_mapping == "crs" and v.cell_measures == "area: cell_area"
      and v.cell_methods == "time: sum" and ds.variables["tmean"].units == "degC"
      and ds.variables["tmean"].cell_methods == "time: mean within days time: mean over days", v.ncattrs())
check("file: chunked in blocks of dates and cells", v.chunking() == list(P.chunk_shape(6, win.rows, win.cols)), v.chunking())
check("file: compressed", v.filters().get("zlib") and v.filters().get("shuffle"))
check("file: when PRISM made each grid", list(ds.variables["ppt_prism_created"][:]) == [20200817] * 6)
check("file: old grids are not provisional", int(ds.variables["provisional"][:].sum()) == 0)
check("file: says where it came from", "PRISM Group, Oregon State University" in ds.citation and "accessed 21 Nov 2026" in ds.citation
      and "very long-term trends" in ds.comment and ds.Conventions == "CF-1.8", ds.citation)
crs = ds.variables["crs"]
check("file: NAD 83", crs.grid_mapping_name == "latitude_longitude" and "NAD83" in crs.crs_wkt and "4269" in crs.spatial_ref)
area = ds.variables["cell_area"][:]
check("file: cells narrow northward", area.shape == (win.rows, win.cols) and area[0, 0] < area[-1, 0]
      and np.allclose(area[:, 0], area[:, -1]), (float(area[0, 0]), float(area[-1, 0])))
# a point's whole series and one date's map, read as an analysis would read them
series = ds.variables["tmean"][:, 5, 7]
check("read: the series of one cell", series.shape == (6,) and abs(float(series[3]) - float(value_grid(
    P.RES_4KM, "tmean", "202001")[win.row0 + 5, win.col0 + 7])) < 1e-6)
ds.close()

# an independent check of the cell areas: the zone between two parallels, by numerical integration
f = 1 / 298.257222101
e2 = f * (2 - f)
a_, b_ = 6378137.0, 6378137.0 * (1 - f)
lat_e = win.lat_edges()


def zone(lat0, lat1, n=20000):
    phi = np.radians(np.linspace(lat0, lat1, n + 1))
    y = np.cos(phi) / (1 - e2 * np.sin(phi) ** 2) ** 2
    return abs(a_ * a_ * (1 - e2) * np.trapezoid(y, phi)) * np.radians(win.cell) / 1e6


mine = P.cell_areas_km2(lat_e, win.cell)
check("cell area: equals the integral over the ellipsoid",
      all(abs(mine[i] - zone(lat_e[i], lat_e[i + 1])) / mine[i] < 1e-8 for i in (0, len(mine) // 2, len(mine) - 1)),
      (mine[0], zone(lat_e[0], lat_e[1])))
check("cell area: about 17 square kilometers at 35 degrees", 16.0 < mine[0] < 18.5, mine[0])

# ---- a second run downloads nothing -------------------------------------------------------------------------
svc2 = FakeService()
log2 = Log()
r2 = build_beside(out_nc, BOX, variables=["ppt", "tmean"], time_step=P.STEP_MONTHLY, year_from=2019, year_to=2020,
             months=[1, 6, 7], service=svc2, **log2.kw())
check("second run: nothing fetched", svc2.fetched == [] and r2["reused"] == 12 and r2["downloaded"] == 0)
check("second run: no release dates asked for old grids", svc2.asked_releases == [])
check("second run: says so", any("already in" in s for s in log2.said) and not any("to download" in s for s in log2.said))

# ---- a bigger window from the same kept files -------------------------------------------------------------
svc3 = FakeService()
out3 = os.path.join(work, "run1", "bigger.nc")
r3 = build_beside(out3, (-114.0, 33.0, -109.0, 37.0), variables=["ppt"], time_step=P.STEP_MONTHLY, year_from=2019,
             year_to=2019, months=[1, 6, 7], service=svc3, margin_cells=3, **Log().kw())
check("another study area: the kept files serve it", svc3.fetched == [] and r3["cols"] > r["cols"])

# ---- files not kept --------------------------------------------------------------------------------------
out4 = os.path.join(work, "run4", "prism.nc")
os.makedirs(os.path.dirname(out4))
svc4 = FakeService()
r4 = build_beside(out4, BOX, variables=["tmax"], time_step=P.STEP_ANNUAL, year_from=2001, year_to=2003, service=svc4,
             keep_downloads=False, **Log().kw())
check("not kept: the file is written", os.path.isfile(out4) and r4["periods"] == 3 and r4["downloaded"] == 3)
check("not kept: nothing is left behind", not os.path.exists(os.path.join(work, "run4", "PRISM_downloads")),
      os.listdir(os.path.join(work, "run4")))
# ---- the notes of an annual file, and the metadata -----------------------------------------------------------
ds4 = netCDF4.Dataset(out4)
note4 = ds4.variables["tmax"].comment
ds4.close()
lead = "A year's grid does not always equal its twelve monthly grids added up or averaged."
check("annual: the caution about years and months LEADS the variable's note, inside what one line shows",
      note4.startswith(lead) and len(lead) < 100 and "For each year: the average of the daily values" in note4
      and note4 == P.variable_note(P.BY_CODE["tmax"], P.STEP_ANNUAL), note4)
check("monthly and daily notes say what a value is, and carry no caution about years",
      P.variable_note(P.BY_CODE["ppt"], P.STEP_MONTHLY).startswith("For each month: the sum of the daily values")
      and P.variable_note(P.BY_CODE["ppt"], P.STEP_DAILY).startswith("For each day: ")
      and "year's grid" not in P.variable_note(P.BY_CODE["ppt"], P.STEP_MONTHLY)
      and P.variable_note(P.BY_CODE["tmax"], P.STEP_ANNUAL, lead=False).startswith("For each year: "))
md4 = P.metadata_parts(r4)
d4 = md4["description"]
check("metadata: every note of an annual file is in the description",
      "<b>The variables.</b>" in d4 and "For each year: the average of the daily values" in d4
      and "In this file its values run from " in d4 and "<b>Annual grids.</b> " + lead in d4
      and d4.count(lead) == 1, d4[:900])
check("metadata: the dates, the young grids, the cells, the caution, the citation, the links",
      "<b>The dates.</b> 3 time steps, one for each year, from 2001 to 2003. Each time step is named by its first day"
      in d4 and "<b>Grids less than six months old.</b> No time step of this file" in d4
      and "lat_bnds, lon_bnds" in d4 and "cell_area" in d4 and "narrow northward" in d4
      and "very long-term trends" in d4 and "<b>How to cite.</b>" in d4 and r4["citation"] in d4
      and "Multidimensional Info" in d4 and d4.count("<a href=") == 3, d4)
ds4 = netCDF4.Dataset(out4)
area_note = ds4.variables["cell_area"].comment
ds4.close()
check("the note of cell_area says what it is and gives no advice on weighting by a polygon's share",
      area_note == "This is each cell's true area. The cells are squares of latitude and longitude, so they narrow "
                   "northward." and "share" not in area_note and "share of it a polygon" not in d4
      and "(cell_area). The cells are squares of latitude and longitude, so they narrow northward. For each" in d4,
      area_note)
check("metadata: the summary says how the file was made",
      md4["summary"].startswith("PRISM annual grids at 4 km, All networks (AN): tmax for 3 time steps (2001 to 2003), "
                                "cut from PRISM's grid to ")
      and " degrees of longitude, " in md4["summary"] and "Written on " + TODAY.isoformat() in md4["summary"]
      and md4["credits"] == r4["citation"] and md4["title"] == "prism.nc" and "terms of use" in md4["accessConstraints"],
      md4["summary"])
md1 = P.metadata_parts(dict(r, area_words="the features of pts", provisional=2, missing=[("tmean", "2019-07")]))
md0 = P.metadata_parts(dict(r4, periods=1, last=r4["first"]))
check("metadata: a file of one time step names its one date",
      ": tmax for one time step (2001), cut from" in md0["summary"]
      and "<b>The dates.</b> One time step: 2001. Each time step is named" in md0["description"], md0["summary"])
check("metadata: a monthly file has no caution about years; young grids and dates not issued are told",
      "Annual grids." not in md1["description"] and "For each month: " in md1["description"]
      and "2 of the 6 time steps were less than six months old when their grids were fetched" in md1["description"]
      and "<b>Dates PRISM had not issued.</b> PRISM had not yet issued: tmean for 2019-07." in md1["description"]
      and "around the features of pts)" in md1["summary"], (md1["summary"], md1["description"][:600]))
check("not kept: the run says what it deleted, and no more than that", r4["deleted"] == 3 and P.not_kept_notes(r4) == [
    "The 3 grids this run downloaded were deleted when their cells had been read."], P.not_kept_notes(r4))

# A run that does not keep its downloads NEVER deletes a file that was in the folder before it began.
store4 = os.path.join(work, "run4b", "PRISM_downloads")
out4b = os.path.join(work, "run4b", "prism.nc")
os.makedirs(os.path.dirname(out4b))
kw4 = dict(variables=["tmax"], time_step=P.STEP_ANNUAL, download_folder=store4, remember=False)
P.build(out4b, BOX, year_from=2001, year_to=2002, service=FakeService(), **dict(kw4, **Log().kw()))
tmax_dir = os.path.dirname(P.kept_path(store4, "an", P.RES_4KM, "tmax", "2001"))
saved = sorted(os.listdir(tmax_dir))
svc4b = FakeService()
r4b = P.build(out4b, BOX, year_from=2001, year_to=2002, service=svc4b, keep_downloads=False, **dict(kw4, **Log().kw()))
check("not kept: a run that needs only grids already there downloads nothing and deletes nothing",
      svc4b.fetched == [] and r4b["deleted"] == 0 and r4b["reused"] == 2 and sorted(os.listdir(tmax_dir)) == saved
      and len(saved) == 2, (svc4b.fetched, r4b["deleted"], os.listdir(tmax_dir)))
n4b = P.not_kept_notes(r4b)
check("not kept: and it says so, and how to delete the saved files",
      len(n4b) == 1 and n4b[0].startswith("This run deleted no files. The grids that were already in " + store4)
      and "a run never deletes a file that was in the folder before it began" in n4b[0]
      and n4b[0].endswith("To delete them, use Windows File Explorer."), n4b)
svc4c = FakeService()
r4c = P.build(out4b, BOX, year_from=2001, year_to=2004, service=svc4c, keep_downloads=False, **dict(kw4, **Log().kw()))
check("not kept: of old and new grids, only the ones this run downloaded are deleted",
      [f[3] for f in svc4c.fetched] == ["2003", "2004"] and r4c["deleted"] == 2 and r4c["periods"] == 4
      and sorted(os.listdir(tmax_dir)) == saved, (svc4c.fetched, r4c["deleted"], os.listdir(tmax_dir)))
n4c = P.not_kept_notes(r4c)
check("not kept: and both are said", len(n4c) == 1 and n4c[0].startswith(
    "The 2 grids this run downloaded were deleted when their cells had been read. The grids that were already in ")
    and "Windows File Explorer" in n4c[0], n4c)
check("not kept: one grid is said in the singular", P.not_kept_notes({"deleted": 1, "reused": 0, "refreshed": 0}) == [
    "The one grid this run downloaded was deleted when its cells had been read."])


# A kept grid that somebody deletes after the run was planned and before the run reaches it.
os.makedirs(os.path.join(work, "run4d"))
store4d = os.path.join(work, "run4d", "PRISM_downloads")
kw4d = dict(variables=["tmax"], time_step=P.STEP_ANNUAL, download_folder=store4d, remember=False)
P.build(os.path.join(work, "run4d", "p.nc"), BOX, year_from=2002, year_to=2002, service=FakeService(),
        **dict(kw4d, **Log().kw()))


class GoneService(FakeService):
    """As FakeService; and while it fetches 2001, 'somebody' deletes the kept grid of 2002 with File Explorer."""

    def fetch(self, set_code, resolution, code, token, dest, cancelled=None):
        if token == "2001":
            os.remove(P.kept_path(store4d, set_code, resolution, code, "2002"))
        return FakeService.fetch(self, set_code, resolution, code, token, dest, cancelled)


svc4d = GoneService()
r4d = P.build(os.path.join(work, "run4d", "p.nc"), BOX, year_from=2001, year_to=2002, service=svc4d,
              **dict(kw4d, **Log().kw()))
check("a kept grid that is deleted while the run is under way is downloaded again, and the run goes on",
      [f[3] for f in svc4d.fetched] == ["2001", "2002"] and r4d["downloaded"] == 2 and r4d["periods"] == 2
      and os.path.isfile(P.kept_path(store4d, "an", P.RES_4KM, "tmax", "2002")), (svc4d.fetched, r4d["downloaded"]))
ds = netCDF4.Dataset(out4)
tb = netCDF4.num2date(ds.variables["time_bnds"][:], ds.variables["time"].units, only_use_cftime_datetimes=False)
check("annual: a year's bounds", tb[0][0].strftime("%Y%m%d") == "20010101" and tb[0][1].strftime("%Y%m%d") == "20020101")
check("annual: the file says a year's grid is PRISM's own, not the months added up",
      "PRISM's own annual grids" in ds.variables["tmax"].comment, ds.variables["tmax"].comment)
ds.close()
check("the day PRISM made a grid: one date", P.created_day({"PRISM_DATASET_CREATE_DATE": "20260729-1335"}) == 20260729)
check("the day PRISM made a grid: a year's grid lists twelve, the latest is taken",
      P.created_day({"PRISM_DATASET_CREATE_DATE": "20190201, 20190301, 20200219, 20200219"}) == 20200219)
check("the day PRISM made a grid: not given", P.created_day({}) == 0)
check("the day PRISM made a grid: the day it was modeled, where the file gives it apart from the day it was written",
      P.created_day({"PRISM_DATASET_CREATE_DATE": "20260729-1347", "PRISM_SOURCE_CREATE_DATE": "20200713-2140"})
      == 20200713)

# ---- grids PRISM has not issued yet -------------------------------------------------------------------------
out5 = os.path.join(work, "run5", "prism.nc")
os.makedirs(os.path.dirname(out5))
svc5 = FakeService(not_issued=[("tmean", "202609"), ("tmean", "202610"), ("ppt", "202610")])
log5 = Log()
r5 = build_beside(out5, BOX, variables=["ppt", "tmean"], time_step=P.STEP_MONTHLY, year_from=2026, year_to=2026,
             months=[7, 8, 9, 10], service=svc5, **log5.kw())
ds = netCDF4.Dataset(out5)
check("not issued: the time steps that have any grid are kept", len(ds.dimensions["time"]) == 3 and r5["periods"] == 3,
      len(ds.dimensions["time"]))
check("not issued: the variable without a grid is no-data there",
      bool(np.ma.getmaskarray(ds.variables["tmean"][2]).all()) and not np.ma.getmaskarray(ds.variables["ppt"][2]).all())
check("not issued: later dates are not asked for", ("an", P.RES_4KM, "tmean", "202610") not in svc5.fetched
      and len(svc5.fetched) == 5, svc5.fetched)
check("not issued: warned", any("has not issued tmean for 2026-09" in s for s in log5.warned)
      and any("no values for tmean" in s for s in log5.warned), log5.warned)
check("recent grids are marked provisional", int(ds.variables["provisional"][:].sum()) == 3 and r5["provisional"] == 3
      and any("less than six months old" in s for s in log5.warned))
check("not issued: coverage ends at the last date written", ds.time_coverage_end == "2026-09-30", ds.time_coverage_end)
ds.close()
raises("nothing issued at all", P.PrismError, lambda: build_beside(
    os.path.join(work, "run5", "none.nc"), BOX, variables=["ppt"], time_step=P.STEP_MONTHLY, year_from=2026,
    year_to=2026, months=[10], service=FakeService(not_issued=[("ppt", "202610")]), **Log().kw()), "none of the grids")
check("nothing issued: no file and no part file", not os.path.exists(os.path.join(work, "run5", "none.nc"))
      and not os.path.exists(os.path.join(work, "run5", "none.nc.part")))

# ---- a kept grid that PRISM has reissued since ---------------------------------------------------------------
kept = P.kept_path(os.path.join(work, "run5", "PRISM_downloads"), "an", P.RES_4KM, "ppt", "202608")
old = time.mktime(datetime.datetime(2026, 10, 1, 12).timetuple())
os.utime(kept, (old, old))
kept7 = P.kept_path(os.path.join(work, "run5", "PRISM_downloads"), "an", P.RES_4KM, "ppt", "202607")
os.utime(kept7, (old, old))
svc6 = FakeService(releases={("ppt", "202608"): datetime.date(2026, 11, 3), ("ppt", "202607"): datetime.date(2026, 9, 20)})
log6 = Log()
r6 = build_beside(out5, BOX, variables=["ppt"], time_step=P.STEP_MONTHLY, year_from=2026, year_to=2026, months=[7, 8],
             service=svc6, **log6.kw())
check("reissued: PRISM is asked once for the grids in doubt", svc6.asked_releases == [("ppt", "202607", "202608")],
      svc6.asked_releases)
check("reissued: only the reissued grid is fetched again",
      svc6.fetched == [("an", P.RES_4KM, "ppt", "202608")] and r6["refreshed"] == 1 and r6["reused"] == 1, svc6.fetched)
check("reissued: said", any("PRISM has reissued it since" in s for s in log6.said), log6.said)

# ---- the limit on a download ---------------------------------------------------------------------------------
svc7 = FakeService()
raises("too large a download is refused before it starts", P.PrismError, lambda: build_beside(
    os.path.join(work, "run5", "big.nc"), BOX, variables=["ppt", "tmin", "tmax"], time_step=P.STEP_DAILY,
    date_from="2010-01-01", date_to="2019-12-31", resolution=P.RES_800M, service=svc7, **Log().kw()), "Nothing was downloaded")
check("too large: nothing fetched", svc7.fetched == [])
svc8 = FakeService()
raises("the limit can be lowered", P.PrismError, lambda: build_beside(
    os.path.join(work, "run5", "big.nc"), BOX, variables=["ppt"], time_step=P.STEP_MONTHLY, year_from=2000,
    year_to=2000, max_download_gb=0.01, service=svc8, **Log().kw()), "more than the 0.01 GB allowed")

# ---- the Long-term networks dataset ----------------------------------------------------------------------------
raises("LT is monthly at 800 m", P.PrismError, lambda: build_beside(
    os.path.join(work, "run5", "lt.nc"), BOX, variables=["tmax"], time_step=P.STEP_MONTHLY, year_from=2005,
    year_to=2005, dataset=P.SET_LT, service=FakeService(), **Log().kw()), "800 m only")
raises("LT has no solar radiation", P.PrismError, lambda: build_beside(
    os.path.join(work, "run5", "lt.nc"), BOX, variables=["soltotal"], time_step=P.STEP_MONTHLY, year_from=2005,
    year_to=2005, dataset=P.SET_LT, resolution=P.RES_800M, service=FakeService(), **Log().kw()), "no solar")
out9 = os.path.join(work, "run9", "lt.nc")
os.makedirs(os.path.dirname(out9))
svc9 = FakeService(kind="lt81/r1812")
r9 = build_beside(out9, (-111.7, 35.1, -111.6, 35.2), variables=["tmax"], time_step=P.STEP_MONTHLY, year_from=2005,
             year_to=2005, months=[6], dataset=P.SET_LT, resolution=P.RES_800M, service=svc9, max_download_gb=1,
             **Log().kw())
check("LT: fetched as lt, kept apart from AN", svc9.fetched == [("lt", P.RES_800M, "tmax", "200506")]
      and os.path.isfile(os.path.join(work, "run9", "PRISM_downloads", "lt_800m", "tmax", "prism_tmax_us_30s_200506.zip")))
ds = netCDF4.Dataset(out9)
w9 = r9["window"]
src = value_grid(P.RES_800M, "tmax", "200506")[w9.row0:w9.row0 + w9.rows, w9.col0:w9.col0 + w9.cols]
check("LT: 800 m values are PRISM's own cells", np.array_equal(ds.variables["tmax"][0].filled(-9999.0), src)
      and ds.prism_dataset == P.SET_LT)
ds.close()
# an AN run that finds an LT grid under its own name must refuse it
wrong = P.kept_path(os.path.join(work, "run9", "PRISM_downloads"), "an", P.RES_800M, "tmax", "200506")
write_zip(wrong, P.RES_800M, "tmax", "200506", kind="lt81/r1812")
raises("a grid of the other dataset is refused", P.PrismError, lambda: build_beside(
    out9, (-111.7, 35.1, -111.6, 35.2), variables=["tmax"], time_step=P.STEP_MONTHLY, year_from=2005, year_to=2005,
    months=[6], resolution=P.RES_800M, service=FakeService(), max_download_gb=1, **Log().kw()), "LT81")
check("a refused run leaves the earlier file alone", os.path.isfile(out9) and not os.path.exists(out9 + ".part"))

# ---- other refusals --------------------------------------------------------------------------------------
raises("outside the United States", P.PrismError, lambda: build_beside(
    out_nc, (10.0, 45.0, 11.0, 46.0), variables=["ppt"], year_from=2000, year_to=2000, service=FakeService(),
    **Log().kw()), "conterminous United States")
raises("an unknown variable", P.PrismError, lambda: P.variables_from(["snow"]), "no PRISM variable")
check("variables come back in PRISM's order", [v.code for v in P.variables_from(["tmax", "Precipitation (ppt)"])] == ["ppt", "tmax"])
check("output must be .nc", P.check_output(os.path.join(work, "x.tif")) is not None
      and P.check_output(os.path.join(work, "x.nc")) is None
      and "geodatabase" in P.check_output(os.path.join(work, "a.gdb", "x.nc"))
      and "geodatabase" in P.check_output("memory\\x.nc")
      and "does not exist" in P.check_output(os.path.join(work, "no_such_folder", "x.nc")))
# a no-data value other than PRISM's own is turned into it
odd = P.kept_path(os.path.join(work, "odd"), "an", P.RES_4KM, "ppt", "200001")
write_zip(odd, P.RES_4KM, "ppt", "200001", nodata=-3.4e38)
a = P.read_window(odd, P.Window(P.RES_4KM, 50, 30, 20, 20))
check("another no-data value becomes -9999", float(a[0, 0]) == -9999.0 and float(a[-1, -1]) > 0, (a[0, 0], a[-1, -1]))


# ---- the folder of downloaded files, remembered from one project to the next ---------------------------------
import json  # noqa: E402


def one_month(out, folder=None, year=2015, **more):
    """One grid (ppt, January) for a project whose output is ``out``. Returns (result, service, log)."""
    if not os.path.isdir(os.path.dirname(out)):
        os.makedirs(os.path.dirname(out))
    s, lg = FakeService(), Log()
    res = P.build(out, BOX, variables=["ppt"], time_step=P.STEP_MONTHLY, year_from=year, year_to=year, months=[1],
                  service=s, download_folder=folder, **dict(lg.kw(), **more))
    return res, s, lg


check("folder: nothing is remembered at first", P.remembered_folder() is None and not os.path.exists(TEST_SETTINGS))
check("folder: with nothing given or remembered, the default one",
      P.choose_folder(None) == (TEST_DEFAULT, "default", None) and P.choose_folder("") == (TEST_DEFAULT, "default", None))
check("folder: a dialog is offered nothing while the default folder is not there", P.folder_to_offer() is None)

# project A, no folder given: the default folder, and nothing written to the settings
rA, sA, lA = one_month(os.path.join(work, "projA", "a.nc"))
check("folder: the default folder is used and made",
      rA["folder"] == TEST_DEFAULT and rA["folder_how"] == "default" and rA["remembered"] is False
      and os.path.isfile(P.kept_path(TEST_DEFAULT, "an", P.RES_4KM, "ppt", "201501")), rA["folder"])
check("folder: the run says where the files go and that a folder can be given",
      any("none is remembered" in s and TEST_DEFAULT in s and "remembers it for every project" in s for s in lA.said),
      lA.said)
check("folder: using the default writes no settings file", not os.path.exists(TEST_SETTINGS))
check("folder: the dialog is offered the default folder once it is there", P.folder_to_offer() == TEST_DEFAULT)

# project A gives a folder outside the project: it is remembered
storeA = os.path.join(work, "Climate Store", "PRISM")
rA2, sA2, lA2 = one_month(os.path.join(work, "projA", "a2.nc"), folder=storeA)
check("folder: a folder that is given is made, used and remembered",
      rA2["folder"] == storeA and rA2["folder_how"] == "given" and rA2["remembered"] is True
      and len(sA2.fetched) == 1 and P.same_folder(P.remembered_folder(), storeA), (rA2["folder"], P.remembered_folder()))
check("folder: the settings file is JSON with the folder in it",
      json.load(open(TEST_SETTINGS, encoding="utf-8")).get("download_folder") == os.path.normpath(storeA))
check("folder: no part file is left beside the settings", os.listdir(os.path.dirname(TEST_SETTINGS)) == ["prism_settings.json"],
      os.listdir(os.path.dirname(TEST_SETTINGS)))

# project B, another folder, another output, no folder given: the files of project A are found
rB, sB, lB = one_month(os.path.join(work, "projB", "deep", "b.nc"))
check("folder: another project finds the remembered folder and downloads nothing",
      rB["folder"] == os.path.normpath(storeA) and rB["folder_how"] == "remembered" and rB["remembered"] is True
      and sB.fetched == [] and rB["reused"] == 1, (rB["folder"], rB["folder_how"], sB.fetched))
check("folder: and says which folder it remembered",
      any("remembers from an earlier run" in s and os.path.normpath(storeA) in s for s in lB.said)
      and not lB.warned, lB.said)
check("folder: no downloads were put beside project B's output, only the file and its statistics",
      sorted(os.listdir(os.path.join(work, "projB", "deep"))) == ["b.nc", "b.nc.aux.xml"],
      os.listdir(os.path.join(work, "projB", "deep")))
check("folder: the dialog is offered the remembered folder", P.same_folder(P.folder_to_offer(), storeA))
check("folder: a folder given is the same folder whatever its slashes and capitals",
      P.same_folder(storeA.replace("\\", "/").upper() + "/", storeA) and not P.same_folder(storeA, TEST_DEFAULT)
      and not P.same_folder(None, storeA))

# what is not remembered
scratch_store = os.path.join(work, "one_off")
rC, sC, lC = one_month(os.path.join(work, "projB", "c.nc"), folder=scratch_store, keep_downloads=False)
check("folder: not remembered when the files are not kept",
      P.same_folder(P.remembered_folder(), storeA) and rC["remembered"] is False and not os.path.exists(scratch_store))
rD, sD, lD = one_month(os.path.join(work, "projB", "d.nc"), folder=scratch_store, remember=False)
check("folder: not remembered when the caller says not to",
      P.same_folder(P.remembered_folder(), storeA) and rD["remembered"] is False and rD["folder"] == scratch_store)

# a new folder takes the old one's place, and the rest of the settings file is kept
data = json.load(open(TEST_SETTINGS, encoding="utf-8"))
data["something_else"] = [1, 2, 3]
json.dump(data, open(TEST_SETTINGS, "w", encoding="utf-8"))
storeB = os.path.join(work, "Other Drive", "PRISM")
rE, sE, lE = one_month(os.path.join(work, "projB", "e.nc"), folder=storeB)
data = json.load(open(TEST_SETTINGS, encoding="utf-8"))
check("folder: a new folder is remembered in place of the old one, and the files are not moved",
      P.same_folder(data.get("download_folder"), storeB) and len(sE.fetched) == 1
      and os.path.isfile(P.kept_path(storeA, "an", P.RES_4KM, "ppt", "201501")), data)
check("folder: whatever else the settings file holds is kept", data.get("something_else") == [1, 2, 3], data)

# the remembered folder has been deleted, and the folder that held it is still there: made again
shutil.rmtree(storeB)
check("folder: a deleted folder is made again in the same place",
      P.choose_folder(None) == (os.path.normpath(storeB), "remade", None) and P.folder_to_offer() is None,
      P.choose_folder(None))
rF, sF, lF = one_month(os.path.join(work, "projC", "f.nc"))
check("folder: the run makes it again, downloads afresh and warns",
      rF["folder"] == os.path.normpath(storeB) and rF["folder_how"] == "remade" and len(sF.fetched) == 1
      and os.path.isdir(storeB) and rF["remembered"] is True
      and any("no longer there" in s and "made again in the same place" in s for s in lF.warned), lF.warned)

# the remembered folder cannot be found at all (a drive that is not connected): the default, with a warning
gone = os.path.join(work, "unplugged_drive", "Climate", "PRISM")
check("folder: remembering a folder that is not there yet", P.remember_folder(gone) is None)
check("folder: a folder that cannot be found leaves the default one",
      P.choose_folder(None) == (TEST_DEFAULT, "lost", os.path.normpath(gone)), P.choose_folder(None))
rG, sG, lG = one_month(os.path.join(work, "projC", "g.nc"))
check("folder: the run warns, names both folders and uses the default",
      rG["folder"] == TEST_DEFAULT and rG["folder_how"] == "lost" and rG["remembered"] is False
      and any("cannot be found" in s and os.path.normpath(gone) in s and TEST_DEFAULT in s
              and "drive that is not connected" in s for s in lG.warned), lG.warned)
check("folder: the folder that was not found is still the one remembered",
      P.same_folder(P.remembered_folder(), gone) and not os.path.exists(gone))
os.makedirs(gone)
check("folder: and is used again when it comes back", P.choose_folder(None) == (os.path.normpath(gone), "remembered", None))

# a settings file that cannot be read is as good as none, and is put right by the next folder given
open(TEST_SETTINGS, "w", encoding="utf-8").write("{ this is not JSON")
check("folder: a damaged settings file means nothing is remembered",
      P.remembered_folder() is None and P.choose_folder(None)[1] == "default")
open(TEST_SETTINGS, "w", encoding="utf-8").write('["a list, not a dictionary"]')
check("folder: nor is a settings file of the wrong shape", P.remembered_folder() is None)
check("folder: the next folder given puts the file right",
      P.remember_folder(storeA) is None and P.same_folder(P.remembered_folder(), storeA))

# a settings file that cannot be written: the run goes on and says so
P.settings_path = lambda: os.path.join(out_nc, "under_a_file", "prism_settings.json")     # out_nc is a file
rH, sH, lH = one_month(os.path.join(work, "projC", "h.nc"), folder=storeB)
check("folder: when the folder cannot be remembered the run still finishes, and says so",
      os.path.isfile(rH["path"]) and rH["remembered"] is False
      and any("could not be remembered" in s for s in lH.warned), lH.warned)
P.settings_path = lambda: TEST_SETTINGS

# folders that cannot hold the files
check("folder: a geodatabase is refused", "geodatabase" in (P.check_folder(os.path.join(work, "x.gdb")) or "")
      and "geodatabase" in (P.check_folder(os.path.join(work, "X.GDB", "feature_dataset") + "\\") or ""))
check("folder: memory is refused", "memory" in (P.check_folder("memory\\prism") or "")
      and "memory" in (P.check_folder("in_memory") or ""))
check("folder: a folder merely named like a geodatabase is accepted",
      P.check_folder(os.path.join(work, "my.gdb_exports", "PRISM")) is None
      and P.check_folder(os.path.join(work, "memory", "PRISM")) is None)
check("folder: a file is refused", "is a file" in (P.check_folder(out_nc) or ""))
raises("folder: the run refuses a geodatabase too", P.PrismError,
       lambda: one_month(os.path.join(work, "projC", "i.nc"), folder=os.path.join(work, "x.gdb")), has="geodatabase")
check("folder: and remembers nothing from the refusal", P.same_folder(P.remembered_folder(), storeA))


# ---- cancelling ------------------------------------------------------------------------------------------
class CancelAfter(object):
    def __init__(self, n):
        self.n, self.calls = n, 0

    def __call__(self):
        self.calls += 1
        return self.calls > self.n


out10 = os.path.join(work, "run10", "prism.nc")
os.makedirs(os.path.dirname(out10))
svc10 = FakeService()
kw = Log().kw()
raises("cancelled part way", P.Cancelled, lambda: build_beside(
    out10, BOX, variables=["ppt"], time_step=P.STEP_MONTHLY, year_from=2018, year_to=2018, service=svc10,
    cancelled=CancelAfter(4), **kw))
check("cancelled: no file, no part file, the fetched grids kept",
      not os.path.exists(out10) and not os.path.exists(out10 + ".part") and 1 <= len(svc10.fetched) < 12
      and os.path.isdir(os.path.join(work, "run10", "PRISM_downloads")), len(svc10.fetched))

# ---- the engine's own download code, against a web server on this computer ------------------------------------
served = {"hits": 0}
good_zip = os.path.join(work, "served.zip")
write_zip(good_zip, P.RES_4KM, "ppt", "199001")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        served["hits"] += 1
        served["agent"] = self.headers.get("User-Agent")
        if "releaseDate" in self.path:
            body = (b'[["1990-01-01", "2024-12-18", "ppt", "7", "u"], ["1990-02-01", "2024-12-19", "ppt", "7", "u"]]'
                    if "199002" in self.path else b'["1990-01-01", "2024-12-18", "ppt", "7", "u"]'
                    if "199001" in self.path else b"There are no release dates for Annual datasets.")
        elif "/202712" in self.path:
            body = b"Invalid date: 202712\nValid year ranges are 1895 to 2026\n"
        elif "/rain/" in self.path:
            body = b"Invalid element: rain"
        else:
            body = open(good_zip, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=UTF-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
real_service = P.SERVICE
P.SERVICE = "http://127.0.0.1:%d/prism/data/get/" % server.server_address[1]
try:
    s = P.Service(pause=0.3)
    dest = os.path.join(work, "dl", "a", "prism_ppt_us_25m_199001.zip")
    t0 = time.time()
    size = s.fetch("an", P.RES_4KM, "ppt", "199001", dest)
    check("download: the zip arrives whole, and nothing unfinished is left beside it",
          size == os.path.getsize(good_zip) and os.path.getsize(dest) == size
          and os.listdir(os.path.dirname(dest)) == ["prism_ppt_us_25m_199001.zip"], os.listdir(os.path.dirname(dest)))
    # A folder shared by several people. Each download is written under a name of its own, so two people
    # fetching the same grid at once never write into one file; an unfinished download more than a day old
    # is swept away, and a younger one (somebody's download in progress) is left alone.
    names = []
    real_open = open


    def spy_open(path, mode="r", *a, **k):
        if "w" in mode and str(path).endswith(".part"):
            names.append(os.path.basename(str(path)))
        return real_open(path, mode, *a, **k)


    P.open = spy_open
    try:
        shared = os.path.join(work, "dl", "shared")
        os.makedirs(shared)
        old_part = os.path.join(shared, "prism_ppt_us_25m_198001.zip.0a1b2c3d.part")
        young_part = os.path.join(shared, "prism_ppt_us_25m_198002.zip.4e5f6a7b.part")
        older_style = os.path.join(shared, "prism_ppt_us_25m_198003.zip.part")     # as versions to 1.121.75 wrote
        not_ours = os.path.join(shared, "somebody_elses_download.part")
        for p_ in (old_part, young_part, older_style, not_ours):
            with real_open(p_, "wb") as f:
                f.write(b"half a file")
        for p_ in (old_part, older_style, not_ours):
            os.utime(p_, (time.time() - 3 * 86400, time.time() - 3 * 86400))
        d1 = os.path.join(shared, "prism_ppt_us_25m_199001.zip")
        s.fetch("an", P.RES_4KM, "ppt", "199001", d1)
        s.fetch("an", P.RES_4KM, "ppt", "199001", d1)          # the same grid again, over the first
    finally:
        del P.open
    check("shared folder: each download is written under a name of its own",
          len(names) == 2 and names[0] != names[1]
          and all(n.startswith("prism_ppt_us_25m_199001.zip.") and n.endswith(".part") for n in names), names)
    check("shared folder: the grid is whole and no unfinished file of ours is left",
          os.path.getsize(d1) == os.path.getsize(good_zip)
          and sorted(os.listdir(shared)) == sorted([os.path.basename(d1), os.path.basename(young_part),
                                                    os.path.basename(not_ours)]),
          os.listdir(shared))
    check("shared folder: the tool's unfinished downloads three days old are swept away, a new one is left alone",
          not os.path.exists(old_part) and not os.path.exists(older_style) and os.path.exists(young_part))
    check("shared folder: an old file that is not the tool's own is never touched", os.path.exists(not_ours))
    # the grid is open in another program (somebody's run is reading it) when ours finishes: theirs is kept
    with real_open(d1, "rb") as held:
        size2 = s.fetch("an", P.RES_4KM, "ppt", "199001", d1)
        check("shared folder: a grid that is in use is left in place, and ours is discarded quietly",
              size2 == os.path.getsize(good_zip) and os.path.getsize(d1) == os.path.getsize(good_zip)
              and not [n for n in os.listdir(shared) if n.endswith(".part")
                       and n not in (os.path.basename(young_part), os.path.basename(not_ours))],
              os.listdir(shared))
    check("download: says who is asking, and nothing personal", served.get("agent") == P.USER_AGENT, served.get("agent"))
    raises("download: a date PRISM has not issued", P.NotIssued,
           lambda: s.fetch("an", P.RES_4KM, "ppt", "202712", os.path.join(work, "dl", "b.zip")), "Invalid date")
    check("download: the pause between requests is kept", time.time() - t0 >= 0.3, time.time() - t0)
    raises("download: any other answer is shown as PRISM wrote it", P.PrismError,
           lambda: s.fetch("an", P.RES_4KM, "rain", "202001", os.path.join(work, "dl", "c.zip")), "Invalid element: rain")
    check("download: a refusal leaves no file", not os.path.exists(os.path.join(work, "dl", "b.zip"))
          and not os.path.exists(os.path.join(work, "dl", "c.zip"))
          and not [n for n in os.listdir(os.path.join(work, "dl")) if n.endswith(".part")],
          os.listdir(os.path.join(work, "dl")))
    check("download: the LT address ends in /lt", s.grid_url("lt", P.RES_800M, "tmax", "200506").endswith("/us/800m/tmax/200506/lt")
          and s.grid_url("an", P.RES_4KM, "ppt", "199504").endswith("/us/4km/ppt/199504"))
    d = s.release_dates(P.RES_4KM, "ppt", "199001", "199002")
    check("release dates: a range", d == {"199001": datetime.date(2024, 12, 18), "199002": datetime.date(2024, 12, 19)}, d)
    check("release dates: one grid", s.release_dates(P.RES_4KM, "ppt", "199001", "199001") == {"199001": datetime.date(2024, 12, 18)})
    check("release dates: no answer is no dates", s.release_dates(P.RES_4KM, "ppt", "1990", "1990") == {})
finally:
    P.SERVICE = real_service
    server.shutdown()

# ---- a folder shared by several people: a grid that arrives while the run is under way ------------------
class SharingService(FakeService):
    """As FakeService; and while it fetches February, 'another person' puts March's grid in the folder."""

    def __init__(self, folder):
        FakeService.__init__(self)
        self.folder = folder

    def fetch(self, set_code, resolution, code, token, dest, cancelled=None):
        size = FakeService.fetch(self, set_code, resolution, code, token, dest, cancelled)
        if code == "ppt" and token == "201902":
            write_zip(P.kept_path(self.folder, set_code, resolution, code, "201903"), resolution, code, "201903")
        return size


share_folder = os.path.join(work, "office", "PRISM_downloads")
out_s = os.path.join(work, "office", "mine.nc")
os.makedirs(os.path.dirname(out_s))
svc_s = SharingService(share_folder)
log_s = Log()
r_s = P.build(out_s, BOX, variables=["ppt"], time_step=P.STEP_MONTHLY, year_from=2019, year_to=2019,
              months=[1, 2, 3, 4], service=svc_s, download_folder=share_folder, remember=False, **log_s.kw())
check("shared folder: a grid another run fetched meanwhile is not asked for again",
      [f[3] for f in svc_s.fetched] == ["201901", "201902", "201904"] and r_s["downloaded"] == 3
      and r_s["arrived"] == 1 and r_s["reused"] == 1 and r_s["periods"] == 4,
      ([f[3] for f in svc_s.fetched], r_s["downloaded"], r_s["arrived"], r_s["reused"]))
check("shared folder: and the run says so", any("One grid arrived in the folder" in s_ and "another run" in s_
                                               for s_ in log_s.said), log_s.said[-4:])
ds_s = netCDF4.Dataset(out_s)
w_s = r_s["window"]
march = value_grid(P.RES_4KM, "ppt", "201903")[w_s.row0:w_s.row0 + w_s.rows, w_s.col0:w_s.col0 + w_s.cols]
check("shared folder: its values are in the file", np.array_equal(ds_s.variables["ppt"][2].filled(-9999.0), march))
ds_s.close()


class RefreshingService(FakeService):
    """As FakeService; and while it fetches July afresh, 'another person' fetches August afresh."""

    def __init__(self, folder, **kw):
        FakeService.__init__(self, **kw)
        self.folder = folder

    def fetch(self, set_code, resolution, code, token, dest, cancelled=None):
        size = FakeService.fetch(self, set_code, resolution, code, token, dest, cancelled)
        if token == "202607":
            write_zip(P.kept_path(self.folder, set_code, resolution, code, "202608"), resolution, code, "202608")
        return size


out_r = os.path.join(work, "office", "theirs.nc")
P.build(out_r, BOX, variables=["ppt"], time_step=P.STEP_MONTHLY, year_from=2026, year_to=2026, months=[7, 8],
        service=FakeService(), download_folder=share_folder, remember=False, **Log().kw())
for tok in ("202607", "202608"):
    os.utime(P.kept_path(share_folder, "an", P.RES_4KM, "ppt", tok), (old, old))
both = {("ppt", "202607"): datetime.date(2026, 11, 3), ("ppt", "202608"): datetime.date(2026, 11, 3)}
svc_r = RefreshingService(share_folder, releases=both)
r_r = P.build(out_r, BOX, variables=["ppt"], time_step=P.STEP_MONTHLY, year_from=2026, year_to=2026, months=[7, 8],
              service=svc_r, download_folder=share_folder, remember=False, **Log().kw())
# a grid PRISM has reissued, fetched afresh over a kept one by a run that does not keep its downloads: it stays
for tok in ("202607", "202608"):
    os.utime(P.kept_path(share_folder, "an", P.RES_4KM, "ppt", tok), (old, old))
svc_k = FakeService(releases={("ppt", "202608"): datetime.date(2026, 11, 3)})
r_k = P.build(out_r, BOX, variables=["ppt"], time_step=P.STEP_MONTHLY, year_from=2026, year_to=2026, months=[7, 8],
              service=svc_k, download_folder=share_folder, remember=False, keep_downloads=False, **Log().kw())
aug = P.kept_path(share_folder, "an", P.RES_4KM, "ppt", "202608")
check("not kept: a reissued grid fetched afresh over a kept one is not deleted, nor is any other kept grid",
      [f[3] for f in svc_k.fetched] == ["202608"] and r_k["refreshed"] == 1 and r_k["deleted"] == 0
      and os.path.isfile(aug) and os.path.getmtime(aug) > old + 86400
      and os.path.isfile(P.kept_path(share_folder, "an", P.RES_4KM, "ppt", "202607"))
      and os.path.isfile(P.kept_path(share_folder, "an", P.RES_4KM, "ppt", "201901")),
      ([f[3] for f in svc_k.fetched], r_k["refreshed"], r_k["deleted"], os.path.isfile(aug)))
check("shared folder: a reissued grid another run fetched afresh meanwhile is not asked for again",
      [f[3] for f in svc_r.fetched] == ["202607"] and r_r["refreshed"] == 1 and r_r["arrived"] == 1,
      ([f[3] for f in svc_r.fetched], r_r["refreshed"], r_r["arrived"]))
check("the test for an arrived grid never raises", P._arrived(os.path.join(work, "office", "no", "such.zip"), True, 1.0)
      is False and P._arrived(os.path.join(work, "office", "no", "such.zip"), False, None) is False)

# ---- in ArcGIS Pro ---------------------------------------------------------------------------------------
import arcpy  # noqa: E402

arcpy.env.overwriteOutput = True
r_pro = arcpy.Raster(out_nc, True)
check("Pro: a multidimensional raster", r_pro.isMultidimensional)
names = sorted(v.split("(")[0] for v in r_pro.variables)
check("Pro: the climate variables are its variables", "ppt" in names and "tmean" in names, r_pro.variables)
check("Pro: NAD 83", r_pro.spatialReference.factoryCode == 4269, (r_pro.spatialReference.name, r_pro.spatialReference.factoryCode))
e = r_pro.extent
check("Pro: the extent is the window", abs(e.XMin - win.west) < 1e-7 and abs(e.XMax - win.east) < 1e-7
      and abs(e.YMin - win.south) < 1e-7 and abs(e.YMax - win.north) < 1e-7, (e.XMin, e.YMin, e.XMax, e.YMax))
check("Pro: the cell size", abs(r_pro.meanCellWidth - win.cell) < 1e-9 and abs(r_pro.meanCellHeight - win.cell) < 1e-9)
dates = r_pro.getDimensionValues("ppt", "StdTime")
# Pro reads each time step as a single date, because time does not name time_bnds as its bounds
check("Pro: the dates, each a single date", len(dates) == 6 and not isinstance(dates[0], (tuple, list))
      and str(dates[0]).startswith("2019-01-01") and str(dates[-1]).startswith("2020-07-01"), dates)
check("Pro: units", r_pro.getVariableAttributes("ppt").get("Unit") == "mm", r_pro.getVariableAttributes("ppt"))
lyr = arcpy.md.MakeMultidimensionalRasterLayer(out_nc, "prism_tmean_test", ["tmean"])[0]
one = os.path.join(work, "slice.tif")
arcpy.management.CopyRaster(lyr, one)
a = arcpy.RasterToNumPyArray(one, nodata_to_value=-9999.0)
src = value_grid(P.RES_4KM, "tmean", "201901")[win.row0:win.row0 + win.rows, win.col0:win.col0 + win.cols]
check("Pro: a layer of one variable shows PRISM's cells where they belong", a.shape == src.shape and np.allclose(a, src, atol=1e-4))

# What a LAYER of the file is given, read from a layer file: the time step Pro works out, whether each step
# is a single date, and the stretch. Pro's time slider takes its step from the layer and moves the layer only
# when the map's time equals one of its time steps, so the step must be a whole month, day or year and each
# time step a single date. The stretch must hold every time step, not only the first.
import json  # noqa: E402


def layer_facts(nc, code, name):
    arcpy.md.MakeMultidimensionalRasterLayer(nc, name, [code])
    lf = os.path.join(work, name + ".lyrx")
    arcpy.management.SaveToLayerFile(name, lf)
    with open(lf, encoding="utf-8") as f:
        ld = json.load(f)["layerDefinitions"][0]
    arcpy.management.Delete(name)
    tdd = ld.get("timeDisplayDefinition") or {}
    tv = (ld.get("activeSlice") or {}).get("timeValue") or {}
    st = (ld.get("colorizer") or {}).get("stretchStats") or {}
    return (tdd.get("timeInterval"), tdd.get("timeIntervalUnits")), tv.get("start") == tv.get("end"), \
        (st.get("min", 0), st.get("max", 0))


for label, kw, step_wanted in (
        ("monthly", dict(time_step=P.STEP_MONTHLY, year_from=2019, year_to=2019), (1, "esriTimeUnitsMonths")),
        ("annual", dict(time_step=P.STEP_ANNUAL, year_from=2001, year_to=2006), (1, "esriTimeUnitsYears")),
        ("daily", dict(time_step=P.STEP_DAILY, date_from="2020-02-25", date_to="2020-03-05"), (1, "esriTimeUnitsDays"))):
    nc_l = os.path.join(work, "layer_" + label, "p.nc")
    os.makedirs(os.path.dirname(nc_l))
    r_l = build_beside(nc_l, BOX, variables=["ppt", "tmax"], service=FakeService(), today=TODAY, **kw)
    for code in ("ppt", "tmax"):
        step_got, one_date, stretch = layer_facts(nc_l, code, "prism_layer_%s_%s" % (label, code))
        check("Pro layer, %s %s: a step of one %s" % (label, code, step_wanted[1][13:-1].lower()),
              step_got == step_wanted, step_got)
        check("Pro layer, %s %s: each time step a single date" % (label, code), one_date)
        rr = r_l["ranges"][code]
        check("Pro layer, %s %s: the stretch holds every time step" % (label, code),
              abs(stretch[0] - rr["min"]) < 1e-3 and abs(stretch[1] - rr["max"]) < 1e-3, (stretch, rr))

# the true area of a cell, as ArcGIS measures it (its north and south edges follow the parallels)
row = win.rows // 2
south, north = lat_e[row + 1], lat_e[row]
n = 200
pts = ([arcpy.Point(win.west + win.cell * i / n, south) for i in range(n + 1)]
       + [arcpy.Point(win.west + win.cell * (n - i) / n, north) for i in range(n + 1)])
poly = arcpy.Polygon(arcpy.Array(pts), arcpy.SpatialReference(4269))
esri = poly.getArea("GEODESIC", "SQUAREKILOMETERS")
check("cell area: equals ArcGIS's geodesic area", abs(esri - mine[row]) / mine[row] < 1e-6, (esri, mine[row]))

# the study area's rectangle
utm = arcpy.SpatialReference(26912)
ext = arcpy.Extent(430000, 3880000, 470000, 3920000, spatial_reference=utm)
box = P.study_area_box(extent=ext)
corner = arcpy.PointGeometry(arcpy.Point(430000, 3880000), utm).projectAs(arcpy.SpatialReference(4269)).firstPoint
check("study area: a UTM rectangle in degrees on NAD 83", box[0] <= corner.X + 1e-6 and abs(box[0] - corner.X) < 0.02
      and abs(box[1] - corner.Y) < 0.02 and box[2] > box[0] and box[3] > box[1], (box, corner.X, corner.Y))
gdb = arcpy.management.CreateFileGDB(work, "t.gdb").getOutput(0)
fc = arcpy.management.CreateFeatureclass(gdb, "pts", "POINT", spatial_reference=arcpy.SpatialReference(4326)).getOutput(0)
with arcpy.da.InsertCursor(fc, ["SHAPE@XY"]) as cur:
    cur.insertRow([(-111.65, 35.20)])
    cur.insertRow([(-111.05, 35.85)])
box = P.study_area_box(layer=fc)
check("study area: points", abs(box[0] + 111.65) < 1e-4 and abs(box[3] - 35.85) < 1e-4 and abs(box[2] + 111.05) < 1e-4, box)
lines = arcpy.management.CreateFeatureclass(gdb, "lines", "POLYLINE", spatial_reference=utm).getOutput(0)
with arcpy.da.InsertCursor(lines, ["SHAPE@"]) as cur:
    cur.insertRow([arcpy.Polyline(arcpy.Array([arcpy.Point(430000, 3880000), arcpy.Point(470000, 3920000)]), utm)])
box = P.study_area_box(layer=lines)
check("study area: lines", abs(box[0] - corner.X) < 1e-6 and abs(box[1] - corner.Y) < 1e-6, box)
raises("study area: an empty layer", P.PrismError, lambda: P.study_area_box(
    layer=arcpy.management.CreateFeatureclass(gdb, "none", "POLYGON", spatial_reference=utm).getOutput(0)), "no features")
raises("study area: none given", P.PrismError, lambda: P.study_area_box())


class Messages(object):
    def __init__(self):
        self.lines, self.warnings = [], []

    def addMessage(self, t):
        self.lines.append(t)

    def addWarningMessage(self, t):
        self.warnings.append(t)


m = Messages()
out11 = os.path.join(work, "run1", "from_run.nc")
r11 = P.run(out11, study_area=fc, variables=["ppt"], time_step=P.STEP_MONTHLY, year_from=2019, year_to=2019,
            months=[1, 6, 7], service=FakeService(), today=TODAY, messages=m, area_words="the features of pts",
            download_folder=os.path.join(work, "run1", "PRISM_downloads"))
check("run in Pro: written from the kept files", os.path.isfile(out11) and r11["downloaded"] == 0 and r11["periods"] == 3)
check("run in Pro: the folder it was given is remembered, and it says so",
      P.same_folder(P.remembered_folder(), os.path.join(work, "run1", "PRISM_downloads")) and r11["remembered"]
      and any("in this project or any other: the tool remembers the folder" in s for s in m.lines), m.lines)
check("run in Pro: says how to cite and how to see the file",
      any("accessed 21 Nov 2026" in s for s in m.lines) and any("choose Multidimensional Data. " in s for s in m.lines)
      and any("very long-term trends" in s for s in m.lines), m.lines)
check("run in Pro: metadata written without complaint", not any("metadata" in s for s in m.warnings), m.warnings)

# the same run with the downloads not kept: it needs only grids that are already there, and deletes none
m2 = Messages()
run1_store = os.path.join(work, "run1", "PRISM_downloads")
ppt_dir = os.path.dirname(P.kept_path(run1_store, "an", P.RES_4KM, "ppt", "201901"))
there = sorted(os.listdir(ppt_dir))
r12 = P.run(os.path.join(work, "run1", "from_run_2.nc"), study_area=fc, variables=["ppt"],
            time_step=P.STEP_MONTHLY, year_from=2019, year_to=2019, months=[1, 6, 7], service=FakeService(),
            today=TODAY, messages=m2, area_words="the features of pts", download_folder=run1_store,
            keep_downloads=False)
check("run in Pro, downloads not kept: the grids that were already there are all still there",
      r12["downloaded"] == 0 and r12["deleted"] == 0 and sorted(os.listdir(ppt_dir)) == there and len(there) >= 3,
      (r12["downloaded"], r12["deleted"], os.listdir(ppt_dir)))
check("run in Pro, downloads not kept: it says that it deleted nothing, and how to delete the saved files",
      any(s.startswith("This run deleted no files.") and s.endswith("To delete them, use Windows File Explorer.")
          for s in m2.lines) and not any("are kept in" in s for s in m2.lines), m2.lines)

check("settings: the tests left the user's real settings file and default folder alone",
      _state(REAL_SETTINGS) == REAL_BEFORE, (REAL_BEFORE, _state(REAL_SETTINGS)))

# ---- a download that is cut off on its way (against the stand-in service of stand_in_service.py) ------------
# Python's web client gives no error when a connection closes before all the bytes a server promised have come:
# reading simply ends. So a grid's zip file that arrived half is given the grid's own name and kept, in a
# folder that may be shared, where every later run finds it and stops. And PRISM allows a file only twice a day.
import io as _io  # noqa: E402

sys.path.insert(0, HERE)
from stand_in_service import StandIn, no_internet, pointed  # noqa: E402

_guard = no_internet()
_buf = _io.BytesIO()
with zipfile.ZipFile(_buf, "w", zipfile.ZIP_STORED) as _z:
    _z.writestr("prism_ppt_us_25m_201901.tif", os.urandom(200000))
_whole = _buf.getvalue()
_dest = os.path.join(work, "cut_off", "prism_ppt_us_25m_201901.zip")
with StandIn() as _web:
    _web.fail(None, "cut", body=_whole)
    with pointed(P, SERVICE=_web.url):
        try:
            _got = P.Service(pause=0.0).fetch("an", list(P.GRIDS)[0], "ppt", "201901", _dest)
        except P.PrismError as e:
            _got = e
    _asked = len(_web.requests)
    _left = os.listdir(os.path.dirname(_dest)) if os.path.isdir(os.path.dirname(_dest)) else []
    # cut off once, whole the second time
    _web.forget()
    _web.fail(None, "cut", body=_whole, times=1)
    _web.answer(None, body=_whole)
    _dest2 = os.path.join(work, "cut_once", "prism_ppt_us_25m_201901.zip")
    with pointed(P, SERVICE=_web.url):
        _size2 = P.Service(pause=0.0).fetch("an", list(P.GRIDS)[0], "ppt", "201901", _dest2)
    _asked2 = len(_web.requests)
    # sent in pieces with no length promised, and cut off
    _web.forget()
    _web.fail(None, "cut stream", body=_whole)
    _dest3 = os.path.join(work, "cut_stream", "prism_ppt_us_25m_201901.zip")
    with pointed(P, SERVICE=_web.url):
        try:
            _got3 = P.Service(pause=0.0).fetch("an", list(P.GRIDS)[0], "ppt", "201901", _dest3)
        except P.PrismError as e:
            _got3 = e
check("a download cut off on its way: the stand-in was asked, and nothing reached another computer",
      _asked >= 1 and _guard.attempts == [], (_asked, _guard.attempts))
_guard.stop()
check("a download cut off on its way is not kept as if it were whole: an error that says how much came",
      isinstance(_got, P.PrismError) and "stopped sending" in str(_got) and "part way: 100," in str(_got) and not os.path.isfile(_dest),
      "%r; %s of %s bytes were kept under the grid's own name" % (_got, os.path.getsize(_dest) if os.path.isfile(_dest) else 0, len(_whole)))
check("a download cut off on its way: asked for twice and no more (PRISM allows a file twice in a day), and no working file is left",
      _asked == 2 and _left == [], (_asked, _left))
check("a download cut off once and whole the second time: the whole file is kept",
      _asked2 == 2 and _size2 == len(_whole) and open(_dest2, "rb").read() == _whole, (_asked2, _size2))
check("a download sent in pieces with no length promised, and cut off: an error, and nothing kept",
      isinstance(_got3, P.PrismError) and not os.path.isfile(_dest3), repr(_got3))

del r_pro, lyr
print("\n%d passed, %d failed" % (passed, failed))
try:
    shutil.rmtree(work)
except OSError:
    pass
sys.exit(1 if failed else 0)
