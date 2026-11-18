// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Award notice, marking of modified versions, and
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
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;

namespace WildlifeTools.OnlineData.Gbif
{
    /// <summary>
    /// Reads the table inside a GBIF download zip without unpacking it to
    /// disk. GBIF's tables are tab-separated UTF-8 with one header row and
    /// no quoting (tabs and line breaks inside values are removed by GBIF):
    ///   SIMPLE_CSV   -> one "&lt;key&gt;.csv"
    ///   DWCA         -> "occurrence.txt" (the interpreted records)
    ///   SPECIES_LIST -> one "&lt;key&gt;.csv" with no coordinates
    /// No ArcGIS types here: the class is compiled into the headless tests.
    /// </summary>
    internal sealed class GbifDownloadFile : IDisposable
    {
        private readonly ZipArchive _zip;
        private readonly StreamReader _reader;

        /// <summary>The column names, in file order.</summary>
        public string[] Columns { get; }
        /// <summary>The name of the table inside the zip.</summary>
        public string TableName { get; }
        /// <summary>Column positions of the coordinates, or -1 when the
        /// table has none (a species list).</summary>
        public int LonIndex { get; }
        public int LatIndex { get; }
        public bool HasCoordinates => LonIndex >= 0 && LatIndex >= 0;

        internal GbifDownloadFile(string zipPath)
        {
            _zip = ZipFile.OpenRead(zipPath);
            ZipArchiveEntry entry =
                _zip.Entries.FirstOrDefault(e => e.FullName.Equals("occurrence.txt", StringComparison.OrdinalIgnoreCase))
                ?? _zip.Entries.Where(e => e.FullName.EndsWith(".csv", StringComparison.OrdinalIgnoreCase))
                               .OrderByDescending(e => e.Length).FirstOrDefault();
            if (entry == null)
            {
                _zip.Dispose();
                throw new InvalidDataException("The GBIF download does not contain a table this tool can read.");
            }
            TableName = entry.FullName;
            _reader = new StreamReader(entry.Open(), Encoding.UTF8, true, 1 << 16);
            string header = _reader.ReadLine() ?? "";
            Columns = header.Split('\t');
            LonIndex = Array.IndexOf(Columns, "decimalLongitude");
            LatIndex = Array.IndexOf(Columns, "decimalLatitude");
        }

        /// <summary>The rows, one array of values per record, in column
        /// order. Short rows are padded with empty strings.</summary>
        public IEnumerable<string[]> Rows()
        {
            string line;
            int n = Columns.Length;
            while ((line = _reader.ReadLine()) != null)
            {
                if (line.Length == 0)
                {
                    continue;
                }
                string[] parts = line.Split('\t');
                if (parts.Length != n)
                {
                    Array.Resize(ref parts, n);
                    for (int i = 0; i < n; i++)
                    {
                        parts[i] = parts[i] ?? "";
                    }
                }
                yield return parts;
            }
        }

        public void Dispose()
        {
            _reader?.Dispose();
            _zip?.Dispose();
        }
    }
}
