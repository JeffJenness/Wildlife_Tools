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

"""Movebank studies, read with a Movebank account.

The Movebank Data Repository (movebank_tracks.py) holds the datasets whose
owners have published them, and needs no account. Movebank itself holds
many more studies, and a signed-in account may download those whose owners
allow it. This module reads them through Movebank's "direct-read" service
(https://github.com/movebank/movebank-api-doc):

* the sign-in is the user's own Movebank user name and password, kept in
  Windows Credential Manager under JennessWildlifeTools.Movebank and sent
  only to www.movebank.org, over https. It is never written to a message,
  a file or the metadata;
* the first request for a study's data returns the study's LICENSE TERMS
  instead of the data, until the account has accepted them. They are
  accepted by repeating the request with the MD5 sum of the terms. This
  module does that only when the caller says the user has read and
  accepted them (``accept``); otherwise it stops and shows the terms;
* the data are written to disk in the format of Movebank's own data
  files (hyphenated column names, a sensor-type and a study-name column),
  with a reference-data file and a README that holds the study's terms,
  so that the rest of the Movebank Animal Tracks tool reads a study
  exactly as it reads a published dataset.

No ArcGIS here: only the standard library.
"""
import base64
import csv
import datetime
import hashlib
import html as _html
import http.client
import http.cookiejar
import io
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

import gbif_occurrences as G

SERVICE = "https://www.movebank.org/movebank/service/direct-read?"
STUDY_PAGE = "https://www.movebank.org/cms/webapp?gwt_fragment=page=studies,path=study{}"
TERMS_OF_USE = "https://www.movebank.org/cms/movebank-content/general-movebank-terms-of-use"
CREDENTIAL_TARGET = "JennessWildlifeTools.Movebank"
USER_AGENT = "WildlifeTools-AddIn (Jenness Enterprises; ArcGIS Pro)"
LICENSES = {"CC_0": "CC0 1.0 Universal", "CC_BY": "CC BY 4.0", "CC_BY_NC": "CC BY-NC 4.0",
            "CUSTOM": "the owner's own terms"}
LICENSE_URLS = {"CC_0": "https://creativecommons.org/publicdomain/zero/1.0/",
                "CC_BY": "https://creativecommons.org/licenses/by/4.0/",
                "CC_BY_NC": "https://creativecommons.org/licenses/by-nc/4.0/"}
LARGE_STUDY = 2000000                # location fixes: warn, and say how big the file will be
BYTES_PER_FIX = 190.0                # of the data file, measured (a GPS study of 300,000 fixes)


def _errors():
    import movebank_tracks
    return movebank_tracks.MovebankError, movebank_tracks.Cancelled


n0 = G.n0


def sign_in():
    """(user name, password) from Windows Credential Manager, or from the
    MOVEBANK_USER and MOVEBANK_PASSWORD environment variables. None when
    there is no sign-in."""
    try:
        import gbif_occurrences
        got = gbif_occurrences._read_windows_credential(CREDENTIAL_TARGET)
    except Exception:                                            # noqa: BLE001
        got = None
    if got and got[0]:
        return got
    user, pw = os.environ.get("MOVEBANK_USER"), os.environ.get("MOVEBANK_PASSWORD")
    if user and pw:
        return user, pw
    return None


def parse_study(text):
    """The Movebank study ID in ``text``, or None. Accepts "study 433126",
    "study:433126", a bare number of five or more digits, and the address
    of a study's page on movebank.org."""
    s = (text or "").strip().strip('"')
    m = re.match(r"^(?:movebank\s+)?study\s*(?:id)?\s*[:#=]?\s*(\d{3,})$", s, flags=re.I)
    if m:
        return m.group(1)
    if re.match(r"^\d{5,}$", s):
        return s
    if "movebank.org" in s.lower():
        m = re.search(r"study(?:_id=|%3D|=)?(\d{3,})", s, flags=re.I)
        if m:
            return m.group(1)
    return None


def plain(raw):
    """A response of the service (HTML) as plain text, paragraphs kept."""
    t = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else (raw or "")
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", t)
    t = _html.unescape(re.sub(r"<[^>]+>", "", t))
    t = _html.unescape(t)                      # the citations are escaped twice
    t = re.sub(r"<[^>]+>", "", t)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in t.replace("\r", "").split("\n")]
    out = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


class Session(object):
    """One signed-in conversation with Movebank (it keeps the cookies the
    license step needs)."""

    def __init__(self, credentials=None):
        Error, _c = _errors()
        cred = credentials or sign_in()
        if not cred:
            raise Error("A Movebank study needs a Movebank sign-in, and none is stored. Sign in on tab 1 of the "
                        "Movebank Animal Tracks window, or store the sign-in from a Command Prompt with: "
                        "cmdkey /generic:{} /user:YOUR_MOVEBANK_USERNAME /pass   (Windows then asks for the "
                        "password). Datasets published in the Movebank Data Repository need no sign-in.".format(
                            CREDENTIAL_TARGET))
        self.user = cred[0]
        self._auth = "Basic " + base64.b64encode("{}:{}".format(cred[0], cred[1]).encode("utf-8")).decode("ascii")
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def open(self, query, timeout=300):
        """The open response of one request. Raises MovebankError with
        words a user can act on."""
        Error, _c = _errors()
        req = urllib.request.Request(SERVICE + query, headers={"User-Agent": USER_AGENT, "Authorization": self._auth})
        try:
            return self._opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise Error("Movebank did not accept the sign-in of {} ({}). Check the user name (not the email "
                            "address) and the password by signing in at www.movebank.org, then sign in again "
                            "in the window. A new account must be activated by Movebank before it works "
                            "here.".format(self.user, e.code))
            if e.code in (502, 503, 504):
                raise Error("Movebank is busy or unavailable right now ({}). Try again in a few minutes.".format(e.code))
            raise Error("Movebank returned an error ({}).".format(e.code))
        except urllib.error.URLError as e:
            raise Error("Movebank could not be reached ({}). Check the internet connection.".format(
                getattr(e, "reason", e)))
        except (TimeoutError, socket.timeout, OSError) as e:
            raise Error("Movebank did not answer in time ({}). Try again.".format(e))

    def get(self, query, timeout=300):
        """(headers with lower-case names, bytes)."""
        Error, _c = _errors()
        r = self.open(query, timeout)
        try:
            raw = r.read()
        except (TimeoutError, socket.timeout, OSError) as e:
            raise Error("Movebank stopped answering ({}). Try again.".format(e))
        finally:
            r.close()
        return dict((k.lower(), v) for k, v in r.headers.items()), raw

    def table(self, query):
        """A CSV answer as a list of dicts."""
        Error, _c = _errors()
        _h, raw = self.get(query)
        text = raw.decode("utf-8", "replace")
        if text.lstrip()[:1] == "<":
            raise Error("Movebank answered: {}".format(plain(raw)[:400]))
        return list(csv.DictReader(io.StringIO(text)))


def study_record(session, study_id):
    """The study's record as the account sees it."""
    Error, _c = _errors()
    rows = session.table("entity_type=study&study_id={}".format(study_id))
    if not rows:
        raise Error("Movebank has no study with the ID {} that the account {} can see.".format(study_id, session.user))
    return rows[0]


_TINY = "&attributes=timestamp&timestamp_start=19000101000000000&timestamp_end=19000101000000001"


def license_terms(session, study_id):
    """The study's license terms (the bytes Movebank sent) when the
    account has not yet accepted them, else None. Asks for the fixes of
    one instant in the year 1900, so no data come back either way."""
    head, raw = session.get("entity_type=event&study_id={}{}".format(study_id, _TINY))
    if head.get("accept-license") == "true" or raw.lstrip()[:1] == b"<":
        return raw
    return None


def accept_terms(session, study_id, raw):
    """Accepts the terms Movebank sent (``raw``), in the account's name.
    True when Movebank then hands over data."""
    md5 = hashlib.md5(raw).hexdigest()
    head, again = session.get("entity_type=event&study_id={}{}&license-md5={}".format(study_id, _TINY, md5))
    return head.get("accept-license") != "true" and again.lstrip()[:1] != b"<"


def _stamp(d):
    """A date or datetime as Movebank's timestamp (yyyyMMddHHmmssSSS)."""
    if not isinstance(d, datetime.datetime):
        d = datetime.datetime.combine(d, datetime.time.min)
    return d.strftime("%Y%m%d%H%M%S") + "000"


def end_of_day(d):
    """The last moment a "last date" covers. A date stands for its whole
    day. So does a date with the time 00:00:00, which is what a date picked
    in a geoprocessing dialog arrives as: read as a moment, it would leave
    out the whole of the last day. Any other time is taken as given."""
    if isinstance(d, datetime.datetime):
        if d.hour or d.minute or d.second or d.microsecond:
            return d
        d = d.date()
    return datetime.datetime.combine(d, datetime.time.max)


def _safe(name, most=60):
    s = re.sub(r"[^A-Za-z0-9]+", "_", name or "").strip("_")
    return (s[:most].strip("_")) or "study"


def fetch_study(session, rec, folder, date_from=None, date_to=None, step=None, cancelled=None, say=None,
                terms_text=None, base=2.0, width=28.0):
    """Fetches a study's events into ``folder`` as a data file in the
    format of Movebank's own files, with a reference-data file and a
    README. The account must already have accepted the study's terms.
    Returns {data, reference, readme, rows}."""
    Error, Cancelled = _errors()
    study_id = rec["id"]
    name = rec.get("name") or "study {}".format(study_id)
    stem = "{}_study_{}".format(_safe(name, 50), study_id)
    os.makedirs(folder, exist_ok=True)

    # sensor types: the service gives their numbers, Movebank's files their names
    sensors = {}
    try:
        for r in session.table("entity_type=tag_type"):
            sensors[r.get("id", "")] = r.get("external_id") or r.get("name") or ""
    except Exception:                                            # noqa: BLE001
        pass

    query = "entity_type=event&study_id={}&attributes=all".format(study_id)
    if date_from:
        query += "&timestamp_start=" + _stamp(date_from)
    if date_to:
        query += "&timestamp_end=" + end_of_day(date_to).strftime("%Y%m%d%H%M%S") + "999"
    try:
        expect = int(float(rec.get("number_of_deployed_locations") or 0))
    except ValueError:
        expect = 0
    data_path = os.path.join(folder, stem + ".csv")
    part = data_path + ".part"
    rows = 0
    deployments = {}                           # deployment id -> animal
    resp = session.open(query, timeout=600)
    whole = False                              # set only when every row has come and been written
    try:
        counted = _Counted(resp)
        text = io.TextIOWrapper(io.BufferedReader(counted, 1 << 16), encoding="utf-8", errors="replace", newline="")
        first = text.read(1)
        if first == "<":
            words = plain(first + text.read(20000))
            if "no data" in words.lower():
                raise Error("Movebank says: {} The account {} may see this study but not download it. Ask the "
                            "study's owner for access through the study's page: {}".format(
                                words[:200], session.user, STUDY_PAGE.format(study_id)))
            raise Error("Movebank did not hand over the data. It answered: {}".format(words[:600]))
        chain = _Chain(first, text)
        rd = csv.reader(chain)
        header = next(rd, None)
        if not header:
            raise Error("Movebank returned no data for the study.")
        idx = dict((c, i) for i, c in enumerate(header))
        i_sensor, i_dep, i_animal = idx.get("sensor_type_id"), idx.get("deployment_id"), idx.get("individual_local_identifier")
        out_header = [c.replace("_", "-") for c in header] + ["sensor-type", "study-name"]
        with open(part, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(out_header)
            last_len = len(header)
            for row in rd:
                if not row:
                    continue
                rows += 1
                last_len = len(row)
                if i_dep is not None and i_animal is not None and len(row) > max(i_dep, i_animal) and row[i_dep]:
                    deployments.setdefault(row[i_dep], row[i_animal])
                w.writerow(row + [sensors.get(row[i_sensor], "") if i_sensor is not None and i_sensor < len(row) else "",
                                  name])
                if rows % 20000 == 0:
                    if cancelled is not None and cancelled():
                        raise Cancelled()
                    if step is not None:
                        step(base + width * min(0.99, rows / float(expect) if expect else 0.5),
                             "Fetching the study from Movebank: {} rows".format(n0(rows)))
        # Did the whole study come? A connection that closes early gives no error (see
        # gbif_occurrences.promised_length), and half a study must never be loaded as the whole of it:
        # the tracks would simply stop short. Two signs of a stream that was cut off: fewer bytes than
        # Movebank said it would send, and a last row that breaks off in the middle of a line.
        promised = G.promised_length(resp)
        if promised is not None and counted.count < promised:
            raise Error("Movebank stopped sending the study part way: {} of {} bytes came ({} rows), so nothing "
                        "was kept. Try again; a range of dates makes the request smaller.".format(
                            n0(counted.count), n0(promised), n0(rows)))
        if rows and chain.last and not chain.last.endswith(("\n", "\r")) and last_len < len(header):
            raise Error("Movebank stopped sending the study part way: row {} breaks off in the middle, so "
                        "nothing was kept. Try again; a range of dates makes the request smaller.".format(n0(rows)))
        whole = True
    except (TimeoutError, socket.timeout, OSError, http.client.HTTPException) as e:
        raise Error("Movebank stopped sending the study after {} rows ({}), so nothing was kept. Try again; a "
                    "range of dates makes the request smaller.".format(n0(rows), e))
    finally:
        try:
            resp.close()
        except Exception:                                        # noqa: BLE001
            pass
        if os.path.exists(part) and (not whole or rows == 0 or (cancelled is not None and cancelled())):
            try:
                os.remove(part)
            except OSError:
                pass
    if rows == 0:
        raise Error("Movebank returned no rows for the study{}.".format(
            " in those dates" if (date_from or date_to) else ""))
    if os.path.exists(data_path):
        os.remove(data_path)
    os.replace(part, data_path)

    # the reference data: sex from the animals, life stage from the deployments
    reference = None
    try:
        animals = session.table("entity_type=individual&study_id={}".format(study_id))
        stage = {}
        try:
            for d in session.table("entity_type=deployment&study_id={}".format(study_id)):
                a = deployments.get(d.get("id", ""))
                if a and d.get("animal_life_stage") and a not in stage:
                    stage[a] = d["animal_life_stage"]
        except Exception:                                        # noqa: BLE001
            pass
        reference = os.path.join(folder, stem + "-reference-data.csv")
        with open(reference, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["animal-id", "animal-taxon", "animal-sex", "animal-life-stage", "animal-nickname",
                        "animal-ring-id", "animal-comments"])
            for a in animals:
                w.writerow([a.get("local_identifier", ""), a.get("taxon_canonical_name", ""), a.get("sex", ""),
                            stage.get(a.get("local_identifier", ""), ""), a.get("nick_name", ""),
                            a.get("ring_id", ""), a.get("comments", "")])
    except Exception:                                            # noqa: BLE001
        reference = None                                         # a courtesy: the fixes are what matter

    readme = os.path.join(folder, "README_{}.txt".format(stem))
    with open(readme, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(readme_text(rec, session.user, terms_text, date_from, date_to, rows))
    return {"data": data_path, "reference": reference, "readme": readme, "rows": rows}


class _Chain(object):
    """Lines of a text stream, with a first piece that was already read.
    ``last`` is the last line handed out."""

    def __init__(self, first, stream):
        self._first, self._stream = first, stream
        self.last = ""

    def __iter__(self):
        return self

    def __next__(self):
        line = self._stream.readline()
        if self._first is not None:
            line, self._first = self._first + line, None
        if not line:
            raise StopIteration
        self.last = line
        return line


class _Counted(io.RawIOBase):
    """Passes a response's bytes through, and counts them."""

    def __init__(self, raw):
        io.RawIOBase.__init__(self)
        self._raw = raw
        self.count = 0

    def readable(self):
        return True

    def readinto(self, b):
        n = self._raw.readinto(b)
        self.count += n or 0
        return n


def license_name(rec):
    return LICENSES.get(rec.get("license_type") or "", rec.get("license_type") or "not stated")


def citation(rec):
    """The study's own citation, as text, or ""."""
    return plain(rec.get("citation") or "")


def readme_text(rec, user, terms_text, date_from, date_to, rows):
    """The README this tool writes beside a study's files: what the study
    is, who to credit, and the terms that were accepted. Its "Terms of
    Use" section is what the tool copies into the output's metadata."""
    def line(label, key):
        v = plain(rec.get(key) or "")
        return "{}: {}\n".format(label, v) if v else ""

    study_terms = plain(rec.get("license_terms") or "")
    parts = [
        "Movebank study: {}\n".format(rec.get("name") or ""),
        "Movebank study ID: {}\n".format(rec.get("id")),
        "Study page: {}\n".format(STUDY_PAGE.format(rec.get("id"))),
        "Fetched {} with the Movebank account {} by the Movebank Animal Tracks tool of the Wildlife and Forestry "
        "Tools add-in for ArcGIS Pro, through Movebank's direct-read service.\n".format(
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), user),
        "Rows fetched: {}{}\n".format(n0(rows), " (fixes from {} to {} only)".format(
            date_from or "the start", date_to or "the end") if (date_from or date_to) else ""),
        "\nA study in Movebank is not a published dataset: its owner can add to it and correct it, so the same "
        "request made later may return different data.\n\n",
        "License: {}\n".format(license_name(rec)),
        line("Principal investigator", "principal_investigator_name"),
        line("Contact person", "contact_person_name"),
        line("Citation", "citation"),
        line("Acknowledgements", "acknowledgements"),
        line("Grants used", "grants_used"),
        line("Taxa", "taxon_ids"),
        line("Sensors", "sensor_type_ids"),
        line("Study objective", "study_objective"),
        "\n----------------------------------------------------------------------\n",
        "Terms of Use\n",
        (terms_text.strip() + "\n") if terms_text else (
            "License: {}\n{}The account {} accepted this study's license terms at Movebank before this "
            "download.\n".format(license_name(rec),
                                 "License terms of the study: {}\n".format(study_terms) if study_terms else "", user)),
        "\nAll use of data from Movebank is also subject to Movebank's general terms of use ({}).\n".format(TERMS_OF_USE),
        "----------------------------------------------------------------------\n",
    ]
    return "".join(parts)
