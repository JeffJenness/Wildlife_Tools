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
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace WildlifeTools.OnlineData.INaturalist
{
    /// <summary>A species or group on iNaturalist.</summary>
    internal sealed class INatTaxon
    {
        public long Id;
        public string Name, Common, Rank, Group;
        public long Observations = -1;
        /// <summary>The form the geoprocessing tool reads: the id is used, the name is a label.</summary>
        public string Entry => Name + " (" + Id.ToString(CultureInfo.InvariantCulture) + ")";
    }

    /// <summary>One of iNaturalist's named places.</summary>
    internal sealed class INatPlace
    {
        public long Id;
        public string Name, DisplayName;
        public int? AdminLevel;
        public string Entry => DisplayName + " (" + Id.ToString(CultureInfo.InvariantCulture) + ")";
        public override string ToString() => DisplayName
            + (AdminLevel == 0 ? "   (country)" : AdminLevel == 10 ? "   (state or province)"
               : AdminLevel == 20 ? "   (county)" : AdminLevel == 30 ? "   (town)" : "");
    }

    /// <summary>
    /// What the window asks of iNaturalist. The twin of the Query class in
    /// inaturalist_observations.py: <see cref="Params"/> must give the same
    /// request the geoprocessing tool will make, so that the count the
    /// window shows is the count the tool finds.
    /// </summary>
    internal sealed class INatQuery
    {
        public List<long> TaxonIds = new List<long>();
        public long? PlaceId;
        public (double West, double South, double East, double North)? Box;
        public bool Research = true, NeedsId, Casual;
        public bool ExcludeCaptive = true;
        public string DateFrom, DateTo;                 // yyyy-MM-dd
        public List<int> Months = new List<int>();
        public bool OpenLicenseOnly, ExcludeNonCommercial, PhotosOnly, ExcludeObscured;
        public double? MaxUncertaintyMeters;
        public string Establishment = "Any";            // Any, Native, Introduced
        public string Observer, Project;

        internal static readonly string[] OpenLicenses =
            { "cc0", "cc-by", "cc-by-nc", "cc-by-sa", "cc-by-nd", "cc-by-nc-sa", "cc-by-nc-nd" };
        internal static readonly string[] CommercialLicenses = { "cc0", "cc-by", "cc-by-sa", "cc-by-nd" };

        /// <summary>Six decimals of a degree, without trailing zeros.</summary>
        internal static string Num(double v)
        {
            string s = Math.Round(v, 6).ToString("0.######", CultureInfo.InvariantCulture);
            return s == "-0" ? "0" : s;
        }

        public bool HasScope => TaxonIds.Count > 0 || PlaceId.HasValue || Box.HasValue
            || !string.IsNullOrWhiteSpace(Observer) || !string.IsNullOrWhiteSpace(Project);

        public List<KeyValuePair<string, string>> Params()
        {
            var q = new List<KeyValuePair<string, string>>();
            void Add(string k, string v) => q.Add(new KeyValuePair<string, string>(k, v));
            CultureInfo inv = CultureInfo.InvariantCulture;
            if (TaxonIds.Count > 0)
            {
                Add("taxon_id", string.Join(",", TaxonIds.Select(i => i.ToString(inv))));
            }
            if (PlaceId.HasValue)
            {
                Add("place_id", PlaceId.Value.ToString(inv));
            }
            if (Box.HasValue)
            {
                Add("swlng", Num(Box.Value.West));
                Add("swlat", Num(Box.Value.South));
                Add("nelng", Num(Box.Value.East));
                Add("nelat", Num(Box.Value.North));
            }
            var grades = new List<string>();
            if (Research) grades.Add("research");
            if (NeedsId) grades.Add("needs_id");
            if (Casual) grades.Add("casual");
            if (grades.Count == 0)
            {
                grades.Add("research");
            }
            if (grades.Count < 3)
            {
                Add("quality_grade", string.Join(",", grades));
            }
            if (ExcludeCaptive && grades.Contains("casual"))
            {
                Add("captive", "false");
            }
            if (!string.IsNullOrEmpty(DateFrom)) Add("d1", DateFrom);
            if (!string.IsNullOrEmpty(DateTo)) Add("d2", DateTo);
            List<int> months = Months.Where(m => m >= 1 && m <= 12).Distinct().OrderBy(m => m).ToList();
            if (months.Count > 0 && months.Count < 12)
            {
                Add("month", string.Join(",", months.Select(m => m.ToString(inv))));
            }
            if (ExcludeNonCommercial)
            {
                Add("license", string.Join(",", CommercialLicenses));
            }
            else if (OpenLicenseOnly)
            {
                Add("license", string.Join(",", OpenLicenses));
            }
            if (PhotosOnly) Add("photos", "true");
            if (MaxUncertaintyMeters.HasValue) Add("acc_below", Num(MaxUncertaintyMeters.Value));
            if (ExcludeObscured)
            {
                Add("geoprivacy", "open");
                Add("taxon_geoprivacy", "open");
            }
            if (Establishment == "Native") Add("native", "true");
            else if (Establishment == "Introduced") Add("introduced", "true");
            if (!string.IsNullOrWhiteSpace(Observer)) Add("user_id", Observer.Trim());
            if (!string.IsNullOrWhiteSpace(Project)) Add("project_id", Project.Trim());
            return q;
        }

        internal static string QueryString(IEnumerable<KeyValuePair<string, string>> q) =>
            string.Join("&", q.Select(p => Uri.EscapeDataString(p.Key) + "=" + Uri.EscapeDataString(p.Value)));
    }

    /// <summary>
    /// Asks iNaturalist's service (api.inaturalist.org, no account) the
    /// small questions the window needs: names, places, counts and the
    /// species of an area. Fetching the observations is the geoprocessing
    /// tool's job. Never more than one request a second. No ArcGIS types.
    /// </summary>
    internal sealed class INatPhoto
    {
        public long ObservationId;
        public string ObservedOn;
        public string Url;              // iNaturalist's 75-pixel square; see Sized
        public string Attribution;      // "(c) name, some rights reserved (CC BY-NC)"
        public string License;          // cc-by-nc, ... or empty for all rights reserved
        public string Observer;

        /// <summary>The address of another size of the same picture:
        /// small (240 pixels), medium (500), large (1024).</summary>
        public string Sized(string size) => (Url ?? "").Replace("/square.", "/" + size + ".");

        public string LicenseWords => string.IsNullOrEmpty(License) ? "all rights reserved"
            : License.ToLowerInvariant() == "cc0" ? "CC0" : License.ToUpperInvariant().Replace("CC-", "CC ");
    }

    internal sealed class INatClient : IDisposable
    {
        internal const string Api = "https://api.inaturalist.org/v1/";
        internal const string Api2 = "https://api.inaturalist.org/v2/";
        internal const string Site = "https://www.inaturalist.org/";

        private readonly HttpClient _http;
        private readonly SemaphoreSlim _gate = new SemaphoreSlim(1, 1);
        private DateTime _last = DateTime.MinValue;

        internal INatClient()
        {
            _http = new HttpClient { Timeout = TimeSpan.FromSeconds(100) };
            _http.DefaultRequestHeaders.UserAgent.ParseAdd("WildlifeTools-AddIn (Jenness Enterprises; ArcGIS Pro)");
            _http.DefaultRequestHeaders.Accept.ParseAdd("application/json");
        }

        public void Dispose() => _http.Dispose();

        private async Task<JsonDocument> GetAsync(string url, CancellationToken ct)
        {
            await _gate.WaitAsync(ct).ConfigureAwait(false);
            try
            {
                TimeSpan wait = TimeSpan.FromSeconds(1) - (DateTime.UtcNow - _last);
                if (wait > TimeSpan.Zero)
                {
                    await Task.Delay(wait, ct).ConfigureAwait(false);
                }
                _last = DateTime.UtcNow;
                using (HttpResponseMessage resp = await _http.GetAsync(url, ct).ConfigureAwait(false))
                {
                    string body = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
                    if (!resp.IsSuccessStatusCode)
                    {
                        throw new INatException(Explain((int)resp.StatusCode, body), (int)resp.StatusCode);
                    }
                    try
                    {
                        return JsonDocument.Parse(body);
                    }
                    catch (JsonException)
                    {
                        throw new INatException("iNaturalist returned something that is not the expected data.", 0);
                    }
                }
            }
            finally
            {
                _gate.Release();
            }
        }

        internal static string Explain(int status, string body)
        {
            string detail = "";
            try
            {
                using (JsonDocument d = JsonDocument.Parse(body ?? ""))
                {
                    if (d.RootElement.ValueKind == JsonValueKind.Object)
                    {
                        if (d.RootElement.TryGetProperty("errors", out JsonElement errs) && errs.ValueKind == JsonValueKind.Array)
                        {
                            detail = string.Join("; ", errs.EnumerateArray()
                                .Select(e => e.ValueKind == JsonValueKind.Object && e.TryGetProperty("message", out JsonElement m)
                                    ? m.ToString() : "").Where(s => s.Length > 0));
                        }
                        else if (d.RootElement.TryGetProperty("error", out JsonElement er))
                        {
                            detail = er.ToString();
                        }
                    }
                }
            }
            catch (JsonException)
            {
            }
            if (detail.Length > 300)
            {
                detail = detail.Substring(0, 300) + "...";
            }
            string lead = status == 429 ? "iNaturalist is limiting requests from this computer. Wait a few minutes and try again."
                : status >= 502 && status <= 504 ? "iNaturalist is busy or unavailable right now. Try again in a few minutes."
                : "iNaturalist returned an error (" + status.ToString(CultureInfo.InvariantCulture) + ").";
            return detail.Length == 0 ? lead : lead + " " + detail;
        }

        private static string Str(JsonElement o, string name) =>
            o.TryGetProperty(name, out JsonElement v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;

        private static long Long(JsonElement o, string name, long none = -1) =>
            o.TryGetProperty(name, out JsonElement v) && v.ValueKind == JsonValueKind.Number && v.TryGetInt64(out long n) ? n : none;

        private static INatTaxon ReadTaxon(JsonElement t) => new INatTaxon
        {
            Id = Long(t, "id"),
            Name = Str(t, "name") ?? "",
            Common = Str(t, "preferred_common_name") ?? "",
            Rank = Str(t, "rank") ?? "",
            Group = Str(t, "iconic_taxon_name") ?? "",
            Observations = Long(t, "observations_count"),
        };

        /// <summary>Species and groups whose scientific or common name
        /// matches, best match first.</summary>
        internal async Task<List<INatTaxon>> SearchTaxaAsync(string text, CancellationToken ct)
        {
            var list = new List<INatTaxon>();
            string url = Api + "taxa?is_active=true&per_page=30&q=" + Uri.EscapeDataString(text.Trim());
            using (JsonDocument doc = await GetAsync(url, ct).ConfigureAwait(false))
            {
                if (doc.RootElement.TryGetProperty("results", out JsonElement res))
                {
                    foreach (JsonElement t in res.EnumerateArray())
                    {
                        list.Add(ReadTaxon(t));
                    }
                }
            }
            return list;
        }

        /// <summary>The id of "Life", the root of iNaturalist's taxonomy: its children are the kingdoms.</summary>
        internal const long RootTaxon = 48460;

        /// <summary>The taxa directly inside a taxon, most observed first
        /// (for browsing the taxonomy). At most 1,000 are read, 200 a
        /// request; Total says how many there are.</summary>
        internal async Task<(List<INatTaxon> Children, long Total)> ChildrenAsync(long parentId, CancellationToken ct)
        {
            var list = new List<INatTaxon>();
            long total = 0;
            for (int page = 1; page <= 5; page++)
            {
                string url = Api + "taxa?is_active=true&per_page=200&parent_id=" + parentId.ToString(CultureInfo.InvariantCulture)
                    + "&page=" + page.ToString(CultureInfo.InvariantCulture);
                int got = 0;
                using (JsonDocument doc = await GetAsync(url, ct).ConfigureAwait(false))
                {
                    total = Long(doc.RootElement, "total_results", 0);
                    if (doc.RootElement.TryGetProperty("results", out JsonElement res))
                    {
                        foreach (JsonElement t in res.EnumerateArray())
                        {
                            list.Add(ReadTaxon(t));
                            got++;
                        }
                    }
                }
                if (got == 0 || list.Count >= total)
                {
                    break;
                }
            }
            return (list, total);
        }

        /// <summary>iNaturalist's places whose name starts with the text.</summary>
        internal async Task<List<INatPlace>> SearchPlacesAsync(string text, CancellationToken ct)
        {
            var list = new List<INatPlace>();
            string url = Api + "places/autocomplete?per_page=20&q=" + Uri.EscapeDataString(text.Trim());
            using (JsonDocument doc = await GetAsync(url, ct).ConfigureAwait(false))
            {
                if (doc.RootElement.TryGetProperty("results", out JsonElement res))
                {
                    foreach (JsonElement p in res.EnumerateArray())
                    {
                        long level = Long(p, "admin_level", long.MinValue);
                        list.Add(new INatPlace
                        {
                            Id = Long(p, "id"),
                            Name = Str(p, "name") ?? "",
                            DisplayName = Str(p, "display_name") ?? Str(p, "name") ?? "",
                            AdminLevel = level == long.MinValue ? (int?)null : (int)level,
                        });
                    }
                }
            }
            return list;
        }

        /// <summary>Photographs from the matching observations of one
        /// taxon, a few at a time, newest observation first: the first
        /// photograph of each observation, with its credit and license.
        /// Total is the number of matching observations of the taxon
        /// that have a photograph.</summary>
        internal async Task<(List<INatPhoto> Photos, long Total)> PhotosAsync(INatQuery q, long taxonId, int page,
            int perPage, CancellationToken ct)
        {
            CultureInfo inv = CultureInfo.InvariantCulture;
            List<KeyValuePair<string, string>> p = q.Params()
                .Where(kv => kv.Key != "taxon_id" && kv.Key != "photos").ToList();
            p.Add(new KeyValuePair<string, string>("taxon_id", taxonId.ToString(inv)));
            p.Add(new KeyValuePair<string, string>("photos", "true"));
            p.Add(new KeyValuePair<string, string>("per_page", perPage.ToString(inv)));
            p.Add(new KeyValuePair<string, string>("page", page.ToString(inv)));
            p.Add(new KeyValuePair<string, string>("order_by", "observed_on"));
            p.Add(new KeyValuePair<string, string>("order", "desc"));
            string url = Api2 + "observations?" + INatQuery.QueryString(p)
                + "&fields=(id:!t,observed_on:!t,user:(login:!t,name:!t),photos:(url:!t,attribution:!t,license_code:!t))";
            var list = new List<INatPhoto>();
            long total = 0;
            using (JsonDocument doc = await GetAsync(url, ct).ConfigureAwait(false))
            {
                total = Long(doc.RootElement, "total_results", 0);
                if (doc.RootElement.TryGetProperty("results", out JsonElement res))
                {
                    foreach (JsonElement o in res.EnumerateArray())
                    {
                        if (!o.TryGetProperty("photos", out JsonElement photos) || photos.ValueKind != JsonValueKind.Array
                            || photos.GetArrayLength() == 0)
                        {
                            continue;
                        }
                        JsonElement ph = photos[0];
                        string who = null;
                        if (o.TryGetProperty("user", out JsonElement u) && u.ValueKind == JsonValueKind.Object)
                        {
                            who = Str(u, "name");
                            if (string.IsNullOrWhiteSpace(who))
                            {
                                who = Str(u, "login");
                            }
                        }
                        list.Add(new INatPhoto
                        {
                            ObservationId = Long(o, "id"),
                            ObservedOn = Str(o, "observed_on") ?? "",
                            Url = Str(ph, "url") ?? "",
                            Attribution = Str(ph, "attribution") ?? "",
                            License = Str(ph, "license_code") ?? "",
                            Observer = who ?? "",
                        });
                    }
                }
            }
            return (list, total);
        }

        /// <summary>The bytes of a picture (not a request to
        /// iNaturalist's data service, so it does not wait its turn).</summary>
        internal async Task<byte[]> GetBytesAsync(string url, CancellationToken ct)
        {
            using (HttpResponseMessage resp = await _http.GetAsync(url, ct).ConfigureAwait(false))
            {
                resp.EnsureSuccessStatusCode();
                return await resp.Content.ReadAsByteArrayAsync(ct).ConfigureAwait(false);
            }
        }

        /// <summary>How many observations match.</summary>
        internal async Task<long> CountAsync(INatQuery q, CancellationToken ct)
        {
            List<KeyValuePair<string, string>> p = q.Params();
            p.Add(new KeyValuePair<string, string>("per_page", "0"));
            using (JsonDocument doc = await GetAsync(Api + "observations?" + INatQuery.QueryString(p), ct).ConfigureAwait(false))
            {
                return Long(doc.RootElement, "total_results", 0);
            }
        }

        /// <summary>The species among the matching observations, most
        /// observed first, 500 a page, with the number of observations of
        /// each.</summary>
        internal async Task<(List<(INatTaxon Taxon, long Count)> Species, long Total)> SpeciesAsync(INatQuery q, int page,
            CancellationToken ct, IReadOnlyList<string> groups = null)
        {
            List<KeyValuePair<string, string>> p = q.Params();
            if (groups != null && groups.Count > 0)
            {
                // iNaturalist's own broad groups ("iconic taxa"): Mammalia, Aves, Plantae, ...
                p.Add(new KeyValuePair<string, string>("iconic_taxa", string.Join(",", groups)));
            }
            p.Add(new KeyValuePair<string, string>("per_page", "500"));
            p.Add(new KeyValuePair<string, string>("page", page.ToString(CultureInfo.InvariantCulture)));
            var list = new List<(INatTaxon, long)>();
            long total = 0;
            using (JsonDocument doc = await GetAsync(Api + "observations/species_counts?" + INatQuery.QueryString(p), ct)
                .ConfigureAwait(false))
            {
                total = Long(doc.RootElement, "total_results", 0);
                if (doc.RootElement.TryGetProperty("results", out JsonElement res))
                {
                    foreach (JsonElement r in res.EnumerateArray())
                    {
                        if (r.TryGetProperty("taxon", out JsonElement t))
                        {
                            list.Add((ReadTaxon(t), Long(r, "count", 0)));
                        }
                    }
                }
            }
            return (list, total);
        }
    }

    /// <summary>A failure the user can act on.</summary>
    internal sealed class INatException : Exception
    {
        public int Status { get; }
        public INatException(string message, int status) : base(message) { Status = status; }
    }
}
