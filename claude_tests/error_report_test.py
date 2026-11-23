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

"""The shared error report (error_report.py): what the report says, where it
is saved, what the tool messages show, and that making a report can never
itself stop a tool.

Run with Pro's Python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" error_report_test.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "WildlifeTools", "Toolboxes"))
import arcpy  # noqa: E402
import error_report as E  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print("FAIL  %s  %s" % (name, detail))


class Msgs:
    def __init__(self):
        self.errors = []

    def addErrorMessage(self, s):
        self.errors.append(s)


class BrokenMsgs:
    def addErrorMessage(self, s):
        raise RuntimeError("the message window is gone")


class Param:
    def __init__(self, name, value):
        self.name, self._value = name, value

    @property
    def valueAsText(self):
        if isinstance(self._value, Exception):
            raise self._value
        return self._value


def deepest_function_of_the_test():
    raise ValueError("the cell size is zero")


made = []          # report files this suite wrote, removed at the end

# ---- 1. what the report says
try:
    deepest_function_of_the_test()
except ValueError:
    text = E.build_report("Slope Zonal Statistics", [Param("in_raster", r"D:\data\dem.tif"),
                                                     Param("cell_size", None),
                                                     Param("zones", OSError("layer was removed"))])
lines = text.splitlines()
check("report: names the tool", "Tool:          Slope Zonal Statistics" in lines, lines[:8])
check("report: names the kind of error", "Error type:    ValueError" in lines, text)
check("report: gives the error's own words", "Error message: the cell size is zero" in lines, text)
check("report: shows where in the code it happened, down to the function that failed",
      "deepest_function_of_the_test" in text and "error_report_test.py" in text
      and 'raise ValueError("the cell size is zero")' in text, text)
check("report: lists each parameter with its value", r"    in_raster = D:\data\dem.tif" in lines, text)
check("report: a parameter with no value is still listed", "    cell_size = None" in lines, text)
check("report: a parameter that cannot be read does not stop the report",
      "    <parameter> = <unavailable>" in lines, text)
check("report: says where to send it", any(E.SUPPORT_EMAIL in ln and "email" in ln.lower() for ln in lines[:4]),
      lines[:4])
check("report: gives the ArcGIS Pro and Python versions",
      any(ln.startswith("ArcGIS Pro:    ") and ln.split(":", 1)[1].strip()
          == arcpy.GetInstallInfo().get("Version") for ln in lines)
      and "Python:        %s" % sys.version.split()[0] in lines, text)
check("report: is dated", any(ln.startswith("Date / time:   20") and len(ln) == len("Date / time:   2026-11-23 10:00:00")
                              for ln in lines), text)

try:
    raise KeyError("x")
except KeyError:
    none_text = E.build_report("A tool")
check("report: a tool without parameters says so", "    (none)" in none_text.splitlines(), none_text)

# an error that ArcGIS's own tools reported is quoted as well
try:
    arcpy.management.GetCount(r"C:\no_such_folder_for_the_test\nothing.shp")
    check("setup: counting a missing dataset fails", False)
except arcpy.ExecuteError:
    gp_text = E.build_report("A tool")
check("report: quotes the messages of the ArcGIS tool that failed",
      "Geoprocessing messages:" in gp_text and "000732" in gp_text.split("Geoprocessing messages:", 1)[1], gp_text)
check("report: an ArcGIS tool error is named as one", "Error type:    ExecuteError" in gp_text, gp_text)

# ---- 2. saving it and showing it
m = Msgs()
try:
    deepest_function_of_the_test()
except ValueError:
    path = E.report(m, "Slope Zonal Statistics", [Param("in_raster", "dem.tif")])
made.append(path)
check("saved: the report file is written to the temporary folder",
      bool(path) and os.path.isfile(path) and os.path.dirname(path) == tempfile.gettempdir(), path)
name = os.path.basename(path or "")
check("saved: the file is named for the tool and the time",
      name.startswith("JennessTools_Error_Slope_Zonal_Statistics_20") and name.endswith(".txt"), name)
saved = open(path, encoding="utf-8").read() if path and os.path.isfile(path) else ""
check("saved: the file holds the same report the messages show", len(m.errors) == 2 and saved == m.errors[1],
      (len(m.errors), saved[:80]))
check("shown: the first message says what happened, where to send the report and where the copy is",
      len(m.errors) == 2 and "unexpected error" in m.errors[0] and E.SUPPORT_EMAIL in m.errors[0]
      and str(path) in m.errors[0], m.errors[:1])
check("shown: the report itself follows", len(m.errors) == 2 and "Error message: the cell size is zero" in m.errors[1],
      m.errors[1:2])

# a tool name with characters a file name cannot hold, and a very long one
try:
    raise RuntimeError("x")
except RuntimeError:
    odd = E.report(Msgs(), 'TPI: 3x3 "large" <test>/\\*?|' + "n" * 80)
made.append(odd)
odd_name = os.path.basename(odd or "")
stem = odd_name[len("JennessTools_Error_"):-len("_20261123_100000.txt")]
check("saved: a tool name with characters a file name cannot hold still gives a file",
      bool(odd) and os.path.isfile(odd) and not any(c in odd_name for c in '<>:"/\\|?* '), odd_name)
check("saved: a long tool name is cut to 40 letters in the file name", len(stem) == 40, (len(stem), stem))

# ---- 3. it can never itself stop a tool
try:
    raise RuntimeError("x")
except RuntimeError:
    try:
        p = E.report(BrokenMsgs(), "A tool")
        made.append(p)
        check("never raises: a message window that fails does not stop it", bool(p) and os.path.isfile(p), p)
    except Exception as e:                                            # noqa: BLE001
        check("never raises: a message window that fails does not stop it", False, repr(e))
    try:
        p = E.report(None, "A tool")
        made.append(p)
        check("never raises: with no message window at all the file is still written", bool(p) and os.path.isfile(p), p)
    except Exception as e:                                            # noqa: BLE001
        check("never raises: with no message window at all the file is still written", False, repr(e))

# no place to save the file: the report is still shown, and says it was not saved
real_tmp = tempfile.gettempdir
tempfile.gettempdir = lambda: os.path.join(real_tmp(), "no_such_folder_for_the_error_report_test", "deeper")
m = Msgs()
try:
    try:
        raise RuntimeError("the disk is full")
    except RuntimeError:
        p = E.report(m, "A tool")
finally:
    tempfile.gettempdir = real_tmp
check("unsaved: no path is returned when the file cannot be written", p is None, p)
check("unsaved: the messages say that the report could not be saved, and to copy it",
      len(m.errors) == 2 and "could not be saved to a file" in m.errors[0] and "copy it from here" in m.errors[0]
      and "the disk is full" in m.errors[1], m.errors[:1])

# called when no error is being handled (a mistake in a tool): still no exception
try:
    m = Msgs()
    p = E.report(m, "A tool")
    made.append(p)
    check("never raises: called with no error at hand, it still reports",
          len(m.errors) == 2 and "Error type:    unknown" in m.errors[1], m.errors[1:2])
except Exception as e:                                                # noqa: BLE001
    check("never raises: called with no error at hand, it still reports", False, repr(e))

for f in made:
    try:
        if f:
            os.remove(f)
    except OSError:
        pass

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
