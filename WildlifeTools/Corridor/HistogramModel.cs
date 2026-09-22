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
using WildlifeTools.PolarPlots;

namespace WildlifeTools.Corridor
{
    // =====================================================================
    //  Histograms and Habitat Suitability Statistics -- the pure data
    //  model, statistics, binning, KDE and chart-building code. No ArcGIS
    //  types anywhere in this file, so every piece is testable headless.
    //  Port of the CorridorDesigner Evaluation Tools' histogram tool
    //  (frmHistogram + frmHistParams), modernized: automatic unit
    //  conversion instead of manual Y multipliers, optional weighted
    //  kernel-density curves, colorblind-safe default palette.
    //  The chart is emitted as a PolarPlotModel -- the suite's shared
    //  resolution-independent primitive list -- so the live WPF preview,
    //  the exported SVG and the layout graphic are guaranteed identical.
    // =====================================================================

    /// <summary>What one unit of a dataset's weight means.</summary>
    internal enum WeightBasis
    {
        Records,    // one record = 1 (tables, points, unweighted features)
        Cells,      // raster cells (CellAreaM2 > 0 makes them convertible)
        AreaM2,     // polygon geodesic square meters
        LengthM,    // polyline geodesic meters
    }

    /// <summary>Y-axis display unit.</summary>
    internal enum YUnit
    {
        Native,     // the raw weight (cells / records / m2 / m)
        SqMeters, Hectares, Acres, SqKilometers, SqMiles,
        Meters, Kilometers, Miles, Feet,
        Custom,     // Native x a user multiplier
    }

    /// <summary>One dataset on the histogram: values compressed to
    /// (value, weight) pairs plus display properties.</summary>
    internal sealed class HistDataset
    {
        public string Name = "";
        public string SourceDesc = "";        // provenance line for reports
        public double[] Values = Array.Empty<double>();
        public double[] Weights = Array.Empty<double>();

        /// <summary>Categorical datasets only: class code -> class name
        /// (Values then hold the codes, one per class). Null = continuous.
        /// Classes are matched BETWEEN datasets by NAME, never by code, so
        /// two rasters coded differently still line up on the axis.</summary>
        public Dictionary<double, string> Labels;
        public bool IsCategorical => Labels != null;

        /// <summary>Class names in this dataset's own order (code order:
        /// raster value order for symbology labels, alphabetical for
        /// VAT-column and text-field groupings).</summary>
        public List<string> CategoryNames() =>
            Labels == null ? new List<string>()
            : Labels.OrderBy(kv => kv.Key).Select(kv => kv.Value).ToList();

        /// <summary>Total weight per class name.</summary>
        public Dictionary<string, double> CategoryTotals()
        {
            var t = new Dictionary<string, double>(StringComparer.Ordinal);
            if (Labels == null) return t;
            for (int i = 0; i < Values.Length; i++)
            {
                if (!Labels.TryGetValue(Values[i], out string name)) continue;
                t.TryGetValue(name, out double cur);
                t[name] = cur + Weights[i];
            }
            return t;
        }

        public WeightBasis Basis = WeightBasis.Records;
        public double CellAreaM2;             // rasters; 0 = unknown
        public bool ApproxCellArea;           // geographic VAT rasters

        // display
        public RgbaColor Fill;
        public RgbaColor Outline;
        public double OutlineWidthPt = 1.0;
        public bool ShowBars = true;
        public bool ShowKde;

        // cached statistics (weighted; equal weights = ordinary)
        public HistStats Stats;

        public void ComputeStats() => Stats = HistogramMath.WeightedStats(Values, Weights);

        /// <summary>Total weight expressed in the display unit (NaN when the
        /// unit does not apply to this dataset's basis).</summary>
        public double TotalInUnit(YUnit unit, double customMult) =>
            HistogramMath.ToUnit(Stats.SumWeights, this, unit, customMult);
    }

    internal struct HistStats
    {
        public double SumWeights;             // total cells / m2 / m / records
        public double Sum;                    // sum of value x weight
        public double Mean, Min, Max, Range, SD, Var, Median, SE;
        public double Count;                  // = SumWeights (original's "count")
        public double NEff;                   // effective n for weighted data
    }

    /// <summary>Everything about how the chart looks. Mutated live by the
    /// window; the builder reads it.</summary>
    internal sealed class HistogramOptions
    {
        public string Title = "Habitat Suitability Comparison";
        public bool ShowTitle = true;
        public string XLabel = "Habitat Suitability";
        public bool ShowXLabel = true;
        public string YLabel = "";            // "" = automatic from unit
        public bool ShowYLabel = true;
        public bool ShowLegend = true;
        public bool ShowRefLines = true;
        public RgbaColor RefLineColor = new RgbaColor(225, 225, 225, 255);

        public int Bins = 10;
        public double AxisMin, AxisMax;       // bin range (may exceed data)
        public bool AutoRange = true;         // track the data min/max

        public double GapFraction = 0.14;     // share of the bin slot left empty
        public bool YPercent;                 // percent of each dataset
        public YUnit Unit = YUnit.Native;
        public double CustomMultiplier = 1.0;

        public bool CurvesOnly;               // hide bars, show only KDEs
        public double KdeBandwidthMult = 1.0; // x Silverman rule of thumb

        public double WidthIn = 6.5;          // chart size, inches
        public double HeightIn = 4.4;

        // fonts (user-editable via the Fonts... dialog): four independent
        // styles per Jeff -- title, axis labels, legend text, axis numbers
        public FontSpec TitleFont = new FontSpec
        { SizePt = 13, Bold = true };
        public FontSpec LabelFont = new FontSpec { SizePt = 10 };
        public FontSpec LegendFont = new FontSpec { SizePt = 9 };
        public FontSpec TickFont = new FontSpec { SizePt = 8.5 };
    }

    /// <summary>One text style: family, size, bold, italic.</summary>
    internal sealed class FontSpec
    {
        public string Family = "Segoe UI";
        public double SizePt = 10;
        public bool Bold;
        public bool Italic;
    }

    // =====================================================================
    //  math
    // =====================================================================
    internal static class HistogramMath
    {
        /// <summary>The Okabe-Ito colorblind-safe palette, cycled for new
        /// datasets (replaces the original's random pastels).</summary>
        public static readonly (byte r, byte g, byte b)[] Palette =
        {
            (0, 114, 178), (230, 159, 0), (0, 158, 115), (204, 121, 167),
            (86, 180, 233), (213, 94, 0), (240, 228, 66), (100, 100, 100),
        };

        public static RgbaColor PaletteFill(int i)
        {
            var (r, g, b) = Palette[i % Palette.Length];
            return new RgbaColor(r, g, b, 210);
        }

        public static RgbaColor PaletteOutline(int i)
        {
            var (r, g, b) = Palette[i % Palette.Length];
            return new RgbaColor((byte)(r * 0.6), (byte)(g * 0.6),
                                 (byte)(b * 0.6), 255);
        }

        /// <summary>Weighted statistics of (value, weight) pairs. Histogram
        /// weights are FREQUENCIES -- a VAT class with 10,000 cells really
        /// is 10,000 observations, and the (value, weight) pairs are a
        /// compressed sample -- so the variance uses the frequency n-1 form
        /// (denominator W-1) and SE = SD / sqrt(W), exactly like the
        /// original tool and exactly what replicating each value weight
        /// times would give. (The Weighted Summary Statistics GP tool
        /// deliberately differs: there a weight is an importance factor on
        /// ONE record, the reliability-weights model.) NEff (W^2 / sum w^2)
        /// is kept for the KDE bandwidth rule.</summary>
        public static HistStats WeightedStats(double[] v, double[] w)
        {
            var s = new HistStats();
            if (v == null || v.Length == 0) return s;
            double W = 0, sumWx = 0, sumW2 = 0;
            double min = double.PositiveInfinity, max = double.NegativeInfinity;
            for (int i = 0; i < v.Length; i++)
            {
                double wi = w[i];
                if (wi <= 0) continue;
                W += wi;
                sumW2 += wi * wi;
                sumWx += wi * v[i];
                if (v[i] < min) min = v[i];
                if (v[i] > max) max = v[i];
            }
            if (W <= 0) return s;
            s.SumWeights = W;
            s.Count = W;
            s.Sum = sumWx;
            s.Mean = sumWx / W;
            s.Min = min;
            s.Max = max;
            s.Range = max - min;
            s.NEff = W * W / sumW2;
            double ss = 0;
            for (int i = 0; i < v.Length; i++)
                if (w[i] > 0) ss += w[i] * (v[i] - s.Mean) * (v[i] - s.Mean);
            double denom = W - 1;            // frequency-weights n-1
            if (denom > 0)
            {
                s.Var = ss / denom;
                s.SD = Math.Sqrt(s.Var);
                s.SE = s.SD / Math.Sqrt(W);
            }
            s.Median = WeightedMedian(v, w, W);
            return s;
        }

        /// <summary>Weighted median: the value where cumulative weight first
        /// reaches half the total (averaging across an exact boundary --
        /// reduces to the ordinary median under equal weights).</summary>
        public static double WeightedMedian(double[] v, double[] w, double W)
        {
            int n = v.Length;
            var order = Enumerable.Range(0, n)
                .Where(i => w[i] > 0)
                .OrderBy(i => v[i]).ToArray();
            double half = W / 2.0, cum = 0;
            for (int k = 0; k < order.Length; k++)
            {
                cum += w[order[k]];
                if (cum >= half - 1e-9 * W)
                {
                    if (Math.Abs(cum - half) <= 1e-9 * W && k + 1 < order.Length)
                        return (v[order[k]] + v[order[k + 1]]) / 2.0;
                    return v[order[k]];
                }
            }
            return order.Length > 0 ? v[order[order.Length - 1]] : 0;
        }

        // ---- units -------------------------------------------------------
        private const double Acre = 4046.8564224;
        private const double SqMile = 2589988.110336;
        private const double Mile = 1609.344;
        private const double Foot = 0.3048;

        /// <summary>Convert a raw weight total to the display unit for one
        /// dataset. NaN = this unit does not apply to this dataset.</summary>
        public static double ToUnit(double weight, HistDataset d, YUnit unit,
                                    double customMult)
        {
            switch (unit)
            {
                case YUnit.Native: return weight;
                case YUnit.Custom: return weight * customMult;
            }
            // area units: from polygon m2, or raster cells x cell area
            double m2 = d.Basis == WeightBasis.AreaM2 ? weight
                : d.Basis == WeightBasis.Cells && d.CellAreaM2 > 0
                    ? weight * d.CellAreaM2 : double.NaN;
            // length units: from polyline meters
            double m = d.Basis == WeightBasis.LengthM ? weight : double.NaN;
            switch (unit)
            {
                case YUnit.SqMeters: return m2;
                case YUnit.Hectares: return m2 / 1e4;
                case YUnit.Acres: return m2 / Acre;
                case YUnit.SqKilometers: return m2 / 1e6;
                case YUnit.SqMiles: return m2 / SqMile;
                case YUnit.Meters: return m;
                case YUnit.Kilometers: return m / 1000.0;
                case YUnit.Miles: return m / Mile;
                case YUnit.Feet: return m / Foot;
                default: return double.NaN;
            }
        }

        /// <summary>The units every dataset in the list can express, for the
        /// unit dropdown: always Native + Custom; area units when every
        /// dataset is area-capable; length units when every dataset is a
        /// polyline.</summary>
        public static List<YUnit> AvailableUnits(IReadOnlyList<HistDataset> ds)
        {
            var list = new List<YUnit> { YUnit.Native };
            if (ds.Count > 0 && ds.All(d =>
                    d.Basis == WeightBasis.AreaM2 ||
                    (d.Basis == WeightBasis.Cells && d.CellAreaM2 > 0)))
                list.AddRange(new[] { YUnit.SqMeters, YUnit.Hectares,
                    YUnit.Acres, YUnit.SqKilometers, YUnit.SqMiles });
            if (ds.Count > 0 && ds.All(d => d.Basis == WeightBasis.LengthM))
                list.AddRange(new[] { YUnit.Meters, YUnit.Kilometers,
                    YUnit.Miles, YUnit.Feet });
            list.Add(YUnit.Custom);
            return list;
        }

        public static string UnitLabel(YUnit u, IReadOnlyList<HistDataset> ds)
        {
            switch (u)
            {
                case YUnit.SqMeters: return "Square Meters";
                case YUnit.Hectares: return "Hectares";
                case YUnit.Acres: return "Acres";
                case YUnit.SqKilometers: return "Square Kilometers";
                case YUnit.SqMiles: return "Square Miles";
                case YUnit.Meters: return "Meters";
                case YUnit.Kilometers: return "Kilometers";
                case YUnit.Miles: return "Miles";
                case YUnit.Feet: return "Feet";
                case YUnit.Custom: return "Custom multiplier";
                default:
                    if (ds != null && ds.Count > 0 &&
                        ds.All(d2 => d2.Basis == WeightBasis.Cells))
                        return "Cells";
                    if (ds != null && ds.Count > 0 &&
                        ds.All(d2 => d2.Basis == WeightBasis.Records))
                        return "Count";
                    return "Native units";
            }
        }

        // ---- categories --------------------------------------------------
        /// <summary>The class axis shared by several categorical datasets:
        /// the FIRST dataset's classes in its own order, then any class the
        /// others add, in order of appearance. Names match exactly (case
        /// and spacing included), since a name IS the class.</summary>
        public static List<string> UnionCategories(IEnumerable<HistDataset> ds)
        {
            var seen = new HashSet<string>(StringComparer.Ordinal);
            var list = new List<string>();
            foreach (var d in ds)
            {
                if (!d.IsCategorical) continue;
                foreach (var n in d.CategoryNames())
                    if (seen.Add(n)) list.Add(n);
            }
            return list;
        }

        /// <summary>Per-class totals of one categorical dataset laid onto a
        /// shared class axis (0 where the dataset lacks the class).</summary>
        public static double[] CategoryTotals(HistDataset d,
                                              IReadOnlyList<string> cats)
        {
            var tot = new double[cats.Count];
            var own = d.CategoryTotals();
            for (int i = 0; i < cats.Count; i++)
                if (own.TryGetValue(cats[i], out double w)) tot[i] = w;
            return tot;
        }

        /// <summary>The classes each dataset LACKS relative to the shared
        /// axis -- empty when every dataset carries every class. This is
        /// what the window warns about: a land-cover raster clipped to one
        /// corridor may simply have no wetland, and the reader must know
        /// whether an empty slot means "none there" or "different
        /// classification".</summary>
        public static List<(HistDataset d, List<string> missing)>
            MissingCategories(IReadOnlyList<HistDataset> ds)
        {
            var cats = UnionCategories(ds);
            var res = new List<(HistDataset, List<string>)>();
            foreach (var d in ds)
            {
                if (!d.IsCategorical) continue;
                var own = new HashSet<string>(d.CategoryNames(),
                                              StringComparer.Ordinal);
                var miss = cats.Where(c => !own.Contains(c)).ToList();
                if (miss.Count > 0) res.Add((d, miss));
            }
            return res;
        }

        /// <summary>Name of the heaviest class and its share of the total
        /// (share NaN when the dataset is not categorical or is empty).</summary>
        public static (string name, double share) LargestCategory(HistDataset d)
        {
            var t = d.CategoryTotals();
            if (t.Count == 0) return (null, double.NaN);
            double sum = t.Values.Sum();
            var top = t.OrderByDescending(kv => kv.Value).First();
            return (top.Key, sum > 0 ? top.Value / sum * 100.0 : double.NaN);
        }

        // ---- binning -----------------------------------------------------
        /// <summary>Per-bin totals of one dataset over [min, max) with the
        /// last bin closed. Values outside the range are excluded and
        /// reported via <paramref name="excludedWeight"/>.</summary>
        public static double[] BinTotals(HistDataset d, int bins, double min,
                                         double max, out double excludedWeight)
        {
            var tot = new double[bins];
            excludedWeight = 0;
            double range = max - min;
            if (range <= 0 || bins < 1) return tot;
            for (int i = 0; i < d.Values.Length; i++)
            {
                double v = d.Values[i], w = d.Weights[i];
                if (w <= 0) continue;
                if (v < min || v > max) { excludedWeight += w; continue; }
                int b = v >= max ? bins - 1 : (int)((v - min) / range * bins);
                if (b < 0) b = 0;
                if (b > bins - 1) b = bins - 1;
                tot[b] += w;
            }
            return tot;
        }

        // ---- KDE ---------------------------------------------------------
        /// <summary>Weighted Gaussian KDE evaluated on a regular grid over
        /// [min, max], with boundary REFLECTION at both ends so mass never
        /// smears past the axis limits (habitat scores cannot fall below
        /// their floor). Returns densities integrating to ~1 over the range.
        /// Bandwidth: Silverman's rule of thumb on the weighted SD with the
        /// effective sample size, x <paramref name="bwMult"/>.</summary>
        public static double[] Kde(HistDataset d, double min, double max,
                                   int gridN, double bwMult, out double bw)
        {
            bw = 0;
            var f = new double[gridN];
            var st = d.Stats;
            if (st.SumWeights <= 0 || gridN < 2 || max <= min) return f;
            double sd = st.SD;
            if (sd <= 0 || double.IsNaN(sd))
                sd = (max - min) / 20.0;      // degenerate data: nominal width
            bw = 1.06 * sd * Math.Pow(Math.Max(st.NEff, 1.0), -0.2)
                 * Math.Max(bwMult, 1e-3);
            if (bw <= 0) return f;
            double h = bw;                 // local copy (out params cannot
                                           // be captured by local functions)
            double W = st.SumWeights;
            // live-slider speed: compress big continuous datasets to 2048
            // mean-preserving micro-bins first (integer/VAT data is already
            // tiny and stays exact)
            double[] vv = d.Values, ww = d.Weights;
            if (vv.Length > 4096)
                Compress(ref vv, ref ww, st.Min, st.Max, 2048);
            double step = (max - min) / (gridN - 1);
            double inv = 1.0 / (h * Math.Sqrt(2 * Math.PI));
            for (int g = 0; g < gridN; g++)
            {
                double x = min + g * step;
                double s = 0;
                for (int i = 0; i < vv.Length; i++)
                {
                    double w = ww[i];
                    if (w <= 0) continue;
                    double v = vv[i];
                    // Out-of-axis-range data is EXCLUDED, matching the
                    // bars (BinTotals drops it as excludedWeight). The
                    // boundary reflection otherwise teleported an
                    // out-of-range point's full mass INSIDE a zoomed
                    // axis, drawing a spurious bump the bars don't show.
                    if (v < min || v > max) continue;
                    s += w * (G(x - v) + G(x - (2 * min - v))
                              + G(x - (2 * max - v)));
                }
                f[g] = s * inv / W;
            }
            return f;

            double G(double u)
            {
                double t = u / h;
                return t * t > 40 ? 0 : Math.Exp(-0.5 * t * t);
            }
        }

        /// <summary>Re-bin (value, weight) pairs into mean-preserving
        /// micro-bins over [lo, hi]: each output value is the weighted mean
        /// of its bin, so totals and the weighted mean are preserved
        /// exactly.</summary>
        private static void Compress(ref double[] v, ref double[] w,
                                     double lo, double hi, int bins)
        {
            if (hi <= lo) return;
            var sumW = new double[bins];
            var sumVW = new double[bins];
            double range = hi - lo;
            for (int i = 0; i < v.Length; i++)
            {
                if (w[i] <= 0) continue;
                int b = v[i] >= hi ? bins - 1
                    : (int)((v[i] - lo) / range * bins);
                if (b < 0) b = 0;
                if (b > bins - 1) b = bins - 1;
                sumW[b] += w[i];
                sumVW[b] += w[i] * v[i];
            }
            int nb = 0;
            for (int b = 0; b < bins; b++) if (sumW[b] > 0) nb++;
            var nv = new double[nb];
            var nw = new double[nb];
            int k = 0;
            for (int b = 0; b < bins; b++)
                if (sumW[b] > 0)
                {
                    nv[k] = sumVW[b] / sumW[b];
                    nw[k] = sumW[b];
                    k++;
                }
            v = nv;
            w = nw;
        }

        // ---- axis ticks --------------------------------------------------
        /// <summary>A "nice" 1-2-5 step covering <paramref name="span"/> with
        /// roughly <paramref name="targetTicks"/> divisions.</summary>
        public static double NiceStep(double span, int targetTicks)
        {
            if (span <= 0 || targetTicks < 1) return 1;
            double raw = span / targetTicks;
            double mag = Math.Pow(10, Math.Floor(Math.Log10(raw)));
            double r = raw / mag;
            double nice = r < 1.5 ? 1 : r < 3.5 ? 2 : r < 7.5 ? 5 : 10;
            return nice * mag;
        }

        /// <summary>Format an axis number: thousands separators, decimals
        /// scaled to the tick step.</summary>
        public static string AxisNumber(double v, double step)
        {
            int dec = 0;
            if (step > 0 && step < 1)
                dec = Math.Min(6, (int)Math.Ceiling(-Math.Log10(step) + 0.001));
            string fmt = dec > 0 ? "#,##0." + new string('0', dec) : "#,##0";
            return v.ToString(fmt, CultureInfo.InvariantCulture);
        }
    }
}
