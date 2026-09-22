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
using WildlifeTools.PolarPlots;

namespace WildlifeTools.SlopeGraphic
{
    /// <summary>
    /// Reads slope values for the Slope Graphic, using the same source model
    /// as Polar Plots (PolarPlotData.Source: feature layers, standalone
    /// tables, and rasters). Vector/table sources read a numeric field with
    /// optional weighting; raster sources stream cell values into exact
    /// running sums (source units) plus a fine ANGLE-space histogram for
    /// quantiles -- never materializing raw values -- with the same VAT fast
    /// path, systematic sampling, and per-window caching as the polar reader.
    /// The whole raster is always considered (clip first for a study area).
    /// Call inside QueuedTask.
    /// </summary>
    internal static class SlopeData
    {
        internal sealed class Options
        {
            public string FieldName = "";
            public PolarWeightOption WeightOption = PolarWeightOption.None;
            public string WeightField = "";
            public bool UseSelectedOnly;
            public SlopeUnit Unit = SlopeUnit.Degrees;
            public int RasterSampleStep;           // 0 auto / 1 all / else Nth
        }

        internal sealed class Result
        {
            public SlopeStats Stats;               // null when nothing usable
            public bool IsBinned;                  // raster source
            public long RawCount;                  // rows read / cells examined
            public long ExcludedInvalid;           // negative / beyond-vertical
            public string SamplingNote;
            public bool Weighted;
        }

        /// <summary>Angle-space histogram resolution: 0.01 degrees.</summary>
        private const int Bins = 9000;
        private const double BinDeg = 90.0 / Bins;
        private const long AutoSampleTarget = 100_000_000;

        internal sealed class RasterCache
        {
            public double[] Hist;                  // angle bins
            public double SumW, SumWX, SumWX2;     // source units
            public double Min, Max;
            public long Examined, Excluded;
            public string SamplingNote;
            public SlopeUnit Unit;                 // unit the sums are in
        }

        /// <summary>
        /// Inspects the raster dataset's METADATA GEOPROCESSING HISTORY for
        /// evidence of how the slope was computed: Esri's Slope and Surface
        /// Parameters tools record DEGREE or PERCENT_RISE in their lineage
        /// Process entries. Returns the detected unit, or null when the
        /// history is absent or ambiguous (the caller keeps its current
        /// default). The LAST slope-related process wins (most recent).
        /// Call inside QueuedTask.
        /// </summary>
        public static SlopeUnit? DetectRasterUnit(PolarPlotData.Source src)
        {
            try
            {
                if (src?.RasterLayer == null) return null;
                var raster = src.RasterLayer.GetRaster();
                var ds = raster?.GetRasterDataset();
                if (ds == null) return null;
                string dir = ds.GetDatastore()?.GetPath()?.LocalPath;
                string name = ds.GetName();
                if (string.IsNullOrEmpty(dir) || string.IsNullOrEmpty(name))
                    return null;
                string full = System.IO.Path.Combine(dir, name);

                var item = ArcGIS.Desktop.Core.ItemFactory.Instance.Create(full);
                string xml = (item as ArcGIS.Desktop.Core.IMetadata)?.GetXml();
                if (string.IsNullOrEmpty(xml)) return null;

                var doc = new System.Xml.XmlDocument();
                doc.LoadXml(xml);
                SlopeUnit? found = null;
                foreach (System.Xml.XmlNode node in doc.GetElementsByTagName("Process"))
                {
                    string text = node.OuterXml;
                    if (text.IndexOf("slope", StringComparison.OrdinalIgnoreCase) < 0 &&
                        text.IndexOf("SurfaceParameters", StringComparison.OrdinalIgnoreCase) < 0)
                        continue;
                    if (text.IndexOf("PERCENT_RISE", StringComparison.OrdinalIgnoreCase) >= 0)
                        found = SlopeUnit.Percent;
                    else if (System.Text.RegularExpressions.Regex.IsMatch(
                                 text, @"\bDEGREE\b",
                                 System.Text.RegularExpressions.RegexOptions.IgnoreCase))
                        found = SlopeUnit.Degrees;
                }
                return found;
            }
            catch { return null; }
        }

        public static Result Read(PolarPlotData.Source src, Options o,
                                  IProgress<double> progress = null,
                                  Dictionary<string, RasterCache> cache = null)
        {
            if (src != null && src.IsRaster) return ReadRaster(src, o, progress, cache);
            return ReadVector(src, o);
        }

        // ---- vector / table -------------------------------------------------
        private static Result ReadVector(PolarPlotData.Source src, Options o)
        {
            var res = new Result { Weighted = o.WeightOption != PolarWeightOption.None };
            var values = new List<double>();
            var weights = new List<double>();
            double maxValid = SlopeStats.MaxValid(o.Unit) * (1.0 + 1e-9);
            using (Table table = PolarPlotData.GetTable(src))
            {
                if (table == null) return res;
                var def = table.GetDefinition();
                int vIdx = def.FindField(o.FieldName);
                if (vIdx < 0) return res;
                int wIdx = (o.WeightOption == PolarWeightOption.ByAttribute)
                    ? def.FindField(o.WeightField) : -1;

                var qf = new QueryFilter();
                if (o.UseSelectedOnly)
                {
                    var ids = PolarPlotData.GetSelectedIds(src);
                    if (ids != null && ids.Count > 0) qf.ObjectIDs = ids.ToList();
                }
                using (RowCursor cur = table.Search(qf, false))
                {
                    while (cur.MoveNext())
                    {
                        using (Row row = cur.Current)
                        {
                            res.RawCount++;
                            if (!PolarPlotData.TryDouble(row[vIdx], out double v))
                                continue;
                            if (v < 0.0 || v > maxValid)
                            { res.ExcludedInvalid++; continue; }

                            double w = 1.0;
                            if (o.WeightOption == PolarWeightOption.ByLength)
                            {
                                var poly = (row as Feature)?.GetShape() as Polyline;
                                w = (poly != null) ? PolarPlotData.PolylineLength(poly) : 0.0;
                            }
                            else if (o.WeightOption == PolarWeightOption.ByAttribute)
                            {
                                if (wIdx < 0) continue;
                                if (!PolarPlotData.TryDouble(row[wIdx], out w)) continue;
                            }
                            if (w <= 0.0 && res.Weighted) continue;
                            values.Add(v);
                            weights.Add(res.Weighted ? w : 1.0);
                        }
                    }
                }
            }
            if (values.Count > 0)
                res.Stats = new SlopeStats(values, weights, false, o.Unit);
            return res;
        }

        // ---- raster ---------------------------------------------------------
        private static Result ReadRaster(PolarPlotData.Source src, Options o,
            IProgress<double> progress, Dictionary<string, RasterCache> cache)
        {
            var res = new Result { Weighted = true, IsBinned = true };
            string key = (src.RasterLayer?.URI ?? src.Name) + "|" +
                o.RasterSampleStep + "|" + (int)o.Unit;
            if (cache != null && cache.TryGetValue(key, out var hit))
            {
                progress?.Report(1.0);
                Finish(res, hit);
                return res;
            }

            var raster = src.RasterLayer.GetRaster();
            if (raster == null) return res;
            int w = raster.GetWidth(), h = raster.GetHeight();
            if (w <= 0 || h <= 0) return res;
            long totalCells = (long)w * h;

            var acc = new RasterCache
            {
                Hist = new double[Bins],
                Min = double.PositiveInfinity,
                Max = double.NegativeInfinity,
                Unit = o.Unit,
            };
            double maxValid = SlopeStats.MaxValid(o.Unit) * (1.0 + 1e-9);

            bool fromVat = o.RasterSampleStep <= 1 &&
                TryReadVat(raster, o.Unit, maxValid, acc);
            if (!fromVat)
            {
                int step = o.RasterSampleStep;
                if (step <= 0)
                {
                    step = 1;
                    while (totalCells / ((long)step * step) > AutoSampleTarget)
                        step *= 2;
                }
                double noData = double.NaN;
                bool hasNoData = false;
                try
                {
                    object nd = raster.GetNoDataValue();
                    if (nd != null && PolarPlotData.TryDouble(nd, out double ndv))
                    { noData = ndv; hasNoData = true; }
                }
                catch { }

                int blockRows = step == 1 ? (w == 256 ? 255 : 256) : 1;
                using (PixelBlock pb = raster.CreatePixelBlock(w, blockRows))
                {
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
                                noData, o.Unit, maxValid, acc);
                            progress?.Report(Math.Min(1.0, (y + rowStep) / (double)h));
                        }
                    }
                    finally { tail?.Dispose(); }
                }
                if (step > 1)
                    acc.SamplingNote = "sampled every " + step + "×" + step +
                        " cells (1/" + (step * (long)step) + " of " +
                        totalCells.ToString("N0") + ")";
            }
            else
            {
                acc.SamplingNote = "from the raster attribute table";
                progress?.Report(1.0);
            }

            if (cache != null) cache[key] = acc;
            Finish(res, acc);
            return res;
        }

        private static void Finish(Result res, RasterCache a)
        {
            res.RawCount = a.Examined;
            res.ExcludedInvalid = a.Excluded;
            res.SamplingNote = a.SamplingNote;
            if (a.SumW > 0)
                res.Stats = SlopeStats.FromBinned(a.Hist, BinDeg, a.SumW, a.SumWX,
                    a.SumWX2, a.Min, a.Max, a.Unit);
        }

        private static void AccumulateValue(double v, double weight, SlopeUnit unit,
            double maxValid, RasterCache a)
        {
            if (double.IsNaN(v)) return;
            a.Examined += (long)weight;
            if (v < 0.0 || v > maxValid) { a.Excluded += (long)weight; return; }
            double deg = SlopeStats.ToDegrees(v, unit);
            int b = (int)(deg / BinDeg);
            if (b >= Bins) b = Bins - 1;
            if (b < 0) b = 0;
            a.Hist[b] += weight;
            a.SumW += weight;
            // Moment sums accumulate the ANGLE, never the raw percent/ratio:
            // mean and SD of slope are angle-space statistics (averaging raw
            // percent values biases them high). Min/max stay in source units.
            a.SumWX += weight * deg;
            a.SumWX2 += weight * deg * deg;
            if (v < a.Min) a.Min = v;
            if (v > a.Max) a.Max = v;
        }

        private static bool TryReadVat(Raster raster, SlopeUnit unit,
            double maxValid, RasterCache a)
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
                                if (!PolarPlotData.TryDouble(row[vIdx], out double v)) continue;
                                if (!PolarPlotData.TryDouble(row[cIdx], out double c) || c <= 0) continue;
                                AccumulateValue(v, c, unit, maxValid, a);
                            }
                        }
                    }
                }
                return true;
            }
            catch { return false; }
        }

        private static void AccumulateBlock(Array data, Array mask, int width,
            int rows, int colStep, bool hasNoData, double noData, SlopeUnit unit,
            double maxValid, RasterCache a)
        {
            if (data == null) return;
            int d0 = data.GetLength(0);
            bool xFirst = d0 == width;
            var mk = mask as byte[,];
            if (mk != null && (mk.GetLength(0) != data.GetLength(0) ||
                               mk.GetLength(1) != data.GetLength(1)))
                mk = null;

            void Acc(double v, bool noDataCell)
            {
                if (noDataCell) return;
                if (hasNoData && v == noData) return;
                AccumulateValue(v, 1.0, unit, maxValid, a);
            }

            switch (data)
            {
                case float[,] arr:
                    for (int r = 0; r < rows; r++)
                        for (int c = 0; c < width; c += colStep)
                            Acc(xFirst ? arr[c, r] : arr[r, c],
                                mk != null && (xFirst ? mk[c, r] : mk[r, c]) == 0);
                    break;
                case double[,] arr:
                    for (int r = 0; r < rows; r++)
                        for (int c = 0; c < width; c += colStep)
                            Acc(xFirst ? arr[c, r] : arr[r, c],
                                mk != null && (xFirst ? mk[c, r] : mk[r, c]) == 0);
                    break;
                case int[,] arr:
                    for (int r = 0; r < rows; r++)
                        for (int c = 0; c < width; c += colStep)
                            Acc(xFirst ? arr[c, r] : arr[r, c],
                                mk != null && (xFirst ? mk[c, r] : mk[r, c]) == 0);
                    break;
                case short[,] arr:
                    for (int r = 0; r < rows; r++)
                        for (int c = 0; c < width; c += colStep)
                            Acc(xFirst ? arr[c, r] : arr[r, c],
                                mk != null && (xFirst ? mk[c, r] : mk[r, c]) == 0);
                    break;
                case ushort[,] arr:
                    for (int r = 0; r < rows; r++)
                        for (int c = 0; c < width; c += colStep)
                            Acc(xFirst ? arr[c, r] : arr[r, c],
                                mk != null && (xFirst ? mk[c, r] : mk[r, c]) == 0);
                    break;
                case byte[,] arr:
                    for (int r = 0; r < rows; r++)
                        for (int c = 0; c < width; c += colStep)
                            Acc(xFirst ? arr[c, r] : arr[r, c],
                                mk != null && (xFirst ? mk[c, r] : mk[r, c]) == 0);
                    break;
                default:
                    for (int r = 0; r < rows; r++)
                        for (int c = 0; c < width; c += colStep)
                        {
                            object obj = xFirst ? data.GetValue(c, r) : data.GetValue(r, c);
                            if (obj != null && PolarPlotData.TryDouble(obj, out double v))
                                Acc(v, mk != null && (xFirst ? mk[c, r] : mk[r, c]) == 0);
                        }
                    break;
            }
        }
    }
}
