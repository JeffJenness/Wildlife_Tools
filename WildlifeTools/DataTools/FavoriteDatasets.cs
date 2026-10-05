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
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading.Tasks;
using ArcGIS.Core.Data;
using ArcGIS.Core.Data.Exceptions;
using ArcGIS.Core.Data.Raster;

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

    /// <summary>Availability probes, in two stages. <see cref="CheckAsync"/>
    /// is the cheap first look (no MCT needed): filesystem checks for local
    /// data, a short HTTP request for services.
    /// <see cref="CheckInsideGeodatabases"/> is the second look, on the MCT:
    /// a file geodatabase that exists is opened and the dataset itself is
    /// looked for inside it. 'null' = could not determine (e.g. enterprise
    /// .sde connections) -- shown as available.</summary>
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

        // ---- the second look: inside file geodatabases ----------------------
        // The first look only proves the GEODATABASE folder is there. A
        // feature class that was renamed or deleted inside it is the most
        // ordinary way for a favorite to go stale, and it showed as
        // available (Jeff, 2026-10-05: "wouldn't it make more sense to
        // actually see if the dataset exists in that geodatabase?").

        private enum GdbKind { FeatureClass, Table, Raster, Unknown }

        /// <summary>What a favorite is, from the ArcGIS Pro item type recorded
        /// when it was added (the ids are Pro's own, from ADGeoDatabase.daml).
        /// Only the three kinds this test can look up by name are told apart;
        /// everything else (mosaic datasets, raster bands, topologies,
        /// networks, feature datasets, ...) is Unknown and is never marked
        /// unavailable by this test.</summary>
        private static GdbKind KindOf(string typeId)
        {
            switch ((typeId ?? "").Trim().ToLowerInvariant())
            {
                case "fgdb_fc_point":
                case "fgdb_fc_line":
                case "fgdb_fc_polygon":
                case "fgdb_fc_multipoint":
                case "fgdb_fc_multipatch":
                case "fgdb_fc_annotation":
                case "fgdb_fc_dimension":
                case "fgdb_fc_general":
                    return GdbKind.FeatureClass;
                case "fgdb_table":
                    return GdbKind.Table;
                case "raster_dataset_fgdb":
                    return GdbKind.Raster;
                default:
                    return GdbKind.Unknown;
            }
        }

        /// <summary>The file geodatabase a favorite's path points into and
        /// the name of the dataset inside it; (null, null) when the path is
        /// not inside a file geodatabase, or IS the geodatabase. A feature
        /// class in a feature dataset (x.gdb\fds\name) gives "name": dataset
        /// names are unique across a geodatabase.</summary>
        public static (string Gdb, string Dataset) SplitGeodatabasePath(string path)
        {
            try
            {
                string p = (path ?? "").Trim().TrimEnd('\\', '/');
                string q = p;
                while (!string.IsNullOrEmpty(q))
                {
                    if (string.Equals(System.IO.Path.GetExtension(q), ".gdb",
                            StringComparison.OrdinalIgnoreCase))
                    {
                        if (q.Length >= p.Length) return (null, null);
                        string name = System.IO.Path.GetFileName(p);
                        return string.IsNullOrEmpty(name) ? (null, null) : (q, name);
                    }
                    string parent = System.IO.Path.GetDirectoryName(q);
                    if (string.IsNullOrEmpty(parent) || parent == q) break;
                    q = parent;
                }
            }
            catch { }
            return (null, null);
        }

        /// <summary>
        /// Looks inside file geodatabases for the datasets themselves. Each
        /// distinct geodatabase is opened once. Result per path: true = the
        /// dataset is there; false = the geodatabase opened and the dataset is
        /// NOT in it; null = could not tell (the geodatabase would not open,
        /// or the favorite is a kind of dataset this test does not look up).
        /// Paths that are not inside a file geodatabase are left out.
        /// MUST run on the MCT (QueuedTask.Run).
        /// </summary>
        public static Dictionary<string, bool?> CheckInsideGeodatabases(
            IEnumerable<(string Path, string TypeId)> items)
        {
            var result = new Dictionary<string, bool?>(StringComparer.OrdinalIgnoreCase);
            var byGdb = new Dictionary<string, List<(string Path, string Name, string TypeId)>>(
                StringComparer.OrdinalIgnoreCase);
            foreach (var (path, typeId) in items)
            {
                var (gdb, name) = SplitGeodatabasePath(path);
                if (gdb == null) continue;
                if (!byGdb.TryGetValue(gdb, out var list))
                    byGdb[gdb] = list = new List<(string, string, string)>();
                list.Add((path, name, typeId));
            }
            foreach (var kv in byGdb)
            {
                Geodatabase gdb;
                try
                {
                    gdb = new Geodatabase(
                        new FileGeodatabaseConnectionPath(new Uri(kv.Key)));
                }
                catch
                {
                    continue;      // would not open: its favorites stay undetermined
                }
                using (gdb)
                    foreach (var it in kv.Value)
                    {
                        bool? found;
                        try { found = Lookup(gdb, it.Name, KindOf(it.TypeId)); }
                        catch { found = null; }
                        result[it.Path] = found;
                    }
            }
            return result;
        }

        /// <summary>Is a dataset of this name in the geodatabase? It is looked
        /// for first as the kind it was recorded as, then as the other kinds
        /// (a dataset of that name under another type still counts as found).
        /// FALSE -- which turns the favorite red -- is returned only when the
        /// recorded kind is one this test can look up and the geodatabase says
        /// it has no such dataset; every doubt comes back null.</summary>
        private static bool? Lookup(Geodatabase gdb, string name, GdbKind hint)
        {
            bool? hinted = null;
            switch (hint)
            {
                case GdbKind.FeatureClass:
                    hinted = Has<FeatureClassDefinition>(gdb, name); break;
                case GdbKind.Table:
                    hinted = Has<TableDefinition>(gdb, name); break;
                case GdbKind.Raster:
                    hinted = HasRaster(gdb, name); break;
            }
            if (hinted == true) return true;
            if (hint != GdbKind.FeatureClass &&
                Has<FeatureClassDefinition>(gdb, name) == true) return true;
            if (hint != GdbKind.Table &&
                Has<TableDefinition>(gdb, name) == true) return true;
            if (hint != GdbKind.Raster && HasRaster(gdb, name) == true) return true;
            return hinted == false ? false : (bool?)null;
        }

        /// <summary>true = a definition of this type and name exists; false =
        /// the geodatabase reports no such dataset (Esri documents
        /// GeodatabaseCatalogDatasetException, a GeodatabaseException, for a
        /// name that "does not exist or cannot be opened"); null = anything
        /// else, such as a definition type the geodatabase does not
        /// support.</summary>
        private static bool? Has<T>(Geodatabase gdb, string name) where T : Definition
        {
            try
            {
                using (T def = gdb.GetDefinition<T>(name))
                    return def != null ? true : (bool?)null;
            }
            catch (GeodatabaseException) { return false; }
            catch { return null; }
        }

        private static bool? HasRaster(Geodatabase gdb, string name)
        {
            bool? viaDefinition = Has<RasterDatasetDefinition>(gdb, name);
            if (viaDefinition.HasValue) return viaDefinition;
            try
            {
                using (RasterDataset r = gdb.OpenDataset<RasterDataset>(name))
                    return r != null ? true : (bool?)null;
            }
            catch (GeodatabaseException) { return false; }
            catch { return null; }
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
