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
using System.Linq;
using ArcGIS.Core.Data;
using ArcGIS.Core.Data.Raster;
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
            public string Name;                    // as listed ("x  [raster]")
            public bool IsPolyline;
            public FeatureLayer FeatureLayer;      // one of these is set
            public StandaloneTable StandaloneTable;
            public BasicRasterLayer RasterLayer;   // aspect raster source
            public bool IsRaster => RasterLayer != null;
            public override string ToString() => Name;

            /// <summary>The layer or table behind this entry.</summary>
            public MapMember Member => (MapMember)FeatureLayer
                ?? (MapMember)StandaloneTable ?? RasterLayer;

            /// <summary>The name as it stands in the Contents pane NOW (no
            /// "[raster]" tag), for titles, report headers and file names.</summary>
            public string LayerName
            {
                get
                {
                    try { return Member?.Name ?? Name ?? ""; }
                    catch { return Name ?? ""; }
                }
            }

            /// <summary>The list text for the member's current name.</summary>
            public string ListName() =>
                IsRaster ? LayerName + "  [raster]" : LayerName;

            public bool SameMember(Source other)
            {
                var a = Member; var b = other?.Member;
                if (a == null || b == null) return false;
                if (ReferenceEquals(a, b)) return true;
                try { return !string.IsNullOrEmpty(a.URI) && a.URI == b.URI; }
                catch { return false; }
            }
        }

        internal sealed class Result
        {
            public List<double> Azimuths = new List<double>();
            public List<double> Weights = new List<double>();
            public List<double> Magnitudes = new List<double>();  // NaN = missing
            public bool Weighted;
            public bool HasMagnitudes;             // magnitude field was read
            public bool IsBinned;                  // raster: 0.1-degree count bins
            public long RawCount;                  // rows read / cells examined
            public long KeptCount;                 // records kept (before reverses)
            public long TableRows = -1;            // rows in the whole table
            public bool SelectionUsed;             // a selection limited the read
            public bool SelectionEmpty;            // asked for, nothing selected
            public bool WeightsAreCounts;          // weight field holds counts
            // Axial data: every kept ORIGINAL direction doubled (mod 360),
            // one entry per observation, for the statistics. The Azimuths
            // list holds both ends for the plot.
            public List<double> AxialAzimuths = new List<double>();
            public List<double> AxialWeights = new List<double>();
            public long ExcludedNegative;
            public string SamplingNote;            // raster: how cells were read
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
            foreach (var rl in map.GetLayersAsFlattenedList().OfType<BasicRasterLayer>())
                list.Add(new Source { Name = rl.Name + "  [raster]", RasterLayer = rl });
            return list;
        }

        /// <summary>Numeric field names on the source (for the azimuth/weight combos).</summary>
        public static List<string> NumericFields(Source src)
        {
            var names = new List<string>();
            if (src == null || src.IsRaster) return names;
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

        internal static Table GetTable(Source src)
        {
            if (src == null) return null;
            if (src.FeatureLayer != null) return src.FeatureLayer.GetTable();
            return src.StandaloneTable?.GetTable();
        }

        /// <summary>The source's rows AS THE MAP SHOWS THEM: the layer's or
        /// standalone table's definition query applied (see
        /// <see cref="LayerRows"/>). GetTable() is still the place to read
        /// the definition and field indexes from -- but never search it
        /// directly, or the definition query is lost. MCT.</summary>
        internal static RowCursor Search(Source src, QueryFilter filter,
                                         bool recycling)
        {
            if (src == null) return null;
            if (src.FeatureLayer != null)
                return LayerRows.Search(src.FeatureLayer, filter, recycling);
            return LayerRows.Search(src.StandaloneTable, filter, recycling);
        }

        /// <summary>How many rows the source shows (its definition query
        /// applied); -1 when it cannot be counted. MCT.</summary>
        internal static long CountRows(Source src)
        {
            if (src?.FeatureLayer != null) return LayerRows.Count(src.FeatureLayer);
            if (src?.StandaloneTable != null) return LayerRows.Count(src.StandaloneTable);
            return -1;
        }

        /// <summary>Finished binned raster read, cached by the window for as
        /// long as the Polar Plots window stays open so switching back to an
        /// already-read raster is instant. Counts are PRE-reverse-fold, so the
        /// Add Reverse checkbox also reuses the cache.</summary>
        internal sealed class RasterBinCache
        {
            public double[] Counts;
            public long Examined;
            public long Excluded;
            public string SamplingNote;
        }

        public static Result Read(Source src, PolarPlotSettings s,
                                  IProgress<double> progress = null,
                                  Dictionary<string, RasterBinCache> rasterCache = null)
        {
            if (src != null && src.IsRaster)
                return ReadRaster(src, s, progress, rasterCache);
            var res = new Result();
            using (Table table = GetTable(src))
            {
                if (table == null) return res;
                var def = table.GetDefinition();
                int azIdx = (s.AzimuthOption == PolarAzimuthOption.ByAttribute)
                    ? def.FindField(s.AzimuthField) : -1;
                int wIdx = (s.WeightOption == PolarWeightOption.ByAttribute)
                    ? def.FindField(s.WeightField) : -1;
                int magIdx = string.IsNullOrEmpty(s.MagnitudeField)
                    ? -1 : def.FindField(s.MagnitudeField);
                res.Weighted = s.WeightOption != PolarWeightOption.None;
                res.WeightsAreCounts = s.WeightsAreCounts &&
                    s.WeightOption == PolarWeightOption.ByAttribute;
                res.HasMagnitudes = magIdx >= 0;
                try { res.TableRows = CountRows(src); } catch { }

                var qf = new QueryFilter();
                if (s.UseSelectedOnly)
                {
                    IReadOnlyList<long> ids = GetSelectedIds(src);
                    if (ids != null && ids.Count > 0)
                    {
                        qf.ObjectIDs = ids.ToList();
                        res.SelectionUsed = true;
                    }
                    else res.SelectionEmpty = true;
                }

                using (RowCursor cur = Search(src, qf, false))
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

                            double mag = double.NaN;
                            if (magIdx >= 0 && !TryDouble(row[magIdx], out mag))
                                mag = double.NaN;

                            // The reverse goes in only with its own record:
                            // an excluded -1 must not contribute a 179.
                            if (AddValue(res, az, weight, mag, s))
                            {
                                res.KeptCount++;
                                if (s.Axial)
                                {
                                    res.AxialAzimuths.Add(CircularStats.Mod360(2.0 * az));
                                    res.AxialWeights.Add(res.Weighted ? weight : 1.0);
                                }
                                if (s.AddReverse || s.Axial)
                                    AddValue(res, CircularStats.Mod360(az + 180.0),
                                             weight, mag, s);
                            }
                        }
                    }
                }
            }
            return res;
        }

        /// <summary>Adds one direction; false when it was excluded.</summary>
        private static bool AddValue(Result res, double az, double weight, double mag,
                                     PolarPlotSettings s)
        {
            // Exclude-negative handling before normalizing into 0-360.
            if (s.ExcludeMinusOneOnly)
            {
                if (Math.Abs(az - (-1.0)) < 1e-9) { res.ExcludedNegative++; return false; }
            }
            else if (s.ExcludeNegative && az < 0.0)
            {
                res.ExcludedNegative++;
                return false;
            }
            res.Azimuths.Add(CircularStats.Mod360(az));
            res.Weights.Add(res.Weighted ? weight : 1.0);
            res.Magnitudes.Add(mag);
            return true;
        }

        internal static IReadOnlyList<long> GetSelectedIds(Source src)
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
            // Zero-length lines and CLOSED loops (identical endpoints) have no
            // start-to-end direction; without this, atan2(0,0) = 0 would count
            // each one as a fabricated due-north observation.
            if (a.X == b.X && a.Y == b.Y) return false;
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

        internal static double PolylineLength(Polyline poly)
        {
            var sr = poly.SpatialReference;
            if (sr != null && sr.IsGeographic)
            {
                // Geodesic length in meters: follows true curves and does
                // not count the jump between the parts of a multipart line.
                try
                {
                    double g = GeometryEngine.Instance.GeodesicLength(poly);
                    if (g > 0.0 && !double.IsNaN(g)) return g;
                }
                catch { }
                // Fallback: great-circle length, part by part.
                double total = 0.0;
                foreach (var part in poly.Parts)
                    foreach (var seg in part)
                        total += GreatCircle(seg.StartPoint, seg.EndPoint);
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

        internal static bool TryDouble(object v, out double d)
        {
            d = 0.0;
            if (v == null) return false;
            try { d = Convert.ToDouble(v); return !double.IsNaN(d); }
            catch { return false; }
        }

        // ---- raster (aspect) source -----------------------------------------
        // A raster is never materialized as raw azimuths: cell values are
        // accumulated on the fly into fine 0.1-degree count bins, so a
        // billion-cell aspect raster reduces to at most 3,600 weighted records
        // and everything downstream (plots, stats) uses the existing weighted
        // pipeline. The whole raster is always considered (users must clip
        // first to analyze a study area), except that very large rasters are
        // systematically sampled -- never via averaged pyramids, which corrupt
        // circular values (mean of 359 and 1 is NOT 180).
        private const int RasterBins = 3600;                 // 0.1-degree bins
        // The class array is 2 x RasterBins long: [b] is the weight (cell
        // count) of class b, [RasterBins + b] the weighted SUM of the values
        // that fell in it. Each class is then reported by the mean of its own
        // values, not its center, so a class holding one distinct value
        // (whole-degree rasters, attribute tables) gives that value exactly.
        private const long AutoSampleTarget = 100_000_000;   // cells, auto mode

        private static Result ReadRaster(Source src, PolarPlotSettings s,
                                         IProgress<double> progress,
                                         Dictionary<string, RasterBinCache> cache)
        {
            var res = new Result { Weighted = true, IsBinned = true };

            // Cache key: the raster plus every option that changes what gets
            // accumulated (the reverse fold happens after the cache).
            string key = (src.RasterLayer?.URI ?? src.Name) + "|" +
                s.RasterSampleStep + "|" + s.ExcludeNegative + "|" +
                s.ExcludeMinusOneOnly;
            if (cache != null && cache.TryGetValue(key, out var hit))
            {
                progress?.Report(1.0);
                FinishRasterResult(res, (double[])hit.Counts.Clone(),
                    hit.Examined, hit.Excluded, hit.SamplingNote, s);
                return res;
            }

            var raster = src.RasterLayer.GetRaster();
            if (raster == null) return res;
            int w = raster.GetWidth(), h = raster.GetHeight();
            if (w <= 0 || h <= 0) return res;
            long totalCells = (long)w * h;

            var counts = new double[2 * RasterBins];
            long examined = 0, excluded = 0;

            // Fast path: an integer raster with a Value/Count attribute table
            // IS the binned data -- no pixels need to be read at all.
            bool fromVat = s.RasterSampleStep <= 1 &&
                TryReadVat(raster, s, counts, ref examined, ref excluded);

            if (!fromVat)
            {
                int step = s.RasterSampleStep;
                if (step <= 0)                    // auto: full read up to the
                {                                 // target, then sample
                    step = 1;
                    while (totalCells / ((long)step * step) > AutoSampleTarget)
                        step *= 2;
                }
                double noData = double.NaN;
                bool hasNoData = false;
                try
                {
                    object nd = raster.GetNoDataValue();
                    if (nd != null && TryDouble(nd, out double ndv))
                    { noData = ndv; hasNoData = true; }
                }
                catch { }

                // Full-width strips; 1-row strips when sampling so skipped
                // rows are never read (that is where the I/O saving is).
                int blockRows = step == 1 ? (w == 256 ? 255 : 256) : 1;
                using (PixelBlock pb = raster.CreatePixelBlock(w, blockRows))
                {
                    // The last strip is usually shorter than blockRows; it gets
                    // its own exactly-sized block so Read never has to fill a
                    // block that extends past the bottom edge of the raster.
                    PixelBlock tail = null;
                    try
                    {
                        int rowStep = step == 1 ? blockRows : step;
                        for (int y = 0; y < h; y += rowStep)
                        {
                            int rows = Math.Min(blockRows, h - y);
                            PixelBlock blk = pb;
                            if (rows < blockRows)
                            {
                                tail = tail ?? raster.CreatePixelBlock(w, rows);
                                blk = tail;
                            }
                            raster.Read(0, y, blk);
                            Array data = blk.GetPixelData(0, false);
                            Array mask = null;
                            try { mask = blk.GetNoDataMask(0, false); } catch { }
                            AccumulateBlock(data, mask, w, rows, step, hasNoData,
                                noData, s, counts, ref examined, ref excluded);
                            progress?.Report(Math.Min(1.0, (y + rowStep) / (double)h));
                        }
                    }
                    finally { tail?.Dispose(); }
                }
                if (step > 1)
                    res.SamplingNote = "sampled every " + step + "×" + step +
                        " cells (1/" + (step * (long)step) + " of " +
                        totalCells.ToString("N0") + ")";
            }
            else
            {
                res.SamplingNote = "from the raster attribute table";
                progress?.Report(1.0);
            }

            if (cache != null)
                cache[key] = new RasterBinCache
                {
                    Counts = (double[])counts.Clone(),
                    Examined = examined,
                    Excluded = excluded,
                    SamplingNote = res.SamplingNote,
                };
            FinishRasterResult(res, counts, examined, excluded, res.SamplingNote, s);
            return res;
        }

        /// <summary>Reverse fold + bins-to-records conversion, shared by fresh
        /// reads and cache hits.</summary>
        private static void FinishRasterResult(Result res, double[] counts,
            long examined, long excluded, string samplingNote, PolarPlotSettings s)
        {
            // The mean value of class b (its center if no sums were kept).
            double ClassMean(double[] c, int b) =>
                c.Length >= 2 * RasterBins && c[b] > 0
                    ? c[RasterBins + b] / c[b]
                    : (b + 0.5) * (360.0 / RasterBins);
            if (s.Axial)
            {
                // Doubled angles of the ORIGINAL cells, one entry per bin.
                for (int b = 0; b < RasterBins; b++)
                    if (counts[b] > 0)
                    {
                        res.AxialAzimuths.Add(CircularStats.Mod360(2.0 * ClassMean(counts, b)));
                        res.AxialWeights.Add(counts[b]);
                    }
            }
            if (s.AddReverse || s.Axial)
            {
                // Fold each class onto the class 180 degrees away. The values
                // of class b2 reversed are v + 180, or v - 180 past 360, and
                // they all land in class b, so the sum shifts by +/-180 each.
                var orig = (double[])counts.Clone();
                bool sums = orig.Length >= 2 * RasterBins;
                for (int b = 0; b < RasterBins; b++)
                {
                    int b2 = (b + RasterBins / 2) % RasterBins;
                    counts[b] = orig[b] + orig[b2];
                    if (sums)
                        counts[RasterBins + b] = orig[RasterBins + b] +
                            orig[RasterBins + b2] +
                            (b2 >= RasterBins / 2 ? -180.0 : 180.0) * orig[b2];
                }
                // `examined` stays the number of cells actually read: the
                // report's "Cells = N examined" line (and the excluded count
                // beside it) describe the raster, not the doubled plot.
            }
            for (int b = 0; b < RasterBins; b++)
                if (counts[b] > 0)
                {
                    res.Azimuths.Add(ClassMean(counts, b));
                    res.Weights.Add(counts[b]);
                }
            res.RawCount = examined;
            res.ExcludedNegative = excluded;
            res.SamplingNote = samplingNote;
        }

        /// <summary>Accumulate one cell value (with weight, for the VAT path)
        /// into the bins, applying the flat/negative exclusion rules.</summary>
        private static void AccumulateValue(double v, double weight,
            PolarPlotSettings s, double[] counts, ref long examined, ref long excluded)
        {
            if (double.IsNaN(v)) return;
            examined += (long)weight;
            if (s.ExcludeMinusOneOnly)
            {
                if (Math.Abs(v + 1.0) < 1e-9) { excluded += (long)weight; return; }
            }
            else if (s.ExcludeNegative && v < 0.0)
            {
                excluded += (long)weight;
                return;
            }
            double a = CircularStats.Mod360(v);
            int b = (int)(a * (RasterBins / 360.0));
            if (b >= RasterBins) { b = 0; a = 0.0; }
            counts[b] += weight;
            counts[RasterBins + b] += weight * a;
        }

        private static bool TryReadVat(Raster raster, PolarPlotSettings s,
            double[] counts, ref long examined, ref long excluded)
        {
            try
            {
                using (Table vat = raster.GetAttributeTable())
                {
                    if (vat == null) return false;
                    var def = vat.GetDefinition();
                    int vIdx = def.FindField("Value");
                    int cIdx = def.FindField("Count");
                    if (vIdx < 0 || cIdx < 0) return false;
                    using (RowCursor cur = vat.Search(null, false))
                    {
                        while (cur.MoveNext())
                        {
                            using (Row row = cur.Current)
                            {
                                if (!TryDouble(row[vIdx], out double v)) continue;
                                if (!TryDouble(row[cIdx], out double c) || c <= 0) continue;
                                AccumulateValue(v, c, s, counts,
                                    ref examined, ref excluded);
                            }
                        }
                    }
                }
                return true;
            }
            catch { return false; }
        }

        /// <summary>Typed, non-boxing accumulation of one pixel-block strip.
        /// The SDK does not document the 2-D array's dimension order, so it is
        /// resolved against the block width (the block height is chosen to
        /// never equal the width).</summary>
        private static void AccumulateBlock(Array data, Array mask, int width,
            int rows, int colStep, bool hasNoData, double noData,
            PolarPlotSettings s, double[] counts, ref long examined, ref long excluded)
        {
            if (data == null) return;
            int d0 = data.GetLength(0);
            bool xFirst = d0 == width;             // data[x, y] vs data[y, x]
            var mk = mask as byte[,];
            if (mk != null && (mk.GetLength(0) != data.GetLength(0) ||
                               mk.GetLength(1) != data.GetLength(1)))
                mk = null;                         // shape mismatch: ignore mask
            long exLocal = 0, one = 0;             // ref params cannot be
                                                   // captured by local functions
            void Acc(double v, bool noDataCell)
            {
                if (noDataCell) return;
                if (hasNoData && v == noData) return;
                AccumulateValue(v, 1.0, s, counts, ref one, ref exLocal);
            }

            switch (data)
            {
                case float[,] a:
                    for (int r = 0; r < rows; r++)
                        for (int c = 0; c < width; c += colStep)
                            Acc(xFirst ? a[c, r] : a[r, c],
                                mk != null && (xFirst ? mk[c, r] : mk[r, c]) == 0);
                    break;
                case double[,] a:
                    for (int r = 0; r < rows; r++)
                        for (int c = 0; c < width; c += colStep)
                            Acc(xFirst ? a[c, r] : a[r, c],
                                mk != null && (xFirst ? mk[c, r] : mk[r, c]) == 0);
                    break;
                case int[,] a:
                    for (int r = 0; r < rows; r++)
                        for (int c = 0; c < width; c += colStep)
                            Acc(xFirst ? a[c, r] : a[r, c],
                                mk != null && (xFirst ? mk[c, r] : mk[r, c]) == 0);
                    break;
                case short[,] a:
                    for (int r = 0; r < rows; r++)
                        for (int c = 0; c < width; c += colStep)
                            Acc(xFirst ? a[c, r] : a[r, c],
                                mk != null && (xFirst ? mk[c, r] : mk[r, c]) == 0);
                    break;
                case ushort[,] a:
                    for (int r = 0; r < rows; r++)
                        for (int c = 0; c < width; c += colStep)
                            Acc(xFirst ? a[c, r] : a[r, c],
                                mk != null && (xFirst ? mk[c, r] : mk[r, c]) == 0);
                    break;
                case byte[,] a:
                    for (int r = 0; r < rows; r++)
                        for (int c = 0; c < width; c += colStep)
                            Acc(xFirst ? a[c, r] : a[r, c],
                                mk != null && (xFirst ? mk[c, r] : mk[r, c]) == 0);
                    break;
                default:
                    // Uncommon pixel types: boxed per-cell fallback.
                    for (int r = 0; r < rows; r++)
                        for (int c = 0; c < width; c += colStep)
                        {
                            object o = xFirst ? data.GetValue(c, r) : data.GetValue(r, c);
                            if (o != null && TryDouble(o, out double v))
                                Acc(v, mk != null && (xFirst ? mk[c, r] : mk[r, c]) == 0);
                        }
                    break;
            }
            examined += one;
            excluded += exLocal;
        }
    }
}
