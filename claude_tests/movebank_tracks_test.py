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

"""Movebank Animal Tracks (movebank_tracks.py and movebank_account.py): a
dataset published in the Movebank Data Repository, files already on disk,
and a study read with a Movebank account. Nothing here touches Movebank, any
account or the internet. A stand-in service on this computer answers in
Movebank's two forms with made-up animals; a guard stops any attempt to
reach another computer; the stored sign-in is replaced by a made-up one
("maria"), so Windows Credential Manager is never read. The fixes are laid
out so that every count, time and length is known beforehand.

In particular: a study's LICENSE TERMS are never accepted unless the caller
says the user has read and accepted them. The stand-in records every
request, and the suite checks that no request ever carries an acceptance
that was not asked for.

Run with Pro's Python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" movebank_tracks_test.py
"""
import base64
import datetime
import hashlib
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "WildlifeTools", "Toolboxes"))
sys.path.insert(0, HERE)
import arcpy  # noqa: E402
import gbif_occurrences as G  # noqa: E402
import movebank_account as A  # noqa: E402
import movebank_tracks as M  # noqa: E402
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


def error_of(fn):
    try:
        fn()
        return None
    except BaseException as e:                                        # noqa: BLE001
        return e


def refused(name, fn, has):
    e = error_of(fn)
    check(name, isinstance(e, M.MovebankError) and has in str(e), "%s: %s" % (type(e).__name__, e))


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

    def everything(self):
        return "\n".join(self.lines + self.warnings)


class Clock:
    def __init__(self):
        self.now, self.slept = 9000.0, []

    def time(self):
        self.now += 0.001
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(float(seconds), 3))
        self.now += float(seconds)


USER, PASSWORD = "maria", "not-a-real-password-9Z"
BASIC = "Basic " + base64.b64encode(("%s:%s" % (USER, PASSWORD)).encode()).decode()
UUID = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
DOI = "10.5441/001/1.abc123"
HANDLE = "10255/move.1234"
CITE = "Lopez M. 2024. Data from: Pronghorn movements near Flagstaff. Movebank Data Repository. https://doi.org/" + DOI

# ---------------------------------------------------------------------------------------------------------
#  the made-up animals
# ---------------------------------------------------------------------------------------------------------
HEAD = ["event-id", "visible", "timestamp", "location-long", "location-lat", "eobs:battery-voltage", "gps:hdop", "comments",
        "empty-col", "argos:long-number", "sensor-type", "individual-taxon-canonical-name", "tag-local-identifier",
        "individual-local-identifier", "study-name"]


def fix(n, animal, tag, when, lon, lat, visible="true", volt="3700", hdop="1.5", note="", sensor="gps", long_no="12345678901234567890"):
    return [str(n), visible, when, str(lon), str(lat), volt, hdop, note, "", long_no, sensor, "Antilocapra americana", tag,
            animal, "Pronghorn movements near Flagstaff"]


# A1 walks north along the meridian of 111 W, from 35.00 N to 35.10 N, in half a day. The rows are out of order.
# One of its fixes was flagged as an outlier by the data's owner; one repeats the place of the fix before it.
FIXES = [
    fix(4, "A1", "T1", "2024-05-01 12:00:00.000", -111.0, 35.10, volt="3650", note="at the fence"),
    fix(1, "A1", "T1", "2024-05-01 00:00:00.000", -111.0, 35.00),
    fix(2, "A1", "T1", "2024-05-01 06:00:00.500", -111.0, 35.05),
    fix(3, "A1", "T1", "2024-05-01 09:00:00.000", -111.0, 35.05),
    fix(5, "A1", "T1", "2024-05-01 03:00:00.000", -111.0, 36.50, visible="false"),
    # B2 crosses the 180th meridian eastward, and carries two tags in turn
    fix(6, "B2", "T2", "2024-05-02 00:00:00.000", 179.5, 10.0),
    fix(7, "B2", "T3", "2024-05-02 01:00:00.000", -179.5, 10.0),
    fix(8, "B2", "T3", "2024-05-02 02:00:00.000", -179.0, 10.0),
    fix(9, "B2", "T3", "2024-05-02 01:30:00.000", "", "", sensor="acceleration"),          # a measurement with no place
    # C3 has one fix only
    fix(10, "C3", "T4", "2024-05-03 00:00:00.000", -111.5, 35.5),
    # three rows that cannot be read: a latitude of 95, a latitude that is not a number, a time that is missing
    fix(11, "A1", "T1", "2024-05-01 13:00:00.000", -111.0, 95.0),
    fix(12, "A1", "T1", "2024-05-01 14:00:00.000", -111.0, "north"),
    fix(13, "A1", "T1", "", -111.0, 35.2),
]


def csv_text(head, rows):
    import csv
    import io
    b = io.StringIO()
    w = csv.writer(b, lineterminator="\n")
    w.writerow(head)
    w.writerows(rows)
    return b.getvalue()


DATA = csv_text(HEAD, FIXES).encode("utf-8")
REFERENCE = csv_text(["tag-id", "animal-id", "animal-taxon", "animal-sex", "animal-life-stage"],
                     [["T1", "A1", "Antilocapra americana", "f", "adult"], ["T2", "B2", "Antilocapra americana", "m", ""],
                      ["T3", "B2", "Antilocapra americana", "", "yearling"]]).encode("utf-8")
README = ("Movebank Data Repository\n\nPronghorn movements near Flagstaff\n\nData package citation:\n" + CITE + "\n\n"
          "-----------\nTerms of Use\nThis data file is licensed by the Creative Commons Zero (CC0 1.0) license.\n"
          "Please cite the data package.\n-----------\nData Attributes\nevent-id: An identifier.\n").encode("utf-8")
MERIDIAN_KM_0_1_DEGREE = 11.094          # a tenth of a degree of latitude at 35 N (110.94 km to the degree)
TERMS_HTML = (b"<html><body><p>License Terms for the study</p><p>You may use these data for research only &amp; must contact "
              b"the owner before publishing.</p></body></html>")


class FakeMovebank:
    """Both of Movebank's services: the Data Repository (no account) and direct-read (an account)."""

    def __init__(self, web):
        self.web = web
        api = web.url + "server/api/"
        self.item = {
            "uuid": UUID,
            "metadata": {"dc.title": [{"value": "Data from: Pronghorn movements near Flagstaff"}],
                         "dc.identifier.doi": [{"value": "doi:" + DOI}],
                         "dc.identifier.uri": [{"value": "https://datarepository.movebank.org/handle/" + HANDLE}],
                         "dc.rights": [{"value": "CC0 1.0"}],
                         "dc.rights.uri": [{"value": "http://creativecommons.org/publicdomain/zero/1.0/"}],
                         "mdr.citation.CSE": [{"value": CITE.replace("Movebank Data Repository", "<i>Movebank Data Repository</i>")}],
                         "dwc.ScientificName": [{"value": "Antilocapra americana"}], "sensor.name": [{"value": "GPS"}],
                         "dc.contributor.author": [{"value": "Lopez, M"}], "dc.date.issued": [{"value": "2024-03-01"}],
                         "mdr.animal.count": [{"value": "3"}], "mdr.location.count": [{"value": "12"}],
                         "mdr.study.id": [{"value": "433126"}], "dc.relation.isreferencedby": [{"value": "doi:10.1111/ecog.01234"}]},
            "_links": {"bundles": {"href": api + "core/items/%s/bundles" % UUID}}}
        self.files = [("Pronghorn movements near Flagstaff.csv", DATA), ("Pronghorn movements near Flagstaff-reference-data.csv", REFERENCE),
                      ("README.txt", README), ("map.pdf", b"%PDF not really")]
        self.found = [self.item]
        web.answer("/server/api/discover/search/objects", handler=self.search)
        web.answer("/server/api/core/items/%s/bundles" % UUID, json={"_embedded": {"bundles": [
            {"name": "LICENSE", "_links": {"bitstreams": {"href": api + "core/bundles/lic/bitstreams"}}},
            {"name": "ORIGINAL", "_links": {"bitstreams": {"href": api + "core/bundles/orig/bitstreams"}}}]}})
        web.answer("/server/api/core/bundles/orig/bitstreams", handler=self.bitstreams)
        web.answer("/server/api/core/items/", handler=lambda q: self.item if q.path.endswith(UUID) else (404, {}, ""))
        web.answer("/content/", handler=self.content)
        # ---- direct-read
        self.terms_accepted = True
        self.access = "true"
        self.study = {"id": "433126", "name": "Pronghorn movements near Flagstaff", "license_type": "CUSTOM",
                      "license_terms": "Research use only.", "i_have_download_access": "true", "i_can_see_data": "true",
                      "number_of_deployed_locations": "12", "number_of_individuals": "3",
                      "timestamp_first_deployed_location": "2024-05-01 00:00:00.000",
                      "timestamp_last_deployed_location": "2024-05-03 00:00:00.000",
                      "principal_investigator_name": "Maria Lopez", "contact_person_name": "Maria Lopez",
                      "citation": "Lopez M (2024) Pronghorn movements. &lt;i&gt;Movebank&lt;/i&gt; study 433126.",
                      "acknowledgements": "", "grants_used": "", "taxon_ids": "Antilocapra americana", "sensor_type_ids": "GPS",
                      "study_objective": "To see where they go."}
        web.answer("/movebank/service/direct-read", handler=self.direct)

    def search(self, q):
        return {"_embedded": {"searchResult": {"_embedded": {"objects": [{"_embedded": {"indexableObject": i}} for i in self.found]},
                                               "page": {"totalElements": len(self.found)}}}}

    def bitstreams(self, q):
        page = int(q.one("page") or 0)                # two to a page, to see that every page is read
        some = self.files[page * 2:page * 2 + 2]
        out = {"_embedded": {"bitstreams": [{"name": n, "sizeBytes": len(b), "_links": {"content": {
            "href": self.web.url + "content/%d" % self.files.index((n, b))}}} for n, b in some]}, "_links": {}}
        if page * 2 + 2 < len(self.files):
            out["_links"]["next"] = {"href": self.web.url + "server/api/core/bundles/orig/bitstreams?size=100&page=%d" % (page + 1)}
        return out

    def content(self, q):
        return 200, {"Content-Type": "application/octet-stream"}, self.files[int(q.path.rsplit("/", 1)[1])][1]

    def direct(self, q):
        if q.headers.get("authorization") != BASIC:
            return 401, {}, ""
        kind = q.one("entity_type")
        if kind == "study":
            rec = dict(self.study, i_have_download_access=self.access)
            return 200, {"Content-Type": "text/csv"}, csv_text(list(rec), [list(rec.values())]) if q.one("study_id") == "433126" \
                else "id,name\n"
        if kind == "tag_type":
            return 200, {}, "id,external_id,name\n653,gps,GPS\n2365683,acceleration,Acceleration\n"
        if kind == "individual":
            return 200, {}, ("id,local_identifier,taxon_canonical_name,sex,nick_name,ring_id,comments\n"
                             "1,A1,Antilocapra americana,f,Alba,,\n2,B2,Antilocapra americana,m,,,\n3,C3,Antilocapra americana,,,,\n")
        if kind == "deployment":
            return 200, {}, "id,animal_life_stage\nd1,adult\nd2,yearling\n"
        if kind == "event":
            if q.one("license-md5"):
                if q.one("license-md5") == hashlib.md5(TERMS_HTML).hexdigest():
                    self.terms_accepted = True
            if not self.terms_accepted:
                return 200, {"accept-license": "true", "Content-Type": "text/html"}, TERMS_HTML
            if q.one("attributes") == "timestamp":
                return 200, {}, "timestamp\n"
            head = ["event_id", "visible", "timestamp", "location_long", "location_lat", "sensor_type_id",
                    "individual_taxon_canonical_name", "tag_local_identifier", "individual_local_identifier", "deployment_id",
                    "eobs_battery_voltage"]
            rows = [[r[0], r[1], r[2], r[3], r[4], "2365683" if r[10] == "acceleration" else "653", r[11], r[12], r[13],
                     {"A1": "d1", "B2": "d2", "C3": "d3"}[r[13]], r[5]] for r in FIXES]
            return 200, {"Content-Type": "text/csv"}, csv_text(head, rows)
        return 400, {}, "unknown entity"


work = tempfile.mkdtemp(prefix="movebank_test_")
arcpy.env.overwriteOutput = True
gdb = arcpy.management.CreateFileGDB(work, "t.gdb")[0]
web = StandIn()
clock = Clock()
account = [(USER, PASSWORD)]
env_before = dict((k, os.environ.pop(k, None)) for k in ("MOVEBANK_USER", "MOVEBANK_PASSWORD"))


def fresh():
    web.forget()
    del clock.slept[:]
    account[0] = (USER, PASSWORD)
    return FakeMovebank(web)


points = [pointed(M, REPO=web.url + "server/api/", time=clock),
          pointed(A, SERVICE=web.url + "movebank/service/direct-read?"),
          pointed(G, _read_windows_credential=lambda target: account[0] if target == A.CREDENTIAL_TARGET else None)]
for p in points:
    p.__enter__()

# =========================================================================================================
#  1. small pieces
# =========================================================================================================
check("a column of Movebank's file as a field name",
      [M.field_name(c) for c in ("eobs:battery-voltage", "location-long", "3d-fix", "  gps:hdop ", "a" * 80)]
      == ["eobs_battery_voltage", "location_long", "f_3d_fix", "gps_hdop", "a" * 64])
check("a time as Movebank writes it, with its milliseconds; anything else is no time",
      (M.parse_time("2017-11-14 09:03:00.000"), M.parse_time("2017-11-14 09:03:00.250"), M.parse_time("2017-11-14 09:03:00"),
       M.parse_time("2017-11-14"), M.parse_time("2017-13-14 09:03:00.000"), M.parse_time(""), M.parse_time(None))
      == (datetime.datetime(2017, 11, 14, 9, 3), datetime.datetime(2017, 11, 14, 9, 3, 0, 250000),
          datetime.datetime(2017, 11, 14, 9, 3), None, None, None, None))
parts = M._split_dateline([(179.5, 10.0), (-179.5, 10.0), (-179.0, 10.0)])
check("a track that crosses the 180th meridian is cut there, at the latitude where it crosses",
      parts == [[(179.5, 10.0), (180.0, 10.0)], [(-180.0, 10.0), (-179.5, 10.0), (-179.0, 10.0)]], parts)
parts = M._split_dateline([(-179.0, 0.0), (179.0, 2.0)])
check("and the other way round, with the crossing halfway between the two fixes",
      parts == [[(-179.0, 0.0), (-180.0, 1.0)], [(180.0, 1.0), (179.0, 2.0)]], parts)
check("a track that does not cross it is one part", M._split_dateline([(10.0, 0.0), (20.0, 1.0)]) == [[(10.0, 0.0), (20.0, 1.0)]])
check("a study named in the ways people write it",
      [A.parse_study(s) for s in ("study 433126", "Study: 433126", "movebank study id=433126", "433126", "1234",
                                  "https://www.movebank.org/cms/webapp?gwt_fragment=page=studies,path=study433126", DOI, "")]
      == ["433126", "433126", "433126", "433126", None, "433126", None, None])
check("Movebank's answer in HTML as plain text, with what was escaped twice read properly",
      A.plain(TERMS_HTML) == "License Terms for the study\nYou may use these data for research only & must contact the owner "
                             "before publishing."
      and A.plain("Lopez M (2024) Tracks. &lt;i&gt;Movebank&lt;/i&gt; study.") == "Lopez M (2024) Tracks. Movebank study.", A.plain(TERMS_HTML))
check("a last date covers its whole day; a time of day is taken as given",
      A.end_of_day(datetime.date(2024, 5, 1)) == datetime.datetime(2024, 5, 1, 23, 59, 59, 999999)
      and A.end_of_day(datetime.datetime(2024, 5, 1)) == datetime.datetime(2024, 5, 1, 23, 59, 59, 999999)
      and A.end_of_day(datetime.datetime(2024, 5, 1, 6, 30)) == datetime.datetime(2024, 5, 1, 6, 30)
      and A._stamp(datetime.date(2024, 5, 1)) == "20240501000000000")
check("licenses in words", (A.license_name({"license_type": "CC_0"}), A.license_name({"license_type": "CUSTOM"}),
                           A.license_name({}), A.license_name({"license_type": "NEW"}))
      == ("CC0 1.0 Universal", "the owner's own terms", "not stated", "NEW"))
check("the output must be a feature class directly in a file geodatabase",
      M.check_output(os.path.join(gdb, "tracks")) is None and "Movebank's field names are too long for a shapefile" in M.check_output(r"C:\x\t.shp")
      and "must start with a letter" in M.check_output(os.path.join(gdb, "1 track")))
check("the terms-of-use section of a README is found, and stops at the line of dashes",
      M.readme_terms(README.decode()) == "This data file is licensed by the Creative Commons Zero (CC0 1.0) license.\nPlease cite the data package."
      and M.readme_terms("nothing of the kind") is None and M.readme_terms(None) is None, M.readme_terms(README.decode()))
check("the repository's README says which dataset the files are",
      M.describe_readme(README.decode()) == {"kind": "published", "citation": CITE, "doi": DOI, "license": ""}
      and M.describe_readme("just some notes") is None and M.describe_readme(None) is None, M.describe_readme(README.decode()))
check("plain text as paragraphs of metadata, with its own '<' kept as text",
      M.text_as_html("One\nline two\n\nTwo <b>") == "<p>One<br/>line two</p><p>Two &lt;b&gt;</p>", M.text_as_html("One\nline two\n\nTwo <b>"))

# files
disk = os.path.join(work, "on_disk")
os.makedirs(disk)
data_path = os.path.join(disk, "Pronghorn movements near Flagstaff.csv")
ref_path = os.path.join(disk, "Pronghorn movements near Flagstaff-reference-data.csv")
open(data_path, "wb").write(b"\xef\xbb\xbf" + DATA)                   # with the mark some programs put at the start
open(ref_path, "wb").write(REFERENCE)
open(os.path.join(disk, "notes.csv"), "w").write("a,b\n1,2\n")
check("which files are Movebank data files: the data, not the reference data, not another table",
      (M.is_data_file(data_path), M.is_data_file(ref_path), M.is_data_file(os.path.join(disk, "notes.csv")),
       M.is_data_file(os.path.join(disk, "none.csv"))) == (True, False, False, False))
check("the reference data: each animal's sex and life stage, the first one given",
      M.read_reference(ref_path) == {"A1": {"sex": "f", "life_stage": "adult"}, "B2": {"sex": "m", "life_stage": "yearling"}}
      and M.read_reference(os.path.join(disk, "none.csv")) == {}, M.read_reference(ref_path))
sc = M.scan_files([data_path], lambda p, t: None, lambda: False, 0, 1)
check("first reading of the data: 13 rows, 12 with a place, 1 flagged, three animals, first and last time",
      (sc.rows, sc.located, sc.flagged, sc.animals, sc.first, sc.last)
      == (13, 12, 1, {"A1": 8, "B2": 3, "C3": 1}, "2024-05-01 00:00:00.000", "2024-05-03 00:00:00.000"),
      (sc.rows, sc.located, sc.flagged, sc.animals, sc.first, sc.last))
extras = dict((c, (nm, ft, ln)) for c, nm, ft, ln in M.extra_fields(sc))
check("the other columns: whole numbers, decimals and text each get a field of their kind; an empty column gets none",
      extras.get("eobs:battery-voltage") == ("eobs_battery_voltage", "LONG", None) and extras.get("gps:hdop") == ("gps_hdop", "DOUBLE", None)
      and extras.get("comments") == ("comments", "TEXT", 12) and "empty-col" not in extras, extras)
check("the other columns: a whole number of 20 digits is not put in a field that cannot hold all its digits",
      extras.get("argos:long-number", ("", "", 0))[1] == "TEXT", "it becomes a %s field" % extras.get("argos:long-number", ("", "?"))[1])
bad = os.path.join(disk, "bad.csv")
open(bad, "w").write("timestamp,location-long,location-lat\n2024-01-01 00:00:00.000,1,2\n")
refused("a file without the animals' column is not a Movebank data file", lambda: M.scan_files([bad], lambda p, t: None, lambda: False, 0, 1),
        "bad.csv is not a Movebank data file: it has no individual-local-identifier column.")
os.remove(bad)

# =========================================================================================================
#  2. the Data Repository (no account)
# =========================================================================================================
mb = fresh()
found, total = M.search(text="pronghorn", taxon="Antilocapra", sensor="GPS", page=2, size=10)
q = web.asked("/server/api/discover/search/objects")[0]
check("search: only published data packages, with the words, the taxon, the sensor, the page and the size",
      (q.one("dsoType"), q.one("f.entityType"), q.one("query"), q.one("f.scientificName"), q.one("f.sensorName"), q.one("page"), q.one("size"))
      == ("ITEM", "Datapackage,equals", "pronghorn", "Antilocapra,contains", "GPS,equals", "2", "10")
      and q.headers.get("user-agent") == M.USER_AGENT and "authorization" not in q.headers, q.query)
d = found[0]
check("search: a data package as plain facts, the citation without its HTML",
      total == 1 and (d["uuid"], d["title"], d["doi"], d["doi_url"], d["license"], d["citation"], d["animals"], d["locations"], d["issued"])
      == (UUID, "Data from: Pronghorn movements near Flagstaff", DOI, "https://doi.org/" + DOI, "CC0 1.0", CITE, "3", "12", "2024-03-01"), d)
for form in (DOI, "doi:" + DOI, "https://doi.org/" + DOI, DOI + "/1", '"%s."' % DOI, UUID,
             "https://datarepository.movebank.org/handle/" + HANDLE):
    mb = fresh()
    check("a dataset named as %s is found" % form, M.find_package(form)["uuid"] == UUID)
mb = fresh()
mb.found = []
refused("a DOI the repository does not have", lambda: M.find_package("10.5441/001/1.zzz999"),
        "The Movebank Data Repository has no dataset with the DOI 10.5441/001/1.zzz999.")
refused("something that is no dataset at all", lambda: M.find_package("my tracks"), '"my tracks" is not a DOI of a dataset')
mb = fresh()
files = M.list_files(M.describe_item(mb.item))
check("the files of a dataset: every page of the list is read, and each file is known for what it is",
      [(f["name"], f["kind"], f["size"]) for f in files]
      == [("Pronghorn movements near Flagstaff.csv", "data", len(DATA)),
          ("Pronghorn movements near Flagstaff-reference-data.csv", "reference", len(REFERENCE)),
          ("README.txt", "readme", len(README)), ("map.pdf", "other", 15)], files)
dl = os.path.join(work, "dl")
os.makedirs(dl)
path, fetched = M.download_file(files[0], dl, lambda p, t: None, lambda: False, 0, 1)
check("a file is fetched whole, with no working file left", fetched and open(path, "rb").read() == DATA and os.listdir(dl) == [os.path.basename(path)])
n_before = len(web.asked("/content/"))
path, fetched = M.download_file(files[0], dl, lambda p, t: None, lambda: False, 0, 1)
check("a file already there at its full size is not fetched again", fetched is False and len(web.asked("/content/")) == n_before)
e = error_of(lambda: M.download_file(files[2], dl, lambda p, t: None, lambda: True, 0, 1))
check("cancelled while a file is fetched: no part of it is left", isinstance(e, M.Cancelled) and os.listdir(dl) == [os.path.basename(path)], os.listdir(dl))
for status, words in ((404, "The Movebank Data Repository has nothing at"), (403, "The file may be under embargo or restricted by its owner."),
                      (500, "The Movebank Data Repository returned an error (500).")):
    web.forget()
    web.answer("/content/", status=status, body="no")
    refused("a file the repository answers %d for: said in words" % status,
            lambda: M.download_file(dict(files[2], url=web.url + "content/2"), dl, lambda p, t: None, lambda: False, 0, 1), words)
web.forget()
web.fail("/content/", "cut", body=DATA)
e = error_of(lambda: M.download_file(dict(files[0], name="cut.csv", url=web.url + "content/0"), dl, lambda p, t: None, lambda: False, 0, 1))
got = os.path.getsize(os.path.join(dl, "cut.csv")) if os.path.exists(os.path.join(dl, "cut.csv")) else None
check("a file cut off on its way is not kept as if it were whole: an error that says so, after a second try",
      isinstance(e, M.MovebankError) and "cut.csv was cut off on its way from the Movebank Data Repository, twice (" in str(e)
      and got is None and len(web.asked("/content/")) == 2 and os.listdir(dl) == [os.path.basename(path)],
      (repr(e), "%s of %s bytes were saved under the file's own name" % (got, len(DATA)), os.listdir(dl)))
web.forget()
web.fail("/content/", "cut", body=DATA, times=1)
web.answer("/content/", body=DATA)
cut_path, fetched = M.download_file(dict(files[0], name="cut.csv", url=web.url + "content/0"), dl, lambda p, t: None, lambda: False, 0, 1)
check("a file cut off once and whole the second time: the whole file is kept",
      fetched and open(cut_path, "rb").read() == DATA and len(web.asked("/content/")) == 2, len(web.asked("/content/")))
os.remove(cut_path)
web.forget()
web.fail("/content/", "cut stream", body=DATA)
e = error_of(lambda: M.download_file(dict(files[0], name="cut.csv", url=web.url + "content/0"), dl, lambda p, t: None, lambda: False, 0, 1))
check("a file sent in pieces with no length promised, and cut off: refused in the same words, and nothing kept",
      isinstance(e, M.MovebankError) and "was cut off on its way" in str(e) and os.listdir(dl) == [os.path.basename(path)], (repr(e), os.listdir(dl)))

# ---- a whole run from a DOI
N_RUN = [0]


def outs():
    N_RUN[0] += 1
    return os.path.join(gdb, "fixes_%02d" % N_RUN[0]), os.path.join(gdb, "tracks_%02d" % N_RUN[0])


def run(dataset, **kw):
    m = Msgs()
    op, ol = outs()
    kw.setdefault("out_points", op)
    kw.setdefault("out_lines", ol)
    try:
        r = M.run(dataset, messages=m, **kw)
    except BaseException as e:                                        # noqa: BLE001
        r = e
    return r, kw["out_points"], kw["out_lines"], m


def table(fc, fields):
    return [dict(zip(fields, row)) for row in arcpy.da.SearchCursor(fc, fields)]


mb = fresh()
r, op, ol, m = run("https://doi.org/" + DOI)
folder = os.path.join(work, M.DOWNLOADS_FOLDER, os.path.basename(op))
check("published dataset: the run returns the outputs, the counts, the DOI, the citation and the folder of original files",
      isinstance(r, dict) and (r["points"], r["lines"], r["written"], r["line_count"], r["doi"], r["citation"], r["folder"])
      == (op, ol, 8, 2, DOI, CITE, folder), r)
check("published dataset: the data, the reference data and the README are kept; the file of another kind is not fetched",
      sorted(os.listdir(folder)) == sorted(["Pronghorn movements near Flagstaff.csv", "Pronghorn movements near Flagstaff-reference-data.csv",
                                            "README.txt"]) and open(os.path.join(folder, "README.txt"), "rb").read() == README, os.listdir(folder))
check("published dataset: the messages name it, its DOI, license and date, and what the file holds",
      m.has("Dataset: Data from: Pronghorn movements near Flagstaff") and m.has("DOI %s; license: CC0 1.0; published 2024-03-01." % DOI)
      and m.has("The data file holds 13 rows: 12 location fixes of 3 animals, from 2024-05-01 to 2024-05-03 (UTC); 1 rows are "
                "measurements without a location and are not loaded."), m.lines[:6])
check("published dataset: what was written and what was left out, and why",
      m.has("Wrote 8 points to " + op) and m.has("Wrote 2 track lines to " + ol) and m.has("1 animal with fewer than two different locations has no line.")
      and m.has("Left out: 1 flagged as outliers by the data owner; 3 whose coordinates or time could not be read."), m.lines)
check("published dataset: how to cite it, its license, and the publication it belongs to",
      m.has("Cite this dataset as: " + CITE) and m.has("License: CC0 1.0 (http://creativecommons.org/publicdomain/zero/1.0/).")
      and m.has("The data belong to the publication https://doi.org/10.1111/ecog.01234, which should be cited as well."), m.lines)
pf = ["SHAPE@XY", "event_id", "visible", "timestamp", "timestamp_text", "location_long", "location_lat", "sensor_type",
      "individual_local_identifier", "tag_local_identifier", "eobs_battery_voltage", "gps_hdop", "comments"]
pts = dict((x["event_id"], x) for x in table(op, pf))
check("points: the eight good fixes, each at its place (WGS 84)", sorted(pts, key=int) == ["1", "2", "3", "4", "6", "7", "8", "10"]
      and abs(pts["4"]["SHAPE@XY"][0] - -111.0) < 1e-8 and abs(pts["4"]["SHAPE@XY"][1] - 35.1) < 1e-8
      and arcpy.Describe(op).spatialReference.factoryCode == 4326, (sorted(pts, key=int), pts.get("4", {}).get("SHAPE@XY")))
x = pts.get("2", {})
# (a geodatabase's date field keeps whole seconds, so 06:00:00.500 is held there to the nearest second; the text
# field beside it is what keeps the file's own digits)
check("points: the time as a date field (to the second) AND as the file's own text, so the milliseconds are not lost",
      x.get("timestamp") is not None and abs((x["timestamp"] - datetime.datetime(2024, 5, 1, 6, 0, 0, 500000)).total_seconds()) <= 0.5
      and x.get("timestamp_text") == "2024-05-01 06:00:00.500", (x.get("timestamp"), x.get("timestamp_text")))
check("points: the other columns come through in fields of their kind",
      (pts["4"]["eobs_battery_voltage"], pts["4"]["gps_hdop"], pts["4"]["comments"], pts["1"]["comments"], pts["6"]["tag_local_identifier"])
      == (3650, 1.5, "at the fence", None, "T2") and "empty_col" not in [f.name for f in arcpy.ListFields(op)], pts["4"])
lf = ["SHAPE@", "individual_local_identifier", "tag_local_identifiers", "sensor_types", "fix_count", "first_fix", "last_fix",
      "duration_days", "length_km", "animal_sex", "animal_life_stage", "study_name"]
lines = dict((x["individual_local_identifier"], x) for x in table(ol, lf))
a1, b2 = lines.get("A1", {}), lines.get("B2", {})
check("lines: one for each animal with two places or more", sorted(lines) == ["A1", "B2"], sorted(lines))
check("lines: A1's fixes are joined in order of time, though the file had them out of order (south to north)",
      a1 and [round(p.Y, 2) for p in a1["SHAPE@"].getPart(0)] == [35.0, 35.05, 35.1] and a1["fix_count"] == 4,
      a1 and ([round(p.Y, 2) for p in a1["SHAPE@"].getPart(0)], a1["fix_count"]))
check("lines: A1's first and last fix, its half day, and its length over the ground (11.094 km, to 0.1 percent)",
      a1 and (a1["first_fix"], a1["last_fix"]) == (datetime.datetime(2024, 5, 1), datetime.datetime(2024, 5, 1, 12))
      and abs(a1["duration_days"] - 0.5) < 1e-9 and abs(a1["length_km"] - MERIDIAN_KM_0_1_DEGREE) < 0.011,
      a1 and (a1["first_fix"], a1["last_fix"], a1["duration_days"], a1["length_km"]))
check("lines: sex and life stage from the reference data; tags and sensors listed",
      a1 and (a1["animal_sex"], a1["animal_life_stage"], a1["tag_local_identifiers"], a1["sensor_types"]) == ("f", "adult", "T1", "gps")
      and (b2["animal_sex"], b2["animal_life_stage"], b2["tag_local_identifiers"]) == ("m", "yearling", "T2; T3"), (a1, b2))
check("lines: B2's line is in two parts, cut at the 180th meridian, and is 1.5 degrees of longitude long at 10 N (about 164 km)",
      b2 and b2["SHAPE@"].partCount == 2 and abs(b2["length_km"] - 1.5 * 111.32 * 0.98481) < 1.0, b2 and (b2["SHAPE@"].partCount, b2["length_km"]))
md = arcpy.metadata.Metadata(op)
check("metadata: the summary names the dataset; the credit is its citation; the README is in the description, its terms "
      "of use under Use Limitations", 'from the published dataset "Data from: Pronghorn movements near Flagstaff" (DOI %s; license CC0 1.0)' % DOI
      in (md.summary or "") and md.credits == CITE and "Data package citation:" in (md.description or "")
      and "Terms of use, from the dataset's README:" in (md.accessConstraints or "")
      and "Please cite the data package." in (md.accessConstraints or ""), (md.summary, md.credits))
check("published dataset: no sign-in is read or sent for a published dataset", all("authorization" not in a.headers for a in web.requests))
n_content = len(web.asked("/content/"))
r2, _op, _ol, m2 = run("doi:" + DOI, out_points=op, out_lines=ol, animals="A1; B2", include_flagged=True,
                       date_from=datetime.datetime(2024, 5, 1), date_to=datetime.datetime(2024, 5, 1))
check("a second run into the same outputs: the files are not fetched again, and the run says so",
      isinstance(r2, dict) and len(web.asked("/content/")) == n_content and m2.has("is already in that folder at its full size, so it was not fetched again."),
      (repr(r2)[:80], m2.lines[4:8]))
check("animals, dates and flagged fixes: only A1's fixes of the first of May (the whole day), the flagged one included and warned of",
      isinstance(r2, dict) and r2["written"] == 5 and r2["line_count"] == 1
      and m2.has("Left out: 1 of animals that were not asked for; 3 outside the dates asked for; 3 whose coordinates or time could not be read.")
      and m2.warned("1 fixes that the data owner flagged as outliers were included."), (repr(r2)[:120], m2.lines, m2.warnings))
r3, _a, _b, m3 = run(DOI, animals="A1; Z9")
check("an animal the dataset does not have: refused, with the animals it does have",
      isinstance(r3, M.MovebankError) and "The dataset has no animal named Z9. Its animals are: A1; B2; C3." in str(r3), repr(r3))
r4, op4, ol4, m4 = run(DOI, output=M.OUT_POINTS, out_lines=None)
check("points only: no lines are made", isinstance(r4, dict) and r4["lines"] is None and arcpy.Exists(op4) and r4["line_count"] == 0, repr(r4)[:120])

# ---- files already on disk
open(os.path.join(disk, "README.txt"), "wb").write(README)
mb = fresh()
r, op, ol, m = run(disk)
check("files on disk, a folder: loaded with nothing asked of Movebank, and the README says which dataset they are",
      isinstance(r, dict) and r["written"] == 8 and r["doi"] == DOI and r["citation"] == CITE and web.requests == []
      and m.has("Loading Movebank data already on disk: Pronghorn movements near Flagstaff.csv.")
      and m.has("The README beside it says what it is: a dataset published in the Movebank Data Repository (DOI %s)." % DOI)
      and m.has("The README gives this citation for the data: " + CITE), (repr(r)[:150], m.lines[:4]))
os.remove(os.path.join(disk, "README.txt"))
r, op, ol, m = run(' "%s" ' % data_path)
check("files on disk, one file and no README: loaded, with a warning that the output carries no DOI or citation",
      isinstance(r, dict) and r["written"] == 8 and r["doi"] == "" and m.warned("No README beside the files says which dataset they are")
      and arcpy.metadata.Metadata(op).credits == "Movebank (https://www.movebank.org).", (repr(r)[:150], m.warnings))
empty = os.path.join(work, "no_data")
os.makedirs(empty)
open(os.path.join(empty, "notes.csv"), "w").write("a,b\n1,2\n")
r, op, ol, m = run(empty)
check("a folder with no Movebank data file: refused", isinstance(r, M.MovebankError) and "holds no data file in Movebank's format" in str(r), repr(r))

# =========================================================================================================
#  3. a study, read with an account
# =========================================================================================================
mb = fresh()
account[0] = None
r, op, ol, m = run("study 433126")
check("study, no sign-in stored: refused with how to sign in, and Movebank is asked nothing",
      isinstance(r, M.MovebankError) and "needs a Movebank sign-in, and none is stored" in str(r) and web.requests == [], repr(r))
os.environ["MOVEBANK_USER"], os.environ["MOVEBANK_PASSWORD"] = "robot", "robot-pw"
check("no sign-in stored: the environment variables MOVEBANK_USER and MOVEBANK_PASSWORD are used instead", A.sign_in() == ("robot", "robot-pw"))
del os.environ["MOVEBANK_USER"], os.environ["MOVEBANK_PASSWORD"]

mb = fresh()
mb.terms_accepted = False
r, op, ol, m = run("study 433126")
check("study whose terms the account has not accepted: NOTHING is fetched, and the terms are shown to be read",
      isinstance(r, M.MovebankError) and "THE STUDY'S LICENSE TERMS HAVE NOT BEEN ACCEPTED, so nothing was fetched." in str(r)
      and "You may use these data for research only & must contact the owner before publishing." in str(r)
      and not arcpy.Exists(op) and not os.path.isdir(os.path.join(work, M.DOWNLOADS_FOLDER, os.path.basename(op))), repr(r)[:200])
check("study whose terms are not accepted: no request carried an acceptance, and none asked for the data",
      all(a.one("license-md5") is None for a in web.requests) and mb.terms_accepted is False
      and not any(a.one("attributes") == "all" for a in web.requests), [a.query_text for a in web.requests])
r, op, ol, m = run("study 433126", accept_terms=True)
accepts = [a for a in web.requests if a.one("license-md5")]
check("study, with the user's acceptance given: the terms are accepted once, by the sum of the very text Movebank sent, "
      "and the run says in whose name", isinstance(r, dict) and len(accepts) == 1
      and accepts[0].one("license-md5") == hashlib.md5(TERMS_HTML).hexdigest()
      and m.has("The study's license terms were accepted in the name of the Movebank account maria, as you asked."),
      (repr(r)[:120], [a.query_text for a in accepts], m.lines[:5]))
folder = os.path.join(work, M.DOWNLOADS_FOLDER, os.path.basename(op))
stem = "Pronghorn_movements_near_Flagstaff_study_433126"
check("study: its data are written as a file in the form of Movebank's own, with reference data and a README",
      sorted(os.listdir(folder)) == sorted([stem + ".csv", stem + "-reference-data.csv", "README_" + stem + ".txt"]), os.listdir(folder))
head = open(os.path.join(folder, stem + ".csv"), encoding="utf-8").readline().strip().split(",")
check("study: the file's columns are named as in Movebank's files, with the sensor's name and the study's name added",
      head[:5] == ["event-id", "visible", "timestamp", "location-long", "location-lat"] and head[-2:] == ["sensor-type", "study-name"]
      and M.is_data_file(os.path.join(folder, stem + ".csv")), head)
check("study: the same fixes, lines and counts as from the published files",
      isinstance(r, dict) and (r["written"], r["line_count"], r["study_id"], r["doi"]) == (8, 2, "433126", ""), repr(r)[:200])
lines = dict((x["individual_local_identifier"], x) for x in table(ol, lf))
check("study: sex from the animals' records, life stage from their deployments, the sensor by name",
      (lines["A1"]["animal_sex"], lines["A1"]["animal_life_stage"], lines["A1"]["sensor_types"], lines["B2"]["animal_life_stage"])
      == ("f", "adult", "gps", "yearling"), lines.get("A1"))
readme = open(os.path.join(folder, "README_" + stem + ".txt"), encoding="utf-8").read()
check("study: the README says what the study is, who fetched it and how, and holds the terms that were accepted",
      readme.startswith("Movebank study: Pronghorn movements near Flagstaff\r\nMovebank study ID: 433126\r\n".replace("\r\n", "\n"))
      and "with the Movebank account maria" in readme and "Rows fetched: 13" in readme
      and "You may use these data for research only & must contact the owner before publishing." in readme
      and "Citation: Lopez M (2024) Pronghorn movements. Movebank study 433126." in readme, readme[:300])
md = arcpy.metadata.Metadata(op)
check("study: the metadata names the study and the account, and carries the terms under Use Limitations",
      'the Movebank study "Pronghorn movements near Flagstaff" (study ID 433126; license the owner\'s own terms;' in (md.summary or "")
      and "read with the Movebank account maria" in (md.summary or "")
      and "must contact the owner before publishing" in (md.accessConstraints or ""), md.summary)
check("study: the messages ask that the owner be contacted, and name the owner once",
      m.has("Movebank strongly encourages users to make a reasonable attempt to contact a study's owner")
      and m.has("This study's owner: Maria Lopez."), [s for s in m.lines if "owner" in s])
everything_written = m.everything() + readme + (md.summary or "") + (md.description or "") + (md.accessConstraints or "") + (md.credits or "")
check("study: the password is in no message, no file, no metadata, no web address",
      PASSWORD not in everything_written and all(PASSWORD not in a.target for a in web.requests)
      and PASSWORD not in open(os.path.join(folder, stem + ".csv"), encoding="utf-8").read())
check("study: every request to Movebank carried the sign-in, and went nowhere but Movebank's service",
      web.requests != [] and all(a.headers.get("authorization") == BASIC and a.path == "/movebank/service/direct-read" for a in web.requests),
      set(a.path for a in web.requests))

mb = fresh()
r, op, ol, m = run("433126", date_from=datetime.datetime(2024, 5, 2), date_to=datetime.datetime(2024, 5, 2))
ev = [a for a in web.requests if a.one("attributes") == "all"]
check("study with dates: Movebank is asked for those days only, the last one to its last moment; terms already accepted are not asked for again",
      isinstance(r, dict) and len(ev) == 1 and (ev[0].one("timestamp_start"), ev[0].one("timestamp_end")) == ("20240502000000000", "20240502235959999")
      and all(a.one("license-md5") is None for a in web.requests), [a.query_text for a in ev])
mb = fresh()
mb.access = "false"
r, op, ol, m = run("study 433126")
check("study the account may see but not download: refused, with where to ask",
      isinstance(r, M.MovebankError) and "The Movebank account maria may not download this study, though it may see it." in str(r)
      and A.STUDY_PAGE.format("433126") in str(r), repr(r))
mb = fresh()
r, op, ol, m = run("study 999999")
check("a study Movebank does not show the account", isinstance(r, M.MovebankError) and "Movebank has no study with the ID 999999 that the account maria can see." in str(r), repr(r))
mb = fresh()
account[0] = (USER, "wrong")
r, op, ol, m = run("study 433126")
check("Movebank does not accept the sign-in: said plainly, naming the account and not the password",
      isinstance(r, M.MovebankError) and "Movebank did not accept the sign-in of maria (401)." in str(r) and "wrong" not in str(r), repr(r))
mb = fresh()
mb.study["number_of_deployed_locations"] = "3000000"
r, op, ol, m = run("study 433126")
check("a large study with no dates given: warned of its size beforehand (3,000,000 fixes, about 544 MB)",
      m.warned("That is a large study: the file of its 3,000,000 fixes will be about 543.6 MB"), m.warnings)
mb = fresh()
web.forget()
half = csv_text(["event_id", "timestamp", "location_long", "location_lat", "individual_local_identifier"],
                [[str(i), "2024-05-01 00:%02d:00.000" % (i % 60), "-111.0", "35.0", "A1"] for i in range(400)]).encode()
web.fail(lambda q: q.one("attributes") == "all", "cut", body=half)
mb = FakeMovebank(web)
r, op, ol, m = run("study 433126")
folder = os.path.join(work, M.DOWNLOADS_FOLDER, os.path.basename(op))
kept = [n for n in (os.listdir(folder) if os.path.isdir(folder) else []) if n.endswith(".csv") and "reference" not in n]
check("a study cut off on its way: the run does not load the part that came as if it were the whole study",
      isinstance(r, M.MovebankError) and "Movebank stopped sending the study part way" in str(r) and "so nothing was kept" in str(r)
      and not arcpy.Exists(op) and not arcpy.Exists(ol) and kept == [],
      "the run ended with %s, having written %s points; files kept: %s" % (
          repr(r) if isinstance(r, BaseException) else "no error", r.get("written") if isinstance(r, dict) else "no", kept))
mb = fresh()
web.forget()
web.fail(lambda q: q.one("attributes") == "all", "cut stream", body=half)
mb = FakeMovebank(web)
r, op, ol, m = run("study 433126")
folder = os.path.join(work, M.DOWNLOADS_FOLDER, os.path.basename(op))
kept = [n for n in (os.listdir(folder) if os.path.isdir(folder) else []) if n.endswith(".csv") and "reference" not in n]
check("a study sent in pieces with no length promised, and cut off: refused in words, and nothing kept or loaded",
      isinstance(r, M.MovebankError) and "Movebank stopped sending the study after" in str(r) and "so nothing was kept" in str(r)
      and not arcpy.Exists(op) and not arcpy.Exists(ol) and kept == [], (repr(r), kept))


# cancelled, or failed, while the outputs are written: the points were whole, the lines were not begun
def stop_here(_pts):
    raise M.Cancelled()


mb = fresh()
with pointed(M, _split_dateline=stop_here):
    r, op, ol, m = run("https://doi.org/" + DOI)
check("cancelled while the track lines are built: no output is left under either name, the points included",
      isinstance(r, M.Cancelled) and not arcpy.Exists(op) and not arcpy.Exists(ol), (repr(r), arcpy.Exists(op), arcpy.Exists(ol)))

# ---- what is refused before anything is asked
mb = fresh()
for name, kw, words in (
        ("no dataset named", dict(dataset=""), "Name the dataset: the DOI of a dataset in the Movebank Data Repository"),
        ("an output that is not one of the three", dict(dataset=DOI, output="Everything"), "The output must be one of: Points and track lines; Points; Track lines."),
        ("dates the wrong way round", dict(dataset=DOI, date_from=datetime.datetime(2024, 6, 1), date_to=datetime.datetime(2024, 5, 1)),
         "The first date is later than the last date.")):
    ds = kw.pop("dataset")
    r, op, ol, m = run(ds, **kw)
    check("refused: %s" % name, isinstance(r, M.MovebankError) and words in str(r), repr(r))
m_ = Msgs()
e = error_of(lambda: M.run(DOI, out_points=os.path.join(gdb, "same"), out_lines=os.path.join(gdb, "SAME"), messages=m_))
check("refused: the same name for the points and the lines", isinstance(e, M.MovebankError) and "need different output names" in str(e), repr(e))
e = error_of(lambda: M.run(DOI, out_points=None, out_lines=os.path.join(gdb, "t"), messages=m_))
check("refused: no output named for the points", isinstance(e, M.MovebankError) and "Give the output feature class for the points." in str(e), repr(e))
check("refused: none of those asked Movebank anything", web.requests == [], web.requests)

check("at the end: nothing tried to reach another computer", guard.attempts == [], guard.attempts)
for p in reversed(points):
    p.__exit__(None, None, None)
check("at the end: the engines' own addresses are put back",
      M.REPO == "https://datarepository.movebank.org/server/api/" and A.SERVICE == "https://www.movebank.org/movebank/service/direct-read?")
for k, v in env_before.items():
    if v is not None:
        os.environ[k] = v
web.close()
guard.stop()

print("\n%d passed, %d failed%s" % (passed, failed, " (and %d known faults not yet fixed)" % known if known else ""))
arcpy.management.ClearWorkspaceCache()
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)
