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
using System.Net.Http.Headers;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace WildlifeTools.OnlineData.Movebank
{
    /// <summary>A study in Movebank that the signed-in account may download.</summary>
    internal sealed class MovebankStudy
    {
        public string Id, Name, Taxa, Sensors, License, Citation, Investigator, Contact, Objective, First, Last;
        public long Animals = -1, Locations = -1;
        public double Lat = double.NaN, Lon = double.NaN;
        public string Page => "https://www.movebank.org/cms/webapp?gwt_fragment=page=studies,path=study" + Id;

        public string LicenseName =>
            License == "CC_0" ? "CC0" : License == "CC_BY" ? "CC BY" : License == "CC_BY_NC" ? "CC BY-NC"
            : License == "CUSTOM" ? "Owner's terms" : License ?? "";
    }

    /// <summary>
    /// Movebank itself, read with the user's own Movebank account through
    /// Movebank's "direct-read" service. The window uses this to list the
    /// studies the account may download and to show a study's license
    /// terms; fetching a study is the geoprocessing tool's job. The user
    /// name and password go only to www.movebank.org, over https, and are
    /// never written anywhere by this class. No ArcGIS types here.
    /// </summary>
    internal sealed class MovebankAccount : IDisposable
    {
        internal const string Service = "Movebank";             // the name in CredentialStore
        private const string Direct = "https://www.movebank.org/movebank/service/direct-read?";

        private readonly HttpClient _http;
        private List<MovebankStudy> _studies;                   // the list of the account now signed in
        private string _studiesOf;

        internal MovebankAccount()
        {
            _http = new HttpClient { Timeout = TimeSpan.FromSeconds(180) };
            _http.DefaultRequestHeaders.UserAgent.ParseAdd("WildlifeTools-AddIn (Jenness Enterprises; ArcGIS Pro)");
        }

        public void Dispose() => _http.Dispose();

        /// <summary>The user name of the stored sign-in, or null.</summary>
        internal static string SignedInAs()
        {
            try
            {
                return CredentialStore.TryRead(Service, out string user, out string _) && !string.IsNullOrEmpty(user) ? user : null;
            }
            catch (Exception)
            {
                return null;
            }
        }

        internal void Forget()
        {
            _studies = null;
            _studiesOf = null;
        }

        /// <summary>True when Movebank accepts the user name and password.
        /// Throws when Movebank cannot be reached.</summary>
        internal async Task<bool> CheckAsync(string user, string password, CancellationToken ct)
        {
            using (var req = Request("entity_type=tag_type", user, password))
            using (HttpResponseMessage resp = await _http.SendAsync(req, ct).ConfigureAwait(false))
            {
                if (resp.StatusCode == HttpStatusCode.Unauthorized || resp.StatusCode == HttpStatusCode.Forbidden)
                {
                    return false;
                }
                if (!resp.IsSuccessStatusCode)
                {
                    throw new InvalidOperationException("Movebank returned an error ("
                        + ((int)resp.StatusCode).ToString(CultureInfo.InvariantCulture) + "). Try again in a few minutes.");
                }
                return true;
            }
        }

        /// <summary>The studies the stored account may download, read once
        /// and kept until the sign-in changes.</summary>
        internal async Task<List<MovebankStudy>> StudiesAsync(CancellationToken ct)
        {
            Credentials(out string user, out string password);
            if (_studies != null && _studiesOf == user)
            {
                return _studies;
            }
            // Movebank is asked for the studies this account may download, not for its whole table of
            // studies with their long descriptions, citations and terms, most of which would be thrown
            // away here. The test of each row below stays: it costs nothing, and the list is then right
            // whether or not the service applied the filter. If the service refuses the filtered request,
            // the whole table is asked for, as before.
            string text;
            bool filtered = true;
            try
            {
                text = await GetAsync("entity_type=study&i_have_download_access=true", user, password, ct).ConfigureAwait(false);
            }
            catch (InvalidOperationException ex) when (ex.Message.StartsWith("Movebank returned an error", StringComparison.Ordinal))
            {
                filtered = false;
                text = await GetAsync("entity_type=study", user, password, ct).ConfigureAwait(false);
            }
            List<string[]> rows = ParseCsv(text);
            var list = new List<MovebankStudy>();
            if (rows.Count > 0)
            {
                var col = new Dictionary<string, int>(StringComparer.Ordinal);
                for (int i = 0; i < rows[0].Length; i++)
                {
                    col[rows[0][i]] = i;
                }
                string V(string[] r, string name) => col.TryGetValue(name, out int i) && i < r.Length ? r[i] : "";
                // a filtered answer that does not carry the column has been filtered by the service already
                bool test = !filtered || col.ContainsKey("i_have_download_access");
                foreach (string[] r in rows.Skip(1))
                {
                    if ((test && V(r, "i_have_download_access") != "true") || V(r, "id").Length == 0)
                    {
                        continue;
                    }
                    var s = new MovebankStudy
                    {
                        Id = V(r, "id"),
                        Name = V(r, "name"),
                        Taxa = V(r, "taxon_ids").Replace(",", ", "),
                        Sensors = V(r, "sensor_type_ids").Replace(",", ", "),
                        License = V(r, "license_type"),
                        Citation = MovebankClient.Plain(WebUtility.HtmlDecode(V(r, "citation"))),
                        Investigator = V(r, "principal_investigator_name"),
                        Contact = V(r, "contact_person_name"),
                        Objective = MovebankClient.Plain(V(r, "study_objective")),
                        First = V(r, "timestamp_first_deployed_location"),
                        Last = V(r, "timestamp_last_deployed_location"),
                    };
                    if (double.TryParse(V(r, "number_of_individuals"), NumberStyles.Float, CultureInfo.InvariantCulture, out double a))
                    {
                        s.Animals = (long)a;
                    }
                    if (double.TryParse(V(r, "number_of_deployed_locations"), NumberStyles.Float, CultureInfo.InvariantCulture, out double n))
                    {
                        s.Locations = (long)n;
                    }
                    if (double.TryParse(V(r, "main_location_lat"), NumberStyles.Float, CultureInfo.InvariantCulture, out double lat)
                        && double.TryParse(V(r, "main_location_long"), NumberStyles.Float, CultureInfo.InvariantCulture, out double lon))
                    {
                        s.Lat = lat;
                        s.Lon = lon;
                    }
                    list.Add(s);
                }
            }
            _studies = list;
            _studiesOf = user;
            return list;
        }

        /// <summary>The study's license terms, as plain text, when the
        /// account has not yet accepted them; null when it has. Asks for
        /// the fixes of one instant in the year 1900, so no data come back
        /// either way. Accepts nothing.</summary>
        internal async Task<string> TermsAsync(string studyId, CancellationToken ct)
        {
            Credentials(out string user, out string password);
            string text = await GetAsync("entity_type=event&study_id=" + Uri.EscapeDataString(studyId)
                + "&attributes=timestamp&timestamp_start=19000101000000000&timestamp_end=19000101000000001",
                user, password, ct).ConfigureAwait(false);
            return text.TrimStart().StartsWith("<", StringComparison.Ordinal) ? PlainParagraphs(text) : null;
        }

        private static void Credentials(out string user, out string password)
        {
            user = password = null;
            bool have = false;
            try
            {
                have = CredentialStore.TryRead(Service, out user, out password);
            }
            catch (Exception)
            {
            }
            if (!have || string.IsNullOrEmpty(user))
            {
                throw new InvalidOperationException("Sign in to Movebank first: click Sign in to Movebank on tab 1.");
            }
        }

        private static HttpRequestMessage Request(string query, string user, string password)
        {
            var req = new HttpRequestMessage(HttpMethod.Get, Direct + query);
            req.Headers.Authorization = new AuthenticationHeaderValue("Basic",
                Convert.ToBase64String(Encoding.UTF8.GetBytes(user + ":" + password)));
            return req;
        }

        private async Task<string> GetAsync(string query, string user, string password, CancellationToken ct)
        {
            using (var req = Request(query, user, password))
            using (HttpResponseMessage resp = await _http.SendAsync(req, ct).ConfigureAwait(false))
            {
                if (resp.StatusCode == HttpStatusCode.Unauthorized || resp.StatusCode == HttpStatusCode.Forbidden)
                {
                    throw new InvalidOperationException("Movebank did not accept the sign-in of " + user
                        + ". Sign in again on tab 1. A new account must be activated by Movebank before it works here.");
                }
                if (!resp.IsSuccessStatusCode)
                {
                    throw new InvalidOperationException(
                        (int)resp.StatusCode >= 502 && (int)resp.StatusCode <= 504
                            ? "Movebank is busy or unavailable right now. Try again in a few minutes."
                            : "Movebank returned an error (" + ((int)resp.StatusCode).ToString(CultureInfo.InvariantCulture) + ").");
                }
                byte[] raw = await resp.Content.ReadAsByteArrayAsync(ct).ConfigureAwait(false);
                return Encoding.UTF8.GetString(raw);
            }
        }

        /// <summary>HTML as plain text, with its paragraphs kept.</summary>
        internal static string PlainParagraphs(string html)
        {
            string t = Regex.Replace(html ?? "", @"(?i)<br\s*/?>|</p>|</div>|</li>", "\n");
            t = WebUtility.HtmlDecode(Regex.Replace(t, "<[^>]+>", ""));
            t = Regex.Replace(WebUtility.HtmlDecode(t), "<[^>]+>", "");      // the citations are escaped twice
            var lines = new List<string>();
            foreach (string raw in t.Replace("\r", "").Split('\n'))
            {
                string line = Regex.Replace(raw, @"[ \t]+", " ").Trim();
                if (line.Length > 0 || (lines.Count > 0 && lines[lines.Count - 1].Length > 0))
                {
                    lines.Add(line);
                }
            }
            return string.Join(Environment.NewLine, lines).Trim();
        }

        /// <summary>Rows of a CSV text: quoted values, doubled quotes and
        /// line breaks inside quotes.</summary>
        internal static List<string[]> ParseCsv(string text)
        {
            var rows = new List<string[]>();
            var row = new List<string>();
            var value = new StringBuilder();
            bool quoted = false, any = false;
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (quoted)
                {
                    if (c == '"')
                    {
                        if (i + 1 < text.Length && text[i + 1] == '"')
                        {
                            value.Append('"');
                            i++;
                        }
                        else
                        {
                            quoted = false;
                        }
                    }
                    else
                    {
                        value.Append(c);
                    }
                }
                else if (c == '"')
                {
                    quoted = true;
                    any = true;
                }
                else if (c == ',')
                {
                    row.Add(value.ToString());
                    value.Clear();
                    any = true;
                }
                else if (c == '\n' || c == '\r')
                {
                    if (c == '\r' && i + 1 < text.Length && text[i + 1] == '\n')
                    {
                        i++;
                    }
                    if (any || value.Length > 0)
                    {
                        row.Add(value.ToString());
                        rows.Add(row.ToArray());
                    }
                    row.Clear();
                    value.Clear();
                    any = false;
                }
                else
                {
                    value.Append(c);
                    any = true;
                }
            }
            if (any || value.Length > 0)
            {
                row.Add(value.ToString());
                rows.Add(row.ToArray());
            }
            return rows;
        }
    }
}
