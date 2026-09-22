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
using ArcGIS.Core.Data;
using ArcGIS.Core.Data.Raster;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Mapping;
using WildlifeTools.PolarPlots;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Data ingestion for the histogram tool: turns a raster band, feature
    /// layer or table field into a (value, weight) HistDataset. MCT only.
    ///
    /// Rasters: the attribute table (VAT) is the exact fast path; without
    /// one (or when clipping) the pixels are streamed in strips through
    /// RasterStack.ReadStrip and accumulated into 65,536 micro-bins that
    /// keep the WEIGHTED MEAN value per bin -- exact for integer-valued
    /// rasters (each integer owns its bin, its mean is itself) and accurate
    /// to range/65,536 for continuous ones. No sub-sampling needed: unlike
    /// the original's 250,000-cell sample, the full raster is read (an
    /// optional stride remains for truly enormous rasters).
    ///
    /// The clip-to-polygon option rasterizes the polygon to a cell mask on
    /// the raster's own grid (RasterStack.BuildClipMask -- true-curve
    /// densify and projection handled there); for features, each shape is
    /// intersected with the clip polygon so only the INSIDE portion's
    /// geodesic size contributes.
    ///
    /// Weights: projected rasters count cells (cell area known, so area
    /// units convert exactly); geographic rasters read by pixel weight each
    /// cell by its per-row spherical area, so area units are correct at any
    /// latitude; polygons/polylines use geodesic area/length; tables and
    /// unweighted features count records.
    /// </summary>
    internal static class HistogramData
    {
        private const int StripRows = 512;
        private const int MicroBins = 65536;
        private const double AuthalicR = 6371007.181;   // meters

        /// <summary>Raster class choice meaning "use the layer's unique-value
        /// symbology labels" (the other choices are VAT text columns; null
        /// = the numeric cell values).</summary>
        public const string SymbologyLabels = "(symbology class labels)";

        /// <summary>A field offered in the Add Data dialog: numeric fields
        /// build ordinary histograms, text fields categorical ones.</summary>
        public sealed class FieldChoice
        {
            public string Name;
            public bool IsText;
            public override string ToString() =>
                IsText ? Name + "   [text: categorical]" : Name;
        }

        public sealed class AddRequest
        {
            public PolarPlotData.Source Source;
            public string Field;              // features / tables
            public string ClassField;         // rasters: null = cell values;
                                              // SymbologyLabels; or a VAT
                                              // text column (categorical)
            public bool SelectedOnly;
            public bool WeightBySize = true;
            public Polygon ClipPolygon;       // any SR; null = no clip
            public string ClipName;
            public int SampleStride = 1;      // rasters: every k-th row/col
        }

        /// <summary>Read the dataset (MCT). Throws with a user-readable
        /// message on anything unusable.</summary>
        public static HistDataset Read(AddRequest req, Action<double> progress)
        {
            var d = req.Source.IsRaster
                ? ReadRaster(req, progress)
                : ReadTable(req, progress);
            if (d.Values.Length == 0)
                throw new InvalidOperationException(
                    "No usable values were found" +
                    (req.ClipPolygon != null ? " inside the clip polygon." : "."));
            if (req.Source.IsRaster && !string.IsNullOrEmpty(req.ClassField))
                CategorizeRaster(d, req);
            d.ComputeStats();
            if (!string.IsNullOrEmpty(req.ClipName))
                d.Name += " [" + req.ClipName + "]";
            return d;
        }

        // =================================================================
        // rasters
        // =================================================================
        private static HistDataset ReadRaster(AddRequest req,
                                              Action<double> progress)
        {
            var raster = req.Source.RasterLayer.GetRaster();
            if (raster == null)
                throw new InvalidOperationException("The raster could not be opened.");
            var sr = raster.GetSpatialReference();
            var cell = raster.GetMeanCellSize();
            bool geographic = sr != null && sr.IsGeographic;

            var d = new HistDataset
            {
                Name = req.Source.Name,
                SourceDesc = "Raster layer '" + req.Source.Name + "'",
            };

            // VAT fast path -- exact, instant; unusable when clipping (the
            // VAT has no cell positions).
            if (req.ClipPolygon == null && req.SampleStride <= 1)
            {
                var vat = TryReadVat(raster);
                if (vat != null)
                {
                    d.Values = vat.Value.values;
                    d.Weights = vat.Value.counts;
                    d.Basis = WeightBasis.Cells;
                    d.CellAreaM2 = CellAreaProjected(sr, cell);
                    if (d.CellAreaM2 <= 0 && geographic)
                    {
                        // center-latitude spherical approximation
                        var ext = raster.GetExtent();
                        double midLat = (ext.YMin + ext.YMax) / 2;
                        d.CellAreaM2 = SphericalCellArea(cell, midLat);
                        d.ApproxCellArea = true;
                    }
                    d.SourceDesc += " (attribute table)";
                    return d;
                }
            }

            // pixel path
            int W = raster.GetWidth(), H = raster.GetHeight();
            var ext2 = raster.GetExtent();
            object noData = raster.GetNoDataValue();

            bool[] clipMask = null;
            if (req.ClipPolygon != null)
                clipMask = RasterStack.BuildClipMask(req.ClipPolygon, sr,
                    ext2.XMin, cell.Item1, ext2.YMax, cell.Item2, W, H);

            int stride = Math.Max(req.SampleStride, 1);
            double projArea = CellAreaProjected(sr, cell);

            // pass 1: min / max
            double vmin = double.PositiveInfinity, vmax = double.NegativeInfinity;
            ForEachCell(raster, W, H, noData, clipMask, stride, null,
                (v, w) =>
                {
                    if (v < vmin) vmin = v;
                    if (v > vmax) vmax = v;
                }, p => progress?.Invoke(p * 0.45));
            if (double.IsInfinity(vmin))
                return d;                                     // nothing valid
            if (vmax <= vmin) vmax = vmin + 1e-9;

            // pass 2: micro-bins keeping the weighted mean value per bin
            var sumW = new double[MicroBins];
            var sumVW = new double[MicroBins];
            double range = vmax - vmin;
            Func<int, double> rowWeight = null;
            if (geographic)
                rowWeight = row => SphericalCellArea(cell,
                    ext2.YMax - (row + 0.5) * cell.Item2) * stride * stride;
            // flat weight: 1 cell (x stride^2 so a sampled read still
            // estimates the full-raster totals)
            double flatW = (double)stride * stride;
            ForEachCell(raster, W, H, noData, clipMask, stride, rowWeight,
                (v, w) =>
                {
                    int b = v >= vmax ? MicroBins - 1
                        : (int)((v - vmin) / range * MicroBins);
                    if (b < 0) b = 0;
                    if (b > MicroBins - 1) b = MicroBins - 1;
                    double wt = w > 0 ? w : flatW;
                    sumW[b] += wt;
                    sumVW[b] += wt * v;
                }, p => progress?.Invoke(0.45 + p * 0.55));

            int nb = sumW.Count(x => x > 0);
            var vals = new double[nb];
            var wts = new double[nb];
            int k = 0;
            for (int b = 0; b < MicroBins; b++)
                if (sumW[b] > 0)
                {
                    vals[k] = sumVW[b] / sumW[b];
                    wts[k] = sumW[b];
                    k++;
                }
            d.Values = vals;
            d.Weights = wts;
            if (geographic)
            {
                d.Basis = WeightBasis.AreaM2;   // weights already in m2
                d.SourceDesc += " (pixels; per-row spherical cell areas)";
            }
            else
            {
                d.Basis = WeightBasis.Cells;
                d.CellAreaM2 = projArea;
                d.SourceDesc += " (pixels)";
            }
            if (stride > 1)
                d.SourceDesc += string.Format(
                    " sampled 1/{0} of cells", (long)stride * stride);
            return d;
        }

        /// <summary>Turn a numeric raster dataset into a categorical one:
        /// every cell value becomes the class named by the layer's
        /// symbology labels or by one VAT text column (several values
        /// sharing a name merge into one class, exactly as Cross-Tab groups
        /// them). Symbology classes keep the raster's value order; VAT
        /// column classes are alphabetical. Values with no name fall into
        /// a trailing "(unlabeled)" class rather than vanishing.</summary>
        private static void CategorizeRaster(HistDataset d, AddRequest req)
        {
            var raster = req.Source.RasterLayer.GetRaster();
            bool symbology = req.ClassField == SymbologyLabels;
            var map = symbology
                ? CrosstabDataReader.ColorizerLabels(req.Source.RasterLayer, raster)
                : CrosstabDataReader.VatColumn(raster, req.ClassField);
            if (map == null || map.Count == 0)
                throw new InvalidOperationException(symbology
                    ? "The layer has no unique-value symbology with class "
                      + "labels. Symbolize it by unique values first, or "
                      + "choose an attribute-table column."
                    : "The attribute-table column '" + req.ClassField
                      + "' has no text to group by.");
            // class order: value order for symbology, alphabetical for a
            // VAT column (the Cross-Tab convention)
            var names = symbology
                ? map.OrderBy(kv => kv.Key).Select(kv => kv.Value).Distinct().ToList()
                : map.Values.Distinct()
                     .OrderBy(t => t, StringComparer.CurrentCultureIgnoreCase)
                     .ToList();
            var code = new Dictionary<string, int>(StringComparer.Ordinal);
            for (int i = 0; i < names.Count; i++) code[names[i]] = i + 1;
            // integer-keyed maps tolerate the pixel path's micro-bin means
            bool intKeys = map.Keys.All(k => Math.Abs(k - Math.Round(k)) < 1e-9);
            var totals = new double[names.Count + 2];   // +1 unlabeled slot
            double unlabeled = 0;
            for (int i = 0; i < d.Values.Length; i++)
            {
                double v = d.Values[i];
                if (!map.TryGetValue(v, out string name) &&
                    !(intKeys && map.TryGetValue(Math.Round(v), out name)))
                { unlabeled += d.Weights[i]; continue; }
                totals[code[name]] += d.Weights[i];
            }
            var labels = new Dictionary<double, string>();
            var vals = new List<double>();
            var wts = new List<double>();
            for (int i = 0; i < names.Count; i++)
            {
                labels[i + 1] = names[i];
                if (totals[i + 1] > 0) { vals.Add(i + 1); wts.Add(totals[i + 1]); }
            }
            if (unlabeled > 0)
            {
                labels[names.Count + 1] = "(unlabeled)";
                vals.Add(names.Count + 1);
                wts.Add(unlabeled);
            }
            d.Values = vals.ToArray();
            d.Weights = wts.ToArray();
            d.Labels = labels;
            d.SourceDesc += symbology
                ? "; classes from the layer's symbology labels"
                : "; classes from attribute-table column [" + req.ClassField + "]";
        }

        /// <summary>The class groupings a raster layer offers (MCT): its
        /// unique-value symbology labels when it has any, then every text
        /// column of its attribute table.</summary>
        public static List<string> RasterClassChoices(PolarPlotData.Source src)
        {
            var list = new List<string>();
            try
            {
                var raster = src.RasterLayer?.GetRaster();
                if (raster == null) return list;
                var col = CrosstabDataReader.ColorizerLabels(src.RasterLayer, raster);
                if (col != null && col.Count > 0) list.Add(SymbologyLabels);
                list.AddRange(CrosstabDataReader.RasterLabelFields(src));
            }
            catch { }
            return list;
        }

        /// <summary>Stream valid cells: calls sink(value, weight) where
        /// weight is 0 (caller supplies flat weight) unless rowWeight is
        /// given (per-row area in m2).</summary>
        private static void ForEachCell(Raster raster, int W, int H,
            object noData, bool[] clipMask, int stride,
            Func<int, double> rowWeight, Action<double, double> sink,
            Action<double> progress)
        {
            for (int y0 = 0; y0 < H; y0 += StripRows)
            {
                int h = Math.Min(StripRows, H - y0);
                RasterStack.ReadStrip(raster, W, h, y0, out var vals,
                                      out var valid, noData, useSentinel: false);
                for (int r = 0; r < h; r++)
                {
                    int row = y0 + r;
                    if (row % stride != 0) continue;
                    double rw = rowWeight?.Invoke(row) ?? 0;
                    int baseIdx = row * W;
                    for (int c = 0; c < W; c += stride)
                    {
                        if (!valid[r, c]) continue;
                        if (clipMask != null && !clipMask[baseIdx + c]) continue;
                        sink(vals[r, c], rw);
                    }
                }
                progress?.Invoke((double)Math.Min(y0 + h, H) / H);
            }
        }

        private static (double[] values, double[] counts)? TryReadVat(Raster raster)
        {
            Table vat = null;
            try
            {
                vat = raster.GetAttributeTable();
                if (vat == null) return null;
                var def = vat.GetDefinition();
                int vi = def.FindField("Value");
                int ci = def.GetFields().ToList().FindIndex(f =>
                    string.Equals(f.Name, "Count",
                                  StringComparison.OrdinalIgnoreCase));
                if (vi < 0 || ci < 0) return null;
                var v = new List<double>();
                var c = new List<double>();
                using (var cur = vat.Search(null, true))
                    while (cur.MoveNext())
                        using (var row = cur.Current)
                        {
                            object a = row[vi], b = row[ci];
                            if (a == null || a is DBNull || b == null ||
                                b is DBNull) continue;
                            v.Add(Convert.ToDouble(a, CultureInfo.InvariantCulture));
                            c.Add(Convert.ToDouble(b, CultureInfo.InvariantCulture));
                        }
                return v.Count > 0 ? (v.ToArray(), c.ToArray()) : null;
            }
            catch { return null; }
        }

        private static double CellAreaProjected(SpatialReference sr,
                                                Tuple<double, double> cell)
        {
            if (sr == null || sr.IsGeographic) return 0;
            double f = (sr.Unit as LinearUnit)?.ConversionFactor ?? 1.0;
            return Math.Abs(cell.Item1 * cell.Item2) * f * f;
        }

        private static double SphericalCellArea(Tuple<double, double> cellDeg,
                                                double latDeg)
        {
            double dx = Math.Abs(cellDeg.Item1) * Math.PI / 180.0;
            double dy = Math.Abs(cellDeg.Item2) * Math.PI / 180.0;
            return AuthalicR * AuthalicR * dx * dy *
                   Math.Cos(latDeg * Math.PI / 180.0);
        }

        // =================================================================
        // features and tables
        // =================================================================
        private static HistDataset ReadTable(AddRequest req,
                                             Action<double> progress)
        {
            var src = req.Source;
            var d = new HistDataset();
            Table table = PolarPlotData.GetTable(src);
            if (table == null)
                throw new InvalidOperationException(
                    "The dataset's table could not be opened.");
            try
            {
                var def = table.GetDefinition();
                int fi = def.FindField(req.Field ?? "");
                if (fi < 0)
                    throw new InvalidOperationException(
                        "Field not found: " + req.Field);

                bool textField = def.GetFields()[fi].FieldType == FieldType.String;
                // text field: each distinct string is a class; codes are
                // assigned alphabetically once every row has been read
                var textCode = new Dictionary<string, int>(StringComparer.Ordinal);
                var textNames = new List<string>();

                var fc = table as FeatureClass;
                GeometryType shp = GeometryType.Unknown;
                if (fc != null) shp = fc.GetDefinition().GetShapeType();
                bool weight = req.WeightBySize && fc != null &&
                    (shp == GeometryType.Polygon || shp == GeometryType.Polyline);

                // clip polygon in the data's spatial reference
                Polygon clip = null;
                if (req.ClipPolygon != null && fc != null)
                {
                    clip = req.ClipPolygon;
                    var dsr = fc.GetDefinition().GetSpatialReference();
                    try
                    {
                        if (dsr != null && clip.SpatialReference != null)
                            clip = (Polygon)GeometryEngine.Instance
                                .Project(clip, dsr);
                    }
                    catch { }
                }

                var qf = new QueryFilter();
                if (req.SelectedOnly && !src.IsRaster)
                {
                    var ids = PolarPlotData.GetSelectedIds(src);
                    if (ids != null && ids.Count > 0)
                        qf.ObjectIDs = ids.ToList();
                }

                var vals = new List<double>();
                var wts = new List<double>();
                long done = 0;
                using (var cur = table.Search(qf, true))
                    while (cur.MoveNext())
                        using (var row = cur.Current)
                        {
                            done++;
                            if ((done & 511) == 0) progress?.Invoke(-1);
                            object o = row[fi];
                            if (o == null || o is DBNull) continue;
                            double v;
                            if (textField)
                            {
                                string s = Convert.ToString(o,
                                    CultureInfo.InvariantCulture).Trim();
                                if (s.Length == 0) continue;
                                if (!textCode.TryGetValue(s, out int c))
                                {
                                    c = textNames.Count + 1;
                                    textCode[s] = c;
                                    textNames.Add(s);
                                }
                                v = c;
                            }
                            else
                                v = Convert.ToDouble(o,
                                    CultureInfo.InvariantCulture);
                            double w = 1.0;
                            if (fc != null)
                            {
                                Geometry g = null;
                                try { g = (row as Feature)?.GetShape(); }
                                catch { }
                                if (g == null || g.IsEmpty) continue;
                                if (clip != null)
                                {
                                    try
                                    {
                                        if (GeometryEngine.Instance
                                            .Disjoint(g, clip)) continue;
                                        if (shp == GeometryType.Polygon ||
                                            shp == GeometryType.Polyline)
                                            g = GeometryEngine.Instance
                                                .Intersection(g, clip,
                                                    shp == GeometryType.Polygon
                                                    ? GeometryDimensionType.EsriGeometry2Dimension
                                                    : GeometryDimensionType.EsriGeometry1Dimension);
                                        if (g == null || g.IsEmpty) continue;
                                    }
                                    catch { continue; }
                                }
                                if (weight)
                                {
                                    w = GeodesicSize(g, shp);
                                    if (w <= 0) continue;
                                }
                            }
                            vals.Add(v);
                            wts.Add(w);
                        }

                d.Values = vals.ToArray();
                d.Weights = wts.ToArray();
                if (textField && textNames.Count > 0)
                {
                    // renumber alphabetically and compress to one (code,
                    // total) pair per class
                    var sorted = textNames
                        .OrderBy(t => t, StringComparer.CurrentCultureIgnoreCase)
                        .ToList();
                    var newCode = new Dictionary<int, int>();
                    for (int i = 0; i < sorted.Count; i++)
                        newCode[textCode[sorted[i]]] = i + 1;
                    var totals = new double[sorted.Count + 1];
                    for (int i = 0; i < d.Values.Length; i++)
                        totals[newCode[(int)d.Values[i]]] += d.Weights[i];
                    var cv = new List<double>();
                    var cw = new List<double>();
                    d.Labels = new Dictionary<double, string>();
                    for (int i = 0; i < sorted.Count; i++)
                    {
                        d.Labels[i + 1] = sorted[i];
                        if (totals[i + 1] > 0) { cv.Add(i + 1); cw.Add(totals[i + 1]); }
                    }
                    d.Values = cv.ToArray();
                    d.Weights = cw.ToArray();
                }
                d.Basis = !weight ? WeightBasis.Records
                    : shp == GeometryType.Polygon ? WeightBasis.AreaM2
                    : WeightBasis.LengthM;
                d.Name = src.Name + " : " + req.Field;
                d.SourceDesc = (fc != null ? "Feature layer '" : "Table '")
                    + src.Name + "', field [" + req.Field + "]"
                    + (req.SelectedOnly ? ", selected records" : "")
                    + (weight
                        ? shp == GeometryType.Polygon
                            ? ", weighted by geodesic area"
                            : ", weighted by geodesic length"
                        : "");
                return d;
            }
            finally
            {
                if (!src.IsRaster) table.Dispose();
            }
        }

        private static double GeodesicSize(Geometry g, GeometryType shp)
        {
            try
            {
                return shp == GeometryType.Polygon
                    ? Math.Abs(GeometryEngine.Instance.GeodesicArea(g))
                    : Math.Abs(GeometryEngine.Instance.GeodesicLength(g));
            }
            catch
            {
                try
                {
                    return shp == GeometryType.Polygon
                        ? Math.Abs((g as Polygon)?.Area ?? 0)
                        : Math.Abs((g as Polyline)?.Length ?? 0);
                }
                catch { return 0; }
            }
        }

        // =================================================================
        // clip polygon assembly (from a polygon layer, honoring selection)
        // =================================================================
        /// <summary>Union the (selected) polygons of a layer into one clip
        /// polygon in the layer's own SR. MCT.</summary>
        public static Polygon BuildClipPolygon(FeatureLayer lyr,
                                               out string clipName)
        {
            clipName = lyr.Name;
            var geoms = new List<Geometry>();
            using (var sel = lyr.GetSelection())
            {
                var ids = sel.GetObjectIDs();
                var qf = new QueryFilter();
                if (ids != null && ids.Count > 0)
                {
                    qf.ObjectIDs = ids.ToList();
                    clipName += " (selection)";
                }
                using (var cur = lyr.Search(qf))
                    while (cur.MoveNext())
                        using (var f = (Feature)cur.Current)
                        {
                            var g = f.GetShape();
                            if (g != null && !g.IsEmpty) geoms.Add(g);
                        }
            }
            if (geoms.Count == 0)
                throw new InvalidOperationException(
                    "The clip layer has no polygons to use.");
            var u = geoms.Count == 1 ? geoms[0]
                : GeometryEngine.Instance.Union(geoms);
            return u as Polygon
                ?? throw new InvalidOperationException(
                    "The clip layer did not produce a polygon.");
        }

        /// <summary>Numeric fields only (weight fields, Cross-Tab). MCT.</summary>
        public static List<string> NumericFields(PolarPlotData.Source src) =>
            ValueFields(src).Where(f => !f.IsText).Select(f => f.Name).ToList();

        /// <summary>Value fields of a feature layer / table source: numeric
        /// fields first, then text fields (categorical). MCT.</summary>
        public static List<FieldChoice> ValueFields(PolarPlotData.Source src)
        {
            var names = new List<FieldChoice>();
            if (src.IsRaster) return names;
            Table t = null;
            try
            {
                t = PolarPlotData.GetTable(src);
                if (t == null) return names;
                var texts = new List<FieldChoice>();
                foreach (var f in t.GetDefinition().GetFields())
                    switch (f.FieldType)
                    {
                        case FieldType.SmallInteger:
                        case FieldType.Integer:
                        case FieldType.BigInteger:
                        case FieldType.Single:
                        case FieldType.Double:
                            names.Add(new FieldChoice { Name = f.Name });
                            break;
                        case FieldType.String:
                            texts.Add(new FieldChoice { Name = f.Name, IsText = true });
                            break;
                    }
                names.AddRange(texts);
            }
            catch { }
            finally { t?.Dispose(); }
            return names;
        }
    }
}
