# -*- coding: utf-8 -*-
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

"""Monthly check of the online sources the Online Data tools depend on.

Run it once a month or so, and before every release:

    "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" DevScripts\\check_online_sources.py

It needs an internet connection and nothing else: no ArcGIS license, no
account, no password. It reads only. It never signs in, never requests a
GBIF download, never asks the Burn Severity Portal for a data bundle, never
accepts a Movebank study's license terms and never downloads a PRISM grid.

Three kinds of check:

1. THE TOOLS' OWN REQUESTS. It imports the engines in WildlifeTools\\Toolboxes
   and makes the same small requests the tools make, with answers that are
   known (the pronghorn is GBIF taxon 2440902 and iNaturalist taxon 42429;
   the Museum Fire is in the MTBS catalog; and so on). A failure here means a
   tool is broken, or about to be.

2. WEB ADDRESSES. Every page the tools link to or the help pages cite is
   asked for. A page that has moved or gone shows up here.

3. API DOCUMENTATION. The parts of each service's published API description
   that the tools use are fingerprinted and compared with the fingerprints
   saved by the last run (online_sources_baseline.json, beside this script).
   A change does not mean a tool is broken. It means the instructions have
   changed and somebody should read them.

The exit code is 0 when nothing failed, 1 when a check failed. A changed
fingerprint alone is reported and does not fail the run.

Options:
    --accept    save today's fingerprints as the new baseline (do this after
                you have read what changed)
"""
import datetime
import hashlib
import json
import os
import re
import sys
import traceback
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLBOXES = os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes")
BASELINE = os.path.join(HERE, "online_sources_baseline.json")
AGENT = "JennessWildlifeTools-source-check/1.0"

sys.path.insert(0, TOOLBOXES)

results = []          # (group, name, status, detail)   status: OK, FAIL, NOTE


def record(group, name, status, detail=""):
    results.append((group, name, status, detail))
    print("  [{:4}] {}{}".format(status, name, (": " + detail) if detail else ""))


def check(group, name):
    """Decorator: run the function now; it returns a detail string, or raises."""
    def wrap(fn):
        try:
            record(group, name, "OK", fn() or "")
        except AssertionError as e:
            record(group, name, "FAIL", str(e) or "the answer was not the expected one")
        except Exception as e:                                   # noqa: BLE001
            last = traceback.extract_tb(sys.exc_info()[2])[-1]
            record(group, name, "FAIL", "{}: {} (at {} line {})".format(
                type(e).__name__, str(e)[:300], os.path.basename(last.filename), last.lineno))
        return fn
    return wrap


def http(url, timeout=60):
    """(status code, body bytes, final address). Never raises for an HTTP status."""
    req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode(), r.read(), r.geturl()
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:                                        # noqa: BLE001
            body = b""
        return e.code, body, url


def say(_text):
    pass


# =============================================================================
#  1. the tools' own requests
# =============================================================================
def tool_requests():
    # ---------------- GBIF ----------------
    print("\nGBIF (api.gbif.org)")
    try:
        import gbif_occurrences as G
    except Exception as e:                                       # noqa: BLE001
        record("GBIF", "import gbif_occurrences", "FAIL", str(e))
        G = None
    if G is not None:
        @check("GBIF", "a scientific name resolves to its backbone key")
        def _():
            got = G.resolve_taxa("Antilocapra americana", say)
            assert "2440902" in json.dumps(got, default=str), "got {}".format(str(got)[:200])
            return "Antilocapra americana -> 2440902"

        @check("GBIF", "occurrence search answers with a count")
        def _():
            r = G._json(G.V1 + "occurrence/search?limit=0&taxonKey=2440902") or {}
            assert int(r.get("count") or 0) > 1000, "count was {}".format(r.get("count"))
            return "{:,} pronghorn records".format(int(r["count"]))

        @check("GBIF", "predicate search (the request a fetch makes) answers with records")
        def _():
            body = json.dumps({"predicate": {"type": "equals", "key": "TAXON_KEY", "value": "2440902"},
                               "limit": 1, "offset": 0}).encode("utf-8")
            r = G._json(G.V1 + "occurrence/search/predicate", data=body) or {}
            rec = (r.get("results") or [{}])[0]
            missing = [k for k in ("key", "datasetKey", "basisOfRecord", "scientificName", "decimalLatitude",
                                   "decimalLongitude") if k not in rec]
            assert int(r.get("count") or 0) > 1000 and not missing, "count {}; a record lacks {}".format(
                r.get("count"), missing)
            return "a record still has its key, dataset, basis of record, name and coordinates"

        @check("GBIF", "a dataset's title can be read")
        def _():
            d = G._json(G.V1 + "dataset/50c9509d-22c7-4a22-a47d-8c48425ef4a7") or {}
            assert "iNaturalist" in (d.get("title") or ""), "title was {!r}".format(d.get("title"))
            return d["title"]

        @check("GBIF", "a finished download can still be described")
        def _():
            d = G.download_status("0010714-261110092616502")
            assert d.get("status") and d.get("doi"), "status {!r}, doi {!r}".format(d.get("status"), d.get("doi"))
            return "status {}, doi {}".format(d["status"], d["doi"])

    # ---------------- iNaturalist ----------------
    print("\niNaturalist (api.inaturalist.org)")
    try:
        import inaturalist_observations as N
    except Exception as e:                                       # noqa: BLE001
        record("iNaturalist", "import inaturalist_observations", "FAIL", str(e))
        N = None
    if N is not None:
        @check("iNaturalist", "a name resolves to its taxon id")
        def _():
            got = N.resolve_taxa("Antilocapra americana", say)
            assert "42429" in json.dumps(got, default=str), "got {}".format(str(got)[:200])
            return "Antilocapra americana -> 42429"

        @check("iNaturalist", "a place resolves to its place id")
        def _():
            got = N.resolve_place("Arizona, US", say)
            assert re.search(r"\b40\b", json.dumps(got, default=str)), "got {}".format(str(got)[:200])
            return "Arizona, US -> 40"

        @check("iNaturalist", "the annotation terms can be read")
        def _():
            n = len(N.annotation_labels())
            assert n >= 10, "only {} annotation values".format(n)
            return "{} annotation values".format(n)

        @check("iNaturalist", "version 1 counts observations")
        def _():
            r = N._json(N.API1 + "observations?taxon_id=42429&place_id=40&quality_grade=research&per_page=0") or {}
            assert int(r.get("total_results") or 0) > 100, "total_results was {}".format(r.get("total_results"))
            return "{:,} research-grade pronghorn observations in Arizona".format(int(r["total_results"]))

        @check("iNaturalist", "version 2 returns observations with the fields asked for")
        def _():
            r = N._json(N.API + "observations?taxon_id=42429&place_id=40&per_page=1&order_by=id&order=asc"
                        "&fields=(id:!t,uuid:!t,quality_grade:!t,obscured:!t,taxon:(id:!t,name:!t),"
                        "geojson:(coordinates:!t))") or {}
            rec = (r.get("results") or [{}])[0]
            missing = [k for k in ("id", "quality_grade", "taxon", "geojson") if k not in rec]
            assert not missing, "an observation lacks {}".format(missing)
            return "an observation still has its id, grade, taxon and coordinates"

        @check("iNaturalist", "species counts (tab 3) answer")
        def _():
            r = N._json(N.API1 + "observations/species_counts?place_id=40&quality_grade=research&per_page=1") or {}
            row = (r.get("results") or [{}])[0]
            assert int(r.get("total_results") or 0) > 100 and "taxon" in row and "count" in row, \
                "total {}; row keys {}".format(r.get("total_results"), sorted(row))
            return "{:,} species".format(int(r["total_results"]))

    # ---------------- Movebank ----------------
    print("\nMovebank (datarepository.movebank.org, www.movebank.org)")
    try:
        import movebank_tracks as M
        import movebank_account as A
    except Exception as e:                                       # noqa: BLE001
        record("Movebank", "import movebank_tracks / movebank_account", "FAIL", str(e))
        M = None
    if M is not None:
        DOI = "10.5441/001/1.74pf3n2p"

        @check("Movebank", "the repository search finds a published dataset")
        def _():
            items, total = M.search(text="pronghorn")
            assert total >= 1 and any(DOI in json.dumps(i, default=str) for i in items), \
                "{} datasets, and none is {}".format(total, DOI)
            return "{} datasets match 'pronghorn'".format(total)

        @check("Movebank", "a dataset is found by its DOI, with its files")
        def _():
            package = M.find_package(DOI)
            files = M.list_files(package)
            assert files, "the dataset lists no files"
            return "{} files".format(len(files))

        @check("Movebank", "the account service is where the tool expects it")
        def _():
            code, _body, _u = http(A.SERVICE + "entity_type=tag_type")
            assert code in (200, 401, 403), "HTTP {} without a sign-in (401 is the expected answer)".format(code)
            return "HTTP {} without a sign-in, as expected (nothing was signed in)".format(code)

    # ---------------- Burn severity ----------------
    print("\nBurn severity (edcintl.cr.usgs.gov, imagery.geoplatform.gov, burnseverity.cr.usgs.gov)")
    try:
        import burn_severity as B
    except Exception as e:                                       # noqa: BLE001
        record("Burn severity", "import burn_severity", "FAIL", str(e))
        B = None
    if B is not None:
        found = {}

        for prog in ("MTBS", "RAVG", "BAER"):
            @check("Burn severity", "the fire catalog lists {} fires".format(prog))
            def _(prog=prog):
                n = B.count(prog)
                assert n > 100, "only {} fires".format(n)
                return "{:,} records".format(n)

        @check("Burn severity", "a fire is found by name, with the properties the tool reads")
        def _():
            fires = B.search("MTBS", with_geometry=True, name="museum", year_from=2019, year_to=2019)
            hit = [f for f in fires if (f.get("properties") or {}).get("event_id") == "AZ3525811164320190721"]
            assert hit, "the 2019 Museum Fire (AZ3525811164320190721) was not found"
            found["museum"] = hit[0]
            props = hit[0]["properties"]
            missing = [f[4] for f in B.FIELDS if f[4] and f[4] not in props]
            assert not missing, "the catalog no longer has: {}".format(", ".join(missing))
            assert hit[0].get("geometry"), "the fire came without a perimeter"
            return "the 2019 Museum Fire, with all {} properties and a perimeter".format(
                len([f for f in B.FIELDS if f[4]]))

        @check("Burn severity", "the MTBS mosaic of a fire's year is offered (WCS)")
        def _():
            fire = found.get("museum")
            assert fire, "skipped: the Museum Fire was not found"
            cov = "mtbs__mtbs_{}_{}".format(B.region_of(fire), B.year_of(fire))
            code, body, _u = http(B.GEOSERVER + "wcs?service=WCS&version=2.0.1&request=DescribeCoverage&coverageId=" + cov,
                                  timeout=120)
            text = body.decode("utf-8", "replace")
            assert code == 200 and "CoverageDescription" in text and "Exception" not in text[:600], \
                "HTTP {} for coverage {}".format(code, cov)
            return cov

        names = ["USFS_EDW_MTBS_CONUS"]
        for prog in ("RAVG", "BAER"):
            for _key, (_short, _classes, how) in B.PROGRAMS[prog]["products"].items():
                if how[0] == "image":
                    names.append(how[1])
        for name in names:
            @check("Burn severity", "image service {}".format(name))
            def _(name=name):
                code, body, _u = http(B.IMAGERY + name + "/ImageServer?f=json", timeout=120)
                d = json.loads(body.decode("utf-8", "replace")) if code == 200 else {}
                assert code == 200 and "error" not in d and d.get("pixelType"), "HTTP {}; {}".format(
                    code, str(d.get("error") or "no pixel type")[:200])
                return "pixel type {}".format(d["pixelType"])

        @check("Burn severity", "the portal's viewer page (where bundles are ordered) answers")
        def _():
            code, _body, _u = http(B.PORTAL + "viewer/?product=MTBS")
            assert code == 200, "HTTP {}".format(code)
            return "HTTP 200 (no bundle was asked for)"

    prism_requests()


def prism_requests():
    """PRISM's web service. No grid is downloaded: a grid is a whole-country file, and PRISM allows a file
    to be downloaded twice in 24 hours. The checks ask for the days three grids were issued, for two
    refusals, and for the first four bytes of one grid nobody is likely to want that day."""
    print("\nPRISM (services.nacse.org)")
    try:
        import prism_climate as P
    except Exception as e:                                       # noqa: BLE001
        record("PRISM", "import prism_climate", "FAIL", str(e))
        return

    @check("PRISM", "the release-date service gives the day a grid was last issued")
    def _():
        got = P.Service().release_dates(P.RES_4KM, "ppt", "202001", "202003")
        assert sorted(got) == ["202001", "202002", "202003"], "it answered for {}".format(sorted(got) or "nothing")
        return "the precipitation grids of January to March 2020, last issued {}".format(
            max(got.values()).isoformat())

    def refusal(what, address, words):
        @check("PRISM", what)
        def _():
            code, body, _u = http(address)
            text = " ".join(body[:400].decode("utf-8", "replace").split())
            assert code == 200 and not body.startswith(b"PK") and words in text.lower(), \
                "HTTP {}: {}".format(code, text[:200] or "(nothing)")
            return text[:90]

    refusal("a date that has no grid is refused in the words the tool looks for",
            P.SERVICE + "us/4km/ppt/209912", "invalid date")
    refusal("a variable that does not exist is refused in words, not with a grid",
            P.SERVICE + "us/4km/rain/202001", "invalid element")

    @check("PRISM", "the grid service sends a zip file (the first four bytes of one grid; no grid is downloaded)")
    def _():
        req = urllib.request.Request(P.SERVICE + "us/4km/ppt/189501",
                                     headers={"User-Agent": AGENT, "Range": "bytes=0-3"})
        with urllib.request.urlopen(req, timeout=120) as r:
            code, head = r.getcode(), r.read(4)
        assert head[:2] == b"PK", "HTTP {}; it began with {!r}".format(code, head)
        return "HTTP {} and a zip file's first bytes".format(code)


# =============================================================================
#  2. web addresses
# =============================================================================
# address -> the HTTP statuses that mean "still there". www.gbif.org and
# www.inaturalist.org refuse scripts with 403 and serve browsers normally, so
# 403 from them is the expected answer; a 404 is not.
BROWSER_ONLY = (200, 403)
PAGES = [
    ("GBIF", "https://www.gbif.org/", BROWSER_ONLY),
    ("GBIF", "https://www.gbif.org/what-is-gbif", BROWSER_ONLY),
    ("GBIF", "https://www.gbif.org/citation-guidelines", BROWSER_ONLY),
    ("GBIF", "https://www.gbif.org/terms/data-user", BROWSER_ONLY),
    ("GBIF", "https://www.gbif.org/derived-dataset/register", BROWSER_ONLY),
    ("GBIF", "https://www.gbif.org/user/profile", BROWSER_ONLY),
    ("GBIF", "https://www.gbif.org/species/2440902", BROWSER_ONLY),
    ("GBIF", "https://dwc.tdwg.org/list/", (200,)),
    ("GBIF", "https://techdocs.gbif.org/en/openapi/", (200,)),
    ("iNaturalist", "https://www.inaturalist.org/", BROWSER_ONLY),
    ("iNaturalist", "https://www.inaturalist.org/pages/developers", BROWSER_ONLY),
    ("iNaturalist", "https://www.inaturalist.org/taxa/42429", BROWSER_ONLY),
    ("iNaturalist", "https://www.inaturalist.org/places/40", BROWSER_ONLY),
    ("iNaturalist", "https://api.inaturalist.org/v2/docs/", (200,)),
    ("iNaturalist", "https://help.inaturalist.org/en/support/solutions/articles/151000169936", (200,)),
    ("iNaturalist", "https://help.inaturalist.org/en/support/solutions/articles/151000169938", (200,)),
    ("iNaturalist", "https://help.inaturalist.org/en/support/solutions/articles/151000173076", (200,)),
    ("iNaturalist", "https://help.inaturalist.org/en/support/solutions/articles/151000170344-how-should-i-cite-inaturalist-", (200,)),
    ("iNaturalist", "https://help.inaturalist.org/en/support/solutions/articles/151000192921-how-to-make-an-observation", (200,)),
    ("iNaturalist", "https://help.inaturalist.org/en/support/solutions/articles/151000171681-how-to-use-the-identify-page", (200,)),
    ("Movebank", "https://www.movebank.org/", (200,)),
    ("Movebank", "https://www.movebank.org/cms/movebank-content/about-movebank", (200,)),
    ("Movebank", "https://www.movebank.org/cms/movebank-content/data-repository", (200,)),
    ("Movebank", "https://www.movebank.org/cms/movebank-content/movebank-attribute-dictionary", (200,)),
    ("Movebank", "https://www.movebank.org/cms/movebank-content/general-movebank-terms-of-use", (200,)),
    ("Movebank", "https://datarepository.movebank.org", (200,)),
    ("Movebank", "https://doi.org/10.5441/001/1.74pf3n2p", (200,)),
    ("Burn severity", "https://burnseverity.cr.usgs.gov/", (200,)),
    ("Burn severity", "https://burnseverity.cr.usgs.gov/ravg/", (200,)),
    ("Burn severity", "https://burnseverity.cr.usgs.gov/ravg/background-products-applications", (200,)),
    ("Burn severity", "https://burnseverity.cr.usgs.gov/baer/", (200,)),
    ("Burn severity", "https://burnseverity.cr.usgs.gov/baer/background-products-applications", (200,)),
    ("Burn severity", "https://burnseverity.cr.usgs.gov/direct-download?product=MTBS", (200,)),
    ("Burn severity", "https://www.mtbs.gov/", (200,)),
    ("Burn severity", "https://www.mtbs.gov/project-overview", (200,)),
    ("Burn severity", "https://www.mtbs.gov/mapping-methods", (200,)),
    ("PRISM", "https://prism.oregonstate.edu/", (200,)),
    ("PRISM", "https://prism.oregonstate.edu/overview/", (200,)),
    ("PRISM", "https://prism.oregonstate.edu/data/", (200,)),
    ("PRISM", "https://prism.oregonstate.edu/terms/", (200,)),
    ("PRISM", "https://prism.oregonstate.edu/documents/PRISM_datasets.pdf", (200,)),
    ("PRISM", "https://prism.oregonstate.edu/documents/PRISM_downloads_web_service.pdf", (200,)),
]


def web_addresses():
    print("\nWeb addresses the tools link to and the help pages cite")
    for group, url, fine in PAGES:
        try:
            code, _body, final = http(url)
        except Exception as e:                                   # noqa: BLE001
            record(group, url, "FAIL", "{}: {}".format(type(e).__name__, str(e)[:200]))
            continue
        # a move to another page of the same site is ordinary (a home page that
        # opens its front page); a move to another site is worth a look
        host = lambda u: re.sub(r"^https?://(www\.)?", "", u).split("/")[0].lower()    # noqa: E731
        moved = "" if host(final) == host(url) else "; now answers from " + final
        if code not in fine:
            record(group, url, "FAIL", "HTTP {}{}".format(code, moved))
        elif code == 403:
            record(group, url, "OK", "HTTP 403: the site refuses scripts and serves browsers, as it always has")
        elif moved and not url.startswith("https://doi.org/"):
            record(group, url, "NOTE", "HTTP 200{}".format(moved))
        else:
            record(group, url, "OK", "")


# =============================================================================
#  3. API documentation
# =============================================================================
# name -> (address, the path prefixes the tools use; None = the whole document)
DOCS = {
    "GBIF occurrence API (OpenAPI)": (
        "https://techdocs.gbif.org/openapi/occurrence.json",
        ["/occurrence/search", "/occurrence/download/request", "/occurrence/download/{", "/occurrence/download/dataset"]),
    "iNaturalist API version 1 (Swagger)": (
        "https://api.inaturalist.org/v1/swagger.json",
        ["/observations", "/taxa", "/places", "/controlled_terms"]),
    "iNaturalist API version 2 (OpenAPI)": (
        "https://api.inaturalist.org/v2/api-docs",
        ["/observations", "/taxa"]),
    "Movebank REST API (movebank-api.md on GitHub)": (
        "https://raw.githubusercontent.com/movebank/movebank-api-doc/master/movebank-api.md", None),
}


def fingerprint(body, prefixes):
    """A hash of the parts of an API description the tools use, and how many
    paths went into it."""
    if prefixes:
        try:
            paths = json.loads(body.decode("utf-8")).get("paths") or {}
            used = {p: v for p, v in paths.items() if any(p.startswith(x) or p.startswith("/v1" + x) for x in prefixes)}
            if used:
                text = json.dumps(used, sort_keys=True).encode("utf-8")
                return hashlib.sha256(text).hexdigest(), len(used)
        except ValueError:
            pass
    return hashlib.sha256(body).hexdigest(), None


def documentation(accept):
    print("\nAPI documentation, against the fingerprints of the last run")
    try:
        old = json.load(open(BASELINE, encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    new = {}
    today = datetime.date.today().isoformat()
    for name, (url, prefixes) in DOCS.items():
        try:
            code, body, _u = http(url, timeout=120)
        except Exception as e:                                   # noqa: BLE001
            record("Documentation", name, "FAIL", "{}: {}".format(type(e).__name__, str(e)[:200]))
            if name in old:
                new[name] = old[name]
            continue
        if code != 200:
            record("Documentation", name, "FAIL", "HTTP {} from {}".format(code, url))
            if name in old:
                new[name] = old[name]
            continue
        digest, n = fingerprint(body, prefixes)
        what = "the {} paths the tools use".format(n) if n else "the whole document"
        was = old.get(name)
        if not was:
            record("Documentation", name, "OK", "first run: fingerprint of {} saved".format(what))
            new[name] = {"sha256": digest, "seen": today, "url": url}
        elif was.get("sha256") == digest:
            record("Documentation", name, "OK", "{} unchanged since {}".format(what, was.get("seen")))
            new[name] = was
        else:
            record("Documentation", name, "NOTE", "{} CHANGED since {}. Read {} and then run this script "
                   "with --accept".format(what, was.get("seen"), url))
            new[name] = {"sha256": digest, "seen": today, "url": url} if accept else was
    if new != old:
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(new, f, indent=2, sort_keys=True)
            f.write("\n")


# =============================================================================
def main():
    accept = "--accept" in sys.argv[1:]
    print("Online sources of the Wildlife and Forestry Tools, checked {}".format(
        datetime.datetime.now().strftime("%d %B %Y at %H:%M")))
    print("Engines read from {}".format(TOOLBOXES))
    tool_requests()
    web_addresses()
    documentation(accept)

    fails = [r for r in results if r[2] == "FAIL"]
    notes = [r for r in results if r[2] == "NOTE"]
    print("\n" + "=" * 72)
    print("{} checks: {} passed, {} failed, {} to read.".format(
        len(results), len(results) - len(fails) - len(notes), len(fails), len(notes)))
    for title, rows in (("FAILED", fails), ("TO READ", notes)):
        if rows:
            print("\n" + title)
            for group, name, _status, detail in rows:
                print("  {} | {}\n      {}".format(group, name, detail))
    if not fails and not notes:
        print("Nothing has changed that the tools depend on.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
