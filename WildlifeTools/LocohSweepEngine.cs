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
using System.Linq;
using System.Threading;
using ArcGIS.Core.Data;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Mapping;

namespace WildlifeTools
{
    /// <summary>One computed sweep step: the parameter value tried and the
    /// areas (square kilometers) of the cumulative isopleths when they first
    /// enclose 50 / 75 / 95 percent of the points. NaN = never reached
    /// (possible only when degenerate hulls leave points uncovered).</summary>
    internal sealed class LocohSweepStep
    {
        public double Param;
        public double AreaKm50 = double.NaN;
        public double AreaKm75 = double.NaN;
        public double AreaKm95 = double.NaN;
    }

    /// <summary>A full leveling-off sweep for one layer and one method.</summary>
    internal sealed class LocohSweepResult
    {
        public string LayerName = "";
        public string Method = "k";            // "k" / "a" / "r"
        public int N;
        public bool GeographicInput;
        public double HeuristicParam;          // in DISPLAY units (k, or meters)
        public string HeuristicLabel = "";
        public string Note = "";
        public readonly List<LocohSweepStep> Steps = new List<LocohSweepStep>();

        public string MethodTitle =>
            Method == "k" ? "Standard (k)"
            : Method == "a" ? "Adaptive (a)" : "Fixed Sphere (r)";
        public string ParamAxisLabel =>
            Method == "k" ? "k (points per hull)"
            : Method == "a" ? "a — cumulative distance budget (m)"
                            : "r — sphere radius (m)";
    }

    /// <summary>
    /// The Getz et al. (2007) leveling-off analysis behind the LoCoH
    /// Parameter Explorer window: sweep the governing parameter (k, a or r)
    /// across a sequence of values, build the local hulls at each value by
    /// the same rules as the LoCoH geoprocessing tools (locoh.py), and
    /// record the area of the cumulative isopleth as it first encloses
    /// 50 / 75 / 95 percent of the points. Plotted against the parameter,
    /// spurious holes fill quickly (area climbs), the curves plateau, and
    /// genuinely unused ground begins to be bridged past it (area climbs
    /// again). All work runs on the MCT (call inside QueuedTask.Run).
    /// </summary>
    internal static class LocohSweepEngine
    {
        // -----------------------------------------------------------------
        //  public entry
        // -----------------------------------------------------------------
        public static LocohSweepResult Run(FeatureLayer lyr, string method,
            int nSteps, Action<int, int, string> progress,
            CancellationToken ct)
        {
            nSteps = Math.Max(4, Math.Min(nSteps, 40));
            var result = new LocohSweepResult
            { LayerName = lyr.Name, Method = method };

            // ---- read the (selected) points --------------------------------
            var raw = new List<MapPoint>();
            bool selected = false;
            using (var sel = lyr.GetSelection())
            {
                var ids = sel.GetObjectIDs();
                var qf = new QueryFilter();
                if (ids != null && ids.Count > 0)
                { qf.ObjectIDs = ids.ToList(); selected = true; }
                using (var cur = lyr.Search(qf))
                    while (cur.MoveNext())
                        using (var f = (Feature)cur.Current)
                        {
                            if (f.GetShape() is MapPoint mp && !mp.IsEmpty)
                                raw.Add((MapPoint)mp.Clone());
                            else if (f.GetShape() is Multipoint mpt
                                     && !mpt.IsEmpty)
                                foreach (var p in mpt.Points)
                                    raw.Add((MapPoint)p.Clone());
                        }
            }
            if (selected) result.LayerName += " (selection)";
            if (raw.Count < 5)
                throw new InvalidOperationException(
                    "At least 5 points are needed for a parameter sweep (" +
                    raw.Count + " found" +
                    (selected ? " in the selection" : "") + ").");

            var sr = raw[0].SpatialReference
                ?? lyr.GetSpatialReference()
                ?? throw new InvalidOperationException(
                    "The layer has no coordinate system.");

            // ---- working space: geographic data go to a data-centered LAEA
            //      so hull areas are equal-area-true; projected data stay in
            //      their own SR with a meters-per-unit factor
            double mpu = 1.0;
            SpatialReference workSr = sr;
            if (sr.IsGeographic)
            {
                result.GeographicInput = true;
                double lon0 = raw.Average(p => p.X);
                double lat0 = raw.Average(p => p.Y);
                // The equal-area working projection is built on the data's
                // OWN geographic coordinate system, so no datum transformation
                // is involved in going to it and back (2026-09-23: it used to
                // hard-code WGS 1984, a datum change for NAD27 or NAD83 data).
                string wkt = string.Format(CultureInfo.InvariantCulture,
                    "PROJCS[\"LoCoH_Sweep_LAEA\"," + sr.Wkt + "," +
                    "PROJECTION[\"Lambert_Azimuthal_Equal_Area\"]," +
                    "PARAMETER[\"False_Easting\",0.0]," +
                    "PARAMETER[\"False_Northing\",0.0]," +
                    "PARAMETER[\"Central_Meridian\",{0:0.########}]," +
                    "PARAMETER[\"Latitude_Of_Origin\",{1:0.########}]," +
                    "UNIT[\"Meter\",1.0]]", lon0, lat0);
                workSr = SpatialReferenceBuilder.CreateSpatialReference(wkt);
                for (int i = 0; i < raw.Count; i++)
                    raw[i] = (MapPoint)GeometryEngine.Instance
                        .Project(raw[i], workSr);
            }
            else
            {
                try { mpu = sr.Unit?.ConversionFactor ?? 1.0; }
                catch { mpu = 1.0; }
                if (mpu <= 0) mpu = 1.0;
            }

            int n = raw.Count;
            result.N = n;
            var xs = new double[n];
            var ys = new double[n];
            for (int i = 0; i < n; i++) { xs[i] = raw[i].X; ys[i] = raw[i].Y; }

            // ---- scale statistics (working units) --------------------------
            // max nearest-neighbor distance (r1 = half of it) and the max
            // pairwise distance (a1); the pairwise max is found on the convex
            // hull's vertices, so the brute-force pass stays tiny
            double maxNN = 0;
            for (int i = 0; i < n; i++)
            {
                double best = double.MaxValue;
                for (int j = 0; j < n; j++)
                {
                    if (j == i) continue;
                    double dx = xs[j] - xs[i], dy = ys[j] - ys[i];
                    double dd = dx * dx + dy * dy;
                    if (dd < best && dd > 0) best = dd;
                }
                if (best < double.MaxValue)
                    maxNN = Math.Max(maxNN, Math.Sqrt(best));
                if ((i & 511) == 0) ct.ThrowIfCancellationRequested();
            }
            double maxPair = MaxPairwise(xs, ys, workSr);
            if (maxPair <= 0 || maxNN <= 0)
                throw new InvalidOperationException(
                    "The points all coincide -- no sweep is possible.");

            // ---- the sweep sequence + the published heuristic --------------
            double[] sweep;
            double heuristic;
            if (method == "k")
            {
                heuristic = Math.Max(3, Math.Min(n,
                    (int)Math.Round(Math.Sqrt(n))));
                sweep = LogSpacedInts(3, n, nSteps, (int)heuristic);
                result.HeuristicParam = heuristic;
                result.HeuristicLabel = "k₁ = √n = " +
                    (int)heuristic;
            }
            else if (method == "a")
            {
                heuristic = maxPair;
                sweep = LogSpaced(maxPair * 0.02, maxPair, nSteps, heuristic);
                result.HeuristicParam = heuristic * mpu;
                result.HeuristicLabel = "a₁ = max pairwise = " +
                    FormatMeters(heuristic * mpu);
            }
            else
            {
                heuristic = maxNN / 2.0;
                double hi = Math.Min(heuristic * 8.0, maxPair / 2.0);
                if (hi <= heuristic) hi = heuristic * 2.0;
                sweep = LogSpaced(heuristic / 4.0, hi, nSteps, heuristic);
                result.HeuristicParam = heuristic * mpu;
                result.HeuristicLabel = "r₁ = ½ max NN dist = " +
                    FormatMeters(heuristic * mpu);
            }

            // ---- the sweep -------------------------------------------------
            int total = sweep.Length * n;
            int done = 0;
            foreach (double value in sweep)
            {
                ct.ThrowIfCancellationRequested();
                string valLabel = method == "k"
                    ? "k = " + (int)value
                    : method + " = " + FormatMeters(value * mpu);
                var step = new LocohSweepStep
                { Param = method == "k" ? value : value * mpu };

                // hulls for every focal point, by this variant's rule
                var hulls = new List<(Polygon poly, double area, int count,
                                      int[] members)>(n);
                var drow = new double[n];
                var order = new int[n];
                for (int i = 0; i < n; i++)
                {
                    for (int j = 0; j < n; j++)
                    {
                        double dx = xs[j] - xs[i], dy = ys[j] - ys[i];
                        drow[j] = Math.Sqrt(dx * dx + dy * dy);
                        order[j] = j;
                    }
                    Array.Sort((double[])drow.Clone(), order);

                    int selCount;
                    if (method == "k")
                        selCount = Math.Min((int)value, n);
                    else if (method == "a")
                    {
                        double cum = 0;
                        selCount = 0;
                        for (int q = 0; q < n; q++)
                        {
                            cum += DistOf(drow, order, q);
                            if (cum > value) break;
                            selCount++;
                        }
                        // Getz et al. 2007: always the focal point and its
                        // two nearest neighbours, so every hull is a polygon
                        selCount = Math.Max(selCount, Math.Min(3, n));
                    }
                    else
                    {
                        selCount = 0;
                        for (int q = 0; q < n; q++)
                        {
                            if (DistOf(drow, order, q) > value) break;
                            selCount++;
                        }
                        selCount = Math.Max(selCount, Math.Min(3, n));
                    }

                    var members = new int[selCount];
                    Array.Copy(order, members, selCount);
                    var coords = new List<Coordinate2D>(selCount);
                    foreach (int m in members)
                        coords.Add(new Coordinate2D(xs[m], ys[m]));
                    var mp = MultipointBuilderEx.CreateMultipoint(
                        coords.Select(c => MapPointBuilderEx.CreateMapPoint(
                            c, workSr)), workSr);
                    var hull = GeometryEngine.Instance.ConvexHull(mp)
                        as Polygon;
                    if (hull != null && !hull.IsEmpty && hull.Area != 0)
                        hulls.Add((hull, Math.Abs(hull.Area), selCount,
                                   members));

                    done++;
                    if ((i & 127) == 0)
                    {
                        ct.ThrowIfCancellationRequested();
                        progress?.Invoke(done, total,
                            valLabel + " — building hulls (" +
                            (i + 1).ToString("N0") + " of " +
                            n.ToString("N0") + ")...");
                    }
                }
                if (hulls.Count == 0) { result.Steps.Add(step); continue; }

                // the variant's own sort: k by area ascending (every hull
                // holds k points, so small = dense); a and r by point count
                // descending, smaller area first on ties
                List<(Polygon poly, double area, int count, int[] members)>
                    sorted = method == "k"
                        ? hulls.OrderBy(h => h.area).ToList()
                        : hulls.OrderByDescending(h => h.count)
                               .ThenBy(h => h.area).ToList();

                // walk the cumulative union WITHOUT building it: a point can
                // only become newly covered by the hull just added, so the
                // marks stay exact and only three batch unions (at the
                // crossings) are ever needed
                progress?.Invoke(done, total, valLabel +
                    " — cumulative isopleths...");
                var covered = new bool[n];
                int cnt = 0;
                int need50 = (int)Math.Ceiling(0.50 * n);
                int need75 = (int)Math.Ceiling(0.75 * n);
                int need95 = (int)Math.Ceiling(0.95 * n);
                int idx50 = -1, idx75 = -1, idx95 = -1;
                for (int h = 0; h < sorted.Count && idx95 < 0; h++)
                {
                    foreach (int m in sorted[h].members)
                        if (!covered[m]) { covered[m] = true; cnt++; }
                    var env = sorted[h].poly.Extent;
                    for (int j = 0; j < n; j++)
                    {
                        if (covered[j]) continue;
                        if (xs[j] < env.XMin || xs[j] > env.XMax ||
                            ys[j] < env.YMin || ys[j] > env.YMax) continue;
                        if (GeometryEngine.Instance.Intersects(
                                sorted[h].poly, raw[j]))
                        { covered[j] = true; cnt++; }
                    }
                    if (idx50 < 0 && cnt >= need50) idx50 = h;
                    if (idx75 < 0 && cnt >= need75) idx75 = h;
                    if (idx95 < 0 && cnt >= need95) idx95 = h;
                }
                double AreaAt(int idx)
                {
                    if (idx < 0) return double.NaN;
                    var u = GeometryEngine.Instance.Union(
                        sorted.Take(idx + 1).Select(h => (Geometry)h.poly));
                    double a = Math.Abs((u as Polygon)?.Area ?? 0);
                    return a * mpu * mpu / 1e6;
                }
                step.AreaKm50 = AreaAt(idx50);
                step.AreaKm75 = AreaAt(idx75);
                step.AreaKm95 = AreaAt(idx95);
                result.Steps.Add(step);
            }

            if (result.Steps.All(s => double.IsNaN(s.AreaKm95)))
                throw new InvalidOperationException(
                    "No sweep value produced usable hulls -- are the " +
                    "points nearly collinear?");
            if (result.Steps.Any(s => double.IsNaN(s.AreaKm95)))
                result.Note = "Some sweep values left more than 5% of the " +
                    "points in degenerate (collinear/duplicate) hulls; " +
                    "those points are omitted from the 95% curve.";
            progress?.Invoke(total, total, "Done.");
            return result;
        }

        // -----------------------------------------------------------------
        //  helpers
        // -----------------------------------------------------------------
        private static double DistOf(double[] drowUnsorted, int[] order,
            int rank)
        {
            // drowUnsorted is the raw row; order[rank] is the point at this
            // rank, so its distance is just the row value there. (The row
            // passed to Array.Sort was a clone; the original stays indexed
            // by point.)
            return drowUnsorted[order[rank]];
        }

        private static double MaxPairwise(double[] xs, double[] ys,
            SpatialReference sr)
        {
            // the farthest pair lies on the convex hull, so collect hull
            // vertices first and brute-force only those
            int n = xs.Length;
            var mp = MultipointBuilderEx.CreateMultipoint(
                Enumerable.Range(0, n).Select(i =>
                    MapPointBuilderEx.CreateMapPoint(xs[i], ys[i], sr)), sr);
            var hull = GeometryEngine.Instance.ConvexHull(mp);
            var verts = new List<(double x, double y)>();
            if (hull is Polygon pg)
                foreach (var p in pg.Points) verts.Add((p.X, p.Y));
            else if (hull is Polyline pl)
                foreach (var p in pl.Points) verts.Add((p.X, p.Y));
            if (verts.Count < 2)
                for (int i = 0; i < n; i++) verts.Add((xs[i], ys[i]));
            double best = 0;
            for (int i = 0; i < verts.Count; i++)
                for (int j = i + 1; j < verts.Count; j++)
                {
                    double dx = verts[j].x - verts[i].x;
                    double dy = verts[j].y - verts[i].y;
                    best = Math.Max(best, dx * dx + dy * dy);
                }
            return Math.Sqrt(best);
        }

        private static double[] LogSpaced(double lo, double hi, int steps,
            double include)
        {
            var vals = new SortedSet<double>();
            double llo = Math.Log(lo), lhi = Math.Log(hi);
            for (int i = 0; i < steps; i++)
                vals.Add(Math.Exp(llo + (lhi - llo) * i / (steps - 1.0)));
            if (include >= lo && include <= hi) vals.Add(include);
            return vals.ToArray();
        }

        private static double[] LogSpacedInts(int lo, int hi, int steps,
            int include)
        {
            var vals = new SortedSet<int>();
            double llo = Math.Log(lo), lhi = Math.Log(hi);
            for (int i = 0; i < steps; i++)
                vals.Add((int)Math.Round(Math.Exp(
                    llo + (lhi - llo) * i / (steps - 1.0))));
            if (include >= lo && include <= hi) vals.Add(include);
            return vals.Select(v => (double)v).ToArray();
        }

        internal static string FormatMeters(double m)
        {
            if (double.IsNaN(m)) return "";
            if (m >= 10000)
                return (m / 1000).ToString("0.##",
                    CultureInfo.InvariantCulture) + " km";
            return m.ToString(m >= 100 ? "0" : "0.#",
                CultureInfo.InvariantCulture) + " m";
        }
    }
}
