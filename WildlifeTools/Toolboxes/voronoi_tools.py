# -*- coding: utf-8 -*-
"""Voronoi (Thiessen) Polygons within Boundary.

Like Esri's Create Thiessen Polygons, with two differences that motivated
the tool: an optional BOUNDARY POLYGON (cells are clipped to a management
or analysis area instead of a bare rectangle) and NO Advanced license
requirement (the tessellation is scipy's, not ArcToolbox's). Frame rules
match Esri's exactly: the processing frame is the points' extent expanded
10% on each axis (union-ed with the boundary's extent when one is given),
and the EXTENT geoprocessing environment REPLACES that automatic frame --
the classic way to push edge effects outward.

TWO ENGINES, chosen by the OUTPUT coordinate system (the Output
Coordinate System environment, defaulting to the point feature class's
own CS):

* PROJECTED -- planar ``scipy.spatial.Voronoi`` in the output CS, exactly
  what Esri's tool computes.
* GEOGRAPHIC -- planar Voronoi in raw degrees would be distorted by the
  cos(latitude) anisotropy, so the tessellation is computed EXACTLY ON
  THE SPHERE instead: points become unit 3-D vectors, whose convex hull
  is the spherical Delaunay triangulation and whose dual is the Voronoi
  diagram with true great-circle bisectors (Brown 1979; scipy's
  ``SphericalVoronoi``). The authalic sphere is within ~0.5% of
  ellipsoidal geodesic distances; the optional ELLIPSOIDAL REFINEMENT
  then slides each densified cell-edge vertex along the local bisector
  normal until its two generating points are equidistant by true
  geodesic distance (Karney's algorithms via pyproj.Geod), giving
  ellipsoid-true boundaries to within the densification spacing.

PHANTOM GENERATORS solve boundedness and degeneracy in one stroke: a few
far-outside points are appended before tessellating, so every REAL cell
is finite (scipy leaves hull cells open otherwise) and the degenerate
inputs -- one point, two points, collinear points -- need no special
cases at all (one point simply owns the whole frame). Phantom cells are
discarded.

Coincident points are deduplicated within the XY tolerance (environment,
else the output CS default) with a warning, first-in wins -- Esri parity.
Multipoint features are exploded, each part generating its own cell
carrying the parent's attributes. Layer selections are honored on both
inputs. Points OUTSIDE the boundary still shape the cells inside it
(true clipped-Voronoi semantics) unless "use only points inside the
boundary" is chosen. Every cell carries ``Point_FID`` linking it to its
generating point's OID, plus any transferred attribute fields, plus an
optional geodesic-hectares ``Area_Ha``.

Dateline safety: longitudes are unwrapped about the data's circular-mean
longitude before any lon/lat geometry is assembled, so an Alaska point
set does not shatter at +/-180.
"""
import math
import os

import numpy as np

import mahalanobis as _mh
import clip_tools as _ct
import patch_analysis as _pa

_CITATION = (
    "Voronoi/Thiessen tessellation; spherical case via the convex-hull "
    "duality (Brown, K.Q. 1979, Information Processing Letters 9:223-228; "
    "implemented in scipy.spatial.SphericalVoronoi), ellipsoidal "
    "refinement via geodesics (Karney, C.F.F. 2013, Journal of Geodesy "
    "87:43-55, https://doi.org/10.1007/s00190-012-0578-z).")

# Densification of spherical cell edges, radians (~0.29 deg -> ~32 km
# max chord on Earth; clipping and refinement both operate on these
# samples, so this bounds the boundary-representation error).
_ARC_STEP_RAD = 0.005

# Frame padding factor for the phantom generators (times the frame's
# longer edge, beyond the frame on every side).
_PHANTOM_PAD = 4.0


def _say(messages, text):
    if messages is not None:
        messages.addMessage(text)


def _warn(messages, text):
    if messages is not None:
        messages.addWarningMessage(text)


# ---------------------------------------------------------------------------
#  small geometry helpers
# ---------------------------------------------------------------------------
def _project_geom(geom, out_sr, extent=None):
    """Project a geometry to ``out_sr``, honoring the Geographic
    Transformations environment (else Esri's best suggestion for the
    datum pair, extent-aware)."""
    import arcpy

    sr = geom.spatialReference
    if sr is None or _same_cs(sr, out_sr):
        return geom
    env_t = [t for t in str(arcpy.env.geographicTransformations or "")
             .split(";") if t]
    tfm, _fb = _ct.transformation_for(sr, out_sr, env_t, extent)
    return geom.projectAs(out_sr, tfm) if tfm else geom.projectAs(out_sr)


def _g_union(a, b):
    """``a.union(b)``, tolerating custom spatial references. arcpy's
    ``union`` raises ``ValueError: <geoprocessing describe geometry
    object ...>`` when the operands' SR has no authority code
    (factoryCode 0 -- e.g. the Repeating Shapes automatic LAEA, or a
    user's home-made projection); ``intersect``/``disjoint``/
    ``contains`` are unaffected (verified Pro 3.7). Fallback: strip to
    unknown-SR WKB, union planar, re-attach the SR. (WKB linearizes
    true curves; acceptable for a boundary merge.)"""
    import arcpy

    try:
        return a.union(b)
    except ValueError:
        res = arcpy.FromWKB(a.WKB).union(arcpy.FromWKB(b.WKB))
        return arcpy.FromWKB(res.WKB, a.spatialReference)


def _same_cs(a, b):
    try:
        if getattr(a, "factoryCode", 0) and getattr(b, "factoryCode", 0):
            return int(a.factoryCode) == int(b.factoryCode)
        return a.name == b.name
    except Exception:                                            # noqa: BLE001
        return False


def clean_sr(sr):
    """A copy of ``sr`` stripped of its dataset-specific XY domain,
    resolution and tolerance, for creating OUTPUT feature classes.
    A ``Describe(...).spatialReference`` carries its source dataset's
    XY domain along with it, and some datasets record a domain hugged
    tightly around their own data -- ``CreateFeatureclass`` then passes
    that cramped domain to the output, and any geometry reaching past
    it (shapes kept whole at a boundary, a Voronoi frame beyond the
    points, radials, arrays) fails to insert with ``RuntimeError: The
    coordinates or measures are out of bounds``. Rebuilding from the
    CS definition alone (the part before the first ';' of
    ``exportToString``) lets the geodatabase assign its default,
    effectively unlimited domain. None / plain strings / already-clean
    SRs pass through unharmed."""
    import arcpy

    try:
        if sr is None or not hasattr(sr, "exportToString"):
            return sr
        s = arcpy.SpatialReference()
        s.loadFromString(str(sr.exportToString()).split(";")[0])
        return s
    except Exception:                                            # noqa: BLE001
        return sr


def _rect_polygon(xmin, ymin, xmax, ymax, sr):
    import arcpy

    return arcpy.Polygon(arcpy.Array(
        [arcpy.Point(xmin, ymin), arcpy.Point(xmin, ymax),
         arcpy.Point(xmax, ymax), arcpy.Point(xmax, ymin),
         arcpy.Point(xmin, ymin)]), sr)


def _xy_tolerance(out_sr):
    """Coincident-point tolerance: the XY Tolerance environment when set,
    else the output CS's own default."""
    import arcpy

    v = arcpy.env.XYTolerance
    if v:
        try:
            parts = str(v).split()
            t = float(parts[0])
            # the env carries its own linear unit; convert to CS units
            # only for the meters-vs-feet-vs-degrees cases we can see
            if len(parts) > 1:
                unit = parts[1].lower()
                per_m = {"meters": 1.0, "meter": 1.0,
                         "feet": 0.3048, "foot": 0.3048,
                         "us-feet": 1200.0 / 3937.0}.get(unit)
                if per_m:
                    if out_sr.type == "Projected":
                        t = t * per_m / (out_sr.metersPerUnit or 1.0)
                    elif out_sr.type == "Geographic":
                        # a LINEAR env tolerance on a geographic output:
                        # meters -> degrees (raw meters read as degrees
                        # would dedupe ~111 km circles of points)
                        t = t * per_m / 111320.0
            return t
        except Exception:                                        # noqa: BLE001
            pass
    try:
        return float(out_sr.XYTolerance)
    except Exception:                                            # noqa: BLE001
        return 1e-9


# ---------------------------------------------------------------------------
#  input reading
# ---------------------------------------------------------------------------
def _read_points(in_points, out_sr, fields, messages):
    """(coords (N,2) in out_sr, oids (N,), rows list-of-tuples, notes).

    Explodes multipoints (each part carries the parent's OID and
    attributes); honors an active layer selection."""
    import arcpy

    try:
        path = arcpy.Describe(in_points).catalogPath
    except Exception:                                            # noqa: BLE001
        path = str(in_points)
    n_total, n_sel, has_sel = _mh.selection_info(in_points, path)
    if has_sel:
        _say(messages, "The point layer has an active selection: {} of {} "
                       "features are used.".format(n_sel, n_total))

    cur_fields = ["OID@", "SHAPE@"] + list(fields or [])

    # Datum handling ONCE, not per feature: same datum -> the cursor
    # projects every geometry in bulk (spatial_reference=); different
    # datum -> resolve the transformation once and projectAs per
    # feature with it (ListTransformations per feature took minutes on
    # 50 k points).
    try:
        src_sr = arcpy.Describe(in_points).spatialReference
    except Exception:                                            # noqa: BLE001
        src_sr = None
    cur_kwargs = {}
    tfm = None
    if src_sr is not None \
            and getattr(src_sr, "name", "Unknown") not in (None, "",
                                                           "Unknown") \
            and not _same_cs(src_sr, out_sr):
        ga = getattr(src_sr, "GCS", None)
        gb = getattr(out_sr, "GCS", None)
        if ga is not None and gb is not None and _same_cs(ga, gb):
            cur_kwargs = {"spatial_reference": out_sr}
        else:
            env_t = [t for t in str(arcpy.env.geographicTransformations
                                    or "").split(";") if t]
            try:
                tfm, _fb = _ct.transformation_for(src_sr, out_sr, env_t)
            except Exception:                                    # noqa: BLE001
                tfm = None
            if tfm is None:
                cur_kwargs = {"spatial_reference": out_sr}

    def _harvest(kwargs, transform):
        xs, ys, oids, rows = [], [], [], []
        n_multi = 0
        with arcpy.da.SearchCursor(in_points, cur_fields, **kwargs) as cur:
            for row in cur:
                geom = row[1]
                if geom is None:
                    continue
                attrs = tuple(row[2:])
                gsr = geom.spatialReference
                if gsr is not None and not _same_cs(gsr, out_sr):
                    if transform == "AUTO":
                        geom = _project_geom(geom, out_sr)
                    elif transform is not None:
                        geom = geom.projectAs(out_sr, transform)
                    else:
                        geom = geom.projectAs(out_sr)
                try:
                    parts = [p for p in geom.getPart()] \
                        if geom.isMultipart else [geom.getPart(0)]
                except Exception:                                # noqa: BLE001
                    parts = [geom.centroid]
                if len(parts) > 1:
                    n_multi += 1
                for p in parts:
                    if p is None:
                        continue
                    xs.append(p.X)
                    ys.append(p.Y)
                    oids.append(row[0])
                    rows.append(attrs)
        return xs, ys, oids, rows, n_multi

    try:
        xs, ys, oids, rows, n_multi = _harvest(cur_kwargs, tfm)
    except Exception:                                            # noqa: BLE001
        # fall back to the transformation-aware per-feature path
        xs, ys, oids, rows, n_multi = _harvest({}, "AUTO")
    if n_multi:
        _warn(messages,
              "{:,} multipoint feature{} exploded -- each part generates "
              "its own cell carrying the parent's attributes and "
              "Point_FID.".format(n_multi, " was" if n_multi == 1
                                  else "s were"))
    if not xs:
        raise arcpy.ExecuteError(
            "The point input contains no usable points (empty dataset, or "
            "an active selection selecting nothing).")
    return (np.column_stack([np.asarray(xs, dtype=np.float64),
                             np.asarray(ys, dtype=np.float64)]),
            np.asarray(oids), rows)


def _dedupe(coords, oids, rows, tol, messages):
    """Drop points within ``tol`` of an earlier point (first-in wins,
    Esri parity). Union-find over cKDTree pairs."""
    from scipy.spatial import cKDTree

    if len(coords) < 2 or tol <= 0:
        return coords, oids, rows
    pairs = cKDTree(coords).query_pairs(tol)
    if not pairs:
        return coords, oids, rows
    drop = set()
    for i, j in sorted(pairs):
        a, b = (i, j) if i < j else (j, i)
        if a not in drop:
            drop.add(b)
    keep = np.array([i for i in range(len(coords)) if i not in drop])
    _warn(messages,
          "{:,} coincident point{} (within the XY tolerance, {:g}) "
          "dropped -- the first point at each location wins.".format(
              len(drop), " was" if len(drop) == 1 else "s were", tol))
    return coords[keep], oids[keep], [rows[i] for i in keep]


def _read_boundary(boundary, out_sr, messages):
    """The (selected) boundary polygons unioned into ONE geometry in
    ``out_sr``, or None."""
    import arcpy

    if boundary in (None, ""):
        return None
    try:
        path = arcpy.Describe(boundary).catalogPath
    except Exception:                                            # noqa: BLE001
        path = str(boundary)
    n_total, n_sel, has_sel = _mh.selection_info(boundary, path)
    if has_sel:
        _say(messages, "The boundary layer has an active selection: {} of "
                       "{} polygons are used.".format(n_sel, n_total))
    geoms = []
    with arcpy.da.SearchCursor(boundary, ["SHAPE@"]) as cur:
        for (g,) in cur:
            if g is not None:
                geoms.append(_project_geom(g, out_sr))
    if not geoms:
        raise arcpy.ExecuteError(
            "The boundary input contains no polygons (empty dataset, or "
            "an active selection selecting nothing).")
    work = list(geoms)
    while len(work) > 1:                     # balanced union
        merged = [_g_union(work[i], work[i + 1])
                  for i in range(0, len(work) - 1, 2)]
        if len(work) % 2:
            merged.append(work[-1])
        work = merged
    return work[0]


# ---------------------------------------------------------------------------
#  frames
# ---------------------------------------------------------------------------
def _frame(coords, bnd_geom, out_sr, messages, tol=0.0):
    """(xmin, ymin, xmax, ymax): the Extent environment when set (Esri
    parity: it REPLACES the automatic frame), else union(boundary extent,
    points extent expanded 10% per axis)."""
    import arcpy

    env_ext = arcpy.env.extent
    if env_ext not in (None, "", "DEFAULT", "MAXOF", "MINOF"):
        try:
            esr = env_ext.spatialReference
            if esr is not None and esr.name not in (None, "", "Unknown") \
                    and not _same_cs(esr, out_sr):
                poly = _project_geom(env_ext.polygon, out_sr)
                e = poly.extent
            else:
                e = env_ext
            _say(messages, "Processing frame: the Extent geoprocessing "
                           "environment (replacing the automatic frame).")
            return e.XMin, e.YMin, e.XMax, e.YMax
        except Exception:                                        # noqa: BLE001
            pass
    xmin, ymin = coords[:, 0].min(), coords[:, 1].min()
    xmax, ymax = coords[:, 0].max(), coords[:, 1].max()
    # Data-scaled pad floors: one point or a collinear set must still get
    # a usable frame (an absolute 1e-9 floor collapses it to a sliver at
    # map-scale coordinates). Degenerate on BOTH axes -> pad by ~1 meter
    # equivalent or 1000x the XY tolerance, whichever is larger; one
    # degenerate axis -> borrow 10% of the other axis's range.
    xrng, yrng = xmax - xmin, ymax - ymin
    span = max(abs(xmin), abs(xmax), abs(ymin), abs(ymax), 1.0)
    eps = 1e-9 * span
    one_unit = (1.0 / 111320.0) if out_sr.type == "Geographic" else 1.0
    if xrng <= eps and yrng <= eps:
        padx = pady = max(one_unit, (tol or 0.0) * 1000.0)
    else:
        padx = 0.10 * (xrng if xrng > eps else max(yrng, one_unit))
        pady = 0.10 * (yrng if yrng > eps else max(xrng, one_unit))
    xmin, xmax = xmin - padx, xmax + padx
    ymin, ymax = ymin - pady, ymax + pady
    if bnd_geom is not None:
        be = bnd_geom.extent
        xmin, ymin = min(xmin, be.XMin), min(ymin, be.YMin)
        xmax, ymax = max(xmax, be.XMax), max(ymax, be.YMax)
    return xmin, ymin, xmax, ymax


# ---------------------------------------------------------------------------
#  planar engine
# ---------------------------------------------------------------------------
def _planar_cells(coords, frame, messages):
    """One arcpy-ready ring (list of (x, y)) per input point, bounded --
    phantom generators well outside the frame make every real cell
    finite and cure the 1-point/2-point/collinear degeneracies."""
    from scipy.spatial import Voronoi

    xmin, ymin, xmax, ymax = frame
    # floor scaled to the coordinates, not absolute -- 1e-9 at UTM
    # offsets would put the phantoms coincident with the frame
    pad = _PHANTOM_PAD * max(xmax - xmin, ymax - ymin,
                             1e-6 * max(abs(xmin), abs(ymin),
                                        abs(xmax), abs(ymax), 1.0))
    cx, cy = 0.5 * (xmin + xmax), 0.5 * (ymin + ymax)
    phantoms = [(cx - pad, cy - pad), (cx, cy - pad), (cx + pad, cy - pad),
                (cx - pad, cy), (cx + pad, cy),
                (cx - pad, cy + pad), (cx, cy + pad), (cx + pad, cy + pad)]
    pts = np.vstack([coords, np.asarray(phantoms)])
    # center before qhull: at map-scale offsets (UTM false eastings /
    # northings ~1e6) the lifted-paraboloid arithmetic silently merges
    # or drops Voronoi vertices; Voronoi is translation-equivariant,
    # so solve about the centroid and shift the vertices back
    mid = pts.mean(axis=0)
    vor = Voronoi(pts - mid)
    vverts = vor.vertices + mid
    rings = []
    for i in range(len(coords)):
        reg = vor.regions[vor.point_region[i]]
        if not reg or -1 in reg:
            rings.append(None)       # cannot happen with sane phantoms
            continue
        rings.append([(float(vverts[k, 0]),
                       float(vverts[k, 1])) for k in reg])
    return rings


# ---------------------------------------------------------------------------
#  spherical engine
# ---------------------------------------------------------------------------
def _lonlat_to_xyz(lon, lat):
    lam, phi = np.radians(lon), np.radians(lat)
    cp = np.cos(phi)
    return np.column_stack([cp * np.cos(lam), cp * np.sin(lam),
                            np.sin(phi)])


def _xyz_to_lonlat(v):
    v = np.atleast_2d(v)
    lon = np.degrees(np.arctan2(v[:, 1], v[:, 0]))
    lat = np.degrees(np.arcsin(np.clip(v[:, 2], -1.0, 1.0)))
    return lon, lat


def _unwrap_lon(lon, lon0):
    """Wrap longitudes into (lon0-180, lon0+180] so dateline-straddling
    data assembles into contiguous polygons."""
    return lon0 + (np.asarray(lon, dtype=np.float64) - lon0 + 180.0) \
        % 360.0 - 180.0


def _densify_if_curved(geom):
    """Densified copy when the polygon holds true curves (gdb buffer
    circles serialize as curveRings that vertex walks cannot see), else
    the polygon unchanged -- the standing curve rule."""
    if getattr(geom, "hasCurves", False):
        try:
            e = geom.extent
            d = max(math.hypot(e.width, e.height) / 500.0, 1e-9)
            return geom.densify("DISTANCE", d, d)
        except Exception:                                        # noqa: BLE001
            pass
    return geom


def _unwrap_polygon(poly, lon0, out_sr):
    """Rebuild a lon/lat polygon with every vertex longitude unwrapped
    into the lon0 window (dateline safety: unwrapped points against a
    RAW-longitude boundary made every cell east of the seam vanish).
    Returns the polygon unchanged when no vertex needs to move."""
    import arcpy

    try:
        g = _densify_if_curved(poly)
        rings = _pa.rings_of(g)
        if not rings:
            return poly
        if all(np.abs(_unwrap_lon(r[:, 0], lon0) - r[:, 0]).max() < 1e-12
               for r in rings):
            return poly
        arrs = []
        for r in rings:
            lon = _unwrap_lon(r[:, 0], lon0)
            arrs.append(arcpy.Array(
                [arcpy.Point(float(x), float(y))
                 for x, y in zip(lon, r[:, 1])]
                + [arcpy.Point(float(lon[0]), float(r[0, 1]))]))
        return arcpy.Polygon(arcpy.Array(arrs), out_sr)
    except Exception:                                            # noqa: BLE001
        return poly


def _points_in_boundary(coords, bnd, out_sr):
    """Boolean mask: which of the (N, 2) coords fall inside ``bnd``.
    Vectorized even-odd test over the boundary's edges (curves densified
    first); per-point arcpy ``disjoint`` only as a fallback."""
    import arcpy

    try:
        edges = _pa.edges_of(_pa.rings_of(_densify_if_curved(bnd)))
        if not len(edges):
            raise ValueError("boundary yielded no rings")
        return _pa.points_inside(coords, edges,
                                 bands=_pa.y_bands(edges))
    except Exception:                                            # noqa: BLE001
        return np.array([not bnd.disjoint(
            arcpy.PointGeometry(arcpy.Point(x, y), out_sr))
            for x, y in coords])


def _slerp(a, b, n):
    """n interior samples along the great-circle arc a->b (unit vectors),
    endpoints excluded."""
    dot = float(np.clip(np.dot(a, b), -1.0, 1.0))
    ang = math.acos(dot)
    if ang < 1e-12 or n < 1:
        return np.zeros((0, 3))
    t = np.linspace(0.0, 1.0, n + 2)[1:-1][:, None]
    sa = np.sin((1.0 - t) * ang) / math.sin(ang)
    sb = np.sin(t * ang) / math.sin(ang)
    out = sa * a[None, :] + sb * b[None, :]
    return out / np.linalg.norm(out, axis=1, keepdims=True)


def _spherical_cells(coords, lon0, refine, out_sr, messages, frame):
    """One densified lon/lat ring per input point (degrees, unwrapped
    about lon0). ``refine``: slide each edge sample to true ellipsoidal
    geodesic equidistance between the edge's two generators.

    The phantom generators sit on a ring JUST OUTSIDE the processing
    frame (plus the centroid's antipode), so every real cell stays
    compact and far from the +/-180-relative-to-lon0 unwrap seam -- a
    distant phantom ring left hull cells stretching most of the way
    around the globe, which tore into self-intersecting bowties when
    unwrapped (caught by the first test run)."""
    from scipy.spatial import SphericalVoronoi

    xyz = _lonlat_to_xyz(coords[:, 0], coords[:, 1])
    n_real = len(xyz)
    c = xyz.mean(axis=0)
    nc = np.linalg.norm(c)
    c = c / nc if nc > 1e-12 else np.array([1.0, 0.0, 0.0])
    ref = np.array([0.0, 0.0, 1.0]) if abs(c[2]) < 0.9 \
        else np.array([1.0, 0.0, 0.0])
    u = np.cross(c, ref)
    u /= np.linalg.norm(u)
    v = np.cross(c, u)

    fx0, fy0, fx1, fy1 = frame
    corners = _lonlat_to_xyz(
        np.array([fx0, fx0, fx1, fx1]), np.array([fy0, fy1, fy1, fy0]))
    r_frame = max(math.acos(float(np.clip(np.dot(c, k), -1.0, 1.0)))
                  for k in corners)
    phantoms = [-c]
    if r_frame < math.radians(60.0):
        r_ph = min(max(2.0 * r_frame, r_frame + math.radians(2.0)),
                   math.radians(80.0))
        for _try in range(4):
            ring = [math.cos(r_ph) * c + math.sin(r_ph)
                    * (math.cos(a) * u + math.sin(a) * v)
                    for a in np.linspace(0, 2 * math.pi, 12,
                                         endpoint=False)]
            lon_r, lat_r = _xyz_to_lonlat(np.asarray(ring))
            lon_r = _unwrap_lon(lon_r, lon0)
            inside = ((lon_r >= fx0) & (lon_r <= fx1)
                      & (lat_r >= fy0) & (lat_r <= fy1))
            if not inside.any() or r_ph >= math.radians(80.0):
                break
            r_ph = min(r_ph * 1.6, math.radians(80.0))
        phantoms += [r / np.linalg.norm(r) for r in ring]
    else:
        _warn(messages,
              "The processing frame spans a large share of the globe; "
              "cells of outermost points may be torn at the "
              "antimeridian opposite the data.")
    pts = np.vstack([xyz, np.asarray(phantoms)])
    if len(pts) < 4:
        # SphericalVoronoi needs at least four generators in general
        # position; a globe-spanning frame adds only the antipode
        # phantom, so 1-2 real points would feed it 2-3. Add the full
        # phantom ring anyway.
        ring = [math.cos(math.radians(80.0)) * c
                + math.sin(math.radians(80.0))
                * (math.cos(a) * u + math.sin(a) * v)
                for a in np.linspace(0, 2 * math.pi, 12, endpoint=False)]
        pts = np.vstack([pts, np.asarray([r / np.linalg.norm(r)
                                          for r in ring])])

    try:
        sv = SphericalVoronoi(pts, radius=1.0)
        sv.sort_vertices_of_regions()
    except Exception as e:                                       # noqa: BLE001
        import arcpy

        raise arcpy.ExecuteError(
            "The spherical Voronoi tessellation failed ({}). This "
            "usually means too few distinct points, or a degenerate "
            "arrangement (all points coincident).".format(e))

    # edge (vertex-id pair) -> generator indices sharing it, for the
    # refinement step
    geod = None
    if refine:
        geod = _make_geod(out_sr, messages)
    edge_gens = {}
    if geod is not None:
        for gi, reg in enumerate(sv.regions):
            for k in range(len(reg)):
                key = tuple(sorted((reg[k], reg[(k + 1) % len(reg)])))
                edge_gens.setdefault(key, []).append(gi)

    # Each shared edge is densified (and refined) ONCE, cached by its
    # sorted vertex-id pair and reversed for the neighboring cell; each
    # shared CORNER vertex is refined once globally (first touch wins),
    # so both incident cells carry the identical corner coordinate --
    # per-cell refinement diverged by centimeters and left sliver gaps
    # at the three-cell junctions.
    edge_cache = {}
    corner_cache = {}
    rings = []
    for i in range(n_real):
        reg = sv.regions[i]
        if not reg:
            rings.append(None)
            continue
        ring_pts = []
        for k in range(len(reg)):
            va, vb = reg[k], reg[(k + 1) % len(reg)]
            key = (va, vb) if va < vb else (vb, va)
            seg = edge_cache.get(key)
            if seg is None:
                lo, hi = key
                a, b = sv.vertices[lo], sv.vertices[hi]
                dot = float(np.clip(np.dot(a, b), -1.0, 1.0))
                n_mid = int(math.acos(dot) / _ARC_STEP_RAD)
                seg = np.vstack([a[None, :], _slerp(a, b, n_mid),
                                 b[None, :]])
                if geod is not None:
                    gens = [g for g in (edge_gens.get(key) or [])
                            if g < n_real]
                    if len(gens) == 2:
                        seg = _refine_arc(seg, coords[gens[0]],
                                          coords[gens[1]], lon0, geod)
                        for vid, row in ((lo, 0), (hi, len(seg) - 1)):
                            cached = corner_cache.get(vid)
                            if cached is None:
                                corner_cache[vid] = seg[row].copy()
                            else:
                                seg[row] = cached
                edge_cache[key] = seg
            use = seg if va == key[0] else seg[::-1]
            ring_pts.append(use[:-1])       # next edge supplies its end
        allv = np.vstack(ring_pts)
        lon, lat = _xyz_to_lonlat(allv)
        lon = _unwrap_lon(lon, lon0)
        rings.append(list(zip(lon.tolist(), lat.tolist())))
    return rings


class _ArcGeod(object):
    """A pyproj.Geod-shaped shim over arcpy's own (Karney-backed)
    geodesic geometry methods, for when pyproj is not importable in the
    active Pro environment. Azimuths are degrees from north, matching
    pyproj's convention."""

    def __init__(self, sr):
        self._sr = sr

    def inv(self, lon1, lat1, lon2, lat2):
        import arcpy

        if np.ndim(lon1):                    # array form, pyproj-style
            az = np.empty(len(lon1), dtype=np.float64)
            dist = np.empty(len(lon1), dtype=np.float64)
            for i in range(len(lon1)):
                az[i], _r, dist[i] = self.inv(
                    float(lon1[i]), float(lat1[i]),
                    float(lon2[i]), float(lat2[i]))
            return az, None, dist
        pg = arcpy.PointGeometry(arcpy.Point(lon1, lat1), self._sr)
        ang, dist = pg.angleAndDistanceTo(
            arcpy.PointGeometry(arcpy.Point(lon2, lat2), self._sr),
            "GEODESIC")
        return ang, None, dist

    def fwd(self, lon, lat, az, s):
        import arcpy

        if np.ndim(lon):                     # array form, pyproj-style
            ox = np.empty(len(lon), dtype=np.float64)
            oy = np.empty(len(lon), dtype=np.float64)
            for i in range(len(lon)):
                ox[i], oy[i], _b = self.fwd(
                    float(lon[i]), float(lat[i]),
                    float(az[i]), float(s[i]))
            return ox, oy, None
        pg = arcpy.PointGeometry(arcpy.Point(lon, lat), self._sr)
        out = pg.pointFromAngleAndDistance(az, s, "GEODESIC")
        p = out.firstPoint
        return p.X, p.Y, None


def _make_geod(out_sr, messages):
    """A geodesic solver on the output CS's ellipsoid: pyproj.Geod when
    importable, else the arcpy shim (slower per call, same Karney
    algorithms), else None (warned)."""
    try:
        import pyproj

        a = float(out_sr.semiMajorAxis)
        b = float(out_sr.semiMinorAxis)
        return pyproj.Geod(a=a, b=b)
    except Exception:                                            # noqa: BLE001
        pass
    try:
        g = _ArcGeod(out_sr)
        g.inv(0.0, 0.0, 1.0, 1.0)          # probe
        _say(messages, "Ellipsoidal refinement uses arcpy's geodesic "
                       "engine (pyproj is not available in this Python "
                       "environment).")
        return g
    except Exception as e:                                       # noqa: BLE001
        _warn(messages,
              "Ellipsoidal refinement unavailable ({}); cells are exact "
              "on the authalic sphere (within ~0.5% of geodesic "
              "distances).".format(e))
        return None


def _refine_arc(seg_xyz, gen_a, gen_b, lon0, geod):
    """Slide each sampled arc vertex (unit xyz) to geodesic equidistance
    between generators a and b (lon/lat degrees). Secant iteration along
    the local bisector normal; returns refined lon/lat pairs re-encoded
    as xyz so the caller's pipeline is unchanged."""
    lon, lat = _xyz_to_lonlat(seg_xyz)
    px = _unwrap_lon(lon, lon0).astype(np.float64)
    py = np.asarray(lat, dtype=np.float64).copy()
    n = len(px)
    la = np.full(n, float(gen_a[0]))
    pa = np.full(n, float(gen_a[1]))
    lb = np.full(n, float(gen_b[0]))
    pb = np.full(n, float(gen_b[1]))
    # Newton with a FRESH gradient each iteration: f = d(v,a)-d(v,b);
    # along the unit direction u = (t_a - t_b)/|t_a - t_b| (t_* = the
    # unit tangent from v toward each generator), df/ds = -|t_a-t_b|,
    # so the Newton step is s = f/|t_a - t_b|. A stale-direction
    # secant left ~25 m residuals; this converges to <1 cm in a few
    # iterations. All vertices of the segment iterate together through
    # ARRAY-form geod calls (scalar per-vertex solves dominated the
    # refinement's runtime), dropping out as they converge.
    active = np.ones(n, dtype=bool)
    for _ in range(8):
        idx = np.flatnonzero(active)
        if idx.size == 0:
            break
        az_a, _r1, d_a = geod.inv(px[idx], py[idx], la[idx], pa[idx])
        az_b, _r2, d_b = geod.inv(px[idx], py[idx], lb[idx], pb[idx])
        f = np.asarray(d_a, dtype=np.float64) \
            - np.asarray(d_b, dtype=np.float64)
        ra = np.radians(np.asarray(az_a, dtype=np.float64))
        rb = np.radians(np.asarray(az_b, dtype=np.float64))
        dvx = np.sin(ra) - np.sin(rb)
        dvy = np.cos(ra) - np.cos(rb)
        nd = np.hypot(dvx, dvy)
        move = (np.abs(f) >= 0.01) & (nd >= 1e-9)   # 1 cm is plenty
        active[idx] = move
        if not move.any():
            break
        mi = idx[move]
        az_move = np.degrees(np.arctan2(dvx[move], dvy[move]))
        nx, ny, _az = geod.fwd(px[mi], py[mi], az_move,
                               f[move] / nd[move])
        px[mi] = np.asarray(nx, dtype=np.float64)
        py[mi] = np.asarray(ny, dtype=np.float64)
    px = _unwrap_lon(px, lon0)
    return _lonlat_to_xyz(px, py)


# ---------------------------------------------------------------------------
#  orchestrator
# ---------------------------------------------------------------------------
def run_voronoi(in_points, out_fc, boundary=None, fields=None,
                only_inside=False, refine_ellipsoid=False,
                add_area=False, messages=None):
    """Build the clipped Voronoi feature class. Returns the output path."""
    import arcpy

    fields = [f for f in (fields or []) if f]
    desc = arcpy.Describe(in_points)
    src_sr = desc.spatialReference
    if src_sr is None or getattr(src_sr, "name", "Unknown") == "Unknown":
        raise arcpy.ExecuteError(
            "The point input has no defined coordinate system.")
    out_sr = arcpy.env.outputCoordinateSystem or src_sr
    geographic = out_sr.type == "Geographic"

    _mh.begin_phases([("Reading the inputs...", 2),
                      ("Building the Voronoi tessellation...", 3),
                      ("Clipping and writing the cells...", 4)])
    _say(messages,
         "Output coordinate system: {} ({}); the tessellation runs {}."
         .format(out_sr.name,
                 "geographic" if geographic else "projected",
                 "exactly on the sphere (great-circle bisectors)"
                 if geographic else "planar in the output CS"))

    # ---- inputs ---------------------------------------------------------
    field_names = _validate_fields(in_points, fields)
    coords, oids, rows = _read_points(in_points, out_sr, field_names,
                                      messages)
    bnd = _read_boundary(boundary, out_sr, messages)
    tol = _xy_tolerance(out_sr)
    coords, oids, rows = _dedupe(coords, oids, rows, tol, messages)

    # Geographic: unwrap longitudes about the data's circular-mean
    # longitude ONCE, up front -- the frame, the clip rectangle, the
    # tessellation, and the written coordinates all live in this window,
    # so a dateline-straddling point set stays contiguous (raw lons for
    # such data would make the "extent" span nearly the whole globe).
    lon0 = 0.0
    if geographic:
        lon0 = math.degrees(math.atan2(
            np.sin(np.radians(coords[:, 0])).mean(),
            np.cos(np.radians(coords[:, 0])).mean()))
        coords[:, 0] = _unwrap_lon(coords[:, 0], lon0)
        if bnd is not None:
            # the boundary must live in the same lon0 window as the
            # points, or the membership tests and the clip both miss
            # everything east of the seam
            bnd = _unwrap_polygon(bnd, lon0, out_sr)

    inside_mask = (_points_in_boundary(coords, bnd, out_sr)
                   if bnd is not None else None)
    if only_inside:
        if bnd is None:
            _warn(messages, "'Use only points inside the boundary' is set "
                            "but no boundary was given; ignored.")
        else:
            keep = inside_mask
            n_out = int((~keep).sum())
            if n_out == len(coords):
                raise arcpy.ExecuteError(
                    "Every point lies outside the boundary polygon.")
            if n_out:
                _say(messages,
                     "{:,} point{} outside the boundary excluded from the "
                     "tessellation.".format(n_out,
                                            "" if n_out == 1 else "s"))
                coords = coords[keep]
                oids = oids[keep]
                rows = [r for r, k in zip(rows, keep) if k]
    elif bnd is not None:
        n_out = int((~inside_mask).sum())
        if n_out:
            _say(messages,
                 "{:,} point{} outside the boundary still shape the "
                 "cells inside it (true clipped-Voronoi semantics; check "
                 "'use only points inside the boundary' to exclude "
                 "them).".format(n_out, " lies" if n_out == 1 else "s lie"))

    frame = _frame(coords, bnd, out_sr, messages, tol=tol)
    _say(messages, "{:,} generating point{}; frame {:.6g}, {:.6g} to "
                   "{:.6g}, {:.6g}.".format(
                       len(coords), "" if len(coords) == 1 else "s",
                       frame[0], frame[1], frame[2], frame[3]))
    _mh.phase_advance()

    # ---- tessellation ---------------------------------------------------
    if geographic:
        rings = _spherical_cells(coords, lon0, refine_ellipsoid, out_sr,
                                 messages, frame)
        if refine_ellipsoid:
            _say(messages, "Cell edges refined to ellipsoidal geodesic "
                           "equidistance.")
    else:
        rings = _planar_cells(coords, frame, messages)
    _mh.phase_advance()

    # ---- output fc ------------------------------------------------------
    out_path, out_names, out_types = _create_output(
        in_points, out_fc, out_sr, field_names, add_area, messages)
    is_shp = out_path.lower().endswith(".shp")
    clipper = _rect_polygon(*frame, out_sr)
    if bnd is not None:
        clipper = clipper.intersect(bnd, 4)
        if clipper is None or clipper.area == 0:
            raise arcpy.ExecuteError(
                "The boundary polygon does not overlap the processing "
                "frame at all.")

    ins_fields = ["SHAPE@", "Point_FID"] + list(out_names)
    if add_area:
        ins_fields.append("Area_Ha")
    n_written = 0
    n_empty = 0
    with arcpy.da.InsertCursor(out_path, ins_fields) as ins:
        for i, ring in enumerate(rings):
            if (i + 1) % 200 == 0:
                _mh.phase_step(i + 1, len(rings))
            if not ring:
                n_empty += 1
                continue
            cell = arcpy.Polygon(
                arcpy.Array([arcpy.Point(x, y) for x, y in ring]
                            + [arcpy.Point(ring[0][0], ring[0][1])]),
                out_sr)
            clipped = cell.intersect(clipper, 4)
            if clipped is None or clipped.area == 0:
                n_empty += 1
                continue
            attrs = list(rows[i])
            if is_shp:
                # shapefiles cannot store nulls: -999 for numbers,
                # empty string for text (dates pass through)
                attrs = [("" if t == "TEXT" else
                          v if t == "DATE" else -999)
                         if v is None else v
                         for v, t in zip(attrs, out_types)]
            vals = [clipped, int(oids[i])] + attrs
            if add_area:
                try:
                    vals.append(clipped.getArea("GEODESIC", "HECTARES"))
                except Exception:                                # noqa: BLE001
                    vals.append(-999 if is_shp else None)
            ins.insertRow(vals)
            n_written += 1
    if n_empty:
        _say(messages,
             "{:,} cell{} entirely outside the boundary/frame and were "
             "not written (their points lie outside the clipped "
             "area).".format(n_empty, " was" if n_empty == 1 else "s were"))
    _say(messages, "{:,} Voronoi cell{} written.".format(
        n_written, "" if n_written == 1 else "s"))

    _stamp(out_path, in_points, boundary, geographic, refine_ellipsoid,
           n_written)
    _mh.end_phases()
    return out_path


def _validate_fields(in_points, fields):
    """The transferable subset of the requested field names, in order."""
    import arcpy

    if not fields:
        return []
    have = {f.name.lower(): f for f in arcpy.ListFields(in_points)}
    out = []
    for name in fields:
        f = have.get(str(name).lower())
        if f is not None and f.type not in ("OID", "Geometry", "Blob",
                                            "Raster"):
            out.append(f.name)
    return out


_TYPE_MAP = {
    "SmallInteger": "SHORT", "Integer": "LONG", "BigInteger": "BIGINTEGER",
    "Single": "FLOAT", "Double": "DOUBLE", "String": "TEXT",
    "Date": "DATE", "GUID": "GUID", "GlobalID": "GUID",
}


def _create_output(in_points, out_fc, out_sr, field_names, add_area,
                   messages):
    """Create the polygon FC (folder -> .shp per the standing rule) and
    add every field in ONE AddFields call. Returns (path, OUTPUT names of
    the transfer fields, AddFields type per transfer field) -- shapefiles
    truncate names to 10 characters, so the insert cursor must use the
    names the output actually holds, not the source names."""
    import arcpy

    out_fc = str(out_fc).replace("/", "\\").rstrip("\\")
    ws, name = os.path.split(out_fc)
    is_shp = ws and not _mh.in_gdb_container(out_fc)
    if is_shp and not name.lower().endswith(".shp"):
        name += ".shp"
    res = arcpy.management.CreateFeatureclass(
        ws, name, "POLYGON", spatial_reference=clean_sr(out_sr))
    out_path = res.getOutput(0)

    src = {f.name: f for f in arcpy.ListFields(in_points)}
    used = {"fid", "shape", "oid", "objectid", "point_fid", "area_ha"}
    rows = [["Point_FID", "LONG", "Point FID", "", "", ""]]
    out_names, out_types = [], []
    for fn in field_names:
        f = src[fn]
        ftype = _TYPE_MAP.get(f.type, "TEXT")
        oname = f.name
        if is_shp and (len(oname) > 10 or oname.lower() in used):
            oname = oname[:10]
            k = 0
            while oname.lower() in used:
                k += 1
                tag = "_{}".format(k)
                oname = f.name[:10 - len(tag)] + tag
            _warn(messages,
                  "Shapefile field-name limit: '{}' is written as "
                  "'{}'.".format(f.name, oname))
        used.add(oname.lower())
        rows.append([oname, ftype, f.aliasName or f.name,
                     f.length if ftype == "TEXT" else "", "", ""])
        out_names.append(oname)
        out_types.append(ftype)
    if add_area:
        rows.append(["Area_Ha", "DOUBLE", "Area (geodesic hectares)",
                     "", "", ""])
    arcpy.management.AddFields(out_path, rows)
    # belt-and-suspenders: address the fields by the names the output
    # dataset ACTUALLY holds after any driver-side renaming
    have = {f.name.lower(): f.name for f in arcpy.ListFields(out_path)}
    out_names = [have.get(n.lower(), n) for n in out_names]
    return out_path, out_names, out_types


def _stamp(out_path, in_points, boundary, geographic, refined, n_cells):
    import arcpy

    try:
        md = arcpy.metadata.Metadata(out_path)
        md.title = os.path.basename(str(out_path))
        md.summary = (
            "Created by Voronoi (Thiessen) Polygons within Boundary: {n} "
            "cell(s) from {src}{bnd}, computed {how}{ref}.".format(
                n=n_cells, src=str(in_points),
                bnd=(", clipped to " + str(boundary)) if boundary else
                    " within the automatic frame",
                how=("exactly on the sphere (great-circle bisectors)"
                     if geographic else "planar in the output CS"),
                ref=(", refined to ellipsoidal geodesics"
                     if refined else "")))
        md.description = (
            "Voronoi (Thiessen) polygons: each cell is the region closer "
            "to its generating point than to any other. Optionally "
            "clipped to a boundary polygon; needs no Advanced license. "
            + _CITATION)
        md.tags = ("Voronoi, Thiessen, tessellation, proximity, "
                   "Jenness Enterprises")
        md.credits = ("Tool by Jeff Jenness, Jenness Enterprises "
                      "(http://www.jennessent.com). Method: " + _CITATION)
        md.save()
    except Exception:                                            # noqa: BLE001
        pass
