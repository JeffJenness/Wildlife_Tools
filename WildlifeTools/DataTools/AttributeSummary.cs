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
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using ArcGIS.Core.Data;
using ArcGIS.Core.Data.Raster;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Mapping;

namespace WildlifeTools.DataTools
{
    /// <summary>
    /// Engine for Attribute Summary Reports: counts and proportions of the
    /// classes in one or more attribute fields, ported from the SSI ArcMap
    /// "Summarize Attribute Fields" tool (SummarizeAttributeValues +
    /// FormatSummarizeItem). Each report line is rendered from a user format
    /// template with the original tool's tokens:
    ///   {val1}..{valN}  the class value(s), in analysis order
    ///   {Count}         records in the class
    ///   {PropN}         proportion of the total, N decimal places (percent)
    ///   {total_count}   total records analyzed
    ///   {increment}     1-based running number; also {increment_alpha_lc},
    ///                   {increment_alpha_uc}, {increment_roman_lc},
    ///                   {increment_roman_uc}
    ///   {tab} {vbcrlf}  literal tab / newline
    ///   {Length}        combined length of the inserted value strings
    ///   {commas}        flag: thousands separators in numbers (removed)
    /// Geometry tokens (2026-08-19, the Corridor Summarize port): the
    /// class's TOTAL geodesic geometry -- {GeomLength_m} / {GeomLength_km}
    /// (polylines), {GeomArea_m2} / {GeomArea_ha} / {GeomArea_km2}
    /// (polygons; VAT rasters via cells x cell area), {Cells} (VAT
    /// rasters), and {GeomPropN}, the class's share of the total length /
    /// area / cells -- the per-unit-LANDSCAPE proportion, where {PropN}
    /// is the per-record one. Inapplicable tokens render as empty text.
    /// All token matching is case-insensitive, like the original.
    /// </summary>
    internal static class AttributeSummary
    {
        public const string DefaultFormat =
            "  {increment}] {val1} (n = {Count}; {Prop2} of {total_count}){commas}";

        /// <summary>What a class's geometry weight means for this source.</summary>
        public enum WeightKind
        {
            None,       // table / points: no geometry to total
            LengthM,    // polylines: geodesic meters
            AreaM2,     // polygons: geodesic square meters
            Cells,      // VAT raster: cell counts (x CellAreaM2 for area)
        }

        // ---- format rendering ----------------------------------------------
        public static string Render(string format, IReadOnlyList<string> values,
                                    long increment, long count, long totalCount,
                                    double weight = 0, double totalWeight = 0,
                                    WeightKind kind = WeightKind.None,
                                    double cellAreaM2 = 0)
        {
            string fmt = format ?? DefaultFormat;
            bool commas = ContainsCI(fmt, "{commas}");
            if (commas) fmt = ReplaceCI(fmt, "{commas}", "");
            string num(long v) => v.ToString(commas ? "#,##0" : "0",
                                             CultureInfo.InvariantCulture);
            string dnum(double v) => v.ToString(commas ? "#,##0.###" : "0.###",
                                                CultureInfo.InvariantCulture);

            long length = 0;
            for (int i = 0; i < values.Count; i++)
            {
                string tok = "{val" + (i + 1) + "}";
                if (ContainsCI(fmt, tok)) length += (values[i] ?? "").Length;
                fmt = ReplaceCI(fmt, tok, values[i] ?? "");
            }
            fmt = ReplaceCI(fmt, "{vbcrlf}", Environment.NewLine);
            fmt = ReplaceCI(fmt, "{tab}", "\t");
            fmt = ReplaceCI(fmt, "{Length}", num(length));
            fmt = ReplaceCI(fmt, "{Count}", num(count));

            // ---- geometry tokens: total geodesic size of the class ----------
            double lenM = kind == WeightKind.LengthM ? weight : double.NaN;
            double areaM2 = kind == WeightKind.AreaM2 ? weight
                : (kind == WeightKind.Cells && cellAreaM2 > 0
                   ? weight * cellAreaM2 : double.NaN);
            string opt(double v) => double.IsNaN(v) ? "" : dnum(v);
            fmt = ReplaceCI(fmt, "{GeomLength_km}", opt(lenM / 1000.0));
            fmt = ReplaceCI(fmt, "{GeomLength_m}", opt(lenM));
            fmt = ReplaceCI(fmt, "{GeomArea_km2}", opt(areaM2 / 1e6));
            fmt = ReplaceCI(fmt, "{GeomArea_ha}", opt(areaM2 / 1e4));
            fmt = ReplaceCI(fmt, "{GeomArea_m2}", opt(areaM2));
            fmt = ReplaceCI(fmt, "{Cells}",
                kind == WeightKind.Cells ? num((long)Math.Round(weight)) : "");
            var gm = System.Text.RegularExpressions.Regex.Match(fmt,
                @"\{GeomProp(\d+)\}",
                System.Text.RegularExpressions.RegexOptions.IgnoreCase);
            while (gm.Success)
            {
                int dec = Math.Min(int.Parse(gm.Groups[1].Value), 10);
                string rep = "";
                if (kind != WeightKind.None && totalWeight > 0)
                    rep = (weight / totalWeight)
                        .ToString("P" + dec, CultureInfo.InvariantCulture)
                        .Replace(" %", "%");
                fmt = fmt.Remove(gm.Index, gm.Length).Insert(gm.Index, rep);
                gm = System.Text.RegularExpressions.Regex.Match(fmt,
                    @"\{GeomProp(\d+)\}",
                    System.Text.RegularExpressions.RegexOptions.IgnoreCase);
            }
            fmt = ReplaceCI(fmt, "{increment_alpha_lc}", AlphaBullet(increment, false));
            fmt = ReplaceCI(fmt, "{increment_alpha_uc}", AlphaBullet(increment, true));
            fmt = ReplaceCI(fmt, "{increment_roman_lc}", Roman(increment).ToLowerInvariant());
            fmt = ReplaceCI(fmt, "{increment_roman_uc}", Roman(increment));
            fmt = ReplaceCI(fmt, "{Increment}", num(increment));
            fmt = ReplaceCI(fmt, "{total_count}", num(totalCount));

            // {PropN}: proportion of the total as a percentage with N decimals.
            var m = System.Text.RegularExpressions.Regex.Match(fmt, @"\{Prop(\d+)\}",
                System.Text.RegularExpressions.RegexOptions.IgnoreCase);
            while (m.Success)
            {
                int dec = Math.Min(int.Parse(m.Groups[1].Value), 10);
                double prop = totalCount > 0 ? (double)count / totalCount : 0.0;
                fmt = fmt.Remove(m.Index, m.Length).Insert(m.Index,
                    prop.ToString("P" + dec, CultureInfo.InvariantCulture)
                        .Replace(" %", "%"));
                m = System.Text.RegularExpressions.Regex.Match(fmt, @"\{Prop(\d+)\}",
                    System.Text.RegularExpressions.RegexOptions.IgnoreCase);
            }
            return fmt;
        }

        private static bool ContainsCI(string s, string tok) =>
            s.IndexOf(tok, StringComparison.OrdinalIgnoreCase) >= 0;

        private static string ReplaceCI(string s, string tok, string with)
        {
            int i;
            while ((i = s.IndexOf(tok, StringComparison.OrdinalIgnoreCase)) >= 0)
                s = s.Remove(i, tok.Length).Insert(i, with);
            return s;
        }

        /// <summary>1 -> a, 26 -> z, 27 -> aa (spreadsheet-column style).</summary>
        public static string AlphaBullet(long n, bool upper)
        {
            if (n < 1) n = 1;
            var sb = new StringBuilder();
            while (n > 0)
            {
                n--;
                sb.Insert(0, (char)('a' + (int)(n % 26)));
                n /= 26;
            }
            string s = sb.ToString();
            return upper ? s.ToUpperInvariant() : s;
        }

        public static string Roman(long n)
        {
            if (n < 1) n = 1;
            if (n > 30000) return n.ToString(CultureInfo.InvariantCulture);
            var vals = new[] { 1000L, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1 };
            var syms = new[] { "M", "CM", "D", "CD", "C", "XC", "L", "XL",
                               "X", "IX", "V", "IV", "I" };
            var sb = new StringBuilder();
            for (int i = 0; i < vals.Length; i++)
                while (n >= vals[i]) { sb.Append(syms[i]); n -= vals[i]; }
            return sb.ToString();
        }

        // ---- data gathering -------------------------------------------------
        /// <summary>One value class: the values (one per analyzed field, in
        /// analysis order, nulls rendered as a marker) and its count.</summary>
        public sealed class ValueClass
        {
            public string[] Values;                // display strings
            public IComparable[] SortKeys;         // typed, for ordering
            public long Count;
            public double Weight;                  // total geodesic size / cells
        }

        public sealed class Section
        {
            public string Title = "";              // e.g. Attribute Field [A]
            public long NullCount;                 // separate mode only
            public double NullWeight;              // their geometry total
            public List<ValueClass> Classes = new List<ValueClass>();
        }

        public sealed class SummaryResult
        {
            public string DatasetName = "";
            public long TotalCount;
            public bool SelectedOnly;
            public bool Combined;
            public WeightKind Kind = WeightKind.None;
            public double TotalWeight;             // all records, nulls included
            public double CellAreaM2;              // VAT rasters; 0 = unknown
            public List<string> FieldNames = new List<string>();
            public List<Section> Sections = new List<Section>();
        }

        public const string NullMarker = "<Null>";

        /// <summary>Every summarizable field on the source (geometry, blob,
        /// raster and XML fields excluded; a raster's fields come from its
        /// attribute table). MUST run on the MCT.</summary>
        public static List<string> ListSummaryFields(PolarPlots.PolarPlotData.Source src)
        {
            var names = new List<string>();
            Table t = null;
            try
            {
                t = src.IsRaster
                    ? src.RasterLayer.GetRaster()?.GetAttributeTable()
                    : PolarPlots.PolarPlotData.GetTable(src);
                if (t == null) return names;
                foreach (var f in t.GetDefinition().GetFields())
                {
                    if (f.FieldType == FieldType.Geometry ||
                        f.FieldType == FieldType.Blob ||
                        f.FieldType == FieldType.Raster ||
                        f.FieldType == FieldType.XML)
                        continue;
                    names.Add(f.Name);
                }
            }
            catch { }
            finally
            {
                if (t != null && !src.IsRaster) t.Dispose();
            }
            return names;
        }

        /// <summary>Reads the table and builds the per-class counts. MUST run
        /// on the MCT (QueuedTask). ``fields`` is in ANALYSIS ORDER; it drives
        /// both the {valN} tokens and the sort precedence.
        /// ``includeGeometry``: also total each class's geodesic length
        /// (polylines) or area (polygons), or its cell count (VAT rasters) --
        /// the per-unit-landscape numbers behind the {Geom*}/{Cells} tokens
        /// and the extra table columns.</summary>
        public static SummaryResult Gather(PolarPlots.PolarPlotData.Source src,
            IReadOnlyList<string> fields, bool selectedOnly, bool combine,
            bool sort, bool includeGeometry = false)
        {
            var res = new SummaryResult
            {
                DatasetName = src?.Name ?? "",
                SelectedOnly = selectedOnly,
                Combined = combine,
            };
            res.FieldNames.AddRange(fields);

            Table table = null;
            bool dispose = true;
            if (src.IsRaster)
            {
                var raster = src.RasterLayer.GetRaster();
                table = raster?.GetAttributeTable();
                if (table == null)
                    throw new InvalidOperationException(
                        "This raster has no attribute table (build one with "
                        + "Build Raster Attribute Table, or use an integer "
                        + "raster).");
                if (includeGeometry)
                {
                    res.Kind = WeightKind.Cells;
                    try
                    {
                        var size = raster.GetMeanCellSize();
                        var rsr = raster.GetSpatialReference();
                        if (rsr != null && !rsr.IsGeographic)
                        {
                            double f = (rsr.Unit as LinearUnit)
                                ?.ConversionFactor ?? 1.0;   // meters per unit
                            res.CellAreaM2 = Math.Abs(
                                size.Item1 * size.Item2) * f * f;
                        }
                    }
                    catch { }
                }
            }
            else
            {
                table = PolarPlots.PolarPlotData.GetTable(src);
                if (includeGeometry && table is FeatureClass fcls)
                {
                    var st = fcls.GetDefinition().GetShapeType();
                    if (st == GeometryType.Polyline)
                        res.Kind = WeightKind.LengthM;
                    else if (st == GeometryType.Polygon)
                        res.Kind = WeightKind.AreaM2;
                }
            }
            if (table == null)
                throw new InvalidOperationException("The dataset's table could not be opened.");

            try
            {
                var def = table.GetDefinition();
                var idx = new int[fields.Count];
                var ftype = new FieldType[fields.Count];
                for (int i = 0; i < fields.Count; i++)
                {
                    idx[i] = def.FindField(fields[i]);
                    if (idx[i] < 0)
                        throw new InvalidOperationException(
                            "Field not found: " + fields[i]);
                    ftype[i] = def.GetFields()[idx[i]].FieldType;
                }

                // VAT rasters weight by the Count column (cells per class)
                int cellCountIdx = -1;
                if (res.Kind == WeightKind.Cells)
                {
                    cellCountIdx = def.GetFields().ToList().FindIndex(f =>
                        string.Equals(f.Name, "Count",
                            StringComparison.OrdinalIgnoreCase));
                    if (cellCountIdx < 0) res.Kind = WeightKind.None;
                }

                // the geodesic size of one row's shape (0 when absent)
                double RowWeight(Row row)
                {
                    if (res.Kind == WeightKind.Cells)
                    {
                        try
                        {
                            object c = row[cellCountIdx];
                            return c == null || c is DBNull ? 0.0
                                : Convert.ToDouble(c,
                                    CultureInfo.InvariantCulture);
                        }
                        catch { return 0.0; }
                    }
                    if (res.Kind == WeightKind.None) return 0.0;
                    Geometry g = null;
                    try { g = (row as Feature)?.GetShape(); } catch { }
                    if (g == null || g.IsEmpty) return 0.0;
                    try
                    {
                        return res.Kind == WeightKind.LengthM
                            ? Math.Abs(GeometryEngine.Instance
                                .GeodesicLength(g))
                            : Math.Abs(GeometryEngine.Instance
                                .GeodesicArea(g));
                    }
                    catch
                    {
                        // unknown coordinate system: planar map units
                        try
                        {
                            return res.Kind == WeightKind.LengthM
                                ? Math.Abs((g as Polyline)?.Length ?? 0.0)
                                : Math.Abs((g as Polygon)?.Area ?? 0.0);
                        }
                        catch { return 0.0; }
                    }
                }

                var qf = new QueryFilter();
                if (selectedOnly && !src.IsRaster)
                {
                    var ids = PolarPlots.PolarPlotData.GetSelectedIds(src);
                    if (ids != null && ids.Count > 0)
                        qf.ObjectIDs = ids.ToList();
                    else
                        selectedOnly = false;      // nothing selected: use all
                }

                if (combine)
                {
                    var section = new Section
                    {
                        Title = "Attribute Fields " + string.Join(", ",
                            fields.Select(f => "[" + f + "]")),
                    };
                    var classes = new Dictionary<string, ValueClass>();
                    using (var cur = table.Search(qf, false))
                    {
                        while (cur.MoveNext())
                        {
                            using (var row = cur.Current)
                            {
                                res.TotalCount++;
                                double wrow = RowWeight(row);
                                res.TotalWeight += wrow;
                                var disp = new string[fields.Count];
                                var keys = new IComparable[fields.Count];
                                var keySb = new StringBuilder();
                                for (int i = 0; i < fields.Count; i++)
                                {
                                    object v = row[idx[i]];
                                    disp[i] = Display(v);
                                    keys[i] = SortKey(v, ftype[i]);
                                    keySb.Append(disp[i]).Append('');
                                }
                                string key = keySb.ToString();
                                if (!classes.TryGetValue(key, out var vc))
                                {
                                    vc = new ValueClass
                                    { Values = disp, SortKeys = keys };
                                    classes[key] = vc;
                                }
                                vc.Count++;
                                vc.Weight += wrow;
                            }
                        }
                    }
                    section.Classes = classes.Values.ToList();
                    if (sort) SortClasses(section.Classes, fields.Count);
                    res.Sections.Add(section);
                }
                else
                {
                    // Each field separately: one pass per field (keeps memory
                    // trivial and matches the original tool).
                    foreach (var (fname, fi) in fields.Select((f, i) => (f, i)))
                    {
                        var section = new Section
                        { Title = "Attribute Field [" + fname + "]" };
                        var classes = new Dictionary<string, ValueClass>();
                        long total = 0;
                        using (var cur = table.Search(qf, false))
                        {
                            while (cur.MoveNext())
                            {
                                using (var row = cur.Current)
                                {
                                    total++;
                                    double wrow = RowWeight(row);
                                    if (fi == 0) res.TotalWeight += wrow;
                                    object v = row[idx[fi]];
                                    if (v == null || v is DBNull)
                                    {
                                        section.NullCount++;
                                        section.NullWeight += wrow;
                                        continue;
                                    }
                                    string d = Display(v);
                                    if (!classes.TryGetValue(d, out var vc))
                                    {
                                        vc = new ValueClass
                                        {
                                            Values = new[] { d },
                                            SortKeys = new[] { SortKey(v, ftype[fi]) },
                                        };
                                        classes[d] = vc;
                                    }
                                    vc.Count++;
                                    vc.Weight += wrow;
                                }
                            }
                        }
                        res.TotalCount = total;
                        section.Classes = classes.Values.ToList();
                        if (sort) SortClasses(section.Classes, 1);
                        res.Sections.Add(section);
                    }
                }
            }
            finally
            {
                if (dispose && !src.IsRaster)
                    table.Dispose();
            }
            return res;
        }

        private static string Display(object v)
        {
            if (v == null || v is DBNull) return NullMarker;
            if (v is DateTime dt)
                return dt.TimeOfDay == TimeSpan.Zero
                    ? dt.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)
                    : dt.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture);
            if (v is double d) return d.ToString("R", CultureInfo.InvariantCulture);
            if (v is float f) return f.ToString("R", CultureInfo.InvariantCulture);
            return Convert.ToString(v, CultureInfo.InvariantCulture) ?? "";
        }

        private static IComparable SortKey(object v, FieldType t)
        {
            // Nulls sort first within their type.
            switch (t)
            {
                case FieldType.Integer:
                case FieldType.SmallInteger:
                case FieldType.BigInteger:
                case FieldType.OID:
                case FieldType.Single:
                case FieldType.Double:
                    if (v == null || v is DBNull) return double.NegativeInfinity;
                    try { return Convert.ToDouble(v, CultureInfo.InvariantCulture); }
                    catch { return double.NegativeInfinity; }
                case FieldType.Date:
                    if (v is DateTime dt) return dt;
                    return DateTime.MinValue;
                default:
                    return (v == null || v is DBNull) ? "" : Display(v);
            }
        }

        private static void SortClasses(List<ValueClass> classes, int nKeys)
        {
            classes.Sort((a, b) =>
            {
                for (int i = 0; i < nKeys; i++)
                {
                    int c;
                    var ka = a.SortKeys[i];
                    var kb = b.SortKeys[i];
                    if (ka is string sa && kb is string sb2)
                        c = string.Compare(sa, sb2, StringComparison.OrdinalIgnoreCase);
                    else if (ka.GetType() == kb.GetType())
                        c = ka.CompareTo(kb);
                    else
                        c = string.Compare(ka.ToString(), kb.ToString(),
                            StringComparison.OrdinalIgnoreCase);
                    if (c != 0) return c;
                }
                return 0;
            });
        }

        // ---- report assembly ------------------------------------------------
        /// <summary>"Length = 12.3 km" / "Area = 45.6 ha" / "Cells = 789
        /// (7.9 ha)" -- the human phrase for a geometry weight (empty when
        /// there is none).</summary>
        public static string WeightText(WeightKind kind, double w,
                                        double cellAreaM2, bool commas)
        {
            string dn(double v) => v.ToString(commas ? "#,##0.###" : "0.###",
                                              CultureInfo.InvariantCulture);
            switch (kind)
            {
                case WeightKind.LengthM:
                    return w >= 10000
                        ? "Length = " + dn(w / 1000.0) + " km"
                        : "Length = " + dn(w) + " m";
                case WeightKind.AreaM2:
                    return "Area = " + dn(w / 1e4) + " ha";
                case WeightKind.Cells:
                    string s = "Cells = " + ((long)Math.Round(w)).ToString(
                        commas ? "#,##0" : "0", CultureInfo.InvariantCulture);
                    if (cellAreaM2 > 0)
                        s += " (" + dn(w * cellAreaM2 / 1e4) + " ha)";
                    return s;
                default:
                    return "";
            }
        }

        /// <summary>The dataset-total phrase for the report header
        /// ("Total length: ...", "Total area: ..."), empty when no
        /// geometry was gathered.</summary>
        public static string TotalWeightText(SummaryResult r, bool commas)
        {
            if (r.Kind == WeightKind.None) return "";
            string body = WeightText(r.Kind, r.TotalWeight, r.CellAreaM2,
                                     commas);
            return "Total " + char.ToLowerInvariant(body[0]) + body.Substring(1)
                .Replace(" = ", ": ");
        }

        /// <summary>Plain-text report; the FlowDocument version is built by
        /// the report window from the same pieces.</summary>
        public static string BuildPlainReport(SummaryResult r, string format)
        {
            bool commas = ContainsCI(format ?? "", "{commas}");
            string num(long v) => v.ToString(commas ? "#,##0" : "0",
                                             CultureInfo.InvariantCulture);
            var sb = new StringBuilder();
            sb.AppendLine("Attribute Summary Report");
            sb.AppendLine("Dataset: " + r.DatasetName +
                          (r.SelectedOnly ? "  (selected records only)" : ""));
            sb.AppendLine("Records analyzed: " + num(r.TotalCount));
            if (r.Kind != WeightKind.None)
                sb.AppendLine(TotalWeightText(r, commas));
            sb.AppendLine(new string('-', 55));
            foreach (var sec in r.Sections)
            {
                sb.AppendLine();
                sb.AppendLine(sec.Title);
                long inc = 0;
                if (sec.NullCount > 0)
                {
                    inc++;
                    sb.AppendLine(NullLine(r, sec, inc, commas));
                }
                foreach (var vc in sec.Classes)
                {
                    inc++;
                    sb.AppendLine(Render(format, vc.Values, inc, vc.Count,
                                         r.TotalCount, vc.Weight,
                                         r.TotalWeight, r.Kind,
                                         r.CellAreaM2));
                }
            }
            return sb.ToString();
        }

        /// <summary>The "Null Values" report line, geometry-aware.</summary>
        public static string NullLine(SummaryResult r, Section sec, long inc,
                                      bool commas)
        {
            string num(long v) => v.ToString(commas ? "#,##0" : "0",
                                             CultureInfo.InvariantCulture);
            double p = r.TotalCount > 0
                ? (double)sec.NullCount / r.TotalCount : 0;
            string line = "  " + inc + "] Null Values:  Count = " +
                num(sec.NullCount) + ", Proportion = " +
                p.ToString("P3", CultureInfo.InvariantCulture)
                 .Replace(" %", "%");
            if (r.Kind != WeightKind.None && sec.NullWeight > 0)
                line += ", " + WeightText(r.Kind, sec.NullWeight,
                                          r.CellAreaM2, commas);
            return line;
        }

        /// <summary>Tab-separated table (headers + one row per class) for a
        /// clean paste into Excel, independent of the format template. The
        /// geometry columns appear when geometry was gathered.</summary>
        public static string BuildTsv(SummaryResult r)
        {
            string geomHead;
            switch (r.Kind)
            {
                case WeightKind.LengthM:
                    geomHead = "\tLength_m\tProp_of_Length"; break;
                case WeightKind.AreaM2:
                    geomHead = "\tArea_m2\tArea_ha\tProp_of_Area"; break;
                case WeightKind.Cells:
                    geomHead = r.CellAreaM2 > 0
                        ? "\tCells\tArea_m2\tArea_ha\tProp_of_Cells"
                        : "\tCells\tProp_of_Cells";
                    break;
                default:
                    geomHead = ""; break;
            }
            string GeomCols(double w)
            {
                if (r.Kind == WeightKind.None) return "";
                string prop = (r.TotalWeight > 0 ? w / r.TotalWeight : 0)
                    .ToString("0.00000", CultureInfo.InvariantCulture);
                string d(double v) =>
                    v.ToString("0.###", CultureInfo.InvariantCulture);
                switch (r.Kind)
                {
                    case WeightKind.LengthM:
                        return "\t" + d(w) + "\t" + prop;
                    case WeightKind.AreaM2:
                        return "\t" + d(w) + "\t" + d(w / 1e4) + "\t" + prop;
                    default:
                        long cells = (long)Math.Round(w);
                        return r.CellAreaM2 > 0
                            ? "\t" + cells + "\t" + d(w * r.CellAreaM2) +
                              "\t" + d(w * r.CellAreaM2 / 1e4) + "\t" + prop
                            : "\t" + cells + "\t" + prop;
                }
            }
            var sb = new StringBuilder();
            foreach (var sec in r.Sections)
            {
                if (sb.Length > 0) sb.AppendLine();
                sb.AppendLine(sec.Title);
                if (r.Combined)
                    sb.AppendLine(string.Join("\t", r.FieldNames) +
                                  "\tCount\tProportion" + geomHead);
                else
                    sb.AppendLine("Value\tCount\tProportion" + geomHead);
                if (sec.NullCount > 0)
                {
                    double p0 = r.TotalCount > 0
                        ? (double)sec.NullCount / r.TotalCount : 0;
                    sb.AppendLine(NullMarker + "\t" + sec.NullCount + "\t" +
                        p0.ToString("0.00000", CultureInfo.InvariantCulture) +
                        GeomCols(sec.NullWeight));
                }
                foreach (var vc in sec.Classes)
                {
                    double p = r.TotalCount > 0
                        ? (double)vc.Count / r.TotalCount : 0;
                    sb.AppendLine(string.Join("\t", vc.Values) + "\t" +
                        vc.Count + "\t" +
                        p.ToString("0.00000", CultureInfo.InvariantCulture) +
                        GeomCols(vc.Weight));
                }
            }
            return sb.ToString();
        }
    }

    /// <summary>Named format presets + last-used settings, persisted in the
    /// per-user store like the rest of the add-in.</summary>
    internal static class SummaryFormatStore
    {
        public static string StoreDir()
        {
            string b = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (string.IsNullOrEmpty(b))
                b = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            string dir = Path.Combine(b, "JennessEnterprises", "WildlifeTools",
                                      "attribute_summary_formats");
            Directory.CreateDirectory(dir);
            return dir;
        }

        public sealed class FormatDto
        {
            [JsonPropertyName("format")] public string Format { get; set; } =
                "JennessAttributeSummaryFormat";
            [JsonPropertyName("version")] public int Version { get; set; } = 1;
            public string Text { get; set; } = AttributeSummary.DefaultFormat;
        }

        public static void Save(string path, string text) =>
            File.WriteAllText(path, JsonSerializer.Serialize(
                new FormatDto { Text = text },
                new JsonSerializerOptions { WriteIndented = true }));

        public static string Load(string path)
        {
            var dto = JsonSerializer.Deserialize<FormatDto>(File.ReadAllText(path),
                new JsonSerializerOptions
                { ReadCommentHandling = JsonCommentHandling.Skip, AllowTrailingCommas = true });
            if (dto == null || dto.Format != "JennessAttributeSummaryFormat")
                return null;
            return dto.Text;
        }

        // Last-used dialog settings.
        private static string SettingsPath() =>
            Path.Combine(Path.GetDirectoryName(StoreDir()),
                         "attribute_summary_settings.json");

        public sealed class Settings
        {
            public string FormatText { get; set; } = AttributeSummary.DefaultFormat;
            public bool Combine { get; set; }
            public bool Sort { get; set; } = true;
            public bool SelectedOnly { get; set; }
            public bool IncludeGeometry { get; set; } = true;
        }

        public static Settings LoadSettings()
        {
            try
            {
                if (File.Exists(SettingsPath()))
                    return JsonSerializer.Deserialize<Settings>(
                        File.ReadAllText(SettingsPath())) ?? new Settings();
            }
            catch { }
            return new Settings();
        }

        public static void SaveSettings(Settings s)
        {
            try
            {
                File.WriteAllText(SettingsPath(),
                    JsonSerializer.Serialize(s,
                        new JsonSerializerOptions { WriteIndented = true }));
            }
            catch { }
        }
    }
}
