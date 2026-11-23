// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Award notice, marking of modified versions, use of names,
// and an additional permission to link with ArcGIS Pro).
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
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace WildlifeTools.OnlineData.Gbif
{
    // ---- what GBIF returns, reduced to what the tool uses --------------------

    /// <summary>A name in GBIF's backbone taxonomy.</summary>
    internal sealed class GbifTaxon
    {
        public string Key;              // backbone key, e.g. "2482598"
        public string ScientificName;   // may include the authorship
        public string Authorship;
        public string Rank;             // KINGDOM ... SPECIES, SUBSPECIES, ...
        public string Status;           // ACCEPTED, SYNONYM, DOUBTFUL, ...
        public string Group;            // e.g. "Chordates" (suggest only)
        public string Context;          // e.g. the family (suggest only)
        public string ParentKey;
        public int ChildCount = -1;     // children service only

        public override string ToString() =>
            ScientificName + (string.IsNullOrEmpty(Rank) ? "" : " (" + Rank.ToLowerInvariant() + ")");
    }

    /// <summary>The answer to "which name did you mean?".</summary>
    internal sealed class GbifNameMatch
    {
        public GbifTaxon Taxon;         // null when nothing matched
        public string MatchType;        // EXACT, VARIANT, FUZZY, HIGHERRANK, NONE
        public int Confidence;          // 0-100
        public List<GbifTaxon> Classification = new List<GbifTaxon>();
    }

    /// <summary>One line of a "how many of each" answer.</summary>
    internal sealed class GbifFacetCount
    {
        public string Name;             // a key or a code, as GBIF returns it
        public long Count;
    }

    /// <summary>A photograph attached to a record, with its own terms.</summary>
    internal sealed class GbifMedia
    {
        public string Url;
        public string License;
        public string Creator;
        public string RightsHolder;
        public string References;       // the publisher's page for the image
    }

    /// <summary>One occurrence record from the search service.</summary>
    internal sealed class GbifOccurrence
    {
        public long Key;
        public double Lon, Lat;
        public Dictionary<string, string> Fields = new Dictionary<string, string>(StringComparer.Ordinal);
        public List<GbifMedia> Media = new List<GbifMedia>();
    }

    internal sealed class GbifSearchPage
    {
        public long Count;
        public bool EndOfRecords;
        public List<GbifOccurrence> Results = new List<GbifOccurrence>();
        public Dictionary<string, List<GbifFacetCount>> Facets =
            new Dictionary<string, List<GbifFacetCount>>(StringComparer.OrdinalIgnoreCase);
    }

    /// <summary>One download on the user's GBIF account.</summary>
    internal sealed class GbifDownload
    {
        public string Key;
        public string Doi;
        public string Status;           // PREPARING, RUNNING, SUCCEEDED, FAILED, CANCELLED, KILLED, FILE_ERASED, ...
        public string Format;           // SIMPLE_CSV, DWCA, SPECIES_LIST
        public string License;
        public string DownloadLink;
        public string PredicateJson;    // the filter that was requested
        public DateTime? Created;
        public DateTime? EraseAfter;
        public long Size;
        public long TotalRecords;
        public int NumberDatasets;

        public bool IsFinished => Status == "SUCCEEDED";
        public bool IsRunning => Status == "PREPARING" || Status == "RUNNING" || Status == "SUSPENDED";
    }

    /// <summary>A failure GBIF reported, with its own words.</summary>
    internal sealed class GbifException : Exception
    {
        public HttpStatusCode Status { get; }
        public GbifException(HttpStatusCode status, string message) : base(message) { Status = status; }
        public bool IsRateLimited => (int)Status == 429;
        public bool IsNotSignedIn => Status == HttpStatusCode.Unauthorized;
    }

    // ---------------------------------------------------------------------------
    /// <summary>
    /// Talks to GBIF's web services (api.gbif.org). Names come from the
    /// version 2 Taxon service against the GBIF backbone taxonomy; counts,
    /// species lists and record pages from the occurrence search service
    /// (sent as a POSTed predicate, so a detailed study-area polygon does
    /// not hit the web-address length limit); bulk data from the download
    /// service, which needs the user's free GBIF account.
    ///
    /// No ArcGIS types here: the class is compiled into the headless tests.
    /// </summary>
    internal sealed class GbifClient : IDisposable
    {
        internal const string Service = "GBIF";      // CredentialStore service name
        internal const string Backbone = "d7dddbf4-2cf0-4f39-9b2a-bb099caae36c";
        internal const string V1 = "https://api.gbif.org/v1/";
        internal const string V2 = "https://api.gbif.org/v2/";

        /// <summary>The search service returns at most this many records a page.</summary>
        internal const int MaxPageSize = 300;
        /// <summary>The search service will not page past this many records.</summary>
        internal const int MaxSearchRecords = 100000;
        /// <summary>GBIF's own advice: above about this many, request a download.</summary>
        internal const int RecommendDownloadAbove = 20000;

        private readonly HttpClient _http;
        private string _user, _password;

        /// <summary>How the window names itself to GBIF. GBIF's API reference
        /// asks for it: "we highly recommend you set the HTTP User-Agent to a
        /// URL or email address. We can then contact you if there is a
        /// problem." It must stay the same as USER_AGENT in
        /// gbif_occurrences.py (a test compares the two).</summary>
        internal const string UserAgent =
            "WildlifeTools-AddIn (Jenness Enterprises; ArcGIS Pro; https://www.jennessent.com)";

        internal GbifClient(string userAgent = UserAgent)
        {
            _http = new HttpClient { Timeout = TimeSpan.FromSeconds(100) };
            _http.DefaultRequestHeaders.UserAgent.ParseAdd(userAgent);
            _http.DefaultRequestHeaders.Accept.ParseAdd("application/json");
        }

        public void Dispose() => _http.Dispose();

        /// <summary>The account used for requests that need one.</summary>
        internal void SetAccount(string user, string password)
        {
            _user = user;
            _password = password;
        }

        internal string UserName => _user;
        internal bool HasAccount => !string.IsNullOrEmpty(_user);

        /// <summary>Loads the account from Windows Credential Manager.</summary>
        internal bool LoadStoredAccount()
        {
            if (CredentialStore.TryRead(Service, out string u, out string p))
            {
                SetAccount(u, p);
                return true;
            }
            return false;
        }

        // ---- names ------------------------------------------------------------

        /// <summary>Type-ahead: real names that start with what was typed.
        /// <paramref name="rank"/> (e.g. "FAMILY") narrows the list.</summary>
        internal async Task<List<GbifTaxon>> SuggestAsync(string typed, string rank = null,
            int limit = 12, CancellationToken ct = default)
        {
            var list = new List<GbifTaxon>();
            if (string.IsNullOrWhiteSpace(typed))
            {
                return list;
            }
            string url = V2 + "taxon/suggest/" + Backbone + "?q=" + Uri.EscapeDataString(typed.Trim())
                + "&limit=" + limit.ToString(CultureInfo.InvariantCulture)
                + (string.IsNullOrEmpty(rank) ? "" : "&taxonRank=" + Uri.EscapeDataString(rank));
            using (JsonDocument doc = await GetJsonAsync(url, false, ct).ConfigureAwait(false))
            {
                foreach (JsonElement e in Items(doc.RootElement))
                {
                    list.Add(ReadTaxon(e));
                }
            }
            return list;
        }

        /// <summary>Best match for a typed or pasted name, misspellings included.</summary>
        internal async Task<GbifNameMatch> MatchAsync(string name, CancellationToken ct = default)
        {
            var m = new GbifNameMatch { MatchType = "NONE" };
            if (string.IsNullOrWhiteSpace(name))
            {
                return m;
            }
            string url = V2 + "species/match?scientificName=" + Uri.EscapeDataString(name.Trim());
            using (JsonDocument doc = await GetJsonAsync(url, false, ct).ConfigureAwait(false))
            {
                JsonElement root = doc.RootElement;
                if (root.TryGetProperty("diagnostics", out JsonElement d))
                {
                    m.MatchType = Str(d, "matchType") ?? "NONE";
                    m.Confidence = (int)Num(d, "confidence");
                }
                if (root.TryGetProperty("usage", out JsonElement u) && u.ValueKind == JsonValueKind.Object)
                {
                    m.Taxon = new GbifTaxon
                    {
                        Key = Str(u, "key"),
                        ScientificName = Str(u, "canonicalName") ?? Str(u, "name"),
                        Authorship = Str(u, "authorship"),
                        Rank = Str(u, "rank"),
                        Status = Str(u, "status"),
                    };
                }
                if (root.TryGetProperty("classification", out JsonElement c) && c.ValueKind == JsonValueKind.Array)
                {
                    foreach (JsonElement e in c.EnumerateArray())
                    {
                        m.Classification.Add(new GbifTaxon
                        {
                            Key = Str(e, "key"),
                            ScientificName = Str(e, "name"),
                            Rank = Str(e, "rank"),
                        });
                    }
                }
            }
            return m;
        }

        /// <summary>The taxa directly inside a taxon (genera of a family,
        /// species of a genus, ...). Pass null for the kingdoms.</summary>
        internal async Task<List<GbifTaxon>> ChildrenAsync(string parentKey, int limit = 1000,
            CancellationToken ct = default)
        {
            var list = new List<GbifTaxon>();
            int offset = 0;
            while (list.Count < limit)
            {
                int page = Math.Min(100, limit - list.Count);
                string url = V2 + "taxon/tree/" + Backbone
                    + (string.IsNullOrEmpty(parentKey) ? "" : "/" + Uri.EscapeDataString(parentKey) + "/children")
                    + "?limit=" + page.ToString(CultureInfo.InvariantCulture)
                    + "&offset=" + offset.ToString(CultureInfo.InvariantCulture);
                bool end;
                int got = 0;
                using (JsonDocument doc = await GetJsonAsync(url, false, ct).ConfigureAwait(false))
                {
                    foreach (JsonElement e in Items(doc.RootElement))
                    {
                        list.Add(ReadTaxon(e));
                        got++;
                    }
                    end = doc.RootElement.ValueKind != JsonValueKind.Object
                        || !doc.RootElement.TryGetProperty("endOfRecords", out JsonElement eor)
                        || eor.ValueKind == JsonValueKind.True;
                }
                if (end || got == 0)
                {
                    break;
                }
                offset += got;
            }
            return list;
        }

        /// <summary>One taxon by key (used to name the keys in a species list).</summary>
        internal async Task<GbifTaxon> TaxonAsync(string key, CancellationToken ct = default)
        {
            string url = V2 + "taxon/" + Backbone + "/" + Uri.EscapeDataString(key);
            using (JsonDocument doc = await GetJsonAsync(url, false, ct).ConfigureAwait(false))
            {
                return ReadTaxon(doc.RootElement);
            }
        }

        /// <summary>GBIF's own common name for a taxon ("pronghorn"), or
        /// null when it has none. From the version 1 species service, whose
        /// keys are the backbone keys.</summary>
        internal async Task<string> CommonNameAsync(string key, CancellationToken ct = default)
        {
            using (JsonDocument doc = await GetJsonAsync(V1 + "species/" + Uri.EscapeDataString(key), false, ct)
                .ConfigureAwait(false))
            {
                string name = Str(doc.RootElement, "vernacularName");
                return string.IsNullOrWhiteSpace(name) ? null : name.Trim();
            }
        }

        /// <summary>What the suggestions table shows beside a name: GBIF's
        /// common name for the taxon, the class and kingdom it belongs to, and
        /// its name without the author (any of them null when GBIF has none).</summary>
        internal async Task<(string Common, string Class, string Kingdom, string Canonical)> TaxonSummaryAsync(string key,
            CancellationToken ct = default)
        {
            using (JsonDocument doc = await GetJsonAsync(V1 + "species/" + Uri.EscapeDataString(key), false, ct)
                .ConfigureAwait(false))
            {
                string name = Str(doc.RootElement, "vernacularName");
                return (string.IsNullOrWhiteSpace(name) ? null : name.Trim(),
                        Str(doc.RootElement, "class"), Str(doc.RootElement, "kingdom"),
                        Str(doc.RootElement, "canonicalName"));
            }
        }

        /// <summary>How many occurrence records GBIF holds of a taxon and
        /// everything inside it, worldwide, of every kind.</summary>
        internal async Task<long> TaxonRecordCountAsync(string key, CancellationToken ct = default)
        {
            using (JsonDocument doc = await GetJsonAsync(
                V1 + "occurrence/search?limit=0&taxonKey=" + Uri.EscapeDataString(key), false, ct).ConfigureAwait(false))
            {
                return doc.RootElement.TryGetProperty("count", out JsonElement c) && c.ValueKind == JsonValueKind.Number
                    && c.TryGetInt64(out long n) ? n : -1;
            }
        }

        /// <summary>Taxa found by a common name ("steller's jay"). Uses the
        /// version 1 species search, the only GBIF service that searches
        /// common names; accepted species come first.</summary>
        internal async Task<List<GbifTaxon>> SearchCommonNameAsync(string typed, int limit = 20,
            CancellationToken ct = default)
        {
            var list = new List<GbifTaxon>();
            if (string.IsNullOrWhiteSpace(typed))
            {
                return list;
            }
            string url = V1 + "species/search?qField=VERNACULAR&datasetKey=" + Backbone
                + "&q=" + Uri.EscapeDataString(typed.Trim())
                + "&limit=" + limit.ToString(CultureInfo.InvariantCulture);
            using (JsonDocument doc = await GetJsonAsync(url, false, ct).ConfigureAwait(false))
            {
                foreach (JsonElement e in Items(doc.RootElement))
                {
                    list.Add(new GbifTaxon
                    {
                        Key = Str(e, "key"),
                        ScientificName = Str(e, "canonicalName") ?? Str(e, "scientificName"),
                        Authorship = Str(e, "authorship"),
                        Rank = Str(e, "rank"),
                        Status = Str(e, "taxonomicStatus"),
                        Context = Str(e, "family"),
                        ParentKey = Str(e, "parentKey"),
                    });
                }
            }
            list.Sort((a, b) => Order(a).CompareTo(Order(b)));
            return list;

            static int Order(GbifTaxon t) =>
                (t.Status == "ACCEPTED" ? 0 : 2) + (t.Rank == "SPECIES" ? 0 : 1);
        }

        // ---- counts, species lists and record pages ----------------------------

        /// <summary>How many records match. Free and quick: no records are fetched.</summary>
        internal async Task<long> CountAsync(GbifFilter filter, CancellationToken ct = default)
        {
            GbifSearchPage p = await SearchAsync(filter, 0, 0, null, 0, 0, ct).ConfigureAwait(false);
            return p.Count;
        }

        /// <summary>"How many of each": record counts grouped by a field,
        /// e.g. SPECIES_KEY (the species list for the area), DATASET_KEY,
        /// LICENSE, BASIS_OF_RECORD, YEAR. Largest first.</summary>
        internal async Task<List<GbifFacetCount>> FacetAsync(GbifFilter filter, string field,
            int limit = 300, int offset = 0, CancellationToken ct = default)
        {
            GbifSearchPage p = await SearchAsync(filter, 0, 0, field, limit, offset, ct).ConfigureAwait(false);
            return p.Facets.TryGetValue(field, out List<GbifFacetCount> list)
                ? list : new List<GbifFacetCount>();
        }

        /// <summary>One page of records (at most 300; offset + limit may not
        /// pass 100,000).</summary>
        internal Task<GbifSearchPage> SearchPageAsync(GbifFilter filter, int limit, int offset,
            CancellationToken ct = default) =>
            SearchAsync(filter, Math.Min(limit, MaxPageSize), offset, null, 0, 0, ct);

        private async Task<GbifSearchPage> SearchAsync(GbifFilter filter, int limit, int offset,
            string facet, int facetLimit, int facetOffset, CancellationToken ct)
        {
            string body;
            using (var ms = new MemoryStream())
            {
                using (var w = new Utf8JsonWriter(ms))
                {
                    w.WriteStartObject();
                    w.WritePropertyName("predicate");
                    if (filter == null || !filter.WritePredicate(w))
                    {
                        w.WriteNullValue();
                    }
                    w.WriteNumber("limit", limit);
                    w.WriteNumber("offset", offset);
                    if (!string.IsNullOrEmpty(facet))
                    {
                        w.WriteStartArray("facets");
                        w.WriteStringValue(facet);
                        w.WriteEndArray();
                        w.WriteNumber("facetLimit", facetLimit);
                        w.WriteNumber("facetOffset", facetOffset);
                    }
                    w.WriteEndObject();
                }
                body = Encoding.UTF8.GetString(ms.ToArray());
            }

            var page = new GbifSearchPage();
            using (JsonDocument doc = await PostJsonAsync(V1 + "occurrence/search/predicate", body, false, ct)
                .ConfigureAwait(false))
            {
                JsonElement root = doc.RootElement;
                page.Count = (long)Num(root, "count");
                page.EndOfRecords = root.TryGetProperty("endOfRecords", out JsonElement eor)
                    && eor.ValueKind == JsonValueKind.True;
                foreach (JsonElement e in Items(root))
                {
                    page.Results.Add(ReadOccurrence(e, KeepFields));
                }
                if (root.TryGetProperty("facets", out JsonElement fs) && fs.ValueKind == JsonValueKind.Array)
                {
                    foreach (JsonElement f in fs.EnumerateArray())
                    {
                        var counts = new List<GbifFacetCount>();
                        if (f.TryGetProperty("counts", out JsonElement cs) && cs.ValueKind == JsonValueKind.Array)
                        {
                            foreach (JsonElement c in cs.EnumerateArray())
                            {
                                counts.Add(new GbifFacetCount { Name = Str(c, "name"), Count = (long)Num(c, "count") });
                            }
                        }
                        page.Facets[Str(f, "field") ?? ""] = counts;
                    }
                }
            }
            return page;
        }

        // ---- downloads (need the account) --------------------------------------

        /// <summary>True when GBIF accepts the account. Throws only on a
        /// network failure.</summary>
        internal async Task<bool> CheckAccountAsync(CancellationToken ct = default)
        {
            if (!HasAccount)
            {
                return false;
            }
            try
            {
                using (await GetJsonAsync(V1 + "occurrence/download/user/" + Uri.EscapeDataString(_user)
                    + "?limit=0", true, ct).ConfigureAwait(false))
                {
                    return true;
                }
            }
            catch (GbifException ex) when (ex.IsNotSignedIn || ex.Status == HttpStatusCode.Forbidden)
            {
                return false;
            }
        }

        /// <summary>The state of one download (no account needed).</summary>
        internal async Task<GbifDownload> DownloadStatusAsync(string key, CancellationToken ct = default)
        {
            using (JsonDocument doc = await GetJsonAsync(V1 + "occurrence/download/" + Uri.EscapeDataString(key),
                false, ct).ConfigureAwait(false))
            {
                return ReadDownload(doc.RootElement);
            }
        }

        /// <summary>The downloads on the account, newest first.</summary>
        internal async Task<List<GbifDownload>> ListDownloadsAsync(int limit = 50, int offset = 0,
            CancellationToken ct = default)
        {
            RequireAccount();
            var list = new List<GbifDownload>();
            string url = V1 + "occurrence/download/user/" + Uri.EscapeDataString(_user)
                + "?limit=" + limit.ToString(CultureInfo.InvariantCulture)
                + "&offset=" + offset.ToString(CultureInfo.InvariantCulture);
            using (JsonDocument doc = await GetJsonAsync(url, true, ct).ConfigureAwait(false))
            {
                foreach (JsonElement e in Items(doc.RootElement))
                {
                    list.Add(ReadDownload(e));
                }
            }
            return list;
        }

        /// <summary>Cancels a download that is still being prepared.</summary>
        internal async Task CancelDownloadAsync(string key, CancellationToken ct = default)
        {
            RequireAccount();
            using (var req = new HttpRequestMessage(HttpMethod.Delete,
                V1 + "occurrence/download/request/" + Uri.EscapeDataString(key)))
            {
                AddAuth(req);
                using (HttpResponseMessage resp = await _http.SendAsync(req, ct).ConfigureAwait(false))
                {
                    if (!resp.IsSuccessStatusCode)
                    {
                        string text = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
                        throw new GbifException(resp.StatusCode, Explain(resp.StatusCode, text));
                    }
                }
            }
        }

        /// <summary>GBIF's own citation text for a download, with its DOI.</summary>
        internal async Task<string> CitationAsync(string key, CancellationToken ct = default)
        {
            using (var req = new HttpRequestMessage(HttpMethod.Get,
                V1 + "occurrence/download/" + Uri.EscapeDataString(key) + "/citation"))
            {
                req.Headers.Accept.Clear();
                req.Headers.Accept.ParseAdd("text/plain");
                req.Headers.Accept.ParseAdd("*/*");
                using (HttpResponseMessage resp = await _http.SendAsync(req, ct).ConfigureAwait(false))
                {
                    string text = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
                    return resp.IsSuccessStatusCode ? text.Trim() : null;
                }
            }
        }

        /// <summary>The title of a dataset, for a "where the records came
        /// from" table.</summary>
        internal async Task<string> DatasetTitleAsync(string datasetKey, CancellationToken ct = default)
        {
            using (JsonDocument doc = await GetJsonAsync(V1 + "dataset/" + Uri.EscapeDataString(datasetKey),
                false, ct).ConfigureAwait(false))
            {
                return Str(doc.RootElement, "title");
            }
        }

        /// <summary>The title, licence and kind of a dataset (a source).</summary>
        internal async Task<(string Title, string License, string Type)> DatasetInfoAsync(string datasetKey,
            CancellationToken ct = default)
        {
            using (JsonDocument doc = await GetJsonAsync(V1 + "dataset/" + Uri.EscapeDataString(datasetKey),
                false, ct).ConfigureAwait(false))
            {
                return (Str(doc.RootElement, "title"), Str(doc.RootElement, "license"), Str(doc.RootElement, "type"));
            }
        }

        /// <summary>GBIF's list of countries and areas: two-letter code and name.</summary>
        internal async Task<List<(string Code, string Name)>> CountriesAsync(CancellationToken ct = default)
        {
            var list = new List<(string Code, string Name)>();
            using (JsonDocument doc = await GetJsonAsync(V1 + "enumeration/country", false, ct).ConfigureAwait(false))
            {
                foreach (JsonElement e in Items(doc.RootElement))
                {
                    string code = Str(e, "iso2"), name = Str(e, "title");
                    if (!string.IsNullOrEmpty(code) && !string.IsNullOrEmpty(name))
                    {
                        list.Add((code, name));
                    }
                }
            }
            list.Sort((a, b) => string.Compare(a.Name, b.Name, StringComparison.OrdinalIgnoreCase));
            return list;
        }

        /// <summary>The datasets that contributed to a download: key and title.</summary>
        internal async Task<Dictionary<string, string>> DownloadDatasetsAsync(string downloadKey,
            CancellationToken ct = default)
        {
            var titles = new Dictionary<string, string>(StringComparer.Ordinal);
            int offset = 0;
            while (offset < 20000)
            {
                string url = V1 + "occurrence/download/" + Uri.EscapeDataString(downloadKey) + "/datasets?limit=100&offset="
                    + offset.ToString(CultureInfo.InvariantCulture);
                int got = 0;
                bool end;
                using (JsonDocument doc = await GetJsonAsync(url, false, ct).ConfigureAwait(false))
                {
                    foreach (JsonElement e in Items(doc.RootElement))
                    {
                        got++;
                        string key = Str(e, "datasetKey"), title = Str(e, "datasetTitle");
                        if (!string.IsNullOrEmpty(key) && !string.IsNullOrEmpty(title))
                        {
                            titles[key] = title;
                        }
                    }
                    end = !doc.RootElement.TryGetProperty("endOfRecords", out JsonElement eor) || eor.ValueKind == JsonValueKind.True;
                }
                if (end || got == 0)
                {
                    break;
                }
                offset += got;
            }
            return titles;
        }

        // ---- photographs ---------------------------------------------------------

        /// <summary>The address of GBIF's own small copy of a record's
        /// photograph (its image cache), <paramref name="width"/> pixels
        /// wide, so a gallery never has to fetch the full-size original.</summary>
        internal static string ThumbnailUrl(long occurrenceKey, string mediaUrl, int width = 200)
        {
            byte[] hash = System.Security.Cryptography.MD5.HashData(Encoding.UTF8.GetBytes(mediaUrl ?? ""));
            return V1 + "image/cache/" + width.ToString(CultureInfo.InvariantCulture) + "x/occurrence/"
                + occurrenceKey.ToString(CultureInfo.InvariantCulture) + "/media/"
                + Convert.ToHexString(hash).ToLowerInvariant();
        }

        /// <summary>Fetches a small file (a thumbnail) into memory.</summary>
        internal async Task<byte[]> GetBytesAsync(string url, CancellationToken ct = default)
        {
            using (var req = new HttpRequestMessage(HttpMethod.Get, url))
            {
                req.Headers.Accept.Clear();
                req.Headers.Accept.ParseAdd("image/*");
                using (HttpResponseMessage resp = await _http.SendAsync(req, ct).ConfigureAwait(false))
                {
                    if (!resp.IsSuccessStatusCode)
                    {
                        throw new GbifException(resp.StatusCode, Explain(resp.StatusCode, ""));
                    }
                    return await resp.Content.ReadAsByteArrayAsync(ct).ConfigureAwait(false);
                }
            }
        }

        /// <summary>When set, a search record keeps only these fields (plus
        /// its coordinates and photographs): a 100,000-record fetch then
        /// stays small in memory.</summary>
        internal HashSet<string> KeepFields { get; set; }

        // ---- plumbing -----------------------------------------------------------

        private void RequireAccount()
        {
            if (!HasAccount)
            {
                throw new GbifException(HttpStatusCode.Unauthorized,
                    "This needs a GBIF account. Sign in first.");
            }
        }

        private void AddAuth(HttpRequestMessage req)
        {
            string token = Convert.ToBase64String(Encoding.UTF8.GetBytes(_user + ":" + _password));
            req.Headers.Authorization = new AuthenticationHeaderValue("Basic", token);
        }

        private async Task<JsonDocument> GetJsonAsync(string url, bool auth, CancellationToken ct)
        {
            using (var req = new HttpRequestMessage(HttpMethod.Get, url))
            {
                if (auth)
                {
                    AddAuth(req);
                }
                return await SendJsonAsync(req, ct).ConfigureAwait(false);
            }
        }

        private async Task<JsonDocument> PostJsonAsync(string url, string body, bool auth, CancellationToken ct)
        {
            using (var req = new HttpRequestMessage(HttpMethod.Post, url))
            {
                req.Content = new StringContent(body, Encoding.UTF8, "application/json");
                if (auth)
                {
                    AddAuth(req);
                }
                return await SendJsonAsync(req, ct).ConfigureAwait(false);
            }
        }

        private async Task<JsonDocument> SendJsonAsync(HttpRequestMessage req, CancellationToken ct)
        {
            using (HttpResponseMessage resp = await _http.SendAsync(req, ct).ConfigureAwait(false))
            {
                string text = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
                if (!resp.IsSuccessStatusCode)
                {
                    throw new GbifException(resp.StatusCode, Explain(resp.StatusCode, text));
                }
                try
                {
                    return JsonDocument.Parse(string.IsNullOrWhiteSpace(text) ? "null" : text);
                }
                catch (JsonException)
                {
                    throw new GbifException(resp.StatusCode,
                        "GBIF returned something that is not the expected data.");
                }
            }
        }

        /// <summary>GBIF's error text, shortened, with a plain lead-in for
        /// the cases a user can do something about.</summary>
        internal static string Explain(HttpStatusCode status, string body)
        {
            string detail = (body ?? "").Trim();
            if (detail.StartsWith("<", StringComparison.Ordinal))
            {
                detail = "";                              // an HTML error page
            }
            if (detail.Length > 400)
            {
                detail = detail.Substring(0, 400) + "...";
            }
            string lead;
            switch ((int)status)
            {
                case 401: lead = "GBIF did not accept the user name and password."; break;
                case 413: lead = "GBIF refused the request as too large."; break;
                case 420:
                case 429: lead = "GBIF is limiting requests from this computer or this account. Wait a minute and try again, or request a download instead."; break;
                case 503: lead = "GBIF is busy or unavailable right now. Try again in a few minutes."; break;
                default: lead = "GBIF returned an error (" + ((int)status).ToString(CultureInfo.InvariantCulture) + ")."; break;
            }
            return detail.Length == 0 ? lead : lead + " " + detail;
        }

        // ---- readers ------------------------------------------------------------

        /// <summary>The list in a response: the array itself, or its "results".</summary>
        private static IEnumerable<JsonElement> Items(JsonElement root)
        {
            if (root.ValueKind == JsonValueKind.Array)
            {
                foreach (JsonElement e in root.EnumerateArray())
                {
                    yield return e;
                }
            }
            else if (root.ValueKind == JsonValueKind.Object
                && root.TryGetProperty("results", out JsonElement r) && r.ValueKind == JsonValueKind.Array)
            {
                foreach (JsonElement e in r.EnumerateArray())
                {
                    yield return e;
                }
            }
        }

        private static GbifTaxon ReadTaxon(JsonElement e)
        {
            var t = new GbifTaxon
            {
                Key = Str(e, "taxonID") ?? Str(e, "key"),
                ScientificName = Str(e, "scientificName") ?? Str(e, "name"),
                Authorship = Str(e, "scientificNameAuthorship"),
                Rank = Str(e, "taxonRank") ?? Str(e, "rank"),
                Status = Str(e, "taxonomicStatus") ?? Str(e, "status"),
                Group = Str(e, "group") ?? Str(e, "taxonomicGroup"),
                Context = Str(e, "context"),
                ParentKey = Str(e, "parentNameUsageID"),
            };
            if (e.ValueKind == JsonValueKind.Object && e.TryGetProperty("children", out JsonElement c)
                && c.ValueKind == JsonValueKind.Number)
            {
                t.ChildCount = c.GetInt32();
            }
            return t;
        }

        private static GbifOccurrence ReadOccurrence(JsonElement e, HashSet<string> keep)
        {
            var o = new GbifOccurrence
            {
                Key = (long)Num(e, "key"),
                Lon = Num(e, "decimalLongitude"),
                Lat = Num(e, "decimalLatitude"),
            };
            foreach (JsonProperty p in e.EnumerateObject())
            {
                if (keep != null && p.Name != "issues" && p.Name != "media" && !keep.Contains(p.Name))
                {
                    continue;
                }
                switch (p.Value.ValueKind)
                {
                    case JsonValueKind.String:
                        o.Fields[p.Name] = p.Value.GetString();
                        break;
                    case JsonValueKind.Number:
                        o.Fields[p.Name] = p.Value.GetRawText();
                        break;
                    case JsonValueKind.True:
                    case JsonValueKind.False:
                        o.Fields[p.Name] = p.Value.GetBoolean() ? "true" : "false";
                        break;
                    case JsonValueKind.Array:
                        if (p.Name == "issues")
                        {
                            var parts = new List<string>();
                            foreach (JsonElement i in p.Value.EnumerateArray())
                            {
                                if (i.ValueKind == JsonValueKind.String)
                                {
                                    parts.Add(i.GetString());
                                }
                            }
                            o.Fields["issue"] = string.Join(";", parts);
                        }
                        else if (p.Name == "media")
                        {
                            foreach (JsonElement m in p.Value.EnumerateArray())
                            {
                                string type = Str(m, "type");
                                string url = Str(m, "identifier");
                                if (url != null && (type == null || type == "StillImage"))
                                {
                                    o.Media.Add(new GbifMedia
                                    {
                                        Url = url,
                                        License = Str(m, "license"),
                                        Creator = Str(m, "creator"),
                                        RightsHolder = Str(m, "rightsHolder"),
                                        References = Str(m, "references"),
                                    });
                                }
                            }
                        }
                        break;
                }
            }
            return o;
        }

        private static GbifDownload ReadDownload(JsonElement e)
        {
            var d = new GbifDownload
            {
                Key = Str(e, "key"),
                Doi = Str(e, "doi"),
                Status = Str(e, "status"),
                License = Str(e, "license"),
                DownloadLink = Str(e, "downloadLink"),
                Created = Date(e, "created"),
                EraseAfter = Date(e, "eraseAfter"),
                Size = (long)Num(e, "size"),
                TotalRecords = (long)Num(e, "totalRecords"),
                NumberDatasets = (int)Num(e, "numberDatasets"),
            };
            if (e.TryGetProperty("request", out JsonElement r) && r.ValueKind == JsonValueKind.Object)
            {
                d.Format = Str(r, "format");
                if (r.TryGetProperty("predicate", out JsonElement p) && p.ValueKind == JsonValueKind.Object)
                {
                    d.PredicateJson = p.GetRawText();
                }
            }
            return d;
        }

        private static string Str(JsonElement e, string name)
        {
            if (e.ValueKind != JsonValueKind.Object || !e.TryGetProperty(name, out JsonElement v))
            {
                return null;
            }
            switch (v.ValueKind)
            {
                case JsonValueKind.String: return v.GetString();
                case JsonValueKind.Number: return v.GetRawText();
                default: return null;
            }
        }

        private static double Num(JsonElement e, string name)
        {
            if (e.ValueKind != JsonValueKind.Object || !e.TryGetProperty(name, out JsonElement v))
            {
                return 0;
            }
            if (v.ValueKind == JsonValueKind.Number)
            {
                return v.GetDouble();
            }
            return v.ValueKind == JsonValueKind.String
                && double.TryParse(v.GetString(), NumberStyles.Float, CultureInfo.InvariantCulture, out double d)
                ? d : 0;
        }

        private static DateTime? Date(JsonElement e, string name)
        {
            string s = Str(e, name);
            return s != null && DateTime.TryParse(s, CultureInfo.InvariantCulture,
                DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out DateTime d)
                ? d : (DateTime?)null;
        }
    }
}
