# Wildlife and Forestry Tools for ArcGIS Pro
# Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version, together with the additional terms
# in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
# Memorial Award notice, marking of modified versions, use of names,
# and an additional permission to link with ArcGIS Pro).
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""PRISM climate data for a study area, as one netCDF file.

PRISM (the PRISM Group, Oregon State University) maps precipitation,
temperature, dew point, vapor pressure deficit and solar radiation
across the conterminous United States, by day since 1981 and by month
and year since 1895, on grids of about 4 km and about 800 m.

What PRISM's web service gives, and what this engine does with it:

  * ONE grid per request, always the whole conterminous United States,
    as a zip file that holds a GeoTIFF. There is no request by
    rectangle. A grid is a variable on a date: forty years of monthly
    precipitation and mean temperature are 960 requests.
  * The engine asks for each grid once, keeps the zip file in a folder,
    reads only the cells of the study area out of it, and writes them
    all to ONE netCDF file with a real time dimension and one variable
    for each climate variable. A later run finds the kept files and
    downloads nothing it already has.
  * The folder of kept files belongs to the Windows user, not to a
    project: the folder a run is given is written to
    %LOCALAPPDATA%\\JennessEnterprises\\WildlifeTools\\prism_settings.json
    and every later run, in any project, looks there. With none given
    and none remembered the files go to PRISM_downloads in the user's
    own folder. See ``choose_folder``.
  * PRISM asks for restraint: "if a file is downloaded twice in a
    24-hour period, no more downloads of that file will be allowed
    during that period", and its sample script waits 2 seconds between
    requests. The engine waits 2 seconds too, and never asks for a
    grid more than twice in a run.
  * PRISM reissues a grid several times in its first six months (eight
    times for a daily grid, seven for a monthly one) as more station
    reports arrive. A kept file that was fetched inside that period is
    checked against PRISM's release-date service and fetched again
    when PRISM has reissued it.

The netCDF file is laid out for the analysis that follows it:

  * the cells are PRISM's own, never resampled, with one extra cell
    (or more) all round the study area;
  * ``lat_bnds`` and ``lon_bnds`` hold every cell's edges and
    ``cell_area`` its true area (the cells are squares of latitude
    and longitude, so they narrow northward). The analysis planned
    for these files (Jeff, 22 November 2026) summarizes a polygon by
    the cells whose CENTERS lie inside it, each weighted by its true
    area, and by the cells the polygon intersects when it holds no
    center. Weighting by the share of a cell that a polygon covers
    is a possible later addition: the edges are there for it;
  * the data are chunked in blocks of dates and of cells, so that a
    map of one date and the whole series of one cell are both quick.

Only the Python standard library, numpy, GDAL and netCDF4 are used
here; arcpy is touched only by ``run`` (the study area, the progress
bar, the metadata). All of them ship with ArcGIS Pro.
"""

import datetime
import http.client
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

import gbif_occurrences as G

SERVICE = "https://services.nacse.org/prism/data/get/"
SITE = "https://prism.oregonstate.edu"
TERMS = "https://prism.oregonstate.edu/terms/"
DATASETS_DOC = "https://prism.oregonstate.edu/documents/PRISM_datasets.pdf"
USER_AGENT = "WildlifeTools-AddIn (Jenness Enterprises; ArcGIS Pro)"
PAUSE = 2.0                          # seconds between requests: what PRISM's own sample script waits
FILL = -9999.0                       # PRISM's own no-data value
STABLE_AFTER_DAYS = 200              # PRISM calls a grid stable after six months; a little more, to be sure
DEFAULT_MAX_GB = 5.0
FOLDER_NAME = "PRISM_downloads"
SETTINGS_NAME = "prism_settings.json"

n0 = G.n0
span = G.span
size_words = G.size_words
Cancelled = G.Cancelled


class PrismError(Exception):
    """A failure the user can act on; shown without a traceback."""


class NotIssued(Exception):
    """PRISM has no grid for this date (yet)."""


# ---------------------------------------------------------------------------
#  what can be asked for
# ---------------------------------------------------------------------------
STEP_MONTHLY, STEP_DAILY, STEP_ANNUAL = "Monthly", "Daily", "Annual"
TIME_STEPS = [STEP_MONTHLY, STEP_DAILY, STEP_ANNUAL]
RES_4KM, RES_800M = "4 km", "800 m"
RESOLUTIONS = [RES_4KM, RES_800M]
SET_AN, SET_LT = "All networks (AN)", "Long-term networks (LT)"
DATASETS = [SET_AN, SET_LT]
SET_CODES = {SET_AN: "an", SET_LT: "lt"}

# PRISM's grids: squares of latitude and longitude on NAD 83. The 800 m cells nest five by five in the 4 km
# cells. ``per_degree`` cells make one degree; ``tag`` is the resolution as PRISM's file names spell it.
WEST = -(125.0 + 1.0 / 48.0)
NORTH = 49.9375
GRIDS = {
    RES_4KM: {"path": "4km", "tag": "25m", "per_degree": 24, "cols": 1405, "rows": 621, "words": "about 4 km"},
    RES_800M: {"path": "800m", "tag": "30s", "per_degree": 120, "cols": 7025, "rows": 3105, "words": "about 800 m"},
}
NAD83_WKT = ('GEOGCS["NAD83",DATUM["North_American_Datum_1983",SPHEROID["GRS 1980",6378137,298.257222101,'
             'AUTHORITY["EPSG","7019"]],AUTHORITY["EPSG","6269"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
             'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AXIS["Latitude",NORTH],'
             'AXIS["Longitude",EAST],AUTHORITY["EPSG","4269"]]')
GRS80_A = 6378137.0
GRS80_INV_F = 298.257222101

FIRST_YEAR = 1895                    # monthly and annual grids begin in January 1895
FIRST_DAY = datetime.date(1981, 1, 1)   # daily grids begin on 1 January 1981


class Variable(object):
    """One PRISM element. ``label`` is the name in the tool's list; ``what`` describes a daily value in
    PRISM's own terms; ``total`` is True where a month or year holds the sum of its days (precipitation)
    and False where it holds their average."""

    def __init__(self, code, label, long_name, units, what, within="mean", total=False, solar=False):
        self.code = code
        self.within = within                 # how a day's value is made from the day: sum, mean, minimum, maximum
        self.label = label
        self.long_name = long_name
        self.units = units
        self.what = what
        self.total = total
        self.solar = solar


VARIABLES = [
    Variable("ppt", "Precipitation (ppt)", "precipitation", "mm",
             "total precipitation, rain and melted snow", within="sum", total=True),
    Variable("tmin", "Minimum temperature (tmin)", "minimum temperature", "degC", "minimum temperature of the day",
             within="minimum"),
    Variable("tmean", "Mean temperature (tmean)", "mean temperature", "degC",
             "mean temperature of the day, which PRISM calculates as the average of the maximum and the minimum"),
    Variable("tmax", "Maximum temperature (tmax)", "maximum temperature", "degC", "maximum temperature of the day",
             within="maximum"),
    Variable("tdmean", "Mean dew point temperature (tdmean)", "mean dew point temperature", "degC",
             "mean dew point temperature of the day"),
    Variable("vpdmin", "Minimum vapor pressure deficit (vpdmin)", "minimum vapor pressure deficit", "hPa",
             "minimum vapor pressure deficit of the day", within="minimum"),
    Variable("vpdmax", "Maximum vapor pressure deficit (vpdmax)", "maximum vapor pressure deficit", "hPa",
             "maximum vapor pressure deficit of the day", within="maximum"),
    Variable("soltotal", "Solar radiation on a horizontal surface (soltotal)",
             "solar radiation on a horizontal surface", "MJ m-2 day-1",
             "total global shortwave solar radiation received in the day on a horizontal surface", within="sum",
             solar=True),
    Variable("solslope", "Solar radiation on the sloping ground (solslope)",
             "solar radiation on the sloping ground", "MJ m-2 day-1",
             "total global shortwave solar radiation received in the day on the ground as it slopes", within="sum",
             solar=True),
]
LABELS = [v.label for v in VARIABLES]
DEFAULT_LABELS = [VARIABLES[0].label, VARIABLES[2].label]
BY_LABEL = {v.label: v for v in VARIABLES}
BY_CODE = {v.code: v for v in VARIABLES}

CAUTION = (
    "A caution about change over time. PRISM's terms of use say: \"It is not recommended that PRISM data be used "
    "to calculate very long-term trends.\" Each grid is made from the weather stations that reported at the "
    "time, and the stations change: they open, close and move, their instruments and their hours of observation "
    "change, and whole networks join the record. From 2002 the precipitation grids of the central and eastern "
    "United States also draw on radar. A change in a cell's values over decades can therefore come from a change "
    "in the measurements and not from a change in the climate. PRISM's Long-term networks (LT) dataset uses only "
    "the networks that have stations with 20 or more years of data, to be more consistent over time, and PRISM "
    "asks for the same care with it. PRISM's advice is that a trend over decades be calculated carefully and, "
    "ideally, backed up with observed data.")


# The point comes first, and so does the note itself in a variable's comment: ArcGIS Pro shows a variable's
# note on ONE line in a layer's properties (Source tab, Multidimensional Info, Description) and cuts it off at
# the edge of the window, at about 130 letters (Jeff's capture, 22 November 2026).
ANNUAL_NOTE = (
    "A year's grid does not always equal its twelve monthly grids added up or averaged. These are PRISM's own "
    "annual grids: PRISM makes a year's grid from that year's monthly grids as they stood at the time, and it "
    "may reissue a monthly grid after that. Where the two must agree, fetch the monthly grids and combine "
    "them.")

TIME_NOTE = ("Each time step is named by its first day. The variable time_bnds holds the span of each step: its "
             "first day, and the day after its last day.")

PRISM_DAY_NOTE = ("A PRISM day runs from 1200 UTC to 1200 UTC and is named for the day it ends on: the grid of "
                  "1 January holds the 24 hours that end at 1200 UTC on 1 January.")

PROVISIONAL_NOTE = ("PRISM reissues a grid several times in its first six months, as more station reports "
                    "arrive, and calls it stable after that. A value of 1 marks a time step that PRISM may "
                    "since have reissued: run the tool again later to bring it up to date.")

# The note says what the variable is and no more (Jeff, 22 November 2026). It used to tell the reader to
# weight a cell by the share of it a polygon covers, a method no tool of ours carries out.
AREA_WHY = "The cells are squares of latitude and longitude, so they narrow northward."
AREA_NOTE = "This is each cell's true area. " + AREA_WHY


def variable_note(v, time_step, lead=True):
    """The note that goes with a climate variable (its ``comment`` in the file): what one value is. In a file
    of annual grids the note BEGINS with the caution about years and months when ``lead`` is true."""
    if time_step == STEP_DAILY:
        return "For each day: {}.".format(v.what)
    what = "For each {}: {} of the daily values, which are the {}.".format(
        "year" if time_step == STEP_ANNUAL else "month", "the sum" if v.total else "the average", v.what)
    return ANNUAL_NOTE + " " + what if (lead and time_step == STEP_ANNUAL) else what


def variables_from(labels):
    """The Variable objects for a list of labels or codes, in PRISM's order."""
    wanted = []
    for text in labels or []:
        t = str(text).strip().strip("'\"")
        if not t:
            continue
        v = BY_LABEL.get(t) or BY_CODE.get(t.lower())
        if v is None:
            raise PrismError("There is no PRISM variable named \"{}\". The variables: {}.".format(
                t, "; ".join(LABELS)))
        if v not in wanted:
            wanted.append(v)
    return [v for v in VARIABLES if v in wanted]


# ---------------------------------------------------------------------------
#  dates
# ---------------------------------------------------------------------------
class Period(object):
    """One time step: ``token`` is the date as PRISM's web addresses and file names spell it; ``start`` is
    its first day and ``end`` the day after its last."""

    def __init__(self, token, start, end):
        self.token = token
        self.start = start
        self.end = end

    def words(self):
        t = self.token
        if len(t) == 4:
            return t
        if len(t) == 6:
            return "{}-{}".format(t[:4], t[4:])
        return "{}-{}-{}".format(t[:4], t[4:6], t[6:])


def _as_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %I:%M:%S %p"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise PrismError("\"{}\" could not be read as a date. Write a date as 2020-06-30.".format(text))


def _months(months):
    out = sorted(set(int(m) for m in (months or []) if str(m).strip() != ""))
    for m in out:
        if not 1 <= m <= 12:
            raise PrismError("Months are numbers from 1 to 12.")
    return out or list(range(1, 13))


def plan_periods(time_step, year_from=None, year_to=None, months=None, date_from=None, date_to=None, today=None):
    """(periods, notes): the time steps to ask for, oldest first, and plain sentences about any that were
    asked for and left out because they are not complete yet."""
    today = today or datetime.date.today()
    notes = []
    periods = []
    if time_step not in TIME_STEPS:
        raise PrismError("The time step must be one of: {}.".format(", ".join(TIME_STEPS)))
    if time_step == STEP_DAILY:
        d0, d1 = _as_date(date_from), _as_date(date_to)
        if d0 is None or d1 is None:
            raise PrismError("Daily data need a first day and a last day.")
        if d0 > d1:
            raise PrismError("The first day is later than the last day.")
        if d1 < FIRST_DAY:
            raise PrismError("PRISM's daily data begin on 1 January 1981. For earlier years choose Monthly or "
                             "Annual, which begin in 1895.")
        if d0 < FIRST_DAY:
            notes.append("PRISM's daily data begin on 1 January 1981: the days before it were left out.")
            d0 = FIRST_DAY
        last = today - datetime.timedelta(days=1)
        if d1 > last:
            notes.append("PRISM's daily data end yesterday: the days from {} on were left out.".format(
                max(d0, today).isoformat()))
            d1 = last
        keep = set(_months(months))
        d = d0
        one = datetime.timedelta(days=1)
        while d <= d1:
            if d.month in keep:
                periods.append(Period(d.strftime("%Y%m%d"), d, d + one))
            d += one
        return periods, notes
    if year_from in (None, "") or year_to in (None, ""):
        raise PrismError("{} data need a first year and a last year.".format(time_step))
    y0, y1 = int(year_from), int(year_to)
    if y0 > y1:
        raise PrismError("The first year is later than the last year.")
    if y1 < FIRST_YEAR:
        raise PrismError("PRISM's data begin in 1895.")
    if y0 < FIRST_YEAR:
        notes.append("PRISM's data begin in 1895: the years before it were left out.")
        y0 = FIRST_YEAR
    if time_step == STEP_ANNUAL:
        if y1 >= today.year:
            notes.append("A year's grid is made when the year is over: {} left out.".format(
                "{} was".format(today.year) if y1 == today.year and y0 <= today.year
                else "the years from {} on were".format(max(y0, today.year))))
            y1 = today.year - 1
        for y in range(y0, y1 + 1):
            periods.append(Period("%04d" % y, datetime.date(y, 1, 1), datetime.date(y + 1, 1, 1)))
        return periods, notes
    keep = _months(months)
    dropped = False
    for y in range(y0, y1 + 1):
        for m in keep:
            start = datetime.date(y, m, 1)
            end = datetime.date(y + (m == 12), m % 12 + 1, 1)
            if end > today:                                  # the month is not over
                dropped = True
                continue
            periods.append(Period("%04d%02d" % (y, m), start, end))
    if dropped:
        notes.append("A month's grid is made when the month is over: the months from {} on were left out.".format(
            today.strftime("%B %Y")))
    return periods, notes


# ---------------------------------------------------------------------------
#  the grid and the window of it that covers the study area
# ---------------------------------------------------------------------------
class Window(object):
    """A block of PRISM cells: ``col0`` and ``row0`` count from the grid's north-west corner."""

    def __init__(self, resolution, col0, row0, cols, rows):
        self.resolution = resolution
        self.grid = GRIDS[resolution]
        self.col0, self.row0, self.cols, self.rows = int(col0), int(row0), int(cols), int(rows)
        self.cell = 1.0 / self.grid["per_degree"]

    @property
    def west(self):
        return WEST + self.col0 * self.cell

    @property
    def north(self):
        return NORTH - self.row0 * self.cell

    @property
    def east(self):
        return self.west + self.cols * self.cell

    @property
    def south(self):
        return self.north - self.rows * self.cell

    def lon_edges(self):
        return [WEST + (self.col0 + i) * self.cell for i in range(self.cols + 1)]

    def lat_edges(self):
        """North to south, as the rows are stored."""
        return [NORTH - (self.row0 + i) * self.cell for i in range(self.rows + 1)]


def window_for_box(resolution, box, margin=1):
    """The cells that hold the rectangle ``box`` (west, south, east, north, in degrees on NAD 83), with
    ``margin`` more cells on every side, cut to PRISM's grid. None when the rectangle misses the grid."""
    if resolution not in GRIDS:
        raise PrismError("The resolution must be one of: {}.".format(", ".join(RESOLUTIONS)))
    g = GRIDS[resolution]
    n = g["per_degree"]
    west, south, east, north = [float(v) for v in box]
    tiny = 1e-9                                              # a box that ends ON a cell edge takes no cell beyond it
    col0 = int(math.floor((west - WEST) * n + tiny)) - margin
    col1 = int(math.ceil((east - WEST) * n - tiny)) + margin
    row0 = int(math.floor((NORTH - north) * n + tiny)) - margin
    row1 = int(math.ceil((NORTH - south) * n - tiny)) + margin
    if col1 <= col0:
        col1 = col0 + 1
    if row1 <= row0:
        row1 = row0 + 1
    # the rectangle itself (without its margin) must touch the grid
    if (east < WEST or west > WEST + g["cols"] / float(n)
            or north < NORTH - g["rows"] / float(n) or south > NORTH):
        return None
    col0, row0 = max(0, col0), max(0, row0)
    col1, row1 = min(g["cols"], col1), min(g["rows"], row1)
    if col1 <= col0 or row1 <= row0:
        return None
    return Window(resolution, col0, row0, col1 - col0, row1 - row0)


def _zone_term(lat_degrees):
    """The authalic-latitude term of the GRS 80 ellipsoid: the area of the zone between the equator and a
    parallel is proportional to it."""
    f = 1.0 / GRS80_INV_F
    e2 = f * (2.0 - f)
    e = math.sqrt(e2)
    s = math.sin(math.radians(lat_degrees))
    return s / (1.0 - e2 * s * s) + math.log((1.0 + e * s) / (1.0 - e * s)) / (2.0 * e)


def cell_areas_km2(lat_edges, cell_degrees):
    """The true area in square kilometers of a cell in each row, on the GRS 80 ellipsoid. ``lat_edges`` are
    the rows' edges (one more than the rows); every cell is ``cell_degrees`` of longitude wide."""
    f = 1.0 / GRS80_INV_F
    b = GRS80_A * (1.0 - f)
    k = b * b / 2.0 * math.radians(cell_degrees) / 1e6
    terms = [_zone_term(v) for v in lat_edges]
    return [abs(k * (terms[i] - terms[i + 1])) for i in range(len(lat_edges) - 1)]


def chunk_shape(times, rows, cols, target_values=131072, most_cells=2304, memory_values=48000000):
    """(dates, rows, cols) in one chunk of the file. A chunk is a block of dates of a block of cells, near
    half a megabyte, so a map of one date and the series of one cell each read few chunks. ``memory_values``
    bounds the block of dates that is held in memory while the file is written."""
    side = max(1, int(math.sqrt(most_cells)))
    cy, cx = max(1, min(rows, side)), max(1, min(cols, side))
    ct = max(1, target_values // (cy * cx))
    ct = min(ct, max(1, memory_values // max(1, rows * cols)))
    ct = max(1, min(ct, max(1, times)))
    return ct, cy, cx


# ---------------------------------------------------------------------------
#  the kept files
# ---------------------------------------------------------------------------
def kept_path(folder, set_code, resolution, code, token):
    """Where a grid's zip file is kept: <folder>\\an_4km\\ppt\\prism_ppt_us_25m_202001.zip. The file name is
    PRISM's own, so a file fetched by hand from PRISM's site can be put there too."""
    g = GRIDS[resolution]
    return os.path.join(folder, "{}_{}".format(set_code, g["path"]), code,
                        "prism_{}_us_{}_{}.zip".format(code, g["tag"], token))


def expected_mb(resolution, code, time_step):
    """About how large one grid's zip file is, in megabytes (measured November 2026)."""
    if resolution == RES_4KM:
        return 2.5
    if code == "ppt" and time_step == STEP_DAILY:
        return 25.0
    return 55.0


def read_info(zip_path):
    """The lines of the zip's .info.txt as a dict (PRISM_DATASET_TYPE and the rest); empty when it has none."""
    out = {}
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".info.txt")]
            if names:
                for line in z.read(names[0]).decode("utf-8", "replace").splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        out[k.strip()] = v.strip()
    except (zipfile.BadZipFile, OSError, KeyError):
        pass
    return out


def created_day(info):
    """The day PRISM made a grid, as the number YYYYMMDD, from its info file; 0 when it does not say. A
    year's grid lists the days its twelve months were made: the latest is taken.

    PRISM's info files come in two layouts (both seen on 21 November 2026). In the older one
    PRISM_DATASET_CREATE_DATE is the day the grid was modeled. In the newer one that line is the day the
    file was written in its delivery format, and the day the grid was modeled is in
    PRISM_SOURCE_CREATE_DATE. The day the grid was modeled is the one that tells one issue of a grid from
    the next, so it is taken where the file gives it."""
    text = info.get("PRISM_SOURCE_CREATE_DATE") or info.get("PRISM_DATASET_CREATE_DATE") or ""
    days = [int(m) for m in re.findall(r"(?<!\d)((?:18|19|20)\d{6})(?!\d)", text)]
    return max(days) if days else 0


def read_window(zip_path, window):
    """The window's cells from the GeoTIFF inside a grid's zip file, as a float32 array with PRISM's no-data
    value. Raises PrismError when the file is not a PRISM grid of the expected size and place."""
    import numpy as np
    from osgeo import gdal

    try:
        with zipfile.ZipFile(zip_path) as z:
            tifs = [n for n in z.namelist() if n.lower().endswith(".tif")]
    except (zipfile.BadZipFile, OSError) as e:
        raise PrismError("{} is not a zip file that can be read ({}). Delete it and run the tool again.".format(
            zip_path, e))
    if not tifs:
        raise PrismError("{} holds no GeoTIFF. Delete it and run the tool again.".format(zip_path))
    src = "/vsizip/{" + zip_path.replace("\\", "/") + "}/" + tifs[0]
    gdal.UseExceptions()
    try:
        ds = gdal.Open(src)
    except RuntimeError as e:
        raise PrismError("The grid in {} could not be opened ({}).".format(zip_path, e))
    try:
        g = window.grid
        gt = ds.GetGeoTransform()
        cell = window.cell
        if (ds.RasterXSize != g["cols"] or ds.RasterYSize != g["rows"] or abs(gt[0] - WEST) > 1e-6
                or abs(gt[3] - NORTH) > 1e-6 or abs(gt[1] - cell) > 1e-9 or abs(gt[5] + cell) > 1e-9):
            raise PrismError(
                "The grid in {} is not laid out as PRISM's {} grid is expected to be ({} by {} cells from "
                "{:.6f}, {:.6f}). PRISM may have changed its grid: please report this.".format(
                    zip_path, window.resolution, ds.RasterXSize, ds.RasterYSize, gt[0], gt[3]))
        band = ds.GetRasterBand(1)
        a = band.ReadAsArray(window.col0, window.row0, window.cols, window.rows)
        a = np.asarray(a, dtype="float32")
        nd = band.GetNoDataValue()
        if nd is not None and nd != FILL:
            a[a == np.float32(nd)] = FILL
        a[~np.isfinite(a)] = FILL
        return a
    finally:
        ds = None


# ---------------------------------------------------------------------------
#  PRISM's web service
# ---------------------------------------------------------------------------
class Service(object):
    """PRISM's web service. ``fetch`` writes one grid's zip file; ``release_dates`` asks when grids were
    last issued. A test gives ``build`` another object with the same two methods."""

    def __init__(self, pause=PAUSE):
        self.pause = pause
        self._last = 0.0
        self._swept = set()                  # the folders whose abandoned downloads have been cleared

    def _wait(self, cancelled=None):
        while True:
            left = self.pause - (time.time() - self._last)
            if left <= 0:
                break
            if cancelled is not None and cancelled():
                raise Cancelled()
            time.sleep(min(0.25, left))
        self._last = time.time()

    def grid_url(self, set_code, resolution, code, token):
        return "{}us/{}/{}/{}{}".format(SERVICE, GRIDS[resolution]["path"], code, token,
                                        "/lt" if set_code == "lt" else "")

    def _open(self, url, cancelled=None, timeout=300):
        self._wait(cancelled)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            try:
                detail = e.read(600).decode("utf-8", "replace").strip()
            except Exception:                                    # noqa: BLE001
                detail = ""
            raise PrismError("PRISM's web service returned an error ({}) for {}.{}".format(
                e.code, url, " It said: " + detail[:300] if detail else ""))
        except (urllib.error.URLError, OSError) as e:
            raise PrismError("PRISM's web service at {} could not be reached ({}). Check the internet "
                             "connection, or try again in a few minutes.".format(
                                 urllib.parse.urlsplit(url).netloc, getattr(e, "reason", e)))

    def fetch(self, set_code, resolution, code, token, dest, cancelled=None):
        """Downloads one grid's zip file to ``dest``; returns its size. NotIssued when PRISM has no grid for
        the date. A download that breaks off is tried once more and no more: PRISM allows a file twice
        in 24 hours."""
        url = self.grid_url(set_code, resolution, code, token)
        # The folder may be shared by several people (a network drive), and two of them may want the same
        # grid at the same moment. So the file is written under a name of this download's own, and takes its
        # real name in one step when it is complete: nobody ever sees, or writes into, half a file.
        part = "{}.{}.part".format(dest, uuid.uuid4().hex[:8])
        folder = os.path.dirname(dest)
        if folder:
            os.makedirs(folder, exist_ok=True)       # another person's run may be making it this moment
        if folder not in self._swept:
            self._swept.add(folder)
            _sweep_parts(folder)
        last = None
        for attempt in range(2):
            try:
                if folder:
                    os.makedirs(folder, exist_ok=True)   # and may have removed it since, when it was empty
                resp = self._open(url, cancelled)
                promised = G.promised_length(resp)
                try:
                    head = b""
                    size = 0
                    with open(part, "wb") as f:
                        while True:
                            block = resp.read(1 << 20)
                            if not block:
                                break
                            if len(head) < 2000:
                                head += block[:2000 - len(head)]
                            f.write(block)
                            size += len(block)
                            if cancelled is not None and cancelled():
                                raise Cancelled()
                finally:
                    resp.close()
            except (http.client.HTTPException, OSError) as e:    # the answer broke off part way
                last = PrismError("PRISM's web service stopped answering part way through {} ({}). Try again "
                                  "in a few minutes.".format(url, e))
                _remove(part)
                continue
            except BaseException:
                _remove(part)
                raise
            if promised is not None and size < promised:
                # the connection closed early, which Python reports as an ordinary end (see
                # gbif_occurrences.promised_length): half a grid must not take the grid's name in a
                # folder that later runs, and other people, read from
                last = PrismError("PRISM's web service stopped sending {} part way: {} of {} bytes came. Try "
                                  "again in a few minutes.".format(url, G.n0(size), G.n0(promised)))
                _remove(part)
                continue
            if head[:2] != b"PK":
                _remove(part)
                text = head.decode("utf-8", "replace").strip()
                if "invalid date" in text.lower():
                    raise NotIssued(" ".join(text.split()))
                raise PrismError("PRISM's web service did not send a grid for {}. It said: {}".format(
                    url, " ".join(text.split())[:400] or "(nothing)"))
            for tries in range(REPLACE_TRIES):
                try:
                    os.replace(part, dest)
                    break
                except OSError:
                    # Windows refuses to replace a file that is open. Someone else's run fetched the same
                    # grid while this one did and is reading it now; a read takes a moment, so wait and try
                    # again. If it never frees, theirs is kept: it is the same grid, and as good as ours.
                    if tries + 1 < REPLACE_TRIES:
                        time.sleep(REPLACE_WAIT)
                        continue
                    _remove(part)
                    if not os.path.isfile(dest):
                        raise
            return size
        raise last

    def release_dates(self, resolution, code, token0, token1, cancelled=None):
        """{token: the date PRISM last issued that grid} for the grids from ``token0`` to ``token1``. Empty
        when the service has nothing to say (it keeps no dates for annual grids, nor before April 2014)."""
        url = "{}releaseDate/us/{}/{}/{}{}?json=true".format(
            SERVICE, GRIDS[resolution]["path"], code, token0, "" if token1 == token0 else "/" + token1)
        try:
            resp = self._open(url, cancelled, timeout=120)
            try:
                body = resp.read(4 << 20).decode("utf-8", "replace")
            finally:
                resp.close()
            rows = json.loads(body)
        except (PrismError, ValueError, http.client.HTTPException, OSError):
            return {}
        if rows and not isinstance(rows[0], list):
            rows = [rows]
        out = {}
        n = len(token0)
        for r in rows:
            try:
                data_day = datetime.datetime.strptime(str(r[0]), "%Y-%m-%d").date()
                issued = datetime.datetime.strptime(str(r[1]), "%Y-%m-%d").date()
            except (ValueError, IndexError, TypeError):
                continue
            out[data_day.strftime("%Y%m%d")[:n]] = issued
        return out


def _remove(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


PART_AGE = 24 * 3600.0               # seconds after which an unfinished download is taken to be abandoned
REPLACE_TRIES = 5                    # tries at giving a finished download its name, when the name is in use
REPLACE_WAIT = 0.4                   # seconds between those tries
_OWN_PART = re.compile(r"^prism_.+\.zip(\.[0-9a-f]{8})?\.part$", re.I)


def _sweep_parts(folder):
    """Removes this tool's unfinished downloads (prism_....zip.part, prism_....zip.<8 letters>.part) in a
    folder when they are more than a day old: what a run that was killed left behind. A younger one may be
    another person's download in progress, in a folder that several people share, and is left alone. No
    other file is touched, whatever its name. Never raises."""
    try:
        now = time.time()
        for name in os.listdir(folder):
            if _OWN_PART.match(name):
                p = os.path.join(folder, name)
                try:
                    if now - os.path.getmtime(p) > PART_AGE:
                        os.remove(p)
                except OSError:
                    pass
    except OSError:
        pass


# ---------------------------------------------------------------------------
#  the netCDF file
# ---------------------------------------------------------------------------
TIME_UNITS = "days since 1900-01-01 00:00:00"
_EPOCH = datetime.date(1900, 1, 1)

# The file's made_with attribute. The add-in looks for exactly this text in the first megabyte of a netCDF
# file to know that this tool wrote it (OnlineData/Prism/PrismLayerFacts.cs, Marker: keep the two the same; a
# test compares them), and only then opens the map's time slider on a single date when a layer of the file is
# added to a map. A file that holds it has single-date time steps. Change the text only together with the
# add-in, and never write it into a file laid out differently.
MADE_WITH = "Wildlife and Forestry Tools for ArcGIS Pro: PRISM Climate Data"


def _days(d):
    return float((d - _EPOCH).days)


def _create(path, window, variables, time_step, set_label, chunks, attributes):
    import netCDF4
    import numpy as np

    ds = netCDF4.Dataset(path, "w", format="NETCDF4")
    for k, v in attributes.items():
        ds.setncattr(k, v)
    ds.createDimension("time", None)
    ds.createDimension("lat", window.rows)
    ds.createDimension("lon", window.cols)
    ds.createDimension("nv", 2)

    t = ds.createVariable("time", "f8", ("time",))
    t.units = TIME_UNITS
    t.calendar = "standard"
    t.standard_name = "time"
    t.long_name = "first day of the " + {STEP_MONTHLY: "month", STEP_DAILY: "day", STEP_ANNUAL: "year"}[time_step]
    t.axis = "T"
    # NOT t.bounds = "time_bnds". With that link ArcGIS Pro (3.7) reads every time step as a span and works
    # out the layer's step as an average (29.5 days for months, 365 for years), and its time slider, which
    # moves a multidimensional layer only when the map's time EQUALS a time step, start and end, can then
    # never move it. Without the link Pro reads single dates and a step of 1 month, 1 day or 1 year. The
    # spans stay in the file, in time_bnds.
    t.comment = TIME_NOTE
    tb = ds.createVariable("time_bnds", "f8", ("time", "nv"))
    tb.units = TIME_UNITS
    tb.calendar = "standard"
    tb.long_name = "first day of each time step, and the day after its last day"
    tb.comment = ("The span of each value of the variable time. The time variable does not name this one in a "
                  "bounds attribute, because ArcGIS Pro then reads every time step as a span and its time "
                  "slider can no longer step through the file.")

    lat_e, lon_e = window.lat_edges(), window.lon_edges()
    la = ds.createVariable("lat", "f8", ("lat",))
    la.units = "degrees_north"
    la.standard_name = "latitude"
    la.long_name = "latitude of the cell's center"
    la.axis = "Y"
    la.bounds = "lat_bnds"
    la[:] = np.array([(lat_e[i] + lat_e[i + 1]) / 2.0 for i in range(window.rows)])
    lo = ds.createVariable("lon", "f8", ("lon",))
    lo.units = "degrees_east"
    lo.standard_name = "longitude"
    lo.long_name = "longitude of the cell's center"
    lo.axis = "X"
    lo.bounds = "lon_bnds"
    lo[:] = np.array([(lon_e[i] + lon_e[i + 1]) / 2.0 for i in range(window.cols)])
    lab = ds.createVariable("lat_bnds", "f8", ("lat", "nv"))
    lab[:] = np.array([[lat_e[i + 1], lat_e[i]] for i in range(window.rows)])      # south edge, north edge
    lob = ds.createVariable("lon_bnds", "f8", ("lon", "nv"))
    lob[:] = np.array([[lon_e[i], lon_e[i + 1]] for i in range(window.cols)])      # west edge, east edge

    crs = ds.createVariable("crs", "i4")
    crs.grid_mapping_name = "latitude_longitude"
    crs.long_name = "NAD 83, latitude and longitude"
    crs.semi_major_axis = GRS80_A
    crs.inverse_flattening = GRS80_INV_F
    crs.longitude_of_prime_meridian = 0.0
    crs.crs_wkt = NAD83_WKT
    crs.spatial_ref = NAD83_WKT
    crs.GeoTransform = "{!r} {!r} 0 {!r} 0 {!r} ".format(window.west, window.cell, window.north, -window.cell)

    area = ds.createVariable("cell_area", "f4", ("lat", "lon"), zlib=True, complevel=4)
    area.units = "km2"
    area.standard_name = "cell_area"
    area.long_name = "true area of the cell on the GRS 80 ellipsoid"
    area.grid_mapping = "crs"
    area.comment = AREA_NOTE
    per_row = np.array(cell_areas_km2(lat_e, window.cell), dtype="float32")
    area[:] = np.repeat(per_row[:, None], window.cols, axis=1)

    for v in variables:
        nv = ds.createVariable(v.code, "f4", ("time", "lat", "lon"), zlib=True, complevel=4, shuffle=True,
                               fill_value=FILL, chunksizes=chunks)
        nv.units = v.units
        nv.long_name = v.long_name
        nv.grid_mapping = "crs"
        nv.cell_measures = "area: cell_area"
        if time_step == STEP_DAILY:
            nv.cell_methods = "time: " + v.within
        else:
            nv.cell_methods = "time: sum" if v.total else "time: {} within days time: mean over days".format(v.within)
        nv.comment = variable_note(v, time_step)
        nv.prism_element = v.code
        nv.prism_dataset = set_label
        made = ds.createVariable(v.code + "_prism_created", "i4", ("time",), fill_value=0)
        made.long_name = "the day PRISM made this grid of {}, as YYYYMMDD (0 where its file does not say)".format(v.code)
    prov = ds.createVariable("provisional", "i1", ("time",), fill_value=-1)
    prov.long_name = "1 where a grid was less than six months old when it was fetched"
    prov.comment = PROVISIONAL_NOTE
    return ds


# ---------------------------------------------------------------------------
#  the run, without arcpy
# ---------------------------------------------------------------------------
def check_output(path):
    """None when a netCDF file can be written at ``path``, else the reason."""
    p = str(path or "").strip()
    if not p:
        return "Give the netCDF file to write."
    low = p.lower().replace("/", "\\").rstrip("\\")
    if not low.endswith(".nc"):
        return "The output is a netCDF file: give it a name that ends in .nc."
    folder = os.path.dirname(low)
    first = low.split("\\")[0]
    if first in ("memory", "in_memory") or any(
            folder.endswith(e) or (e + "\\") in folder + "\\" for e in (".gdb", ".sde")):
        return "A netCDF file is a file in a folder: it cannot be written into a geodatabase or to memory."
    if not folder or not os.path.isdir(os.path.dirname(p)):
        return "The folder {} does not exist.".format(os.path.dirname(p) or "(none)")
    return None


# ---------------------------------------------------------------------------
#  the folder of downloaded files, remembered from one project to the next
# ---------------------------------------------------------------------------
# PRISM's files are whole-country grids, as useful to the next project as to this one, so the folder that
# keeps them belongs to the Windows user and not to a project. The folder a run is given is written to a
# small settings file in the user's own application data, and every later run, in any project, looks there.
HOW_GIVEN, HOW_REMEMBERED, HOW_REMADE, HOW_DEFAULT, HOW_LOST = "given", "remembered", "remade", "default", "lost"


def settings_path():
    """The file that remembers the folder: %LOCALAPPDATA%\\JennessEnterprises\\WildlifeTools\\
    prism_settings.json, beside the add-in's other per-user settings."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "JennessEnterprises", "WildlifeTools", SETTINGS_NAME)


def default_folder():
    """Where the files go when no folder has ever been given: PRISM_downloads in the user's own folder
    (C:\\Users\\name), which is outside every project. Not Documents: Windows can be set to copy Documents
    to OneDrive, and these files run to gigabytes."""
    return os.path.join(os.path.expanduser("~"), FOLDER_NAME)


def _read_settings():
    try:
        with open(settings_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _arrived(path, had, stamp):
    """True when a grid this run meant to fetch is in the folder after all, put there by another run since
    this one was planned: a file where there was none, or (for a grid PRISM has reissued, whose kept file
    was written at ``stamp``) a file written since. Never raises."""
    try:
        if not had:
            return os.path.isfile(path)
        return stamp is not None and os.path.getmtime(path) > stamp
    except OSError:
        return False


def same_folder(a, b):
    return bool(a) and bool(b) and os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def remembered_folder():
    """The folder a run was last given for the downloaded files, or None. It may no longer be there."""
    text = _read_settings().get("download_folder")
    if isinstance(text, str) and text.strip():
        return os.path.normpath(text.strip())
    return None


def remember_folder(folder):
    """Writes the folder to the settings file, keeping whatever else the file holds. Returns None, or the
    reason it could not be written."""
    try:
        path = settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = _read_settings()
        data["download_folder"] = os.path.normpath(os.path.abspath(folder))
        tmp = path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        return None
    except (OSError, TypeError, ValueError) as e:
        return str(e)


def choose_folder(given=None):
    """Which folder holds the downloaded files. Returns (folder, how, lost):

    given       the caller named it
    remembered  none was named, and the remembered folder is there
    remade      none was named; the remembered folder is gone but the folder that held it is there, so it
                is made again in the same place
    default     none was named and none is remembered: ``default_folder()``
    lost        none was named; the remembered folder and the folder that held it cannot be found (a drive
                that is not connected, usually). ``lost`` is its path, and the default folder is used.
    """
    text = "" if given in (None, "") else str(given).strip().strip('"').strip()
    if text:
        return os.path.normpath(os.path.abspath(text)), HOW_GIVEN, None
    kept = remembered_folder()
    if not kept or same_folder(kept, default_folder()):
        return default_folder(), HOW_DEFAULT, None
    if os.path.isdir(kept):
        return kept, HOW_REMEMBERED, None
    if os.path.isdir(os.path.dirname(kept)):
        return kept, HOW_REMADE, None
    return default_folder(), HOW_LOST, kept


def folder_to_offer():
    """The folder a dialog shows before the user has named one: the folder a run would use, when it is
    there already. None when it is not (a dialog cannot show a folder that does not exist)."""
    try:
        folder, _how, _lost = choose_folder(None)
        return folder if os.path.isdir(folder) else None
    except Exception:                                            # noqa: BLE001
        return None


def check_folder(path):
    """None when the path can be the folder of downloaded files, or the reason it cannot."""
    p = str(path or "").strip()
    low = p.lower().replace("/", "\\").rstrip("\\")
    if not low:
        return "Give a folder for the downloaded files."
    if low.split("\\")[0] in ("memory", "in_memory") or any(
            low.endswith(e) or (e + "\\") in low for e in (".gdb", ".sde")):
        return ("PRISM's files are zip files and are kept in a folder on disk: they cannot go into a "
                "geodatabase or to memory.")
    if os.path.isfile(p):
        return "{} is a file, not a folder.".format(p)
    return None


def folder_notes(how, folder, lost=None):
    """What to tell the user about a folder that was not named: (text, is a warning). (None, False) when
    the folder was named."""
    if how == HOW_REMEMBERED:
        return ("No folder was given for the downloaded files, so the tool uses the one it remembers from an "
                "earlier run: {}.".format(folder), False)
    if how == HOW_REMADE:
        return ("No folder was given for the downloaded files. The one the tool remembers, {}, is no longer "
                "there, so it is made again in the same place and the grids are downloaded "
                "afresh.".format(folder), True)
    if how == HOW_DEFAULT:
        return ("No folder was given for the downloaded files and none is remembered, so they go to {}. To "
                "keep them somewhere else, give a folder: the tool remembers it for every "
                "project.".format(folder), False)
    if how == HOW_LOST:
        return ("The folder the tool remembers for the downloaded files, {}, cannot be found. If it is on a "
                "drive that is not connected, connect the drive and run the tool again. For this run the "
                "files go to {}, and a grid this run needs is downloaded even if it is in the folder that "
                "was not found. The tool still remembers that folder.".format(lost, folder), True)
    return (None, False)


def build(out_nc, box, variables=None, time_step=STEP_MONTHLY, year_from=None, year_to=None, months=None,
          date_from=None, date_to=None, resolution=RES_4KM, dataset=SET_AN, download_folder=None,
          keep_downloads=True, margin_cells=1, max_download_gb=DEFAULT_MAX_GB, service=None, say=None,
          warn=None, step=None, cancelled=None, today=None, area_words=None, remember=True):
    """Fetches the grids and writes the netCDF file. ``box`` is the study area's rectangle (west, south,
    east, north) in degrees on NAD 83. ``download_folder`` is where PRISM's files are kept; None uses the
    folder remembered from an earlier run, or the default one (see ``choose_folder``). A folder that is
    given is remembered for later runs when ``remember`` and ``keep_downloads`` are both true. Returns a
    dict: path, variables, periods, rows, cols, bytes, downloaded, reused, refreshed, missing, provisional,
    folder, folder_how, remembered."""
    import numpy as np

    say = say or (lambda text: None)
    warn = warn or (lambda text: None)
    step = step or (lambda percent, text: None)
    cancelled = cancelled or (lambda: False)
    today = today or datetime.date.today()
    service = service or Service()

    why = check_output(out_nc)
    if why:
        raise PrismError(why)
    wanted = variables_from(variables or DEFAULT_LABELS)
    if not wanted:
        raise PrismError("Choose at least one variable.")
    if resolution not in GRIDS:
        raise PrismError("The resolution must be one of: {}.".format(", ".join(RESOLUTIONS)))
    if dataset not in SET_CODES:
        raise PrismError("The dataset must be one of: {}.".format("; ".join(DATASETS)))
    set_code = SET_CODES[dataset]
    if set_code == "lt":
        if time_step != STEP_MONTHLY or resolution != RES_800M:
            raise PrismError("PRISM offers its Long-term networks (LT) dataset as monthly grids at 800 m only. "
                             "Choose Monthly and 800 m, or the All networks (AN) dataset.")
        solar = [v.label for v in wanted if v.solar]
        if solar:
            raise PrismError("The Long-term networks (LT) dataset has no solar radiation. Leave out: {}.".format(
                "; ".join(solar)))
    try:
        margin = int(margin_cells if margin_cells not in (None, "") else 1)
    except (TypeError, ValueError):
        raise PrismError("The number of cells to add around the study area must be a whole number.")
    if not 0 <= margin <= 1000:
        raise PrismError("The number of cells to add around the study area must be from 0 to 1,000.")
    try:
        max_gb = float(max_download_gb if max_download_gb not in (None, "") else DEFAULT_MAX_GB)
    except (TypeError, ValueError):
        raise PrismError("The largest download allowed must be a number of gigabytes.")
    if max_gb <= 0:
        raise PrismError("The largest download allowed must be greater than zero.")

    periods, notes = plan_periods(time_step, year_from, year_to, months, date_from, date_to, today)
    for line in notes:
        warn(line)
    if not periods:
        raise PrismError("None of the dates asked for has a PRISM grid yet.")
    window = window_for_box(resolution, box, margin)
    if window is None:
        raise PrismError("The study area lies outside PRISM's grids, which cover the conterminous United "
                         "States only (the lower 48 states).")
    folder, folder_how, lost = choose_folder(download_folder)
    why = check_folder(folder)
    if why:
        raise PrismError(why)
    if not os.path.isdir(folder):
        try:
            os.makedirs(folder)
        except OSError as e:
            raise PrismError("The folder for the downloaded files could not be made: {} ({}).".format(folder, e))
    # the folder a run is given is the one every later run looks in, in this project or any other
    is_remembered = folder_how in (HOW_REMEMBERED, HOW_REMADE)
    not_remembered = None
    if folder_how == HOW_GIVEN and remember and keep_downloads:
        if same_folder(folder, remembered_folder()):
            is_remembered = True
        else:
            not_remembered = remember_folder(folder)
            is_remembered = not_remembered is None

    # ---- which grids are already here, which have been reissued, which must be fetched --------------
    T, V = len(periods), len(wanted)
    paths = [[kept_path(folder, set_code, resolution, v.code, p.token) for p in periods] for v in wanted]
    have = [[os.path.isfile(paths[i][k]) for k in range(T)] for i in range(V)]
    stale = [[False] * T for _ in range(V)]
    stale_stamp = {}                                         # (i, k): when the kept file was written
    for i, v in enumerate(wanted):
        if set_code != "an" or time_step == STEP_ANNUAL:
            continue                                         # PRISM's release-date service covers neither
        doubt = []
        for k, p in enumerate(periods):
            if have[i][k]:
                got = datetime.date.fromtimestamp(os.path.getmtime(paths[i][k]))
                if got < p.end + datetime.timedelta(days=STABLE_AFTER_DAYS) and got < today:
                    doubt.append(k)
        if not doubt:
            continue
        step(0, "Asking PRISM which kept grids of {} it has reissued...".format(v.code))
        issued = service.release_dates(resolution, v.code, periods[doubt[0]].token, periods[doubt[-1]].token,
                                       cancelled)
        for k in doubt:
            when = issued.get(periods[k].token)
            got = datetime.date.fromtimestamp(os.path.getmtime(paths[i][k]))
            if when is not None and when > got:
                stale[i][k] = True
                stale_stamp[(i, k)] = os.path.getmtime(paths[i][k])
    to_fetch = sum(1 for i in range(V) for k in range(T) if not have[i][k] or stale[i][k])
    n_stale = sum(1 for i in range(V) for k in range(T) if stale[i][k])
    reuse = T * V - to_fetch
    mb = sum(expected_mb(resolution, wanted[i].code, time_step)
             for i in range(V) for k in range(T) if not have[i][k] or stale[i][k])

    say("PRISM {} grids at {} ({} cells), {}: {} for {} ({} to {}).".format(
        time_step.lower(), resolution, GRIDS[resolution]["words"], dataset,
        "; ".join(v.code for v in wanted),
        "{} {}".format(n0(T), {STEP_MONTHLY: "months", STEP_DAILY: "days", STEP_ANNUAL: "years"}[time_step])
        if T != 1 else "one " + {STEP_MONTHLY: "month", STEP_DAILY: "day", STEP_ANNUAL: "year"}[time_step],
        periods[0].words(), periods[-1].words()))
    say("The study area{} takes {} by {} cells of PRISM's grid ({} cells), from {:.4f} to {:.4f} degrees of "
        "longitude and {:.4f} to {:.4f} of latitude, with {} added on every side.".format(
            " (" + area_words + ")" if area_words else "", n0(window.cols), n0(window.rows),
            n0(window.cols * window.rows), window.west, window.east, window.south, window.north,
            "one cell" if margin == 1 else "{} cells".format(n0(margin))))
    note, serious = folder_notes(folder_how, folder, lost)
    if note:
        (warn if serious else say)(note)
    if not_remembered:
        warn("The folder could not be remembered for later runs ({}). Give it again next time.".format(
            not_remembered))
    say("{} in all. {} already in {}{}.".format(
        "One grid" if T * V == 1 else "{} grids".format(n0(T * V)),
        "None is" if reuse == 0 else "One is" if reuse == 1 else "{} are".format(n0(reuse)), folder,
        "" if not n_stale else " ({} more {} there, but PRISM has reissued {} since)".format(
            n0(n_stale), "is" if n_stale == 1 else "are", "it" if n_stale == 1 else "them")))
    if to_fetch:
        say("{} to download, about {}: every grid PRISM sends is the whole conterminous United States, whatever "
            "the study area. It takes at least {}, because PRISM asks for a pause of {:.0f} seconds between "
            "requests.".format(
                "One grid" if to_fetch == 1 else "{} grids".format(n0(to_fetch)), size_words(mb * 1024 * 1024),
                span(to_fetch * service_pause(service)), service_pause(service)))
    if mb / 1024.0 > max_gb:
        raise PrismError(
            "This request would download about {}, more than the {:g} GB allowed. Nothing was downloaded. "
            "Ask for fewer variables or dates, choose 4 km in place of 800 m, or raise the largest download "
            "allowed. PRISM's own words on downloads: \"Repeated excessive download activity may result in IP "
            "address blocking.\"".format(size_words(mb * 1024 * 1024), max_gb))

    # ---- the file -------------------------------------------------------------------------------------
    chunks = chunk_shape(T, window.rows, window.cols)
    block = chunks[0]
    cite = "PRISM Group, Oregon State University, {}, accessed {}.".format(
        SITE, "{} {}".format(today.day, today.strftime("%b %Y")))
    attributes = {
        "Conventions": "CF-1.8",
        "title": "PRISM climate data: {} grids at {}, {}".format(time_step.lower(), resolution, dataset),
        "institution": "PRISM Group, Oregon State University",
        "source": "PRISM's web service, " + SERVICE,
        "references": SITE + " ; " + DATASETS_DOC,
        "citation": cite,
        "license": "PRISM's terms of use: " + TERMS,
        "comment": CAUTION,
        "history": "{}: written by the PRISM Climate Data tool of Wildlife and Forestry Tools for ArcGIS Pro "
                   "(Jenness Enterprises, https://www.jennessent.com).".format(today.isoformat()),
        "made_with": MADE_WITH,
        "prism_dataset": dataset,
        "prism_resolution": resolution,
        "prism_time_step": time_step,
        "geospatial_lon_min": window.west, "geospatial_lon_max": window.east,
        "geospatial_lat_min": window.south, "geospatial_lat_max": window.north,
        "time_coverage_start": periods[0].start.isoformat(),
        "time_coverage_end": (periods[-1].end - datetime.timedelta(days=1)).isoformat(),
    }
    if time_step == STEP_DAILY:
        attributes["prism_day"] = PRISM_DAY_NOTE
    tmp = out_nc + ".part"
    _remove(tmp)
    ds = _create(tmp, window, wanted, time_step, dataset, chunks, attributes)
    fetched = refreshed = reused = arrived = deleted = 0
    fetched_bytes = 0
    issued_count = [0] * V
    missing = []
    made_here = set()
    provisional = np.zeros(T, dtype="int8")
    # each variable's values over ALL its time steps, for the file (actual_range) and for the stretch of the
    # layers: ArcGIS Pro takes a netCDF layer's stretch from the first time step alone
    seen = [{"min": float("inf"), "max": float("-inf"), "sum": 0.0, "sum2": 0.0, "n": 0} for _ in range(V)]
    done = 0
    t_start = time.time()
    fetch_seconds = 0.0
    try:
        for i, v in enumerate(wanted):
            nc_var = ds.variables[v.code]
            made_var = ds.variables[v.code + "_prism_created"]
            stop = False
            for k0 in range(0, T, block):
                k1 = min(T, k0 + block)
                buf = np.full((k1 - k0, window.rows, window.cols), FILL, dtype="float32")
                made = np.zeros(k1 - k0, dtype="int32")
                got = 0
                for k in range(k0, k1):
                    if cancelled():
                        raise Cancelled()
                    p = periods[k]
                    left_to_fetch = to_fetch - fetched - refreshed
                    eta = ""
                    if fetched + refreshed >= 2 and left_to_fetch > 0:
                        eta = " (about {} left)".format(span(fetch_seconds / (fetched + refreshed) * left_to_fetch))
                    step(100.0 * done / (T * V), "Grid {} of {}: {}, {}{}".format(
                        n0(done + 1), n0(T * V), v.code, p.words(), eta))
                    path = paths[i][k]
                    fetch_it = not have[i][k] or stale[i][k]
                    if fetch_it and _arrived(path, have[i][k], stale_stamp.get((i, k))):
                        # The grid has arrived since this run was planned: another person's run fetched it
                        # into a folder that is shared. It is whole (a file takes its name only when it is
                        # complete), so PRISM is not asked for it again.
                        fetch_it = False
                        arrived += 1
                    elif not fetch_it and not os.path.isfile(path):
                        # The grid was in the folder when this run was planned and is gone now: someone has
                        # deleted it, or a run that does not keep its downloads has. It is fetched.
                        fetch_it = True
                    if fetch_it:
                        t0 = time.time()
                        try:
                            size = service.fetch(set_code, resolution, v.code, p.token, path, cancelled)
                        except NotIssued:
                            missing.append((v.code, p.words()))
                            later = T - k - 1
                            warn("PRISM has not issued {} for {} yet.{}".format(
                                v.code, p.words(),
                                "" if not later else " The {} after it {} not asked for.".format(
                                    "date" if later == 1 else "{} dates".format(n0(later)),
                                    "was" if later == 1 else "were")))
                            stop = True
                            break
                        fetch_seconds += time.time() - t0
                        fetched_bytes += size
                        if stale[i][k]:
                            refreshed += 1
                        else:
                            fetched += 1
                        if not have[i][k]:
                            made_here.add(path)      # a grid fetched afresh over a kept one is not: see below
                    else:
                        reused += 1
                    info = read_info(path)
                    kind = (info.get("PRISM_DATASET_TYPE") or "").lower()
                    if kind and not kind.startswith(set_code):
                        raise PrismError(
                            "{} holds a grid of PRISM's {} dataset, not the {} that was asked for. Move or "
                            "delete it and run the tool again.".format(path, kind.split("/")[0].upper(), dataset))
                    buf[k - k0] = read_window(path, window)
                    made[k - k0] = created_day(info)
                    when = datetime.date.fromtimestamp(os.path.getmtime(path))
                    if when < p.end + datetime.timedelta(days=183):
                        provisional[k] = 1
                    got += 1
                    done += 1
                    # A run NEVER deletes a file that was in the folder before it began, whatever
                    # keep_downloads says: the files may serve other projects and other people. Only what
                    # this run added to the folder is its own to delete. (A grid PRISM had reissued, fetched
                    # afresh over the kept one, stays: the kept file was there before.)
                    if not keep_downloads and path in made_here:
                        _remove(path)
                        deleted += 1
                if got:
                    nc_var[k0:k0 + got] = buf[:got]
                    made_var[k0:k0 + got] = made[:got]
                    issued_count[i] = k0 + got
                    good = buf[:got][buf[:got] != np.float32(FILL)].astype("float64")
                    if good.size:
                        s = seen[i]
                        s["min"] = min(s["min"], float(good.min()))
                        s["max"] = max(s["max"], float(good.max()))
                        s["sum"] += float(good.sum())
                        s["sum2"] += float((good * good).sum())
                        s["n"] += int(good.size)
                if stop:
                    done = (i + 1) * T
                    break
        used = max(issued_count)
        if used == 0:
            raise PrismError("PRISM has issued none of the grids asked for yet. Nothing was written.")
        ds.variables["time"][:used] = np.array([_days(p.start) for p in periods[:used]])
        ds.variables["time_bnds"][:used] = np.array([[_days(p.start), _days(p.end)] for p in periods[:used]])
        ds.variables["provisional"][:used] = provisional[:used]
        ranges = {}
        for i, v in enumerate(wanted):
            r = value_range(seen[i])
            if r is not None:
                ranges[v.code] = r
                ds.variables[v.code].actual_range = np.array([r["min"], r["max"]], dtype="float32")
        ds.setncattr("time_coverage_end", (periods[used - 1].end - datetime.timedelta(days=1)).isoformat())
        ds.close()
        ds = None
        _remove(out_nc)
        os.replace(tmp, out_nc)
        write_statistics(out_nc, ranges)
    except BaseException:
        try:
            if ds is not None:
                ds.close()
        except Exception:                                        # noqa: BLE001
            pass
        _remove(tmp)
        raise
    finally:
        if not keep_downloads:
            _prune(folder)
    step(100, "Done.")

    if arrived:
        say("{} arrived in the folder while this run was under way, fetched by another run that shares the "
            "folder, and {} not downloaded again.".format(
                "One grid" if arrived == 1 else "{} grids".format(n0(arrived)), "was" if arrived == 1 else "were"))
    short = [wanted[i].code for i in range(V) if issued_count[i] < used]
    if short:
        warn("The file's last dates hold no values for {}, which PRISM had not issued: those cells are no-data "
             "there.".format(" and ".join(short)))
    n_prov = int(provisional[:used].sum())
    if n_prov:
        warn("{} of the {} time steps {} less than six months old. PRISM reissues such grids as more station "
             "reports arrive, and calls them stable after six months: run the tool again later to bring them up "
             "to date. They are marked 1 in the file's \"provisional\" variable.".format(
                 n0(n_prov), n0(used), "was" if n_prov == 1 else "were"))
    return {
        "path": out_nc, "variables": [v.code for v in wanted], "periods": used, "rows": window.rows,
        "cols": window.cols, "bytes": os.path.getsize(out_nc), "downloaded": fetched, "refreshed": refreshed,
        "reused": reused, "arrived": arrived, "deleted": deleted, "downloaded_bytes": fetched_bytes,
        "missing": missing,
        "provisional": n_prov,
        "folder": folder, "folder_how": folder_how, "remembered": bool(is_remembered and keep_downloads),
        "kept": bool(keep_downloads), "citation": cite, "window": window,
        "first": periods[0].words(), "last": periods[used - 1].words(), "seconds": time.time() - t_start,
        "ranges": ranges, "time_step": time_step, "even_steps": even_steps(periods[:used]),
        "dataset": dataset, "resolution": resolution, "run_date": today.isoformat(), "area_words": area_words,
    }


def value_range(seen):
    """The least and greatest value, the mean and the standard deviation of a variable over all its time
    steps, from the running totals kept while the file was written. None when it held no value at all."""
    n = seen.get("n") or 0
    if n <= 0:
        return None
    mean = seen["sum"] / n
    spread = max(0.0, seen["sum2"] / n - mean * mean) ** 0.5
    return {"min": seen["min"], "max": seen["max"], "mean": mean, "std": spread, "n": n}


def statistics_path(out_nc):
    return out_nc + ".aux.xml"


def statistics_text(ranges):
    """The text of the statistics file ArcGIS Pro reads beside a netCDF file. Pro (3.7) takes a netCDF layer's
    color stretch from the statistics of the variable's FIRST time step, which it works out when a layer is
    first made and keeps in <file>.aux.xml under the name "Sliced view of /<variable> ([0])". Written here
    first, with each variable's values over ALL its time steps, the same entry gives every layer made from
    the file a stretch that holds every date, so one date can be compared with another. If a later Pro stops
    reading the entry, it works the first step's statistics out again, as before."""
    lines = ["<PAMDataset>"]
    for code in sorted(ranges):
        r = ranges[code]
        lines += ['  <Array name="Sliced view of /{} ([0])">'.format(code),
                  "    <Statistics>",
                  "      <ApproxStats>1</ApproxStats>",
                  "      <Minimum>{!r}</Minimum>".format(float(r["min"])),
                  "      <Maximum>{!r}</Maximum>".format(float(r["max"])),
                  "      <Mean>{!r}</Mean>".format(float(r["mean"])),
                  "      <StdDev>{!r}</StdDev>".format(float(r["std"])),
                  "      <ValidSampleCount>{}</ValidSampleCount>".format(int(r["n"])),
                  "    </Statistics>",
                  "  </Array>"]
    lines.append("</PAMDataset>")
    return "\n".join(lines) + "\n"


def write_statistics(out_nc, ranges):
    """Writes the statistics file beside the netCDF file (see ``statistics_text``), in place of any left by
    an earlier file of the same name. Returns its path, or None. Never raises: the file is a courtesy."""
    path = statistics_path(out_nc)
    _remove(path)
    if not ranges:
        return None
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(statistics_text(ranges))
        return path
    except OSError:
        return None


def even_steps(periods):
    """True when every time step begins where the one before it ended: no month, day or year is left out
    between the first and the last. ArcGIS Pro's time slider steps by one fixed step, so it lands on every
    time step of such a file and misses the steps of a file with gaps (June to August of every year, say)."""
    return all(periods[k].end == periods[k + 1].start for k in range(len(periods) - 1))


def service_pause(service):
    try:
        return float(service.pause)
    except (AttributeError, TypeError, ValueError):
        return PAUSE


def not_kept_notes(out):
    """What a run that did not keep its downloads says about the files: which it deleted, and that the ones
    that were in the folder before it began are still there."""
    n = out.get("deleted", 0)
    others = out.get("reused", 0) + out.get("refreshed", 0)      # grids this run read and did not add
    if n:
        first = "{} this run downloaded {} deleted when {} cells had been read.".format(
            "The one grid" if n == 1 else "The {} grids".format(n0(n)), "was" if n == 1 else "were",
            "its" if n == 1 else "their")
    else:
        first = "This run deleted no files."
    if others <= 0:
        return [first]
    return [first + " The grids that were already in {} are still there: a run never deletes a file that was "
            "in the folder before it began. To delete them, use Windows File Explorer.".format(out["folder"])]


def _prune(folder):
    """Removes the folders of the kept files that are now empty, and the folder itself if it is."""
    try:
        for root, _dirs, _files in os.walk(folder, topdown=False):
            if not os.listdir(root):
                os.rmdir(root)
    except OSError:
        pass


# ---------------------------------------------------------------------------
#  the run, in ArcGIS Pro
# ---------------------------------------------------------------------------
def study_area_box(layer=None, extent=None):
    """The rectangle (west, south, east, north) on NAD 83 that holds a layer's features (points, lines or
    polygons; its selection and definition query are honored) or an extent."""
    import arcpy
    import clip_tools

    nad83 = arcpy.SpatialReference(4269)
    geoms = []
    if layer is not None:
        with arcpy.da.SearchCursor(layer, ["SHAPE@"]) as cur:
            for (g,) in cur:
                if g is not None and g.pointCount > 0:
                    geoms.append(g)
        if not geoms:
            raise PrismError("The study area has no features to use.")
    elif extent is not None:
        sr = getattr(extent, "spatialReference", None)
        if sr is None or getattr(sr, "name", "") in ("", "Unknown"):
            if not (-180 <= extent.XMin <= 180 and -180 <= extent.XMax <= 180
                    and -90 <= extent.YMin <= 90 and -90 <= extent.YMax <= 90):
                raise PrismError("The rectangle has no coordinate system and its numbers are not degrees of "
                                 "longitude and latitude.")
            sr = nad83
        geoms.append(arcpy.Polygon(arcpy.Array([
            arcpy.Point(extent.XMin, extent.YMin), arcpy.Point(extent.XMin, extent.YMax),
            arcpy.Point(extent.XMax, extent.YMax), arcpy.Point(extent.XMax, extent.YMin),
            arcpy.Point(extent.XMin, extent.YMin)]), sr))
    else:
        raise PrismError("Give a study area: a layer, or a rectangle.")

    xs, ys = [], []
    for g in geoms:
        sr = g.spatialReference
        try:
            # straighten true curves, and add vertices along long straight edges so that they stay in
            # place when the shape is unprojected
            if g.type in ("polygon", "polyline"):
                ext = g.extent
                size = max(ext.width, ext.height) / 200.0
                projected = sr is not None and getattr(sr, "type", "") == "Projected"
                if size > 0 and (getattr(g, "hasCurves", False) or (projected and g.pointCount < 400)):
                    import wildlife_geom as _wg
                    g = _wg.densify_curves(g, size)
        except Exception:                                        # noqa: BLE001
            pass
        if sr is not None and getattr(sr, "name", "") not in ("", "Unknown") and sr.factoryCode != 4269:
            g = clip_tools.project_geometry(g, nad83)
        if g is None or g.pointCount == 0:
            continue
        e = g.extent
        xs += [e.XMin, e.XMax]
        ys += [e.YMin, e.YMax]
    if not xs:
        raise PrismError("The study area has no features to use.")
    return (min(xs), min(ys), max(xs), max(ys))


def run(out_nc, **kwargs):
    """See ``_run``. Returns the dict of ``build``."""
    import arcpy

    try:
        auto = arcpy.env.autoCancelling
    except Exception:                                            # noqa: BLE001
        auto = None
    try:
        if auto is not None:
            arcpy.env.autoCancelling = False
        return _run(out_nc, **kwargs)
    finally:
        if auto is not None:
            try:
                arcpy.env.autoCancelling = auto
            except Exception:                                    # noqa: BLE001
                pass


def _run(out_nc, study_area=None, extent=None, messages=None, area_words=None, **choices):
    import arcpy

    def say(text):
        if messages is not None:
            messages.addMessage(text)

    def warn(text):
        if messages is not None:
            messages.addWarningMessage(text)

    def step(percent, text):
        try:
            arcpy.SetProgressor("step", text, 0, 100, 1)
            arcpy.SetProgressorPosition(int(max(0, min(100, percent))))
        except Exception:                                        # noqa: BLE001
            pass

    def cancelled():
        try:
            return bool(arcpy.env.isCancelled)
        except Exception:                                        # noqa: BLE001
            return False

    why = check_output(out_nc)
    if why:
        raise PrismError(why)
    box = study_area_box(study_area, extent)
    out = build(out_nc, box, say=say, warn=warn, step=step, cancelled=cancelled, area_words=area_words,
                **choices)

    say("Wrote {}: {} for {} {} ({} to {}) on {} by {} cells, {}.".format(
        out["path"], "; ".join(out["variables"]), n0(out["periods"]),
        "time step" if out["periods"] == 1 else "time steps", out["first"], out["last"],
        n0(out["cols"]), n0(out["rows"]), size_words(out["bytes"])))
    got = out["downloaded"] + out["refreshed"]
    if got:
        say("Downloaded {} ({}){}; {} already there.".format(
            "one grid" if got == 1 else "{} grids".format(n0(got)), size_words(out["downloaded_bytes"]),
            "" if not out["refreshed"] else ", {} of them to replace a grid PRISM had reissued".format(
                n0(out["refreshed"])),
            "none was" if out["reused"] == 0 else "one was" if out["reused"] == 1
            else "{} were".format(n0(out["reused"]))))
    else:
        say("Nothing was downloaded: every grid was already in {}.".format(out["folder"]))
    if out["kept"]:
        say("The downloaded files are kept in {}, where a later run will find them{}. They are whole-country "
            "grids; the folder can be deleted when no more runs are planned.".format(
                out["folder"],
                ", in this project or any other: the tool remembers the folder" if out.get("remembered")
                else ", in this project or any other" if out.get("folder_how") == HOW_DEFAULT else ""))
    else:
        for line in not_kept_notes(out):
            say(line)
    for line in viewing_notes(out):
        say(line)
    say("When the data are used, PRISM asks that they be cited with its name, its web address and the date of "
        "access: " + out["citation"])
    say(CAUTION)
    stamp_metadata(out, messages)
    return out


def viewing_notes(out):
    """What to say, after a run, about seeing the file in a map: how to step through its dates, what the
    colors of a layer are stretched over, and how to add the file to another map."""
    notes = []
    one = {STEP_MONTHLY: "month", STEP_DAILY: "day", STEP_ANNUAL: "year"}.get(out.get("time_step"), "time step")
    if (out.get("periods") or 0) > 1:
        text = ("A layer shows one time step at a time. To step through the dates, click the layer in the "
                "Contents pane and use the Multidimensional tab: the StdTime list, or the step and play "
                "buttons.")
        if out.get("even_steps", True):
            # Seen in ArcGIS Pro 3.7 (Jeff, 2026-11-22): the time slider opens with a span one step long
            # that ENDS on the first date, and a layer is drawn only when the span STARTS exactly on one of
            # its dates. The span keeps its length in days, so with months it drifts off the first of the
            # month. With the span at 0 the slider holds one date and steps by the layer's step.
            # The add-in closes the span itself when layers of a file of this tool are added to a map that
            # has no other layer that uses time (OnlineData/Prism/PrismTimeSlider.cs).
            text += (" The map's time slider moves the layers too, when it holds a single date: it then steps "
                     "from one {0} to the next. ArcGIS Pro opens the time slider with a span one {0} long, and "
                     "draws a layer only when the span starts exactly on one of the file's dates, so this "
                     "add-in closes the span to a single date when the layers arrive in a map that has no "
                     "other layer that uses time. If the map is empty, or the layers come and go as the "
                     "slider moves, close it yourself: on the Time tab, in the Current Time group, set Span "
                     "to 0.".format(one))
        else:
            text += (" The time steps of this file are not evenly spaced, because some {}s are left out, so "
                     "the map's time slider, which moves by one fixed step, will not land on them.".format(one))
        notes.append(text)
        if out.get("ranges"):
            notes.append("The colors of each layer are stretched over the variable's values at all the time "
                         "steps, so that one date can be compared with another: " + "; ".join(
                             "{} {:,.1f} to {:,.1f} {}".format(
                                 code, r["min"], r["max"], BY_CODE[code].units if code in BY_CODE else "").strip()
                             for code, r in sorted(out["ranges"].items())) + ".")
    # The names are those of ArcGIS Pro 3.7.3 (Jeff's capture, 22 November 2026): the menu choice reads
    # "Multidimensional Data..." and the window "Add Multidimensional Data". LOOK AGAIN AFTER A PRO UPGRADE.
    notes.append("To add the file to another map: on the Map tab, click the arrow under Add Data and choose "
                 "Multidimensional Data. In the window that opens, click the folder button and pick the file, "
                 "check the variables to show, and click OK.")
    notes.append("The notes that go with the data (what a value of each variable is, the dates, the grids less "
                 "than six months old, how to cite PRISM and the caution below) are in the metadata: "
                 "right-click a layer in the Contents pane and choose View Metadata. A layer's properties "
                 "show the note of its variable too: on the Source tab, under Multidimensional Info.")
    return notes


def metadata_parts(out):
    """The metadata that goes with the file, as a dict: title, tags, summary, description, credits and
    accessConstraints (use limitations). The description holds EVERY note that is in the file itself (what a
    value of each variable is, the caution about annual grids, the dates, the grids less than six months
    old, the cells' edges and areas, the caution about change over time, how to cite PRISM), because the
    metadata is where a reader looks first and ArcGIS Pro shows the notes in the file only in part. The
    summary says how this file was made. Needs no arcpy."""
    w = out["window"]
    step = out.get("time_step")
    T = out["periods"]
    one = {STEP_MONTHLY: "month", STEP_DAILY: "day", STEP_ANNUAL: "year"}.get(step, "time step")
    para = lambda head, text: "<p><b>{}</b> {}</p>".format(G.html_text(head), G.html_text(text))    # noqa: E731

    summary = "PRISM {}grids at {}{}: {} for {}, cut from PRISM's grid to {} by {} cells".format(
        (step.lower() + " ") if step else "", out.get("resolution") or w.resolution,
        ", " + out["dataset"] if out.get("dataset") else "", "; ".join(out["variables"]),
        "one time step ({})".format(out["first"]) if T == 1
        else "{} time steps ({} to {})".format(n0(T), out["first"], out["last"]),
        n0(w.cols), n0(w.rows))
    summary += " ({:.4f} to {:.4f} degrees of longitude, {:.4f} to {:.4f} of latitude{}).".format(
        w.west, w.east, w.south, w.north,
        ", around " + out["area_words"] if out.get("area_words") else "")
    summary += " Written{} by the PRISM Climate Data tool of Wildlife and Forestry Tools for ArcGIS Pro.".format(
        " on " + out["run_date"] if out.get("run_date") else "")

    items = []
    for code in out["variables"]:
        v = BY_CODE.get(code)
        if v is None:
            continue
        r = (out.get("ranges") or {}).get(code)
        items.append("<b>{}</b>, in {}. {}{}".format(
            G.html_text(v.label), G.html_text(v.units), G.html_text(variable_note(v, step, lead=False)),
            "" if not r else G.html_text(" In this file its values run from {:,.1f} to {:,.1f}.".format(
                r["min"], r["max"]))))
    n_prov = out.get("provisional") or 0
    if n_prov:
        young = ("{} of the {} was less than six months old when its grid was fetched{}. ".format(
            "One" if n_prov == 1 else n0(n_prov),
            "time step" if T == 1 else "{} time steps".format(n0(T)),
            " (" + out["run_date"] + ")" if out.get("run_date") else "")
            if n_prov == 1 else
            "{} of the {} time steps were less than six months old when their grids were fetched{}. ".format(
                n0(n_prov), n0(T), " (" + out["run_date"] + ")" if out.get("run_date") else ""))
        young += ("PRISM reissues a grid several times in its first six months, as more station reports arrive, "
                  "and calls it stable after that. The variable named provisional marks those time steps "
                  "with 1. Run the tool again later, with the same settings, to bring them up to date.")
    else:
        young = ("No time step of this file was less than six months old when its grid was fetched, so the "
                 "variable named provisional is 0 throughout. (PRISM reissues a grid several times in its "
                 "first six months and calls it stable after that.)")
    missing = out.get("missing") or []

    description = (
        "<p>" + G.html_text(
            "Grids of the PRISM Group, Oregon State University, fetched from PRISM's web service by the "
            "PRISM Climate Data tool of Wildlife and Forestry Tools and written to one netCDF file with a "
            "time dimension. The cells are PRISM's own, not resampled.") + "</p>"
        + "<p><b>The variables.</b> " + G.html_text(
            "Each is one value for every cell at every time step. No-data cells hold -9999, which is PRISM's "
            "own no-data value.") + "</p>" + G.html_list(items)
        + (para("Annual grids.", ANNUAL_NOTE) if step == STEP_ANNUAL else "")
        + para("The dates.", "{}. {}{}".format(
            "One time step: {}".format(out["first"]) if T == 1
            else "{} time steps, one for each {}, from {} to {}".format(n0(T), one, out["first"], out["last"]),
            TIME_NOTE, " " + PRISM_DAY_NOTE if step == STEP_DAILY else ""))
        + para("Grids less than six months old.", young)
        + (para("Dates PRISM had not issued.", "PRISM had not yet issued: {}. The file stops at the last date "
                "for which it had a grid.".format("; ".join("{} for {}".format(c, when) for c, when in missing)))
           if missing else "")
        + para("Also in the file.", "The edges of every cell (lat_bnds, lon_bnds) and its true area in square "
               "kilometers (cell_area). " + AREA_WHY + " For each climate variable, a second variable (such "
               "as ppt_prism_created) holds the day PRISM made the grid of each time step.")
        + "<p>" + G.html_text(CAUTION) + "</p>"
        + para("How to cite.", "PRISM asks that any description of the use of its data state its name, its web "
               "address and the date of access: " + out["citation"])
        + para("Where else these notes are.", "They are inside the netCDF file too, where any program that reads "
               "netCDF finds them. In ArcGIS Pro, the properties of a layer of the file show the note of its "
               "variable and of its dates: on the Source tab, under Multidimensional Info.")
        + "<p><b>Links to the source:</b></p>" + G.html_list([
            "PRISM: " + G.html_link(SITE),
            "PRISM's terms of use: " + G.html_link(TERMS),
            "PRISM's description of its datasets: " + G.html_link(DATASETS_DOC)]))
    return {
        "title": os.path.basename(out["path"]),
        "tags": "PRISM, climate, precipitation, temperature, netCDF, multidimensional, Wildlife and Forestry Tools",
        "summary": summary,
        "description": description,
        "credits": out["citation"],
        "accessConstraints": "<p>" + G.html_text(
            "PRISM's terms of use say its data may be freely reproduced and distributed, and ask that any "
            "description of their use state PRISM's name, its web address and the date of access. ")
        + G.html_link(TERMS) + "</p>",
    }


def stamp_metadata(out, messages=None):
    """Title, tags, summary, description, credits and use limitations beside the file (<file>.nc.xml). Never
    raises: metadata is a courtesy, not the result. The add-in gives the same metadata to the layers of the
    file as they arrive in a map (OnlineData/Prism/PrismLayerMetadata.cs): a layer of a netCDF variable
    otherwise shows none."""
    try:
        import arcpy

        parts = metadata_parts(out)
        md = arcpy.metadata.Metadata(out["path"])
        md.title = parts["title"]
        md.tags = parts["tags"]
        md.summary = parts["summary"]
        md.description = parts["description"]
        md.credits = parts["credits"]
        md.accessConstraints = parts["accessConstraints"]
        md.save()
    except Exception as e:                                       # noqa: BLE001
        if messages is not None:
            try:
                messages.addWarningMessage("Could not write the output's metadata: {}".format(e))
            except Exception:                                    # noqa: BLE001
                pass
