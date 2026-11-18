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

"""Load Burn Severity Bundle: the data bundles of the Burn Severity
Portal (MTBS, RAVG and BAER), as they arrive by email.

A bundle is a zip file. Inside it each fire has a folder of files named

    <program>_<fire id>_<map id>_<pre-fire date>_<post-fire date>_<product>.<ext>

with the fire's perimeter as a shapefile and its rasters as GeoTIFFs in
the fire's own projection, with true 30-meter cells: the continuous
indices (dNBR, RdNBR and, for RAVG, the modelled losses) and the
classified maps. The classified rasters come with a color table and no
attribute table, so their values have no names.

This engine

  * unpacks a zip (or reads a folder already unpacked),
  * writes every fire's perimeter to one feature class,
  * copies every raster to the output workspace, and gives every
    classified raster an attribute table with the NAME of each class,
  * summarizes each fire in a table: the area in each class, measured
    from the true cells, and the mean, standard deviation and range of
    each continuous index inside the burned area.

Only the Python standard library, arcpy, numpy and GDAL are used.
"""

import datetime
import os
import re
import shutil
import tarfile
import zipfile

import burn_severity as B
import gbif_occurrences as G

n0 = G.n0
Cancelled = G.Cancelled
BundleError = B.FireError

ACRE_M2 = 4046.8564224
FOLDER = "Burn_Severity_Bundles"

# product token of a file name -> (what it is, kind, classes for a classified raster, long name)
PRODUCTS = {
    "dnbr": ("continuous", None, "dNBR: differenced Normalized Burn Ratio (pre-fire NBR minus post-fire NBR, times 1000)"),
    "rdnbr": ("continuous", None, "RdNBR: Relative differenced Normalized Burn Ratio (dNBR divided by the square root of the absolute pre-fire NBR)"),
    "dnbr6": ("classified", B.MTBS_CLASSES, "MTBS thematic burn severity, 6 classes"),
    "ba": ("continuous", None, "RAVG percent basal area loss, modelled (0 to 100)"),
    "cc": ("continuous", None, "RAVG percent canopy cover loss, modelled (0 to 100)"),
    "cbi": ("continuous", None, "RAVG composite burn index, modelled (0 to 3)"),
    "ba7": ("classified", B.BA7_CLASSES, "RAVG percent basal area loss, 7 classes"),
    "cc5": ("classified", B.CC5_CLASSES, "RAVG percent canopy cover loss, 5 classes"),
    "cbi4": ("classified", B.CBI4_CLASSES, "RAVG composite burn index, 4 classes"),
    "sbs": ("classified", B.SBS_CLASSES, "BAER soil burn severity"),
    "barc4": ("classified", B.SBS_CLASSES, "BAER Burned Area Reflectance Classification (BARC), 4 classes"),
    "barc": ("classified", B.SBS_CLASSES, "BAER Burned Area Reflectance Classification (BARC), 4 classes"),
    "barc256": ("continuous", None, "BAER Burned Area Reflectance Classification (BARC), 256 values"),
}
EVENT = re.compile(r"^[a-z]{2}\d{13,}$", re.I)
OLD_SKIP = ("utm", "alb", "albers", "30m")             # words of an older file name that say nothing of the product
OLD_PRODUCTS = {"barc4": "barc4", "barc": "barc", "barc256": "barc256", "barca": "barca", "dnbr": "dnbr",
                "pre": "pre", "prefire": "pre", "post": "post"}
NAME = re.compile(r"^(?P<prog>[a-z]+)_(?P<event>[a-z]{2}\d{13,})_(?P<map>\d+)_(?P<rest>.+)$", re.I)

SUMMARY_FIELDS = [
    ("program", "Program", "TEXT", 20, "The mapping program: MTBS, RAVG or BAER."),
    ("fire_name", "Fire name", "TEXT", 120, "The name of the fire, from the bundle's perimeter."),
    ("fire_id", "Fire ID", "TEXT", 40, "The program's identifier of the fire."),
    ("fire_year", "Year", "LONG", None, "The year of the ignition date."),
    ("product", "Raster", "TEXT", 40, "The raster this row summarizes: the product token of its file name."),
    ("kind", "Kind", "TEXT", 12, "classified or continuous."),
    ("class_value", "Class value", "LONG", None, "For a classified raster, the cell value of the class of this row."),
    ("class_name", "Class name", "TEXT", 60, "For a classified raster, the name of the class of this row."),
    ("cells", "Cells", "LONG", None, "The number of cells: in the class, or with a value inside the burned area."),
    ("hectares", "Hectares", "DOUBLE", None, "The area of those cells in hectares, from the raster's true cell size."),
    ("acres", "Acres", "DOUBLE", None, "The area of those cells in acres."),
    ("percent", "Percent", "DOUBLE", None,
     "For a classified raster, the class's share of the cells the program assessed. Cells outside the fire, "
     "cells without data and the program's mask classes are not counted in the total; a mask class has no percent."),
    ("mean", "Mean", "DOUBLE", None, "For a continuous raster, the mean of its values."),
    ("std_dev", "Standard deviation", "DOUBLE", None, "For a continuous raster, the standard deviation of its values."),
    ("minimum", "Minimum", "DOUBLE", None, "For a continuous raster, its smallest value."),
    ("median", "Median", "DOUBLE", None, "For a continuous raster, its median value."),
    ("maximum", "Maximum", "DOUBLE", None, "For a continuous raster, its largest value."),
    ("raster", "Raster path", "TEXT", 255, "The copy of the raster this tool wrote."),
]


def unpack(source, home):
    """The folder that holds a bundle's files: the folder itself, or a
    zip unpacked beside the output (never above it: entries with .. or a
    drive are refused)."""
    src = str(source or "").strip().strip('"')
    if os.path.isdir(src):
        return src, False
    if not os.path.isfile(src):
        raise BundleError("There is no file or folder at {}.".format(src))
    if not zipfile.is_zipfile(src):
        raise BundleError("{} is not a zip file. Give the zip file that the Burn Severity Portal's email "
                          "links to, or the folder it was unzipped into.".format(src))
    folder = os.path.join(home, FOLDER, os.path.splitext(os.path.basename(src))[0])
    os.makedirs(folder, exist_ok=True)
    root = os.path.abspath(folder)
    with zipfile.ZipFile(src) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            target = os.path.abspath(os.path.join(root, info.filename))
            if not target.startswith(root + os.sep) or ".." in info.filename.replace("\\", "/").split("/"):
                raise BundleError("The zip file holds an entry outside its own folder ({}), so it was not "
                                  "unpacked.".format(info.filename))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.isfile(target) and os.path.getsize(target) == info.file_size:
                continue
            with z.open(info) as srcf, open(target, "wb") as dst:
                while True:
                    chunk = srcf.read(1 << 20)
                    if not chunk:
                        break
                    dst.write(chunk)
    return folder, True


def find_fires(folder):
    """{(program, fire id, map id): {"perimeter": path, "rasters":
    {product: path}, "pre": date, "post": date}} for the bundle files
    under a folder."""
    fires = {}
    for base, _dirs, files in os.walk(folder):
        for f in files:
            stem, ext = os.path.splitext(f)
            ext = ext.lower()
            if ext not in (".tif", ".shp"):
                continue
            m = NAME.match(stem)
            if not m:
                continue
            tokens = m.group("rest").split("_")
            dates = [t for t in tokens if re.match(r"^\d{8}$", t)]
            product = "_".join(t for t in tokens if not re.match(r"^\d{8}$", t)).lower()
            key = (m.group("prog").upper(), m.group("event").upper(), m.group("map"))
            d = fires.setdefault(key, {"perimeter": None, "rasters": {}, "pre": None, "post": None})
            if dates:
                d["pre"] = dates[0] if len(dates) > 1 else d["pre"]
                d["post"] = dates[-1]
            if ext == ".shp":
                if "burn" in product or "bndy" in product or "perim" in product or d["perimeter"] is None:
                    d["perimeter"] = os.path.join(base, f)
            else:
                d.setdefault("found", []).append((product, dates[-1] if dates else "", os.path.join(base, f)))
    for d in fires.values():
        found = d.pop("found", [])
        # The satellite images (..._<date>_<sensor>_refl) carry one date each, and it is all that tells
        # the image before the fire from the one after: their names keep it, and say which is which.
        images = sorted((i for i in found if i[0].split("_")[-1] == "refl"), key=lambda i: (i[1], i[0]))
        two = len(images) == 2 and images[0][1] and images[0][1] != images[1][1]
        for n, (product, date, path) in enumerate(images):
            label = "_".join(w for w in (("pre", "post")[n] if two else "", date, product) if w)
            k = 2
            while label in d["rasters"]:
                label = "{}_{}_{}".format(k, date, product)
                k += 1
            d["rasters"][label] = path
        rest = [i for i in found if i[0].split("_")[-1] != "refl"]
        for product in sorted(set(i[0] for i in rest)):
            same = sorted(i for i in rest if i[0] == product)
            if len(same) == 1:
                d["rasters"][product] = same[0][2]
            else:
                for n, (_p, date, path) in enumerate(same, 1):
                    d["rasters"]["{}_{}".format(date or n, product)] = path
    return fires


def untar(path):
    """Unpack a tar archive of an older bundle into a folder beside it:
    plain files only, each by its own name (no folders, no links)."""
    out = os.path.splitext(path)[0] + "_unpacked"
    try:
        with tarfile.open(path) as t:
            for m in t.getmembers():
                name = os.path.basename(m.name.replace("\\", "/"))
                if not m.isfile() or not name or name.startswith("."):
                    continue
                os.makedirs(out, exist_ok=True)
                target = os.path.join(out, name)
                if os.path.isfile(target) and os.path.getsize(target) == m.size:
                    continue
                src = t.extractfile(m)
                with open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
    except (tarfile.TarError, OSError, EOFError):
        return None
    return out if os.path.isdir(out) else None


def find_older(folder, fires):
    """Add to ``fires`` the fires of a bundle that came in the portal's
    older form (BAER fires before 2017, in the bundles seen): a folder
    named for the fire's id, holding rasters in ERDAS Imagine format, or a
    tar archive of them, with names that differ from fire to fire, and no
    perimeter. Every raster in such a folder is taken. Returns the keys
    added."""
    from osgeo import gdal

    gdal.UseExceptions()

    def event_of(base):
        parts = os.path.relpath(base, folder).replace("/", "\\").split("\\")
        ev = [p for p in parts if EVENT.match(p)]
        if not ev:
            return None, None
        prog = [p.upper() for p in parts if p.upper() in ("MTBS", "RAVG", "BAER")]
        return (prog[0] if prog else "BAER"), ev[-1].upper()

    taken = set()
    for d in fires.values():
        for p in [d.get("perimeter")] + list(d["rasters"].values()):
            if p:
                taken.add(os.path.normcase(os.path.dirname(p)))
    for base, _dirs, files in list(os.walk(folder)):
        if os.path.normcase(base) not in taken and event_of(base)[1]:
            for f in files:
                if f.lower().endswith(".tar"):
                    untar(os.path.join(base, f))
    found = {}                                 # key -> [(name words, product, date, path)]
    dirs = {}
    for base, _dirs, files in os.walk(folder):
        program, event = event_of(base)
        if not event or os.path.normcase(base) in taken:
            continue
        key = (program, event, "")
        for f in sorted(files):
            stem, ext = os.path.splitext(f)
            ext = ext.lower()
            if ext == ".tar":
                dirs.setdefault(key, set()).add(base)
            if ext not in (".img", ".tif") or NAME.match(stem):
                continue
            path = os.path.join(base, f)
            try:
                ds = gdal.Open(path)
                bands = ds.RasterCount
                ds = None
            except Exception:                                    # noqa: BLE001
                continue
            words = [w for w in re.split(r"[_\-\s]+", stem.lower()) if w]
            dates = [w for w in words if re.match(r"^\d{8}$", w)]
            words = [w for w in words if w not in OLD_SKIP and not re.match(r"^\d{8}$", w)]
            known = [w for w in words if w in OLD_PRODUCTS]
            product = OLD_PRODUCTS[known[-1]] if known else ("image" if bands > 1 else "raster")
            found.setdefault(key, []).append((tuple(w for w in words if w not in OLD_PRODUCTS), product,
                                              dates[-1] if dates else "", path))
            dirs.setdefault(key, set()).add(base)
    added = []
    for key, items in found.items():
        several = len(set(i[0] for i in items)) > 1        # more than one fire name in the folder
        d = {"perimeter": None, "rasters": {}, "pre": None, "post": None, "older": True,
             "dirs": sorted(dirs.get(key, ()))}
        for words, product, date, path in sorted(items, key=lambda i: i[3].lower()):
            label = "_".join(list(words) + [product]) if several and words else product
            if date and (product in ("pre", "post", "image") or label in d["rasters"]):
                label = "{}_{}".format(label, date) if product in ("pre", "post", "image") else "{}_{}".format(
                    date, label)                                  # an image's name keeps the date it was taken
            k = 2
            base_label = label
            while label in d["rasters"]:
                label = "{}_{}".format(k, base_label)          # the product word stays last: it tells the kind
                k += 1
            d["rasters"][label] = path
        fires[key] = d
        added.append(key)
    return added


def catalog_perimeter(feature, arcpy):
    """(attributes named as a bundle's perimeter shapefile names them,
    polygon in WGS 84) of a feature of the portal's fire catalog."""
    props = feature.get("properties") or {}
    attrs = dict((str(k).upper(), v) for k, v in props.items())
    attrs["ASMNT_TYPE"] = props.get("asmt_type")
    attrs["MOD_T"], attrs["HIGH_T"] = props.get("high_t"), props.get("mod_t")     # crosswise: see B.FIELDS
    shape = None
    try:
        g = arcpy.AsShape(feature.get("geometry"))
        shape = arcpy.FromWKB(bytes(g.WKB), arcpy.SpatialReference(4326))
    except Exception:                                            # noqa: BLE001
        shape = None
    return attrs, shape


def find_unread(folder, fires):
    """[(folder relative to the bundle, {extension: count})] for the folders
    of a bundle that hold files and none this tool reads. The portal sends
    some older assessments (BAER fires before 2017, in the bundles seen) in
    an older form: ERDAS Imagine rasters, tar archives, free file names and
    no perimeter. Those are reported, never guessed at."""
    read = set()
    for d in fires.values():
        for p in [d.get("perimeter")] + list(d["rasters"].values()):
            if p:
                read.add(os.path.normcase(os.path.dirname(p)))
        for p in d.get("dirs") or ():
            read.add(os.path.normcase(p))
    out = []
    for base, _dirs, files in os.walk(folder):
        files = [f for f in files if not f.startswith(".")]
        if not files or os.path.normcase(base) in read:
            continue
        exts = {}
        for f in files:
            e = (os.path.splitext(f)[1].lower() or "(no extension)")
            exts[e] = exts.get(e, 0) + 1
        out.append((os.path.relpath(base, folder), exts))
    return sorted(out)


def describe_raster(path):
    """(kind, classes or None, long name, cell area in square meters,
    values array, nodata) of a bundle raster."""
    import numpy as np
    from osgeo import gdal

    gdal.UseExceptions()
    ds = gdal.Open(path)
    band = ds.GetRasterBand(1)
    a = band.ReadAsArray()
    gt = ds.GetGeoTransform()
    sr = ds.GetSpatialRef()
    unit = sr.GetLinearUnits() if sr is not None and sr.IsProjected() else None
    cell_m2 = abs(gt[1] * gt[5]) * unit * unit if unit else None
    nodata = band.GetNoDataValue()
    is_byte = gdal.GetDataTypeName(band.DataType) == "Byte"
    bands = ds.RasterCount
    ds = None
    return a, nodata, cell_m2, is_byte, np, bands


def classify_product(product, a, nodata, is_byte, np):
    """(kind, classes, long name) of a raster, from its product token
    and, for a token this tool does not know, from its values."""
    token = product.split("_")[-1]
    for key in (product, token):
        if key in PRODUCTS:
            kind, classes, long_name = PRODUCTS[key]
            return kind, classes, long_name
    values = np.unique(a[a != nodata] if nodata is not None else a)
    if is_byte and values.size <= 16:
        return "classified", None, "Classified raster ({})".format(product)
    return "continuous", None, "Continuous raster ({})".format(product)


def stamp_raster(raster, program, fire_words, event, what, named_classes, older, source, today):
    """Title, summary, description and credits for one loaded raster, so
    that the raster says where it came from when it is found on its own.
    Never raises: metadata is a courtesy, not the result."""
    try:
        import arcpy

        info = B.PROGRAMS.get(program) or {}
        md = arcpy.metadata.Metadata(raster)
        md.title = os.path.basename(raster)
        md.tags = "{}, burn severity, fire severity, wildfire, data bundle, Wildlife and Forestry Tools".format(program)
        md.summary = ("{} of the {} (fire ID {}), loaded on {} by the Load Burn Severity Bundle tool from a data "
                      "bundle of the Burn Severity Portal.".format(what, fire_words, event, today))
        md.description = (
            "<p>" + G.html_text(
                "{}, for the {}. It is one of the rasters of the fire's data bundle{}, as {} made it: in the "
                "bundle's own projection, with the original cells. The bundle's file was {}.".format(
                    what, fire_words, ", which came in the portal's older form" if older else "",
                    info.get("name") or program, os.path.basename(source))) + "</p>"
            + ("<p>" + G.html_text(
                "The values are classes: the raster's attribute table names each class and gives its area in the "
                "Hectares and Acres fields and its share of the raster's assessed cells in Percent.") + "</p>"
               if named_classes else "")
            + "<p>These data are produced by the United States Government and cover the United States and its "
              "territories only.</p>"
            + "<p><b>Links to the sources:</b></p>" + G.html_list(
                (["The program: " + G.html_link(info["home"], "{} ({})".format(info["name"], program))]
                 if info else []) + ["The Burn Severity Portal: " + G.html_link(B.PORTAL)]))
        md.credits = ("{}Burn Severity Portal, U.S. Geological Survey and USDA Forest Service, {}. Loaded on "
                      "{}.".format("{} ({}), {}. ".format(info["name"], program, info["agency"]) if info else "",
                                   B.PORTAL, today))
        md.accessConstraints = "<p>Produced by agencies of the United States Government.</p>"
        md.save()
    except Exception:                                            # noqa: BLE001
        pass


def stamp_perimeters(fc, source, count, from_catalog, field_names, today, messages=None):
    """The same for the perimeters, with a description of every field.
    Never raises."""
    try:
        import arcpy
        import tool_metadata

        words = dict((f[0], f[5]) for f in B.FIELDS)
        words.update({
            "ignition_date": "The date the fire started, as the program records it (YYYY-MM-DD).",
            "pre_scene": "The identifier of the satellite image taken before the fire.",
            "post_scene": "The identifier of the satellite image taken after the fire.",
        })
        md = arcpy.metadata.Metadata(fc)
        md.title = os.path.basename(fc)
        md.tags = "burn severity, fire severity, wildfire, fire perimeter, data bundle, Wildlife and Forestry Tools"
        md.summary = "Created {} by the Load Burn Severity Bundle tool from {}: the {} of {}.".format(
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), source,
            "perimeter" if count == 1 else "perimeters", "1 fire" if count == 1 else "{} fires".format(n0(count)))
        md.description = (
            "<p>The perimeters of the fires of a Burn Severity Portal data bundle, one row for each fire, with "
            "what the bundle says of the fire: its name, dates and area, and for MTBS the dNBR thresholds the "
            "analyst chose.</p>"
            + ("<p>" + G.html_text(
                "{} of fires whose bundle came in the portal's older form, which holds no perimeter. {} taken "
                "from the fire catalog of the Burn Severity Portal on {}.".format(
                    "1 of the perimeters is" if from_catalog == 1
                    else "{} of the perimeters are".format(n0(from_catalog)),
                    "It was" if from_catalog == 1 else "They were", today)) + "</p>" if from_catalog else "")
            + "<p>These data are produced by the United States Government and cover the United States and its "
              "territories only.</p>"
            + "<p><b>Links to the sources:</b></p>" + G.html_list(
                ["The Burn Severity Portal: " + G.html_link(B.PORTAL)]))
        md.credits = "Burn Severity Portal, U.S. Geological Survey and USDA Forest Service, {}. Loaded on {}.".format(
            B.PORTAL, today)
        md.accessConstraints = "<p>Produced by agencies of the United States Government.</p>"
        md.save()
        tool_metadata.set_entity_and_fields(
            fc, entity="Fire perimeters of a Burn Severity Portal data bundle, one row per fire.",
            fields=[{"name": nm, "description": words[nm]} for nm in field_names if words.get(nm)],
            messages=messages)
    except Exception as e:                                       # noqa: BLE001
        if messages is not None:
            try:
                messages.addWarningMessage("Could not write the perimeters' metadata: {}".format(e))
            except Exception:                                    # noqa: BLE001
                pass


def run(source, out_workspace, **kwargs):
    """See ``_run``. Returns a dict: fires, perimeters, summary, rasters."""
    import arcpy

    try:
        auto = arcpy.env.autoCancelling
    except Exception:                                            # noqa: BLE001
        auto = None
    try:
        if auto is not None:
            arcpy.env.autoCancelling = False
        return _run(source, out_workspace, **kwargs)
    finally:
        if auto is not None:
            try:
                arcpy.env.autoCancelling = auto
            except Exception:                                    # noqa: BLE001
                pass


def _run(source, out_workspace, out_perimeters=None, out_summary=None, messages=None):
    import arcpy
    import mahalanobis as _mh
    import tool_metadata

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

    why = B.check_workspace(out_workspace)
    if why:
        raise BundleError(why.replace("for the classified rasters", "for the output"))
    ws = str(out_workspace).strip().replace("/", "\\").rstrip("\\")
    in_gdb = _mh.is_gdb_workspace(ws)
    home = os.path.dirname(ws) if in_gdb else ws
    for label, p in (("perimeters", out_perimeters), ("summary table", out_summary)):
        if p and not _mh.in_gdb_container(p):
            raise BundleError("Put the output {} in a file geodatabase.".format(label))

    step(1, "Reading the bundle...")
    folder, unpacked = unpack(source, home)
    if unpacked:
        say("Unpacked the zip file into {}.".format(folder))
    fires = find_fires(folder)
    older = find_older(folder, fires)
    unread = find_unread(folder, fires)

    def unread_words():
        return "; ".join("{} ({})".format(rel, ", ".join("{} {}".format(n, e) for e, n in sorted(exts.items())))
                         for rel, exts in unread[:20]) + (
                             " and {} more".format(n0(len(unread) - 20)) if len(unread) > 20 else "")

    if not fires:
        raise BundleError("No burn severity bundle files that this tool reads were found in {}. A bundle's files "
                          "are named like mtbs_az3525811164320190721_10016328_20190705_20200707_dnbr.tif.{}".format(
                              folder, " The folder does hold files in another form: {}. The portal sends some "
                              "older assessments that way; add those files to a map yourself.".format(unread_words())
                              if unread else ""))
    say("The bundle holds {} {} that this tool reads.".format(
        n0(len(fires)), "fire" if len(fires) == 1 else "fires") if unread
        else "The bundle holds {} {}.".format(n0(len(fires)), "fire" if len(fires) == 1 else "fires"))
    if unread:
        warn("{} of the bundle {} NOT LOADED, because this tool found no raster or perimeter it can read there: "
             "{}. The files are in {}.".format(
                 "1 folder" if len(unread) == 1 else "{} folders".format(n0(len(unread))),
                 "was" if len(unread) == 1 else "were", unread_words(), folder))
    catalog = {}
    if older:
        say("{} of the {} came in the portal's older form: rasters in ERDAS Imagine (.img) format, some inside a "
            "tar archive, named differently for each fire. Such a bundle holds the first map of the fire (BARC), "
            "its dNBR and the satellite images, and no soil burn severity raster and no perimeter. All of its "
            "rasters are loaded, under the names their files have, and the perimeter is taken from the fire "
            "catalog of the Burn Severity Portal.".format(
                n0(len(older)), "fire" if len(fires) == 1 else "fires"))
        step(3, "Asking the fire catalog for the perimeters of the older fires...")
        for program in sorted(set(k[0] for k in older)):
            try:
                for f in B.search(program, fire_ids=sorted(k[1] for k in older if k[0] == program)):
                    ev = str((f.get("properties") or {}).get("event_id") or "").upper()
                    catalog.setdefault((program, ev), f)
            except BundleError as e:
                warn("The fire catalog could not be read ({}), so the fires in the older form have no perimeter "
                     "and no name here, and their continuous rasters are summarized over their whole rectangle "
                     "and not the burned area alone.".format(str(e).rstrip(".")))
        lost = [k[1] for k in older if (k[0], k[1]) not in catalog]
        if lost and catalog:
            warn("The fire catalog has no {} fire with the ID {}, so {} no perimeter and no name here.".format(
                older[0][0], ", ".join(lost), "it has" if len(lost) == 1 else "they have"))

    scratch = arcpy.env.scratchFolder
    today = datetime.date.today().strftime("%Y-%m-%d")
    perims = []                       # (key, attributes dict, geometry)
    rows = []                         # the summary
    all_rasters = []
    used = set()
    unnamed = []
    todo = sum(len(d["rasters"]) for d in fires.values()) or 1
    done = 0
    for key in sorted(fires):
        program, event, map_id = key
        d = fires[key]
        attrs = {}
        shape = None
        if d["perimeter"]:
            names = [f.name for f in arcpy.ListFields(d["perimeter"])]
            with arcpy.da.SearchCursor(d["perimeter"], ["SHAPE@"] + [n for n in names if n.upper() not in ("FID", "SHAPE")]) as cur:
                for row in cur:
                    part = row[0]
                    shape = part if shape is None else shape.union(part)
                    attrs = dict(zip([n.upper() for n in names if n.upper() not in ("FID", "SHAPE")], row[1:]))
        if d.get("older") and (program, event) in catalog:
            attrs, shape = catalog_perimeter(catalog[(program, event)], arcpy)
        fire_name = str(attrs.get("INCID_NAME") or event)
        ig = str(attrs.get("IG_DATE") or "")
        m = re.match(r"(\d{4})", ig) or re.search(r"(\d{4})\d{4}$", event)
        year = int(m.group(1)) if m else None
        say("{} {} {} ({}{}): {}.".format(
            program, fire_name, year or "", event, ", older form" if d.get("older") else "",
            ", ".join(sorted(d["rasters"])) or "no rasters"))
        if shape is not None:
            perims.append((key, attrs, shape, fire_name, year))
        for product in sorted(d["rasters"]):
            if cancelled():
                raise Cancelled()
            done += 1
            step(5 + 85.0 * done / todo, "Loading {} of {}: {} {}".format(n0(done), n0(todo), fire_name, product))
            src = d["rasters"][product]
            a, nodata, cell_m2, is_byte, np, bands = describe_raster(src)
            kind, classes, long_name = classify_product(product, a, nodata, is_byte, np)
            base = B.safe_name("{}_{}_{}_{}".format(program, fire_name, year or "", product), 60)
            name = base
            k = 2
            while name.lower() in used:
                name = "{}_{}".format(base, k)
                k += 1
            used.add(name.lower())
            final = os.path.join(ws, name if in_gdb else name + ".tif")
            if arcpy.Exists(final):
                arcpy.management.Delete(final)
            # A scratch copy gets its statistics and histogram here, and Copy Raster carries them into
            # the output: ArcGIS Pro never has to calculate them. The copy also declares the "no data"
            # value the cells really use, where the bundle's file does not.
            tmp = os.path.join(scratch, "bundle_{}_{}.tif".format(os.getpid(), done))
            for old in (tmp, tmp + ".aux.xml"):
                if os.path.exists(old):
                    os.remove(old)
            from osgeo import gdal
            gdal.Translate(tmp, src, creationOptions=["COMPRESS=LZW", "BIGTIFF=IF_SAFER"])
            if kind == "classified":
                # drop the bundle's color table: with it ArcGIS Pro draws Colormap symbology and lists
                # some 250 black boxes without names; the colors go into the attribute table instead
                B.drop_color_table(tmp)
            fix = None
            if kind == "classified" and nodata is None and not any(c["value"] == 0 for c in (classes or [])):
                fix = 0                    # 0 is "outside the fire", though the file does not say so
            elif (kind == "classified" and classes and nodata != 255
                  and not any(int(c["value"]) == 255 for c in classes) and bool((a == 255).any())
                  and (nodata is None or not bool((a == nodata).any()))):
                fix = 255                  # filled with 255 outside the fire, though the file declares another
                #                            value (or none) and holds no cell of it: seen in BAER's sbs rasters
            elif kind == "continuous" and a.dtype.kind in "iu" and int(a.min()) == -32768 and nodata != -32768:
                fix = -32768               # the file declares another value and holds this one
            write_stats = B.write_statistics(tmp, also_nodata=fix)
            if fix is not None:
                nodata = fix
            if kind == "classified" and classes:
                B.write_class_table(tmp, classes, cell_m2=cell_m2)   # Copy Raster carries the table along
            try:
                arcpy.management.CopyRaster(tmp, final)
            finally:
                for old in (tmp, tmp + ".aux.xml"):
                    try:
                        os.remove(old)
                    except OSError:
                        pass
            all_rasters.append(final)
            stamp_raster(final, program,
                         "{} fire{}".format(fire_name, " of {}".format(year) if year else ""), event,
                         "Satellite image of {} bands ({})".format(bands, product) if bands > 1 else long_name,
                         kind == "classified" and bool(classes), bool(d.get("older")), src, today)
            if bands > 1:
                continue                   # a satellite image of several bands: copied, and not summarized
            valid = a != nodata if nodata is not None else np.ones(a.shape, bool)
            if kind == "continuous":
                # some bundles declare one "no data" value and hold another
                valid &= (a > -32768) if a.dtype.kind in "iu" else (a > -1e30)
            if kind == "classified":
                valid &= a > 0
                if d.get("older") and shape is not None:
                    # an older classified raster (BARC) covers the rectangle around the fire, with the land
                    # outside in its lowest class: count the cells inside the perimeter only
                    inside = _inside(src, shape, np)
                    if inside is not None and bool((valid & inside).any()):
                        valid &= inside
                values, counts = np.unique(a[valid], return_counts=True)
                names_of = dict((int(c["value"]), c["name"]) for c in (classes or []))
                masks = set(int(c["value"]) for c in (classes or []) if c.get("mask"))
                # the percentages are of the cells the program assessed: cells outside the fire, cells
                # without data and the program's mask classes are not in the total
                total = float(sum(n for v, n in zip(values.tolist(), counts.tolist()) if int(v) not in masks)) or 1.0
                for v, n in zip(values.tolist(), counts.tolist()):
                    area = n * cell_m2 if cell_m2 else None
                    rows.append([program, fire_name, event, year, product, kind, int(v),
                                 names_of.get(int(v), "(no name known for value {})".format(int(v))), int(n),
                                 area / 10000.0 if area else None, area / ACRE_M2 if area else None,
                                 None if int(v) in masks else 100.0 * n / total,
                                 None, None, None, None, None, final])
                if classes:
                    B.name_classes(final, classes, messages, cell_m2=cell_m2)
                else:
                    unnamed.append(os.path.basename(final))
            else:
                vals = a[valid].astype("float64")
                # the continuous rasters cover the rectangle around the fire: summarize the burned area only
                if shape is not None and vals.size:
                    inside = _inside(src, shape, np)
                    if inside is not None:
                        vals = a[valid & inside].astype("float64")
                if vals.size:
                    area = vals.size * cell_m2 if cell_m2 else None
                    rows.append([program, fire_name, event, year, product, kind, None, None, int(vals.size),
                                 area / 10000.0 if area else None, area / ACRE_M2 if area else None, None,
                                 float(vals.mean()), float(vals.std()), float(vals.min()),
                                 float(np.median(vals)), float(vals.max()), final])
    say("Copied {} {} into {}, each in its fire's own projection with its true cells.".format(
        n0(len(all_rasters)), "raster" if len(all_rasters) == 1 else "rasters", ws))
    if unnamed:
        warn("{} classified {} of a kind this tool does not know, so {} attribute table has values without "
             "names: {}.".format(n0(len(unnamed)), "raster is" if len(unnamed) == 1 else "rasters are",
                                 "its" if len(unnamed) == 1 else "their", "; ".join(unnamed)))

    # ---- perimeters -------------------------------------------------------
    step(92, "Writing the perimeters...")
    if out_perimeters and perims:
        gdb, fc_name = os.path.split(out_perimeters)
        if arcpy.Exists(out_perimeters):
            arcpy.management.Delete(out_perimeters)
        wgs = arcpy.SpatialReference(4326)
        arcpy.management.CreateFeatureclass(gdb, fc_name, "POLYGON", spatial_reference=_mh.clean_sr(wgs))
        spec = [("program", "TEXT", 20, "MAP_PROG"), ("fire_name", "TEXT", 120, "INCID_NAME"),
                ("fire_id", "TEXT", 40, "EVENT_ID"), ("fire_year", "LONG", None, None),
                ("ignition_date", "TEXT", 20, "IG_DATE"), ("fire_type", "TEXT", 40, "INCID_TYPE"),
                ("assessment", "TEXT", 40, "ASMNT_TYPE"), ("acres", "DOUBLE", None, "BURNBNDAC"),
                ("dnbr_offset", "LONG", None, "DNBR_OFFST"), ("dnbr_stddev", "LONG", None, "DNBR_STDDV"),
                ("greenness_t", "LONG", None, "INCGREEN_T"), ("low_t", "LONG", None, "LOW_T"),
                ("moderate_t", "LONG", None, "MOD_T"), ("high_t", "LONG", None, "HIGH_T"),
                ("pre_scene", "TEXT", 40, "PRE_ID"), ("post_scene", "TEXT", 40, "POST_ID"),
                ("comment", "TEXT", 255, "COMMENT"), ("map_id", "LONG", None, "MAP_ID")]
        # all the fields in ONE call: Add Field once for each of them is slow inside ArcGIS Pro
        arcpy.management.AddFields(out_perimeters, [[nm, kind, None, length if kind == "TEXT" else None]
                                                    for nm, kind, length, _src in spec])
        with arcpy.da.InsertCursor(out_perimeters, ["SHAPE@"] + [s[0] for s in spec]) as cur:
            for key, attrs, shape, fire_name, year in perims:
                row = [shape.projectAs(wgs)]
                for nm, kind, length, srcf in spec:
                    v = year if nm == "fire_year" else attrs.get(srcf)
                    if nm == "program" and not v:
                        v = key[0]
                    if v in (None, "None", "NULL", ""):
                        v = None
                    elif kind == "TEXT":
                        v = str(v)[:length]
                        day = re.match(r"\d{4}-\d{2}-\d{2}", v) if nm == "ignition_date" else None
                        if day:
                            v = day.group(0)       # a shapefile gives the date with a time, the catalog with a Z
                    else:
                        try:
                            v = float(v) if kind == "DOUBLE" else int(float(v))
                        except (TypeError, ValueError):
                            v = None
                        if v is not None and kind == "LONG" and nm.endswith("_t") and abs(v) >= 9999:
                            v = None
                    row.append(v)
                cur.insertRow(row)
        say("Wrote {} {} to {}.".format(n0(len(perims)), "perimeter" if len(perims) == 1 else "perimeters",
                                        out_perimeters))
        stamp_perimeters(out_perimeters, source, len(perims),
                         sum(1 for k, _a, _s, _n, _y in perims if fires[k].get("older")),
                         [s[0] for s in spec], today, messages)
    elif out_perimeters:
        warn("The bundle holds no perimeter shapefile, so no perimeters were written.")

    # ---- the summary ------------------------------------------------------
    if out_summary:
        gdb, t_name = os.path.split(out_summary)
        if arcpy.Exists(out_summary):
            arcpy.management.Delete(out_summary)
        arcpy.management.CreateTable(gdb, t_name)
        arcpy.management.AddFields(out_summary, [[nm, kind, alias, length if kind == "TEXT" else None]
                                                 for nm, alias, kind, length, _d in SUMMARY_FIELDS])
        with arcpy.da.InsertCursor(out_summary, [f[0] for f in SUMMARY_FIELDS]) as cur:
            for row in rows:
                row = list(row)
                row[-1] = str(row[-1])[:255]
                cur.insertRow(row)
        say("Wrote the summary of {} to {}: the area in each class of each classified raster, and the mean, "
            "standard deviation and range of each continuous raster inside the burned area. Areas are from "
            "the rasters' true cells.".format("the fire" if len(fires) == 1 else "the {} fires".format(n0(len(fires))),
                                              out_summary))
        try:
            md = arcpy.metadata.Metadata(out_summary)
            md.title = t_name
            md.summary = "Created {} by the Load Burn Severity Bundle tool from {}.".format(
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), source)
            md.description = ("<p>A summary of the rasters of a Burn Severity Portal data bundle, one row for "
                              "each class of each classified raster and one row for each continuous raster. "
                              "The programs are of the United States Government and map the United States and "
                              "its territories only.</p>")
            md.credits = "Burn Severity Portal, U.S. Geological Survey and USDA Forest Service, " + B.PORTAL
            md.save()
            tool_metadata.set_entity_and_fields(
                out_summary, entity="Burn severity summary.",
                fields=[{"name": f[0], "description": f[4]} for f in SUMMARY_FIELDS], messages=messages)
        except Exception:                                        # noqa: BLE001
            pass
    step(100, "Done: {} fires, {} rasters.".format(n0(len(fires)), n0(len(all_rasters))))
    return {"fires": len(fires), "perimeters": out_perimeters if perims else "", "summary": out_summary or "",
            "rasters": all_rasters, "rows": rows}


def _inside(raster, shape, np):
    """Which cells of a raster have their center inside a polygon
    (an arcpy geometry in any coordinate system), or None."""
    try:
        from osgeo import gdal, ogr, osr

        ds = gdal.Open(raster)
        sr = ds.GetSpatialRef()
        import arcpy

        try:
            g = shape.projectAs(arcpy.SpatialReference(text=sr.ExportToWkt()))
            geom = ogr.CreateGeometryFromWkb(bytes(g.WKB))
            if geom is None or geom.IsEmpty():
                raise ValueError("empty")
        except Exception:                                        # noqa: BLE001
            # ArcGIS does not take the coordinate system of some older rasters (ERDAS Imagine files
            # in Albers) as text: let GDAL project the perimeter, which is only a mask here
            wgs = osr.SpatialReference()
            wgs.ImportFromEPSG(4326)
            wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            to = osr.SpatialReference(sr.ExportToWkt())
            to.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            geom = ogr.CreateGeometryFromWkb(bytes(shape.projectAs(arcpy.SpatialReference(4326)).WKB))
            geom.Transform(osr.CoordinateTransformation(wgs, to))
        mem = ogr.GetDriverByName("MEM").CreateDataSource("m")
        lyr = mem.CreateLayer("p", sr, geom.GetGeometryType())
        feat = ogr.Feature(lyr.GetLayerDefn())
        feat.SetGeometry(geom)
        lyr.CreateFeature(feat)
        mask = gdal.GetDriverByName("MEM").Create("", ds.RasterXSize, ds.RasterYSize, 1, gdal.GDT_Byte)
        mask.SetGeoTransform(ds.GetGeoTransform())
        mask.SetProjection(sr.ExportToWkt())
        gdal.RasterizeLayer(mask, [1], lyr, burn_values=[1])
        return mask.GetRasterBand(1).ReadAsArray() == 1
    except Exception:                                            # noqa: BLE001
        return None
