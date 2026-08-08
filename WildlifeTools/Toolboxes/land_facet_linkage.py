# -*- coding: utf-8 -*-
"""Machine-readable linkage records for the Land Facet Corridor analysis chain.

The Land Facet analysis is a chain of tools -- clustering produces a facet
raster, the density tool turns that into a per-facet neighborhood-proportion
raster, and the Mahalanobis tool consumes both -- and each step needs to know
exactly what the previous step did. Prose metadata cannot carry that reliably,
so each output also carries a small JSON record that the next tool reads.

WHERE THE RECORD LIVES (verified in Pro 3.7 on 2026-08-07, do not change without
re-testing): the ArcGIS metadata element ``dataIdInfo/suppInfo`` -- the standard
"Supplemental Information" free-text field. It was the only carrier tested that
survived every step of a realistic round trip:

===========================  ==========================  ==================
carrier                      CopyRaster into a file gdb  SELECTIVE / ALWAYS
                                                         synchronize
===========================  ==========================  ==================
dataIdInfo/suppInfo          survives                    survives
custom root element          STRIPPED IMMEDIATELY        --
GDAL dataset metadata items  LOST                        --
GDAL band descriptions       LOST                        --
GDAL band metadata items     LOST (band 1 only, flakily) --
===========================  ==========================  ==================

Two consequences worth remembering. First, GDAL-side metadata is useless here:
``CopyRaster`` strips it even GeoTIFF-to-GeoTIFF, and a file-geodatabase raster
cannot be re-stamped through GDAL afterwards. Second, because band descriptions
do not survive either, a multi-band density raster's bands arrive in the
geodatabase named only ``Band_1..Band_N`` -- so the band-to-facet mapping MUST
come from this record. That is the whole reason it exists.

The record is written with ElementTree over ``md.xml`` (the same technique
``tool_metadata.py`` uses for field metadata) rather than through a typed
property, and always AFTER the dataset reaches its final location.
"""
import datetime
import json
import uuid
import xml.etree.ElementTree as ET

SCHEMA = "jenness.landfacet/1"

# Record kinds.
FACETS = "facets"       # written by the Land Facet Clustering tool
DENSITY = "density"     # written by the Land Facet Density tool
MAHALANOBIS = "mahalanobis"   # written by the Land Facet Mahalanobis tool
INVERTED = "inverted"   # written by the Invert Raster tool
DIVERSITY = "diversity"       # written by the Diversity Indices tool
TERMINI = "termini"     # written by the Identify Termini Polygons tool

# Marker prefix so the blob is recognizable (and so a human reading the
# Supplemental Information field can see what it is) and separable from any
# other supplemental text already present.
_MARK = "JENNESS-LANDFACET-LINKAGE:"

# The characteristic ("ideal") value of the density variable in a Mahalanobis
# analysis is 100% -- NOT the sample mean. Brost & Beier use 100% "even if the
# 100% value is far above the mean" (Land_Facet_Tools.pdf p.11 fn.4, p.12, and
# the manual workflow step at p.30 which tells the user to hand-edit the mean to
# 1). Carrying it here lets the Mahalanobis tool apply that automatically
# instead of relying on the user remembering the manual edit.
IDEAL_DENSITY = 1.0

# Denominator rules for the neighborhood proportion. FAITHFUL reproduces the
# original ArcMap tool exactly (TopoCor_Functions.CalcDensityFromArray): the
# divisor is every neighborhood cell that lies inside the raster, whether or not
# it holds data. See the density tool for why the alternative is discouraged.
DENOM_IN_RASTER = "all_in_raster_cells"
DENOM_NON_NODATA = "non_nodata_cells_only"


def new_analysis_id():
    """A fresh analysis identifier, shared by every output of one analysis."""
    return str(uuid.uuid4())


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# record construction
# ---------------------------------------------------------------------------
def facet_record(analysis_id, variables, facets, grid=None, params=None,
                 source=None):
    """Build the record stamped on a Land Facet CLUSTER raster.

    ``variables`` is the ORDERED list of clustering variables -- order is load
    bearing, because the Mahalanobis mean vector and covariance matrix must be
    built in the same order (manual p.77, p.82), and getting it wrong produces
    silently wrong distances. Each entry: ``{"name", "source"}``.

    ``facets``: ``[{"facet_id", "category_value", "category_label", "cluster"}]``
    -- ``facet_id`` is the cell value actually written to the raster.
    """
    return {
        "schema": SCHEMA,
        "record": FACETS,
        "analysis_id": analysis_id,
        "created": _utc_now(),
        "tool": "Land Facet Clustering",
        "source": source or {},
        "grid": grid or {},
        "variables": list(variables or []),
        "facets": list(facets or []),
        "params": params or {},
    }


def density_record(analysis_id, source_facet_raster, bands, neighborhood,
                   denominator=DENOM_IN_RASTER, scale_factor=None,
                   source_analysis_id=None, variables=None, grid=None):
    """Build the record stamped on a multi-band Land Facet DENSITY raster.

    ``bands``: ``[{"band", "facet_id", "category_value", "category_label",
    "cluster"}]`` with ``band`` 1-based, matching GDAL/arcpy band numbering.

    ``scale_factor`` is the multiplier that converts a stored integer back to a
    proportion (stored / 10000 -> 0..1). ``None`` means the band already holds
    the proportion as floating point.
    """
    return {
        "schema": SCHEMA,
        "record": DENSITY,
        "analysis_id": analysis_id,
        "created": _utc_now(),
        "tool": "Land Facet Density",
        "source_facet_raster": str(source_facet_raster),
        "source_analysis_id": source_analysis_id,
        "grid": grid or {},
        "variables": list(variables or []),
        "neighborhood": neighborhood or {},
        "denominator": denominator,
        "scale_factor": scale_factor,
        "ideal_value": IDEAL_DENSITY,
        "bands": list(bands or []),
    }


def mahalanobis_record(analysis_id, facet, source_facet_raster, variables,
                       out_type, n_sample, source_analysis_id=None,
                       density_band=None, density_ideal=None,
                       source_density_raster=None, blocks=None, tables=None):
    """Build the record stamped on ONE facet's Mahalanobis cost surface.

    Unlike the facet and density records this describes a single facet, because
    each facet gets its own single-band raster -- these are terminal products fed
    to least-cost corridor tools, which expect one band.
    """
    return {
        "schema": SCHEMA,
        "record": MAHALANOBIS,
        "analysis_id": analysis_id,
        "created": _utc_now(),
        "tool": "Land Facet Mahalanobis",
        "source_analysis_id": source_analysis_id,
        "source_facet_raster": str(source_facet_raster),
        "source_density_raster": (str(source_density_raster)
                                  if source_density_raster else None),
        "wildland_blocks": str(blocks) if blocks else None,
        "facet": facet or {},
        "variables": list(variables or []),
        "density_band": density_band,
        "density_ideal": density_ideal,
        "out_type": out_type,
        "n_sample": n_sample,
        "tables": tables or {},
    }


def diversity_record(analysis_id, source_raster, index_label, q,
                     neighborhood_note, source_analysis_id=None,
                     exclude_if_any_null=False):
    """Build the record stamped on a Diversity Indices output.

    Diversity of the land facets is the published step-3 alternative -- its
    inversion becomes the cost surface for the facet-interspersion corridor --
    so its outputs are chain members: when the input categorical raster carries
    a facets record the analysis id chains through, and the Invert Raster /
    Describe tools can then follow the lineage. The tool also serves plain
    species/community rasters, where the record simply documents provenance
    with a fresh id.
    """
    return {
        "schema": SCHEMA,
        "record": DIVERSITY,
        "analysis_id": analysis_id,
        "created": _utc_now(),
        "tool": "Diversity Indices",
        "source_raster": str(source_raster),
        "source_analysis_id": source_analysis_id,
        "index": index_label,
        "q": q,
        "neighborhood": neighborhood_note,
        "exclude_if_any_null": bool(exclude_if_any_null),
    }


def termini_record(analysis_id, source_raster, blocks, threshold_mode,
                   relative_size_pct, source_analysis_id=None, facets=None,
                   threshold_value=None, n_blocks=None):
    """Build the record stamped on an Identify Termini Polygons output.

    Termini are the POLYGON product of the chain -- the high-density (or
    high-diversity) concentrations inside each Wildland Block that the
    least-cost corridors will run between -- so the record carries what they
    were cut from and how: the source surface, the threshold rule (a fixed
    value, or the median computed within each block), the relative-size
    retention rule, and the facets present in the output (the future corridor
    creation tools group the feature class by its Facet field and can build
    every facet's corridor in one run).

    ``facets``: ``[{"facet_id", "label", "band", "n_termini"}]``.
    ``threshold_value`` is the fixed threshold (None in median mode, where
    each block computed its own).
    """
    return {
        "schema": SCHEMA,
        "record": TERMINI,
        "analysis_id": analysis_id,
        "created": _utc_now(),
        "tool": "Identify Termini Polygons",
        "source_raster": str(source_raster),
        "source_analysis_id": source_analysis_id,
        "wildland_blocks": str(blocks) if blocks else None,
        "n_blocks": n_blocks,
        "threshold_mode": threshold_mode,
        "threshold_value": threshold_value,
        "relative_size_pct": relative_size_pct,
        "facets": list(facets or []),
    }


def inverted_record(analysis_id, source_raster, constant, scale_applied,
                    source_analysis_id=None, facet=None, band=None):
    """Build the record stamped on an Invert Raster output.

    Records where the surface stands in the Land Facet chain: what it was
    inverted from (and, for a density band, WHICH facet's band), with what
    constant, and whether the stored x10000 scaling was undone first. When the
    source carried a record, the analysis id chains through; a source without
    one (e.g. a Diversity Indices raster) gets a fresh id so later steps can
    still group the products of one analysis.
    """
    return {
        "schema": SCHEMA,
        "record": INVERTED,
        "analysis_id": analysis_id,
        "created": _utc_now(),
        "tool": "Invert Raster",
        "source_raster": str(source_raster),
        "source_analysis_id": source_analysis_id,
        "facet": facet or None,
        "band": band,
        "formula": "1/(x + c)",
        "constant": constant,
        "scale_applied": scale_applied,
    }


# ---------------------------------------------------------------------------
# read / write
# ---------------------------------------------------------------------------
def _child(parent, tag):
    el = parent.find(tag)
    if el is None:
        el = ET.SubElement(parent, tag)
    return el


def write(target, record, messages=None):
    """Stamp ``record`` onto ``target``'s metadata. Never raises.

    Call this AFTER the dataset is at its final location -- the record travels
    with the Esri metadata, not with the pixels, so stamping a scratch file and
    then copying would work, but stamping the destination is what is tested.
    Returns True on success.
    """
    try:
        from arcpy import metadata as md
        m = md.Metadata(target)
        root = ET.fromstring(m.xml)
        el = _child(_child(root, "dataIdInfo"), "suppInfo")
        el.attrib.pop("Sync", None)       # else Esri may overwrite our text
        existing = el.text or ""
        # Preserve any non-linkage supplemental text the dataset already had.
        keep = existing.split(_MARK, 1)[0].strip() if _MARK in existing else existing.strip()
        blob = _MARK + json.dumps(record, separators=(",", ":"))
        el.text = (keep + "\n\n" + blob) if keep else blob
        m.xml = ET.tostring(root, encoding="unicode")
        m.save()
        return True
    except Exception as e:                                       # noqa: BLE001
        if messages is not None:
            messages.addWarningMessage(
                "Could not write the land facet linkage metadata: {}".format(e))
        return False


def read(target, kind=None):
    """Return the linkage record stamped on ``target``, or None.

    ``kind`` optionally requires a specific record type (``FACETS`` /
    ``DENSITY``); a mismatch returns None. Never raises -- an unstamped or
    unreadable dataset simply has no record, which callers must handle by
    falling back to asking the user.
    """
    try:
        from arcpy import metadata as md
        xml = md.Metadata(target).xml or ""
        text = ET.fromstring(xml).findtext("dataIdInfo/suppInfo") or ""
        if _MARK not in text:
            return None
        blob = text.split(_MARK, 1)[1].strip()
        # Tolerate trailing prose appended after the JSON by a later editor.
        rec = json.JSONDecoder().raw_decode(blob)[0]
    except Exception:                                            # noqa: BLE001
        return None
    if not isinstance(rec, dict) or rec.get("schema") != SCHEMA:
        return None
    if kind is not None and rec.get("record") != kind:
        return None
    return rec


def band_for_facet(density_record_dict, facet_id):
    """1-based band number holding ``facet_id``'s density, or None."""
    for b in density_record_dict.get("bands") or []:
        if b.get("facet_id") == facet_id:
            return b.get("band")
    return None


def facet_label(entry, fallback_prefix="Facet"):
    """Human label for a facet/band entry, e.g. 'Valley, Cluster 2'."""
    cat = entry.get("category_label")
    clu = entry.get("cluster")
    if cat and clu:
        return "{}, Cluster {}".format(cat, clu)
    if cat:
        return str(cat)
    return "{} {}".format(fallback_prefix, entry.get("facet_id"))
