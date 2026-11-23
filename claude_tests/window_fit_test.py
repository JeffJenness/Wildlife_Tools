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

"""Every window the add-in makes must fit a laptop screen (Jeff Jenness,
23 November 2026). This is a search of the add-in's own source, and a guard
for windows yet to be written. It checks that:

  1. every window written in XAML calls WindowFit.Apply from its constructor;
  2. every window built in code (new Window, new ProWindow) is handed to
     WindowFit.Apply before it is shown;
  3. a window that tells WindowFit it scrolls its own content really has a
     scroll box of its own;
  4. no window's smallest size is larger than a laptop gives, where shrinking
     the window could no longer help.

What WindowFit then does with a window is tested in claude_tests/WindowFitTest.

The laptop: a 1366 by 768 screen, or 1920 by 1080 at 150 percent, leaves a
work area of about 1280 by 672 without the taskbar. With WindowFit's margin
of 16 a window may be 1264 by 656 there.

Needs no ArcGIS. Run with any Python 3:
  python window_fit_test.py            (add "list" to print every window)
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ADDIN = os.path.normpath(os.path.join(HERE, "..", "WildlifeTools"))
LAPTOP_W, LAPTOP_H = 1280.0 - 16.0, 672.0 - 16.0
# A smallest height a little above the laptop's is allowed: WindowFit lowers it, and the layouts have a row
# that gives way. The Histogram window asks for 660.
SLACK = 8.0

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print("FAIL  %s  %s" % (name, detail))


def read(path):
    return io.open(path, encoding="utf-8-sig", errors="replace").read()


def source_files(ext):
    for folder, dirs, files in os.walk(ADDIN):
        dirs[:] = [d for d in dirs if d not in ("obj", "bin")]
        for n in sorted(files):
            if n.endswith(ext):
                yield os.path.join(folder, n)


def rel(path):
    return os.path.relpath(path, ADDIN).replace("\\", "/")


ROOT = re.compile(r"\s*(?:<\?xml[^>]*\?>\s*)?<((?:\w+:)?(?:ProWindow|Window))\b([^>]*)>", re.S)


def attribute(head, name):
    m = re.search(r'(?<![\w.:])%s="([^"]*)"' % name, head)
    return m.group(1) if m else None


def number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


# ---- 1, 3 and 4: the windows written in XAML ------------------------------------------------------------
windows = []
for xaml in source_files(".xaml"):
    text = read(xaml)
    m = ROOT.match(text)
    if m:
        windows.append((xaml, text, m.group(2)))
check("the add-in's windows were found (41 on 23 November 2026; more are welcome)", len(windows) >= 41, len(windows))

no_call, no_code, own_scroll_missing, too_big = [], [], [], []
table = []
for xaml, text, head in windows:
    code_path = xaml + ".cs"
    if not os.path.isfile(code_path):
        no_code.append(rel(xaml))
        continue
    code = read(code_path)
    call = re.search(r"\bWindowFit\.Apply\(this\b([^;]*)\);", code)
    if not call:
        no_call.append(rel(xaml))
        continue
    scrolls_itself = "scrollContent: false" in call.group(1)
    if scrolls_itself and "<ScrollViewer" not in text:
        own_scroll_missing.append(rel(xaml))
    min_w, min_h = number(attribute(head, "MinWidth")), number(attribute(head, "MinHeight"))
    if (min_w is not None and min_w > LAPTOP_W) or (min_h is not None and min_h > LAPTOP_H + SLACK):
        too_big.append("%s (smallest %s by %s)" % (rel(xaml), attribute(head, "MinWidth"), attribute(head, "MinHeight")))
    w, h = number(attribute(head, "Width")), number(attribute(head, "Height"))
    table.append((rel(xaml), attribute(head, "Width") or "content", attribute(head, "Height") or "content",
                  "%g" % min(w, LAPTOP_W) if w is not None else "content",
                  "%g" % min(h, LAPTOP_H) if h is not None else "content, at most %g" % LAPTOP_H,
                  "scrolls its own text" if scrolls_itself else ""))

check("every XAML window has its code file beside it", not no_code, no_code)
check("every XAML window calls WindowFit.Apply(this) from its code", not no_call,
      "these do not, so they can open larger than a laptop's screen: %s" % no_call)
check("a window that says it scrolls its own content (scrollContent: false) has a scroll box in its XAML",
      not own_scroll_missing, own_scroll_missing)
check("no window's smallest size is beyond a laptop's work area (%g by %g, with %g to spare in height)"
      % (LAPTOP_W, LAPTOP_H, SLACK), not too_big, too_big)

# the call comes after InitializeComponent, when the window's content exists
late = []
for xaml, text, head in windows:
    code = read(xaml + ".cs") if os.path.isfile(xaml + ".cs") else ""
    a, b = code.find("InitializeComponent();"), code.find("WindowFit.Apply(this")
    if a < 0 or b < 0 or b < a:
        late.append(rel(xaml))
check("in every XAML window the call comes after InitializeComponent()", not late, late)

# ---- 2: the windows built in code -----------------------------------------------------------------------
NEW = re.compile(r"(?:(?:var|Window|ProWindow)\s+(\w+)\s*=\s*)?new\s+(?:[\w.]+\.)?(?:ProWindow|Window)\s*(?:\(\s*\))?\s*(?=\{|;)")
built, unfitted = 0, []
for cs in source_files(".cs"):
    code = read(cs)
    for m in NEW.finditer(code):
        line = code.count("\n", 0, m.start()) + 1
        built += 1
        name = m.group(1)
        if not name:
            unfitted.append("%s:%d (the window has no name, so it cannot be handed to WindowFit before it is shown)" % (rel(cs), line))
            continue
        rest = code[m.end():]
        shown = re.search(r"\b%s\.Show(?:Dialog)?\(\)" % re.escape(name), rest)
        fitted = re.search(r"\bWindowFit\.Apply\(%s\b" % re.escape(name), rest)
        if not shown:
            continue                                   # made and handed elsewhere: not shown here
        if not fitted or fitted.start() > shown.start():
            unfitted.append("%s:%d (%s)" % (rel(cs), line, name))
check("the windows built in code were found (9 on 23 November 2026)", built >= 9, built)
check("every window built in code is handed to WindowFit.Apply before it is shown", not unfitted, unfitted)

# ---- the helper itself ----------------------------------------------------------------------------------
helper = os.path.join(ADDIN, "WindowFit.cs")
check("WindowFit.cs is there", os.path.isfile(helper), helper)
old = [rel(p) for p in list(source_files(".cs")) + list(source_files(".xaml")) if "OnlineWindowFit" in read(p)]
check("nothing still names the earlier helper, OnlineWindowFit", not old, old)

if len(sys.argv) > 1 and sys.argv[1] == "list":
    print("%-56s %8s %8s   %s" % ("window", "asks for", "", "opens on a laptop at"))
    for name, w, h, lw, lh, note in sorted(table):
        print("%-56s %8s %8s   %s by %s  %s" % (name, w, h, lw, lh, note))
    print("%d XAML windows, %d built in code" % (len(table), built))

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
