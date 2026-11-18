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
using System.Globalization;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace WildlifeTools.OnlineData.Movebank
{
    /// <summary>A dataset published in the Movebank Data Repository.</summary>
    internal sealed class MovebankPackage
    {
        public string Uuid, Title, Doi, Page, License, LicenseUrl, Citation, Issued, Abstract, Paper;
        public List<string> Taxa = new List<string>();
        public List<string> Sensors = new List<string>();
        public List<string> Keywords = new List<string>();
        public List<string> Authors = new List<string>();
        public string Journal;
        public long Animals = -1, Locations = -1;
        public string BundlesLink;
        public string DoiUrl => string.IsNullOrEmpty(Doi) ? Page : "https://doi.org/" + Doi;
    }

    /// <summary>One file of a published dataset.</summary>
    internal sealed class MovebankFile
    {
        public string Name;
        public long Size;
        public string Kind;         // data, reference, readme, other
    }

    /// <summary>
    /// Searches the Movebank Data Repository (datarepository.movebank.org,
    /// a DSpace server), which needs no account. The window uses this to
    /// find a dataset; fetching and writing it is the geoprocessing tool's
    /// job. No ArcGIS types here.
    /// </summary>
    internal sealed class MovebankClient : IDisposable
    {
        internal const string Repo = "https://datarepository.movebank.org/server/api/";
        internal const string RepoSite = "https://datarepository.movebank.org";

        private readonly HttpClient _http;

        internal MovebankClient()
        {
            _http = new HttpClient { Timeout = TimeSpan.FromSeconds(100) };
            _http.DefaultRequestHeaders.UserAgent.ParseAdd("WildlifeTools-AddIn (Jenness Enterprises; ArcGIS Pro)");
            _http.DefaultRequestHeaders.Accept.ParseAdd("application/json");
        }

        public void Dispose() => _http.Dispose();

        /// <summary>Published datasets that match, newest first.
        /// <paramref name="text"/> searches titles, abstracts, species and
        /// keywords; <paramref name="sensor"/> is a sensor name as the
        /// repository writes it ("GPS"), or null.</summary>
        internal async Task<(List<MovebankPackage> Packages, long Total)> SearchAsync(string text, string sensor,
            int page, int size, CancellationToken ct)
        {
            string url = Repo + "discover/search/objects?dsoType=ITEM&f.entityType=Datapackage,equals"
                + "&size=" + size.ToString(CultureInfo.InvariantCulture)
                + "&page=" + page.ToString(CultureInfo.InvariantCulture)
                + "&sort=dc.date.accessioned,DESC"
                + (string.IsNullOrWhiteSpace(text) ? "" : "&query=" + Uri.EscapeDataString(text.Trim()))
                + (string.IsNullOrWhiteSpace(sensor) ? "" : "&f.sensorName=" + Uri.EscapeDataString(sensor.Trim()) + ",equals");
            var list = new List<MovebankPackage>();
            long total = 0;
            using (JsonDocument doc = await GetAsync(url, ct).ConfigureAwait(false))
            {
                if (doc.RootElement.TryGetProperty("_embedded", out JsonElement e1)
                    && e1.TryGetProperty("searchResult", out JsonElement res))
                {
                    if (res.TryGetProperty("page", out JsonElement pg) && pg.TryGetProperty("totalElements", out JsonElement te))
                    {
                        total = te.GetInt64();
                    }
                    if (res.TryGetProperty("_embedded", out JsonElement e2) && e2.TryGetProperty("objects", out JsonElement objs))
                    {
                        foreach (JsonElement o in objs.EnumerateArray())
                        {
                            if (o.TryGetProperty("_embedded", out JsonElement e3)
                                && e3.TryGetProperty("indexableObject", out JsonElement item))
                            {
                                list.Add(Read(item));
                            }
                        }
                    }
                }
            }
            return (list, Math.Max(total, list.Count));
        }

        /// <summary>The files of a dataset (its original files only).</summary>
        internal async Task<List<MovebankFile>> FilesAsync(MovebankPackage p, CancellationToken ct)
        {
            var files = new List<MovebankFile>();
            if (string.IsNullOrEmpty(p.BundlesLink))
            {
                return files;
            }
            using (JsonDocument doc = await GetAsync(p.BundlesLink, ct).ConfigureAwait(false))
            {
                if (!doc.RootElement.TryGetProperty("_embedded", out JsonElement e) || !e.TryGetProperty("bundles", out JsonElement bundles))
                {
                    return files;
                }
                foreach (JsonElement b in bundles.EnumerateArray())
                {
                    if (Str(b, "name") != "ORIGINAL" || !b.TryGetProperty("_links", out JsonElement links)
                        || !links.TryGetProperty("bitstreams", out JsonElement bl))
                    {
                        continue;
                    }
                    using (JsonDocument bits = await GetAsync(Str(bl, "href") + "?size=100", ct).ConfigureAwait(false))
                    {
                        if (bits.RootElement.TryGetProperty("_embedded", out JsonElement be) && be.TryGetProperty("bitstreams", out JsonElement arr))
                        {
                            foreach (JsonElement bs in arr.EnumerateArray())
                            {
                                string name = Str(bs, "name") ?? "";
                                string low = name.ToLowerInvariant();
                                files.Add(new MovebankFile
                                {
                                    Name = name,
                                    Size = bs.TryGetProperty("sizeBytes", out JsonElement sz) && sz.ValueKind == JsonValueKind.Number ? sz.GetInt64() : 0,
                                    Kind = low.Contains("readme") ? "readme"
                                        : low.EndsWith(".csv", StringComparison.Ordinal) && low.Contains("reference-data") ? "reference"
                                        : low.EndsWith(".csv", StringComparison.Ordinal) ? "data" : "other",
                                });
                            }
                        }
                    }
                }
            }
            return files;
        }

        private async Task<JsonDocument> GetAsync(string url, CancellationToken ct)
        {
            using (HttpResponseMessage resp = await _http.GetAsync(url, ct).ConfigureAwait(false))
            {
                string text = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
                if (!resp.IsSuccessStatusCode)
                {
                    throw new InvalidOperationException(resp.StatusCode == HttpStatusCode.ServiceUnavailable
                        ? "The Movebank Data Repository is busy or unavailable right now. Try again in a few minutes."
                        : "The Movebank Data Repository returned an error (" + ((int)resp.StatusCode).ToString(CultureInfo.InvariantCulture) + ").");
                }
                try
                {
                    return JsonDocument.Parse(string.IsNullOrWhiteSpace(text) ? "null" : text);
                }
                catch (JsonException)
                {
                    throw new InvalidOperationException("The Movebank Data Repository returned something that is not the expected data.");
                }
            }
        }

        private static MovebankPackage Read(JsonElement item)
        {
            var p = new MovebankPackage { Uuid = Str(item, "uuid") ?? Str(item, "id") };
            if (item.TryGetProperty("_links", out JsonElement links) && links.TryGetProperty("bundles", out JsonElement b))
            {
                p.BundlesLink = Str(b, "href");
            }
            if (!item.TryGetProperty("metadata", out JsonElement md) || md.ValueKind != JsonValueKind.Object)
            {
                return p;
            }
            p.Title = First(md, "dc.title") ?? "";
            p.Doi = (First(md, "dc.identifier.doi") ?? "").Replace("doi:", "").Trim();
            p.Page = First(md, "dc.identifier.uri") ?? "";
            p.License = First(md, "dc.rights") ?? "";
            p.LicenseUrl = First(md, "dc.rights.uri") ?? "";
            p.Citation = Plain(First(md, "mdr.citation.CSE") ?? "");
            p.Issued = First(md, "dc.date.issued") ?? "";
            p.Abstract = First(md, "dc.description.abstract") ?? "";
            p.Paper = (First(md, "dc.relation.isreferencedby") ?? "").Replace("doi:", "https://doi.org/");
            p.Taxa = All(md, "dwc.ScientificName").Distinct().ToList();
            p.Sensors = All(md, "sensor.name").Distinct().ToList();
            p.Keywords = All(md, "dc.subject").Distinct(StringComparer.OrdinalIgnoreCase).ToList();
            p.Authors = All(md, "dc.contributor.author").ToList();
            p.Journal = First(md, "mdr.journal.title") ?? "";
            if (long.TryParse(First(md, "mdr.animal.count"), NumberStyles.Integer, CultureInfo.InvariantCulture, out long a))
            {
                p.Animals = a;
            }
            if (long.TryParse(First(md, "mdr.location.count"), NumberStyles.Integer, CultureInfo.InvariantCulture, out long n))
            {
                p.Locations = n;
            }
            return p;
        }

        private static IEnumerable<string> All(JsonElement md, string key)
        {
            if (md.TryGetProperty(key, out JsonElement arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (JsonElement v in arr.EnumerateArray())
                {
                    string s = Str(v, "value");
                    if (!string.IsNullOrEmpty(s))
                    {
                        yield return s;
                    }
                }
            }
        }

        private static string First(JsonElement md, string key) => All(md, key).FirstOrDefault();

        private static string Str(JsonElement e, string name) =>
            e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out JsonElement v) && v.ValueKind == JsonValueKind.String
                ? v.GetString() : null;

        /// <summary>Text with HTML tags and entities removed.</summary>
        internal static string Plain(string text) =>
            WebUtility.HtmlDecode(Regex.Replace(text ?? "", "<[^>]+>", "")).Trim();
    }
}
