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

"""Fire severity from three programs of the United States Government:
MTBS, RAVG and BAER. One engine for the three tools.

  MTBS  Monitoring Trends in Burn Severity (USGS and USDA Forest
        Service): every large fire since 1984, six thematic classes
        that an analyst sets for each fire from dNBR.
  RAVG  Rapid Assessment of Vegetation Condition after Wildfire (USDA
        Forest Service): percent basal area loss, percent canopy cover
        loss and the composite burn index, modelled from RdNBR.
  BAER  Burned Area Emergency Response imagery support: soil burn
        severity, checked in the field by the BAER team.

What a program can do without an account, and what this engine does:

  * FIND fires by name, year and place in the catalog the Burn Severity
    Portal's own viewer uses (a USGS GeoServer feature service), and
    write their perimeters.
  * FETCH each fire's CLASSIFIED raster from the public map services,
    cut to the fire's perimeter. These rasters are the services' Web
    Mercator copies of the programs' national mosaics: right for
    looking and for overlay, not the original cells of the fire's own
    data bundle.
  * REQUEST the fires' DATA BUNDLES (the continuous dNBR and RdNBR, the
    images, the metadata). The portal prepares bundles and emails a
    link; nothing comes back to this tool.

Only the Python standard library, arcpy, numpy and GDAL are used.
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

import gbif_occurrences as G

GEOSERVER = "https://edcintl.cr.usgs.gov/geoserver/"
CATALOG = "mtbs:fire_polygons"
IMAGERY = "https://imagery.geoplatform.gov/iipp/rest/services/Fire_Aviation/"
QUEUE = "https://burnseverity.cr.usgs.gov/downloads/addQueue.php"
PORTAL = "https://burnseverity.cr.usgs.gov/"
USER_AGENT = "WildlifeTools-AddIn (Jenness Enterprises; ArcGIS Pro)"
PAUSE = 0.5
PAGE = 500
MAX_BUNDLE_FIRES = 500              # the portal's own limit for one request
MANY_RASTERS = 100                  # above this the run says how long it will take
IDS_PER_REQUEST = 100               # more fire IDs than this in one request make a web address the catalog's
#                                     server refuses (HTTP 414 at about 8,000 characters; 100 IDs are 3,500)
MAX_FAILS = 5                       # this many raster requests failing in a row: the service is down, stop asking

n0 = G.n0
span = G.span
Cancelled = G.Cancelled


class FireError(Exception):
    """A failure the user can act on; shown without a traceback. ``status``
    is the HTTP status when a service refused a request (None otherwise),
    and ``detail`` the start of what it sent with the refusal."""

    def __init__(self, message, status=None, detail=""):
        Exception.__init__(self, message)
        self.status = status
        self.detail = detail


def _c(value, name, r, g, b, mask=False):
    """A class of a classified raster. ``mask`` marks cells the program could not assess (cloud,
    smoke, developed land): they are named and measured, and left out of the percentages."""
    return {"value": value, "name": name, "color": [r, g, b], "mask": mask}


# The classes of each classified raster. Colors follow the programs' own legends.
MTBS_CLASSES = [
    _c(1, "Unburned to Low", 0, 100, 0), _c(2, "Low", 127, 255, 212), _c(3, "Moderate", 255, 255, 0),
    _c(4, "High", 255, 0, 0), _c(5, "Increased Greenness", 127, 255, 0),
    _c(6, "Non-Processing Area Mask", 255, 255, 255, True)]
BA7_CLASSES = [
    _c(1, "0% basal area loss", 0, 100, 0), _c(2, ">0% to <10%", 85, 170, 0), _c(3, "10% to <25%", 170, 213, 0),
    _c(4, "25% to <50%", 255, 255, 0), _c(5, "50% to <75%", 255, 170, 0), _c(6, "75% to <90%", 255, 85, 0),
    _c(7, "90% to 100%", 200, 0, 0), _c(9, "Masked", 200, 200, 200, True)]
CC5_CLASSES = [
    _c(1, "0% canopy cover loss", 0, 100, 0), _c(2, ">0% to <25%", 150, 200, 0), _c(3, "25% to <50%", 255, 255, 0),
    _c(4, "50% to <75%", 255, 140, 0), _c(5, "75% to 100%", 200, 0, 0), _c(9, "Masked", 200, 200, 200, True)]
CBI4_CLASSES = [
    _c(1, "Unchanged (CBI 0 to <0.1)", 0, 100, 0), _c(2, "Low (CBI 0.1 to <1.25)", 127, 255, 212),
    _c(3, "Moderate (CBI 1.25 to <2.25)", 255, 255, 0), _c(4, "High (CBI 2.25 to 3.0)", 255, 0, 0),
    _c(9, "Masked", 200, 200, 200, True)]
SBS_CLASSES = [
    _c(1, "Unburned to very low", 0, 100, 0), _c(2, "Low", 127, 255, 212), _c(3, "Moderate", 255, 255, 0),
    _c(4, "High", 255, 0, 0), _c(5, "Masked (developed)", 200, 200, 200, True)]

PRODUCT_MTBS = "Thematic burn severity (6 classes)"
PRODUCT_BA7 = "Basal area loss (7 classes)"
PRODUCT_CC5 = "Canopy cover loss (5 classes)"
PRODUCT_CBI4 = "Composite burn index (4 classes)"
PRODUCT_SBS = "Soil burn severity (4 classes)"

# program -> what it is, and its classified products:
#   label -> (short name for file names, classes, how to fetch)
PROGRAMS = {
    "MTBS": {
        "name": "Monitoring Trends in Burn Severity",
        "agency": "U.S. Geological Survey and USDA Forest Service",
        "home": "https://www.mtbs.gov/",
        "products": {PRODUCT_MTBS: ("severity", MTBS_CLASSES, ("wcs", None))},
        "bundle_products": ["Burned area boundary", "6 - Class thematic severity", "Continuous severity (i.e dnbr)",
                            "Relative continuous severity (i.e rdnbr)", "Non-processing mask"],
    },
    "RAVG": {
        "name": "Rapid Assessment of Vegetation Condition after Wildfire",
        "agency": "USDA Forest Service",
        "home": PORTAL + "ravg/",
        "products": {
            PRODUCT_BA7: ("ba7", BA7_CLASSES, ("image", "USFS_EDW_RAVG_BasalAreaPercentChange")),
            PRODUCT_CC5: ("cc5", CC5_CLASSES, ("image", "USFS_EDW_RAVG_CanopyCoverPercentChange")),
            PRODUCT_CBI4: ("cbi4", CBI4_CLASSES, ("image", "USFS_EDW_RAVG_CompositeBurnIndex")),
        },
        "bundle_products": ["Burned area boundary", "7 - Class basal area loss", "5 - Class canopy cover loss",
                            "4 - Class composite burn index", "Continuous basal area loss",
                            "Continuous canopy cover loss", "Continuous composite burn index",
                            "Continuous severity (i.e dnbr)", "Relative continuous severity (i.e rdnbr)",
                            "Non-processing mask"],
    },
    "BAER": {
        "name": "Burned Area Emergency Response imagery support",
        "agency": "USDA Forest Service and U.S. Geological Survey",
        "home": PORTAL + "baer/",
        "products": {PRODUCT_SBS: ("sbs", SBS_CLASSES, ("image", "USFS_EDW_BAER_SoilBurnSeverityClassification"))},
        "bundle_products": ["Burned area boundary", "Soil burn severity", "Continuous severity (i.e dnbr)",
                            "Non-processing mask"],
    },
}
BUNDLE_IMAGES = ["Pre-fire reflectance", "Post-fire reflectance"]
PROJECTIONS = ["UTM", "Albers"]

# the perimeter feature class: (field, alias, type, length, catalog property, description)
FIELDS = [
    ("program", "Program", "TEXT", 20, "map_prog", "The mapping program that made this assessment: MTBS, RAVG or BAER."),
    ("fire_name", "Fire name", "TEXT", 120, "incid_name", "The name of the fire as the program records it."),
    ("fire_id", "Fire ID", "TEXT", 40, "event_id",
     "The program's identifier of the fire: the state, the latitude and longitude of the fire's center and the "
     "ignition date. The same fire has the same identifier in every program."),
    ("ignition_date", "Ignition date", "DATE", None, "ig_date", "The date the fire started, as the program records it."),
    ("fire_year", "Year", "LONG", None, None, "The year of the ignition date."),
    ("fire_type", "Fire type", "TEXT", 40, "incid_type",
     "Wildfire, Prescribed Fire, Wildland Fire Use, Unknown or Other, as the program records it."),
    ("assessment", "Assessment type", "TEXT", 40, "asmt_type",
     "Initial (from an image taken soon after the fire), Extended (from an image taken at the peak of the next "
     "growing season) or Emergency (a BAER assessment made within days)."),
    ("acres", "Acres", "DOUBLE", None, "burnbndac", "The area inside the mapped perimeter, in acres, as the program reports it."),
    ("center_lat", "Center latitude", "DOUBLE", None, "burnbndlat", "Latitude of the center of the burned area."),
    ("center_lon", "Center longitude", "DOUBLE", None, "burnbndlon", "Longitude of the center of the burned area."),
    ("postfire_date", "Post-fire image date", "DATE", None, "postfire_date",
     "The date of the satellite image taken after the fire, where the catalog gives it."),
    ("dnbr_offset", "dNBR offset", "LONG", None, "dnbr_offst",
     "The mean dNBR of unburned land outside the fire, which the analyst subtracts so that unchanged land reads zero."),
    ("dnbr_stddev", "dNBR standard deviation", "LONG", None, "dnbr_stddv",
     "The standard deviation of dNBR over that unburned land."),
    ("greenness_t", "Increased greenness threshold", "LONG", None, "incgreen_t",
     "The dNBR value below which the analyst classed a cell as increased greenness."),
    ("low_t", "Low threshold", "LONG", None, "low_t",
     "The dNBR value at which the analyst set the start of the low class for this fire."),
    # In the portal's all-programs catalog the two columns are the other way round: mod_t holds the
    # start of the HIGH class and high_t the start of the MODERATE class (checked against MTBS's own
    # layer for six fires, 2026-11-15), so they are read crosswise here.
    ("moderate_t", "Moderate threshold", "LONG", None, "high_t",
     "The dNBR value at which the analyst set the start of the moderate class for this fire."),
    ("high_t", "High threshold", "LONG", None, "mod_t",
     "The dNBR value at which the analyst set the start of the high class for this fire."),
    ("comment", "Comment", "TEXT", 255, "comment", "The analyst's comment on the assessment."),
    ("map_id", "Map ID", "LONG", None, "map_id", "The portal's number for this assessment."),
    ("irwin_id", "IRWIN ID", "TEXT", 60, "irwinid", "The fire's identifier in the interagency IRWIN system, where it has one."),
    ("portal_url", "On the Burn Severity Portal", "TEXT", 200, None,
     "The Burn Severity Portal's interactive viewer for this program, where the fire's data bundle can be asked for."),
    ("raster", "Classified raster", "TEXT", 1000, None,
     "The classified raster or rasters this tool fetched for the fire, separated by semicolons; empty when none was asked for or none exists."),
]


# ---------------------------------------------------------------------------
#  talking to the services
# ---------------------------------------------------------------------------
_last = [0.0]


def _open(url, data=None, timeout=180, headers=None):
    wait = PAUSE - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read(4000).decode("utf-8", "replace")
        except Exception:                                        # noqa: BLE001
            detail = ""
        raise FireError("The service returned an error ({}) for {}.".format(e.code, url.split("?")[0]),
                        e.code, detail)
    except (urllib.error.URLError, OSError) as e:
        raise FireError("The service at {} could not be reached ({}). Check the internet connection, or try "
                        "again in a few minutes.".format(urllib.parse.urlsplit(url).netloc, getattr(e, "reason", e)))


def _read(url, data=None, timeout=180, headers=None, tries=3):
    """The body and headers of a response. A request that failed for a
    reason that may pass (the network, a 5xx, 408 or 429) is repeated up to
    ``tries`` times; any other refusal is raised at once."""
    last = None
    for attempt in range(tries):
        try:
            resp = _open(url, data=data, timeout=timeout, headers=headers)
            try:
                return resp.read(), dict(resp.headers)
            finally:
                resp.close()
        except FireError as e:
            last = e
            if e.status is not None and 400 <= e.status < 500 and e.status not in (408, 429):
                raise
        except (http.client.HTTPException, OSError) as e:        # the answer broke off part way
            last = FireError("The service at {} stopped answering part way ({}). Try again in a few "
                             "minutes.".format(urllib.parse.urlsplit(url).netloc, e))
        if attempt + 1 < tries:
            time.sleep(2 + 3 * attempt)
    raise last


def _quote(text):
    return str(text).replace("'", "''")


def catalog_filter(program, name=None, year_from=None, year_to=None, box=None, fire_ids=None):
    """The CQL filter of a search of the catalog."""
    parts = ["map_prog='{}'".format(_quote(program))]
    if fire_ids:
        parts.append("event_id IN ({})".format(", ".join("'{}'".format(_quote(i)) for i in fire_ids)))
    if name and str(name).strip():
        words = [w for w in re.split(r"\s*;\s*", str(name).strip()) if w]
        parts.append("(" + " OR ".join("incid_name ILIKE '%{}%'".format(_quote(w)) for w in words) + ")")
    if year_from:
        parts.append("ig_date >= '{:04d}-01-01'".format(int(year_from)))
    if year_to:
        parts.append("ig_date <= '{:04d}-12-31'".format(int(year_to)))
    if box:
        parts.append("BBOX(geom,{:.6f},{:.6f},{:.6f},{:.6f},'EPSG:4326')".format(*box))
    return " AND ".join(parts)


def split_fire_ids(text):
    """(fire IDs, {fire ID: set of map IDs}) from the Fire IDs text. An
    entry is a fire's event identifier, which stands for every assessment of
    the fire, or an identifier, a colon and a map ID
    (AZ3525811164320190721:10016328), which stands for that one assessment.
    A fire named both ways has all its assessments."""
    ids, maps, whole = [], {}, set()
    for entry in G.split_list(text or ""):
        event, sep, map_id = entry.partition(":")
        event, map_id = event.strip().upper(), map_id.strip()
        if not event:
            continue
        if event not in maps and event not in whole:
            ids.append(event)
        if sep and map_id:
            if not re.match(r"^\d+$", map_id):
                raise FireError("The fire ID entry \"{}\" could not be read. Give a fire ID, or a fire ID, a colon "
                                "and a map ID, such as AZ3525811164320190721:10016328.".format(entry))
            maps.setdefault(event, set()).add(int(map_id))
        else:
            whole.add(event)
            maps.setdefault(event, set())
    return ids, dict((e, m) for e, m in maps.items() if e not in whole and m)


def _id_batches(criteria):
    """The criteria of a search as one or more sets of criteria, none of
    which names more than IDS_PER_REQUEST fire IDs."""
    ids = list(dict.fromkeys(criteria.get("fire_ids") or []))
    if len(ids) <= IDS_PER_REQUEST:
        return [dict(criteria, fire_ids=ids or None)]
    return [dict(criteria, fire_ids=ids[k:k + IDS_PER_REQUEST]) for k in range(0, len(ids), IDS_PER_REQUEST)]


def _catalog_order(fire):
    """Oldest first, then in the catalog's own order: what sortBy=ig_date,id gives."""
    p = fire.get("properties") or {}
    tail = re.search(r"(\d+)$", str(p.get("id") if p.get("id") is not None else fire.get("id") or ""))
    return str(p.get("ig_date") or ""), int(tail.group(1)) if tail else 0


def count(program, **criteria):
    """How many fires of the program match."""
    total = 0
    for batch in _id_batches(criteria):
        url = (GEOSERVER + "wfs?service=WFS&version=2.0.0&request=GetFeature&typeNames=" + CATALOG
               + "&resultType=hits&cql_filter=" + urllib.parse.quote(catalog_filter(program, **batch)))
        body, _h = _read(url)
        m = re.search(r'numberMatched="(\d+)"', body.decode("utf-8", "replace"))
        if not m:
            raise FireError("The fire catalog did not answer as expected.")
        total += int(m.group(1))
    return total


def search(program, cancelled=None, with_geometry=True, limit=None, **criteria):
    """The fires of the program that match, as GeoJSON features (WGS 84),
    oldest first. A long list of fire IDs is asked for in several
    requests."""
    batches = _id_batches(criteria)
    if len(batches) == 1:
        return _search(program, cancelled, with_geometry, limit, **batches[0])
    fires = []
    for batch in batches:
        fires.extend(_search(program, cancelled, with_geometry, None, **batch))
    fires.sort(key=_catalog_order)
    return fires[:limit] if limit else fires


def _search(program, cancelled=None, with_geometry=True, limit=None, **criteria):
    cql = catalog_filter(program, **criteria)
    fires = []
    start = 0
    props = "" if with_geometry else "&propertyName=" + ",".join(
        f[4] for f in FIELDS if f[4]) + ",nonstandard"
    while True:
        if cancelled is not None and cancelled():
            raise Cancelled()
        url = (GEOSERVER + "wfs?service=WFS&version=2.0.0&request=GetFeature&typeNames=" + CATALOG
               + "&outputFormat=application/json&srsName=EPSG:4326&sortBy=ig_date,id&count={}&startIndex={}".format(
                   PAGE, start) + props + "&cql_filter=" + urllib.parse.quote(cql))
        body, _h = _read(url, timeout=300)
        try:
            page = json.loads(body.decode("utf-8", "replace")).get("features") or []
        except ValueError:
            raise FireError("The fire catalog did not answer as expected.")
        fires.extend(page)
        start += len(page)
        if len(page) < PAGE or (limit and len(fires) >= limit):
            break
    return fires[:limit] if limit else fires


def year_of(fire):
    d = (fire.get("properties") or {}).get("ig_date") or ""
    m = re.match(r"(\d{4})", str(d))
    return int(m.group(1)) if m else None


def _date(text):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(text or ""))
    if not m:
        return None
    try:
        return datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def rings_of(fire):
    """[polygon][ring][(lon, lat)] of a catalog feature."""
    g = fire.get("geometry") or {}
    c = g.get("coordinates") or []
    if g.get("type") == "Polygon":
        c = [c]
    return [[[(float(p[0]), float(p[1])) for p in ring] for ring in poly] for poly in c]


def box_of(fire):
    xs = [p[0] for poly in rings_of(fire) for ring in poly for p in ring]
    ys = [p[1] for poly in rings_of(fire) for ring in poly for p in ring]
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def area_of(fire):
    """The area inside a catalog feature's perimeter, in square meters,
    on a sphere of the Earth's equal-area radius (good to a fraction of a
    percent, which is all the check of a raster's cover needs)."""
    r2 = 6371007.2 ** 2
    total = 0.0
    for poly in rings_of(fire):
        for k, ring in enumerate(poly):
            s = 0.0
            for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
                s += math.radians(x2 - x1) * (2.0 + math.sin(math.radians(y1)) + math.sin(math.radians(y2)))
            total += (1.0 if k == 0 else -1.0) * abs(s) * r2 / 2.0     # the first ring is the outline, the rest holes
    return total


PARTIAL = 0.5                       # a raster that covers less than this share of its fire is reported


# ---------------------------------------------------------------------------
#  the classified rasters
# ---------------------------------------------------------------------------
R_MERC = 6378137.0


def merc(lon, lat):
    lat = max(-85.0, min(85.0, lat))
    return (math.radians(lon) * R_MERC,
            math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0)) * R_MERC)


def region_of(fire):
    """The MTBS mosaic a fire is in: CONUS, AK, HI or PR."""
    p = fire.get("properties") or {}
    try:
        lat, lon = float(p.get("burnbndlat")), float(p.get("burnbndlon"))
    except (TypeError, ValueError):
        b = box_of(fire)
        lat, lon = (b[1] + b[3]) / 2.0, (b[0] + b[2]) / 2.0
    if lat > 50.0:
        return "AK"
    if lon < -150.0 and lat < 30.0:
        return "HI"
    if lon > -70.0 and lat < 20.0:
        return "PR"
    return "CONUS"


def _snap(box_m, cell=30.0, pad=1):
    """A Web Mercator rectangle grown to whole cells of the services' grid."""
    x0 = math.floor(box_m[0] / cell) * cell - pad * cell
    y0 = math.floor(box_m[1] / cell) * cell - pad * cell
    x1 = math.ceil(box_m[2] / cell) * cell + pad * cell
    y1 = math.ceil(box_m[3] / cell) * cell + pad * cell
    return x0, y0, x1, y1


def _is_tiff(body):
    return body[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")


def _refusal(body):
    """What a service said in place of a raster, shortened. (The image
    services answer some failures with HTTP 200, the content type of a
    TIFF, and a message.)"""
    text = body[:600].decode("utf-8", "replace").strip()
    try:
        err = json.loads(body.decode("utf-8", "replace"))
        err = err.get("error") if isinstance(err, dict) else None
        if isinstance(err, dict):
            text = "{} (code {})".format(str(err.get("message") or "").strip() or "no reason given", err.get("code"))
    except ValueError:
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()
    return text[:300] or "nothing"


def fetch_classes(fire, how, tif, cell=30.0, attempt=0):
    """Saves the classified cells in the rectangle around a fire as a
    GeoTIFF (Web Mercator, the service's own grid). False when the
    service has no raster for the fire's year; None when there is no
    such ``attempt``. MTBS has two sources: the U.S. Geological
    Survey's server (attempt 0), which lacks some years, and the Forest
    Service's image service of the same mosaics (attempt 1).

    A service that FAILS is not a service that has no raster: only the
    answers that mean "nothing here" return False (the map server's
    NoSuchCoverage and InvalidSubsetting; an image service answers such a
    request with an empty raster, which the caller finds). Any other
    refusal, and anything that is not a TIFF, raises FireError."""
    b = box_of(fire)
    year = year_of(fire)
    if b is None or year is None:
        return False
    x0, y0 = merc(b[0], b[1])
    x1, y1 = merc(b[2], b[3])
    kind, service = how
    where = "beginyear <= {0} AND endyear >= {0}".format(year)
    sort = "zorder"
    if kind == "wcs" and attempt == 1:
        kind, service = "image", "USFS_EDW_MTBS_" + region_of(fire)
        where, sort = "year = {}".format(year), "year"
    elif attempt > 0:
        return None
    if kind == "wcs":
        x0, y0, x1, y1 = _snap((x0, y0, x1, y1), 30.0)
        url = (GEOSERVER + "wcs?service=WCS&version=2.0.1&request=GetCoverage&coverageId=mtbs__mtbs_{}_{}"
               "&format=image/geotiff&subset=X({:.1f},{:.1f})&subset=Y({:.1f},{:.1f})".format(
                   region_of(fire), year, x0, x1, y0, y1))
        try:
            body, h = _read(url, timeout=600)
        except FireError as e:
            if e.status == 404 and ("NoSuchCoverage" in e.detail or "InvalidSubsetting" in e.detail):
                return False                      # no mosaic for that region and year, or the fire is outside it
            raise
        if not _is_tiff(body):
            raise FireError("The U.S. Geological Survey's map server sent no raster. It answered: {}".format(
                _refusal(body)))
    else:
        x0, y0, x1, y1 = _snap((x0, y0, x1, y1), cell)
        w, hgt = int(round((x1 - x0) / cell)), int(round((y1 - y0) / cell))
        q = {"bbox": "{:.1f},{:.1f},{:.1f},{:.1f}".format(x0, y0, x1, y1), "bboxSR": 3857, "imageSR": 3857,
             "size": "{},{}".format(w, hgt), "format": "tiff", "pixelType": "U8", "noData": 0,
             "interpolation": "RSP_NearestNeighbor", "renderingRule": json.dumps({"rasterFunction": "None"}),
             "mosaicRule": json.dumps({"mosaicMethod": "esriMosaicAttribute", "sortField": sort, "where": where}),
             "f": "image"}
        body, h = _read(IMAGERY + service + "/ImageServer/exportImage?" + urllib.parse.urlencode(q), timeout=600)
        if not _is_tiff(body):
            raise FireError("The image service {} sent no raster for a request of {} by {} cells. It answered: "
                            "{}".format(service, n0(w), n0(hgt), _refusal(body)))
    with open(tif, "wb") as f:
        f.write(body)
    return True


def cut_to_fire(src_tif, fire, out_tif, classes, keep_outside=False):
    """Writes the cells of ``src_tif`` that lie inside the fire's
    perimeter as a compressed GeoTIFF with a color table. Returns
    {class value: square meters on the ground} for the cells kept, or
    None when no burned-area cell is there. The raster is in Web
    Mercator, whose cells are not their nominal size on the ground: a
    cell at latitude phi covers (cell size times cos phi) squared, and
    the areas are summed row by row with each row's own latitude."""
    import numpy as np
    from osgeo import gdal, ogr, osr

    gdal.UseExceptions()
    ds = gdal.Open(src_tif)
    band = ds.GetRasterBand(1)
    a = band.ReadAsArray()
    gt = ds.GetGeoTransform()
    sr = osr.SpatialReference()
    sr.ImportFromEPSG(3857)
    if not keep_outside:
        mem = ogr.GetDriverByName("Memory" if ogr.GetDriverByName("Memory") else "MEM").CreateDataSource("fire")
        lyr = mem.CreateLayer("p", sr, ogr.wkbMultiPolygon)
        multi = ogr.Geometry(ogr.wkbMultiPolygon)
        for poly in rings_of(fire):
            g = ogr.Geometry(ogr.wkbPolygon)
            for ring in poly:
                r = ogr.Geometry(ogr.wkbLinearRing)
                for lon, lat in ring:
                    r.AddPoint_2D(*merc(lon, lat))
                r.CloseRings()
                g.AddGeometry(r)
            multi.AddGeometry(g)
        feat = ogr.Feature(lyr.GetLayerDefn())
        feat.SetGeometry(multi)
        lyr.CreateFeature(feat)
        mask_ds = gdal.GetDriverByName("MEM").Create("", ds.RasterXSize, ds.RasterYSize, 1, gdal.GDT_Byte)
        mask_ds.SetGeoTransform(gt)
        mask_ds.SetProjection(sr.ExportToWkt())
        gdal.RasterizeLayer(mask_ds, [1], lyr, burn_values=[1], options=["ALL_TOUCHED=TRUE"])
        inside = mask_ds.GetRasterBand(1).ReadAsArray() == 1
        a = np.where(inside, a, 0).astype(np.uint8)
    else:
        a = a.astype(np.uint8)
    known = set(c["value"] for c in classes)
    a[~np.isin(a, list(known))] = 0
    values = np.unique(a[a > 0])
    if values.size == 0:
        return None
    y_center = gt[3] + (np.arange(ds.RasterYSize) + 0.5) * gt[5]
    lat = 2.0 * np.arctan(np.exp(y_center / R_MERC)) - np.pi / 2.0
    row_m2 = abs(gt[1] * gt[5]) * np.cos(lat) ** 2                # ground area of one cell, by row
    areas = {}
    for v in values.tolist():
        areas[int(v)] = float(((a == v).sum(axis=1) * row_m2).sum())
    out = gdal.GetDriverByName("GTiff").Create(out_tif, ds.RasterXSize, ds.RasterYSize, 1, gdal.GDT_Byte,
                                               options=["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"])
    out.SetGeoTransform(gt)
    out.SetProjection(sr.ExportToWkt())
    ob = out.GetRasterBand(1)
    ob.SetNoDataValue(0)
    # NO color table: a Byte raster's color table has all 256 entries, so ArcGIS Pro would draw the
    # raster with Colormap symbology and list some 250 black boxes without names. The colors go into
    # the attribute table instead (name_classes), and Pro then draws Unique Values.
    ob.WriteArray(a)
    ob.FlushCache()
    out = None
    ds = None
    return areas


def drop_color_table(tif):
    """Rewrites a one-band GeoTIFF without its color table (and so as a
    plain gray band). A Byte raster's color table has all 256 entries, and
    ArcGIS Pro draws a raster that has one with Colormap symbology: a legend
    of some 250 black boxes without names. Without it, Pro draws Unique
    Values from the attribute table. Does nothing to a raster that has no
    color table. Never raises."""
    from osgeo import gdal

    try:
        gdal.UseExceptions()
        ds = gdal.Open(tif)
        band = ds.GetRasterBand(1)
        if band.GetColorTable() is None:
            ds = None
            return
        mem = gdal.GetDriverByName("MEM").Create("", ds.RasterXSize, ds.RasterYSize, 1, band.DataType)
        mem.SetGeoTransform(ds.GetGeoTransform())
        mem.SetProjection(ds.GetProjection())
        mb = mem.GetRasterBand(1)
        if band.GetNoDataValue() is not None:
            mb.SetNoDataValue(band.GetNoDataValue())
        mb.WriteArray(band.ReadAsArray())
        ds = None
        for old in (tif, tif + ".aux.xml"):
            if os.path.exists(old):
                os.remove(old)
        gdal.GetDriverByName("GTiff").CreateCopy(
            tif, mem, options=["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"])
        mem = None
    except Exception:                                            # noqa: BLE001
        pass


def write_statistics(tif, also_nodata=None):
    """Computes a GeoTIFF's statistics and histogram from its own cells and
    stores them with it, so that Copy Raster carries them into the output
    and ArcGIS Pro never has to calculate them. (Pro's Calculate
    Statistics tool is slow on geodatabase rasters and has hung inside
    this toolbox.) ``also_nodata`` names a second value to leave out, and
    makes it the raster's declared "no data" value. Returns the
    statistics, or None for a raster with no values."""
    import numpy as np
    from osgeo import gdal

    gdal.UseExceptions()
    ds = gdal.Open(tif, gdal.GA_Update)
    band = ds.GetRasterBand(1)
    a = band.ReadAsArray()
    nodata = band.GetNoDataValue()
    valid = np.ones(a.shape, bool)
    if nodata is not None:
        valid &= a != nodata
    if also_nodata is not None:
        valid &= a != also_nodata
        band.SetNoDataValue(float(also_nodata))
    if a.dtype.kind == "f":
        valid &= np.isfinite(a) & (a > -1e30)
    vals = a[valid].astype("float64")
    stats = None
    if vals.size:
        mn, mx = float(vals.min()), float(vals.max())
        stats = {"min": mn, "max": mx, "mean": float(vals.mean()), "std": float(vals.std()), "count": int(vals.size)}
        band.SetStatistics(mn, mx, stats["mean"], stats["std"])
        if mx > mn:
            counts = np.histogram(vals, bins=256, range=(mn, mx))[0]
            band.SetDefaultHistogram(mn, mx, [int(c) for c in counts])
    band.FlushCache()
    ds = None
    return stats


def write_class_table(tif, classes, areas=None, cell_m2=None):
    """Writes a classified GeoTIFF's attribute table INTO THE FILE (GDAL
    keeps it in the .aux.xml beside it), before the raster is copied to
    where it is going: Copy Raster carries the table along, into a
    geodatabase as well. Building the table afterwards with the
    geoprocessing tools (Build Raster Attribute Table and seven Add Field
    calls for every raster) took several times longer than fetching the
    raster. The fields and their meaning are those of ``name_classes``,
    which checks the table once the raster is in place. A mask class's
    percent is -999 here (the file cannot hold "no value"). Returns True
    when the table was written. Never raises."""
    import numpy as np
    from osgeo import gdal

    try:
        gdal.UseExceptions()
        ds = gdal.Open(tif, gdal.GA_Update)
        band = ds.GetRasterBand(1)
        a = band.ReadAsArray()
        nodata = band.GetNoDataValue()
        valid = np.ones(a.shape, bool) if nodata is None else (a != nodata)
        vals, counts = np.unique(a[valid], return_counts=True)
        if vals.size == 0:
            band = None
            ds = None
            return False
        names = dict((int(c["value"]), c["name"]) for c in classes)
        masks = set(int(c["value"]) for c in classes if c.get("mask"))
        colors = dict((int(c["value"]), list(c.get("color") or [156, 156, 156])) for c in classes)

        def area_of(v, n):
            if areas is not None:
                return areas.get(v)
            return n * cell_m2 if cell_m2 else None

        rows = [(int(v), float(n)) for v, n in zip(vals.tolist(), counts.tolist())]
        total = sum((area_of(v, n) if (areas is not None or cell_m2) else n) or 0.0
                    for v, n in rows if v not in masks)
        rat = gdal.RasterAttributeTable()
        for nm, ft, fu in (("Value", gdal.GFT_Integer, gdal.GFU_MinMax), ("Count", gdal.GFT_Real, gdal.GFU_PixelCount),
                           ("Class_Name", gdal.GFT_String, gdal.GFU_Generic),
                           ("Hectares", gdal.GFT_Real, gdal.GFU_Generic), ("Acres", gdal.GFT_Real, gdal.GFU_Generic),
                           ("Percent", gdal.GFT_Real, gdal.GFU_Generic), ("Red", gdal.GFT_Integer, gdal.GFU_Generic),
                           ("Green", gdal.GFT_Integer, gdal.GFU_Generic), ("Blue", gdal.GFT_Integer, gdal.GFU_Generic)):
            rat.CreateColumn(nm, ft, fu)
        rat.SetRowCount(len(rows))
        for i, (v, n) in enumerate(rows):
            m2 = area_of(v, n)
            share = m2 if m2 is not None else n
            rgb = colors.get(v, [156, 156, 156])
            rat.SetValueAsInt(i, 0, v)
            rat.SetValueAsDouble(i, 1, n)
            rat.SetValueAsString(i, 2, names.get(v, str(v)))
            rat.SetValueAsDouble(i, 3, m2 / 10000.0 if m2 is not None else -999.0)
            rat.SetValueAsDouble(i, 4, m2 / 4046.8564224 if m2 is not None else -999.0)
            rat.SetValueAsDouble(i, 5, -999.0 if v in masks or not total else 100.0 * share / total)
            for k in range(3):
                rat.SetValueAsInt(i, 6 + k, int(rgb[k]))
        band.SetDefaultRAT(rat)
        band.FlushCache()
        band = None
        ds = None
        return True
    except Exception:                                            # noqa: BLE001
        return False


def _table_is_complete(raster, classes):
    """True when the raster already has the attribute table of
    ``write_class_table``, with a name for every value."""
    import arcpy

    try:
        have = set(f.name for f in arcpy.ListFields(raster))
        if not {"Value", "Class_Name", "Hectares", "Acres", "Percent", "Red", "Green", "Blue"} <= have:
            return False
        named = dict((int(v), n) for v, n in arcpy.da.SearchCursor(raster, ["Value", "Class_Name"]))
        return bool(named) and not [v for v, n in named.items() if not n or n == str(v)]
    except Exception:                                            # noqa: BLE001
        return False


def name_classes(raster, classes, messages=None, areas=None, cell_m2=None):
    """Gives a classified raster its attribute table: the NAME of each
    class (Class_Name), and its area in hectares and acres and its share
    of the fire in percent. ``areas`` is {value: square meters}; without
    it the area is the cell count times ``cell_m2``. Mask classes (cells
    the program could not assess) have an area and no percent, and are
    left out of the total the percentages are taken of. Also writes a
    layer file beside the raster with the program's colors.

    A raster that already carries the table (``write_class_table`` wrote
    it into the scratch GeoTIFF, and Copy Raster brought it along) is only
    checked and finished here, which is fast; the table is built with the
    geoprocessing tools only when it is missing."""
    import arcpy
    import mahalanobis as _mh

    try:
        if _table_is_complete(raster, classes):
            masks = [int(c["value"]) for c in classes if c.get("mask")]
            if masks and _mh.in_gdb_container(raster):
                # in a geodatabase a mask class has NO percent, not the file's -999
                try:
                    with arcpy.da.UpdateCursor(raster, ["Value", "Percent", "Hectares", "Acres"]) as cur:
                        for v, pct, ha, ac in cur:
                            if pct == -999.0 or ha == -999.0:
                                cur.updateRow([v, None if pct == -999.0 else pct,
                                               None if ha == -999.0 else ha, None if ac == -999.0 else ac])
                except Exception:                                # noqa: BLE001
                    pass                                         # -999 stays: still not a percentage
            return _mh.default_unique_value_lyrx(raster, classes, field="Class_Name", messages=messages)
        arcpy.management.BuildRasterAttributeTable(raster, "Overwrite")
        have = [f.name for f in arcpy.ListFields(raster)]
        for nm, kind, length in (("Class_Name", "TEXT", 60), ("Hectares", "DOUBLE", None),
                                 ("Acres", "DOUBLE", None), ("Percent", "DOUBLE", None),
                                 ("Red", "SHORT", None), ("Green", "SHORT", None), ("Blue", "SHORT", None)):
            if nm not in have:
                arcpy.management.AddField(raster, nm, kind, field_length=length)
        names = dict((int(c["value"]), c["name"]) for c in classes)
        masks = set(int(c["value"]) for c in classes if c.get("mask"))
        rows = [(int(v), float(n)) for v, n in arcpy.da.SearchCursor(raster, ["Value", "Count"])]

        def area_of(v, n):
            if areas is not None:
                return areas.get(v)
            return n * cell_m2 if cell_m2 else None

        total = sum((area_of(v, n) if (areas is not None or cell_m2) else n) or 0.0
                    for v, n in rows if v not in masks)
        # Red, Green and Blue are the fields ArcGIS Pro reads the colors of a raster's Unique Values
        # symbology from, when the raster has an attribute table and no color table
        colors = dict((int(c["value"]), list(c.get("color") or [156, 156, 156])) for c in classes)
        # a raster in a folder keeps its attribute table in a .dbf, which cannot hold "no value":
        # there a mask class's percent is -999
        empty = None if _mh.in_gdb_container(raster) else -999.0
        with arcpy.da.UpdateCursor(raster, ["Value", "Count", "Class_Name", "Hectares", "Acres", "Percent",
                                            "Red", "Green", "Blue"]) as cur:
            for row in cur:
                v, n = int(row[0]), row[1]
                m2 = area_of(v, float(n))
                share = (m2 if m2 is not None else float(n))
                rgb = colors.get(v, [156, 156, 156])
                cur.updateRow([v, n, names.get(v, str(v)),
                               m2 / 10000.0 if m2 is not None else empty,
                               m2 / 4046.8564224 if m2 is not None else empty,
                               empty if v in masks or not total else 100.0 * share / total,
                               int(rgb[0]), int(rgb[1]), int(rgb[2])])
        # read the table back: a raster must never leave this tool with numbers and no names
        named = dict((int(v), n) for v, n in arcpy.da.SearchCursor(raster, ["Value", "Class_Name"]))
        unnamed = [v for v, n in named.items() if not n or n == str(v)]
        if not named or unnamed:
            raise RuntimeError("no name for the values {}".format(unnamed or "in the table"))
        return _mh.default_unique_value_lyrx(raster, classes, field="Class_Name", messages=messages)
    except Exception as e:                                       # noqa: BLE001
        if messages is not None:
            messages.addWarningMessage(
                "THE CLASS NAMES OF {} COULD NOT BE WRITTEN TO ITS ATTRIBUTE TABLE ({}). Its values mean: {}.".format(
                    raster, e, "; ".join("{} = {}".format(c["value"], c["name"]) for c in classes)))
        return None


def join_paths(paths, most):
    """Paths joined with "; ": as many whole paths as fit in ``most``
    characters. A path is never cut."""
    out = ""
    for path in paths:
        longer = path if not out else out + "; " + path
        if len(longer) > most:
            break
        out = longer
    return out


def safe_name(text, most=40):
    s = re.sub(r"[^A-Za-z0-9]+", "_", str(text or "")).strip("_")
    if not s or not s[0].isalpha():
        s = "fire_" + s
    return s[:most].rstrip("_")


# ---------------------------------------------------------------------------
#  asking the portal for data bundles
# ---------------------------------------------------------------------------
def valid_email(text):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", str(text or "").strip()))


def bundle_request(fires, products, projection):
    """The body the portal's own viewer sends for a bundle request."""
    ids = []
    bundles = []
    for f in fires:
        p = f.get("properties") or {}
        if p.get("map_id") is None:
            continue
        if p.get("nonstandard") in (True, "True", "true"):
            bundles.append(["{}/{}/{}.zip".format(str(p.get("map_prog") or "").lower(), year_of(f), p.get("event_id")),
                            p.get("map_id")])
        else:
            ids.append(p.get("map_id"))
    return {"download_type": "mapping_products", "mapping_bundles": bundles, "mapping_ids": ids,
            "mapping_products": list(products), "projection": projection, "mosaics": []}


def request_bundles(email, fires, products, projection="UTM"):
    """Asks the Burn Severity Portal to prepare the fires' data bundles
    and to email a download link to ``email``. Returns the portal's
    message. Nothing is downloaded: the bundles arrive by email."""
    if not valid_email(email):
        raise FireError("That does not look like an email address: {}".format(email))
    if len(fires) > MAX_BUNDLE_FIRES:
        raise FireError("The portal takes at most {} fires in one bundle request, and {} were found. Narrow the "
                        "years, the area or the name.".format(n0(MAX_BUNDLE_FIRES), n0(len(fires))))
    body = bundle_request(fires, products, projection)
    if not body["mapping_ids"] and not body["mapping_bundles"]:
        raise FireError("None of the fires has a data bundle on the portal.")
    data = ("products=" + urllib.parse.quote(json.dumps(body)) + "&email=" + urllib.parse.quote(email.strip())
            + "&request_origin='viewer'").encode("utf-8")
    raw, _h = _read(QUEUE, data=data, timeout=120, tries=1,
                    headers={"Content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                             "Referer": PORTAL + "viewer/"})
    text = raw.decode("utf-8", "replace")
    try:
        r = json.loads(text)
    except ValueError:
        raise FireError("The portal did not confirm the bundle request. It answered: {}".format(text[:300]))
    message = str(r.get("message") or "")
    if r.get("success") is True or "successfully" in message:
        return message or "The request was accepted."
    raise FireError("The portal did not accept the bundle request: {}".format(message or text[:300]))


# ---------------------------------------------------------------------------
#  the run
# ---------------------------------------------------------------------------
def check_workspace(path):
    """None when classified rasters can be written to ``path`` (a folder
    or a file geodatabase), else the reason."""
    import mahalanobis as _mh

    p = str(path or "").strip().replace("/", "\\").rstrip("\\")
    if not p:
        return "Give a folder or a file geodatabase for the classified rasters."
    if _mh.is_memory_workspace(p):
        return "Rasters cannot be written to the memory workspace. Give a folder or a file geodatabase."
    if not os.path.isdir(p):
        return "{} does not exist. Give an existing folder or file geodatabase.".format(p)
    if p.lower().endswith(".sde"):
        return "Give a folder or a file geodatabase, not an enterprise geodatabase."
    return None


def run(out_perimeters, **kwargs):
    """See ``_run``. Returns a dict: found, written, rasters (a list of
    paths), bundle_message."""
    import arcpy

    try:
        auto = arcpy.env.autoCancelling
    except Exception:                                            # noqa: BLE001
        auto = None
    try:
        if auto is not None:
            arcpy.env.autoCancelling = False
        return _run(out_perimeters, **kwargs)
    finally:
        if auto is not None:
            try:
                arcpy.env.autoCancelling = auto
            except Exception:                                    # noqa: BLE001
                pass


def _run(out_perimeters, program="MTBS", fire_name=None, year_from=None, year_to=None, study_area=None,
         extent=None, fire_ids=None, products=None, raster_workspace=None, bundle_email=None,
         bundle_images=False, bundle_projection="UTM", messages=None, area_words=None):
    import arcpy
    import mahalanobis as _mh

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

    program = str(program or "").upper()
    if program not in PROGRAMS:
        raise FireError("The program must be MTBS, RAVG or BAER.")
    info = PROGRAMS[program]
    why = G.check_output(out_perimeters)
    if why:
        raise FireError(why.replace("GBIF_occurrences", program + "_fires"))
    products = [p for p in (products or []) if p]
    for p in products:
        if p not in info["products"]:
            raise FireError("{} has no product named \"{}\". Its products: {}.".format(
                program, p, "; ".join(info["products"])))
    if products:
        why = check_workspace(raster_workspace)
        if why:
            raise FireError(why)
    if bundle_email and not valid_email(bundle_email):
        raise FireError("That does not look like an email address: {}".format(bundle_email))
    this_year = datetime.date.today().year
    if year_from not in (None, "") and not (1984 <= int(year_from) <= this_year + 1):
        raise FireError("The first year must be between 1984 and this year.")
    if year_to not in (None, "") and int(year_to) < 1984:
        raise FireError("The last year must be 1984 or later.")
    year_from = int(year_from) if year_from not in (None, "") else None
    year_to = int(year_to) if year_to not in (None, "") else None
    if year_from and year_to and year_from > year_to:
        raise FireError("The first year is after the last year.")
    # A last year later than this one is accepted (9999 is the easy way to say
    # "through the latest fires"): it is clamped, and the years that were not
    # available are named.
    if year_to and year_to > this_year:
        warn("Fires were asked for through {0}, but {1} not in the {2} catalog yet. The request is made "
             "through {3}.".format(
                 year_to,
                 "the year {} is".format(this_year + 1) if year_to == this_year + 1
                 else "the years {} to {} are".format(this_year + 1, year_to),
                 program, this_year))
        year_to = this_year

    # ---- where ---------------------------------------------------------------
    polygons = None
    box = None
    area_geoms = None
    if study_area is not None or extent is not None:
        try:
            polygons = G.study_area_polygons(study_area, extent)
        except G.GbifError as e:
            raise FireError(str(e))
        xs = [p[0] for poly in polygons for p in poly[0]]
        ys = [p[1] for poly in polygons for p in poly[0]]
        box = (max(-180.0, min(xs)), max(-90.0, min(ys)), min(180.0, max(xs)), min(90.0, max(ys)))
        wgs = arcpy.SpatialReference(4326)
        area_geoms = [arcpy.Polygon(arcpy.Array([arcpy.Array([arcpy.Point(x, y) for x, y in ring]) for ring in poly]), wgs)
                      for poly in polygons]
    ids, only_maps = split_fire_ids(fire_ids) if fire_ids else (None, {})
    if not (fire_name and str(fire_name).strip()) and not ids and box is None and not (year_from or year_to):
        raise FireError("Give at least a fire name, a year, a study area or a fire ID. With none of them the "
                        "request is every fire {} has mapped.".format(program))

    say("{} ({}), a program of the {}. It maps fires in the United States and its territories only.".format(
        program, info["name"], info["agency"]))
    criteria = dict(name=fire_name, year_from=year_from, year_to=year_to, box=box, fire_ids=ids)
    step(1, "Searching the fire catalog...")
    fires = search(program, cancelled=cancelled, **criteria)
    where_words = area_words or ("the study area" if study_area is not None else "the rectangle" if box else None)
    if area_geoms:
        # the catalog searches by rectangle: keep the fires whose perimeter touches the study area itself
        kept = []
        wgs = arcpy.SpatialReference(4326)
        for f in fires:
            touches = False
            for poly in rings_of(f):
                try:
                    g = arcpy.Polygon(arcpy.Array([arcpy.Array([arcpy.Point(x, y) for x, y in ring]) for ring in poly]), wgs)
                    if any(not g.disjoint(a) for a in area_geoms):
                        touches = True
                        break
                except Exception:                                # noqa: BLE001
                    touches = True
                    break
            if touches:
                kept.append(f)
        fires = kept
    left_out = 0
    if only_maps:
        # an entry "fire ID:map ID" asks for that one assessment of the fire, not for all of them
        kept, found_maps = [], set()
        for f in fires:
            p = f.get("properties") or {}
            event = str(p.get("event_id") or "").upper()
            try:
                map_id = int(p.get("map_id"))
            except (TypeError, ValueError):
                map_id = None
            if event not in only_maps or map_id in only_maps[event]:
                kept.append(f)
                found_maps.add((event, map_id))
            else:
                left_out += 1
        fires = kept
        absent = ["{}:{}".format(e, m) for e in sorted(only_maps) for m in sorted(only_maps[e])
                  if (e, m) not in found_maps]
        if absent:
            warn("The {} catalog has no assessment for {}: {}.".format(
                program, "this entry" if len(absent) == 1 else "these entries", ", ".join(absent[:20])
                + (" and {} more".format(n0(len(absent) - 20)) if len(absent) > 20 else "")))
    chosen = "; ".join(x for x in (
        "name contains \"{}\"".format(fire_name) if fire_name and str(fire_name).strip() else None,
        "fire IDs {}".format(", ".join(ids)) if ids else None,
        ("{}".format(year_from) if year_from == year_to else "{} to {}".format(year_from or "the start", year_to or "now"))
        if (year_from or year_to) else None,
        "perimeters that touch {}".format(where_words) if where_words else None) if x)
    say("{} {} {} ({}).".format(n0(len(fires)), program, "fire matches" if len(fires) == 1 else "fires match", chosen))
    if left_out:
        say("{} of these fires {} left out: the fire IDs name particular assessments (a fire ID, a colon "
            "and a map ID), and only those were asked for.".format(
                "1 other assessment" if left_out == 1 else "{} other assessments".format(n0(left_out)),
                "was" if left_out == 1 else "were"))
    if area_geoms and fires:
        say("The study area only selects the fires. Each fire that touches it is written whole, with the parts "
            "of it that lie outside the study area; nothing is clipped.")
    if not fires:
        warn("No {} fire matches, so the output is empty. MTBS maps fires of at least 1,000 acres in the West "
             "and 500 acres in the East; RAVG and BAER map far fewer fires. Try a shorter part of the name, "
             "more years or a larger area.".format(program))

    # ---- the data bundles: asked for FIRST -----------------------------------
    # The portal takes a minute or more to prepare the bundles, so the request goes out before anything
    # is fetched: the portal works while this tool does. A request that fails is a warning, not the end
    # of the run: the perimeters and the rasters are still worth having.
    bundle_message = ""
    if bundle_email and fires:
        wanted = list(info["bundle_products"]) + (BUNDLE_IMAGES if bundle_images else [])
        step(3, "Asking the Burn Severity Portal for the data bundles...")
        try:
            bundle_message = request_bundles(bundle_email, fires, wanted, bundle_projection or "UTM") or "requested"
            say("The request for the data {} of {} {} was sent to the Burn Severity Portal at {}, and the portal "
                "accepted it. The portal is preparing {} now, while this tool goes on, and will send an email "
                "to {} with a link.".format(
                    "bundle" if len(fires) == 1 else "bundles", n0(len(fires)),
                    "fire" if len(fires) == 1 else "fires", datetime.datetime.now().strftime("%H:%M:%S"),
                    "it" if len(fires) == 1 else "them", bundle_email))
        except FireError as e:
            warn("THE REQUEST FOR THE DATA BUNDLES WAS NOT MADE: {} The rest of the run goes on; run the tool "
                 "again with the email address to ask once more.".format(e))
    elif bundle_email:
        say("No bundle was requested, because no fire matched.")

    # ---- the classified rasters --------------------------------------------
    rasters = {}                                   # index of the fire -> [paths]
    all_rasters = []
    if products and fires:
        if len(fires) * len(products) > MANY_RASTERS:
            warn("{} classified rasters will be fetched, one request each, which will take about {}. The tool "
                 "can be cancelled at any time.".format(n0(len(fires) * len(products)),
                                                        span(len(fires) * len(products) * 4.0)))
        ws = str(raster_workspace).strip().replace("/", "\\").rstrip("\\")
        in_gdb = _mh.is_gdb_workspace(ws)
        scratch = arcpy.env.scratchFolder
        used = set()
        missing = []
        partial = []
        failed = []
        why_failed = ""
        in_a_row = 0
        done = 0
        todo = len(fires) * len(products)
        today = datetime.date.today().strftime("%Y-%m-%d")
        for i, f in enumerate(fires):
            if in_a_row >= MAX_FAILS:
                break
            p = f["properties"]
            for label in products:
                if cancelled():
                    raise Cancelled()
                if in_a_row >= MAX_FAILS:
                    break
                short, classes, how = info["products"][label]
                done += 1
                step(5 + 80.0 * done / todo, "Fetching {} of {}: {} {}".format(
                    n0(done), n0(todo), p.get("incid_name") or p.get("event_id"), year_of(f)))
                base = safe_name("{}_{}_{}_{}".format(program, p.get("incid_name") or "fire", year_of(f), short), 60)
                name = base
                k = 2
                while name.lower() in used:
                    name = "{}_{}".format(base, k)
                    k += 1
                used.add(name.lower())
                raw = os.path.join(scratch, "fire_raw_{}.tif".format(os.getpid()))
                cut = os.path.join(scratch if in_gdb else ws, name + ".tif")
                areas = None
                trouble = None
                try:
                    for attempt in (0, 1):
                        try:
                            got = fetch_classes(f, how, raw, attempt=attempt)
                        except FireError as e:
                            trouble = e                   # this source failed: the other one may still answer
                            continue
                        if got is None:
                            break
                        if not got:
                            continue
                        for old in (cut, cut + ".vat.dbf", cut + ".aux.xml", cut + ".ovr"):
                            if os.path.exists(old):
                                os.remove(old)
                        try:
                            areas = cut_to_fire(raw, f, cut, classes)
                        except Exception as e:                   # noqa: BLE001  (a raster that broke off part way)
                            trouble = FireError("The raster the service sent could not be read ({}).".format(e))
                            continue
                        if areas is not None:
                            write_statistics(cut)
                            break
                finally:
                    try:
                        os.remove(raw)
                    except OSError:
                        pass
                if areas is None:
                    # a service that failed is not a service that has no raster: the two are reported apart
                    if trouble is not None:
                        failed.append("{} {} ({})".format(p.get("incid_name"), year_of(f), short))
                        why_failed = str(trouble)
                        in_a_row += 1
                    else:
                        missing.append("{} {} ({})".format(p.get("incid_name"), year_of(f), short))
                        in_a_row = 0
                    try:
                        os.remove(cut)
                    except OSError:
                        pass
                    continue
                in_a_row = 0
                write_class_table(cut, classes, areas=areas)     # Copy Raster carries the table along
                whole = area_of(f)
                have = sum(areas.values())
                if whole > 0 and have < PARTIAL * whole:
                    partial.append("{} {} ({}): {} of {} hectares, {}".format(
                        p.get("incid_name"), year_of(f), short, n0(round(have / 10000.0)),
                        n0(round(whole / 10000.0)),
                        "less than 1 percent" if have < 0.01 * whole
                        else "{} percent".format(int(round(100.0 * have / whole)))))
                final = cut
                if in_gdb:
                    final = os.path.join(ws, name)
                    if arcpy.Exists(final):
                        arcpy.management.Delete(final)
                    arcpy.management.CopyRaster(cut, final)
                    try:
                        arcpy.management.Delete(cut)
                    except Exception:                            # noqa: BLE001
                        pass
                name_classes(final, classes, messages, areas=areas)
                stamp_raster(final, program, label, f, today)
                rasters.setdefault(i, []).append(final)
                all_rasters.append(final)
        say("Fetched {} classified {} into {}.".format(
            n0(len(all_rasters)), "raster" if len(all_rasters) == 1 else "rasters", ws))
        if failed:
            not_asked = todo - done if in_a_row >= MAX_FAILS else 0
            warn("{} COULD NOT BE FETCHED, because the map service failed or refused the request: {}{}. The "
                 "last reason given: {} This is not the same as a fire that has no raster: run the tool again "
                 "later for {}.{}".format(
                     "1 raster" if len(failed) == 1 else "{} rasters".format(n0(len(failed))),
                     "; ".join(failed[:12]),
                     " and {} more".format(n0(len(failed) - 12)) if len(failed) > 12 else "",
                     why_failed, "this fire" if len(failed) == 1 else "these fires",
                     " After {} requests in a row had failed the tool stopped asking, so {} more {} not "
                     "asked for.".format(MAX_FAILS, n0(not_asked), "raster was" if not_asked == 1 else "rasters were")
                     if not_asked else ""))
        if partial:
            warn("{} only a small part of {} fire: {}{}. The map service holds classified cells for that part of "
                 "the fire only, so {} not a map of the whole fire, and {} percentages are of the mapped part "
                 "alone. The fire's data bundle may hold a complete raster, or none.".format(
                     "1 of the rasters covers" if len(partial) == 1
                     else "{} of the rasters cover".format(n0(len(partial))),
                     "its" if len(partial) == 1 else "their",
                     "; ".join(partial[:12]),
                     " and {} more".format(n0(len(partial) - 12)) if len(partial) > 12 else "",
                     "the raster is" if len(partial) == 1 else "these rasters are",
                     "its" if len(partial) == 1 else "their"))
        if missing:
            warn("The map services hold no classified raster for {} of the fires: {}{}. {} {}".format(
                     n0(len(missing)), "; ".join(missing[:12]),
                     " and {} more".format(n0(len(missing) - 12)) if len(missing) > 12 else "",
                     "The services carry MTBS's yearly national mosaics, and a fire mapped after its year's "
                     "mosaic was made is not in it yet." if program == "MTBS"
                     else "The map service carries RAVG's yearly mosaics for 2012 to 2023 only."
                     if program == "RAVG"
                     else "The map service carries the soil burn severity the Forest Service has published, "
                          "from 2017, and not for every fire.",
                     "A fire's data bundle may have what the service lacks, and it is the only other source, but "
                     "it does not always hold a soil burn severity raster: for some fires none was published, "
                     "and the bundles of fires before 2017 are in an older form with the first map (BARC) "
                     "and no soil burn severity. The Burn Severity Portal sends bundles by email: give an email "
                     "address to this tool to ask for them." if program == "BAER"
                     else "Such a fire has its rasters only in its data bundle, which the Burn Severity Portal "
                          "sends by email: give an email address to this tool to ask for it."))
        if all_rasters:
            say("These rasters are the map services' copies of {}'s national mosaics, in the Web Mercator "
                "coordinate system with cells of 30 map units, cut to each fire's perimeter. They are right "
                "for looking and for overlay. A cell is not 30 meters on the ground (Web Mercator stretches "
                "distance with latitude), so do not count cells to measure area: use a tool that measures "
                "true area, or the fire's data bundle, which holds the original cells. Each raster's "
                "attribute table gives the true area of each class in its Hectares and Acres fields, and "
                "its share of the fire in Percent.".format(program))

    # ---- the perimeters ------------------------------------------------------
    step(88, "Writing the perimeters...")
    gdb, fc_name = os.path.split(out_perimeters)
    if arcpy.Exists(out_perimeters):
        arcpy.management.Delete(out_perimeters)
    wgs = arcpy.SpatialReference(4326)
    arcpy.management.CreateFeatureclass(gdb, fc_name, "POLYGON", spatial_reference=_mh.clean_sr(wgs))
    # all the fields in ONE call: Add Field once for each of them took most of the time of this step
    arcpy.management.AddFields(out_perimeters, [[name, kind, alias, length if kind == "TEXT" else None]
                                                for name, alias, kind, length, _prop, _d in FIELDS])
    written = 0
    acres = 0.0
    with arcpy.da.InsertCursor(out_perimeters, ["SHAPE@"] + [f[0] for f in FIELDS]) as cur:
        for i, f in enumerate(fires):
            p = f["properties"]
            parts = arcpy.Array()
            for poly in rings_of(f):
                for ring in poly:
                    parts.add(arcpy.Array([arcpy.Point(x, y) for x, y in ring]))
            if parts.count == 0:
                continue
            row = [arcpy.Polygon(parts, wgs)]
            for name, _a, kind, length, prop, _d in FIELDS:
                if name == "fire_year":
                    v = year_of(f)
                elif name == "portal_url":
                    v = PORTAL + "viewer/?product=" + program
                elif name == "raster":
                    v = join_paths(rasters.get(i, []), length)
                else:
                    v = p.get(prop)
                    if kind == "DATE":
                        v = _date(v)
                    elif kind in ("LONG", "DOUBLE"):
                        try:
                            v = float(v) if kind == "DOUBLE" else int(v)
                        except (TypeError, ValueError):
                            v = None
                        if v is not None and kind == "LONG" and name != "map_id" and abs(v) >= 9999:
                            v = None                             # the catalog's "no value" (a map ID is eight digits)
                    elif v in (None, "None", "NULL"):
                        v = None
                    else:
                        v = str(v)[:length]
                row.append(v)
            cur.insertRow(row)
            written += 1
            try:
                acres += float(p.get("burnbndac") or 0)
            except (TypeError, ValueError):
                pass
    if written:
        say("Wrote {} {} to {}: {} acres in all, as {} reports them.".format(
            n0(written), "perimeter" if written == 1 else "perimeters", out_perimeters, n0(round(acres)), program))

    # ---- the data bundles: the reminder (the request went out first, above) --
    if bundle_message:
        warn("THE DATA BUNDLES WERE NOT DOWNLOADED. The portal prepares the bundles and sends an email to {} with a link, "
             "usually within minutes. Open that email and download the zip file yourself; the link works for "
             "30 days. Then run the Load Burn Severity Bundle tool on the zip file. The "
             "bundles hold what the map services do not: {}{}, in the {} projection.".format(
                 bundle_email,
                 "the continuous dNBR and RdNBR with each fire's true 30-meter cells, the rasters of fires too "
                 "recent for the national mosaics, and each fire's thresholds in its perimeter"
                 if program == "MTBS"
                 else "the continuous dNBR and the continuous percent basal area loss, percent canopy cover loss "
                      "and composite burn index, with each fire's true cells, for every fire and not only those "
                      "of 2012 to 2023" if program == "RAVG"
                 else "the continuous dNBR, and the soil burn severity with its true cells where one was "
                      "published (the bundles of fires before 2017 hold the first map of the fire, BARC, "
                      "instead)",
                 ", and the pre-fire and post-fire images" if bundle_images else "",
                 bundle_projection or "UTM"))

    # ---- metadata ------------------------------------------------------------
    today = datetime.date.today().strftime("%Y-%m-%d")
    summary = ("Created {} by the {} Burn Severity tool from the fire catalog of the Burn Severity Portal "
               "(U.S. Geological Survey and USDA Forest Service). Choices: {}. {} perimeters written{}{}.".format(
                   datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), program, chosen, n0(written),
                   "; {} classified rasters fetched into {}".format(n0(len(all_rasters)), raster_workspace)
                   if all_rasters else "",
                   "; data bundles requested by email" if bundle_message else ""))
    stamp_metadata(out_perimeters, program, summary, today, all_rasters, messages)
    step(100, "Done: {} fires.".format(n0(written)))
    return {"found": len(fires), "written": written, "rasters": all_rasters, "bundle_message": bundle_message}


DESCRIPTIONS = {
    "MTBS": "Perimeters of fires mapped by Monitoring Trends in Burn Severity (MTBS), a program of the U.S. "
            "Geological Survey and the USDA Forest Service. MTBS maps every fire of at least 1,000 acres in the "
            "western United States and 500 acres in the east, on all ownerships, since 1984, from Landsat and "
            "Sentinel-2 images taken before and after the fire. Its burn severity classes are set for each fire "
            "by an analyst from the differenced Normalized Burn Ratio (dNBR), so a class does not mean the same "
            "thing in every fire or every region. The thresholds the analyst chose are in the low_t, moderate_t "
            "and high_t fields.",
    "RAVG": "Perimeters of fires mapped by Rapid Assessment of Vegetation Condition after Wildfire (RAVG), a "
            "program of the USDA Forest Service. RAVG maps fires that burn at least 1,000 acres of forested "
            "National Forest System land (500 acres in the Eastern and Southern regions since 2016), about 45 "
            "days after containment. It estimates percent basal area loss, percent canopy cover loss and the "
            "composite burn index from the Relative differenced Normalized Burn Ratio (RdNBR) with regression "
            "models built from field plots. The models are not calibrated for vegetation that is not forest.",
    "BAER": "Perimeters of fires assessed for Burned Area Emergency Response (BAER) teams by the BAER imagery "
            "support program of the USDA Forest Service and the U.S. Geological Survey. Within days of "
            "containment the program makes a Burned Area Reflectance Classification (BARC) from dNBR; the BAER "
            "team then checks it in the field and produces the soil burn severity map, which describes the "
            "fire's effect on the soil and ground cover, not on the vegetation canopy.",
}


def stamp_raster(raster, program, product, fire, today):
    """Title, summary, description and credits for one fetched raster, so
    that the raster says where it came from when it is found on its own.
    Never raises: metadata is a courtesy, not the result."""
    try:
        import arcpy

        info = PROGRAMS[program]
        p = fire.get("properties") or {}
        fire_words = "{} fire{}".format(
            str(p.get("incid_name") or "").strip() or "unnamed",
            " of {}".format(year_of(fire)) if year_of(fire) else "")
        md = arcpy.metadata.Metadata(raster)
        md.title = os.path.basename(raster)
        md.tags = "{}, burn severity, fire severity, wildfire, Wildlife and Forestry Tools".format(program)
        md.summary = ("{} of the {} (fire ID {}), fetched on {} by the {} Burn Severity tool from the public map "
                      "services of the USDA Forest Service and the U.S. Geological Survey.".format(
                          product, fire_words, p.get("event_id") or "unknown", today, program))
        md.description = (
            "<p>" + G.html_text(
                "{} from {} ({}), a program of the {}, for the {}. The values are classes: the raster's "
                "attribute table names each class and gives its true area in the Hectares and Acres fields "
                "and its share of the fire in Percent.".format(product, info["name"], program, info["agency"],
                                                              fire_words)) + "</p>"
            "<p>" + G.html_text(
                "This raster is the map service's copy of the program's national mosaic, in the Web Mercator "
                "coordinate system with cells of 30 map units, cut to the fire's perimeter. It is right for "
                "looking and for overlay. A cell is not 30 meters on the ground (Web Mercator stretches distance "
                "with latitude), so do not count cells to measure area. The original cells are in the fire's "
                "data bundle, which the Burn Severity Portal sends by email.") + "</p>"
            "<p>These data are produced by the United States Government and cover the United States and its "
            "territories only.</p>"
            "<p><b>Links to the sources:</b></p>" + G.html_list([
                "The program: " + G.html_link(info["home"], "{} ({})".format(info["name"], program)),
                "The Burn Severity Portal: " + G.html_link(PORTAL)]))
        md.credits = ("{} ({}), {}. Burn Severity Portal, {}. Accessed on {}.".format(
            info["name"], program, info["agency"], PORTAL, today))
        md.accessConstraints = "<p>Produced by agencies of the United States Government.</p>"
        md.save()
    except Exception:                                            # noqa: BLE001
        pass


def stamp_metadata(fc, program, summary, today, rasters, messages=None):
    """Never raises: metadata is a courtesy, not the result."""
    try:
        import arcpy
        import tool_metadata

        info = PROGRAMS[program]
        links = [
            "The program: " + G.html_link(info["home"], "{} ({})".format(info["name"], program)),
            "The Burn Severity Portal's interactive viewer for {}, where each fire's data bundle can be asked "
            "for: ".format(program) + G.html_link(PORTAL + "viewer/?product=" + program),
            "The portal's direct downloads (national perimeters, fire points and mosaics): "
            + G.html_link(PORTAL + "direct-download?product=" + program),
            "The Burn Severity Portal: " + G.html_link(PORTAL),
        ]
        md = arcpy.metadata.Metadata(fc)
        md.title = os.path.basename(fc)
        md.tags = "{}, burn severity, fire severity, wildfire, fire perimeter, Wildlife and Forestry Tools".format(program)
        md.summary = summary
        md.description = (
            "<p>" + G.html_text(DESCRIPTIONS[program]) + "</p>"
            "<p>These data are produced by the United States Government and cover the United States and its "
            "territories only.</p>"
            "<p><b>Where these perimeters came from.</b> The fire catalog of the Burn Severity Portal, read on "
            + today + " from the U.S. Geological Survey's map server."
            + (" The classified rasters named in the raster field came from the public map services of the "
               "USDA Forest Service and the U.S. Geological Survey: they are Web Mercator copies of the "
               "program's national mosaics, cut to each perimeter." if rasters else "") + "</p>"
            "<p><b>Links to the sources:</b></p>" + G.html_list(links))
        md.credits = ("{} ({}), {}. Burn Severity Portal, {}. Accessed on {}.".format(
            info["name"], program, info["agency"], PORTAL, today))
        md.accessConstraints = "<p>Produced by agencies of the United States Government.</p>"
        md.save()
        tool_metadata.set_entity_and_fields(
            fc, entity="{} fire perimeters, one row per assessment of a fire.".format(program),
            fields=[{"name": f[0], "description": f[5]} for f in FIELDS], messages=messages)
    except Exception as e:                                       # noqa: BLE001
        if messages is not None:
            try:
                messages.addWarningMessage("Could not write the output's metadata: {}".format(e))
            except Exception:                                    # noqa: BLE001
                pass
