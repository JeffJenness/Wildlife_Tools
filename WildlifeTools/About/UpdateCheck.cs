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
using System.Net.Http;
using System.Text.Json;
using System.Threading.Tasks;

namespace WildlifeTools.About
{
    /// <summary>
    /// Asks GitHub for the add-in's newest release: one GET of the public
    /// releases/latest endpoint (no sign-in, no key; the same data the Tool
    /// Reference page's download count uses). Any failure -- no internet, a
    /// proxy, a rate limit, odd JSON -- answers null, and the card says it
    /// could not check. Free of ArcGIS, like <see cref="AboutInfo"/>.
    /// </summary>
    internal static class UpdateCheck
    {
        /// <summary>The "tag_name" of a GitHub release object, or null.</summary>
        public static string ParseTag(string json)
        {
            if (string.IsNullOrWhiteSpace(json))
            {
                return null;
            }
            try
            {
                using (JsonDocument doc = JsonDocument.Parse(json))
                {
                    return doc.RootElement.TryGetProperty("tag_name", out JsonElement tag)
                        && tag.ValueKind == JsonValueKind.String
                        ? tag.GetString()
                        : null;
                }
            }
            catch (JsonException)
            {
                return null;
            }
        }

        /// <summary>The newest release's tag ("v1.112.60.0"), or null when
        /// GitHub could not be reached within <paramref name="timeout"/>.</summary>
        public static async Task<string> LatestTagAsync(TimeSpan timeout)
        {
            try
            {
                using (var http = new HttpClient { Timeout = timeout })
                {
                    // GitHub refuses requests without a User-Agent.
                    http.DefaultRequestHeaders.UserAgent.ParseAdd("WildlifeTools-AddIn");
                    http.DefaultRequestHeaders.Accept.ParseAdd("application/vnd.github+json");
                    string json = await http.GetStringAsync(AboutInfo.LatestReleaseApi)
                        .ConfigureAwait(false);
                    return ParseTag(json);
                }
            }
            catch (Exception)
            {
                return null;
            }
        }
    }
}
