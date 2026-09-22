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
using WildlifeTools.PolarPlots;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Persistence and export for the histogram tool:
    ///  - JSON dataset presets ("JennessHistogramData") replacing the
    ///    original's dBASE save/load -- a preset holds the compressed
    ///    (value, weight) data plus symbology, so corridor comparisons can
    ///    be re-assembled without re-reading any raster;
    ///  - the two statistics tables (general stats + per-bin stats) as
    ///    schema+rows, renderable to CSV text or written to a geodatabase
    ///    or dBASE table by the window (any tabular format, per Jeff).
    /// </summary>
    internal static class HistogramStore
    {
        // =================================================================
        //  JSON presets
        // =================================================================
        public const string FormatName = "JennessHistogramData";

        public sealed class DatasetDto
        {
            public string Name { get; set; }
            public string SourceDesc { get; set; }
            public string Basis { get; set; }
            public double CellAreaM2 { get; set; }
            public bool ApproxCellArea { get; set; }
            public byte[] Fill { get; set; }          // r,g,b,a
            public byte[] Outline { get; set; }
            public double OutlineWidthPt { get; set; } = 1.0;
            public bool ShowBars { get; set; } = true;
            public bool ShowKde { get; set; }
            public double[] Values { get; set; }
            public double[] Weights { get; set; }
            // categorical datasets (v2): parallel code / name arrays
            public double[] LabelCodes { get; set; }
            public string[] LabelNames { get; set; }
        }

        public sealed class FileDto
        {
            [JsonPropertyName("format")] public string Format { get; set; } = FormatName;
            [JsonPropertyName("version")] public int Version { get; set; } = 2;
            public List<DatasetDto> Datasets { get; set; } = new List<DatasetDto>();
        }

        public static void Save(string path, IEnumerable<HistDataset> datasets)
        {
            var dto = new FileDto();
            foreach (var d in datasets)
                dto.Datasets.Add(new DatasetDto
                {
                    Name = d.Name,
                    SourceDesc = d.SourceDesc,
                    Basis = d.Basis.ToString(),
                    CellAreaM2 = d.CellAreaM2,
                    ApproxCellArea = d.ApproxCellArea,
                    Fill = new[] { d.Fill.R, d.Fill.G, d.Fill.B, d.Fill.A },
                    Outline = new[] { d.Outline.R, d.Outline.G, d.Outline.B,
                                      d.Outline.A },
                    OutlineWidthPt = d.OutlineWidthPt,
                    ShowBars = d.ShowBars,
                    ShowKde = d.ShowKde,
                    Values = d.Values,
                    Weights = d.Weights,
                    LabelCodes = d.Labels?.Keys.ToArray(),
                    LabelNames = d.Labels?.Values.ToArray(),
                });
            File.WriteAllText(path, JsonSerializer.Serialize(dto));
        }

        /// <summary>Load a preset; returns null when the file is not a
        /// histogram preset (mirrors the original's compatibility alert).</summary>
        public static List<HistDataset> Load(string path)
        {
            var dto = JsonSerializer.Deserialize<FileDto>(File.ReadAllText(path),
                new JsonSerializerOptions
                {
                    ReadCommentHandling = JsonCommentHandling.Skip,
                    AllowTrailingCommas = true,
                });
            if (dto == null || dto.Format != FormatName) return null;
            var list = new List<HistDataset>();
            foreach (var x in dto.Datasets)
            {
                var d = new HistDataset
                {
                    Name = x.Name ?? "",
                    SourceDesc = x.SourceDesc ?? "",
                    CellAreaM2 = x.CellAreaM2,
                    ApproxCellArea = x.ApproxCellArea,
                    OutlineWidthPt = x.OutlineWidthPt,
                    ShowBars = x.ShowBars,
                    ShowKde = x.ShowKde,
                    Values = x.Values ?? Array.Empty<double>(),
                    Weights = x.Weights ?? Array.Empty<double>(),
                };
                if (Enum.TryParse<WeightBasis>(x.Basis ?? "", out var b))
                    d.Basis = b;
                d.Fill = ToColor(x.Fill, new RgbaColor(0, 114, 178, 210));
                d.Outline = ToColor(x.Outline, new RgbaColor(0, 68, 106, 255));
                if (d.Values.Length != d.Weights.Length)
                    throw new InvalidOperationException(
                        "The preset's data arrays are inconsistent.");
                if (x.LabelCodes != null && x.LabelNames != null &&
                    x.LabelCodes.Length == x.LabelNames.Length &&
                    x.LabelCodes.Length > 0)
                {
                    d.Labels = new Dictionary<double, string>();
                    for (int i = 0; i < x.LabelCodes.Length; i++)
                        d.Labels[x.LabelCodes[i]] = x.LabelNames[i] ?? "";
                }
                d.ComputeStats();
                list.Add(d);
            }
            return list;
        }

        private static RgbaColor ToColor(byte[] c, RgbaColor fallback) =>
            c != null && c.Length == 4
                ? new RgbaColor(c[0], c[1], c[2], c[3]) : fallback;

        // =================================================================
        //  statistics tables (schema + rows)
        // =================================================================
        public sealed class TableSpec
        {
            public List<(string name, string type, int len, string alias)> Schema
                = new List<(string, string, int, string)>();
            public List<object[]> Rows = new List<object[]>();
        }

        /// <summary>General statistics: one row per dataset (the original's
        /// first export table, plus the weight basis and provenance).</summary>
        public static TableSpec GeneralTable(IReadOnlyList<HistDataset> ds)
        {
            var t = new TableSpec();
            t.Schema.Add(("DATASET", "TEXT", 254, "Dataset"));
            t.Schema.Add(("BASIS", "TEXT", 64, "Weight Basis"));
            t.Schema.Add(("TOTAL_W", "DOUBLE", 0, "Total Weight (cells / m2 / m / records)"));
            t.Schema.Add(("SUM", "DOUBLE", 0, "Sum of Value x Weight"));
            t.Schema.Add(("MEAN", "DOUBLE", 0, "Mean"));
            t.Schema.Add(("MIN", "DOUBLE", 0, "Minimum"));
            t.Schema.Add(("MAX", "DOUBLE", 0, "Maximum"));
            t.Schema.Add(("RANGE", "DOUBLE", 0, "Range"));
            t.Schema.Add(("STD_DEV", "DOUBLE", 0, "Standard Deviation"));
            t.Schema.Add(("VARIANCE", "DOUBLE", 0, "Variance"));
            t.Schema.Add(("MEDIAN", "DOUBLE", 0, "Median"));
            t.Schema.Add(("SE_MEAN", "DOUBLE", 0, "Standard Error of the Mean"));
            t.Schema.Add(("N_CLASSES", "LONG", 0, "Number of Classes (categorical)"));
            t.Schema.Add(("TOP_CLASS", "TEXT", 254, "Largest Class (categorical)"));
            t.Schema.Add(("TOP_PCT", "DOUBLE", 0, "Largest Class Percent (categorical)"));
            t.Schema.Add(("SOURCE", "TEXT", 254, "Source"));
            foreach (var d in ds)
            {
                var s = d.Stats;
                if (d.IsCategorical)
                {
                    // the value statistics describe CODES, not the classes:
                    // left blank rather than reported as if they meant
                    // something
                    var (top, share) = HistogramMath.LargestCategory(d);
                    double nan = double.NaN;
                    t.Rows.Add(new object[]
                    {
                        d.Name, BasisWord(d), s.SumWeights, nan, nan, nan,
                        nan, nan, nan, nan, nan, nan,
                        d.CategoryNames().Count, top, share, d.SourceDesc,
                    });
                    continue;
                }
                t.Rows.Add(new object[]
                {
                    d.Name, BasisWord(d), s.SumWeights, s.Sum, s.Mean, s.Min,
                    s.Max, s.Range, s.SD, s.Var, s.Median, s.SE,
                    null, null, double.NaN, d.SourceDesc,
                });
            }
            return t;
        }

        /// <summary>Per-bin statistics: one row per dataset x bin (the
        /// original's second export table).</summary>
        public static TableSpec BinsTable(IReadOnlyList<HistDataset> ds,
                                          HistogramOptions o)
        {
            var t = new TableSpec();
            t.Schema.Add(("DATASET", "TEXT", 254, "Dataset"));
            t.Schema.Add(("BIN", "LONG", 0, "Bin Number (1-based)"));
            t.Schema.Add(("CLASS", "TEXT", 254, "Class Name (categorical)"));
            t.Schema.Add(("BIN_MIN", "DOUBLE", 0, "Bin Lower Edge"));
            t.Schema.Add(("BIN_MAX", "DOUBLE", 0, "Bin Upper Edge"));
            t.Schema.Add(("WEIGHT", "DOUBLE", 0, "Weight in Bin (cells / m2 / m / records)"));
            t.Schema.Add(("UNIT_VAL", "DOUBLE", 0,
                "Value in the Display Unit (" +
                HistogramMath.UnitLabel(o.Unit, ds) + ")"));
            t.Schema.Add(("PERCENT", "DOUBLE", 0, "Percent of Dataset"));
            // categorical: one row per dataset x class on the SHARED class
            // axis (a class the dataset lacks gets an explicit 0 row)
            var cats = HistogramMath.UnionCategories(ds);
            int bins = cats.Count > 0 ? cats.Count : Math.Max(o.Bins, 1);
            double w = (o.AxisMax - o.AxisMin) / bins;
            foreach (var d in ds)
            {
                bool cat = d.IsCategorical && cats.Count > 0;
                var tot = cat
                    ? HistogramMath.CategoryTotals(d, cats)
                    : HistogramMath.BinTotals(d, bins, o.AxisMin, o.AxisMax,
                                              out _);
                for (int b = 0; b < bins; b++)
                {
                    double unitV = HistogramMath.ToUnit(tot[b], d, o.Unit,
                                                        o.CustomMultiplier);
                    if (double.IsNaN(unitV)) unitV = tot[b];
                    t.Rows.Add(new object[]
                    {
                        d.Name, b + 1,
                        cat ? cats[b] : null,
                        cat ? double.NaN : o.AxisMin + b * w,
                        cat ? double.NaN : o.AxisMin + (b + 1) * w,
                        tot[b], unitV,
                        d.Stats.SumWeights > 0
                            ? tot[b] / d.Stats.SumWeights * 100.0 : 0.0,
                    });
                }
            }
            return t;
        }

        public static string BasisWord(HistDataset d)
        {
            switch (d.Basis)
            {
                case WeightBasis.Cells:
                    return d.CellAreaM2 > 0
                        ? "Cells (" + d.CellAreaM2.ToString("0.###",
                              CultureInfo.InvariantCulture) + " m2 each"
                          + (d.ApproxCellArea ? ", approx." : "") + ")"
                        : "Cells";
                case WeightBasis.AreaM2: return "Geodesic area (m2)";
                case WeightBasis.LengthM: return "Geodesic length (m)";
                default: return "Records";
            }
        }

        // =================================================================
        //  CSV rendering
        // =================================================================
        public static string ToCsv(TableSpec t)
        {
            var sb = new StringBuilder();
            sb.AppendLine(string.Join(",",
                t.Schema.Select(s => CsvCell(s.name))));
            foreach (var row in t.Rows)
                sb.AppendLine(string.Join(",", row.Select(CsvValue)));
            return sb.ToString();
        }

        private static string CsvValue(object v)
        {
            if (v == null) return "";
            if (v is double d)
                return double.IsNaN(d) ? ""
                    : d.ToString("R", CultureInfo.InvariantCulture);
            if (v is int i) return i.ToString(CultureInfo.InvariantCulture);
            return CsvCell(Convert.ToString(v, CultureInfo.InvariantCulture));
        }

        private static string CsvCell(string s)
        {
            s = s ?? "";
            return s.Contains(',') || s.Contains('"') || s.Contains('\n')
                ? "\"" + s.Replace("\"", "\"\"") + "\"" : s;
        }

        /// <summary>The AddFields text form ("Name TYPE # len # #;...") for
        /// a schema -- one batched GP call per the standing rule.</summary>
        public static string AddFieldsText(TableSpec t) =>
            string.Join(";", t.Schema.Select(s =>
                s.name + " " + s.type + " # " +
                (s.len > 0 ? s.len.ToString(CultureInfo.InvariantCulture) : "#")
                + " # #"));
    }
}
