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
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace WildlifeTools.OnlineData.BurnSeverity
{
    /// <summary>One assessment of a fire in the Burn Severity Portal's catalog.</summary>
    internal sealed class BurnFire
    {
        public string Program;          // MTBS, RAVG, BAER
        public string Name;
        public string Id;               // the programs' event identifier, e.g. AZ3525811164320190721
        public long MapId;              // the portal's number for this assessment; 0 when the catalog gives none
        public string IgnitionDate;     // yyyy-MM-dd
        public int Year;
        public string FireType;
        public string Assessment;       // Initial, Extended, Emergency
        public double Acres = -1;
        public double Lat = double.NaN, Lon = double.NaN;
        /// <summary>[polygon][ring][vertex], WGS 84; filled only when asked for.</summary>
        public List<List<List<(double Lon, double Lat)>>> Polygons;
    }

    /// <summary>
    /// The Burn Severity window's own requests to the fire catalog of the
    /// Burn Severity Portal (a U.S. Geological Survey map server): the
    /// list of fires that match a name, a range of years and a rectangle.
    /// Fetching rasters and requesting data bundles is the geoprocessing
    /// tool's job. No ArcGIS types.
    /// </summary>
    internal sealed class BurnClient : IDisposable
    {
        internal const string Catalog = "https://edcintl.cr.usgs.gov/geoserver/wfs";
        internal const string Portal = "https://burnseverity.cr.usgs.gov/";
        internal const int Most = 2000;         // the most fires one search lists

        private readonly HttpClient _http;

        internal BurnClient()
        {
            _http = new HttpClient { Timeout = TimeSpan.FromSeconds(180) };
            _http.DefaultRequestHeaders.UserAgent.ParseAdd("WildlifeTools-AddIn (Jenness Enterprises; ArcGIS Pro)");
        }

        public void Dispose() => _http.Dispose();

        private static string Quote(string s) => (s ?? "").Replace("'", "''");

        /// <summary>The catalog's filter for a search (the same one the geoprocessing tool builds).</summary>
        internal static string Filter(string program, string name, int? yearFrom, int? yearTo,
            (double West, double South, double East, double North)? box)
        {
            CultureInfo inv = CultureInfo.InvariantCulture;
            var parts = new List<string> { "map_prog='" + Quote(program) + "'" };
            string[] words = (name ?? "").Split(';').Select(w => w.Trim()).Where(w => w.Length > 0).ToArray();
            if (words.Length > 0)
            {
                parts.Add("(" + string.Join(" OR ", words.Select(w => "incid_name ILIKE '%" + Quote(w) + "%'")) + ")");
            }
            if (yearFrom.HasValue)
            {
                parts.Add("ig_date >= '" + yearFrom.Value.ToString("0000", inv) + "-01-01'");
            }
            if (yearTo.HasValue)
            {
                parts.Add("ig_date <= '" + yearTo.Value.ToString("0000", inv) + "-12-31'");
            }
            if (box.HasValue)
            {
                var b = box.Value;
                parts.Add(string.Format(inv, "BBOX(geom,{0:0.######},{1:0.######},{2:0.######},{3:0.######},'EPSG:4326')",
                    b.West, b.South, b.East, b.North));
            }
            return string.Join(" AND ", parts);
        }

        /// <summary>The fires that match, oldest first. With
        /// <paramref name="withGeometry"/> each fire carries its perimeter
        /// (needed to test it against a polygon study area).</summary>
        internal async Task<List<BurnFire>> SearchAsync(string program, string name, int? yearFrom, int? yearTo,
            (double West, double South, double East, double North)? box, bool withGeometry, CancellationToken ct)
        {
            var fires = new List<BurnFire>();
            string cql = Uri.EscapeDataString(Filter(program, name, yearFrom, yearTo, box));
            const int Page = 500;
            for (int start = 0; start < Most; start += Page)
            {
                string url = Catalog + "?service=WFS&version=2.0.0&request=GetFeature&typeNames=mtbs:fire_polygons"
                    + "&outputFormat=application/json&srsName=EPSG:4326&sortBy=ig_date,id&count=" + Page
                    + "&startIndex=" + start
                    + (withGeometry ? "" : "&propertyName=map_prog,incid_name,event_id,map_id,ig_date,incid_type,asmt_type,burnbndac,burnbndlat,burnbndlon")
                    + "&cql_filter=" + cql;
                string body;
                using (HttpResponseMessage resp = await _http.GetAsync(url, ct).ConfigureAwait(false))
                {
                    body = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
                    if (!resp.IsSuccessStatusCode)
                    {
                        throw new InvalidOperationException("The fire catalog returned an error ("
                            + ((int)resp.StatusCode).ToString(CultureInfo.InvariantCulture) + "). Try again in a few minutes.");
                    }
                }
                int got = 0;
                using (JsonDocument doc = JsonDocument.Parse(body))
                {
                    if (!doc.RootElement.TryGetProperty("features", out JsonElement feats) || feats.ValueKind != JsonValueKind.Array)
                    {
                        throw new InvalidOperationException("The fire catalog did not answer as expected.");
                    }
                    foreach (JsonElement f in feats.EnumerateArray())
                    {
                        got++;
                        if (!f.TryGetProperty("properties", out JsonElement p))
                        {
                            continue;
                        }
                        var fire = new BurnFire
                        {
                            Program = Str(p, "map_prog") ?? program,
                            Name = Str(p, "incid_name") ?? "",
                            Id = Str(p, "event_id") ?? "",
                            IgnitionDate = (Str(p, "ig_date") ?? "").TrimEnd('Z'),
                            FireType = Str(p, "incid_type") ?? "",
                            Assessment = Str(p, "asmt_type") ?? "",
                            Acres = Num(p, "burnbndac", -1),
                            Lat = Num(p, "burnbndlat", double.NaN),
                            Lon = Num(p, "burnbndlon", double.NaN),
                        };
                        double mapId = Num(p, "map_id", 0);
                        fire.MapId = mapId > 0 && mapId < 1e15 ? (long)mapId : 0;
                        if (fire.IgnitionDate.Length >= 4 && int.TryParse(fire.IgnitionDate.Substring(0, 4), out int y))
                        {
                            fire.Year = y;
                        }
                        if (withGeometry && f.TryGetProperty("geometry", out JsonElement g) && g.ValueKind == JsonValueKind.Object)
                        {
                            fire.Polygons = ReadPolygons(g);
                        }
                        fires.Add(fire);
                    }
                }
                if (got < Page)
                {
                    break;
                }
            }
            return fires;
        }

        private static List<List<List<(double, double)>>> ReadPolygons(JsonElement g)
        {
            var polys = new List<List<List<(double, double)>>>();
            if (!g.TryGetProperty("coordinates", out JsonElement c) || c.ValueKind != JsonValueKind.Array)
            {
                return polys;
            }
            string type = Str(g, "type") ?? "";
            IEnumerable<JsonElement> list = type == "Polygon" ? new[] { c } : c.EnumerateArray();
            foreach (JsonElement poly in list)
            {
                var rings = new List<List<(double, double)>>();
                foreach (JsonElement ring in poly.EnumerateArray())
                {
                    var pts = new List<(double, double)>();
                    foreach (JsonElement pt in ring.EnumerateArray())
                    {
                        if (pt.ValueKind == JsonValueKind.Array && pt.GetArrayLength() >= 2)
                        {
                            pts.Add((pt[0].GetDouble(), pt[1].GetDouble()));
                        }
                    }
                    if (pts.Count >= 3)
                    {
                        rings.Add(pts);
                    }
                }
                if (rings.Count > 0)
                {
                    polys.Add(rings);
                }
            }
            return polys;
        }

        private static string Str(JsonElement o, string name)
        {
            if (!o.TryGetProperty(name, out JsonElement v))
            {
                return null;
            }
            return v.ValueKind == JsonValueKind.String ? v.GetString()
                : v.ValueKind == JsonValueKind.Null ? null : v.ToString();
        }

        private static double Num(JsonElement o, string name, double none)
        {
            if (!o.TryGetProperty(name, out JsonElement v))
            {
                return none;
            }
            if (v.ValueKind == JsonValueKind.Number && v.TryGetDouble(out double d))
            {
                return d;
            }
            return v.ValueKind == JsonValueKind.String
                && double.TryParse(v.GetString(), NumberStyles.Float, CultureInfo.InvariantCulture, out double s) ? s : none;
        }
    }
}
