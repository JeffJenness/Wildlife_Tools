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
using System.Linq;
using System.Threading.Tasks;
using ArcGIS.Core.Data;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Threading.Tasks;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Shared helper for writing a small table (schema + rows) to a
    /// geodatabase or dBASE table from C# window code: one CreateTable +
    /// one batched AddFields GP call (never an AddField loop -- standing
    /// rule), then row inserts through an EditOperation. GP calls run with
    /// quiet flags so no unstyled layer is auto-added to the map.
    /// </summary>
    internal static class GpTables
    {
        /// <summary>Workspace + dataset name from a path, honoring the
        /// standing rule: ".gdb"/".sde" counts only when followed by a
        /// separator (or ending the string).</summary>
        public static (string ws, string name, bool gdb) SplitPath(string path)
        {
            string norm = (path ?? "").Replace('/', '\\').TrimEnd('\\');
            string low = norm.ToLowerInvariant();
            foreach (var ext in new[] { ".gdb", ".sde" })
            {
                int at = 0;
                while (true)
                {
                    int i = low.IndexOf(ext, at, StringComparison.Ordinal);
                    if (i < 0) break;
                    int end = i + ext.Length;
                    if (end == low.Length) break;
                    if (low[end] == '\\')
                        return (norm.Substring(0, end),
                                norm.Substring(norm.LastIndexOf('\\') + 1),
                                true);
                    at = end;
                }
            }
            return (System.IO.Path.GetDirectoryName(norm),
                    System.IO.Path.GetFileName(norm), false);
        }

        /// <summary>Run a GP tool without adding outputs to the map (the
        /// default flags auto-add; the NRE fallback mirrors the LandFacet
        /// finding).</summary>
        public static Task<IGPResult> RunQuiet(string tool,
            IReadOnlyList<string> args)
        {
            try
            {
                return Geoprocessing.ExecuteToolAsync(tool,
                    (IEnumerable<string>)args, null,
                    (System.Threading.CancellationToken?)null, null,
                    GPExecuteToolFlags.GPThread
                    | GPExecuteToolFlags.RefreshProjectItems);
            }
            catch (NullReferenceException)
            {
                return Geoprocessing.ExecuteToolAsync(tool, args);
            }
        }

        /// <summary>Create and fill a table from EXPLICIT parts (workspace,
        /// name, kind) -- no dialog-path parsing anywhere. NEVER
        /// overwrites: an existing dataset (which may be locked by the map
        /// or an edit session) makes the name auto-increment instead.
        ///
        /// Geodatabase targets use the Core.Data DDL SchemaBuilder and
        /// Geodatabase.ApplyEdits: ZERO geoprocessing-tool launches and no
        /// Pro edit session, so the export is fast and leaves nothing
        /// pending. Folder (.dbf) targets keep the GP + EditOperation path
        /// (the DDL does not cover dBASE).
        ///
        /// Call from the UI thread; MCT/GP work happens inside. Returns
        /// the final path actually written.</summary>
        public static async Task<string> WriteAsync(string ws, string name,
            bool gdb, HistogramStore.TableSpec spec)
        {
            // sanitize the dataset name (standing rule)
            if (gdb)
            {
                int dot = name.IndexOf('.');
                if (dot > 0) name = name.Substring(0, dot);
                var sb = new System.Text.StringBuilder();
                foreach (char c in name)
                    sb.Append(char.IsLetterOrDigit(c) || c == '_' ? c : '_');
                name = sb.ToString();
                if (name.Length == 0 || !char.IsLetter(name[0]))
                    name = "T_" + name;
            }
            else if (name.ToLowerInvariant().EndsWith(".dbf"))
            {
                name = name.Substring(0, name.Length - 4);
            }

            var maxLen = spec.Schema.Select(s2 =>
                s2.type == "TEXT" && s2.len > 0 ? s2.len : 0).ToArray();
            var fieldNames = spec.Schema.Select(s => s.name).ToArray();

            void FillBuffer(RowBuffer buf, object[] row)
            {
                for (int i = 0; i < fieldNames.Length; i++)
                {
                    object v = row[i];
                    if (v is double dv && double.IsNaN(dv)) v = null;
                    // clamp strings to the declared length -- one long
                    // value must never kill the whole insert
                    if (v is string sv && maxLen[i] > 0 &&
                        sv.Length > maxLen[i])
                        v = sv.Substring(0, maxLen[i]);
                    buf[fieldNames[i]] = v;
                }
            }

            if (gdb)
            {
                return await QueuedTask.Run(() =>
                {
                    using (var g = OpenGdb(ws))
                    {
                        string cand = name;
                        for (int i = 1; i <= 999 && GdbHas(g, cand); i++)
                            cand = name + "_" + i;

                        var fds = new List<ArcGIS.Core.Data.DDL.FieldDescription>();
                        foreach (var (fname, ftype, flen, falias) in spec.Schema)
                        {
                            ArcGIS.Core.Data.DDL.FieldDescription fd;
                            switch (ftype)
                            {
                                case "TEXT":
                                    fd = ArcGIS.Core.Data.DDL.FieldDescription
                                        .CreateStringField(fname,
                                            flen > 0 ? flen : 254);
                                    break;
                                case "LONG":
                                    fd = ArcGIS.Core.Data.DDL.FieldDescription
                                        .CreateIntegerField(fname);
                                    break;
                                default:
                                    fd = new ArcGIS.Core.Data.DDL
                                        .FieldDescription(fname,
                                            FieldType.Double);
                                    break;
                            }
                            fd.AliasName = falias;
                            fds.Add(fd);
                        }
                        var builder = new ArcGIS.Core.Data.DDL.SchemaBuilder(g);
                        builder.Create(new ArcGIS.Core.Data.DDL
                            .TableDescription(cand, fds));
                        if (!builder.Build())
                            throw new InvalidOperationException(
                                "Could not create the table: " + string.Join(
                                    "; ", builder.ErrorMessages));

                        using (var table = g.OpenDataset<Table>(cand))
                        {
                            g.ApplyEdits(() =>
                            {
                                foreach (var row in spec.Rows)
                                    using (var buf = table.CreateRowBuffer())
                                    {
                                        FillBuffer(buf, row);
                                        table.CreateRow(buf).Dispose();
                                    }
                            });
                        }
                        return ws + "\\" + cand;
                    }
                });
            }

            // ---- folder / dBASE path (GP + EditOperation) ---------------
            name = await QueuedTask.Run(() =>
            {
                string cand = name;
                for (int i = 1; i <= 999 && System.IO.File.Exists(
                         System.IO.Path.Combine(ws, cand + ".dbf")); i++)
                    cand = name + "_" + i;
                return cand;
            });
            string path = System.IO.Path.Combine(ws, name + ".dbf");

            var r1 = await RunQuiet("management.CreateTable",
                Geoprocessing.MakeValueArray(ws, name + ".dbf"));
            if (r1.IsFailed)
                throw new InvalidOperationException(string.Join("; ",
                    r1.ErrorMessages.Select(m => m.Text)));
            var r2 = await RunQuiet("management.AddFields",
                Geoprocessing.MakeValueArray(path,
                    HistogramStore.AddFieldsText(spec)));
            if (r2.IsFailed)
                throw new InvalidOperationException(string.Join("; ",
                    r2.ErrorMessages.Select(m => m.Text)));
            // CreateTable on a folder always adds a mandatory placeholder
            // column (dBASE needs at least one field); drop it now that the
            // real schema exists, so exports don't carry a junk Field1.
            await RunQuiet("management.DeleteField",
                Geoprocessing.MakeValueArray(path, "Field1"));

            bool needSessionSave = false;
            // Captured BEFORE any writing: after the fallback EditOperation
            // runs, HasEdits is true by definition.
            bool hadPriorEdits = false;
            try { hadPriorEdits = ArcGIS.Desktop.Core.Project.Current.HasEdits; }
            catch { }
            await QueuedTask.Run(() =>
            {
                using (var ds = OpenDatastore(path))
                using (var table = OpenTable(ds, path))
                {
                    try
                    {
                        // Direct row creation: a shapefile/dBASE datastore is
                        // not transactional, so no Pro edit session is
                        // involved. The old EditOperation +
                        // Project.SaveEditsAsync() path committed EVERY
                        // pending edit project-wide -- including unrelated,
                        // unsaved feature edits the user might have intended
                        // to discard.
                        foreach (var row in spec.Rows)
                            using (var buf = table.CreateRowBuffer())
                            {
                                FillBuffer(buf, row);
                                table.CreateRow(buf).Dispose();
                            }
                    }
                    catch (Exception)
                    {
                        // Fallback: some datastore states still demand the
                        // edit-session path.
                        var op = new ArcGIS.Desktop.Editing.EditOperation
                        {
                            Name = "Write statistics table",
                            ShowProgressor = false,
                        };
                        op.Callback(context =>
                        {
                            foreach (var row in spec.Rows)
                                using (var buf = table.CreateRowBuffer())
                                {
                                    FillBuffer(buf, row);
                                    using (var r = table.CreateRow(buf))
                                        context.Invalidate(r);
                                }
                        }, table);
                        if (!op.Execute())
                            throw new InvalidOperationException(
                                op.ErrorMessage ?? "row insert failed");
                        needSessionSave = true;
                    }
                }
            });
            if (needSessionSave && !hadPriorEdits)
            {
                // Commit the fallback's edit operation -- but never silently
                // commit the user's own unrelated pending edits along with
                // it; when the user already had pending edits, the rows stay
                // pending with them, saved or discarded together.
                try
                {
                    await ArcGIS.Desktop.Core.Project.Current.SaveEditsAsync();
                }
                catch { }
            }
            return path;
        }

        private static bool GdbHas(Geodatabase g, string name)
        {
            try
            {
                using (var t = g.OpenDataset<Table>(name)) return true;
            }
            catch { return false; }
        }

        /// <summary>Open a geodatabase workspace: file gdb via its folder
        /// path, enterprise via the .sde connection file. SplitPath
        /// classifies BOTH as gdb=true; opening an .sde with
        /// FileGeodatabaseConnectionPath failed with an unhelpful
        /// exception.</summary>
        private static Geodatabase OpenGdb(string ws)
        {
            return ws.TrimEnd('\\', '/').EndsWith(".sde",
                    StringComparison.OrdinalIgnoreCase)
                ? new Geodatabase(new DatabaseConnectionFile(new Uri(ws)))
                : new Geodatabase(new FileGeodatabaseConnectionPath(
                    new Uri(ws)));
        }

        private static Datastore OpenDatastore(string tablePath)
        {
            var (ws, _n, gdb) = SplitPath(tablePath);
            if (gdb)
                return OpenGdb(ws);
            return new FileSystemDatastore(new FileSystemConnectionPath(
                new Uri(ws), FileSystemDatastoreType.Shapefile));
        }

        private static Table OpenTable(Datastore ds, string tablePath)
        {
            var (_w, name, gdb) = SplitPath(tablePath);
            if (ds is Geodatabase g) return g.OpenDataset<Table>(name);
            return ((FileSystemDatastore)ds).OpenDataset<Table>(name);
        }
    }
}
