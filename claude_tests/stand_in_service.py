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

"""A stand-in for a web service, for testing the tools that get their data
from one (GBIF, iNaturalist, Movebank, the Burn Severity Portal, PRISM and
those to come) WITHOUT touching the real service, any account or the
internet.

Three pieces:

  StandIn      a web server on this computer. A test tells it what to answer
               to which request, points the engine at it, runs the engine,
               and then reads back every request the engine made.
  no_internet  a guard. While it is on, any attempt by Python to connect to
               another computer raises InternetReached and is written down.
               Every suite of an online engine runs wholly inside it.
  pointed      sets an engine's web addresses (and anything else) for the
               length of a ``with`` block and puts them back afterward.

HOW TO WRITE THE SUITE OF AN ONLINE ENGINE (the suites of 2026-11-23 follow
this list; so should the suite of each new online tool):

   1. Start with ``guard = no_internet()`` and end with a check that
      ``guard.attempts`` is empty. Replace whatever reads a stored password
      with a function of the test's own: a test never reads the Windows
      Credential Manager.
   2. Write the service's answers out in the test, from the service's own
      documentation, with values chosen so that the right result is known
      beforehand. (Whether the real service still answers in that form is a
      different question, which DevScripts/check_online_sources.py asks the
      real services once a month and before each release.)
   3. What the engine ASKS: the address, each parameter, the User-Agent, the
      pause between requests, the page size, and that no password or token
      is ever in an address or in a message.
   4. What it makes of the ANSWER: every field, including the awkward
      records (no coordinates, empty text, accents, dates of all shapes).
   5. Every page: more than one page, exactly a page, an empty answer, a
      service that says there is more than it will give.
   6. What it does when the service FAILS: each of ``refuse``, 500, 503, 429,
      401, 403, 404, an answer cut short, an answer that is not what it
      should be (a web page in place of data), and silence (a time-out).
      The message must say what happened in words a user can act on.
      For every FILE the engine downloads, both ``cut`` and ``cut stream``:
      Python gives no error when a connection closes before the promised
      bytes have come, so an engine that reads a file in pieces must count
      them itself (``gbif_occurrences.promised_length``). Check that the
      half that came is neither kept under the file's name nor loaded.
   7. The output: fields, coordinate system, metadata and the citation the
      data's owner asks for; and that a run that fails leaves no half-made
      output behind.
   8. Cancelling.

Run on its own, this file tests itself:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" stand_in_service.py
"""
import contextlib
import http.server
import json as _json
import re
import socket
import sys
import threading
import time
import urllib.parse

_LOCAL = ("127.0.0.1", "::1", "localhost")


class InternetReached(OSError):
    """Raised by the guard when something tries to connect to another computer."""


class Request:
    """One request the stand-in received."""

    def __init__(self, method, target, headers, body):
        self.method = method
        self.target = target                                   # the path and the query, as sent
        parts = urllib.parse.urlsplit(target)
        self.path = urllib.parse.unquote(parts.path)
        self.query_text = parts.query
        self.query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
        self.headers = dict((k.lower(), v) for k, v in headers.items())
        self.body = body or b""
        self.time = time.monotonic()
        self.answered = None                                   # the status sent back, or what was done instead

    def one(self, name, default=None):
        """The single value of a query parameter (the first, if it was sent more than once)."""
        return self.query.get(name, [default])[0]

    def form(self):
        """The body read as a web form."""
        return urllib.parse.parse_qs(self.body.decode("utf-8", "replace"), keep_blank_values=True)

    def json(self):
        return _json.loads(self.body.decode("utf-8"))

    def __repr__(self):
        return "<%s %s -> %s>" % (self.method, self.target, self.answered)


class _Rule:
    def __init__(self, match, method, status, headers, body, handler, times, delay, fail):
        self.match, self.method, self.status, self.headers = match, method, status, headers or {}
        self.body, self.handler, self.left, self.delay, self.fail = body, handler, times, delay, fail
        self.used = 0

    def fits(self, req):
        if self.left is not None and self.left <= 0:
            return False
        if self.method and self.method.upper() != req.method:
            return False
        m = self.match
        if m is None:
            return True
        if callable(m):
            return bool(m(req))
        if hasattr(m, "search"):
            return bool(m.search(req.target))
        return req.path.startswith(m) if m.startswith("/") else (m in req.target)


class StandIn:
    """A web server on this computer that answers as a test tells it to.

        with StandIn() as web:
            web.answer("/v1/things", json={"results": [], "count": 0})
            with pointed(engine, API=web.url + "v1/"):
                engine.run(...)
            asked = web.requests            # every request, in order

    ``answer(match, ...)``: ``match`` is the start of a path ("/v1/things"), text found anywhere in the
    address, a compiled regular expression, or a function of the Request; None fits every request. The
    answer is ``json=`` (any Python value), ``body=`` (text or bytes) or ``file=`` (a path), with
    ``status=`` (200) and ``headers=``. ``handler=`` is a function of the Request that returns
    (status, headers, body) or any of the three forms above, for answers that depend on the request (pages).
    ``times=n`` uses the rule for the first n requests that fit and then lets later rules answer: a service
    that fails twice and then works is two rules. ``delay=`` waits that many seconds first.

    ``fail(match, how)``: ``how`` is "refuse" (the connection is dropped with no answer), "cut" (the answer
    is promised whole and stops halfway), "cut stream" (the answer comes in pieces with no length promised,
    as a service sends a result it is still making, and stops halfway), or "silence" (nothing is sent for
    ``seconds``: a time-out).

    Rules are tried in the order they were given. A request that fits none gets 404 and is listed in
    ``unanswered``; a suite should end by checking that list is empty.
    """

    def __init__(self):
        self.requests = []
        self.unanswered = []
        self._rules = []
        self._lock = threading.Lock()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"                      # one request to a connection: nothing lingers

            def log_message(self, *_a):                        # no chatter on the test's output
                pass

            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                req = Request(self.command, self.path, self.headers, body)
                with outer._lock:
                    outer.requests.append(req)
                    rule = next((r for r in outer._rules if r.fits(req)), None)
                    if rule is not None:
                        rule.used += 1
                        if rule.left is not None:
                            rule.left -= 1
                if rule is None:
                    with outer._lock:
                        outer.unanswered.append(req)
                    return self._send(req, 404, {"Content-Type": "application/json"},
                                      b'{"error": "the stand-in service has no answer prepared for this request"}')
                if rule.delay:
                    time.sleep(rule.delay)
                if rule.fail == "refuse":
                    req.answered = "refused"
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    self.connection.close()
                    return None
                if rule.fail == "silence":
                    req.answered = "silence"
                    time.sleep(rule.status)                    # (the seconds are kept in .status)
                    return None
                status, headers, payload = outer._made(rule, req)
                if rule.fail in ("cut", "cut stream"):
                    req.answered = "cut short"
                    half = payload[:max(1, len(payload) // 2)]
                    self.send_response(status)
                    for k, v in headers.items():
                        self.send_header(k, v)
                    if rule.fail == "cut":
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(half)
                    else:
                        # one piece, and never the piece of length 0 that ends such an answer
                        self.send_header("Transfer-Encoding", "chunked")
                        self.end_headers()
                        self.wfile.write(b"%x\r\n" % len(half) + half + b"\r\n")
                    self.wfile.flush()
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return None
                return self._send(req, status, headers, payload)

            def _send(self, req, status, headers, payload):
                req.answered = status
                try:
                    self.send_response(status)
                    for k, v in headers.items():
                        self.send_header(k, v)
                    if "Content-Length" not in headers:
                        self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(payload)
                except OSError:
                    pass                                       # the engine hung up first: its business

            do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = _serve

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self.url = "http://127.0.0.1:%d/" % self.port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    # ---- what a test says ---------------------------------------------------------------------------------
    def answer(self, match=None, body=None, status=200, headers=None, json=None, file=None, handler=None,
               times=None, delay=0.0, method=None):
        if json is not None:
            body = _json.dumps(json).encode("utf-8")
            headers = dict({"Content-Type": "application/json"}, **(headers or {}))
        elif file is not None:
            with open(file, "rb") as f:
                body = f.read()
        elif isinstance(body, str):
            body = body.encode("utf-8")
        rule = _Rule(match, method, status, headers, body or b"", handler, times, delay, None)
        with self._lock:
            self._rules.append(rule)
        return rule

    def fail(self, match=None, how="refuse", times=None, seconds=30.0, status=200, body=b"x" * 2000, method=None):
        if how not in ("refuse", "cut", "cut stream", "silence"):
            raise ValueError("how must be refuse, cut, cut stream or silence")
        if isinstance(body, str):
            body = body.encode("utf-8")
        rule = _Rule(match, method, seconds if how == "silence" else status, None, body, None, times, 0.0, how)
        with self._lock:
            self._rules.append(rule)
        return rule

    def forget(self):
        """Drops every rule and every request written down: a clean stand-in for the next test."""
        with self._lock:
            del self._rules[:]
            del self.requests[:]
            del self.unanswered[:]

    def asked(self, match=None, method=None):
        """The requests received that fit ``match`` (as in ``answer``), in order."""
        probe = _Rule(match, method, 0, None, b"", None, None, 0.0, None)
        with self._lock:
            return [r for r in self.requests if probe.fits(r)]

    def _made(self, rule, req):
        if rule.handler is None:
            return rule.status, dict(rule.headers), rule.body
        got = rule.handler(req)
        status, headers, body = rule.status, dict(rule.headers), got
        if isinstance(got, tuple) and len(got) == 3:
            status, more, body = got
            headers.update(more or {})
        if isinstance(body, (dict, list)):
            body = _json.dumps(body).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        return status, headers, body or b""

    def close(self):
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        self.close()


class _Guard:
    def __init__(self):
        self.attempts = []                                     # (what was tried, the address)
        self._connect = socket.socket.connect
        self._connect_ex = socket.socket.connect_ex
        self._getaddrinfo = socket.getaddrinfo
        self.on = False

    def _local(self, host):
        return str(host).lower() in _LOCAL or str(host).startswith("127.")

    def start(self):
        guard = self

        def connect(sock, address, *a):
            host = address[0] if isinstance(address, tuple) else address
            if not guard._local(host):
                guard.attempts.append(("connect", address))
                raise InternetReached("a test tried to connect to %r: tests must not reach the internet" % (address,))
            return guard._connect(sock, address, *a)

        def connect_ex(sock, address, *a):
            host = address[0] if isinstance(address, tuple) else address
            if not guard._local(host):
                guard.attempts.append(("connect", address))
                raise InternetReached("a test tried to connect to %r: tests must not reach the internet" % (address,))
            return guard._connect_ex(sock, address, *a)

        def getaddrinfo(host, *a, **k):
            if host is not None and not guard._local(host):
                guard.attempts.append(("look up", host))
                raise InternetReached("a test tried to look up %r: tests must not reach the internet" % (host,))
            return guard._getaddrinfo(host, *a, **k)

        socket.socket.connect = connect
        socket.socket.connect_ex = connect_ex
        socket.getaddrinfo = getaddrinfo
        self.on = True
        return self

    def stop(self):
        socket.socket.connect = self._connect
        socket.socket.connect_ex = self._connect_ex
        socket.getaddrinfo = self._getaddrinfo
        self.on = False

    def __enter__(self):
        return self if self.on else self.start()

    def __exit__(self, *_a):
        self.stop()


def no_internet():
    """Turns the guard on and returns it. From then on, anything in this Python that tries to connect to, or
    look up the address of, another computer raises InternetReached, and the attempt is added to
    ``guard.attempts``. (It guards Python's own networking, which is what the engines use; it does not
    stop ArcGIS's own programs.) ``guard.stop()`` turns it off; it also works as ``with no_internet():``."""
    return _Guard().start()


@contextlib.contextmanager
def pointed(module, **values):
    """Sets attributes of an engine (its web addresses, its pause between requests, the function that reads
    a stored password...) for the length of a ``with`` block, and puts the old values back afterward."""
    missing = [k for k in values if not hasattr(module, k)]
    if missing:
        raise AttributeError("%s has no %s: has the engine been changed?" % (module.__name__, ", ".join(missing)))
    old = dict((k, getattr(module, k)) for k in values)
    try:
        for k, v in values.items():
            setattr(module, k, v)
        yield module
    finally:
        for k, v in old.items():
            setattr(module, k, v)


def page_of(items, offset, limit):
    """A slice of a made-up list of records, for answering a request for one page."""
    offset, limit = int(offset or 0), int(limit or len(items))
    return items[offset:offset + limit]


# ---------------------------------------------------------------------------------------------------------
#  the stand-in tested against Python's own web client
# ---------------------------------------------------------------------------------------------------------
def _self_test():
    import urllib.error
    import urllib.request

    passed = failed = 0

    def check(name, ok, detail=""):
        nonlocal passed, failed
        if ok:
            passed += 1
        else:
            failed += 1
            print("FAIL  %s  %s" % (name, detail))

    def get(url, data=None, headers=None, timeout=10):
        req = urllib.request.Request(url, data=data, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict((k.lower(), v) for k, v in r.headers.items()), r.read()

    def error_of(fn):
        try:
            fn()
            return None
        except Exception as e:                                        # noqa: BLE001
            return e

    guard = no_internet()
    with StandIn() as web:
        check("the stand-in is on this computer", web.url.startswith("http://127.0.0.1:") and web.port > 0, web.url)
        web.answer("/v1/things", json={"count": 2, "results": [{"id": 1}, {"id": 2}]})
        status, headers, body = get(web.url + "v1/things?limit=20&name=Ursus%20americanus&name=Puma",
                                    headers={"User-Agent": "a test"})
        check("an answer given as a Python value arrives as JSON",
              status == 200 and _json.loads(body) == {"count": 2, "results": [{"id": 1}, {"id": 2}]}
              and headers.get("content-type") == "application/json", (status, headers, body))
        r = web.requests[-1]
        check("the request is written down: method, path, each parameter, the headers",
              (r.method, r.path, r.one("limit"), r.query["name"], r.headers.get("user-agent"), r.answered)
              == ("GET", "/v1/things", "20", ["Ursus americanus", "Puma"], "a test", 200), r)
        e = error_of(lambda: get(web.url + "v1/other"))
        check("a request nothing was prepared for gets 404 and is listed as unanswered",
              isinstance(e, urllib.error.HTTPError) and e.code == 404 and [u.path for u in web.unanswered] == ["/v1/other"],
              (e, web.unanswered))

        web.forget()
        check("forget: a clean stand-in", web.requests == [] and web.unanswered == [])
        web.answer("/flaky", status=503, body="busy", times=2)
        web.answer("/flaky", body="done")
        got = []
        for _ in range(3):
            e = error_of(lambda: got.append(get(web.url + "flaky")[2]))
            if e is not None:
                got.append(getattr(e, "code", repr(e)))
        check("a rule for the first two requests, then another: fails twice and then works", got == [503, 503, b"done"], got)

        web.forget()
        items = [{"n": i} for i in range(7)]
        web.answer("/paged", handler=lambda q: {"total": len(items), "results": page_of(items, q.one("offset"), q.one("limit"))})
        pages = [_json.loads(get(web.url + "paged?offset=%d&limit=3" % o)[2])["results"] for o in (0, 3, 6, 9)]
        check("an answer worked out from the request: pages of 3, 3, 1 and none",
              [len(p) for p in pages] == [3, 3, 1, 0] and pages[2] == [{"n": 6}], pages)
        web.answer(re.compile(r"/item/\d+$"), handler=lambda q: (201, {"X-Made": "yes"}, "made " + q.path.rsplit("/", 1)[1]))
        status, headers, body = get(web.url + "item/42")
        check("a rule chosen by a pattern, answering with a status, a header and text",
              (status, headers.get("x-made"), body) == (201, "yes", b"made 42"), (status, headers, body))
        web.answer(lambda q: q.method == "POST", handler=lambda q: {"got": q.form(), "type": q.headers.get("content-type")})
        status, headers, body = get(web.url + "submit", data=b"a=1&b=two+words",
                                    headers={"Content-Type": "application/x-www-form-urlencoded"})
        check("a rule chosen by a function, reading a posted form",
              _json.loads(body) == {"got": {"a": ["1"], "b": ["two words"]}, "type": "application/x-www-form-urlencoded"}, body)
        check("asked(): the requests that fit, in order",
              [q.one("offset") for q in web.asked("/paged")] == ["0", "3", "6", "9"] and len(web.asked(method="POST")) == 1)

        web.forget()
        web.fail("/refuse", "refuse")
        e = error_of(lambda: get(web.url + "refuse"))
        check("refuse: the connection is dropped with no answer", e is not None and not isinstance(e, urllib.error.HTTPError), repr(e))
        web.fail("/cut", "cut", body=b"0123456789" * 200)
        e = error_of(lambda: get(web.url + "cut"))
        short = None
        if e is None:
            short = len(get(web.url + "cut")[2])
        check("cut: the answer stops halfway (an error, or fewer bytes than were promised)",
              e is not None or (short is not None and short < 2000), (repr(e), short))
        web.fail("/stream", "cut stream", body=b"0123456789" * 200)
        e = error_of(lambda: get(web.url + "stream"))
        check("cut stream: an answer sent in pieces, with no length promised, stops halfway (an error)",
              e is not None and not isinstance(e, urllib.error.HTTPError), repr(e))
        web.fail("/silence", "silence", seconds=3.0)
        t0 = time.monotonic()
        e = error_of(lambda: get(web.url + "silence", timeout=0.5))
        check("silence: nothing comes, and the caller's own time-out ends the wait",
              e is not None and time.monotonic() - t0 < 2.5, (repr(e), time.monotonic() - t0))
        web.answer("/slow", body="late", delay=0.3)
        t0 = time.monotonic()
        check("delay: the answer comes after the wait", get(web.url + "slow")[2] == b"late" and time.monotonic() - t0 >= 0.3)
        web.answer("/page", body="<html><body>Service unavailable</body></html>", headers={"Content-Type": "text/html"})
        check("a web page in place of data can be served as easily", b"<html>" in get(web.url + "page")[2])

    # the guard
    e = error_of(lambda: get("http://example.invalid/anything", timeout=2))
    check("the guard: a request to another computer is stopped before it leaves, and written down",
          e is not None and "must not reach the internet" in str(getattr(e, "reason", e)) and len(guard.attempts) == 1
          and guard.attempts[0][1] == "example.invalid", (repr(e), guard.attempts))
    e = error_of(lambda: socket.create_connection(("192.0.2.1", 80), timeout=1))
    check("the guard: so is a connection to a bare address", isinstance(e, InternetReached) and len(guard.attempts) == 2,
          (repr(e), guard.attempts))
    guard.stop()
    check("the guard can be turned off again", socket.getaddrinfo is guard._getaddrinfo and socket.socket.connect is guard._connect)

    class Engine:
        API = "https://real.example/api/"
        PAUSE = 1.0

    with pointed(Engine, API="http://127.0.0.1:1/", PAUSE=0.0):
        inside = (Engine.API, Engine.PAUSE)
    check("pointed: an engine's settings are changed inside the block and put back after it",
          inside == ("http://127.0.0.1:1/", 0.0) and (Engine.API, Engine.PAUSE) == ("https://real.example/api/", 1.0))
    e = error_of(lambda: pointed(Engine, NO_SUCH_NAME=1).__enter__())
    check("pointed: a name the engine does not have is an error, so a renamed setting cannot be missed",
          isinstance(e, AttributeError) and "NO_SUCH_NAME" in str(e), repr(e))

    print("\n%d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_self_test())
