# -*- coding: utf-8 -*-
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

"""iNaturalist Observations: fetch observations straight from
iNaturalist.org and write them as points.

This is the engine behind the geoprocessing tool, and behind the last
step of the iNaturalist Observations window, which runs the tool so that
every run leaves a Geoprocessing History entry.

GBIF holds only the research-grade iNaturalist observations that carry a
CC0, CC BY or CC BY-NC license. Asking iNaturalist itself also gives the
observations that still need an identification, the casual ones, the
ones under other terms, and fields GBIF does not carry (identification
agreement, annotations, whether the location was obscured).

How the service is used (verified 2026-11-14, no account needed):
  * version 2 of the service, which returns only the fields asked for
    (a version 1 observation is about 140 KB, a version 2 one about
    1.5 KB);
  * 200 observations a request, in order of observation id, each
    request starting after the last id of the one before ("id_above"),
    because the service will not page past 10,000 any other way;
  * one request a second at most;
  * the service searches by rectangle or by one of iNaturalist's own
    named places, never by polygon, so a polygon study area is asked
    for by its bounding rectangle and cut to the polygons on writing.

Only the Python standard library and arcpy are used.
"""

import datetime
import gzip
import http.client
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import gbif_occurrences as G

API = "https://api.inaturalist.org/v2/"
API1 = "https://api.inaturalist.org/v1/"
SITE = "https://www.inaturalist.org/"
USER_AGENT = "WildlifeTools-AddIn (Jenness Enterprises; ArcGIS Pro)"

PAGE = 200                      # the most the service gives in one request
LARGE_REQUEST = 100000          # above this the run warns how long it will take; nothing is cut
PAUSE = 1.0                     # seconds between requests
SECONDS_PER_PAGE = 2.2          # one request and the pause; measured at about 1.9
# iNaturalist's stated limits (https://api.inaturalist.org/v2/docs/, read 2026-11-15): requests are
# throttled at 100 a minute, it asks for 60 a minute or fewer, and for fewer than 10,000 a day.
DAILY_REQUESTS = 10000
MAX_OBSERVATIONS = DAILY_REQUESTS * PAGE     # 2,000,000: a run that would need more is refused
TAXA_PER_REQUEST = 30

GRADE_RESEARCH = "Research grade"
GRADE_NEEDS_ID = "Needs ID"
GRADE_CASUAL = "Casual"
GRADES = [GRADE_RESEARCH, GRADE_NEEDS_ID, GRADE_CASUAL]
GRADE_CODES = {GRADE_RESEARCH: "research", GRADE_NEEDS_ID: "needs_id", GRADE_CASUAL: "casual"}
GRADE_DEFAULT = [GRADE_RESEARCH]

ESTABLISHMENT_ANY = "Any"
ESTABLISHMENT_NATIVE = "Native"
ESTABLISHMENT_INTRODUCED = "Introduced"
ESTABLISHMENTS = [ESTABLISHMENT_ANY, ESTABLISHMENT_NATIVE, ESTABLISHMENT_INTRODUCED]

OPEN_LICENSES = ["cc0", "cc-by", "cc-by-nc", "cc-by-sa", "cc-by-nd", "cc-by-nc-sa", "cc-by-nc-nd"]
COMMERCIAL_LICENSES = ["cc0", "cc-by", "cc-by-sa", "cc-by-nd"]
NO_LICENSE = "all rights reserved"

# iNaturalist's annotations: attribute id -> output field (controlled_terms,
# read 2026-11-14). The value labels are read from the service at run time.
ANNOTATION_FIELDS = {1: "lifeStage", 9: "sex", 17: "aliveOrDead", 22: "evidence",
                     12: "flowersFruits", 36: "leaves", 33: "established"}

# what is asked of the service for every observation
OBSERVATION_FIELDS = (
    "(id:!t,uri:!t,quality_grade:!t,captive:!t,observed_on:!t,time_observed_at:!t,created_at:!t,"
    "observed_on_details:(year:!t,month:!t,day:!t,hour:!t),"
    "location:!t,positional_accuracy:!t,public_positional_accuracy:!t,obscured:!t,geoprivacy:!t,"
    "taxon_geoprivacy:!t,place_guess:!t,species_guess:!t,description:!t,license_code:!t,"
    "identifications_count:!t,num_identification_agreements:!t,num_identification_disagreements:!t,"
    "user:(login:!t,name:!t),"
    "taxon:(id:!t,name:!t,rank:!t,preferred_common_name:!t,iconic_taxon_name:!t,ancestor_ids:!t,"
    "threatened:!t,introduced:!t,native:!t,endemic:!t),"
    "annotations:(controlled_attribute_id:!t,controlled_value_id:!t,vote_score:!t),"
    "photos:(id:!t,url:!t,license_code:!t,attribution:!t),sounds:(id:!t))")

# The output fields: (name, alias, kind, text length, description). Kinds:
# T text, I whole number, D decimal number. Where a field means the same
# as a field of the GBIF Species Occurrences tool it has the same name.
FIELDS = [
    ("inatID", "iNaturalist observation ID", "T", 20,
     "iNaturalist's number of the observation."),
    ("scientificName", "Scientific name", "T", 255,
     "The scientific name of the taxon the iNaturalist community identifies the observation as."),
    ("commonName", "Common name", "T", 255,
     "iNaturalist's preferred English common name of that taxon, where it has one."),
    ("species", "Species", "T", 150,
     "The species (empty when the observation is identified only to a higher rank)."),
    ("taxonRank", "Rank of the identification", "T", 30,
     "The taxonomic rank the observation is identified to (species, subspecies, genus, ...)."),
    ("iconicTaxon", "Broad group", "T", 30,
     "The broad group iNaturalist files the taxon under (Mammalia, Aves, Reptilia, Amphibia, "
     "Actinopterygii, Insecta, Arachnida, Mollusca, Plantae, Fungi, ...)."),
    ("kingdom", "Kingdom", "T", 60, "Kingdom in iNaturalist's taxonomy."),
    ("phylum", "Phylum", "T", 60, "Phylum in iNaturalist's taxonomy."),
    ("class_", "Class", "T", 60, "Class in iNaturalist's taxonomy."),
    ("order_", "Order", "T", 60, "Order in iNaturalist's taxonomy."),
    ("family", "Family", "T", 60, "Family in iNaturalist's taxonomy."),
    ("genus", "Genus", "T", 60, "Genus in iNaturalist's taxonomy."),
    ("taxonID", "iNaturalist taxon ID", "T", 20,
     "iNaturalist's number of the taxon (not a GBIF taxon key)."),
    ("eventDate", "Date observed", "T", 20, "The date of the observation, as text in the form YYYY-MM-DD."),
    ("eventTime", "Time observed", "T", 40,
     "The date and time of the observation with its offset from UTC, where the observer gave a time."),
    ("year", "Year", "I", 0, "Year of the observation."),
    ("month", "Month", "I", 0, "Month of the observation (1-12)."),
    ("day", "Day", "I", 0, "Day of the month of the observation."),
    ("decimalLatitude", "Latitude (WGS 84)", "D", 0,
     "Latitude in decimal degrees of the location iNaturalist shows to the public. For an obscured "
     "observation this is a random point, not the true location."),
    ("decimalLongitude", "Longitude (WGS 84)", "D", 0,
     "Longitude in decimal degrees of the location iNaturalist shows to the public. For an obscured "
     "observation this is a random point, not the true location."),
    ("coordUncert_m", "Coordinate uncertainty (m)", "D", 0,
     "The uncertainty of the public location, in meters. For an obscured observation it is large "
     "(tens of kilometers), because it covers the area the true location was hidden in. Empty when "
     "the observer stated no accuracy."),
    ("statedAccuracy_m", "Accuracy the observer stated (m)", "D", 0,
     "The accuracy of the true location as the observer stated it, in meters. For an obscured "
     "observation it describes the hidden true location, not the point shown here."),
    ("obscured", "Location obscured", "T", 3,
     "Yes when iNaturalist shows a random point within about 0.2 degrees of latitude and longitude "
     "of the true location, because the observer chose that or because the species is at risk."),
    ("geoprivacy", "Observer's location privacy", "T", 20,
     "The observer's own choice: open, obscured or private (empty means open)."),
    ("taxonGeoprivacy", "Location privacy set for the taxon", "T", 20,
     "The privacy iNaturalist applies because of the taxon's conservation status: open or obscured."),
    ("locality", "Place as the observer described it", "T", 500,
     "The observer's or iNaturalist's description of the place."),
    ("qualityGrade", "Quality grade", "T", 20,
     "research: dated, located, with a photo or sound, of a wild organism, and identified to species "
     "(or finer) with more than two-thirds of identifiers agreeing. needs_id: meets the same "
     "conditions but the identification is not yet agreed. casual: lacks a date, a location or a "
     "photo or sound, or is of a captive or cultivated organism, or was voted down."),
    ("captive", "Captive or cultivated", "T", 3,
     "Yes when the organism is where it is because people put it there (a pet, a zoo animal, a "
     "garden plant)."),
    ("idCount", "Identifications", "I", 0, "Number of identifications the observation has received."),
    ("idAgree", "Identifications that agree", "I", 0,
     "Number of identifications that agree with the observation's taxon."),
    ("idDisagree", "Identifications that disagree", "I", 0,
     "Number of identifications that disagree with the observation's taxon."),
    ("lifeStage", "Life stage", "T", 60, "Annotation: adult, juvenile, larva, egg and so on."),
    ("sex", "Sex", "T", 60, "Annotation: female, male or cannot be determined."),
    ("aliveOrDead", "Alive or dead", "T", 60, "Annotation: alive, dead or cannot be determined."),
    ("evidence", "Evidence of presence", "T", 120,
     "Annotation: what was observed (organism, track, scat, feather, bone, ...)."),
    ("flowersFruits", "Flowers and fruits", "T", 120,
     "Annotation for plants: flowers, flower buds, fruits or seeds, or none."),
    ("leaves", "Leaves", "T", 120,
     "Annotation for plants: breaking leaf buds, green leaves, colored leaves or no live leaves."),
    ("established", "Established", "T", 60,
     "Annotation: Not Established for an organism that is not part of a self-sustaining population."),
    ("native", "Native here", "T", 3,
     "Yes when iNaturalist lists the taxon as native to the place of the observation."),
    ("introduced", "Introduced here", "T", 3,
     "Yes when iNaturalist lists the taxon as introduced to the place of the observation."),
    ("endemic", "Endemic here", "T", 3,
     "Yes when iNaturalist lists the taxon as endemic to the place of the observation."),
    ("threatened", "Threatened here", "T", 3,
     "Yes when iNaturalist holds a conservation status of threatened or worse for the taxon at the "
     "place of the observation."),
    ("recordedBy", "Observer", "T", 255,
     "The observer's name as given on iNaturalist, or the login name when no name is given."),
    ("observerLogin", "Observer's login name", "T", 100, "The observer's iNaturalist login name."),
    ("license", "License of the observation", "T", 30,
     "The license the observer gave the observation record: cc0, cc-by, cc-by-nc, cc-by-sa, cc-by-nd, "
     "cc-by-nc-sa or cc-by-nc-nd; \"all rights reserved\" when the observer gave none."),
    ("photoCount", "Photographs", "I", 0, "Number of photographs attached to the observation."),
    ("soundCount", "Sound recordings", "I", 0, "Number of sound recordings attached to the observation."),
    ("photoURL", "First photograph", "T", 255, "Web address of the observation's first photograph."),
    ("photoLicense", "License of the first photograph", "T", 30,
     "The license of the first photograph, which can differ from the license of the observation."),
    ("photoCredit", "Credit for the first photograph", "T", 255,
     "The credit line iNaturalist gives for the first photograph."),
    ("description", "Observer's notes", "T", 1000, "The notes the observer wrote."),
    ("createdAt", "Date posted", "T", 40, "When the observation was posted to iNaturalist."),
    ("recordURL", "Observation on iNaturalist.org", "T", 80,
     "Web address of the observation's page on iNaturalist.org."),
]
FIELD_NAMES = [f[0] for f in FIELDS]
RANK_FIELDS = {"kingdom": "kingdom", "phylum": "phylum", "class": "class_", "order": "order_",
               "family": "family", "genus": "genus", "species": "species"}

Cancelled = G.Cancelled
n0 = G.n0
span = G.span


class INatError(Exception):
    """A failure the user can act on; shown without a traceback."""

    def __init__(self, message, status=0):
        Exception.__init__(self, message)
        self.status = status


# ---------------------------------------------------------------------------
#  talking to iNaturalist
# ---------------------------------------------------------------------------
_last_request = [0.0]


def explain(status, body):
    detail = ""
    try:
        j = json.loads(body or "")
        if isinstance(j, dict):
            errs = j.get("errors") or []
            detail = "; ".join(str(e.get("message") or "") for e in errs if isinstance(e, dict)) \
                or str(j.get("error") or "")
    except ValueError:
        detail = ""
    if len(detail) > 400:
        detail = detail[:400] + "..."
    if status == 429:
        lead = "iNaturalist is limiting requests from this computer. Wait a few minutes and try again."
    elif status in (502, 503, 504):
        lead = "iNaturalist is busy or unavailable right now. Try again in a few minutes."
    else:
        lead = "iNaturalist returned an error ({}).".format(status)
    return lead if not detail else lead + " " + detail


def _json(url, tries=3):
    """One request, never sooner than PAUSE seconds after the last one."""
    last = None
    for attempt in range(max(1, tries)):
        wait = PAUSE - (time.time() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=120)
            try:
                text = resp.read().decode("utf-8", "replace")
            finally:
                resp.close()
            try:
                return json.loads(text or "null")
            except ValueError:
                # an answer that cannot be read is asked for again, like one that never came
                last = INatError("iNaturalist returned something that is not the expected data.")
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:                                    # noqa: BLE001
                body = ""
            last = INatError(explain(e.code, body), e.code)
            if e.code < 500:
                raise last
        except urllib.error.URLError as e:
            last = INatError("iNaturalist could not be reached ({}). Check the internet connection.".format(
                getattr(e, "reason", e)))
        except (TimeoutError, OSError, http.client.HTTPException) as e:   # the last: an answer cut short
            last = INatError("iNaturalist did not answer in time ({}). Try again.".format(e))
        if attempt + 1 < tries:
            time.sleep(3 + 4 * attempt)
    raise last


def _results(root):
    if isinstance(root, dict) and isinstance(root.get("results"), list):
        return root["results"]
    return []


# ---------------------------------------------------------------------------
#  names, places, projects
# ---------------------------------------------------------------------------
_ID_TAIL = re.compile(r"[\(\[]\s*(\d+)\s*[\)\]]\s*$")


def _name_and_id(entry):
    """'Pronghorn (42429)' -> ('Pronghorn', '42429'); '42429' -> ('', '42429');
    'Pronghorn' -> ('Pronghorn', None)."""
    m = _ID_TAIL.search(entry)
    if m:
        return entry[:m.start()].strip(), m.group(1)
    if entry.isdigit():
        return "", entry
    return entry, None


def resolve_taxa(text, say):
    """iNaturalist taxon ids for a list of names and ids.

    Each entry is an iNaturalist taxon id ("42429"), a scientific or
    common name ("Antilocapra americana", "pronghorn"), or a name
    followed by its id in brackets ("Pronghorn (42429)": the id is used
    and the name is only a label). A name must match one taxon exactly;
    a name that matches none, or several, stops the tool with the
    candidates listed.
    """
    ids, labels = [], []
    for entry in G.split_list(text):
        name, tid = _name_and_id(entry)
        if tid is not None:
            try:
                got = _results(_json(API1 + "taxa/" + tid))
            except INatError as e:
                if e.status in (404, 422):
                    got = []
                else:
                    raise
            if not got:
                raise INatError("{} is not an iNaturalist taxon id.".format(tid))
            t = got[0]
        else:
            got = _results(_json(API1 + "taxa?" + urllib.parse.urlencode(
                {"q": name, "is_active": "true", "per_page": 30})))
            low = name.lower()
            exact = [t for t in got if (t.get("name") or "").lower() == low]
            if not exact:
                exact = [t for t in got if low in ((t.get("preferred_common_name") or "").lower(),
                                                   (t.get("matched_term") or "").lower())]
            if len(exact) != 1:
                shown = exact or got
                raise INatError(
                    "iNaturalist {} \"{}\".{} Give the scientific name, or the iNaturalist taxon id (the "
                    "iNaturalist Observations window finds both).".format(
                        "has more than one taxon named" if exact else "has no taxon named exactly", name,
                        "" if not shown else " The closest are: " + "; ".join(
                            "{} ({}{}, id {})".format(
                                t.get("name"), t.get("rank"),
                                ", " + t["preferred_common_name"] if t.get("preferred_common_name") else "",
                                t.get("id")) for t in shown[:8]) + "."))
            t = exact[0]
        tid = str(t.get("id"))
        say("Species or group: {}{} ({}; iNaturalist taxon id {}).".format(
            t.get("name"), " [{}]".format(t["preferred_common_name"]) if t.get("preferred_common_name") else "",
            t.get("rank") or "rank not given", tid))
        if tid not in ids:
            ids.append(tid)
            labels.append(t.get("name") or tid)
    return ids, labels


def resolve_place(text, say):
    """(place id, display name) for an iNaturalist place name or id."""
    entry = (text or "").strip().strip("'\"").strip()
    if not entry:
        return None, None
    name, pid = _name_and_id(entry)
    if pid is not None:
        try:
            got = _results(_json(API1 + "places/" + pid))
        except INatError as e:
            if e.status in (404, 422):
                got = []
            else:
                raise
        if not got:
            raise INatError("{} is not an iNaturalist place id.".format(pid))
        p = got[0]
    else:
        got = _results(_json(API1 + "places/autocomplete?" + urllib.parse.urlencode(
            {"q": name, "per_page": 20})))
        low = name.lower()
        exact = [p for p in got if low in ((p.get("name") or "").lower(), (p.get("display_name") or "").lower())]
        if len(exact) > 1:
            # a country outranks a state, a state a county, a county a town
            official = [p for p in exact if p.get("admin_level") is not None]
            if official:
                top = min(p["admin_level"] for p in official)
                best = [p for p in official if p["admin_level"] == top]
                if len(best) == 1:
                    exact = best
        if len(exact) != 1:
            shown = exact or got
            raise INatError(
                "iNaturalist {} \"{}\".{} Give the place's name as iNaturalist writes it, or its place id.".format(
                    "has more than one place named" if exact else "has no place named exactly", name,
                    "" if not shown else " The closest are: " + "; ".join(
                        "{} (id {})".format(p.get("display_name") or p.get("name"), p.get("id"))
                        for p in shown[:8]) + "."))
        p = exact[0]
    label = p.get("display_name") or p.get("name") or str(p.get("id"))
    say("Place: {} (iNaturalist place id {}).".format(label, p.get("id")))
    return str(p.get("id")), label


def annotation_labels():
    """{(attribute id, value id): label} from the service; {} if it fails
    (the fields are then left empty, with a warning from the caller)."""
    out = {}
    for c in _results(_json(API1 + "controlled_terms")):
        for v in c.get("values") or []:
            out[(c.get("id"), v.get("id"))] = v.get("label") or ""
    return out


# ---------------------------------------------------------------------------
#  the request
# ---------------------------------------------------------------------------
class Query(object):
    """What is asked of iNaturalist."""

    def __init__(self):
        self.taxon_ids = []
        self.place_id = None
        self.box = None                 # (west, south, east, north), WGS 84
        self.polygons = []              # the true study area, for the cut
        self.grades = list(GRADE_DEFAULT)
        self.exclude_captive = True
        self.date_from = None           # 'YYYY-MM-DD'
        self.date_to = None
        self.months = []
        self.open_license_only = False
        self.exclude_noncommercial = False
        self.photos_only = False
        self.max_uncertainty_m = None
        self.exclude_obscured = False
        self.establishment = ESTABLISHMENT_ANY
        self.observer = None
        self.project = None

    def params(self):
        q = {}
        if self.taxon_ids:
            q["taxon_id"] = ",".join(self.taxon_ids)
        if self.place_id:
            q["place_id"] = self.place_id
        if self.box:
            w, s, e, n = self.box
            q.update(swlng=G._num(w), swlat=G._num(s), nelng=G._num(e), nelat=G._num(n))
        grades = [g for g in GRADES if g in set(self.grades)]
        if grades and len(grades) < len(GRADES):
            q["quality_grade"] = ",".join(GRADE_CODES[g] for g in grades)
        if self.exclude_captive and (not grades or GRADE_CASUAL in grades):
            q["captive"] = "false"
        if self.date_from:
            q["d1"] = self.date_from
        if self.date_to:
            q["d2"] = self.date_to
        months = sorted(set(int(m) for m in self.months if 1 <= int(m) <= 12))
        if 0 < len(months) < 12:
            q["month"] = ",".join(str(m) for m in months)
        if self.exclude_noncommercial:
            q["license"] = ",".join(COMMERCIAL_LICENSES)
        elif self.open_license_only:
            q["license"] = ",".join(OPEN_LICENSES)
        if self.photos_only:
            q["photos"] = "true"
        if self.max_uncertainty_m is not None:
            q["acc_below"] = G._num(self.max_uncertainty_m)
        if self.exclude_obscured:
            q["geoprivacy"] = "open"
            q["taxon_geoprivacy"] = "open"
        if self.establishment == ESTABLISHMENT_NATIVE:
            q["native"] = "true"
        elif self.establishment == ESTABLISHMENT_INTRODUCED:
            q["introduced"] = "true"
        if self.observer:
            q["user_id"] = self.observer
        if self.project:
            q["project_id"] = self.project
        return q

    def contains(self, lon, lat):
        f = G.GbifFilter()
        f.polygons = self.polygons
        self.contains = f.contains                   # build once
        return f.contains(lon, lat)


def count(query):
    q = query.params()
    q["per_page"] = 0
    try:
        r = _json(API1 + "observations?" + urllib.parse.urlencode(q)) or {}
    except INatError as e:
        if e.status == 422 and (query.observer or query.project):
            raise INatError("iNaturalist does not know {}. Check the spelling.".format(
                " or ".join(w for w in ("the observer \"{}\"".format(query.observer) if query.observer else "",
                                        "the project \"{}\"".format(query.project) if query.project else "")
                            if w)))
        raise
    return int(r.get("total_results") or 0)


def seconds_to_fetch(total):
    """About how long fetching `total` observations takes."""
    return total / float(PAGE) * SECONDS_PER_PAGE


def fetch(query, total, say, step, cancelled, spool, share=80.0):
    """Fetches all matching observations, 200 a request, in order of id.

    The observations are not held in memory: each request's worth is
    written as one line of the compressed file `spool`, so a request for
    millions of observations needs disk space, not memory. Returns
    (the number fetched, {taxon id: taxon} for the taxa among them).
    """
    with gzip.open(spool, "wt", encoding="utf-8") as f:
        return _fetch(query, total, say, step, cancelled, f, share)


def spooled(spool):
    """The observations of a file written by fetch, one at a time."""
    with gzip.open(spool, "rt", encoding="utf-8") as f:
        for line in f:
            for o in json.loads(line):
                yield o


def _fetch(query, total, say, step, cancelled, f, share):
    q = query.params()
    q.update(per_page=PAGE, order_by="id", order="asc", fields=OBSERVATION_FIELDS)
    n = 0
    taxa = {}
    last_id = None
    waits = 0
    t0 = time.time()
    while True:
        if cancelled():
            raise Cancelled()
        if last_id is not None:
            q["id_above"] = last_id
        try:
            page = _json(API + "observations?" + urllib.parse.urlencode(q)) or {}
        except INatError as e:
            # A long request is hours of work. iNaturalist asking for a pause, or failing for a while, does
            # not throw it away: the request is made again after a wait, up to five times in a row.
            passing = e.status == 429 or not e.status or e.status >= 500
            if passing and waits < 5:
                waits += 1
                say(("iNaturalist asked this computer to slow down; waiting 60 seconds ({} of 5)." if e.status == 429
                     else "iNaturalist did not answer; waiting 60 seconds and asking again ({} of 5).").format(waits))
                G._sleep(60, cancelled)
                continue
            raise
        waits = 0                                    # the allowance is for one stretch of trouble, not for the run
        results = _results(page)
        if not results:
            break
        f.write(json.dumps(results, separators=(",", ":")) + "\n")
        n += len(results)
        for o in results:
            t = o.get("taxon") or {}
            if t.get("id") is not None and t["id"] not in taxa:
                taxa[t["id"]] = {"id": t["id"], "rank": t.get("rank"), "name": t.get("name"),
                                 "ancestor_ids": t.get("ancestor_ids")}
        last_id = results[-1].get("id")
        frac = min(1.0, n / float(max(1, total)))
        left = (time.time() - t0) * (1 - frac) / frac if frac > 0.02 else None
        step(frac * share, "Fetched {} of {} observations{}".format(
            n0(n), n0(total), "" if left is None else "; about {} left".format(span(left))))
        if len(results) < PAGE or last_id is None:
            break
        if n > total * 1.1 + 10 * PAGE:
            break                                    # never run away from the count
    say("Fetched {} observations in {}.".format(n0(n), span(time.time() - t0)))
    return n, taxa


def lineages(taxa, step, cancelled, base=80.0, width=7.0):
    """{taxon id: {rank: name}} for the taxa of the observations
    ({taxon id: taxon}, as fetch returns them).

    An observation carries its taxon's name and rank and the ids of the
    taxon's ancestors, but not the ancestors' names. Those are asked for
    30 taxa at a time, the deepest taxa first; a taxon whose ancestors
    are all known by then (another species of a genus already asked for)
    costs no request.
    """
    known = {}                                       # id -> (rank, name)
    for tid, t in taxa.items():
        known[tid] = (t.get("rank") or "", t.get("name") or "")

    def missing(t):
        return [a for a in (t.get("ancestor_ids") or []) if a not in known]

    todo = sorted(taxa.values(), key=lambda t: -len(t.get("ancestor_ids") or []))
    asked = 0
    i = 0
    while i < len(todo):
        batch = []
        while i < len(todo) and len(batch) < TAXA_PER_REQUEST:
            if missing(todo[i]):
                batch.append(todo[i])
            i += 1
        if not batch:
            break
        if cancelled():
            raise Cancelled()
        r = _json(API + "taxa/" + ",".join(str(t["id"]) for t in batch)
                  + "?fields=(id:!t,ancestors:(id:!t,name:!t,rank:!t))&per_page={}".format(TAXA_PER_REQUEST))
        for t in _results(r):
            for a in t.get("ancestors") or []:
                if a.get("id") is not None:
                    known[a["id"]] = (a.get("rank") or "", a.get("name") or "")
        asked += 1
        step(base + width * min(1.0, i / float(max(1, len(todo)))),
             "Reading the classification of {} taxa: {} of them done".format(n0(len(todo)), n0(i)))
    out = {}
    for tid, t in taxa.items():
        line = {}
        for a in list(t.get("ancestor_ids") or []) + [tid]:
            rank, name = known.get(a, ("", ""))
            if rank in RANK_FIELDS and name:
                line[rank] = name
        out[tid] = line
    return out, len(taxa), asked


# ---------------------------------------------------------------------------
#  the rows
# ---------------------------------------------------------------------------
def _yes_no(v):
    return None if v is None else ("Yes" if v else "No")


def _text(v):
    if v is None:
        return None
    s = str(v).replace("\r\n", "\n").strip()
    return s or None


def rows(observations, lines, labels):
    """(lon, lat, has coordinates, {field: value}) per observation."""
    for o in observations:
        t = o.get("taxon") or {}
        line = lines.get(t.get("id")) or {}
        d = o.get("observed_on_details") or {}
        u = o.get("user") or {}
        photos = o.get("photos") or []
        lon = lat = None
        has = False
        loc = o.get("location")
        if isinstance(loc, str) and "," in loc:
            try:
                a, b = loc.split(",", 1)
                lat, lon = float(a), float(b)
                has = True
            except ValueError:
                has = False
        oid = o.get("id")
        v = {
            "inatID": _text(oid),
            "scientificName": _text(t.get("name")) or _text(o.get("species_guess")),
            "commonName": _text(t.get("preferred_common_name")),
            "taxonRank": _text(t.get("rank")),
            "iconicTaxon": _text(t.get("iconic_taxon_name")),
            "taxonID": _text(t.get("id")),
            "eventDate": _text(o.get("observed_on")),
            "eventTime": _text(o.get("time_observed_at")),
            "year": d.get("year"), "month": d.get("month"), "day": d.get("day"),
            "decimalLatitude": lat, "decimalLongitude": lon,
            "coordUncert_m": o.get("public_positional_accuracy"),
            "statedAccuracy_m": o.get("positional_accuracy"),
            "obscured": _yes_no(bool(o.get("obscured"))),
            "geoprivacy": _text(o.get("geoprivacy")),
            "taxonGeoprivacy": _text(o.get("taxon_geoprivacy")),
            "locality": _text(o.get("place_guess")),
            "qualityGrade": _text(o.get("quality_grade")),
            "captive": _yes_no(o.get("captive")),
            "idCount": o.get("identifications_count"),
            "idAgree": o.get("num_identification_agreements"),
            "idDisagree": o.get("num_identification_disagreements"),
            "native": _yes_no(t.get("native")) if t else None,
            "introduced": _yes_no(t.get("introduced")) if t else None,
            "endemic": _yes_no(t.get("endemic")) if t else None,
            "threatened": _yes_no(t.get("threatened")) if t else None,
            "recordedBy": _text(u.get("name")) or _text(u.get("login")),
            "observerLogin": _text(u.get("login")),
            "license": _text(o.get("license_code")) or NO_LICENSE,
            "photoCount": len(photos),
            "soundCount": len(o.get("sounds") or []),
            "description": _text(o.get("description")),
            "createdAt": _text(o.get("created_at")),
            "recordURL": None if oid is None else SITE + "observations/{}".format(oid),
        }
        for rank, field in RANK_FIELDS.items():
            v[field] = line.get(rank)
        if photos:
            p = photos[0]
            v["photoURL"] = _text((p.get("url") or "").replace("/square.", "/medium."))
            v["photoLicense"] = _text(p.get("license_code")) or NO_LICENSE
            v["photoCredit"] = _text(p.get("attribution"))
        notes = {}
        for a in o.get("annotations") or []:
            field = ANNOTATION_FIELDS.get(a.get("controlled_attribute_id"))
            if field is None or (a.get("vote_score") or 0) < 0:
                continue
            label = labels.get((a.get("controlled_attribute_id"), a.get("controlled_value_id")))
            if label and label not in notes.setdefault(field, []):
                notes[field].append(label)
        for field, vals in notes.items():
            v[field] = "; ".join(vals)
        yield lon, lat, has, v


class WriteResult(object):
    def __init__(self):
        self.written = 0
        self.skipped_no_coordinates = 0
        self.skipped_outside_area = 0
        self.truncated = 0
        self.obscured = 0
        self.by_license = {}
        self.by_grade = {}
        self.taxa = set()


def write_points(out_fc, records, keep=None, progress=None, cancelled=None):
    """Creates the point feature class (WGS 84) and writes the rows. An
    existing feature class of that name is replaced."""
    import arcpy
    import mahalanobis

    r = WriteResult()
    ws, name = os.path.split(out_fc)
    if arcpy.Exists(out_fc):
        arcpy.management.Delete(out_fc)
    sr = mahalanobis.clean_sr(arcpy.SpatialReference(4326))
    arcpy.management.CreateFeatureclass(ws, name, "POINT", has_m="DISABLED", has_z="DISABLED",
                                        spatial_reference=sr)
    types = {"T": "TEXT", "I": "LONG", "D": "DOUBLE"}
    arcpy.management.AddFields(out_fc, [
        [f[0], types[f[2]], f[1], f[3] if f[2] == "T" else None] for f in FIELDS])
    kinds = [(f[0], f[2], f[3]) for f in FIELDS]
    try:
        seen = _write_rows(arcpy, out_fc, records, keep, kinds, r, progress, cancelled)
    except BaseException:
        G.discard_output(out_fc)        # cancelled or failed part way: no half-written output is left
        raise
    if progress is not None:
        progress(seen)
    return r


def _write_rows(arcpy, out_fc, records, keep, kinds, r, progress, cancelled):
    seen = 0
    with arcpy.da.InsertCursor(out_fc, ["SHAPE@XY"] + FIELD_NAMES) as cur:
        for lon, lat, has, vals in records:
            seen += 1
            if cancelled is not None and seen % 500 == 0 and cancelled():
                raise Cancelled()
            if not has or lat < -90 or lat > 90 or lon < -180 or lon > 180 or (lon == 0 and lat == 0):
                r.skipped_no_coordinates += 1
                continue
            if keep is not None and not keep(lon, lat):
                r.skipped_outside_area += 1
                continue
            row = [(lon, lat)]
            for fname, kind, length in kinds:
                v = vals.get(fname)
                if v is None:
                    row.append(None)
                elif kind == "I":
                    try:
                        row.append(int(v))
                    except (TypeError, ValueError):
                        row.append(None)
                elif kind == "D":
                    try:
                        row.append(float(v))
                    except (TypeError, ValueError):
                        row.append(None)
                else:
                    s = str(v)
                    if len(s) > length:
                        r.truncated += 1
                        s = s[:length]
                    row.append(s)
            cur.insertRow(row)
            r.written += 1
            if vals.get("obscured") == "Yes":
                r.obscured += 1
            lic = vals.get("license") or NO_LICENSE
            r.by_license[lic] = r.by_license.get(lic, 0) + 1
            g = vals.get("qualityGrade") or "(not given)"
            r.by_grade[g] = r.by_grade.get(g, 0) + 1
            if vals.get("taxonID"):
                r.taxa.add(vals["taxonID"])
            if progress is not None and r.written % 1000 == 0:
                progress(seen)
    return seen


# ---------------------------------------------------------------------------
#  the report and the metadata
# ---------------------------------------------------------------------------
LICENSE_WORDS = {
    "cc0": "CC0 (no restriction)",
    "cc-by": "CC BY (credit required)",
    "cc-by-nc": "CC BY-NC (credit required, non-commercial use only)",
    "cc-by-sa": "CC BY-SA (credit required, share alike)",
    "cc-by-nd": "CC BY-ND (credit required, no derivatives)",
    "cc-by-nc-sa": "CC BY-NC-SA (credit required, non-commercial use only, share alike)",
    "cc-by-nc-nd": "CC BY-NC-ND (credit required, non-commercial use only, no derivatives)",
    NO_LICENSE: "all rights reserved (no license given: ask the observer before reusing the record)",
}

DESCRIPTION = (
    "Observations from iNaturalist (iNaturalist.org), written by the iNaturalist Observations tool of "
    "the Wildlife and Forestry Tools for ArcGIS Pro. Each row is one observation: a person's record "
    "of an organism at a place and time, usually with a photograph, identified by the iNaturalist "
    "community. Coordinates are WGS 84 and are the location iNaturalist shows to the public. Where "
    "the obscured field is Yes, that location is a random point within about 0.2 degrees of the true "
    "one, so the point must not be used for work finer than that. Identifications are made by "
    "volunteers: the qualityGrade field tells whether the community agrees on one (research), has "
    "not yet agreed (needs_id) or the observation lacks what is needed to check it (casual). Every "
    "observation carries its observer's own license (the license field).")


def inat_links(query, taxa_labels, place_label):
    """The links of the output's metadata: the species or groups, the
    place, the observer and the project asked for, and iNaturalist's and
    GBIF's pages on citing the data. Each item is a line of HTML."""
    items = []
    for i, tid in enumerate(query.taxon_ids or []):
        label = taxa_labels[i] if i < len(taxa_labels) else "iNaturalist taxon " + str(tid)
        items.append("Species or group asked for: " + G.html_link(SITE + "taxa/" + str(tid), label))
    if query.place_id:
        items.append("Place asked for: " + G.html_link(SITE + "places/" + str(query.place_id),
                                                       place_label or "iNaturalist place " + str(query.place_id)))
    if query.observer:
        items.append("Observer asked for: " + G.html_link(SITE + "people/" + urllib.parse.quote(str(query.observer)),
                                                          str(query.observer)))
    if query.project:
        items.append("Project asked for: " + G.html_link(SITE + "projects/" + urllib.parse.quote(str(query.project)),
                                                         str(query.project)))
    items.append("Each observation's own page on iNaturalist, with its photographs and the discussion of its "
                 "identification, is in its recordURL field.")
    items.append("How iNaturalist asks to be cited: " + G.html_link(
        "https://help.inaturalist.org/en/support/solutions/articles/151000170344-how-should-i-cite-inaturalist-"))
    items.append("iNaturalist's research-grade observations at GBIF, a dataset with a DOI of its own: "
                 + G.html_link("https://doi.org/10.15468/ab3s5x"))
    items.append("iNaturalist's data exports for bulk use: " + G.html_link(SITE + "pages/developers"))
    items.append("iNaturalist: " + G.html_link("https://www.inaturalist.org"))
    return items


def stamp_metadata(out_fc, summary, cite, messages=None, how=None, links=None, licenses=None):
    """The description says where the observations came from, how to cite
    them, and links to the sources. Never raises: metadata is a courtesy,
    not the result."""
    try:
        import arcpy
        import tool_metadata

        md = arcpy.metadata.Metadata(out_fc)
        md.title = os.path.basename(out_fc)
        md.tags = "iNaturalist, species observation, citizen science, biodiversity, Wildlife and Forestry Tools"
        md.summary = summary
        md.description = (
            "<p>" + G.html_text(DESCRIPTION) + "</p>"
            + ("<p><b>Where these observations came from.</b> " + G.html_text(how) + "</p>" if how else "")
            + ("<p><b>How to cite them.</b> " + G.html_text(cite) + " iNaturalist issues no DOI for observations "
               "fetched from it. It prefers that research cite a GBIF download where it can: the research-grade "
               "observations under the CC0, CC BY and CC BY-NC licenses are also in GBIF, where the GBIF Species "
               "Occurrences tool can request a download that carries a DOI.</p>" if cite else "")
            + "<p><b>Links to the sources:</b></p>" + G.html_list(links or []))
        md.credits = cite
        if licenses:
            md.accessConstraints = (
                "<p>Every observation carries the license its observer chose, held in its license field, and "
                "some carry none. In this feature class: " + G.html_text(licenses) + ".</p>"
                "<p>An observation with no license is all rights reserved: ask the observer before reusing "
                "the record. Photographs carry their own licenses (the photoLicense and photoCredit fields). "
                "Use of iNaturalist is subject to its terms of service: "
                + G.html_link("https://www.inaturalist.org/terms") + "</p>")
        md.save()
        tool_metadata.set_entity_and_fields(
            out_fc, entity="iNaturalist observations, one row per observation.",
            fields=[{"name": f[0], "description": f[4]} for f in FIELDS], messages=messages)
    except Exception as e:                                       # noqa: BLE001
        if messages is not None:
            try:
                messages.addWarningMessage("Could not write the output's metadata: {}".format(e))
            except Exception:                                    # noqa: BLE001
                pass


def describe(query, taxa_labels, place_label, area_words):
    parts = []
    if taxa_labels:
        parts.append("species or groups: " + "; ".join(taxa_labels))
    if place_label:
        parts.append("iNaturalist place: " + place_label)
    if area_words:
        parts.append("study area: " + area_words)
    grades = [g for g in GRADES if g in set(query.grades)] or list(GRADES)
    parts.append("quality grades: " + ", ".join(grades))
    if GRADE_CASUAL in grades:
        parts.append("captive or cultivated organisms {}".format(
            "left out" if query.exclude_captive else "included"))
    if query.date_from or query.date_to:
        parts.append("observed from {} to {}".format(query.date_from or "the earliest",
                                                     query.date_to or "the latest"))
    months = sorted(set(query.months))
    if 0 < len(months) < 12:
        parts.append("months: " + ", ".join(str(m) for m in months))
    if query.exclude_noncommercial:
        parts.append("only observations whose license allows commercial use")
    elif query.open_license_only:
        parts.append("only observations under CC0 or a Creative Commons license")
    if query.photos_only:
        parts.append("only observations with a photograph")
    if query.max_uncertainty_m is not None:
        parts.append("stated accuracy at most {} m (observations stating none left out)".format(
            G._num(query.max_uncertainty_m)))
    if query.exclude_obscured:
        parts.append("observations with an obscured location left out")
    if query.establishment != ESTABLISHMENT_ANY:
        parts.append("only taxa iNaturalist lists as {} at the place of the observation".format(
            query.establishment.lower()))
    if query.observer:
        parts.append("observer: " + query.observer)
    if query.project:
        parts.append("project: " + query.project)
    return "; ".join(parts)


def citation(taxa_labels, place_label, area_words, query, today):
    """The form iNaturalist's help gives for records exported from it."""
    what = "; ".join(taxa_labels) if taxa_labels else "all taxa"
    where = place_label or area_words or "the whole world"
    when = ""
    if query.date_from or query.date_to:
        when = " observed between {} and {}".format(query.date_from or "the earliest date",
                                                    query.date_to or today)
    return "iNaturalist community. Observations of {} from {}{}. Exported from {} on {}.".format(
        what, where, when, SITE.rstrip("/"), today)


# ---------------------------------------------------------------------------
#  the tool
# ---------------------------------------------------------------------------
def check_output(out_fc):
    why = G.check_output(out_fc)
    return None if why is None else why.replace("GBIF_occurrences", "iNaturalist_observations")


def _date(v, what):
    if v in (None, ""):
        return None
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y", "%m/%d/%Y %I:%M:%S %p", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise INatError("The {} date \"{}\" could not be read. Write it as YYYY-MM-DD.".format(what, s))


def run(out_fc, **kwargs):
    """Runs the tool (see ``_run``). Returns a dict: written, matched.
    The tool watches for Cancel itself; the session's own setting is put
    back afterwards."""
    import arcpy

    try:
        before = arcpy.env.autoCancelling
    except Exception:                                            # noqa: BLE001
        before = None
    try:
        return _run(out_fc, **kwargs)
    except G.GbifError as e:                         # from the shared study-area code
        raise INatError(str(e))
    finally:
        if before is not None:
            try:
                arcpy.env.autoCancelling = before
            except Exception:                                    # noqa: BLE001
                pass


def _run(out_fc, taxa=None, study_area=None, extent=None, place=None, quality_grades=None,
         exclude_captive=True, date_from=None, date_to=None, months=None, open_license_only=False,
         exclude_noncommercial=False, photos_only=False, max_uncertainty_m=None,
         exclude_obscured=False, establishment=None, observer=None, project=None,
         messages=None, area_words=None):
    import arcpy

    def say(text):
        if messages is not None:
            messages.addMessage(text)

    def warn(text):
        if messages is not None:
            messages.addWarningMessage(text)

    def step(percent, text):
        try:
            arcpy.SetProgressorLabel(text)
            arcpy.SetProgressorPosition(int(max(0, min(100, percent))))
        except Exception:                                        # noqa: BLE001
            pass

    def cancelled():
        try:
            return bool(arcpy.env.isCancelled)
        except Exception:                                        # noqa: BLE001
            return False

    why = check_output(out_fc)
    if why:
        raise INatError(why)
    out_fc = str(out_fc).strip().replace("/", "\\").rstrip("\\")
    if study_area is not None and extent is not None:
        raise INatError("Give a study area layer or a rectangle, not both.")
    try:
        arcpy.env.autoCancelling = False
    except Exception:                                            # noqa: BLE001
        pass
    arcpy.SetProgressor("step", "Reading the choices...", 0, 100, 1)

    q = Query()
    grades = [g for g in GRADES if g in set(quality_grades or GRADE_DEFAULT)]
    unknown = [g for g in (quality_grades or []) if g not in GRADES]
    if unknown:
        raise INatError("The quality grades are: {}.".format("; ".join(GRADES)))
    q.grades = grades or list(GRADE_DEFAULT)
    q.exclude_captive = bool(exclude_captive)
    q.date_from = _date(date_from, "first")
    q.date_to = _date(date_to, "last")
    if q.date_from and q.date_to and q.date_from > q.date_to:
        raise INatError("The first date is later than the last date.")
    q.months = [int(m) for m in (months or [])]
    q.open_license_only = bool(open_license_only)
    q.exclude_noncommercial = bool(exclude_noncommercial)
    q.photos_only = bool(photos_only)
    if max_uncertainty_m not in (None, ""):
        if float(max_uncertainty_m) <= 0:
            raise INatError("The accuracy must be a number of meters greater than zero, or empty.")
        q.max_uncertainty_m = float(max_uncertainty_m)
    q.exclude_obscured = bool(exclude_obscured)
    q.establishment = establishment or ESTABLISHMENT_ANY
    if q.establishment not in ESTABLISHMENTS:
        raise INatError("Native or introduced must be one of: {}.".format("; ".join(ESTABLISHMENTS)))
    q.observer = (observer or "").strip() or None
    q.project = (project or "").strip() or None

    polygons = G.study_area_polygons(study_area, extent)
    cut = False
    tilted = False
    if polygons:
        xs = [p[0] for poly in polygons for p in poly[0]]
        ys = [p[1] for poly in polygons for p in poly[0]]
        q.box = (max(-180.0, min(xs)), max(-90.0, min(ys)), min(180.0, max(xs)), min(90.0, max(ys)))
        q.polygons = polygons
        cut = study_area is not None
        if not cut:
            # A rectangle given in a projected coordinate system is not a rectangle of longitude and
            # latitude: its sides lean and curve. iNaturalist can only be asked for the longitude-latitude
            # rectangle around it, so the observations are cut to the rectangle itself afterwards.
            inside = sum(abs(G.signed_area(poly[0])) for poly in polygons)
            around = (max(xs) - min(xs)) * (max(ys) - min(ys))
            tilted = cut = around > 0 and inside < around * (1.0 - 1e-6)
        if tilted:
            say("The rectangle is not square to the lines of longitude and latitude (it was given in a projected "
                "coordinate system), and iNaturalist searches only by rectangles that are. The observations are "
                "asked for in the longitude-latitude rectangle around it (longitude {} to {}, latitude {} to {}) "
                "and cut to the rectangle itself when they are written.".format(
                    G._num(q.box[0]), G._num(q.box[2]), G._num(q.box[1]), G._num(q.box[3])))
        elif cut:
            say("iNaturalist searches by rectangle, not by polygon. The observations are asked for in the "
                "rectangle around the study area (longitude {} to {}, latitude {} to {}) and cut to the "
                "study area's polygons when they are written.".format(
                    G._num(q.box[0]), G._num(q.box[2]), G._num(q.box[1]), G._num(q.box[3])))

    q.taxon_ids, taxa_labels = resolve_taxa(taxa, say)
    q.place_id, place_label = resolve_place(place, say)
    if not (q.taxon_ids or q.place_id or q.box or q.observer or q.project):
        raise INatError("Choose at least one species or group, a study area, an iNaturalist place, an "
                        "observer or a project. With none of them, the request is every observation "
                        "iNaturalist holds.")
    area = area_words or ("a rectangle" if (tilted or (polygons and not cut)) else "a polygon layer" if cut else None)
    choices = describe(q, taxa_labels, place_label, area)

    step(0, "Counting...")
    total = count(q)
    where = (" in the longitude-latitude rectangle around the rectangle given" if tilted
             else " in the rectangle around the study area" if cut else "")
    say("iNaturalist has no observations that match{}.".format(where) if total == 0 else
        "iNaturalist has 1 observation that matches{}.".format(where) if total == 1 else
        "iNaturalist has {} observations that match{}.".format(n0(total), where))
    if total > MAX_OBSERVATIONS:
        raise INatError(
            "There are {} observations{}, and this tool does not fetch more than {} in one run. Nothing "
            "was fetched. The limit is there for two reasons. iNaturalist says its service is meant for "
            "applications and not for copying its data in bulk, and the tool respects that. And "
            "iNaturalist asks each user to make fewer than {} requests a day, which at 200 observations a "
            "request is {} observations, and says it may block, without notice, a computer that goes "
            "beyond what it asks; the limit keeps that from happening to you. Narrow the species, the "
            "area, the dates or the quality grades. For research-grade observations in bulk, the GBIF "
            "Species Occurrences tool can request a GBIF download of any size, and iNaturalist publishes "
            "data exports of its own (https://www.inaturalist.org/pages/developers).".format(
                n0(total), where, n0(MAX_OBSERVATIONS), n0(DAILY_REQUESTS), n0(MAX_OBSERVATIONS)))
    if total > LARGE_REQUEST:
        warn("That is a large request: {} observations{}. iNaturalist's service gives 200 observations a "
             "request and this tool makes no more than one request a second, so fetching them will take "
             "about {}. The tool can be cancelled at any time. Narrowing the species, the area, the dates "
             "or the quality grades shortens it. For research-grade observations in bulk, the GBIF Species "
             "Occurrences tool can request a GBIF download of any size: iNaturalist publishes its "
             "research-grade observations under the CC0, CC BY and CC BY-NC licenses to GBIF.".format(
                 n0(total), where, span(seconds_to_fetch(total))))
    elif total > 5000:
        say("At 200 observations a request this will take about {}.".format(span(seconds_to_fetch(total))))

    handle, spool = tempfile.mkstemp(prefix="inaturalist_", suffix=".jsonl.gz")
    os.close(handle)
    try:
        fetched, taxa_found = fetch(q, total, say, step, cancelled, spool) if total > 0 else (0, {})
        step(80, "Reading the classification...")
        lines, n_taxa, n_asked = lineages(taxa_found, step, cancelled) if fetched else ({}, 0, 0)
        labels = {}
        if fetched:
            try:
                labels = annotation_labels()
            except INatError as e:
                warn("iNaturalist's list of annotation values could not be read ({}), so the annotation "
                     "fields (life stage, sex and so on) are empty.".format(e))
        step(88, "Writing the points...")
        expect = max(1, fetched)
        r = write_points(
            out_fc, rows(spooled(spool), lines, labels), keep=q.contains if cut else None,
            progress=lambda n: step(88 + 10 * min(1.0, n / float(expect)),
                                    "Writing the points: {}".format(n0(n))),
            cancelled=cancelled)
    finally:
        try:
            os.remove(spool)
        except OSError:
            pass

    if total == 0:
        warn("No observations matched, so the output feature class is empty. Widen the study area, the "
             "dates or the quality grades.")
    elif r.written == 0:
        warn("No observation could be written as a point, so the output feature class is empty.")
    say("Wrote {} points to {}.{}{}{}".format(
        n0(r.written), out_fc,
        " Left out {} outside {}.".format(n0(r.skipped_outside_area),
                                         "the rectangle" if tilted else "the study area's polygons")
        if r.skipped_outside_area else "",
        " Left out {} without coordinates (observations whose location the observer keeps private "
        "have none).".format(n0(r.skipped_no_coordinates)) if r.skipped_no_coordinates else "",
        " {} long text values were shortened to fit their fields.".format(n0(r.truncated))
        if r.truncated else ""))
    if r.written:
        say("They are of {} {}. Quality grades: {}.".format(
            n0(len(r.taxa)), "taxon" if len(r.taxa) == 1 else "taxa", "; ".join("{}: {}".format(k, n0(v)) for k, v in
                                       sorted(r.by_grade.items(), key=lambda kv: -kv[1]))))
    unverified = sum(v for k, v in r.by_grade.items() if k != "research")
    if unverified:
        warn("{} of the points are not research grade: nobody has confirmed their identification, or "
             "they lack what is needed to check it. Use the qualityGrade, idAgree and idDisagree fields "
             "to judge them.".format(n0(unverified)))
    if r.obscured:
        warn("{} of the points have an OBSCURED location (the obscured field is Yes): iNaturalist shows a "
             "random point within about 0.2 degrees of latitude and longitude of the true one, which can be "
             "as much as about 30 kilometers away. They are observations of species at risk, or ones whose "
             "observers chose to hide the place. Do not use those points for work finer than that. "
             "Their coordUncert_m field gives the size of the area.".format(n0(r.obscured)))
    if r.by_license:
        say("Licenses of the observations: {}.".format("; ".join(
            "{}: {}".format(LICENSE_WORDS.get(k, k), n0(v))
            for k, v in sorted(r.by_license.items(), key=lambda kv: -kv[1]))))
    today = datetime.date.today().strftime("%Y-%m-%d")
    cite = citation(taxa_labels, place_label, area, q, today)
    say("iNaturalist asks that records exported from it be cited in this form: " + cite)
    say("iNaturalist prefers that research cite a GBIF download where it can. The research-grade "
        "observations under the CC0, CC BY and CC BY-NC licenses are also in GBIF, where the GBIF Species "
        "Occurrences tool can request a download that carries a DOI. To credit a single observation or "
        "photograph, name its observer (the recordedBy and photoCredit fields) and give its web address "
        "(the recordURL field).")
    stamp_metadata(
        out_fc, "Created {} by the iNaturalist Observations tool, fetched directly from iNaturalist.org. "
        "Choices: {}. {} observations matched{}; {} points written{}. Licenses: {}.".format(
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), choices, n0(total), where, n0(r.written),
            ", {} of them with an obscured location".format(n0(r.obscured)) if r.obscured else "",
            "; ".join("{}: {}".format(k, n0(v)) for k, v in
                      sorted(r.by_license.items(), key=lambda kv: -kv[1])) or "none"),
        cite, messages,
        how="Fetched directly from iNaturalist.org on {}, for these choices: {}. iNaturalist's holdings change "
            "by the minute, as observations are added and identifications change, so the same request on "
            "another day returns a different set.".format(today, choices),
        links=inat_links(q, taxa_labels, place_label),
        licenses="; ".join("{}: {}".format(LICENSE_WORDS.get(k, k), n0(v)) for k, v in
                           sorted(r.by_license.items(), key=lambda kv: -kv[1])))
    step(100, "Done: {} points.".format(n0(r.written)))
    return {"written": r.written, "matched": total}
