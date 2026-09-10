"""Cartography tools for the Jenness Enterprises toolbox.

Currently one tool: Create Shadow Polygons -- concentric ring buffers around
the (dissolved) polygons of a feature class, with a per-ring "Transparency"
value that fades outward, so the rings render as a soft cartographic shadow
behind the source features. Port of the VB6 ``CreateShadowPolys`` in
``MyGeneralOperations.bas`` (Springs Stewardship Institute VBA_Code), with the
legacy world-azimuthal reprojection replaced by true geodesic buffering for
geographic inputs.

Ring construction follows the legacy incremental approach: each iteration
buffers the PREVIOUS shell outward by one increment (smoother seams and much
faster than buffering the source at k increasing distances), and the ring is
the buffer minus the previous shell. Because the rings are purely aesthetic,
the dissolved source is generalized (Douglas-Peucker) before buffering so the
output stays lightweight.
"""

import json
import math
import os

import arcpy

import mahalanobis as _mh
import tool_metadata

# Size-unit choices for the outermost ring distance.
SIZE_PROP_WIDTH = "Proportion of extent width"
SIZE_PROP_HEIGHT = "Proportion of extent height"
SIZE_UNITS = [SIZE_PROP_WIDTH, SIZE_PROP_HEIGHT,
              "Meters", "Kilometers", "Feet", "Miles"]
_UNIT_METERS = {"Meters": 1.0, "Kilometers": 1000.0,
                "Feet": 0.3048, "Miles": 1609.344}

_MEM_DISS = "memory\\jenness_shadow_dissolve"
_MEM_BUF = "memory\\jenness_shadow_buffer"
_MEM_PRJ = "memory\\jenness_shadow_project"


def _msg(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _dissolve_source(in_features, messages):
    """Dissolve the input's polygons into ONE geometry.

    A layer with a selection dissolves just the selected polygons (Dissolve
    honors layer selections); a layer without a selection, or a dataset path,
    dissolves every polygon.
    """
    try:
        desc = arcpy.Describe(in_features)
        fid_set = getattr(desc, "FIDSet", None)
        n_sel = len(fid_set.split(";")) if fid_set else 0
    except Exception:
        n_sel = 0
    if arcpy.Exists(_MEM_DISS):
        arcpy.management.Delete(_MEM_DISS)
    arcpy.management.Dissolve(in_features, _MEM_DISS, multi_part="MULTI_PART")
    geom = None
    with arcpy.da.SearchCursor(_MEM_DISS, ["SHAPE@"]) as cur:
        for row in cur:
            geom = row[0]
            break
    arcpy.management.Delete(_MEM_DISS)
    if geom is None or geom.area <= 0:
        raise arcpy.ExecuteError(
            "The input has no polygons to buffer (empty dataset or empty selection).")
    _msg(messages,
         "Source: dissolved {} into a single polygon.".format(
             "the {} selected polygon(s)".format(n_sel) if n_sel else "all polygons"))
    return geom, n_sel


def _outer_distance_meters(geom, sr, size_value, size_units, geodesic):
    """The outermost ring distance in METERS from the user's value + units."""
    if size_units in _UNIT_METERS:
        return size_value * _UNIT_METERS[size_units]

    ext = geom.extent
    if size_units == SIZE_PROP_WIDTH:
        p1 = (ext.XMin, (ext.YMin + ext.YMax) / 2.0)
        p2 = (ext.XMax, (ext.YMin + ext.YMax) / 2.0)
        planar_span = ext.width
    elif size_units == SIZE_PROP_HEIGHT:
        p1 = ((ext.XMin + ext.XMax) / 2.0, ext.YMin)
        p2 = ((ext.XMin + ext.XMax) / 2.0, ext.YMax)
        planar_span = ext.height
    else:
        raise arcpy.ExecuteError("Unknown size units: {}".format(size_units))

    if geodesic:
        pg1 = arcpy.PointGeometry(arcpy.Point(*p1), sr)
        pg2 = arcpy.PointGeometry(arcpy.Point(*p2), sr)
        span_m = pg1.angleAndDistanceTo(pg2, "GEODESIC")[1]
    else:
        span_m = planar_span * (sr.metersPerUnit or 1.0)
    return size_value * span_m


def _buffer_geometry(geom, dist_m, sr, geodesic):
    """Buffer a polygon geometry by dist_m meters (geodesic for GCS inputs)."""
    if not geodesic:
        return geom.buffer(dist_m / (sr.metersPerUnit or 1.0))
    if arcpy.Exists(_MEM_BUF):
        arcpy.management.Delete(_MEM_BUF)
    arcpy.analysis.Buffer(geom, _MEM_BUF, "{} Meters".format(dist_m),
                          method="GEODESIC", dissolve_option="NONE")
    out = None
    with arcpy.da.SearchCursor(_MEM_BUF, ["SHAPE@"]) as cur:
        for row in cur:
            out = row[0]
            break
    arcpy.management.Delete(_MEM_BUF)
    if out is None:
        raise arcpy.ExecuteError("Geodesic buffering failed.")
    return out


def _write_layer_file(out_fc, name, messages):
    """A .lyrx beside the output: black solid fill, no outline, with a
    transparency visual variable bound to the Transparency field (the CIM
    analog of the legacy ITransparencyRenderer.TransparencyField)."""
    ws = os.path.dirname(out_fc)
    # Normalized before the suffix test (trailing separators / forward slashes
    # defeat a bare endswith), and .sde counts as a geodatabase too -- this
    # mirrors mahalanobis.is_gdb_workspace inline (no mahalanobis import here).
    ws_norm = ws.lower().replace("/", "\\").rstrip("\\")
    if ws_norm.split("\\", 1)[0] in ("memory", "in_memory"):
        return None                    # no on-disk home for a side .lyrx
    is_gdb = ws_norm.endswith((".gdb", ".sde"))
    lyr_dir = os.path.dirname(ws) if is_gdb else ws
    lyrx = os.path.join(lyr_dir, name + ".lyrx")

    doc = {
        "type": "CIMLayerDocument",
        "version": "3.0.0",
        "build": 0,
        "layers": ["CIMPATH=shadow/{}.json".format(name)],
        "layerDefinitions": [{
            "type": "CIMFeatureLayer",
            "name": name,
            "uRI": "CIMPATH=shadow/{}.json".format(name),
            "layerType": "Operational",
            "visibility": True,
            "featureTable": {
                "type": "CIMFeatureTable",
                "editable": True,
                "dataConnection": {
                    "type": "CIMStandardDataConnection",
                    "workspaceConnectionString": "DATABASE=" + ws,
                    "workspaceFactory": "FileGDB" if is_gdb else "Shapefile",
                    "dataset": name,
                    "datasetType": "esriDTFeatureClass",
                },
                "studyAreaSpatialRel": "esriSpatialRelUndefined",
                "searchOrder": "esriSearchOrderSpatial",
            },
            "renderer": {
                "type": "CIMSimpleRenderer",
                "patch": "Default",
                "symbol": {
                    "type": "CIMSymbolReference",
                    "symbol": {
                        "type": "CIMPolygonSymbol",
                        "symbolLayers": [{
                            "type": "CIMSolidFill",
                            "enable": True,
                            "color": {"type": "CIMRGBColor",
                                      "values": [0, 0, 0, 100]},
                        }],
                    },
                },
                "visualVariables": [{
                    "type": "CIMTransparencyVisualVariable",
                    "field": "Transparency",
                    "dataValues": [0.0, 100.0],
                    "transparencyValues": [0.0, 100.0],
                }],
            },
        }],
    }
    try:
        with open(lyrx, "w") as f:
            json.dump(doc, f, indent=2)
        return _mh.ensure_label_class(lyrx, messages)
    except Exception as exc:
        _msg(messages, "Warning: could not write the layer file ({}).".format(exc))
        return None


def _reproject_in_place(fc, messages):
    """Honor the Output Coordinate System environment: project the finished
    output in place (memory round-trip), mirroring the LoCoH tools."""
    out_cs = arcpy.env.outputCoordinateSystem
    if out_cs is None:
        return
    src_sr = arcpy.Describe(fc).spatialReference
    if src_sr and src_sr.factoryCode == out_cs.factoryCode and \
            src_sr.name == out_cs.name:
        return
    if arcpy.Exists(_MEM_PRJ):
        arcpy.management.Delete(_MEM_PRJ)
    with arcpy.EnvManager(outputCoordinateSystem=None):
        arcpy.management.Project(fc, _MEM_PRJ, out_cs)
        arcpy.management.Delete(fc)
        arcpy.management.CopyFeatures(_MEM_PRJ, fc)
        arcpy.management.Delete(_MEM_PRJ)
    _msg(messages, "Output projected to {} (Output Coordinate System "
                   "environment).".format(out_cs.name))


def run_shadow_polygons(in_features, out_fc, num_rings=10, size_value=0.25,
                        size_units=SIZE_PROP_WIDTH, min_transparency=50.0,
                        messages=None):
    """Create the shadow-ring feature class. See the module docstring."""
    num_rings = int(num_rings)
    if num_rings < 1:
        raise arcpy.ExecuteError("The number of buffers must be at least 1.")
    min_transparency = float(min_transparency)
    if not 0.0 <= min_transparency < 100.0:
        raise arcpy.ExecuteError(
            "The minimum transparency must be at least 0 and less than 100.")
    size_value = float(size_value)
    if size_value <= 0:
        raise arcpy.ExecuteError("The outermost buffer size must be positive.")

    src_geom, n_sel = _dissolve_source(in_features, messages)
    sr = src_geom.spatialReference
    geodesic = (sr.type == "Geographic")

    outer_m = _outer_distance_meters(src_geom, sr, size_value, size_units, geodesic)
    increment_m = outer_m / num_rings
    _msg(messages, "Outermost ring: {:.1f} m ({} rings of {:.1f} m each, "
                   "{} buffering).".format(outer_m, num_rings, increment_m,
                                           "geodesic" if geodesic else "planar"))

    # Generalize the dissolved source before buffering -- the rings are purely
    # aesthetic, so a tolerance of 1/10 of one ring increment is invisible at
    # any scale where the shadow reads, and keeps the output small.
    tol_m = increment_m / 10.0
    tol_native = tol_m / 111320.0 if geodesic else tol_m / (sr.metersPerUnit or 1.0)
    gen_geom = src_geom
    try:
        g = src_geom.generalize(tol_native)
        if g is not None and g.area > 0:
            gen_geom = g
            _msg(messages, "Source generalized with a {:.2f} m tolerance "
                           "before buffering.".format(tol_m))
    except Exception:
        pass

    # Incremental ring construction (legacy approach): buffer the previous
    # shell one increment outward; the ring is the new shell minus the old.
    step = (100.0 - min_transparency) / num_rings
    rows = [(src_geom, 0.0, 100.0)]              # the source, fully transparent
    prev = gen_geom
    for i in range(1, num_rings + 1):
        arcpy.SetProgressorLabel("Buffer {} of {}...".format(i, num_rings)) \
            if hasattr(arcpy, "SetProgressorLabel") else None
        shell = _buffer_geometry(prev, increment_m, sr, geodesic)
        ring = shell.difference(prev)
        if ring is None or ring.area <= 0:
            ring = shell
        transparency = min_transparency + (i - 1) * step
        rows.append((ring, i * increment_m, transparency))
        prev = shell

    # Output feature class: Buffer_Size (meters, 0 for the source) and
    # Transparency (percent) -- same schema as the legacy tool plus the source.
    out_ws, out_name = os.path.split(out_fc)
    if arcpy.Exists(out_fc):
        arcpy.management.Delete(out_fc)
    arcpy.management.CreateFeatureclass(out_ws, out_name, "POLYGON",
                                        spatial_reference=_mh.clean_sr(sr))
    arcpy.management.AddFields(out_fc, [
        ["Buffer_Size", "DOUBLE", "", "", "", ""],
        ["Transparency", "DOUBLE", "", "", "", ""]])
    with arcpy.da.InsertCursor(out_fc, ["SHAPE@", "Buffer_Size", "Transparency"]) as cur:
        for geom, dist, transp in rows:
            cur.insertRow((geom, dist, transp))
    _msg(messages, "Wrote {} polygons (source + {} rings).".format(len(rows), num_rings))

    _reproject_in_place(out_fc, messages)

    # Metadata: conceptual description + per-run provenance (standing rule).
    try:
        md = arcpy.metadata.Metadata(out_fc)
        md.title = out_name
        md.tags = "shadow, buffer, ring, cartography, Jenness Enterprises"
        md.summary = (
            "Created by Create Shadow Polygons from {src}{sel}: {n} ring buffer(s), "
            "outermost {val} {units} ({m:.1f} m), minimum transparency {mt}%, "
            "{method} buffering. The dissolved source polygon is included with "
            "Transparency = 100 (fully transparent).").format(
                src=str(in_features), n=num_rings, val=size_value,
                units=size_units.lower(), m=outer_m, mt=min_transparency,
                sel=" (selected polygons only)" if n_sel else "",
                method="geodesic" if geodesic else "planar")
        md.description = (
            "Concentric ring buffers around a source polygon, symbolized as a "
            "fading black shadow: each ring's Transparency value increases "
            "outward, so rendering the rings in black with per-feature "
            "transparency makes the source features appear to cast a soft "
            "shadow. The rings are approximate (the source is generalized "
            "before buffering) and intended for cartographic effect, not "
            "analysis.")
        md.save()
    except Exception:
        pass
    try:
        tool_metadata.set_entity_and_fields(
            out_fc,
            entity="One shadow ring per record, plus the dissolved source polygon.",
            fields=[
                {"name": "Buffer_Size",
                 "description": "Outer distance of this ring from the source "
                                "polygon, in meters. 0 for the source polygon "
                                "itself."},
                {"name": "Transparency",
                 "description": "Percent transparency (0-100) to render this "
                                "polygon with, when the layer varies "
                                "transparency by this field. The source polygon "
                                "is 100 (invisible); rings fade outward from "
                                "the minimum transparency to nearly 100."},
            ])
    except Exception:
        pass

    return _write_layer_file(out_fc, out_name, messages)


# ----------------------------------------------------------------------------
#  Simple Hypsometric Shading (group-layer composition; creates NO dataset)
# ----------------------------------------------------------------------------

def _raster_layer_cim(raster, messages):
    """CIM layer definition (dict) for ``raster``, with the dataConnection
    written by Esri's own code: MakeRasterLayer -> SaveToLayerFile -> parse.
    Returns the CIMRasterLayer dict from the saved document.
    """
    path = arcpy.Describe(raster).catalogPath
    name = os.path.splitext(os.path.basename(path))[0]
    tmp_name = arcpy.CreateUniqueName("hypso_part", "memory").replace(
        "memory\\", "jenness_hypso_")
    lyr_obj = arcpy.management.MakeRasterLayer(path, tmp_name)[0]
    tmp_lyrx = arcpy.CreateUniqueName("hypso_part.lyrx", arcpy.env.scratchFolder)
    try:
        arcpy.management.SaveToLayerFile(lyr_obj, tmp_lyrx, "ABSOLUTE")
        with open(tmp_lyrx) as f:
            doc = json.load(f)
    finally:
        try:
            arcpy.management.Delete(lyr_obj)
        except Exception:
            pass
        try:
            os.remove(tmp_lyrx)
        except Exception:
            pass
    lyr_def = doc["layerDefinitions"][0]
    lyr_def["name"] = name
    return lyr_def, doc.get("version"), doc.get("build")


def run_simple_hypso_shading(dem, hillshade, transparency=50.0,
                             blend_mode="Normal", group_name=None,
                             out_lyrx=None, messages=None):
    """Compose a hypsometric-shading GROUP LAYER (no new dataset): the DEM on
    top, stretched black (lowest elevation) to white with the given percent
    ``transparency``, over the existing ``hillshade``. Writes a .lyrx layer
    file referencing the two existing rasters and returns its path (default:
    a scratch-folder file, so the only lasting artifact is the group layer
    the caller adds to the map).
    """
    transparency = float(transparency)
    if not 0.0 <= transparency <= 100.0:
        raise arcpy.ExecuteError("The transparency must be 0 to 100 percent.")
    if blend_mode not in _BLEND_CIM:
        raise arcpy.ExecuteError(
            "Unknown layer blend mode: {} (choose one of {}).".format(
                blend_mode, ", ".join(BLEND_MODE_NAMES)))

    dem_def, version, build = _raster_layer_cim(dem, messages)
    hs_def, _v, _b = _raster_layer_cim(hillshade, messages)

    # Unique CIM URIs for the three layers.
    dem_def["uRI"] = "CIMPATH=jenness_hypso_dem.xml"
    hs_def["uRI"] = "CIMPATH=jenness_hypso_hillshade.xml"
    group_uri = "CIMPATH=jenness_hypso_group.xml"

    # DEM on top: min-max stretch, black at the minimum elevation up to
    # white, at the requested transparency and blend mode. PATCH the
    # Esri-generated colorizer rather than replacing it -- its
    # stretchClasses/stretchStats are what the Contents pane renders as the
    # layer's legend (a rebuilt colorizer without them shows no legend).
    dem_def["transparency"] = transparency
    dem_def["blendingMode"] = _BLEND_CIM[blend_mode]
    col = dem_def.get("colorizer") or {}
    if col.get("type") != "CIMRasterStretchColorizer":
        col = {
            "type": "CIMRasterStretchColorizer",
            "resamplingType": "BilinearInterpolation",
            "noDataColor": {"type": "CIMRGBColor", "values": [255, 255, 255, 0]},
            "backgroundColor": {"type": "CIMRGBColor", "values": [255, 255, 255, 0]},
            "colorScheme": "",
            "hillshadeZFactor": 1,
            "gammaValue": 1,
            "maxPercent": 0.25,
            "minPercent": 0.25,
            "standardDeviationParam": 2,
        }
    col["stretchType"] = "MinimumMaximum"
    col["statsType"] = "Dataset"
    col["dra"] = False
    col["colorRamp"] = {
        "type": "CIMLinearContinuousColorRamp",
        "colorSpace": {"type": "CIMICCColorSpace", "url": "Default RGB"},
        "fromColor": {"type": "CIMRGBColor", "values": [0, 0, 0, 100]},
        "toColor": {"type": "CIMRGBColor", "values": [255, 255, 255, 100]},
    }
    dem_def["colorizer"] = col

    if not group_name:
        group_name = "{} Hypsometric Shading".format(dem_def["name"])
    group_def = {
        "type": "CIMGroupLayer",
        "name": group_name,
        "uRI": group_uri,
        "layers": [dem_def["uRI"], hs_def["uRI"]],
        "visibility": True,
        "expanded": True,
    }

    doc = {
        "type": "CIMLayerDocument",
        "version": version or "3.0.0",
        "build": build or 0,
        "layers": [group_uri],
        "layerDefinitions": [group_def, dem_def, hs_def],
    }

    if not out_lyrx:
        out_lyrx = arcpy.CreateUniqueName("hypso_group.lyrx",
                                          arcpy.env.scratchFolder)
    with open(out_lyrx, "w") as f:
        json.dump(doc, f, indent=2)
    _msg(messages, "Hypsometric shading group layer composed: '{}' (DEM "
                   "stretched black to white at {:g}% transparency{} over "
                   "the hillshade). No new dataset was created; the group "
                   "layer references the existing rasters.".format(
                       group_name, transparency,
                       "" if blend_mode == "Normal"
                       else ", blend mode {}".format(blend_mode)))
    return out_lyrx


# Layer blend modes offered by the Simple Hypsometric Shading dialog, in the
# order of ArcGIS Pro's own Layer Blend menu: dialog name -> CIM BlendingMode
# enum value ("Normal" is CIM "Alpha").
BLEND_MODES = [
    ("Normal", "Alpha"),
    ("Screen", "Screen"), ("Color Dodge", "ColorDodge"),
    ("Lighten", "Lighten"), ("Linear Dodge", "LinearDodge"),
    ("Multiply", "Multiply"), ("Color Burn", "ColorBurn"),
    ("Darken", "Darken"), ("Linear Burn", "LinearBurn"),
    ("Difference", "Difference"), ("Exclusion", "Exclusion"),
    ("Overlay", "Overlay"), ("Hard Light", "HardLight"),
    ("Soft Light", "SoftLight"), ("Linear Light", "LinearLight"),
    ("Pin Light", "PinLight"), ("Vivid Light", "VividLight"),
    ("Luminosity", "Luminosity"), ("Saturation", "Saturation"),
    ("Hue", "Hue"), ("Color", "Color"),
]
BLEND_MODE_NAMES = [n for n, _ in BLEND_MODES]
_BLEND_CIM = dict(BLEND_MODES)
