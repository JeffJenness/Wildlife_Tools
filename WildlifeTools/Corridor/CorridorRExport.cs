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

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Writes the "export for R" tables: one comma-delimited file per category
    /// class of the template raster, each row a complete-case cell and each column
    /// a variable's raw value -- the same schema the original SSI "Export for R
    /// Analysis" tool produced (no id/coordinate columns, no standardization). This
    /// lets users who prefer to run the fuzzy clustering in R branch off with data
    /// in the expected format; it reuses the same aligned <see cref="RasterStack"/>
    /// the in-Pro analysis uses.
    /// </summary>
    internal static class CorridorRExport
    {
        /// <summary>Write one FuzzyData_Class&lt;value&gt;.csv per class into
        /// <paramref name="folder"/>. Returns the file paths written.</summary>
        public static List<string> WriteTables(RasterStack stack, string folder)
        {
            Directory.CreateDirectory(folder);
            var written = new List<string>();
            // RFC 4180-quote the names: a comma (legal in Pro layer names)
            // in the header otherwise shifts every column read.csv sees,
            // silently misaligning the variables LF_fxns.R clusters on.
            string header = string.Join(",",
                stack.VariableNames.Select(CsvQuote));

            foreach (var cd in stack.Classes.Values.OrderBy(c => c.Value))
            {
                string fileName = "FuzzyData_Class" + FormatCode(cd.Value) + ".csv";
                string path = UniquePath(folder, fileName);
                using (var w = new StreamWriter(path, false))
                {
                    w.WriteLine(header);
                    foreach (var row in cd.Rows)
                        w.WriteLine(string.Join(",", row.Select(
                            v => v.ToString("R", CultureInfo.InvariantCulture))));
                }
                written.Add(path);
            }
            return written;
        }

        /// <summary>RFC 4180: wrap in quotes when the value carries a
        /// comma, quote, or line break; double any embedded quotes.</summary>
        private static string CsvQuote(string s)
        {
            s = s ?? "";
            if (s.IndexOfAny(new[] { ',', '"', (char)10, (char)13 }) < 0)
                return s;
            return "\"" + s.Replace("\"", "\"\"") + "\"";
        }

        private static string FormatCode(double code)
        {
            // Category codes are normally integers; keep those clean.
            if (Math.Abs(code - Math.Round(code)) < 1e-9)
                return ((long)Math.Round(code)).ToString(CultureInfo.InvariantCulture);
            return code.ToString("R", CultureInfo.InvariantCulture).Replace('.', '_');
        }

        private static string UniquePath(string folder, string fileName)
        {
            string path = Path.Combine(folder, fileName);
            if (!File.Exists(path)) return path;
            string stem = Path.GetFileNameWithoutExtension(fileName);
            string ext = Path.GetExtension(fileName);
            for (int i = 2; ; i++)
            {
                string cand = Path.Combine(folder, $"{stem}_{i}{ext}");
                if (!File.Exists(cand)) return cand;
            }
        }
    }
}
