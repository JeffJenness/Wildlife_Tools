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
using System.Net;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace WildlifeTools.OnlineData.Prism
{
    /// <summary>What can be told about a map layer from its definition (as
    /// JSON) and from the file behind it, for <see cref="PrismTimeSlider"/>:
    /// whether the layer uses time, whether it is a multidimensional layer
    /// of a netCDF file, which date it shows, and whether that file was
    /// written by the PRISM Climate Data tool. Nothing here touches ArcGIS,
    /// so all of it is tested outside ArcGIS Pro
    /// (claude_tests/PrismLayerFactsTest).</summary>
    internal static class PrismLayerFacts
    {
        /// <summary>The text the PRISM Climate Data tool writes into every
        /// netCDF file it makes, as the file's <c>made_with</c> attribute
        /// (prism_climate.py, MADE_WITH: the two must stay the same, and a
        /// test compares them). A file that holds it has single-date time
        /// steps on whole days, months or years.</summary>
        public const string Marker = "Wildlife and Forestry Tools for ArcGIS Pro: PRISM Climate Data";

        /// <summary>How far into a file the marker is looked for. A netCDF
        /// file keeps its own notes near its beginning: in the tool's files
        /// the text sits about 17 KB in, whatever the size of the
        /// file.</summary>
        public const int MarkerSearchBytes = 1024 * 1024;

        private static readonly Regex NetCdfInXml = new Regex(@">\s*([^<>]+?\.nc)\s*<",
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

        private static readonly Dictionary<string, (long Length, DateTime Written, bool Ours)> Known =
            new Dictionary<string, (long, DateTime, bool)>(StringComparer.OrdinalIgnoreCase);

        /// <summary>True when a layer's definition says the layer uses time:
        /// a <c>useTime</c> that is true anywhere in it, or time fields that
        /// name a field. When the definition cannot be read the answer is
        /// true, so that a layer nobody understands counts as one that must
        /// not be disturbed.</summary>
        public static bool IsTimeAware(string layerJson)
        {
            if (string.IsNullOrWhiteSpace(layerJson)) return true;
            try
            {
                using (JsonDocument doc = JsonDocument.Parse(layerJson))
                    return UsesTime(doc.RootElement, 0);
            }
            catch (JsonException)
            {
                return true;
            }
        }

        private static bool UsesTime(JsonElement e, int depth)
        {
            if (depth > 40) return false;
            if (e.ValueKind == JsonValueKind.Object)
            {
                foreach (JsonProperty p in e.EnumerateObject())
                {
                    if (p.NameEquals("useTime") && p.Value.ValueKind == JsonValueKind.True) return true;
                    if (p.NameEquals("timeFields") && p.Value.ValueKind == JsonValueKind.Object)
                    {
                        foreach (JsonProperty f in p.Value.EnumerateObject())
                        {
                            if ((f.NameEquals("startTimeField") || f.NameEquals("endTimeField"))
                                && f.Value.ValueKind == JsonValueKind.String
                                && !string.IsNullOrEmpty(f.Value.GetString()))
                                return true;
                        }
                    }
                    if (UsesTime(p.Value, depth + 1)) return true;
                }
            }
            else if (e.ValueKind == JsonValueKind.Array)
            {
                foreach (JsonElement item in e.EnumerateArray())
                    if (UsesTime(item, depth + 1)) return true;
            }
            return false;
        }

        /// <summary>Reads a multidimensional raster layer of a netCDF file
        /// from its definition: the file, the date the layer shows, and
        /// whether that date is a single date (not a span of days). False
        /// for every other kind of layer.</summary>
        public static bool TryReadNetCdfLayer(string layerJson, out string ncPath, out DateTime shownDate,
            out bool singleDate)
        {
            ncPath = null;
            shownDate = default;
            singleDate = false;
            if (string.IsNullOrWhiteSpace(layerJson)) return false;
            try
            {
                using (JsonDocument doc = JsonDocument.Parse(layerJson))
                {
                    JsonElement root = doc.RootElement;
                    if (root.ValueKind != JsonValueKind.Object) return false;
                    if (!root.TryGetProperty("activeSlice", out JsonElement slice)
                        || slice.ValueKind != JsonValueKind.Object) return false;
                    if (!slice.TryGetProperty("timeValue", out JsonElement time)
                        || time.ValueKind != JsonValueKind.Object) return false;
                    if (!time.TryGetProperty("start", out JsonElement start) || !TryDate(start, out shownDate))
                        return false;
                    if (time.TryGetProperty("end", out JsonElement end) && TryDate(end, out DateTime last))
                        singleDate = last == shownDate;
                    else
                        singleDate = false;         // a time with no end is not a single date
                    if (!root.TryGetProperty("dataConnection", out JsonElement conn)
                        || conn.ValueKind != JsonValueKind.Object) return false;
                    ncPath = NetCdfPath(conn);
                    return ncPath != null;
                }
            }
            catch (JsonException)
            {
                return false;
            }
        }

        /// <summary>A time in a layer definition: milliseconds since the
        /// start of 1970, or a date written out.</summary>
        private static bool TryDate(JsonElement e, out DateTime date)
        {
            date = default;
            if (e.ValueKind == JsonValueKind.Number && e.TryGetDouble(out double ms)
                && ms > -6.0e13 && ms < 2.5e14)
            {
                date = new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Unspecified).AddMilliseconds(ms);
                return true;
            }
            if (e.ValueKind == JsonValueKind.String
                && DateTime.TryParse(e.GetString(), System.Globalization.CultureInfo.InvariantCulture,
                    System.Globalization.DateTimeStyles.AdjustToUniversal
                    | System.Globalization.DateTimeStyles.AssumeUniversal, out DateTime parsed))
            {
                date = DateTime.SpecifyKind(parsed, DateTimeKind.Unspecified);
                return true;
            }
            return false;
        }

        /// <summary>The netCDF file a data connection points at, or null. A
        /// multidimensional layer names its file inside a description of
        /// the dataset written as XML; a plainer connection gives a folder
        /// and a file name.</summary>
        private static string NetCdfPath(JsonElement conn)
        {
            string dataset = conn.TryGetProperty("dataset", out JsonElement d) && d.ValueKind == JsonValueKind.String
                ? d.GetString() : null;
            if (string.IsNullOrWhiteSpace(dataset)) return null;
            if (dataset.TrimStart().StartsWith("<", StringComparison.Ordinal))
            {
                string found = null;
                foreach (Match m in NetCdfInXml.Matches(dataset))
                {
                    string path = WebUtility.HtmlDecode(m.Groups[1].Value).Trim();
                    if (!Path.IsPathRooted(path)) continue;
                    if (found != null && !string.Equals(found, path, StringComparison.OrdinalIgnoreCase))
                        return null;                 // two different files: not a layer of one netCDF file
                    found = path;
                }
                return found;
            }
            if (!dataset.EndsWith(".nc", StringComparison.OrdinalIgnoreCase)) return null;
            if (Path.IsPathRooted(dataset)) return dataset;
            string where = conn.TryGetProperty("workspaceConnectionString", out JsonElement w)
                && w.ValueKind == JsonValueKind.String ? w.GetString() : null;
            if (string.IsNullOrWhiteSpace(where)) return null;
            foreach (string part in where.Split(';'))
            {
                string p = part.Trim();
                if (p.StartsWith("DATABASE=", StringComparison.OrdinalIgnoreCase))
                {
                    string folder = p.Substring("DATABASE=".Length).Trim();
                    if (Path.IsPathRooted(folder)) return Path.Combine(folder, dataset);
                }
            }
            return null;
        }

        /// <summary>True when the netCDF file was written by the PRISM
        /// Climate Data tool: its first megabyte holds the tool's marker.
        /// False for a file that is missing, cannot be read or holds no
        /// marker. The answer is kept for as long as the file's size and
        /// date stay the same.</summary>
        public static bool WrittenByTool(string ncPath)
        {
            if (string.IsNullOrWhiteSpace(ncPath)) return false;
            try
            {
                var info = new FileInfo(ncPath);
                if (!info.Exists) return false;
                lock (Known)
                {
                    if (Known.TryGetValue(info.FullName, out var k) && k.Length == info.Length
                        && k.Written == info.LastWriteTimeUtc)
                        return k.Ours;
                }
                bool ours;
                using (var s = new FileStream(info.FullName, FileMode.Open, FileAccess.Read,
                    FileShare.ReadWrite | FileShare.Delete))
                    ours = ContainsMarker(s, MarkerSearchBytes);
                lock (Known)
                {
                    if (Known.Count > 200) Known.Clear();
                    Known[info.FullName] = (info.Length, info.LastWriteTimeUtc, ours);
                }
                return ours;
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException
                || ex is ArgumentException || ex is NotSupportedException
                || ex is System.Security.SecurityException)
            {
                return false;
            }
        }

        /// <summary>True when the marker's bytes are among the first
        /// <paramref name="limit"/> bytes of the stream.</summary>
        public static bool ContainsMarker(Stream s, int limit)
        {
            byte[] needle = Encoding.ASCII.GetBytes(Marker);
            var buffer = new byte[Math.Max(needle.Length, Math.Min(limit, 1024 * 1024))];
            int have = 0;
            while (have < buffer.Length)
            {
                int n = s.Read(buffer, have, buffer.Length - have);
                if (n <= 0) break;
                have += n;
            }
            for (int i = 0; i + needle.Length <= have; i++)
            {
                int j = 0;
                while (j < needle.Length && buffer[i + j] == needle[j]) j++;
                if (j == needle.Length) return true;
            }
            return false;
        }
    }
}
