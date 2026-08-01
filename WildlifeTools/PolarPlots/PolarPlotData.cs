using System;
using System.Collections.Generic;
using System.Linq;
using ArcGIS.Core.Data;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Mapping;

namespace WildlifeTools.PolarPlots
{
    /// <summary>
    /// Reads azimuths and weights from a map layer or standalone table for the
    /// polar plot: azimuths from a numeric field, or derived from polyline
    /// geometry (start-to-end bearing; great-circle initial bearing on
    /// geographic data, planar otherwise -- matching the original tool's
    /// geodesic start-azimuth behavior for lat/long). Applies weight, selected-
    /// only, add-reverse, and exclude-negative options. Call inside QueuedTask.
    /// </summary>
    internal static class PolarPlotData
    {
        internal sealed class Source
        {
            public string Name;
            public bool IsPolyline;
            public FeatureLayer FeatureLayer;      // one of these is set
            public StandaloneTable StandaloneTable;
            public override string ToString() => Name;
        }

        internal sealed class Result
        {
            public List<double> Azimuths = new List<double>();
            public List<double> Weights = new List<double>();
            public bool Weighted;
            public int RawCount;                   // rows read
            public int ExcludedNegative;
            public int UsableCount => Azimuths.Count;
        }

        private const double R2D = 180.0 / Math.PI;
        private const double D2R = Math.PI / 180.0;
        private const double EarthR = 6371000.0;   // m; relative length only

        /// <summary>Feature layers + standalone tables in the active map.</summary>
        public static List<Source> ListSources(Map map)
        {
            var list = new List<Source>();
            if (map == null) return list;
            foreach (var fl in map.GetLayersAsFlattenedList().OfType<FeatureLayer>())
            {
                bool poly;
                try { poly = fl.ShapeType.ToString().IndexOf("Polyline",
                                 StringComparison.OrdinalIgnoreCase) >= 0; }
                catch { poly = false; }
                list.Add(new Source { Name = fl.Name, IsPolyline = poly, FeatureLayer = fl });
            }
            foreach (var st in map.GetStandaloneTablesAsFlattenedList())
                list.Add(new Source { Name = st.Name, IsPolyline = false, StandaloneTable = st });
            return list;
        }

        /// <summary>Numeric field names on the source (for the azimuth/weight combos).</summary>
        public static List<string> NumericFields(Source src)
        {
            var names = new List<string>();
            using (Table table = GetTable(src))
            {
                if (table == null) return names;
                foreach (Field f in table.GetDefinition().GetFields())
                {
                    var t = f.FieldType;
                    if (t == FieldType.Double || t == FieldType.Single ||
                        t == FieldType.Integer || t == FieldType.SmallInteger ||
                        t == FieldType.BigInteger)
                        names.Add(f.Name);
                }
            }
            return names;
        }

        private static Table GetTable(Source src)
        {
            if (src == null) return null;
            if (src.FeatureLayer != null) return src.FeatureLayer.GetTable();
            return src.StandaloneTable?.GetTable();
        }

        public static Result Read(Source src, PolarPlotSettings s)
        {
            var res = new Result();
            using (Table table = GetTable(src))
            {
                if (table == null) return res;
                var def = table.GetDefinition();
                int azIdx = (s.AzimuthOption == PolarAzimuthOption.ByAttribute)
                    ? def.FindField(s.AzimuthField) : -1;
                int wIdx = (s.WeightOption == PolarWeightOption.ByAttribute)
                    ? def.FindField(s.WeightField) : -1;
                res.Weighted = s.WeightOption != PolarWeightOption.None;

                var qf = new QueryFilter();
                if (s.UseSelectedOnly)
                {
                    IReadOnlyList<long> ids = GetSelectedIds(src);
                    if (ids != null && ids.Count > 0)
                        qf.ObjectIDs = ids.ToList();
                }

                using (RowCursor cur = table.Search(qf, false))
                {
                    while (cur.MoveNext())
                    {
                        using (Row row = cur.Current)
                        {
                            res.RawCount++;
                            double az;
                            if (s.AzimuthOption == PolarAzimuthOption.ByPolyline)
                            {
                                var feat = row as Feature;
                                var poly = feat?.GetShape() as Polyline;
                                if (poly == null || poly.PointCount < 2) continue;
                                if (!TryPolylineAzimuth(poly, out az)) continue;
                            }
                            else
                            {
                                if (azIdx < 0) continue;
                                object v = row[azIdx];
                                if (v == null || !TryDouble(v, out az)) continue;
                            }

                            double weight = 1.0;
                            if (s.WeightOption == PolarWeightOption.ByLength)
                            {
                                var feat = row as Feature;
                                var poly = feat?.GetShape() as Polyline;
                                weight = (poly != null) ? PolylineLength(poly) : 0.0;
                            }
                            else if (s.WeightOption == PolarWeightOption.ByAttribute)
                            {
                                if (wIdx < 0) continue;
                                if (!TryDouble(row[wIdx], out weight)) continue;
                            }
                            if (weight <= 0.0 && res.Weighted) continue;

                            AddValue(res, az, weight, s);
                            if (s.AddReverse)
                                AddValue(res, CircularStats.Mod360(az + 180.0), weight, s);
                        }
                    }
                }
            }
            return res;
        }

        private static void AddValue(Result res, double az, double weight, PolarPlotSettings s)
        {
            // Exclude-negative handling before normalizing into 0-360.
            if (s.ExcludeMinusOneOnly)
            {
                if (Math.Abs(az - (-1.0)) < 1e-9) { res.ExcludedNegative++; return; }
            }
            else if (s.ExcludeNegative && az < 0.0)
            {
                res.ExcludedNegative++;
                return;
            }
            res.Azimuths.Add(CircularStats.Mod360(az));
            res.Weights.Add(res.Weighted ? weight : 1.0);
        }

        private static IReadOnlyList<long> GetSelectedIds(Source src)
        {
            try
            {
                if (src.FeatureLayer != null)
                    return src.FeatureLayer.GetSelection()?.GetObjectIDs();
                return src.StandaloneTable?.GetSelection()?.GetObjectIDs();
            }
            catch { return null; }
        }

        // ---- azimuth + length from polyline geometry ------------------------
        private static bool TryPolylineAzimuth(Polyline poly, out double azimuthDeg)
        {
            azimuthDeg = 0.0;
            var pts = poly.Points;
            if (pts == null || pts.Count < 2) return false;
            MapPoint a = pts[0], b = pts[pts.Count - 1];
            var sr = poly.SpatialReference;
            if (sr != null && sr.IsGeographic)
            {
                // Great-circle initial bearing from (lon1,lat1) to (lon2,lat2).
                double lat1 = a.Y * D2R, lat2 = b.Y * D2R;
                double dLon = (b.X - a.X) * D2R;
                double y = Math.Sin(dLon) * Math.Cos(lat2);
                double x = Math.Cos(lat1) * Math.Sin(lat2) -
                           Math.Sin(lat1) * Math.Cos(lat2) * Math.Cos(dLon);
                azimuthDeg = CircularStats.Mod360(Math.Atan2(y, x) * R2D);
            }
            else
            {
                // Planar compass azimuth: atan2(east, north).
                azimuthDeg = CircularStats.Mod360(Math.Atan2(b.X - a.X, b.Y - a.Y) * R2D);
            }
            return true;
        }

        private static double PolylineLength(Polyline poly)
        {
            var sr = poly.SpatialReference;
            if (sr != null && sr.IsGeographic)
            {
                // Great-circle length (relative weight only; radius arbitrary).
                double total = 0.0;
                var pts = poly.Points;
                for (int i = 1; i < pts.Count; i++)
                    total += GreatCircle(pts[i - 1], pts[i]);
                return total;
            }
            return poly.Length;
        }

        private static double GreatCircle(MapPoint a, MapPoint b)
        {
            double lat1 = a.Y * D2R, lat2 = b.Y * D2R;
            double dLat = (b.Y - a.Y) * D2R, dLon = (b.X - a.X) * D2R;
            double h = Math.Sin(dLat / 2) * Math.Sin(dLat / 2) +
                       Math.Cos(lat1) * Math.Cos(lat2) *
                       Math.Sin(dLon / 2) * Math.Sin(dLon / 2);
            return 2 * EarthR * Math.Asin(Math.Min(1.0, Math.Sqrt(h)));
        }

        private static bool TryDouble(object v, out double d)
        {
            d = 0.0;
            if (v == null) return false;
            try { d = Convert.ToDouble(v); return !double.IsNaN(d); }
            catch { return false; }
        }
    }
}
