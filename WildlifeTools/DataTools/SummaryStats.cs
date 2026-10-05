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
using System.Text.RegularExpressions;

namespace WildlifeTools.DataTools
{
    /// <summary>
    /// Per-class statistics of numeric fields for Attribute Summary Reports
    /// (Jeff, 2026-10-05). A statistics placeholder names the statistic, the
    /// field, and optionally the decimal places:
    ///   {Mean:ElevationM}     {Mean:ElevationM:1}     {N:ElevationM}
    /// Statistics: Min, Mean, Median, Max, Range, StDev, Sum, N. Null values
    /// of the field are skipped; N is the number of values used.
    ///
    /// An OBSERVATION is one record -- or, for a raster's attribute table,
    /// one CELL: each row stands for Count cells, so every statistic there
    /// is weighted by the row's cell count (N = cells, Sum = sum over cells,
    /// and the mean, median and standard deviation are those of the cells).
    ///
    /// This file is deliberately free of ArcGIS types so that it can be
    /// compiled and tested on its own (claude_tests/SummaryStatsTest).
    /// </summary>
    internal static class SummaryStats
    {
        public enum Kind { Min, Mean, Median, Max, Range, StDev, Sum, N }

        public const int DefaultDecimals = 2;

        /// <summary>One statistics placeholder found in a format.</summary>
        public sealed class Token
        {
            public int Index, Length;        // position in the format text
            public Kind Stat;
            public string Field = "";        // as typed
            public int Decimals = -1;        // -1 = not given
        }

        // The field part is anything but braces and colons (joined fields
        // carry dots: "Parcels.Acres").
        private static readonly Regex Rx = new Regex(
            @"\{(Min|Mean|Median|Max|Range|StDev|Sum|N):([^{}:]+)(?::(\d{1,2}))?\}",
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

        public static List<Token> Parse(string format)
        {
            var list = new List<Token>();
            if (string.IsNullOrEmpty(format)) return list;
            foreach (Match m in Rx.Matches(format))
            {
                list.Add(new Token
                {
                    Index = m.Index,
                    Length = m.Length,
                    Stat = (Kind)Enum.Parse(typeof(Kind), m.Groups[1].Value, true),
                    Field = m.Groups[2].Value.Trim(),
                    Decimals = m.Groups[3].Success
                        ? Math.Min(int.Parse(m.Groups[3].Value,
                                             CultureInfo.InvariantCulture), 10)
                        : -1,
                });
            }
            return list;
        }

        /// <summary>The placeholder text a button inserts.</summary>
        public static string TokenText(Kind k, string field, int decimals) =>
            k == Kind.N
                ? "{N:" + field + "}"
                : "{" + k + ":" + field + ":" +
                  Math.Max(0, Math.Min(decimals, 10)) + "}";

        /// <summary>The finished statistics of one field in one class. A
        /// statistic that is not defined (no values; a standard deviation of
        /// one value) is NaN and prints as empty text.</summary>
        public sealed class FieldStats
        {
            public double N;
            public double Sum = double.NaN, Mean = double.NaN,
                          Median = double.NaN, Min = double.NaN,
                          Max = double.NaN, StDev = double.NaN;

            public double Get(Kind k)
            {
                switch (k)
                {
                    case Kind.Min: return Min;
                    case Kind.Mean: return Mean;
                    case Kind.Median: return Median;
                    case Kind.Max: return Max;
                    case Kind.Range: return Max - Min;
                    case Kind.StDev: return StDev;
                    case Kind.Sum: return Sum;
                    default: return N;
                }
            }
        }

        /// <summary>Collects one field's values for one class. The weight is
        /// 1 for a record and the cell count for a raster attribute-table
        /// row (a frequency weight: the row is that many observations).</summary>
        public sealed class Accumulator
        {
            private readonly List<double> _x = new List<double>();
            private List<double> _w;         // null while every weight is 1

            public void Add(double x, double w = 1.0)
            {
                if (double.IsNaN(x) || double.IsInfinity(x)) return;
                if (double.IsNaN(w) || w <= 0) return;
                if (w != 1.0 && _w == null)
                {
                    _w = new List<double>(_x.Count + 16);
                    for (int i = 0; i < _x.Count; i++) _w.Add(1.0);
                }
                _x.Add(x);
                _w?.Add(w);
            }

            public FieldStats Finish()
            {
                var s = new FieldStats();
                int n = _x.Count;
                if (n == 0) return s;

                // sort by value, carrying the weights
                var order = new int[n];
                for (int i = 0; i < n; i++) order[i] = i;
                Array.Sort(order, (a, b) => _x[a].CompareTo(_x[b]));

                double wsum = 0, sum = 0;
                for (int i = 0; i < n; i++)
                {
                    double w = _w == null ? 1.0 : _w[i];
                    wsum += w;
                    sum += w * _x[i];
                }
                s.N = wsum;
                s.Sum = sum;
                s.Mean = sum / wsum;
                s.Min = _x[order[0]];
                s.Max = _x[order[n - 1]];

                // sample standard deviation, each observation counted once
                if (wsum > 1)
                {
                    double ss = 0;
                    for (int i = 0; i < n; i++)
                    {
                        double d = _x[i] - s.Mean;
                        ss += (_w == null ? 1.0 : _w[i]) * d * d;
                    }
                    s.StDev = Math.Sqrt(ss / (wsum - 1));
                }

                // median of the observations: the middle one, or the mean of
                // the two middle ones when their number is even
                double kLo = Math.Floor((wsum + 1) / 2.0);
                double kHi = Math.Ceiling((wsum + 1) / 2.0);
                double xLo = double.NaN, xHi = double.NaN, cum = 0;
                for (int i = 0; i < n; i++)
                {
                    int j = order[i];
                    cum += _w == null ? 1.0 : _w[j];
                    if (double.IsNaN(xLo) && cum >= kLo) xLo = _x[j];
                    if (cum >= kHi) { xHi = _x[j]; break; }
                }
                if (double.IsNaN(xHi)) xHi = s.Max;     // rounding guard
                if (double.IsNaN(xLo)) xLo = xHi;
                s.Median = (xLo + xHi) / 2.0;
                return s;
            }
        }

        /// <summary>Example numbers for the window's Sample line.</summary>
        public static FieldStats Sample() => new FieldStats
        {
            N = 1234, Sum = 2290551.25, Mean = 1856.2, Median = 1790.5,
            Min = 1203.4, Max = 2914.8, StDev = 312.6,
        };

        public static string Format(Kind k, double v, int decimals, bool commas)
        {
            if (double.IsNaN(v) || double.IsInfinity(v)) return "";
            int d = k == Kind.N ? 0
                : decimals < 0 ? DefaultDecimals : Math.Min(decimals, 10);
            string f = (commas ? "#,##0" : "0") +
                       (d > 0 ? "." + new string('0', d) : "");
            return v.ToString(f, CultureInfo.InvariantCulture);
        }

        /// <summary>Replaces every statistics placeholder in a format.
        /// ``lookup`` gives the class's statistics for a field name, or null
        /// when there are none (field unknown, statistics not gathered): the
        /// placeholder then prints as empty text, like any other placeholder
        /// that does not apply.</summary>
        public static string Replace(string fmt, Func<string, FieldStats> lookup,
                                     bool commas)
        {
            if (string.IsNullOrEmpty(fmt)) return fmt ?? "";
            return Rx.Replace(fmt, m =>
            {
                var k = (Kind)Enum.Parse(typeof(Kind), m.Groups[1].Value, true);
                FieldStats st = lookup?.Invoke(m.Groups[2].Value.Trim());
                if (st == null) return "";
                // a class with no values: {N:..} is 0, the rest are
                // undefined (NaN) and print as empty text
                int dec = m.Groups[3].Success
                    ? int.Parse(m.Groups[3].Value, CultureInfo.InvariantCulture)
                    : -1;
                return Format(k, st.Get(k), dec, commas);
            });
        }
    }
}
