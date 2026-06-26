# -*- coding: utf-8 -*-
"""Reusable geodesic / geometry helpers for the Wildlife Tools toolbox.

This module is a faithful Python port of the relevant numeric routines from the
legacy ArcObjects/VB6 ``MyGeometricOperations`` module, cross-checked against the
already-ported C# in the SSI add-in (``MyGeometricOps.cs``). Keeping the math
identical preserves output parity with the original ArcMap tools.

Pure ``math`` / ``numpy`` only -- no arcpy -- so it can be unit-tested outside Pro
and reused by any Wildlife tool. Distances/areas are returned in spheroid units
(meters for WGS84 defaults).

Parity references (SSI MyGeometricOps.cs):
  - DistanceHaversineNumbers      -> haversine_distance        (:5638)
  - DistancePythagoreanNumbers    -> euclidean_distance        (:3969)
  - SphericalPolygonAreaNumbers   -> spherical_polygon_area    (:4148)
  - SphericalTriangleArea         -> _spherical_triangle_area  (:5964)
  - MultipointCentroidSphere      -> _multipoint_centroid_sphere (:4105)
  - SphericalLatLongToCart        -> _sph_latlong_to_cart      (:4813)
  - SphericalCartToLatLong        -> _sph_cart_to_latlong      (:4840)
"""

import math
import numpy as np

# WGS 1984 spheroid (matches the C# defaults).
EQUATORIAL_RADIUS = 6378137.000       # a
POLAR_RADIUS = 6356752.31424518       # b
# Mean radius used by the legacy Haversine routine (DistanceHaversineNumbers default).
HAVERSINE_RADIUS = 6371000.79000915

_TOL = 1e-12


# ---------------------------------------------------------------------------
# Distances
# ---------------------------------------------------------------------------
def haversine_distance(lat1, lon1, lat2, lon2, radius=HAVERSINE_RADIUS):
    """Great-circle distance(s) via the Haversine formula, in ``radius`` units.

    Vectorized: any argument may be a scalar or a NumPy array (broadcasting).
    Argument order (lat, lon) matches the legacy ``DistanceHaversineNumbers``.
    """
    lat1 = np.radians(lat1)
    lat2 = np.radians(lat2)
    dlat = lat1 - lat2
    dlon = np.radians(lon1 - lon2)
    t = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    return 2.0 * np.arctan2(np.sqrt(t), np.sqrt(np.maximum(0.0, 1.0 - t))) * radius


def euclidean_distance(x1, y1, x2, y2):
    """Planar (Pythagorean) distance(s). Vectorized."""
    return np.hypot(np.asarray(x2) - np.asarray(x1), np.asarray(y2) - np.asarray(y1))


# ---------------------------------------------------------------------------
# Spherical <-> Cartesian (unit sphere) helpers
# ---------------------------------------------------------------------------
def _sph_latlong_to_cart(lon, lat, radius=1.0):
    phi = math.radians(90.0 - lat)      # angle down from north pole
    theta = math.radians(lon)           # angle from Greenwich
    sin_phi = math.sin(phi)
    return (radius * sin_phi * math.cos(theta),
            radius * sin_phi * math.sin(theta),
            radius * math.cos(phi))


def _sph_cart_to_latlong(x, y, z):
    phi = math.atan2(math.sqrt(x * x + y * y), z)
    theta = math.atan2(y, x)
    return math.degrees(theta), 90.0 - math.degrees(phi)   # (lon, lat)


def _multipoint_centroid_sphere(ring):
    """Spherical centroid of a ring of (lon, lat) vertices.

    Mirrors the C# routine exactly, including that the (closed) ring's repeated
    closing vertex is part of the average.
    """
    n = len(ring)
    if n == 0:
        return float("nan"), float("nan")
    sx = sy = sz = 0.0
    for lon, lat in ring:
        x, y, z = _sph_latlong_to_cart(lon, lat)
        sx += x
        sy += y
        sz += z
    return _sph_cart_to_latlong(sx / n, sy / n, sz / n)


# ---------------------------------------------------------------------------
# Spherical (spheroid-corrected) polygon area -- Girard / l'Huilier
# ---------------------------------------------------------------------------
def _spherical_triangle_area(ax, ay, bx, by, cx, cy,
                             eq_radius=EQUATORIAL_RADIUS, pol_radius=POLAR_RADIUS):
    """Signed area of a spherical triangle (x=lon, y=lat in degrees), sq. meters.

    Side lengths are great-circle central angles (Haversine); the spherical
    excess E uses l'Huilier's formula; area = R^2 * E * orientation_sign, where R
    is the equal-volume sphere radius (a^2 * b)^(1/3). Sign encodes winding.
    Faithful port of SphericalTriangleArea.
    """
    # Degenerate (two coincident vertices) -> zero area.
    if ((abs(ax - bx) < _TOL and abs(ax - cx) < _TOL) or
            (abs(ax - bx) < _TOL and abs(ay - by) < _TOL) or
            (abs(ax - cx) < _TOL and abs(ay - cy) < _TOL) or
            (abs(bx - cx) < _TOL and abs(by - cy) < _TOL)):
        return 0.0

    # Two or more vertices at a pole -> zero area.
    poles = 0
    if abs(abs(ay) - 90.0) < _TOL:
        poles += 1
    if abs(abs(by) - 90.0) < _TOL:
        poles += 1
    if abs(abs(cy) - 90.0) < _TOL:
        poles += 1
    if poles > 1:
        return 0.0

    R = (eq_radius ** 2 * pol_radius) ** (1.0 / 3.0)   # equal-volume radius

    # --- side A-B (central angle) + initial azimuth A->B ---
    lat1 = math.radians(ay)
    lat2 = math.radians(by)
    dlon = math.radians(ax - bx)
    t = math.sin((lat1 - lat2) / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    ab = 2.0 * math.atan2(math.sqrt(t), math.sqrt(max(0.0, 1.0 - t)))
    az_ab = math.atan2(math.sin(-dlon) * math.cos(lat2),
                       math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(-dlon))

    # --- side B-C (central angle) ---
    lat1 = math.radians(by)
    lat2 = math.radians(cy)
    dlon = math.radians(bx - cx)
    t = math.sin((lat1 - lat2) / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    bc = 2.0 * math.atan2(math.sqrt(t), math.sqrt(max(0.0, 1.0 - t)))

    # --- side C-A (central angle) + initial azimuth A->C ---
    lat1 = math.radians(cy)
    lat2 = math.radians(ay)
    dlon = math.radians(cx - ax)
    t = math.sin((lat1 - lat2) / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    ca = 2.0 * math.atan2(math.sqrt(t), math.sqrt(max(0.0, 1.0 - t)))
    az_ac = math.atan2(math.sin(dlon) * math.cos(lat1),
                       math.cos(lat2) * math.sin(lat1) - math.sin(lat2) * math.cos(lat1) * math.cos(dlon))

    if az_ab < 0:
        az_ab += 2.0 * math.pi
    if az_ac < 0:
        az_ac += 2.0 * math.pi

    diff = az_ac - az_ab
    if diff > 0:
        mult = -1.0 if diff > math.pi else 1.0
    else:
        mult = 1.0 if abs(diff) > math.pi else -1.0

    s = (ab + bc + ca) / 2.0
    tan_e_over_4 = math.sqrt(math.tan(s / 2.0) * math.tan((s - ab) / 2.0)
                             * math.tan((s - bc) / 2.0) * math.tan((s - ca) / 2.0))
    e = math.atan(tan_e_over_4) * 4.0
    return R ** 2 * e * mult


def spherical_polygon_area(rings, eq_radius=EQUATORIAL_RADIUS, pol_radius=POLAR_RADIUS):
    """Signed spheroid-corrected area (sq. m) of a polygon given as closed rings.

    ``rings`` is an iterable of rings; each ring is a sequence of (lon, lat) pairs
    with the first vertex repeated as the last (closed). Exterior and interior
    rings contribute with opposite winding, so holes are subtracted naturally.
    The result is signed; callers that want a magnitude should take ``abs``.
    """
    total = 0.0
    for ring in rings:
        if len(ring) < 4:        # fewer than 3 distinct vertices -> no area
            continue
        cx, cy = _multipoint_centroid_sphere(ring)
        for i in range(len(ring) - 1):
            total += _spherical_triangle_area(ring[i][0], ring[i][1],
                                              ring[i + 1][0], ring[i + 1][1],
                                              cx, cy, eq_radius, pol_radius)
    return total


def geodesic_area(rings, eq_radius=EQUATORIAL_RADIUS, pol_radius=POLAR_RADIUS):
    """Magnitude of the spheroid-corrected polygon area (sq. m)."""
    return abs(spherical_polygon_area(rings, eq_radius, pol_radius))
