# Wildlife and Forestry Tools for ArcGIS Pro
# Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version, together with the additional terms
# in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
# Memorial Scholarship Fund notice, marking of modified versions, and
# use of names).
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Hidden helper for the Land Facet Clustering tool -- NOT a geoprocessing tool.

Stamps the land facet linkage record onto a finished raster so the later tools
in the chain (Land Facet Density, then Mahalanobis) can tell exactly what the
clustering run produced: the analysis identifier, the ORDERED variable list, and
which cell value is which category/cluster.

Why a subprocess instead of writing the metadata from C#: the record format has
exactly one implementation, ``Toolboxes/land_facet_linkage.py``, and it is the
same code the density tool reads back with. Reimplementing the XML editing on
the C# side would create two definitions of one format that could drift apart
silently. Writing the metadata needs ``arcpy.metadata`` (the only carrier that
survives CopyRaster into a file geodatabase -- GDAL-side metadata does not), and
arcpy is only available in Pro's Python, so this runs there.

Reads a JSON job file:  {"target": "<raster path>", "record": {...}}
Prints ``OK <target>`` on success, or ``ERROR <message>``, and exits non-zero on
failure. Stamping is non-fatal to the caller: a raster without a record still
works, the downstream tools just fall back to asking the user.
"""
import json
import os
import sys


def main(argv):
    if len(argv) < 2:
        print("ERROR usage: corridor_write_metadata.py <job.json>")
        return 2
    try:
        with open(argv[1], "r", encoding="utf-8") as fh:
            job = json.load(fh)
    except Exception as exc:                                     # noqa: BLE001
        print("ERROR could not read the job file: {}".format(exc))
        return 2

    target = job.get("target")
    record = job.get("record")
    if not target or not isinstance(record, dict):
        print("ERROR the job must supply 'target' and 'record'.")
        return 2

    # land_facet_linkage lives with the Python toolbox, which the add-in
    # deploys as <assembly dir>\Toolboxes\ while this helper sits in
    # <assembly dir>\Corridor\.
    here = os.path.dirname(os.path.abspath(__file__))
    toolboxes = os.path.join(os.path.dirname(here), "Toolboxes")
    if toolboxes not in sys.path:
        sys.path.insert(0, toolboxes)

    try:
        import land_facet_linkage as lnk
    except Exception as exc:                                     # noqa: BLE001
        print("ERROR could not import land_facet_linkage from {}: {}".format(
            toolboxes, exc))
        return 3

    if not lnk.write(target, record):
        print("ERROR the linkage record could not be written to " + str(target))
        return 4

    # Read it straight back: a write that silently produced nothing readable is
    # the failure mode that would matter downstream, so prove the round trip
    # rather than trusting the write call's return value.
    back = lnk.read(target, record.get("record"))
    if back is None:
        print("ERROR the linkage record did not read back from " + str(target))
        return 5

    print("OK " + str(target))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
