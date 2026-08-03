using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading.Tasks;

namespace WildlifeTools.DataTools
{
    /// <summary>One saved favorite: everything needed to load the dataset
    /// later -- the catalog path (or service URL), a type hint recorded when
    /// it was added, and the user's description.</summary>
    public sealed class FavoriteItem
    {
        public string Description { get; set; } = "";
        public string Path { get; set; } = "";
        public string TypeId { get; set; } = "";      // Pro item TypeID (hint)
        public bool IsTable { get; set; }             // standalone table vs layer
        public string Added { get; set; } = "";       // provenance (yyyy-mm-dd)
    }

    public sealed class FavoriteGroup
    {
        public string Name { get; set; } = "";
        public List<FavoriteItem> Items { get; set; } = new List<FavoriteItem>();
    }

    /// <summary>
    /// The four favorites groups, persisted as JSON in the same per-user
    /// store as the rest of the add-in's settings. Modeled on the ArcMap
    /// "Load Favorite Datasets" tool (SSI frmLoadRefDatasets): four lists,
    /// user-defined membership and descriptions.
    /// </summary>
    public sealed class FavoritesStore
    {
        [JsonPropertyName("format")] public string Format { get; set; } = "JennessFavoriteDatasets";
        [JsonPropertyName("version")] public int Version { get; set; } = 1;
        public List<FavoriteGroup> Groups { get; set; } = new List<FavoriteGroup>();

        public static string StorePath()
        {
            string b = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (string.IsNullOrEmpty(b))
                b = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            string dir = System.IO.Path.Combine(b, "JennessEnterprises", "WildlifeTools");
            Directory.CreateDirectory(dir);
            return System.IO.Path.Combine(dir, "favorite_datasets.json");
        }

        public static FavoritesStore Load()
        {
            try
            {
                string p = StorePath();
                if (File.Exists(p))
                {
                    var s = JsonSerializer.Deserialize<FavoritesStore>(
                        File.ReadAllText(p), new JsonSerializerOptions
                        {
                            ReadCommentHandling = JsonCommentHandling.Skip,
                            AllowTrailingCommas = true,
                        });
                    if (s != null && s.Format == "JennessFavoriteDatasets")
                    {
                        s.EnsureFourGroups();
                        return s;
                    }
                }
            }
            catch { }
            var fresh = new FavoritesStore();
            fresh.EnsureFourGroups();
            return fresh;
        }

        public void Save() =>
            File.WriteAllText(StorePath(), JsonSerializer.Serialize(this,
                new JsonSerializerOptions { WriteIndented = true }));

        private void EnsureFourGroups()
        {
            Groups = Groups ?? new List<FavoriteGroup>();
            while (Groups.Count < 4)
                Groups.Add(new FavoriteGroup());
            for (int i = 0; i < 4; i++)
            {
                if (Groups[i] == null) Groups[i] = new FavoriteGroup();
                if (string.IsNullOrWhiteSpace(Groups[i].Name))
                    Groups[i].Name = "Group " + (i + 1);
                Groups[i].Items = Groups[i].Items ?? new List<FavoriteItem>();
            }
            if (Groups.Count > 4) Groups = Groups.Take(4).ToList();
        }
    }

    /// <summary>Cheap availability probes (no MCT needed): filesystem checks
    /// for local data, a short HTTP request for services. 'null' = could not
    /// determine (e.g. enterprise .sde connections) -- shown as available.</summary>
    public static class FavoriteAvailability
    {
        private static readonly HttpClient Http = new HttpClient
        { Timeout = TimeSpan.FromSeconds(5) };

        public static async Task<bool?> CheckAsync(string path)
        {
            if (string.IsNullOrWhiteSpace(path)) return false;
            string p = path.Trim();
            if (p.StartsWith("http://", StringComparison.OrdinalIgnoreCase) ||
                p.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    // Any completed HTTP response (even 4xx from an auth
                    // gate) proves the server is reachable; only transport
                    // errors / timeouts count as unavailable.
                    using (var resp = await Http.GetAsync(p,
                        HttpCompletionOption.ResponseHeadersRead))
                        return true;
                }
                catch { return false; }
            }
            // Service datasets browsed through the catalog carry the path of
            // their local CONNECTION FILE (.ags / .wms / ...) with the service
            // name appended; the data itself is on a server. Find such a
            // segment first: if the connection file exists, ping the host
            // named in the file name ("services on <host>.ags"); a reachable
            // host is available, an unpingable one stays UNDETERMINED (never
            // red -- the file may simply hide its host), and only a missing
            // connection file is definitely unavailable.
            string conn = FindSegmentWithExtension(p,
                ".ags", ".wms", ".wmts", ".wcs", ".stac", ".ogcapi");
            if (conn != null)
            {
                if (!File.Exists(conn)) return false;
                var m = System.Text.RegularExpressions.Regex.Match(
                    System.IO.Path.GetFileNameWithoutExtension(conn),
                    @"\bon\s+([A-Za-z0-9][A-Za-z0-9.\-]*)",
                    System.Text.RegularExpressions.RegexOptions.IgnoreCase);
                if (m.Success)
                {
                    try
                    {
                        using (var resp = await Http.GetAsync(
                            "https://" + m.Groups[1].Value + "/",
                            HttpCompletionOption.ResponseHeadersRead))
                            return true;
                    }
                    catch { return null; }
                }
                return null;
            }

            return await Task.Run<bool?>(() =>
            {
                try
                {
                    if (File.Exists(p) || Directory.Exists(p)) return true;
                    // Dataset inside a workspace: find the workspace segment
                    // (.gdb folder, .sde/.sqlite/.gpkg file, or shapefile
                    // folder) and test that.
                    string q = p;
                    while (!string.IsNullOrEmpty(q))
                    {
                        string ext = System.IO.Path.GetExtension(q)
                            .ToLowerInvariant();
                        if (ext == ".gdb")
                            return Directory.Exists(q);
                        if (ext == ".sde")
                            return File.Exists(q) ? (bool?)null : false;
                        if (ext == ".sqlite" || ext == ".gpkg" || ext == ".kml"
                            || ext == ".kmz")
                            return File.Exists(q);
                        string parent = System.IO.Path.GetDirectoryName(q);
                        if (string.IsNullOrEmpty(parent) || parent == q)
                            break;
                        q = parent;
                    }
                    // Shapefile-style: folder + dataset name.
                    string dir = System.IO.Path.GetDirectoryName(p);
                    if (!string.IsNullOrEmpty(dir) && Directory.Exists(dir))
                    {
                        string name = System.IO.Path.GetFileName(p);
                        if (File.Exists(System.IO.Path.Combine(dir, name + ".shp")))
                            return true;
                        return false;
                    }
                    return false;
                }
                catch { return null; }
            });
        }

        /// <summary>The leading portion of <paramref name="path"/> ending in
        /// one of the given extensions, or null (e.g. the ".ags" connection
        /// file inside a service dataset's catalog path).</summary>
        private static string FindSegmentWithExtension(string path,
            params string[] exts)
        {
            try
            {
                string q = path;
                while (!string.IsNullOrEmpty(q))
                {
                    string ext = System.IO.Path.GetExtension(q).ToLowerInvariant();
                    if (exts.Contains(ext)) return q;
                    string parent = System.IO.Path.GetDirectoryName(q);
                    if (string.IsNullOrEmpty(parent) || parent == q) break;
                    q = parent;
                }
            }
            catch { }
            return null;
        }
    }
}
