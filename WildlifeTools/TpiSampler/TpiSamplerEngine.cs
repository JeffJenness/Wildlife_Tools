// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Scholarship Fund notice, marking of modified versions, and
// use of names).
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using System.Threading.Tasks;

namespace WildlifeTools.TpiSampler
{
    // The arithmetic behind the TPI Neighborhood Sampler window, with no
    // ArcGIS or WPF types in it, so it compiles into a plain console test
    // (claude_tests/TpiSamplerTest). The window holds a coarse snapshot of a
    // DEM, computes TPI over a circular neighborhood of any radius from row
    // prefix sums (one lookup per chord of the circle, so a whole grid takes
    // tens of milliseconds), traces the threshold as a contour, and paints
    // the result. The neighborhood is the one the Python TPI tools use: the
    // elliptical 0/1 kernel of tpi_tools._annulus_kernel, with the east-west
    // radius varying by row on a geographic DEM so the circle is true on the
    // ground (tpi_tools._cell_radii / _row_groups).

    /// <summary>The radius units the TPI tools offer, in their order.</summary>
    internal static class RadiusUnits
    {
        public static readonly string[] Names = { "Cells", "Meters", "Kilometers", "Feet", "Miles" };

        /// <summary>Meters per unit; NaN for Cells.</summary>
        public static double MetersPer(string unit)
        {
            switch (unit)
            {
                case "Meters": return 1.0;
                case "Kilometers": return 1000.0;
                case "Feet": return 0.3048;
                case "Miles": return 1609.344;
                default: return double.NaN;
            }
        }

        public static string Singular(string unit)
        {
            switch (unit)
            {
                case "Cells": return "cell";
                case "Meters": return "meter";
                case "Kilometers": return "kilometer";
                case "Feet": return "foot";
                case "Miles": return "mile";
                default: return unit;
            }
        }
    }

    /// <summary>Which TPI the window shows: raw, in the DEM's elevation units,
    /// or standardized by the neighborhood's standard deviation of elevation
    /// (the "dev" representation of the TPI tools, Wilson and Gallant eq. 3.30).</summary>
    internal enum TpiScale { Raw, NeighborhoodSd }

    /// <summary>
    /// A coarse snapshot of a DEM: a row-major grid whose cells are
    /// <see cref="Factor"/> × <see cref="Factor"/> DEM cells each, with the
    /// ground size of a cell per row so radii in linear units convert to cells.
    /// </summary>
    internal sealed class SamplerGrid
    {
        public int Width;
        public int Height;
        /// <summary>Elevations, row-major, NaN where the DEM has no data.</summary>
        public float[] Z;
        public bool[] Valid;
        /// <summary>DEM cells per snapshot cell, in each direction.</summary>
        public int Factor = 1;
        public bool Geographic;
        /// <summary>Ground width of a snapshot cell, east-west, per row (meters).</summary>
        public double[] RowCellWidthM;
        /// <summary>Ground height of a snapshot cell, north-south (meters).</summary>
        public double CellHeightM;
        /// <summary>The DEM's own cell size, in its own units, for the readouts.</summary>
        public double NativeCellSize;
        public string NativeUnitName = "";
        public string DemName = "";
        /// <summary>True when the snapshot sampled every Factor-th DEM cell
        /// instead of averaging blocks (very large views).</summary>
        public bool Sampled;
        public double ZMin = double.NaN;
        public double ZMax = double.NaN;

        /// <summary>The mean ground size of a snapshot cell, meters.</summary>
        public double MeanCellM
        {
            get
            {
                double s = 0;
                for (int r = 0; r < Height; r++) s += RowCellWidthM[r];
                return (s / Math.Max(1, Height) + CellHeightM) / 2.0;
            }
        }

        public int CellCount => Width * Height;

        /// <summary>Builds a snapshot by averaging Factor × Factor blocks of a
        /// native block of DEM cells (row-major, NaN = NoData). The native block
        /// must be a whole number of blocks in each direction.</summary>
        public static SamplerGrid FromBlockMeans(float[] native, int nw, int nh, int factor)
        {
            if (factor < 1) throw new ArgumentOutOfRangeException(nameof(factor));
            if (nw % factor != 0 || nh % factor != 0)
                throw new ArgumentException("The native block must be a whole number of blocks.");
            int w = nw / factor, h = nh / factor;
            var g = new SamplerGrid { Width = w, Height = h, Factor = factor, Z = new float[w * h], Valid = new bool[w * h] };
            for (int r = 0; r < h; r++)
            {
                for (int c = 0; c < w; c++)
                {
                    double sum = 0;
                    int n = 0;
                    for (int rr = r * factor; rr < (r + 1) * factor; rr++)
                    {
                        int baseIdx = rr * nw + c * factor;
                        for (int cc = 0; cc < factor; cc++)
                        {
                            float v = native[baseIdx + cc];
                            if (!float.IsNaN(v))
                            {
                                sum += v;
                                n++;
                            }
                        }
                    }
                    int i = r * w + c;
                    // a block that is less than half NoData keeps its mean
                    if (n * 2 >= factor * factor)
                    {
                        g.Z[i] = (float)(sum / n);
                        g.Valid[i] = true;
                    }
                    else
                    {
                        g.Z[i] = float.NaN;
                    }
                }
            }
            g.FinishStats();
            return g;
        }

        /// <summary>Wraps an already-coarse grid: block means, or every
        /// Factor-th cell when <paramref name="sampled"/>.</summary>
        public static SamplerGrid FromCoarse(float[] z, int w, int h, int factor, bool sampled)
        {
            var g = new SamplerGrid { Width = w, Height = h, Factor = factor, Z = z, Valid = new bool[w * h], Sampled = sampled };
            for (int i = 0; i < z.Length; i++) g.Valid[i] = !float.IsNaN(z[i]);
            g.FinishStats();
            return g;
        }

        public void FinishStats()
        {
            double lo = double.PositiveInfinity, hi = double.NegativeInfinity;
            for (int i = 0; i < Z.Length; i++)
            {
                if (!Valid[i]) continue;
                if (Z[i] < lo) lo = Z[i];
                if (Z[i] > hi) hi = Z[i];
            }
            ZMin = double.IsPositiveInfinity(lo) ? double.NaN : lo;
            ZMax = double.IsNegativeInfinity(hi) ? double.NaN : hi;
        }

        /// <summary>Gives every row the same ground cell size (a projected DEM).</summary>
        public void SetUniformCells(double cellWidthM, double cellHeightM)
        {
            RowCellWidthM = new double[Height];
            for (int r = 0; r < Height; r++) RowCellWidthM[r] = cellWidthM;
            CellHeightM = cellHeightM;
            Geographic = false;
        }

        /// <summary>
        /// Ground cell sizes for a geographic snapshot: the spheroidal ECEF
        /// distance from a cell center to its east neighbor's center, per row,
        /// and to its north neighbor (mean over rows), exactly as
        /// hillshade._geographic_row_distances measures them.
        /// </summary>
        public void SetGeographicCells(double a, double b, double lonCenter, double latTop, double dLon, double dLat)
        {
            RowCellWidthM = new double[Height];
            double vSum = 0;
            for (int r = 0; r < Height; r++)
            {
                double lat = latTop - (r + 0.5) * dLat;
                var focal = Ecef(a, b, lonCenter, lat);
                var right = Ecef(a, b, lonCenter + dLon, lat);
                var above = Ecef(a, b, lonCenter, lat + dLat);
                RowCellWidthM[r] = Math.Max(Dist(focal, right), 1e-9);
                vSum += Dist(focal, above);
            }
            CellHeightM = Math.Max(vSum / Math.Max(1, Height), 1e-9);
            Geographic = true;
        }

        private static (double x, double y, double z) Ecef(double a, double b, double lonDeg, double latDeg)
        {
            double lon = lonDeg * Math.PI / 180.0, lat = latDeg * Math.PI / 180.0;
            double e2 = 1.0 - (b * b) / (a * a);
            double sinLat = Math.Sin(lat), cosLat = Math.Cos(lat);
            double n = a / Math.Sqrt(1.0 - e2 * sinLat * sinLat);
            return (n * cosLat * Math.Cos(lon), n * cosLat * Math.Sin(lon), n * (1.0 - e2) * sinLat);
        }

        private static double Dist((double x, double y, double z) p, (double x, double y, double z) q)
        {
            double dx = p.x - q.x, dy = p.y - q.y, dz = p.z - q.z;
            return Math.Sqrt(dx * dx + dy * dy + dz * dz);
        }

        /// <summary>The ground size of a snapshot cell in the given radius
        /// units (Cells = DEM cells, so Factor).</summary>
        public double CellSizeIn(string unit)
        {
            if (unit == "Cells") return Factor;
            return MeanCellM / RadiusUnits.MetersPer(unit);
        }
    }

    /// <summary>A neighborhood radius resolved to snapshot cells: the
    /// east-west radius per row and the north-south radius.</summary>
    internal sealed class Neighborhood
    {
        public double Value;
        public string Unit;
        public double[] RxRows;
        public double Ry;
        /// <summary>The radius in mean snapshot cells, for captions.</summary>
        public double MeanCells;
        /// <summary>The radius in DEM cells, for captions.</summary>
        public double DemCells;
        public double Meters;

        public string Key => Unit + ":" + Value.ToString("R", CultureInfo.InvariantCulture);

        /// <summary>Resolves a radius the way tpi_tools._cell_radii does, then
        /// rounds the per-row east-west radius to whole cells on a geographic
        /// grid, as tpi_tools._row_groups does, so the kernels match.</summary>
        public static Neighborhood Resolve(SamplerGrid g, double value, string unit)
        {
            var n = new Neighborhood { Value = value, Unit = unit, RxRows = new double[g.Height] };
            if (unit == "Cells")
            {
                double cells = value / g.Factor;         // DEM cells -> snapshot cells
                for (int r = 0; r < g.Height; r++) n.RxRows[r] = cells;
                n.Ry = cells;
                n.MeanCells = cells;
                n.DemCells = value;
                n.Meters = cells * g.MeanCellM;
                return n;
            }
            double m = value * RadiusUnits.MetersPer(unit);
            n.Meters = m;
            double sumRx = 0;
            for (int r = 0; r < g.Height; r++)
            {
                double rx = m / g.RowCellWidthM[r];
                if (g.Geographic) rx = Math.Max(Math.Round(rx), 1.0);
                n.RxRows[r] = rx;
                sumRx += rx;
            }
            n.Ry = m / g.CellHeightM;
            n.MeanCells = (sumRx / Math.Max(1, g.Height) + n.Ry) / 2.0;
            n.DemCells = n.MeanCells * g.Factor;
            return n;
        }
    }

    /// <summary>TPI grids for one neighborhood: raw and neighborhood-SD
    /// standardized, NaN where the cell or its neighborhood has no data.</summary>
    internal sealed class TpiGrids
    {
        public float[] Raw;
        public float[] Dev;
        public float[] For(TpiScale scale) => scale == TpiScale.Raw ? Raw : Dev;
    }

    /// <summary>
    /// The focal arithmetic: row prefix sums of z, z² and the valid count, so
    /// the sum over any chord of a row is two lookups and a circular
    /// neighborhood costs one pair of lookups per row it spans.
    /// </summary>
    internal sealed class FocalEngine
    {
        private readonly SamplerGrid _g;
        private readonly double[] _s;     // (W+1) per row, prefix sum of z
        private readonly double[] _q;     // prefix sum of z²
        private readonly double[] _n;     // prefix count of valid cells
        private readonly int _stride;
        private readonly Dictionary<string, TpiGrids> _cache = new Dictionary<string, TpiGrids>();
        private readonly object _lock = new object();

        public FocalEngine(SamplerGrid g)
        {
            _g = g;
            _stride = g.Width + 1;
            _s = new double[g.Height * _stride];
            _q = new double[g.Height * _stride];
            _n = new double[g.Height * _stride];
            for (int r = 0; r < g.Height; r++)
            {
                double s = 0, q = 0, n = 0;
                int row = r * _stride, src = r * g.Width;
                for (int c = 0; c < g.Width; c++)
                {
                    if (g.Valid[src + c])
                    {
                        double z = g.Z[src + c];
                        s += z;
                        q += z * z;
                        n += 1;
                    }
                    _s[row + c + 1] = s;
                    _q[row + c + 1] = q;
                    _n[row + c + 1] = n;
                }
            }
        }

        public SamplerGrid Grid => _g;

        /// <summary>The cached grids for a neighborhood, computed on first use.</summary>
        public TpiGrids Get(Neighborhood nb)
        {
            lock (_lock)
            {
                if (_cache.TryGetValue(nb.Key, out TpiGrids hit)) return hit;
            }
            TpiGrids made = Compute(nb);
            lock (_lock)
            {
                _cache[nb.Key] = made;
            }
            return made;
        }

        public bool IsCached(Neighborhood nb)
        {
            lock (_lock)
            {
                return _cache.ContainsKey(nb.Key);
            }
        }

        /// <summary>The chord half-width, in whole cells, of the kernel at row
        /// offset dy: the cells with (dx/rx)² + (dy/ry)² ≤ 1 + 1e-9, the test
        /// tpi_tools._annulus_kernel applies to integer offsets.</summary>
        public static int ChordHalfWidth(double rx, double ry, int dy)
        {
            rx = Math.Max(rx, 0.5);
            ry = Math.Max(ry, 0.5);
            double t = 1.0 + 1e-9 - (dy / ry) * (dy / ry);
            if (t < 0) return -1;
            return (int)Math.Floor(rx * Math.Sqrt(t) + 1e-9);
        }

        private TpiGrids Compute(Neighborhood nb)
        {
            int w = _g.Width, h = _g.Height;
            var raw = new float[w * h];
            var dev = new float[w * h];
            double ry = Math.Max(nb.Ry, 0.5);
            int ny = (int)Math.Ceiling(ry);
            Parallel.For(0, h, r =>
            {
                var sum = new double[w];
                var sq = new double[w];
                var cnt = new double[w];
                double rx = nb.RxRows[r];
                for (int dy = -ny; dy <= ny; dy++)
                {
                    int rr = r + dy;
                    if (rr < 0 || rr >= h) continue;
                    int half = ChordHalfWidth(rx, ry, dy);
                    if (half < 0) continue;
                    int row = rr * _stride;
                    for (int c = 0; c < w; c++)
                    {
                        int c0 = c - half, c1 = c + half + 1;
                        if (c0 < 0) c0 = 0;
                        if (c1 > w) c1 = w;
                        sum[c] += _s[row + c1] - _s[row + c0];
                        sq[c] += _q[row + c1] - _q[row + c0];
                        cnt[c] += _n[row + c1] - _n[row + c0];
                    }
                }
                int src = r * w;
                for (int c = 0; c < w; c++)
                {
                    int i = src + c;
                    if (!_g.Valid[i] || cnt[c] < 0.5)
                    {
                        raw[i] = float.NaN;
                        dev[i] = float.NaN;
                        continue;
                    }
                    double mean = sum[c] / cnt[c];
                    double tpi = _g.Z[i] - mean;
                    raw[i] = (float)tpi;
                    double var = sq[c] / cnt[c] - mean * mean;
                    double sd = var > 0 ? Math.Sqrt(var) : 0.0;
                    dev[i] = sd > 0 ? (float)(tpi / sd) : 0f;
                }
            });
            return new TpiGrids { Raw = raw, Dev = dev };
        }

        /// <summary>TPI of one cell for one neighborhood, without the whole
        /// grid: the pinned feature's curve. NaN when there is no data.</summary>
        public (double raw, double dev) At(Neighborhood nb, int c, int r)
        {
            int w = _g.Width, h = _g.Height;
            if (c < 0 || c >= w || r < 0 || r >= h || !_g.Valid[r * w + c]) return (double.NaN, double.NaN);
            double ry = Math.Max(nb.Ry, 0.5);
            int ny = (int)Math.Ceiling(ry);
            double rx = nb.RxRows[r];
            double sum = 0, sq = 0, cnt = 0;
            for (int dy = -ny; dy <= ny; dy++)
            {
                int rr = r + dy;
                if (rr < 0 || rr >= h) continue;
                int half = ChordHalfWidth(rx, ry, dy);
                if (half < 0) continue;
                int row = rr * _stride;
                int c0 = Math.Max(0, c - half), c1 = Math.Min(w, c + half + 1);
                sum += _s[row + c1] - _s[row + c0];
                sq += _q[row + c1] - _q[row + c0];
                cnt += _n[row + c1] - _n[row + c0];
            }
            if (cnt < 0.5) return (double.NaN, double.NaN);
            double mean = sum / cnt;
            double tpi = _g.Z[r * w + c] - mean;
            double var = sq / cnt - mean * mean;
            double sd = var > 0 ? Math.Sqrt(var) : 0.0;
            return (tpi, sd > 0 ? tpi / sd : 0.0);
        }
    }

    /// <summary>The threshold line: marching squares over cell centers.</summary>
    internal static class Contour
    {
        /// <summary>Line segments, in grid coordinates where cell (c, r) is
        /// centered at (c + 0.5, r + 0.5). Squares touching NoData are skipped.</summary>
        public static List<(double x1, double y1, double x2, double y2)> Trace(float[] v, int w, int h, double level)
        {
            var segs = new List<(double, double, double, double)>();
            for (int r = 0; r + 1 < h; r++)
            {
                for (int c = 0; c + 1 < w; c++)
                {
                    float a = v[r * w + c], b = v[r * w + c + 1];
                    float d = v[(r + 1) * w + c], e = v[(r + 1) * w + c + 1];
                    if (float.IsNaN(a) || float.IsNaN(b) || float.IsNaN(d) || float.IsNaN(e)) continue;
                    int code = (a >= level ? 8 : 0) | (b >= level ? 4 : 0) | (e >= level ? 2 : 0) | (d >= level ? 1 : 0);
                    if (code == 0 || code == 15) continue;
                    double x0 = c + 0.5, y0 = r + 0.5;
                    // edge crossings: top (a-b), right (b-e), bottom (d-e), left (a-d)
                    (double, double) Top() => (x0 + Frac(a, b, level), y0);
                    (double, double) Right() => (x0 + 1, y0 + Frac(b, e, level));
                    (double, double) Bottom() => (x0 + Frac(d, e, level), y0 + 1);
                    (double, double) Left() => (x0, y0 + Frac(a, d, level));
                    void Add((double, double) p, (double, double) q) => segs.Add((p.Item1, p.Item2, q.Item1, q.Item2));
                    switch (code)
                    {
                        case 1: case 14: Add(Left(), Bottom()); break;
                        case 2: case 13: Add(Bottom(), Right()); break;
                        case 3: case 12: Add(Left(), Right()); break;
                        case 4: case 11: Add(Top(), Right()); break;
                        case 6: case 9: Add(Top(), Bottom()); break;
                        case 7: case 8: Add(Left(), Top()); break;
                        case 5:
                        case 10:
                            {
                                double center = (a + b + d + e) / 4.0;
                                bool centerHigh = center >= level;
                                if ((code == 5) == centerHigh)
                                {
                                    Add(Left(), Top());
                                    Add(Bottom(), Right());
                                }
                                else
                                {
                                    Add(Left(), Bottom());
                                    Add(Top(), Right());
                                }
                                break;
                            }
                    }
                }
            }
            return segs;
        }

        private static double Frac(float p, float q, double level)
        {
            double d = q - p;
            if (Math.Abs(d) < 1e-12) return 0.5;
            double f = (level - p) / d;
            return f < 0 ? 0 : (f > 1 ? 1 : f);
        }
    }

    /// <summary>Painting the snapshot: a diverging TPI ramp over an optional
    /// hillshade, with the cells that pass the threshold tinted.</summary>
    internal static class Painter
    {
        /// <summary>Horn hillshade of the snapshot, 0..255, 128 where undefined.
        /// Light from the northwest, 45° up; the elevation is taken as meters.</summary>
        public static byte[] Hillshade(SamplerGrid g)
        {
            int w = g.Width, h = g.Height;
            var o = new byte[w * h];
            double az = 315.0 * Math.PI / 180.0, alt = 45.0 * Math.PI / 180.0;
            double cx = Math.Max(g.MeanCellM, 1e-9), cy = Math.Max(g.CellHeightM, 1e-9);
            for (int r = 0; r < h; r++)
            {
                for (int c = 0; c < w; c++)
                {
                    int i = r * w + c;
                    if (!g.Valid[i]) { o[i] = 128; continue; }
                    double Zc(int cc, int rr)
                    {
                        cc = Math.Max(0, Math.Min(w - 1, cc));
                        rr = Math.Max(0, Math.Min(h - 1, rr));
                        int j = rr * w + cc;
                        return g.Valid[j] ? g.Z[j] : g.Z[i];
                    }
                    double dzdx = ((Zc(c + 1, r - 1) + 2 * Zc(c + 1, r) + Zc(c + 1, r + 1))
                                   - (Zc(c - 1, r - 1) + 2 * Zc(c - 1, r) + Zc(c - 1, r + 1))) / (8 * cx);
                    double dzdy = ((Zc(c - 1, r + 1) + 2 * Zc(c, r + 1) + Zc(c + 1, r + 1))
                                   - (Zc(c - 1, r - 1) + 2 * Zc(c, r - 1) + Zc(c + 1, r - 1))) / (8 * cy);
                    double slope = Math.Atan(Math.Sqrt(dzdx * dzdx + dzdy * dzdy));
                    double aspect = Math.Atan2(dzdy, -dzdx);
                    double shade = Math.Cos(Math.PI / 2 - alt) * Math.Cos(slope)
                                   + Math.Sin(Math.PI / 2 - alt) * Math.Sin(slope) * Math.Cos(az - Math.PI / 2 - aspect);
                    o[i] = (byte)Math.Max(0, Math.Min(255, Math.Round(255 * shade)));
                }
            }
            return o;
        }

        /// <summary>
        /// The picture as BGRA bytes: blue below zero through white to red
        /// above, saturating at ±range, lit by the hillshade when given; cells
        /// that pass the threshold test get a yellow tint; NoData is
        /// transparent.
        /// </summary>
        public static byte[] Paint(float[] v, int w, int h, double range, double threshold, bool above,
                                   bool tint, byte[] hillshade, int edgeX = 0, int edgeY = 0)
        {
            var px = new byte[w * h * 4];
            if (range <= 0 || double.IsNaN(range)) range = 1;
            for (int i = 0; i < w * h; i++)
            {
                float t = v[i];
                int o = i * 4;
                if (float.IsNaN(t)) continue;            // alpha 0
                int c = i % w, r = i / w;
                // the band where neighborhoods run off the snapshot is faded
                bool edge = c < edgeX || c >= w - edgeX || r < edgeY || r >= h - edgeY;
                double u = Math.Max(-1.0, Math.Min(1.0, t / range));
                double rr, gg, bb;
                if (u >= 0)
                {
                    rr = 255 - u * (255 - 185);
                    gg = 255 - u * (255 - 35);
                    bb = 255 - u * (255 - 35);
                }
                else
                {
                    rr = 255 + u * (255 - 40);
                    gg = 255 + u * (255 - 90);
                    bb = 255 + u * (255 - 200);
                }
                if (hillshade != null)
                {
                    double light = 0.35 + 0.65 * hillshade[i] / 255.0;
                    rr *= light;
                    gg *= light;
                    bb *= light;
                }
                bool passes = above ? t >= threshold : t <= threshold;
                if (tint && passes)
                {
                    rr = rr * 0.6 + 255 * 0.4;
                    gg = gg * 0.6 + 230 * 0.4;
                    bb = bb * 0.6 + 0 * 0.4;
                }
                if (edge)
                {
                    rr = rr * 0.55 + 235 * 0.45;
                    gg = gg * 0.55 + 235 * 0.45;
                    bb = bb * 0.55 + 235 * 0.45;
                }
                px[o] = (byte)Math.Max(0, Math.Min(255, bb));
                px[o + 1] = (byte)Math.Max(0, Math.Min(255, gg));
                px[o + 2] = (byte)Math.Max(0, Math.Min(255, rr));
                px[o + 3] = 255;
            }
            return px;
        }

        /// <summary>A round number at or above the given fraction of the |values|
        /// in the grids (1 = the largest), so the threshold slider and the colors
        /// keep one range for every radius.</summary>
        public static double NiceRange(IEnumerable<float[]> grids, double fraction = 1.0)
        {
            var all = new List<float>();
            foreach (float[] g in grids)
            {
                foreach (float v in g)
                {
                    if (!float.IsNaN(v)) all.Add(Math.Abs(v));
                }
            }
            if (all.Count == 0) return 1;
            all.Sort();
            int at = (int)Math.Min(all.Count - 1, Math.Max(0, Math.Round((all.Count - 1) * Math.Max(0.0, Math.Min(1.0, fraction)))));
            double m = all[at];
            if (m <= 0) return 1;
            double p = Math.Pow(10, Math.Floor(Math.Log10(m)));
            foreach (double k in new[] { 1, 2, 2.5, 5, 10 })
            {
                if (k * p >= m) return k * p;
            }
            return 10 * p;
        }
    }

    /// <summary>Captions and the settings text the Copy button puts on the clipboard.</summary>
    internal static class Captions
    {
        public static string Number(double v, int sig = 3)
        {
            if (double.IsNaN(v)) return "n/a";
            if (v == 0) return "0";
            double mag = Math.Floor(Math.Log10(Math.Abs(v)));
            int decimals = (int)Math.Max(0, sig - 1 - mag);
            return Math.Round(v, Math.Min(decimals, 6)).ToString("#,##0.######", CultureInfo.CurrentCulture);
        }

        public static string Snapshot(SamplerGrid g)
        {
            string cell = g.Geographic
                ? Number(g.MeanCellM) + " m on the ground"
                : Number(g.MeanCellM) + " m";
            string native = g.Factor == 1
                ? "the DEM's own cells"
                : g.Factor + " × " + g.Factor + " DEM cells" + (g.Sampled ? ", sampled" : ", averaged");
            return "Snapshot: " + g.Width + " × " + g.Height + " cells of about " + cell + " (" + native + ")";
        }

        public static string Radius(Neighborhood nb, SamplerGrid g)
        {
            string unitWord = nb.Unit == "Cells"
                ? (nb.Value == 1 ? " DEM cell" : " DEM cells")
                : " " + nb.Unit.ToLowerInvariant();
            string more = nb.Unit == "Cells"
                ? Number(nb.Meters) + " m"
                : Number(nb.DemCells) + " DEM cells";
            return Number(nb.Value) + unitWord + " (" + more + ", " + Number(nb.MeanCells, 2) + " snapshot cells)";
        }

        public static string Threshold(double t, TpiScale scale, bool above)
        {
            string op = above ? "at or above " : "at or below ";
            string unit = scale == TpiScale.Raw ? " elevation units" : " standard deviations";
            return "TPI " + op + Number(t) + unit;
        }

        /// <summary>The clipboard text: the settings as words, then the
        /// Python call that computes the same TPI with the Topographic
        /// Position Index tool.</summary>
        public static string Settings(SamplerGrid g, Neighborhood nb, TpiScale scale, double threshold, bool above,
                                      bool classify = true)
        {
            var sb = new StringBuilder();
            sb.AppendLine("TPI Neighborhood Sampler settings");
            sb.AppendLine("DEM: " + g.DemName);
            sb.AppendLine("Neighborhood: circle, radius " + Radius(nb, g));
            sb.AppendLine("TPI scale: " + (scale == TpiScale.Raw
                ? "raw TPI, in the DEM's elevation units"
                : "standardized at neighborhood scale (TPI divided by the neighborhood's standard deviation of elevation)"));
            sb.AppendLine("Classify as the feature: " + (classify ? Threshold(threshold, scale, above) : "no threshold applied"));
            sb.AppendLine(Snapshot(g));
            sb.AppendLine();
            sb.AppendLine("# Python: the same TPI with the Topographic Position Index tool");
            sb.AppendLine("import arcpy");
            sb.AppendLine("arcpy.ImportToolbox(r\"C:\\path\\to\\JennessEnterprisesTools.pyt\")  # your installed path is shown in the tool's run messages");
            string outArg = scale == TpiScale.Raw ? "out_tpi" : "out_dev";
            sb.AppendLine("arcpy.jenness.SimpleTPI(");
            sb.AppendLine("    in_raster=r\"" + g.DemName + "\",");
            sb.AppendLine("    " + outArg + "=r\"C:\\data\\tpi_" + Number(nb.Value).Replace(",", "").Replace(".", "_") + ".tif\",");
            sb.AppendLine("    nb_shape=\"Circle\",");
            sb.AppendLine("    nb_outer=" + nb.Value.ToString("R", CultureInfo.InvariantCulture) + ",");
            sb.AppendLine("    nb_units=\"" + nb.Unit + "\")");
            if (classify)
            {
                sb.AppendLine("# then classify the feature as cells " + (above ? ">= " : "<= ")
                              + threshold.ToString("R", CultureInfo.InvariantCulture)
                              + " in that raster, or put the threshold in a classification system");
            }
            return sb.ToString();
        }
    }
}
