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

"""iNaturalist Observations (inaturalist_observations.py). Nothing here
touches iNaturalist or the internet: a stand-in service on this computer
(stand_in_service.py) answers in iNaturalist's form with made-up
observations, and a guard stops any attempt to reach another computer. The
engine's waits (a second between requests, a minute when asked to slow down)
run against a clock of the test's own, so the suite takes seconds.

Run with Pro's Python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" inaturalist_observations_test.py
"""
import datetime
import glob
import os
import shutil
import sys
import tempfile
import time as real_time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "WildlifeTools", "Toolboxes"))
sys.path.insert(0, HERE)
import arcpy  # noqa: E402
import gbif_occurrences as G  # noqa: E402
import inaturalist_observations as I  # noqa: E402
from stand_in_service import StandIn, no_internet, pointed  # noqa: E402

guard = no_internet()                    # from here on, nothing may reach another computer
passed = failed = known = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print("FAIL  %s  %s" % (name, detail))


def known_fault(name, ok, detail=""):
    """A fault that has been found and reported and is not yet fixed: shown on every run, and not counted as a
    failure. When the engine is corrected this says so, and the line is to become an ordinary check()."""
    global known, passed
    if ok:
        passed += 1
        print("NOW PASSES (make it an ordinary check)  %s" % name)
    else:
        known += 1
        print("KNOWN FAULT, not yet fixed  %s  %s" % (name, detail))


def error_of(fn, kind=Exception):
    try:
        fn()
        return None
    except kind as e:
        return e


def refused(name, fn, has, status=None):
    e = error_of(fn)
    check(name, isinstance(e, I.INatError) and has in str(e) and (status is None or e.status == status),
          "%s: %s" % (type(e).__name__, e))


class Msgs:
    def __init__(self):
        self.lines, self.warnings = [], []

    def addMessage(self, s):
        self.lines.append(s)

    def addWarningMessage(self, s):
        self.warnings.append(s)

    def has(self, text):
        return any(text in s for s in self.lines)

    def warned(self, text):
        return any(text in s for s in self.warnings)


class Clock:
    """Stands in for the engine's ``time``: sleeping takes no time and is written down."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def time(self):
        self.now += 0.001                # a request takes a moment
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(float(seconds), 3))
        self.now += float(seconds)


# ---------------------------------------------------------------------------------------------------------
#  a made-up iNaturalist
# ---------------------------------------------------------------------------------------------------------
NAMES = {1: ("kingdom", "Animalia"), 2: ("phylum", "Chordata"), 40151: ("class", "Mammalia"),
         152870: ("order", "Artiodactyla"), 42427: ("family", "Antilocapridae"), 42428: ("genus", "Antilocapra"),
         42429: ("species", "Antilocapra americana"), 42158: ("family", "Cervidae"), 42219: ("genus", "Odocoileus"),
         5000: ("species", "Odocoileus hemionus"), 5001: ("subspecies", "Odocoileus hemionus eremicus"),
         355675: ("subphylum", "Vertebrata")}
ANCESTORS = {42429: [1, 2, 355675, 40151, 152870, 42427, 42428], 42428: [1, 2, 355675, 40151, 152870, 42427],
             5000: [1, 2, 355675, 40151, 152870, 42158, 42219], 5001: [1, 2, 355675, 40151, 152870, 42158, 42219, 5000]}
COMMON = {42429: "Pronghorn", 5000: "Mule Deer", 5001: "Burro Mule Deer"}


def taxon(tid, **over):
    t = {"id": tid, "name": NAMES[tid][1], "rank": NAMES[tid][0], "preferred_common_name": COMMON.get(tid),
         "iconic_taxon_name": "Mammalia", "ancestor_ids": ANCESTORS[tid], "threatened": False, "introduced": False,
         "native": True, "endemic": False}
    t.update(over)
    return t


def obs(i, lat=35.2, lon=-111.6, tid=42429, **over):
    o = {"id": i, "uri": "https://www.inaturalist.org/observations/%d" % i, "quality_grade": "research",
         "captive": False, "observed_on": "2024-05-17", "time_observed_at": "2024-05-17T08:30:00-07:00",
         "created_at": "2024-05-18T10:00:00-07:00",
         "observed_on_details": {"year": 2024, "month": 5, "day": 17, "hour": 8},
         "location": None if lat is None else "%s,%s" % (lat, lon), "positional_accuracy": 12,
         "public_positional_accuracy": 12, "obscured": False, "geoprivacy": None, "taxon_geoprivacy": "open",
         "place_guess": "Flagstaff, AZ, USA", "species_guess": "Pronghorn", "description": None,
         "license_code": "cc-by-nc", "identifications_count": 3, "num_identification_agreements": 2,
         "num_identification_disagreements": 0, "user": {"login": "maria", "name": "Maria Lopez"},
         "taxon": taxon(tid), "annotations": [], "photos": [], "sounds": []}
    o.update(over)
    return o


TERMS = [{"id": 1, "label": "Life Stage", "values": [{"id": 2, "label": "Adult"}, {"id": 8, "label": "Juvenile"}]},
         {"id": 9, "label": "Sex", "values": [{"id": 10, "label": "Female"}, {"id": 11, "label": "Male"}]},
         {"id": 22, "label": "Evidence of Presence", "values": [{"id": 24, "label": "Organism"}, {"id": 26, "label": "Track"}]},
         {"id": 17, "label": "Alive or Dead", "values": [{"id": 18, "label": "Alive"}, {"id": 19, "label": "Dead"}]}]
PLACES = [{"id": 40, "name": "Arizona", "display_name": "Arizona, US", "admin_level": 10},
          {"id": 2764, "name": "Arizona", "display_name": "Arizona, Atl\u00e1ntida, HN", "admin_level": 20},
          {"id": 900, "name": "Coconino", "display_name": "Coconino County, US, AZ", "admin_level": 20},
          {"id": 901, "name": "Kaibab", "display_name": "Kaibab National Forest, AZ, US", "admin_level": None},
          {"id": 902, "name": "Kaibab", "display_name": "Kaibab Plateau, AZ, US", "admin_level": None}]


class FakeINat:
    """What the stand-in answers: a list of observations and a count that may differ from it on purpose."""

    def __init__(self, web):
        self.web = web
        self.observations = []
        self.says_total = None           # what the count says, when not the true number
        web.answer("/v1/controlled_terms", json={"results": TERMS})
        web.answer("/v1/taxa/", handler=self.taxon_by_id)
        web.answer("/v1/taxa", handler=self.taxa_by_name)
        web.answer("/v1/places/autocomplete", handler=self.places_by_name)
        web.answer("/v1/places/", handler=self.place_by_id)
        web.answer("/v1/observations", handler=self.count)
        web.answer("/v2/observations", handler=self.page)
        web.answer("/v2/taxa/", handler=self.ancestors)

    def taxon_by_id(self, q):
        tid = q.path.rsplit("/", 1)[1]
        if not tid.isdigit() or int(tid) not in ANCESTORS:
            return 404, {}, {"error": "Not found", "status": 404}
        return {"results": [taxon(int(tid))]}

    def taxa_by_name(self, q):
        low = (q.one("q") or "").lower()
        hits = [taxon(t) for t in ANCESTORS if low in NAMES[t][1].lower() or low in (COMMON.get(t) or "").lower()]
        return {"results": hits}

    def places_by_name(self, q):
        low = (q.one("q") or "").lower()
        return {"results": [p for p in PLACES if low in p["name"].lower() or low in p["display_name"].lower()]}

    def place_by_id(self, q):
        pid = q.path.rsplit("/", 1)[1]
        hits = [p for p in PLACES if str(p["id"]) == pid]
        return {"results": hits} if hits else (404, {}, {"error": "Not found", "status": 404})

    def count(self, q):
        return {"total_results": len(self.observations) if self.says_total is None else self.says_total,
                "page": 1, "per_page": 0, "results": []}

    def page(self, q):
        above = int(q.one("id_above") or 0)
        left = [o for o in self.observations if o["id"] > above]
        return {"results": left[:int(q.one("per_page") or 30)]}

    def ancestors(self, q):
        ids = [int(x) for x in q.path.rsplit("/", 1)[1].split(",")]
        return {"results": [{"id": t, "ancestors": [{"id": a, "name": NAMES[a][1], "rank": NAMES[a][0]}
                                                     for a in ANCESTORS.get(t, [])]} for t in ids]}


work = tempfile.mkdtemp(prefix="inat_test_")
arcpy.env.overwriteOutput = True
gdb = arcpy.management.CreateFileGDB(work, "t.gdb")[0]
web = StandIn()
clock = Clock()
waits = []                               # the long waits the engine asks the shared helper for


def long_wait(seconds, cancelled):
    waits.append(seconds)
    if cancelled():
        raise I.Cancelled()


def fresh():
    web.forget()
    del waits[:]
    del clock.slept[:]
    I._last_request[0] = 0.0
    return FakeINat(web)


point_engine = pointed(I, API=web.url + "v2/", API1=web.url + "v1/", time=clock)
point_engine.__enter__()
point_wait = pointed(G, _sleep=long_wait)
point_wait.__enter__()

# =========================================================================================================
#  1. what is asked: the choices as the service's parameters
# =========================================================================================================
q = I.Query()
check("choices: with nothing changed, research-grade observations only", q.params() == {"quality_grade": "research"}, q.params())
q.grades = list(I.GRADES)
check("choices: all three grades ask for no grade at all, and then captive organisms are left out by a "
      "parameter of their own", q.params() == {"captive": "false"}, q.params())
q.exclude_captive = False
check("choices: all three grades, captive organisms included: nothing is asked", q.params() == {}, q.params())
q.grades = [I.GRADE_CASUAL, I.GRADE_RESEARCH]
q.exclude_captive = True
check("choices: two grades are named in iNaturalist's own words and order",
      q.params() == {"quality_grade": "research,casual", "captive": "false"}, q.params())
q = I.Query()
q.taxon_ids, q.place_id, q.box = ["42429", "5000"], "40", (-112.123456789, 34.5, -110.0, 36.25)
check("choices: species, place and rectangle (corners to six decimals of a degree)",
      dict((k, v) for k, v in q.params().items() if k != "quality_grade")
      == {"taxon_id": "42429,5000", "place_id": "40", "swlng": "-112.123457", "swlat": "34.5", "nelng": "-110",
          "nelat": "36.25"}, q.params())
q = I.Query()
q.date_from, q.date_to, q.months = "2020-01-01", "2024-12-31", [7, 6, 6, 8]
check("choices: dates and months", (q.params().get("d1"), q.params().get("d2"), q.params().get("month"))
      == ("2020-01-01", "2024-12-31", "6,7,8"), q.params())
q.months = list(range(1, 13))
check("choices: all twelve months is no choice of months", "month" not in q.params())
q = I.Query()
q.open_license_only = True
lic_open = q.params().get("license")
q.exclude_noncommercial = True
check("choices: licenses: open ones, or only those that allow commercial use",
      lic_open == "cc0,cc-by,cc-by-nc,cc-by-sa,cc-by-nd,cc-by-nc-sa,cc-by-nc-nd"
      and q.params().get("license") == "cc0,cc-by,cc-by-sa,cc-by-nd", (lic_open, q.params().get("license")))
q = I.Query()
q.photos_only, q.max_uncertainty_m, q.exclude_obscured = True, 100.0, True
q.establishment, q.observer, q.project = I.ESTABLISHMENT_NATIVE, "maria", "arizona-mammals"
p = q.params()
check("choices: photographs, accuracy, obscured, native, observer and project",
      (p.get("photos"), p.get("acc_below"), p.get("geoprivacy"), p.get("taxon_geoprivacy"), p.get("native"),
       p.get("user_id"), p.get("project_id"), "introduced" in p)
      == ("true", "100", "open", "open", "true", "maria", "arizona-mammals", False), p)
q.establishment = I.ESTABLISHMENT_INTRODUCED
check("choices: introduced", q.params().get("introduced") == "true" and "native" not in q.params())

# small things
check("names: a name with its id, an id alone, a name alone",
      (I._name_and_id("Pronghorn (42429)"), I._name_and_id("Pronghorn [42429]"), I._name_and_id("42429"),
       I._name_and_id("Antilocapra americana"))
      == (("Pronghorn", "42429"), ("Pronghorn", "42429"), ("", "42429"), ("Antilocapra americana", None)))
check("dates: read in the forms a dialog and a script give them",
      [I._date(v, "first") for v in ("2024-05-17", "2024-05-17 00:00:00", "5/17/2024", "5/17/2024 12:00:00 AM",
                                     "2024-05-17T08:00:00", datetime.date(2024, 5, 17),
                                     datetime.datetime(2024, 5, 17, 9, 30), None, "")]
      == ["2024-05-17"] * 7 + [None, None])
refused("dates: one that cannot be read is refused, with the form to use", lambda: I._date("17 May", "first"),
        'The first date "17 May" could not be read. Write it as YYYY-MM-DD.')
check("time: 200 observations a request at about 2.2 seconds each", abs(I.seconds_to_fetch(100000) - 1100.0) < 1e-9,
      I.seconds_to_fetch(100000))
check("limits: one run stops at 2,000,000 observations, iNaturalist's 10,000 requests a day",
      (I.PAGE, I.DAILY_REQUESTS, I.MAX_OBSERVATIONS, I.PAUSE) == (200, 10000, 2000000, 1.0))
check("errors in words: asked to slow down, busy, and anything else with the service's own reason",
      I.explain(429, "").startswith("iNaturalist is limiting requests from this computer")
      and I.explain(503, "<html>").startswith("iNaturalist is busy or unavailable right now")
      and I.explain(422, '{"errors": [{"message": "Unknown user"}, {"message": "Try again"}]}')
      == "iNaturalist returned an error (422). Unknown user; Try again"
      and I.explain(400, '{"error": "bad request"}') == "iNaturalist returned an error (400). bad request")
q = I.Query()
q.date_from = "2020-01-01"
check("citation: in the form iNaturalist gives for exported records",
      I.citation(["Antilocapra americana"], "Arizona, US", None, q, "2026-11-23")
      == "iNaturalist community. Observations of Antilocapra americana from Arizona, US observed between 2020-01-01 "
         "and 2026-11-23. Exported from https://www.inaturalist.org on 2026-11-23."
      and I.citation([], None, None, I.Query(), "2026-11-23")
      == "iNaturalist community. Observations of all taxa from the whole world. Exported from "
         "https://www.inaturalist.org on 2026-11-23.")

# =========================================================================================================
#  2. talking to the service
# =========================================================================================================
inat = fresh()
web.answer("/v1/ping", json={"ok": True})
with pointed(I, PAUSE=1.0):
    for _ in range(4):
        I._json(web.url + "v1/ping")
asked = web.asked("/v1/ping")
check("requests: each says who is asking and that it wants JSON",
      len(asked) == 4 and all(a.headers.get("user-agent") == I.USER_AGENT and a.headers.get("accept") == "application/json"
                              for a in asked), [a.headers for a in asked[:1]])
check("requests: no more than one a second (three waits of about a second for four requests)",
      len(clock.slept) == 3 and all(0.95 <= s <= 1.0 for s in clock.slept), clock.slept)
check("requests: the add-in names itself and no person", "WildlifeTools" in I.USER_AGENT and "@" not in I.USER_AGENT)

inat = fresh()
web.forget()
web.answer("/v1/busy", status=503, body="<html>Service Unavailable</html>", times=2)
web.answer("/v1/busy", json={"ok": 1})
with pointed(I, PAUSE=0.0):
    got = I._json(web.url + "v1/busy")
check("busy twice, then an answer: asked three times in all, after waits of 3 and 7 seconds",
      got == {"ok": 1} and len(web.asked("/v1/busy")) == 3 and clock.slept == [3.0, 7.0], (got, clock.slept))
web.forget()
del clock.slept[:]
web.answer("/v1/down", status=503, body="busy")
with pointed(I, PAUSE=0.0):
    refused("busy every time: given up after three tries, in words a user can act on",
            lambda: I._json(web.url + "v1/down"), "iNaturalist is busy or unavailable right now. Try again in a few minutes.", 503)
check("busy every time: three requests were made", len(web.asked("/v1/down")) == 3, len(web.asked("/v1/down")))
for status, words in ((429, "iNaturalist is limiting requests from this computer"), (404, "iNaturalist returned an error (404)."),
                      (400, "iNaturalist returned an error (400)."), (422, "iNaturalist returned an error (422).")):
    web.forget()
    web.answer("/v1/no", status=status, json={"error": "said the service"})
    with pointed(I, PAUSE=0.0):
        refused("status %d: reported at once, with the service's own reason, and not asked again" % status,
                lambda: I._json(web.url + "v1/no"), words, status)
    check("status %d: one request only" % status, len(web.asked("/v1/no")) == 1 and "said the service" in str(
        error_of(lambda: I._json(web.url + "v1/no"))), len(web.asked("/v1/no")))
web.forget()
web.answer("/v1/page", body="<html><body>Maintenance</body></html>", headers={"Content-Type": "text/html"})
with pointed(I, PAUSE=0.0):
    refused("a web page in place of data: asked again, then reported for what it is",
            lambda: I._json(web.url + "v1/page"), "iNaturalist returned something that is not the expected data.")
check("a web page in place of data: three tries", len(web.asked("/v1/page")) == 3)
web.forget()
web.fail("/v1/gone", "refuse")
with pointed(I, PAUSE=0.0):
    e = error_of(lambda: I._json(web.url + "v1/gone"))
check("a connection that is dropped: reported as a failure to reach iNaturalist or to get its answer, after three tries",
      isinstance(e, I.INatError) and ("could not be reached" in str(e) or "did not answer in time" in str(e))
      and len(web.asked("/v1/gone")) == 3, repr(e))
web.forget()
web.fail("/v1/cut", "cut", body='{"results": [' + "1," * 3000 + "1]}")
with pointed(I, PAUSE=0.0):
    e = error_of(lambda: I._json(web.url + "v1/cut"))
check("an answer cut short: never taken as a whole answer", isinstance(e, I.INatError), repr(e))
web.forget()
web.fail("/v1/quiet", "silence", seconds=4.0)
real_open = urllib.request.urlopen
with pointed(I, PAUSE=0.0), pointed(urllib.request, urlopen=lambda req, timeout=None: real_open(req, timeout=0.4)):
    t0 = real_time.monotonic()
    e = error_of(lambda: I._json(web.url + "v1/quiet", tries=2))
check("silence: the wait ends, and the message says iNaturalist did not answer",
      isinstance(e, I.INatError) and ("did not answer in time" in str(e) or "could not be reached" in str(e))
      and real_time.monotonic() - t0 < 4.0, (repr(e), real_time.monotonic() - t0))

# =========================================================================================================
#  3. names of species and places
# =========================================================================================================
inat = fresh()
said = []
with pointed(I, PAUSE=0.0):
    ids, labels = I.resolve_taxa("Pronghorn (42429); antilocapra AMERICANA\n5001; mule deer", said.append)
check("species: by id, by scientific name in any case, by common name; a repeat is taken once",
      ids == ["42429", "5001", "5000"] and labels == ["Antilocapra americana", "Odocoileus hemionus eremicus",
                                                      "Odocoileus hemionus"], (ids, labels))
check("species: each is reported with its common name, rank and id",
      said[0] == "Species or group: Antilocapra americana [Pronghorn] (species; iNaturalist taxon id 42429)."
      and len(said) == 4, said)
byname = web.asked(lambda r: r.path == "/v1/taxa")
check("species: a name is looked up among active taxa, 30 at most",
      len(byname) == 2 and byname[0].one("q") == "antilocapra AMERICANA" and byname[0].one("is_active") == "true"
      and byname[0].one("per_page") == "30", [(b.one("q"), b.query) for b in byname])
with pointed(I, PAUSE=0.0):
    refused("species: an id iNaturalist does not have", lambda: I.resolve_taxa("999999", said.append),
            "999999 is not an iNaturalist taxon id.")
    refused("species: a name that fits several taxa is not guessed at; the candidates are listed",
            lambda: I.resolve_taxa("odocoileus", said.append), 'iNaturalist has no taxon named exactly "odocoileus". '
            "The closest are: Odocoileus hemionus (species, Mule Deer, id 5000)")
    refused("species: a name that fits none", lambda: I.resolve_taxa("unicornus", said.append),
            'iNaturalist has no taxon named exactly "unicornus". Give the scientific name')
    check("species: nothing asked for is nothing to look up", I.resolve_taxa("", said.append) == ([], [])
          and I.resolve_taxa(None, said.append) == ([], []))
    said = []
    check("places: by id", I.resolve_place("900", said.append) == ("900", "Coconino County, US, AZ")
          and said == ["Place: Coconino County, US, AZ (iNaturalist place id 900)."], said)
    check("places: of two with the same name, the one of higher standing (a state before a county)",
          I.resolve_place("Arizona", said.append) == ("40", "Arizona, US"))
    check("places: by the name iNaturalist displays", I.resolve_place("'Kaibab Plateau, AZ, US'", said.append)[0] == "902")
    refused("places: two of the same name and no standing to choose by: both are listed",
            lambda: I.resolve_place("Kaibab", said.append), 'iNaturalist has more than one place named "Kaibab". The closest '
            "are: Kaibab National Forest, AZ, US (id 901); Kaibab Plateau, AZ, US (id 902).")
    refused("places: a name that fits none", lambda: I.resolve_place("Atlantis", said.append),
            'iNaturalist has no place named exactly "Atlantis".')
    refused("places: an id iNaturalist does not have", lambda: I.resolve_place("123456", said.append),
            "123456 is not an iNaturalist place id.")
    check("places: none asked for", I.resolve_place("  ", said.append) == (None, None))
    labels_ = I.annotation_labels()
check("annotations: the values' names are read from the service",
      labels_.get((1, 2)) == "Adult" and labels_.get((9, 11)) == "Male" and labels_.get((22, 26)) == "Track"
      and len(labels_) == 8, labels_)

# =========================================================================================================
#  4. fetching every page
# =========================================================================================================
def fetch_all(n, says=None, cancel_after=None, per_page=None):
    inat = fresh()
    inat.observations = [obs(1000 + i * 3) for i in range(n)]
    inat.says_total = says
    spool = os.path.join(work, "spool_%d.gz" % len(os.listdir(work)))
    said, steps = [], []
    calls = [0]

    def cancelled():
        calls[0] += 1
        return cancel_after is not None and len(web.asked("/v2/observations")) >= cancel_after

    q = I.Query()
    q.taxon_ids = ["42429"]
    with pointed(I, PAUSE=1.0):
        got = I.fetch(q, n if says is None else says, said.append, lambda p, t: steps.append((p, t)), cancelled, spool)
    return got, spool, said, steps


(n_got, taxa_found), spool, said, steps = fetch_all(450)
pages = web.asked("/v2/observations")
check("450 observations: three requests, of 200, 200 and 50", n_got == 450 and len(pages) == 3, (n_got, len(pages)))
check("pages: 200 at a time, in order of id, each starting after the last id of the one before",
      [(p.one("per_page"), p.one("order_by"), p.one("order"), p.one("id_above")) for p in pages]
      == [("200", "id", "asc", None), ("200", "id", "asc", str(1000 + 199 * 3)), ("200", "id", "asc", str(1000 + 399 * 3))],
      [(p.one("per_page"), p.one("id_above")) for p in pages])
check("pages: every request carries the choices and asks for the engine's list of fields",
      all(p.one("taxon_id") == "42429" and p.one("quality_grade") == "research" and p.one("fields") == I.OBSERVATION_FIELDS
          for p in pages))
check("pages: the requests are a second apart", clock.slept.count(1.0) + sum(1 for s in clock.slept if 0.95 <= s < 1.0) >= 2,
      clock.slept)
back = list(I.spooled(spool))
check("the observations are kept on disk, all of them and in order",
      [o["id"] for o in back] == [1000 + i * 3 for i in range(450)])
check("the taxa among them are noted once each", list(taxa_found) == [42429]
      and taxa_found[42429]["ancestor_ids"] == ANCESTORS[42429], taxa_found)
check("progress is reported as it goes, and the total at the end",
      steps and steps[0][1].startswith("Fetched 200 of 450 observations") and said[-1].startswith("Fetched 450 observations in "),
      (steps[:1], said[-1:]))
(n_got, _t), spool, said, steps = fetch_all(400)
check("exactly two full pages: a third request finds nothing, and the fetch ends with all 400",
      n_got == 400 and len(web.asked("/v2/observations")) == 3, (n_got, len(web.asked("/v2/observations"))))
(n_got, _t), spool, said, steps = fetch_all(0)
check("none at all: one request, nothing fetched", n_got == 0 and len(web.asked("/v2/observations")) == 1)
(n_got, _t), spool, said, steps = fetch_all(5000, says=100)
check("a service that keeps giving more than it counted (100): the fetch stops by itself, at 2,200",
      n_got == 2200 and len(web.asked("/v2/observations")) == 11, (n_got, len(web.asked("/v2/observations"))))
e = error_of(lambda: fetch_all(1000, cancel_after=2))
check("cancelled after two pages: the fetch stops there", isinstance(e, I.Cancelled) and len(web.asked("/v2/observations")) == 2,
      (repr(e), len(web.asked("/v2/observations"))))


def fetch_with_trouble(rules, n=450):
    inat = fresh()
    web.forget()
    for kind, status, times in rules:
        if kind == "status":
            web.answer("/v2/observations", status=status, json={"error": "trouble"}, times=times)
    inat = FakeINat(web)                 # the working answers come after the trouble
    inat.observations = [obs(1000 + i) for i in range(n)]
    said = []
    q = I.Query()
    q.taxon_ids = ["42429"]
    with pointed(I, PAUSE=0.0):
        try:
            got = I.fetch(q, n, said.append, lambda p, t: None, lambda: False, os.path.join(work, "trouble.gz"))
        except Exception as e:                                        # noqa: BLE001
            got = e
    return got, said


got, said = fetch_with_trouble([("status", 429, 2)])
check("asked to slow down twice: a minute's wait each time, said in the messages, and then everything is fetched",
      not isinstance(got, Exception) and got[0] == 450 and waits == [60, 60]
      and said[:2] == ["iNaturalist asked this computer to slow down; waiting 60 seconds (1 of 5).",
                       "iNaturalist asked this computer to slow down; waiting 60 seconds (2 of 5)."], (got, waits, said[:3]))
got, said = fetch_with_trouble([("status", 429, 6)])
check("asked to slow down six times in a row: given up after five waits",
      isinstance(got, I.INatError) and got.status == 429 and waits == [60] * 5, (repr(got), waits))
got, said = fetch_with_trouble([("status", 503, 3)])
check("busy for three requests (one round of tries): one wait of a minute, then everything is fetched",
      not isinstance(got, Exception) and got[0] == 450 and waits == [60]
      and said[0] == "iNaturalist did not answer; waiting 60 seconds and asking again (1 of 5).", (got, waits, said[:1]))
got, said = fetch_with_trouble([("status", 400, 1)])
check("a request the service refuses outright (400): reported at once, with no waiting",
      isinstance(got, I.INatError) and got.status == 400 and waits == [], (repr(got), waits))
got, said = fetch_with_trouble([("status", 429, 1)], n=450)
inat = fresh()
web.forget()
state = {"n": 0}


def trouble_between_pages(qr):
    state["n"] += 1
    if state["n"] in (2, 4):             # trouble before the second page, and again before the third
        return 429, {}, {"error": "slow down"}
    above = int(qr.one("id_above") or 0)
    return {"results": [obs(i) for i in range(above + 1, min(above + 201, 451))]}


web.answer("/v2/observations", handler=trouble_between_pages)
said = []
q = I.Query()
q.taxon_ids = ["42429"]
with pointed(I, PAUSE=0.0):
    n_got, _t = I.fetch(q, 450, said.append, lambda p, t: None, lambda: False, os.path.join(work, "between.gz"))
check("trouble between pages: no observation is lost or fetched twice",
      n_got == 450 and [o["id"] for o in I.spooled(os.path.join(work, "between.gz"))] == list(range(1, 451)), n_got)
check("trouble between pages: each stretch of trouble is counted from one again",
      [s for s in said if "slow down" in s] == ["iNaturalist asked this computer to slow down; waiting 60 seconds (1 of 5)."] * 2,
      said)

# the classification: asked for the deepest taxa first, and not at all for taxa whose ancestors are known by then
inat = fresh()
four = dict((t, {"id": t, "rank": NAMES[t][0], "name": NAMES[t][1], "ancestor_ids": ANCESTORS[t]}) for t in (42429, 5000, 5001, 42428))
with pointed(I, PAUSE=0.0, TAXA_PER_REQUEST=2):
    lines, n_taxa, n_asked = I.lineages(four, lambda p, t: None, lambda: False)
check("classification: kingdom to species for each taxon, from the names of its ancestors",
      lines[42429] == {"kingdom": "Animalia", "phylum": "Chordata", "class": "Mammalia", "order": "Artiodactyla",
                       "family": "Antilocapridae", "genus": "Antilocapra", "species": "Antilocapra americana"}
      and lines[5001] == {"kingdom": "Animalia", "phylum": "Chordata", "class": "Mammalia", "order": "Artiodactyla",
                          "family": "Cervidae", "genus": "Odocoileus", "species": "Odocoileus hemionus"}, (lines[42429], lines[5001]))
check("classification: an identification to genus has no species", "species" not in lines[42428]
      and lines[42428].get("genus") == "Antilocapra", lines[42428])
check("classification: four taxa cost one request, because the other two's ancestors were known by then",
      (n_taxa, n_asked) == (4, 1) and len(web.asked("/v2/taxa/")) == 1
      and web.asked("/v2/taxa/")[0].path == "/v2/taxa/5001,42429", (n_asked, [a.path for a in web.asked("/v2/taxa/")]))

# =========================================================================================================
#  5. one observation into one row
# =========================================================================================================
LABELS = dict(((c["id"], v["id"]), v["label"]) for c in TERMS for v in c["values"])
LINE = {42429: {"kingdom": "Animalia", "phylum": "Chordata", "class": "Mammalia", "order": "Artiodactyla",
                "family": "Antilocapridae", "genus": "Antilocapra", "species": "Antilocapra americana"}}
rich = obs(7001, lat=35.123456, lon=-111.654321, description="Two does\r\nand a fawn. ", obscured=True, geoprivacy="obscured",
           taxon_geoprivacy="obscured", public_positional_accuracy=28583, positional_accuracy=8, license_code=None,
           quality_grade="needs_id", captive=False,
           annotations=[{"controlled_attribute_id": 1, "controlled_value_id": 2, "vote_score": 1},
                        {"controlled_attribute_id": 9, "controlled_value_id": 10, "vote_score": 0},
                        {"controlled_attribute_id": 9, "controlled_value_id": 11, "vote_score": -1},
                        {"controlled_attribute_id": 22, "controlled_value_id": 24, "vote_score": 0},
                        {"controlled_attribute_id": 22, "controlled_value_id": 26, "vote_score": 2},
                        {"controlled_attribute_id": 22, "controlled_value_id": 26, "vote_score": 2},
                        {"controlled_attribute_id": 99, "controlled_value_id": 1, "vote_score": 5}],
           photos=[{"id": 1, "url": "https://static.inaturalist.org/photos/1/square.jpg", "license_code": None,
                    "attribution": "(c) Maria Lopez, all rights reserved"},
                   {"id": 2, "url": "https://static.inaturalist.org/photos/2/square.jpg", "license_code": "cc-by"}],
           sounds=[{"id": 5}], user={"login": "maria", "name": "Mar\u00eda L\u00f3pez"})
lon, lat, has, v = next(I.rows([rich], LINE, LABELS))
check("row: the place, as longitude and latitude from the service's 'latitude,longitude'",
      (lon, lat, has, v["decimalLatitude"], v["decimalLongitude"]) == (-111.654321, 35.123456, True, 35.123456, -111.654321))
check("row: what it is", (v["inatID"], v["scientificName"], v["commonName"], v["taxonRank"], v["iconicTaxon"], v["taxonID"])
      == ("7001", "Antilocapra americana", "Pronghorn", "species", "Mammalia", "42429"), v)
check("row: its classification", [v[f] for f in ("kingdom", "phylum", "class_", "order_", "family", "genus", "species")]
      == ["Animalia", "Chordata", "Mammalia", "Artiodactyla", "Antilocapridae", "Antilocapra", "Antilocapra americana"])
check("row: when", (v["eventDate"], v["eventTime"], v["year"], v["month"], v["day"], v["createdAt"])
      == ("2024-05-17", "2024-05-17T08:30:00-07:00", 2024, 5, 17, "2024-05-18T10:00:00-07:00"))
check("row: how well the place is known, and that it is obscured",
      (v["coordUncert_m"], v["statedAccuracy_m"], v["obscured"], v["geoprivacy"], v["taxonGeoprivacy"])
      == (28583, 8, "Yes", "obscured", "obscured"))
check("row: grade and identifications", (v["qualityGrade"], v["captive"], v["idCount"], v["idAgree"], v["idDisagree"])
      == ("needs_id", "No", 3, 2, 0))
check("row: annotations by name; one voted down is left out, a repeat is given once, two values are both given, "
      "an attribute the tool does not carry is ignored",
      (v.get("lifeStage"), v.get("sex"), v.get("evidence"), v.get("aliveOrDead")) == ("Adult", "Female", "Organism; Track", None),
      (v.get("lifeStage"), v.get("sex"), v.get("evidence")))
check("row: the observer, with accents kept", (v["recordedBy"], v["observerLogin"]) == ("Mar\u00eda L\u00f3pez", "maria"))
check("row: no license given is 'all rights reserved', for the observation and for its first photograph",
      (v["license"], v["photoLicense"]) == ("all rights reserved", "all rights reserved"))
check("row: photographs and sounds are counted, and the first photograph is given at a useful size with its credit",
      (v["photoCount"], v["soundCount"], v["photoURL"], v["photoCredit"])
      == (2, 1, "https://static.inaturalist.org/photos/1/medium.jpg", "(c) Maria Lopez, all rights reserved"))
check("row: the notes keep their lines and lose the space at the ends", v["description"] == "Two does\nand a fawn.", repr(v["description"]))
check("row: its page on iNaturalist, and where the taxon is native",
      (v["recordURL"], v["native"], v["introduced"], v["endemic"], v["threatened"])
      == ("https://www.inaturalist.org/observations/7001", "Yes", "No", "No", "No"))
plain = obs(7002, taxon=None, species_guess="some antelope", user={"login": "maria", "name": None}, lat=None,
            observed_on=None, time_observed_at=None, observed_on_details=None)
lon, lat, has, v = next(I.rows([plain], LINE, LABELS))
check("row, bare: no location is no coordinates", (lon, lat, has) == (None, None, False))
check("row, bare: with no identification the observer's own guess is the name, and nothing is said of where it is native",
      (v["scientificName"], v["taxonID"], v["native"], v["species"], v["recordedBy"], v["eventDate"], v["year"])
      == ("some antelope", None, None, None, "maria", None, None), v)
check("row: a location that is not two numbers is no coordinates",
      [next(I.rows([obs(1, location=x)], LINE, LABELS))[2] for x in ("35.1", "north,west", "", 35.1, "35.1,-111.2")]
      == [False, False, False, False, True])
check("row: every field of the output has a description, and field names are unique",
      len(set(I.FIELD_NAMES)) == len(I.FIELD_NAMES) == 53 and all(f[4] and f[1] for f in I.FIELDS)
      and all(f[2] in "TID" for f in I.FIELDS), len(I.FIELD_NAMES))

# =========================================================================================================
#  6. whole runs
# =========================================================================================================
SR84 = arcpy.SpatialReference(4326)
BOX = arcpy.Extent(-112.0, 35.0, -111.0, 36.0, spatial_reference=SR84)
N_RUN = [0]


def out_fc():
    N_RUN[0] += 1
    return os.path.join(gdb, "inat_%02d" % N_RUN[0])


def table(fc):
    return [dict(zip(["xy"] + I.FIELD_NAMES, r)) for r in arcpy.da.SearchCursor(fc, ["SHAPE@XY"] + I.FIELD_NAMES)]


def run(**kw):
    m = Msgs()
    o = kw.pop("out", None) or out_fc()
    with pointed(I, PAUSE=0.0):
        try:
            r = I.run(o, messages=m, **kw)
        except Exception as e:                                        # noqa: BLE001
            r = e
    return r, o, m


inat = fresh()
inat.observations = [
    rich,
    obs(7003, lat=35.5, lon=-111.5, tid=5001, license_code="cc0", description="x" * 1500),
    obs(7004, lat=35.6, lon=-111.4, tid=42428, quality_grade="casual", captive=True, license_code="cc-by-nc"),
    obs(7005, lat=None),                                              # the observer keeps the place private
    obs(7006, lat=0, lon=0),                                          # a place of 0, 0 is no place
]
r, o, m = run(taxa="Pronghorn (42429); 5001; 42428", extent=BOX, quality_grades=list(I.GRADES), exclude_captive=False,
              date_from="2020-01-01", date_to=datetime.date(2024, 12, 31))
check("run: it returns what matched and what was written", r == {"written": 3, "matched": 5}, r)
rows_ = table(o) if arcpy.Exists(o) else []
by_id = dict((x["inatID"], x) for x in rows_)
check("run: the points are written in WGS 84, with the engine's fields in order",
      arcpy.Describe(o).spatialReference.factoryCode == 4326 and arcpy.Describe(o).shapeType == "Point"
      and [f.name for f in arcpy.ListFields(o)][2:] == I.FIELD_NAMES, [f.name for f in arcpy.ListFields(o)][:6])
check("run: an observation with no place, and one at 0, 0, are left out and counted",
      sorted(by_id) == ["7001", "7003", "7004"] and m.has("Left out 2 without coordinates"), (sorted(by_id), m.lines))
x = by_id.get("7001", {})
check("run: a row holds its point and its values",
      x.get("xy") and abs(x["xy"][0] - -111.654321) < 1e-9 and abs(x["xy"][1] - 35.123456) < 1e-9
      and (x["scientificName"], x["genus"], x["lifeStage"], x["evidence"], x["obscured"], x["license"], x["year"],
           x["coordUncert_m"], x["recordedBy"])
      == ("Antilocapra americana", "Antilocapra", "Adult", "Organism; Track", "Yes", "all rights reserved", 2024, 28583.0,
          "Mar\u00eda L\u00f3pez"), x)
check("run: a subspecies has its species, a genus has none",
      (by_id["7003"]["species"], by_id["7003"]["taxonRank"], by_id["7004"]["species"], by_id["7004"]["genus"])
      == ("Odocoileus hemionus", "subspecies", None, "Antilocapra"))
check("run: text too long for its field is shortened, and that is said",
      len(by_id["7003"]["description"]) == 1000 and m.has("1 long text values were shortened to fit their fields."), m.lines)
cnt = web.asked("/v1/observations")
check("run: the count is asked with the choices and for no observations",
      len(cnt) == 1 and cnt[0].one("per_page") == "0" and cnt[0].one("taxon_id") == "42429,5001,42428"
      and (cnt[0].one("swlng"), cnt[0].one("swlat"), cnt[0].one("nelng"), cnt[0].one("nelat")) == ("-112", "35", "-111", "36")
      and (cnt[0].one("d1"), cnt[0].one("d2")) == ("2020-01-01", "2024-12-31") and cnt[0].one("quality_grade") is None
      and cnt[0].one("captive") is None, cnt and cnt[0].query)
check("run: the messages give the count, the grades and the licenses in words",
      m.has("iNaturalist has 5 observations that match.") and m.has("They are of 3 taxa. Quality grades: ")
      and m.has("CC0 (no restriction): 1") and m.has("all rights reserved (no license given: ask the observer before "
                                                    "reusing the record): 1"), m.lines)
check("run: warnings for points not of research grade and for obscured locations",
      m.warned("2 of the points are not research grade") and m.warned("1 of the points have an OBSCURED location"), m.warnings)
today = datetime.date.today().strftime("%Y-%m-%d")
cite = ("iNaturalist community. Observations of Antilocapra americana; Odocoileus hemionus eremicus; Antilocapra from a "
        "rectangle observed between 2020-01-01 and 2024-12-31. Exported from https://www.inaturalist.org on %s." % today)
check("run: the citation iNaturalist asks for is given", m.has("iNaturalist asks that records exported from it be cited "
                                                            "in this form: " + cite), [s for s in m.lines if "cited" in s])
md = arcpy.metadata.Metadata(o)
check("metadata: the credit is that citation; the summary says what was asked and what came",
      md.credits == cite and "fetched directly from iNaturalist.org. Choices: species or groups: Antilocapra americana"
      in (md.summary or "") and "5 observations matched; 3 points written, 1 of them with an obscured location"
      in (md.summary or ""), (md.credits, md.summary))
check("metadata: the description explains obscured places and links to each species asked for",
      "random point within about 0.2 degrees" in (md.description or "") and "https://www.inaturalist.org/taxa/42429"
      in (md.description or "") and "How to cite them." in (md.description or ""), (md.description or "")[:200])
check("metadata: the licenses are in the use limitations",
      "CC0 (no restriction): 1" in (md.accessConstraints or "") and "https://www.inaturalist.org/terms"
      in (md.accessConstraints or ""), md.accessConstraints)
check("run: nothing is left in the temporary folder", glob.glob(os.path.join(tempfile.gettempdir(), "inaturalist_*.jsonl.gz")) == [])
check("run: every request found an answer prepared, and none went to another computer",
      web.unanswered == [] and guard.attempts == [], (web.unanswered, guard.attempts))

# a study area of polygons: asked for by its rectangle, cut to the polygon
UTM = arcpy.SpatialReference(26912)
tri = arcpy.management.CreateFeatureclass(gdb, "study_area", "POLYGON", spatial_reference=UTM)[0]
with arcpy.da.InsertCursor(tri, ["SHAPE@"]) as c:                     # a triangle: its rectangle has room outside it
    c.insertRow((arcpy.Polygon(arcpy.Array([arcpy.Point(430000, 3900000), arcpy.Point(430000, 3950000),
                                            arcpy.Point(480000, 3900000)]), UTM),))
tri_wgs = [r[0].projectAs(SR84) for r in arcpy.da.SearchCursor(tri, ["SHAPE@"])][0]
e_ = tri_wgs.extent
inside_pt = arcpy.PointGeometry(arcpy.Point(440000, 3910000), UTM).projectAs(SR84).firstPoint
outside_pt = arcpy.PointGeometry(arcpy.Point(475000, 3945000), UTM).projectAs(SR84).firstPoint       # in the rectangle only
inat = fresh()
inat.observations = [obs(8001, lat=inside_pt.Y, lon=inside_pt.X), obs(8002, lat=outside_pt.Y, lon=outside_pt.X)]
r, o, m = run(study_area=tri)
cnt = web.asked("/v1/observations")
# (to within 0.00003 degree, about 3 m: the engine moves the polygon from NAD 83 to WGS 84 with the datum shift
# of about a meter, which the plain projection used here for comparison leaves out)
check("polygons: the service is asked for the rectangle around them, in longitude and latitude",
      len(cnt) == 1 and abs(float(cnt[0].one("swlng")) - e_.XMin) < 3e-5 and abs(float(cnt[0].one("swlat")) - e_.YMin) < 3e-5
      and abs(float(cnt[0].one("nelng")) - e_.XMax) < 3e-5 and abs(float(cnt[0].one("nelat")) - e_.YMax) < 3e-5,
      (cnt and cnt[0].query, (e_.XMin, e_.YMin, e_.XMax, e_.YMax)))
check("polygons: the observation outside the polygon is left out, and the run says why and how many",
      r == {"written": 1, "matched": 2} and [x["inatID"] for x in table(o)] == ["8001"]
      and m.has("iNaturalist searches by rectangle, not by polygon.") and m.has("Left out 1 outside the study area's polygons.")
      and m.has("iNaturalist has 2 observations that match in the rectangle around the study area."), (r, m.lines))
inat = fresh()
inat.observations = [obs(8001, lat=inside_pt.Y, lon=inside_pt.X)]
r, o, m = run(extent=arcpy.Extent(430000, 3900000, 480000, 3950000, spatial_reference=UTM))
check("a rectangle in a projection: the run says that it leans, and asks for the rectangle of longitude and latitude "
      "around it", isinstance(r, dict) and m.has("The rectangle is not square to the lines of longitude and latitude"), m.lines[:2])
inat = fresh()
inat.observations = [obs(8001)]
r, o, m = run(place="Arizona", observer=" maria ", project="arizona-mammals", months=[5, 6], photos_only=True,
              max_uncertainty_m=250, exclude_obscured=True, establishment=I.ESTABLISHMENT_NATIVE, open_license_only=True)
c0 = web.asked("/v1/observations")[0]
check("place, observer, project and the other choices all reach the service",
      (c0.one("place_id"), c0.one("user_id"), c0.one("project_id"), c0.one("month"), c0.one("photos"), c0.one("acc_below"),
       c0.one("geoprivacy"), c0.one("native"), c0.one("quality_grade"))
      == ("40", "maria", "arizona-mammals", "5,6", "true", "250", "open", "true", "research")
      and m.has("Place: Arizona, US (iNaturalist place id 40)."), c0.query)

# nothing found; a great many; too many
inat = fresh()
r, o, m = run(taxa="42429")
check("nothing found: an empty feature class, a warning, and no request for observations",
      r == {"written": 0, "matched": 0} and arcpy.Exists(o) and int(arcpy.management.GetCount(o)[0]) == 0
      and m.has("iNaturalist has no observations that match.") and m.warned("No observations matched")
      and web.asked("/v2/observations") == [], (r, m.lines, m.warnings))
inat = fresh()
inat.observations = [obs(8001)]
inat.says_total = 150000
r, o, m = run(taxa="42429")
check("a large request (150,000): warned beforehand, with the time it will take (about 28 minutes)",
      m.warned("That is a large request: 150,000 observations") and m.warned("will take about 28 minutes"), m.warnings[:1])
inat = fresh()
inat.says_total = 2000001
r, o, m = run(taxa="42429")
check("more than 2,000,000: refused, with the reasons and what to do, and nothing is fetched",
      isinstance(r, I.INatError) and "this tool does not fetch more than 2,000,000 in one run. Nothing was fetched." in str(r)
      and web.asked("/v2/observations") == [] and not arcpy.Exists(o), repr(r)[:200])
inat = fresh()
inat.observations = [obs(8000 + i) for i in range(6000)]
r, o, m = run(taxa="42429")
check("6,000 observations: the run says how long it will take, makes 31 requests and writes them all",
      r == {"written": 6000, "matched": 6000} and m.has("At 200 observations a request this will take about 66 seconds.")
      and len(web.asked("/v2/observations")) == 31, (r, len(web.asked("/v2/observations"))))

# trouble during a run
inat = fresh()
web.forget()
web.answer("/v1/controlled_terms", status=500, json={"error": "boom"})
inat = FakeINat(web)
inat.observations = [rich]
r, o, m = run(taxa="42429", quality_grades=[I.GRADE_NEEDS_ID])
check("the list of annotation values cannot be read: the points are written, the annotation fields are empty, "
      "and the run says so", isinstance(r, dict) and r["written"] == 1 and table(o)[0]["lifeStage"] is None
      and m.warned("iNaturalist's list of annotation values could not be read"), (r, m.warnings))
inat = fresh()
web.forget()
web.answer("/v2/observations", status=400, json={"errors": [{"message": "Unknown field"}]})
inat = FakeINat(web)
inat.observations = [obs(1)]
before = out_fc()
arcpy.management.CreateFeatureclass(gdb, os.path.basename(before), "POINT", spatial_reference=SR84)
r, o, m = run(taxa="42429", out=before)
check("the service refuses the request for observations: the error carries its reason, and nothing is left in the "
      "temporary folder", isinstance(r, I.INatError) and "iNaturalist returned an error (400). Unknown field" in str(r)
      and glob.glob(os.path.join(tempfile.gettempdir(), "inaturalist_*.jsonl.gz")) == [], repr(r))
check("the service refuses the request for observations: a feature class already there is left as it was",
      arcpy.Exists(before) and "inatID" not in [f.name for f in arcpy.ListFields(before)])
inat = fresh()
web.forget()
web.answer("/v1/observations", status=422, json={"error": "Unprocessable"})
inat = FakeINat(web)
r, o, m = run(observer="nobody_by_this_name")
check("an observer iNaturalist does not know: said plainly",
      isinstance(r, I.INatError) and 'iNaturalist does not know the observer "nobody_by_this_name". Check the spelling.' in str(r),
      repr(r))

# cancelling while the points are written
inat = fresh()
recs = list(I.rows([obs(9000 + i) for i in range(1200)], LINE, LABELS))
half = out_fc()
e = error_of(lambda: I.write_points(half, iter(recs), cancelled=lambda: True))
left = int(arcpy.management.GetCount(half)[0]) if arcpy.Exists(half) else None
check("cancelled while writing: the writing stops", isinstance(e, I.Cancelled), repr(e))
check("cancelled while writing: no half-written feature class is left under the output's name",
      left is None, "a feature class with %s of 1,200 points is left, looking like a finished result" % left)

# what is refused before anything is asked
inat = fresh()
for name, kw, words in (
        ("nothing chosen at all", {}, "Choose at least one species or group, a study area, an iNaturalist place"),
        ("a study area and a rectangle together", dict(study_area=tri, extent=BOX), "Give a study area layer or a rectangle, not both."),
        ("a grade that is not one of the three", dict(taxa="42429", quality_grades=["Excellent"]), "The quality grades are: Research grade; Needs ID; Casual."),
        ("dates the wrong way round", dict(taxa="42429", date_from="2024-06-01", date_to="2024-05-01"), "The first date is later than the last date."),
        ("an accuracy of zero", dict(taxa="42429", max_uncertainty_m=0), "The accuracy must be a number of meters greater than zero"),
        ("an unknown choice of native or introduced", dict(taxa="42429", establishment="Feral"), "Native or introduced must be one of: Any; Native; Introduced.")):
    r, o, m = run(**kw)
    check("refused: %s" % name, isinstance(r, I.INatError) and words in str(r) and not arcpy.Exists(o), repr(r))
check("refused: none of those asked the service for observations", web.asked("/v2/observations") == [])
r, o, m = run(taxa="42429", out=os.path.join(work, "points.shp"))
check("refused: an output outside a geodatabase, with an example of the right form",
      isinstance(r, I.INatError) and "must be a feature class inside a file geodatabase" in str(r)
      and "iNaturalist_observations" in str(r) and "GBIF" not in str(r), repr(r))
r, o, m = run(taxa="42429", out=os.path.join(gdb, "2024 points"))
check("refused: an output name a geodatabase cannot hold", isinstance(r, I.INatError) and "must start with a letter" in str(r), repr(r))

check("at the end: every request found an answer prepared", web.unanswered == [], web.unanswered)
check("at the end: nothing tried to reach another computer", guard.attempts == [], guard.attempts)
point_wait.__exit__(None, None, None)
point_engine.__exit__(None, None, None)
check("at the end: the engine's own addresses are put back", I.API == "https://api.inaturalist.org/v2/" and I.PAUSE == 1.0)
web.close()
guard.stop()

print("\n%d passed, %d failed%s" % (passed, failed, " (and %d known faults not yet fixed)" % known if known else ""))
arcpy.management.ClearWorkspaceCache()
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)
