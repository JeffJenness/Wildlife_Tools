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

"""Synthetic-DEM test of the geodesic ground geometry (2026-10-16).

One physical surface per site -- a plane tilted 10 degrees toward azimuth
60 plus two Gaussian hills, defined as a function of longitude and latitude
-- is written as a DEM in several coordinate systems. Every DEM tool is run
on every DEM and checked (a) against the analytic slope and aspect, (b)
against Esri's own planar Slope / Aspect on a true-to-scale UTM grid
(identity), and (c) across coordinate systems at the same physical points.
Run with ArcGIS Pro's python. Writes only under its own scratch folder."""
import math
import os
import shutil
import sys
import time
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TBX = os.path.join(os.path.dirname(HERE), "WildlifeTools", "Toolboxes")
sys.path.insert(0, TBX)
import tempfile  # noqa: E402
OUT = os.path.join(tempfile.gettempdir(), "wft_geodesic_dem_test")

import arcpy  # noqa: E402
from osgeo import gdal, osr  # noqa: E402

import hillshade as hs  # noqa: E402
import topographic_tools as tt  # noqa: E402
import aspect_tools as at  # noqa: E402
import tpi_tools  # noqa: E402
import lsri  # noqa: E402
import line_surface_tools as lst  # noqa: E402

PASS = FAIL = 0
NOTES = []


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS  {}  {}".format(name, detail))
    else:
        FAIL += 1
        print("  FAIL  {}  {}".format(name, detail))


class Msgs(object):
    def __init__(self):
        self.lines, self.warnings, self.errors = [], [], []

    def addMessage(self, s):
        self.lines.append(str(s))

    def addWarningMessage(self, s):
        self.warnings.append(str(s))

    def addErrorMessage(self, s):
        self.errors.append(str(s))


# ------------------------------------------------------------- the surface
A_WGS, F_WGS = 6378137.0, 1.0 / 298.257223563
E2 = F_WGS * (2 - F_WGS)


def radii(lat_deg):
    s = math.sin(math.radians(lat_deg))
    N = A_WGS / math.sqrt(1 - E2 * s * s)
    M = A_WGS * (1 - E2) / (1 - E2 * s * s) ** 1.5
    return N, M


class Site(object):
    def __init__(self, name, lon0, lat0, slope_deg=10.0, downhill_az=60.0,
                 hills=((1500.0, 1000.0, 150.0, 800.0),
                        (-2000.0, -1500.0, 120.0, 700.0))):
        self.name, self.lon0, self.lat0 = name, lon0, lat0
        N0, M0 = radii(lat0)
        self.kx = N0 * math.cos(math.radians(lat0))    # m per radian of lon
        self.ky = M0                                   # m per radian of lat
        t = math.tan(math.radians(slope_deg))
        az = math.radians(downhill_az)
        self.gx = -t * math.sin(az)                    # uphill gradient, east
        self.gy = -t * math.cos(az)                    # uphill gradient, north
        self.hills = hills

    def local_xy(self, lon, lat):
        x = np.radians(np.asarray(lon) - self.lon0) * self.kx
        y = np.radians(np.asarray(lat) - self.lat0) * self.ky
        return x, y

    def z(self, lon, lat):
        x, y = self.local_xy(lon, lat)
        z = 1000.0 + self.gx * x + self.gy * y
        for hx, hy, amp, sig in self.hills:
            z = z + amp * np.exp(-((x - hx) ** 2 + (y - hy) ** 2) / (2 * sig * sig))
        return z

    def grad_true(self, lon, lat):
        """True ground gradient (east, north; m/m) of the surface at
        (lon, lat): the equirectangular gradient corrected by the ratio of
        the local to the reference metric."""
        x, y = self.local_xy(lon, lat)
        gx = np.full(np.shape(x), self.gx, dtype=np.float64)
        gy = np.full(np.shape(y), self.gy, dtype=np.float64)
        for hx, hy, amp, sig in self.hills:
            g = amp * np.exp(-((x - hx) ** 2 + (y - hy) ** 2) / (2 * sig * sig))
            gx = gx - (x - hx) / (sig * sig) * g
            gy = gy - (y - hy) / (sig * sig) * g
        lat = np.asarray(lat, dtype=np.float64)
        s = np.sin(np.radians(lat))
        N = A_WGS / np.sqrt(1 - E2 * s * s)
        M = A_WGS * (1 - E2) / (1 - E2 * s * s) ** 1.5
        rx = self.kx / (N * np.cos(np.radians(lat)))   # d(local x)/d(true east)
        ry = self.ky / M
        return gx * rx, gy * ry

    def slope_aspect(self, lon, lat):
        gx, gy = self.grad_true(lon, lat)
        slope = np.degrees(np.arctan(np.hypot(gx, gy)))
        aspect = np.degrees(np.arctan2(-gx, -gy)) % 360.0     # downhill compass
        return slope, aspect


# ------------------------------------------------------------ coordinate systems
def albers_wgs84():
    sr = arcpy.SpatialReference(102003)
    s = sr.exportToString().replace(
        'GEOGCS["GCS_North_American_1983",DATUM["D_North_American_1983",'
        'SPHEROID["GRS_1980",6378137.0,298.257222101]]',
        'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",'
        '6378137.0,298.257223563]]')
    out = arcpy.SpatialReference()
    out.loadFromString(s)
    assert out.GCS.name == "GCS_WGS_1984"
    return out


def osr_from_arcpy(sr):
    srs = osr.SpatialReference()
    srs.ImportFromWkt(sr.exportToString())
    try:
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    except Exception:
        pass
    return srs


GCS = osr.SpatialReference()
GCS.ImportFromEPSG(4326)
try:
    GCS.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
except Exception:
    pass


class CS(object):
    def __init__(self, label, sr):
        self.label = label
        self.sr = sr
        self.geographic = (sr.type == "Geographic")
        self.srs = osr_from_arcpy(sr)
        self.fwd = osr.CoordinateTransformation(GCS, self.srs)
        self.inv = osr.CoordinateTransformation(self.srs, GCS)

    def project(self, lon, lat):
        pts = self.fwd.TransformPoints(list(zip(np.ravel(lon).tolist(), np.ravel(lat).tolist())))
        x = np.array([p[0] for p in pts]).reshape(np.shape(lon))
        y = np.array([p[1] for p in pts]).reshape(np.shape(lon))
        return x, y

    def unproject(self, x, y):
        pts = self.inv.TransformPoints(list(zip(np.ravel(x).tolist(), np.ravel(y).tolist())))
        lon = np.array([p[0] for p in pts]).reshape(np.shape(x))
        lat = np.array([p[1] for p in pts]).reshape(np.shape(x))
        return lon, lat

    def convergence(self, lon, lat):
        """Grid convergence (degrees, clockwise from true north to grid
        north) at (lon, lat): the bearing of a small northward step in the
        projected plane. Aspects computed on a projected grid -- by this
        add-in and by Esri's Aspect tool alike -- are measured from GRID
        north; add this to get true-north aspects."""
        if self.geographic:
            return np.zeros(np.shape(lon))
        x0, y0 = self.project(lon, lat)
        x1, y1 = self.project(lon, np.asarray(lat) + 1e-4)
        return np.degrees(np.arctan2(x1 - x0, y1 - y0))


# ------------------------------------------------------------------ fixtures
def write_dem(site, cs, cell_m, half_km, path):
    """DEM of ``site`` in ``cs``: cells of ``cell_m`` nominal meters (or the
    equivalent degrees), covering +/- half_km around the site center."""
    if cs.geographic:
        N0, M0 = radii(site.lat0)
        dlat = cell_m / (math.pi / 180 * M0)
        dlon = cell_m / (math.pi / 180 * N0 * math.cos(math.radians(site.lat0)))
        n = int(round(2 * half_km * 1000 / cell_m))
        gt = (site.lon0 - n / 2 * dlon, dlon, 0.0, site.lat0 + n / 2 * dlat, 0.0, -dlat)
        W = H = n
        cols = np.arange(W) + 0.5
        rows = np.arange(H) + 0.5
        lon = gt[0] + cols[None, :] * dlon + 0 * rows[:, None]
        lat = gt[3] - rows[:, None] * dlat + 0 * cols[None, :]
    else:
        xc, yc = cs.project(np.array([site.lon0]), np.array([site.lat0]))
        xc, yc = float(xc[0]), float(yc[0])
        # cover +/- half_km of GROUND: project the ground square's corners
        # and take their bounding box (a Web Mercator cell at 75 N is only a
        # quarter of its nominal size on the ground)
        dlon = half_km * 1000 / site.kx * 180 / math.pi
        dlat = half_km * 1000 / site.ky * 180 / math.pi
        cx, cy = cs.project(np.array([site.lon0 - dlon, site.lon0 + dlon, site.lon0 + dlon, site.lon0 - dlon]),
                            np.array([site.lat0 - dlat, site.lat0 - dlat, site.lat0 + dlat, site.lat0 + dlat]))
        half_x = max(abs(cx - xc).max(), 1.0)
        half_y = max(abs(cy - yc).max(), 1.0)
        W = int(math.ceil(2 * half_x / cell_m))
        H = int(math.ceil(2 * half_y / cell_m))
        gt = (xc - W / 2 * cell_m, cell_m, 0.0, yc + H / 2 * cell_m, 0.0, -cell_m)
        xs = gt[0] + (np.arange(W) + 0.5) * cell_m
        ys = gt[3] - (np.arange(H) + 0.5) * cell_m
        X, Y = np.meshgrid(xs, ys)
        lon, lat = cs.unproject(X, Y)
    z = site.z(lon, lat).astype(np.float32)
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(path, W, H, 1, gdal.GDT_Float32)
    ds.SetGeoTransform(gt)
    ds.SetProjection(cs.srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(-9999.0)
    band.WriteArray(z)
    band.FlushCache()
    ds = None
    return path, gt, W, H


def make_points(gdb, name, cs, lon, lat):
    fc = os.path.join(gdb, name)
    if arcpy.Exists(fc):
        arcpy.management.Delete(fc)
    arcpy.management.CreateFeatureclass(gdb, name, "POINT", spatial_reference=cs.sr)
    if cs.geographic:
        x, y = np.asarray(lon), np.asarray(lat)
    else:
        x, y = cs.project(lon, lat)
    with arcpy.da.InsertCursor(fc, ["SHAPE@XY"]) as cur:
        for xi, yi in zip(x, y):
            cur.insertRow([(float(xi), float(yi))])
    return fc, x, y


def field_values(fc, fld):
    out = []
    with arcpy.da.SearchCursor(fc, ["OID@", fld], sql_clause=(None, "ORDER BY OBJECTID")) as cur:
        for oid, v in cur:
            out.append(np.nan if v is None else float(v))
    return np.array(out)


def sample_tif(path, x, y):
    ds = gdal.Open(path)
    gt = ds.GetGeoTransform()
    arr = ds.GetRasterBand(1).ReadAsArray().astype(np.float64)
    nod = ds.GetRasterBand(1).GetNoDataValue()
    ds = None
    c = np.floor((np.asarray(x) - gt[0]) / gt[1]).astype(int)
    r = np.floor((gt[3] - np.asarray(y)) / abs(gt[5])).astype(int)
    ok = (c >= 0) & (c < arr.shape[1]) & (r >= 0) & (r < arr.shape[0])
    v = np.full(len(c), np.nan)
    v[ok] = arr[r[ok], c[ok]]
    if nod is not None:
        v[v == nod] = np.nan
    return v


def circ_diff(a, b):
    d = np.abs((np.asarray(a) - np.asarray(b) + 180.0) % 360.0 - 180.0)
    return d


def read_dem(path):
    ds = gdal.Open(path)
    gt = ds.GetGeoTransform()
    proj = ds.GetProjection()
    arr = ds.GetRasterBand(1).ReadAsArray().astype(np.float64)
    ds = None
    return arr, gt, proj


# ------------------------------------------------------------------ the runs
def run_site(site, systems, gdb, tag):
    print("\n=== Site {} (lat {:.1f}) ===".format(site.name, site.lat0))
    rng = np.random.RandomState(7)
    npts = 150
    half_km = 5.0
    cell = 30.0
    # physical points within +/- 3.5 km (so every window fits)
    lon_p = site.lon0 + rng.uniform(-3500, 3500, npts) / site.kx * 180 / math.pi
    lat_p = site.lat0 + rng.uniform(-3500, 3500, npts) / site.ky * 180 / math.pi
    slope_ref, aspect_ref = site.slope_aspect(lon_p, lat_p)
    results = {}          # cs label -> dict of arrays at the physical points
    dems = {}
    for cs in systems:
        print("\n--- {} ---".format(cs.label))
        dem_path = os.path.join(OUT, "{}_{}.tif".format(tag, cs.label))
        t0 = time.time()
        write_dem(site, cs, cell, half_km, dem_path)
        dems[cs.label] = dem_path
        fc, px, py = make_points(gdb, "pts_{}_{}".format(tag, cs.label), cs, lon_p, lat_p)
        res = {}
        results[cs.label] = res

        # ---- A. whole-grid slope (tpi_tools.slope_grid -> _twi_tanbeta + GroundGeometry)
        try:
            arr, gt, proj = read_dem(dem_path)
            sr = arcpy.Describe(dem_path).spatialReference
            valid = np.isfinite(arr) & (arr != -9999.0)
            m = Msgs()
            g = tpi_tools.slope_grid(arr, valid, gt, sr, proj, "Meters", "Degrees")
            H, W = arr.shape
            cols = np.arange(W) + 0.5
            rows = np.arange(H) + 0.5
            if cs.geographic:
                lon_c = gt[0] + cols[None, :] * gt[1] + 0 * rows[:, None]
                lat_c = gt[3] + rows[:, None] * gt[5] + 0 * cols[None, :]
            else:
                X = gt[0] + cols[None, :] * gt[1] + 0 * rows[:, None]
                Y = gt[3] + rows[:, None] * gt[5] + 0 * cols[None, :]
                lon_c, lat_c = cs.unproject(X, Y)
            s_ref, a_ref = site.slope_aspect(lon_c, lat_c)
            inner = (slice(2, H - 2), slice(2, W - 2))
            err = np.abs(g[inner] - s_ref[inner])
            check("{} whole-grid Horn slope vs analytic".format(cs.label),
                  np.nanmax(err) < 0.05,
                  "max err {:.4f} deg, mean {:.5f}".format(np.nanmax(err), np.nanmean(err)))
            res["grid_slope"] = g
            res["grid_gt"] = gt
        except Exception:
            check("{} whole-grid slope".format(cs.label), False, traceback.format_exc()[-400:])

        # ---- B/C. the point tools from the DEM
        def extract(kind, interpolate, method, window, name):
            m = Msgs()
            out_fc = os.path.join(gdb, "{}_{}_{}".format(tag, cs.label, name))
            if kind == "slope":
                fld = tt.run_extract_slope_points(
                    in_points=fc, in_raster=dem_path, out_points=out_fc,
                    interpolate=interpolate, field_name="V",
                    slope_units=tt.SLOPE_UNITS_Z[0], messages=m, from_dem=True,
                    dem_method=method, window_size=window, elev_units="Meters")
            else:
                fld = at.run_extract_aspect_points(
                    in_points=fc, in_raster=dem_path, out_points=out_fc,
                    interpolate=interpolate, field_name="V", messages=m,
                    from_dem=True, dem_method=method, window_size=window,
                    elev_units="Meters")
            if m.errors:
                raise RuntimeError("\n".join(m.errors))
            return field_values(out_fc, fld), m

        try:
            # analytic references at the containing cell centers (nearest) and at the points
            arr, gt, proj = read_dem(dem_path)
            ci = np.floor((px - gt[0]) / gt[1]).astype(int)
            ri = np.floor((gt[3] - py) / abs(gt[5])).astype(int)
            xc = gt[0] + (ci + 0.5) * gt[1]
            yc = gt[3] + (ri + 0.5) * gt[5]
            if cs.geographic:
                lon_cc, lat_cc = xc, yc
            else:
                lon_cc, lat_cc = cs.unproject(xc, yc)
            s_cc, a_cc = site.slope_aspect(lon_cc, lat_cc)
            # aspects from a projected grid are measured from GRID north
            gam_cc = cs.convergence(lon_cc, lat_cc)
            gam_p = cs.convergence(lon_p, lat_p)
            # convergence() gives TRUE north's bearing in the grid frame, so
            # a true-north aspect A reads A + gamma on the grid
            a_cc_grid = (a_cc + gam_cc) % 360.0
            aspect_ref_grid = (aspect_ref + gam_p) % 360.0
            res["convergence_max"] = float(np.max(np.abs(gam_p)))

            v, m = extract("slope", False, at.DEM_METHODS[0], 3, "sh_n")
            res["slope_horn_nearest"] = v
            check("{} Extract Slope from DEM, Horn nearest vs analytic cell value".format(cs.label),
                  np.nanmax(np.abs(v - s_cc)) < 0.05,
                  "max err {:.4f} deg; nan {}".format(np.nanmax(np.abs(v - s_cc)), int(np.isnan(v).sum())))
            # identical to the whole-grid slope at those cells
            gv = res["grid_slope"][ri, ci]
            check("{} point tool (Horn) == whole-grid slope at the same cells".format(cs.label),
                  np.nanmax(np.abs(v - gv)) < 1e-5, "max diff {:.2e}".format(np.nanmax(np.abs(v - gv))))
            NOTES.append("{} {}: {}".format(tag, cs.label, [l for l in m.lines if "Projected" in l or "Geographic" in l][:1]))

            v, m = extract("slope", True, at.DEM_METHODS[0], 3, "sh_b")
            res["slope_horn_bilinear"] = v
            check("{} Extract Slope from DEM, Horn bilinear vs analytic at point".format(cs.label),
                  np.nanmax(np.abs(v - slope_ref)) < 0.1,
                  "max err {:.4f} deg".format(np.nanmax(np.abs(v - slope_ref))))

            v, m = extract("slope", False, at.DEM_METHODS[1], 3, "sq3")
            res["slope_quad3"] = v
            check("{} Extract Slope from DEM, quadratic 3x3 nearest vs analytic".format(cs.label),
                  np.nanmax(np.abs(v - s_cc)) < 0.05,
                  "max err {:.4f} deg".format(np.nanmax(np.abs(v - s_cc))))

            v, m = extract("slope", False, at.DEM_METHODS[1], 7, "sq7")
            res["slope_quad7"] = v
            # 7 x 7 smooths the hills a little; the plane is exact
            far = np.ones(npts, bool)
            x_p, y_p = site.local_xy(lon_p, lat_p)
            for hx, hy, amp, sig in site.hills:
                far &= np.hypot(x_p - hx, y_p - hy) > 4 * sig
            errq = np.abs(v - s_cc)
            check("{} Extract Slope from DEM, quadratic 7x7 (plane cells exact, hills within 0.5)".format(cs.label),
                  (np.nanmax(errq[far]) < 0.02 if far.any() else True) and np.nanmax(errq) < 0.5,
                  "plane max err {:.4f}, overall max {:.3f} deg".format(
                      np.nanmax(errq[far]) if far.any() else -1, np.nanmax(errq)))

            v, m = extract("aspect", False, at.DEM_METHODS[0], 3, "ah_n")
            res["aspect_horn_nearest"] = v
            steep = s_cc > 2.0
            d = circ_diff(v, a_cc_grid)
            check("{} Extract Aspect from DEM, Horn nearest vs analytic grid-north aspect (slope > 2 deg)".format(cs.label),
                  np.nanmax(d[steep]) < 0.5, "max err {:.3f} deg; flat(-1) {}; grid convergence up to {:.2f} deg".format(
                      np.nanmax(d[steep]), int((v == -1).sum()), res["convergence_max"]))
            v, m = extract("aspect", True, at.DEM_METHODS[0], 3, "ah_b")
            res["aspect_horn_bilinear"] = v
            res["aspect_true_north"] = np.where(v >= 0, (v - gam_p) % 360.0, v)
            steep = slope_ref > 2.0
            d = circ_diff(v, aspect_ref_grid)
            check("{} Extract Aspect from DEM, Horn bilinear vs analytic grid-north aspect at point".format(cs.label),
                  np.nanmax(d[steep]) < 1.0, "max err {:.3f} deg".format(np.nanmax(d[steep])))
            v, m = extract("aspect", False, at.DEM_METHODS[1], 5, "aq5")
            d = circ_diff(v, a_cc_grid)
            check("{} Extract Aspect from DEM, quadratic 5x5 vs analytic grid-north aspect".format(cs.label),
                  np.nanmax(d[s_cc > 2.0]) < 1.5, "max err {:.3f} deg".format(np.nanmax(d[s_cc > 2.0])))
        except Exception:
            check("{} point tools from DEM".format(cs.label), False, traceback.format_exc()[-600:])

        # ---- D. identity with Esri's planar Slope / Aspect (UTM = true to scale)
        if any(k in cs.label for k in ("UTM", "WebMercator", "Geographic", "PolarStereo")):
            try:
                arcpy.CheckOutExtension("Spatial")
                es = os.path.join(OUT, "{}_{}_esri_slope.tif".format(tag, cs.label))
                ea = os.path.join(OUT, "{}_{}_esri_aspect.tif".format(tag, cs.label))
                arcpy.sa.Slope(dem_path, "DEGREE", 1, "PLANAR", "METER").save(es)
                arcpy.sa.Aspect(dem_path, "PLANAR", "METER").save(ea)
                m = Msgs()
                o1 = os.path.join(gdb, "{}_{}_esri_s_n".format(tag, cs.label))
                f1 = tt.run_extract_slope_points(fc, es, o1, interpolate=False, field_name="V",
                                                 slope_units=tt.SLOPE_UNITS_Z[0], messages=m)
                es_n = field_values(o1, f1)
                o2 = os.path.join(gdb, "{}_{}_esri_s_b".format(tag, cs.label))
                f2 = tt.run_extract_slope_points(fc, es, o2, interpolate=True, field_name="V",
                                                 slope_units=tt.SLOPE_UNITS_Z[0], messages=m)
                es_b = field_values(o2, f2)
                o3 = os.path.join(gdb, "{}_{}_esri_a_n".format(tag, cs.label))
                f3 = at.run_extract_aspect_points(fc, ea, o3, interpolate=False, field_name="V", messages=m)
                ea_n = field_values(o3, f3)
                if "UTM" in cs.label:
                    d1 = np.nanmax(np.abs(es_n - res["slope_horn_nearest"]))
                    d2 = np.nanmax(np.abs(es_b - res["slope_horn_bilinear"]))
                    d3 = np.nanmax(circ_diff(ea_n, res["aspect_horn_nearest"])[res["aspect_horn_nearest"] >= 0])
                    # UTM is true to scale only within its 0.9996 factor: ours
                    # uses the true ground distance, Esri's planar the nominal
                    # cell, so the two differ by that 0.04% of the tangent
                    # (0.005 deg at 10 deg); identical on an exactly true-to-
                    # scale grid
                    check("{} IDENTITY: Horn-from-DEM == extract from Esri planar Slope (nearest)".format(cs.label),
                          d1 < 1e-2, "max diff {:.2e} deg (UTM scale factor 0.9996 accounts for it)".format(d1))
                    check("{} IDENTITY: Horn-from-DEM == extract from Esri planar Slope (bilinear)".format(cs.label),
                          d2 < 1e-2, "max diff {:.2e} deg".format(d2))
                    check("{} IDENTITY: Horn-from-DEM == extract from Esri planar Aspect".format(cs.label),
                          d3 < 2e-3, "max diff {:.2e} deg".format(d3))
                else:
                    # informational: how Esri's PLANAR method fares on a distorted projection
                    ratio = np.nanmean(np.tan(np.radians(es_n)) / np.tan(np.radians(res["slope_horn_nearest"])))
                    print("  info  {} Esri PLANAR Slope on this projection: mean tangent ratio to ours {:.4f} "
                          "(a naive constant-cell-size method would give {:.4f}); max err vs analytic: "
                          "Esri {:.3f} deg, ours {:.4f} deg".format(
                              cs.label, ratio, math.cos(math.radians(site.lat0)),
                              np.nanmax(np.abs(es_n - s_cc)),
                              np.nanmax(np.abs(res["slope_horn_nearest"] - s_cc))))
            except Exception:
                check("{} Esri comparison".format(cs.label), False, traceback.format_exc()[-600:])

        # ---- F. whole-grid tools: crash check + sampled values
        def run_tool(name, fn, out_name, **kw):
            m = Msgs()
            out = os.path.join(OUT, "{}_{}_{}.tif".format(tag, cs.label, out_name))
            t1 = time.time()
            try:
                fn(dem_path, out, messages=m, **kw)
                if m.errors:
                    raise RuntimeError("\n".join(m.errors))
                v = sample_tif(out, px, py)
                res[name] = v
                print("        {:<16} ran in {:5.1f} s; warnings: {}".format(
                    name, time.time() - t1, len(m.warnings)))
                for wmsg in m.warnings:
                    print("            WARNING: " + wmsg[:160])
            except Exception:
                check("{} {} ran".format(cs.label, name), False, traceback.format_exc()[-500:])

        run_tool("hillshade", hs.run_enhanced_hillshade, "hs", elev_units="Meters")
        run_tool("vrm", tt.run_vrm, "vrm", elev_units="Meters", radius=90.0, radius_units="Meters")
        run_tool("surface_ratio", lambda d, o, messages: tt.run_surface_area(
            d, out_ratio=o, elev_units="Meters", messages=messages), "sar")
        run_tool("tri_slope", tt.run_tri, "tri", method=tt.TRI_METHODS[2], radius=60.0,
                 radius_units="Meters", elev_units="Meters")
        run_tool("tpi", lambda d, o, messages: tpi_tools.run_tpi(
            d, out_raw=o, outer=300.0, units="Meters", elev_units="Meters", messages=messages), "tpi")
        run_tool("twi", tt.run_twi, "twi", elev_units="Meters")
        run_tool("lsri", lambda d, o, messages: lsri.run_lsri_on_dem(
            d, o, contour_interval=20.0, radius=300.0, radius_units="Meters", messages=messages), "lsri")
        # surface ratio vs analytic 1 / cos(slope) at the cell
        if "surface_ratio" in res:
            exp = 1.0 / np.cos(np.radians(s_cc))
            rel = np.abs(res["surface_ratio"] / exp - 1.0)
            check("{} surface ratio vs analytic 1/cos(slope)".format(cs.label),
                  np.nanmax(rel) < 0.005, "max rel err {:.4%}".format(np.nanmax(rel)))
        # line tools' landscape slope grid
        try:
            dem = lst.load_dem(dem_path)
            lst._landscape_grids(dem, 1.0)
            ls = dem["land_slope_deg"]
            H, W = ls.shape
            inner = (slice(2, H - 2), slice(2, W - 2))
            # analytic at cell centers of the full DEM (computed above as s_ref)
            err = np.abs(ls[inner] - s_ref[inner])
            check("{} line tools' landscape slope vs analytic".format(cs.label),
                  np.nanmax(err) < 0.05, "max err {:.4f} deg".format(np.nanmax(err)))
        except Exception:
            check("{} line tools landscape slope".format(cs.label), False, traceback.format_exc()[-400:])
        print("    [{} done in {:.1f} s]".format(cs.label, time.time() - t0))

    # ---- E. across coordinate systems at the same physical points
    labels = [cs.label for cs in systems]
    base = labels[0]
    print("\n--- cross-CS agreement (reference: {}) ---".format(base))
    for lab in labels[1:]:
        a, b = results[base], results[lab]
        for key, tol, circ in (("slope_horn_bilinear", 0.1, False), ("aspect_true_north", 1.0, True),
                               ("hillshade", None, False), ("surface_ratio", 0.003, False),
                               ("tri_slope", None, False), ("vrm", None, False),
                               ("tpi", None, False), ("lsri", None, False), ("twi", None, False)):
            if key not in a or key not in b:
                continue
            if circ:
                d = circ_diff(a[key], b[key])
                d = d[(results[base]["slope_horn_bilinear"] > 2.0)]
            else:
                d = np.abs(a[key] - b[key])
            if tol is not None:
                check("{} vs {}: {} agree".format(base, lab, key), np.nanmax(d) < tol,
                      "max diff {:.4f}".format(np.nanmax(d)))
            else:
                ma = np.nanmean(np.abs(a[key]))
                print("  info  {} vs {}: {} max abs diff {:.4g} (mean |value| {:.4g}, rel {:.2%})".format(
                    base, lab, key, np.nanmax(d), ma, np.nanmax(d) / ma if ma else 0))


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    gdb = os.path.join(OUT, "synth.gdb")
    arcpy.management.CreateFileGDB(OUT, "synth.gdb")
    arcpy.env.overwriteOutput = True
    arcpy.env.scratchWorkspace = OUT
    arcpy.env.workspace = gdb
    t0 = time.time()

    flag = Site("Flagstaff", -111.65, 35.2)
    run_site(flag, [CS("UTM12N", arcpy.SpatialReference(32612)),
                    CS("Geographic", arcpy.SpatialReference(4326)),
                    CS("WebMercator", arcpy.SpatialReference(3857)),
                    CS("AlbersWGS84", albers_wgs84())], gdb, "flag")

    arctic = Site("Arctic", -40.0, 75.0)
    run_site(arctic, [CS("UTM24N", arcpy.SpatialReference(32624)),
                      CS("PolarStereo", arcpy.SpatialReference(3413)),
                      CS("WebMercator", arcpy.SpatialReference(3857)),
                      CS("Geographic", arcpy.SpatialReference(4326))], gdb, "arc")

    print("\nGround-geometry notes seen:")
    for n in NOTES:
        print("  ", str(n)[:300])
    print("\n{} passed, {} failed, {:.0f} s total".format(PASS, FAIL, time.time() - t0))
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
