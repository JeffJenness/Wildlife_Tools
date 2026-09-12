using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;

namespace WildlifeTools.Corridor
{
    // =====================================================================
    //  Cross-Tabulation tool -- the pure data model: paired observations,
    //  per-axis class definitions, the 2-D binning, and the text report
    //  builders. No ArcGIS types anywhere, so everything is testable
    //  headless. Port of the CorridorDesigner Evaluation Tools' Cross-Tab
    //  Statistics (frmCrossTab + CrossTabFunctions), manual pp. 67-87.
    // =====================================================================

    /// <summary>The gathered observations: paired values plus a weight
    /// (geodesic size, cells, attribute value, or 1 per record).</summary>
    internal sealed class CrosstabData
    {
        public double[] V1 = Array.Empty<double>();   // horizontal variable
        public double[] V2 = Array.Empty<double>();   // vertical variable
        public double[] W = Array.Empty<double>();

        public string Name1 = "Variable 1";           // editable labels
        public string Name2 = "Variable 2";
        public WeightBasis Basis = WeightBasis.Records;
        public double CellAreaM2;                     // raster bases
        public bool ApproxCellArea;
        public string WeightDesc = "Count";           // provenance word
        public List<string> SourceLines = new List<string>();  // report

        // Categorical axes: code -> display label (a text field's distinct
        // values, a coded-value domain, or a raster attribute table). Null
        // = a plain numeric variable.
        public Dictionary<double, string> Labels1, Labels2;

        public long Observations => V1.Length;

        public (double min, double max) Range1() => RangeOf(V1);
        public (double min, double max) Range2() => RangeOf(V2);

        /// <summary>The variable's sorted distinct values when there are
        /// at most <paramref name="cap"/> of them (discrete data, e.g.
        /// patch-class codes 1..3); null when the variable has more (treat
        /// as continuous). NaN and zero-weight observations excluded.</summary>
        public double[] UniqueValues1(int cap) => Uniques(V1, cap);
        public double[] UniqueValues2(int cap) => Uniques(V2, cap);

        private double[] Uniques(double[] v, int cap)
        {
            var set = new SortedSet<double>();
            for (int i = 0; i < v.Length; i++)
            {
                if (W[i] <= 0 || double.IsNaN(v[i])) continue;
                set.Add(v[i]);
                if (set.Count > cap) return null;
            }
            return set.Count > 0 ? set.ToArray() : null;
        }

        private (double, double) RangeOf(double[] v)
        {
            double mn = double.PositiveInfinity, mx = double.NegativeInfinity;
            for (int i = 0; i < v.Length; i++)
            {
                if (W[i] <= 0) continue;
                if (v[i] < mn) mn = v[i];
                if (v[i] > mx) mx = v[i];
            }
            if (double.IsInfinity(mn)) { mn = 0; mx = 1; }
            if (mx <= mn) mx = mn + 1;
            return (mn, mx);
        }
    }

    /// <summary>One class (bin) on an axis: display name + [Min, Max)
    /// range (the last class of an axis is closed at its Max).</summary>
    internal sealed class CrosstabClass
    {
        public string Name = "";
        public double Min;
        public double Max;
    }

    /// <summary>One axis: variable name + ordered classes.</summary>
    internal sealed class CrosstabAxis
    {
        public string VariableName = "";
        public List<CrosstabClass> Classes = new List<CrosstabClass>();

        /// <summary>Equal-interval classes over [min, max] with range-based
        /// names -- the original's "Autofill Class Definitions".</summary>
        public static List<CrosstabClass> EqualInterval(int n, double min,
                                                        double max)
        {
            var list = new List<CrosstabClass>();
            n = Math.Max(n, 1);
            if (max <= min) max = min + 1;
            double w = (max - min) / n;
            for (int i = 0; i < n; i++)
            {
                // The final class's Max is the data max VERBATIM:
                // min + n*((max-min)/n) can round an ulp below max, and a
                // value exactly at the data maximum (always present -- the
                // range came from the data) then fell into a spurious
                // "Other" row.
                double lo = min + i * w;
                double hi = i == n - 1 ? max : min + (i + 1) * w;
                list.Add(new CrosstabClass
                {
                    Min = lo,
                    Max = hi,
                    Name = RangeName(lo, hi, w),
                });
            }
            return list;
        }

        public static string RangeName(double lo, double hi, double step)
        {
            return HistogramMath.AxisNumber(lo, step) + " - "
                 + HistogramMath.AxisNumber(hi, step);
        }

        /// <summary>One SINGLE-VALUE class per distinct value -- the
        /// default for discrete variables (patch classes, codes) so a
        /// GRIDCODE of 1, 2, 3 makes exactly three classes named
        /// "1", "2", "3".</summary>
        public static List<CrosstabClass> ForValues(
            IReadOnlyList<double> values,
            IReadOnlyDictionary<double, string> labels = null)
        {
            var list = new List<CrosstabClass>();
            foreach (var v in values)
                list.Add(new CrosstabClass
                {
                    Min = v,
                    Max = v,
                    Name = labels != null && labels.TryGetValue(v, out var s)
                        ? s
                        : HistogramMath.AxisNumber(v,
                            v == Math.Floor(v) ? 1 : 0.01),
                });
            return list;
        }

        /// <summary>Class index for a value: [Min, Max) per class, the
        /// LAST class closed; a class with Min == Max matches that exact
        /// value; -1 = no class contains it ("Other").</summary>
        public int IndexOf(double v)
        {
            for (int i = 0; i < Classes.Count; i++)
            {
                var c = Classes[i];
                if (c.Max == c.Min)
                {
                    if (v == c.Min) return i;
                    continue;
                }
                bool last = i == Classes.Count - 1;
                if (v >= c.Min && (v < c.Max || (last && v <= c.Max)))
                    return i;
            }
            return -1;
        }
    }

    /// <summary>The computed table: cell totals plus the Other row/column
    /// (observations outside every class) and the marginal sums.</summary>
    internal sealed class CrosstabResult
    {
        public double[,] Cells;        // [row(v2 class), col(v1 class)]
        public double[] OtherRow;      // per column: v2 out of range
        public double[] OtherCol;      // per row: v1 out of range
        public double OtherBoth;
        public double Total;           // all binned weight incl. Other
        public bool HasOther =>
            OtherBoth > 0 || OtherRow.Any(x => x > 0)
                          || OtherCol.Any(x => x > 0);

        public double RowSum(int r)
        {
            double s = 0;
            for (int c = 0; c < Cells.GetLength(1); c++) s += Cells[r, c];
            return s + OtherCol[r];
        }

        public double ColSum(int c)
        {
            double s = 0;
            for (int r = 0; r < Cells.GetLength(0); r++) s += Cells[r, c];
            return s + OtherRow[c];
        }
    }

    internal static class CrosstabMath
    {
        /// <summary>Bin every observation into the axis classes.</summary>
        public static CrosstabResult Bin(CrosstabData d, CrosstabAxis h,
                                         CrosstabAxis v)
        {
            int nc = Math.Max(h.Classes.Count, 1);
            int nr = Math.Max(v.Classes.Count, 1);
            var res = new CrosstabResult
            {
                Cells = new double[nr, nc],
                OtherRow = new double[nc],
                OtherCol = new double[nr],
            };
            for (int i = 0; i < d.V1.Length; i++)
            {
                double w = d.W[i];
                if (w <= 0) continue;
                int c = h.IndexOf(d.V1[i]);
                int r = v.IndexOf(d.V2[i]);
                res.Total += w;
                if (r >= 0 && c >= 0) res.Cells[r, c] += w;
                else if (r >= 0) res.OtherCol[r] += w;
                else if (c >= 0) res.OtherRow[c] += w;
                else res.OtherBoth += w;
            }
            return res;
        }

        /// <summary>Format one display value: percent (as a proportion,
        /// like the original's reports) or count/size, with the chosen
        /// decimals.</summary>
        public static string Fmt(double value, double total, bool percent,
                                 int decimals)
        {
            double v = percent ? (total > 0 ? value / total : 0) : value;
            return v.ToString("F" + Math.Min(Math.Max(decimals, 0), 10),
                              CultureInfo.InvariantCulture);
        }
    }

    // =====================================================================
    //  text outputs
    // =====================================================================
    internal static class CrosstabReport
    {
        /// <summary>The table as tab-separated text (the original's "Copy
        /// Table"), optionally flipped.</summary>
        public static string Tsv(CrosstabResult res, CrosstabAxis h,
                                 CrosstabAxis v, bool percent, int decimals,
                                 bool flip, double factor = 1.0)
        {
            var (grid, _colHead, _rowHead) = Grid(res, h, v, percent,
                                                  decimals, flip, factor);
            var sb = new StringBuilder();
            foreach (var row in grid)
                sb.AppendLine(string.Join("\t", row.Select(TsvCell)));
            return sb.ToString();
        }

        /// <summary>Tab/newline-proof a cell for TSV output: user-editable
        /// class names pasted from Excel can carry embedded tabs or
        /// newlines, which silently shift every later cell.</summary>
        private static string TsvCell(string s)
            => (s ?? "").Replace((char)9, ' ').Replace((char)13, ' ')
                        .Replace((char)10, ' ');

        /// <summary>The rendered table as a string grid: first row =
        /// headers ("Classes", column class names..., "Sum"), then one row
        /// per vertical class (+ Other), then the Sum row. ``flip`` swaps
        /// the axes.</summary>
        public static (List<string[]> grid, string colVar, string rowVar)
            Grid(CrosstabResult res, CrosstabAxis h, CrosstabAxis v,
                 bool percent, int decimals, bool flip, double factor = 1.0)
        {
            CrosstabAxis ch = flip ? v : h, cv = flip ? h : v;
            int nc = ch.Classes.Count, nr = cv.Classes.Count;
            double Cell(int r, int c) => flip ? CellAt(res, c, r)
                                              : CellAt(res, r, c);
            double OtherC(int r) => flip ? res.OtherRow[r] : res.OtherCol[r];
            double OtherR(int c) => flip ? res.OtherCol[c] : res.OtherRow[c];
            double RowS(int r) => flip ? res.ColSum(r) : res.RowSum(r);
            double ColS(int c) => flip ? res.RowSum(c) : res.ColSum(c);
            bool other = res.HasOther;

            string F(double x) => CrosstabMath.Fmt(
                percent ? x : x * factor, res.Total, percent, decimals);
            var grid = new List<string[]>();
            var head = new List<string> { "Classes" };
            head.AddRange(ch.Classes.Select(c => c.Name));
            if (other) head.Add("Other");
            head.Add("Sum");
            grid.Add(head.ToArray());
            for (int r = 0; r < nr; r++)
            {
                var row = new List<string> { cv.Classes[r].Name };
                for (int c = 0; c < nc; c++) row.Add(F(Cell(r, c)));
                if (other) row.Add(F(OtherC(r)));
                row.Add(F(RowS(r)));
                grid.Add(row.ToArray());
            }
            if (other)
            {
                var row = new List<string> { "Other" };
                double otherRowSum = res.OtherBoth;
                for (int c = 0; c < nc; c++)
                {
                    row.Add(F(OtherR(c)));
                    otherRowSum += OtherR(c);
                }
                row.Add(F(res.OtherBoth));
                row.Add(F(otherRowSum));
                grid.Add(row.ToArray());
            }
            var sum = new List<string> { "Sum" };
            for (int c = 0; c < nc; c++) sum.Add(F(ColS(c)));
            if (other)
            {
                double o = res.OtherBoth;
                for (int r = 0; r < nr; r++) o += OtherC(r);
                sum.Add(F(o));
            }
            sum.Add(F(res.Total));
            grid.Add(sum.ToArray());
            return (grid, ch.VariableName, cv.VariableName);
        }

        private static double CellAt(CrosstabResult res, int r, int c) =>
            res.Cells[r, c];

        /// <summary>The original's Full Report: sources, both tables
        /// (counts/sizes and percents), and the class definitions.</summary>
        public static string FullReport(CrosstabData d, CrosstabResult res,
                                        CrosstabAxis h, CrosstabAxis v,
                                        int decimals, bool flip,
                                        double factor = 1.0,
                                        string unitWord = null)
        {
            var sb = new StringBuilder();
            sb.AppendLine("Crosstabs Report:");
            sb.AppendLine("-------------------------------------");
            foreach (var line in d.SourceLines)
                sb.AppendLine("   *** " + line);
            sb.AppendLine("   *** Cross-tabulation cell units:  "
                          + (unitWord ?? d.WeightDesc));
            sb.AppendLine(string.Format(CultureInfo.InvariantCulture,
                "   *** {0:#,##0} total observations analyzed...",
                d.Observations));
            sb.AppendLine(new string('-', 56));
            sb.AppendLine("Crosstabs Table as Counts or Areas:");
            AppendGrid(sb, Grid(res, h, v, false, decimals, flip,
                                factor).grid);
            sb.AppendLine();
            sb.AppendLine("Crosstabs Table as Percents:");
            AppendGrid(sb, Grid(res, h, v, true, Math.Max(decimals, 3),
                                flip).grid);
            sb.AppendLine();
            sb.AppendLine(new string('-', 56));
            sb.AppendLine();
            sb.AppendLine("Variable Class Definitions:");
            sb.AppendLine();
            AppendAxis(sb, flip ? v : h,
                "Horizontal Axis; Classes distributed over columns:");
            sb.AppendLine();
            AppendAxis(sb, flip ? h : v,
                "Vertical Axis; Classes distributed over rows:");
            return sb.ToString();
        }

        private static void AppendGrid(StringBuilder sb, List<string[]> grid)
        {
            foreach (var row in grid)
                sb.AppendLine(string.Join("\t", row.Select(TsvCell)));
        }

        private static void AppendAxis(StringBuilder sb, CrosstabAxis ax,
                                       string title)
        {
            sb.AppendLine(title);
            sb.AppendLine("  Variable Name = " + ax.VariableName);
            sb.AppendLine("  Classes:");
            for (int i = 0; i < ax.Classes.Count; i++)
            {
                var c = ax.Classes[i];
                sb.AppendLine(string.Format(CultureInfo.InvariantCulture,
                    "     {0}) {1}:  Range = {2}.....{3}", i + 1, c.Name,
                    c.Min.ToString("0.###", CultureInfo.InvariantCulture),
                    c.Max.ToString("0.###", CultureInfo.InvariantCulture)));
            }
        }

        /// <summary>The two tables as TableSpecs for the gdb/dbf/CSV
        /// export (counts + percents in one table, one row per cell).</summary>
        public static HistogramStore.TableSpec ExportTable(CrosstabData d,
            CrosstabResult res, CrosstabAxis h, CrosstabAxis v)
        {
            var t = new HistogramStore.TableSpec();
            t.Schema.Add(("ROW_CLASS", "TEXT", 128,
                          "Row Class [" + v.VariableName + "]"));
            t.Schema.Add(("COL_CLASS", "TEXT", 128,
                          "Column Class [" + h.VariableName + "]"));
            t.Schema.Add(("ROW_MIN", "DOUBLE", 0, "Row Class Minimum"));
            t.Schema.Add(("ROW_MAX", "DOUBLE", 0, "Row Class Maximum"));
            t.Schema.Add(("COL_MIN", "DOUBLE", 0, "Column Class Minimum"));
            t.Schema.Add(("COL_MAX", "DOUBLE", 0, "Column Class Maximum"));
            t.Schema.Add(("VALUE", "DOUBLE", 0,
                          "Cell Value (" + d.WeightDesc + ")"));
            t.Schema.Add(("PERCENT", "DOUBLE", 0, "Percent of Total"));
            void Row(string rn, string cn, double rmin, double rmax,
                     double cmin, double cmax, double val)
            {
                t.Rows.Add(new object[]
                {
                    rn, cn, rmin, rmax, cmin, cmax, val,
                    res.Total > 0 ? val / res.Total * 100.0 : 0.0,
                });
            }
            for (int r = 0; r < v.Classes.Count; r++)
                for (int c = 0; c < h.Classes.Count; c++)
                    Row(v.Classes[r].Name, h.Classes[c].Name,
                        v.Classes[r].Min, v.Classes[r].Max,
                        h.Classes[c].Min, h.Classes[c].Max,
                        res.Cells[r, c]);
            if (res.HasOther)
            {
                for (int c = 0; c < h.Classes.Count; c++)
                    if (res.OtherRow[c] > 0)
                        Row("Other", h.Classes[c].Name, double.NaN,
                            double.NaN, h.Classes[c].Min, h.Classes[c].Max,
                            res.OtherRow[c]);
                for (int r = 0; r < v.Classes.Count; r++)
                    if (res.OtherCol[r] > 0)
                        Row(v.Classes[r].Name, "Other", v.Classes[r].Min,
                            v.Classes[r].Max, double.NaN, double.NaN,
                            res.OtherCol[r]);
                if (res.OtherBoth > 0)
                    Row("Other", "Other", double.NaN, double.NaN,
                        double.NaN, double.NaN, res.OtherBoth);
            }
            return t;
        }
    }
}
