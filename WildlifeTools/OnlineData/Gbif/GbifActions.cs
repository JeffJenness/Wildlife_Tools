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
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using ArcGIS.Core.Data;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.OnlineData.Gbif
{
    /// <summary>
    /// Everything the GBIF Species Occurrences window needs from ArcGIS
    /// Pro: the polygon layers of the active map and their shapes in WGS 84,
    /// a default output path, running the geoprocessing tool that fetches and
    /// writes the records, and the help page. Kept apart from the panel so the panel can be built and
    /// drawn outside Pro.
    /// </summary>
    internal static class GbifActions
    {
        internal const string HelpPage = "data-tools/gbif-species-occurrences.html";
        internal const string Title = "GBIF Species Occurrences";

        /// <summary>One entry of the Study area list.</summary>
        internal sealed class AreaChoice
        {
            internal const int KindWorld = 0, KindExtent = 1, KindLayer = 2;
            public int Kind { get; set; }
            public string Label { get; set; }
            public string Note { get; set; }
            public object Layer { get; set; }             // a FeatureLayer when Kind is KindLayer
            public bool IsSelectedInContents { get; set; }
            public override string ToString() => Label;

            internal static AreaChoice World() => new AreaChoice
            {
                Kind = KindWorld,
                Label = "No study area (the whole world)",
                Note = "Records from anywhere. Choose at least one species or group on the left.",
            };
        }

        /// <summary>The study-area choices for the active map.</summary>
        internal static Task<List<AreaChoice>> ListAreasAsync()
        {
            return QueuedTask.Run(() =>
            {
                var list = new List<AreaChoice> { AreaChoice.World() };
                MapView mv = MapView.Active;
                Map map = mv?.Map;
                if (map == null)
                {
                    return list;
                }
                list.Add(new AreaChoice
                {
                    Kind = AreaChoice.KindExtent,
                    Label = "The current map extent",
                    Note = "The area the active map view shows right now.",
                });
                IReadOnlyList<Layer> selected = null;
                try { selected = mv.GetSelectedLayers(); } catch { }
                foreach (FeatureLayer fl in map.GetLayersAsFlattenedList().OfType<FeatureLayer>())
                {
                    try
                    {
                        if (fl.ShapeType != ArcGIS.Core.CIM.esriGeometryType.esriGeometryPolygon)
                        {
                            continue;
                        }
                        long sel = 0;
                        try { sel = fl.GetSelection()?.GetCount() ?? 0; } catch { }
                        list.Add(new AreaChoice
                        {
                            Kind = AreaChoice.KindLayer,
                            Layer = fl,
                            Label = fl.Name,
                            IsSelectedInContents = selected != null && selected.Contains(fl),
                            Note = sel > 0
                                ? sel.ToString("N0", CultureInfo.CurrentCulture) + (sel == 1 ? " polygon is" : " polygons are")
                                  + " selected in this layer, and only " + (sel == 1 ? "it" : "those") + " will be the study area."
                                : "No polygons are selected, so every polygon the layer shows is the study area"
                                  + (string.IsNullOrWhiteSpace(fl.DefinitionQuery) ? "." : " (its definition query is honored)."),
                        });
                    }
                    catch
                    {
                        // a broken layer is simply not offered
                    }
                }
                return list;
            });
        }

        /// <summary>The study area as polygons in WGS 84: each a list of
        /// rings, outer ring first. Polygons are dissolved together first,
        /// so overlapping or adjoining ones become one shape.</summary>
        internal static Task<List<List<List<(double Lon, double Lat)>>>> AreaPolygonsAsync(AreaChoice choice)
        {
            return QueuedTask.Run(() =>
            {
                var result = new List<List<List<(double Lon, double Lat)>>>();
                var shapes = new List<Polygon>();
                if (choice.Kind == AreaChoice.KindExtent)
                {
                    Envelope env = MapView.Active?.Extent
                        ?? throw new InvalidOperationException("There is no active map view.");
                    Polygon box = PolygonBuilderEx.CreatePolygon(env, env.SpatialReference);
                    shapes.Add(box);
                }
                else if (choice.Kind == AreaChoice.KindLayer && choice.Layer is FeatureLayer fl)
                {
                    // the selected features when there is a selection, otherwise all that the layer shows
                    QueryFilter qf = null;
                    using (Selection sel = fl.GetSelection())
                    {
                        if (sel != null && sel.GetCount() > 0)
                        {
                            qf = new QueryFilter { ObjectIDs = sel.GetObjectIDs() };
                        }
                    }
                    using (RowCursor cur = global::WildlifeTools.LayerRows.Search(fl, qf))
                    {
                        while (cur.MoveNext())
                        {
                            using (var f = cur.Current as Feature)
                            {
                                if (f?.GetShape() is Polygon p && !p.IsEmpty)
                                {
                                    shapes.Add(p);
                                }
                            }
                        }
                    }
                }
                if (shapes.Count == 0)
                {
                    return result;
                }

                SpatialReference wgs84 = SpatialReferences.WGS84;
                var projected = new List<Polygon>();
                foreach (Polygon raw in shapes)
                {
                    Polygon p = raw;
                    SpatialReference sr = p.SpatialReference;
                    try
                    {
                        // straighten true curves, and add vertices along long straight
                        // edges so they stay in place when the shape is unprojected
                        Envelope e = p.Extent;
                        double step = Math.Max(e.Width, e.Height) / 200.0;
                        if (step > 0 && (p.HasCurves || (sr != null && !sr.IsGeographic && p.PointCount < 400)))
                        {
                            p = (Polygon)GeometryEngine.Instance.DensifyByLength(p, step);
                        }
                    }
                    catch
                    {
                        p = raw;
                    }
                    if (sr != null && !sr.IsEqual(wgs84))
                    {
                        ProjectionTransformation pt = ProjectionTransformation.Create(sr, wgs84, p.Extent);
                        p = (Polygon)GeometryEngine.Instance.ProjectEx(p, pt);
                    }
                    if (p != null && !p.IsEmpty)
                    {
                        projected.Add(p);
                    }
                }
                if (projected.Count == 0)
                {
                    return result;
                }
                Geometry merged = projected.Count == 1
                    ? GeometryEngine.Instance.SimplifyAsFeature(projected[0], true)
                    : GeometryEngine.Instance.Union(projected);
                foreach (Geometry g in GeometryEngine.Instance.MultipartToSinglePart(merged))
                {
                    if (!(g is Polygon one) || one.IsEmpty)
                    {
                        continue;
                    }
                    var rings = new List<List<(double Lon, double Lat)>>();
                    foreach (ReadOnlySegmentCollection part in one.Parts)
                    {
                        var ring = new List<(double Lon, double Lat)>();
                        foreach (Segment s in part)
                        {
                            ring.Add((s.StartPoint.X, s.StartPoint.Y));
                        }
                        if (ring.Count >= 3)
                        {
                            rings.Add(ring);
                        }
                    }
                    if (rings.Count == 0)
                    {
                        continue;
                    }
                    // the outer ring is the largest; the rest are its holes
                    rings = rings.OrderByDescending(r => Math.Abs(GbifFilter.SignedArea(r))).ToList();
                    result.Add(rings);
                }
                return result;
            });
        }

        /// <summary>A feature class path in the project's default
        /// geodatabase, with a name not yet in use.</summary>
        internal static Task<string> DefaultOutputPathAsync(string baseName)
        {
            return QueuedTask.Run(() =>
            {
                string gdb = Project.Current?.DefaultGeodatabasePath;
                if (string.IsNullOrEmpty(gdb))
                {
                    return "";
                }
                string name = baseName;
                try
                {
                    using (var g = new Geodatabase(new FileGeodatabaseConnectionPath(new Uri(gdb))))
                    {
                        var used = new HashSet<string>(g.GetDefinitions<FeatureClassDefinition>().Select(d =>
                        {
                            using (d) { return d.GetName(); }
                        }), StringComparer.OrdinalIgnoreCase);
                        for (int i = 2; used.Contains(name) && i < 1000; i++)
                        {
                            name = baseName + "_" + i.ToString(CultureInfo.InvariantCulture);
                        }
                    }
                }
                catch
                {
                }
                return Path.Combine(gdb, name);
            });
        }

        // =====================================================================
        //  the geoprocessing tool
        // =====================================================================
        internal const string ToolName = "GBIFSpeciesOccurrences";

        /// <summary>The full path of the toolbox on this computer, for the
        /// ImportToolbox line of a script.</summary>
        internal static string ToolboxPath => global::WildlifeTools.ToolboxPaths.Pyt;
        internal const string MethodFetch = "Fetch directly";
        internal const string MethodDownload = "Request a GBIF download";
        internal const string MethodLoad = "Load an existing GBIF download";

        /// <summary>What the window hands the geoprocessing tool. The
        /// order of <see cref="ToArgs"/> is the tool's parameter order.</summary>
        internal sealed class ToolRequest
        {
            public List<string> Taxa = new List<string>();      // "Name (key)"
            public AreaChoice Area;                             // null or KindWorld = no study area
            public string Country, StateProvince;
            public bool IncludeNoCoordinates;
            public int? YearFrom, YearTo;
            public List<int> Months = new List<int>();          // empty = all
            public List<string> RecordKinds = new List<string>();
            public bool NoLocationProblems, NoCentroids, PresentOnly, NoNonCommercial, ImagesOnly;
            public double? MaxUncertaintyMeters;
            public List<string> ExcludedSources = new List<string>();
            public string Method = MethodFetch;
            public string ExistingDownload;
            public int MaxWaitMinutes = 1440;
            public string OutputPath;
        }

        /// <summary>A multivalue argument: values joined with semicolons,
        /// a value that holds a space in single quotes.</summary>
        private static string Multi(IEnumerable<string> values) =>
            string.Join(";", values.Select(v => v.IndexOf(' ') >= 0 ? "'" + v + "'" : v));

        private static string Flag(bool b) => b ? "true" : "false";

        /// <summary>Runs the GBIF Species Occurrences geoprocessing tool,
        /// which fetches the records, writes the feature class and adds it
        /// to the map. The run is recorded in the Geoprocessing History, so
        /// its Python command can be copied from there. <paramref name="onMessage"/>
        /// gets each message of the tool with its severity (0 information,
        /// 1 warning, 2 error); <paramref name="onProgress"/> the progress
        /// label and percent as they change (either may be null / -1).
        /// Both may be called on a background thread.</summary>
        internal static async Task<IGPResult> RunToolAsync(ToolRequest q, Action<string, int> onMessage,
            Action<string, int> onProgress, CancellationToken ct)
        {
            object layerArg = "";
            object extentArg = "";
            if (q.Area != null && q.Area.Kind == AreaChoice.KindLayer && q.Area.Layer is FeatureLayer fl)
            {
                layerArg = fl;
            }
            else if (q.Area != null && q.Area.Kind == AreaChoice.KindExtent)
            {
                extentArg = await QueuedTask.Run(() => (object)(MapView.Active?.Extent
                    ?? throw new InvalidOperationException("There is no active map view.")));
            }
            CultureInfo inv = CultureInfo.InvariantCulture;
            IReadOnlyList<string> args = Geoprocessing.MakeValueArray(
                string.Join("; ", q.Taxa),
                layerArg,
                extentArg,
                q.Country ?? "",
                q.StateProvince ?? "",
                Flag(q.IncludeNoCoordinates),
                q.YearFrom.HasValue ? q.YearFrom.Value.ToString(inv) : "",
                q.YearTo.HasValue ? q.YearTo.Value.ToString(inv) : "",
                string.Join(";", q.Months.Select(m => m.ToString(inv))),
                Multi(q.RecordKinds),
                Flag(q.NoLocationProblems),
                Flag(q.NoCentroids),
                Flag(q.PresentOnly),
                Flag(q.NoNonCommercial),
                Flag(q.ImagesOnly),
                q.MaxUncertaintyMeters.HasValue ? q.MaxUncertaintyMeters.Value.ToString("R", inv) : "",
                string.Join(";", q.ExcludedSources),
                q.Method,
                q.ExistingDownload ?? "",
                q.MaxWaitMinutes.ToString(inv),
                q.OutputPath);

            return await OnlineToolRunner.RunAsync(ToolName, args, onMessage, onProgress, ct);
        }

        /// <summary>The sources of a written feature class (record count
        /// and name per GBIF dataset key), read back from its datasetKey
        /// and datasetName fields, for Save the source list.</summary>
        internal static Task<GbifFeatureWriter.Result> SummarizeAsync(string gdb, string name)
        {
            return QueuedTask.Run(() =>
            {
                var r = new GbifFeatureWriter.Result();
                using (var g = new Geodatabase(new FileGeodatabaseConnectionPath(new Uri(gdb))))
                using (FeatureClass fc = g.OpenDataset<FeatureClass>(name))
                using (FeatureClassDefinition def = fc.GetDefinition())
                {
                    int iKey = def.FindField("datasetKey"), iName = def.FindField("datasetName");
                    int iShape = def.FindField(def.GetShapeField());
                    var qf = new QueryFilter { SubFields = "datasetKey,datasetName," + def.GetShapeField() };
                    using (RowCursor cur = fc.Search(qf, true))
                    {
                        while (cur.MoveNext())
                        {
                            Row row = cur.Current;
                            r.Written++;
                            if (iShape >= 0 && (row[iShape] == null || (row[iShape] is Geometry gm && gm.IsEmpty)))
                            {
                                r.WrittenWithoutLocation++;
                            }
                            string key = iKey >= 0 ? row[iKey] as string : null;
                            if (string.IsNullOrEmpty(key))
                            {
                                continue;
                            }
                            r.ByDataset[key] = r.ByDataset.TryGetValue(key, out long c) ? c + 1 : 1;
                            string nm = iName >= 0 ? row[iName] as string : null;
                            if (!string.IsNullOrEmpty(nm))
                            {
                                r.DatasetNames[key] = nm;
                            }
                        }
                    }
                }
                return r;
            });
        }

        internal static void Info(string message) => MessageBox.Show(message, Title);

        /// <summary>Asks where to save the output feature class. Null when cancelled.</summary>
        internal static string BrowseOutputFeatureClass(string current, string title) =>
            OnlineBrowse.FeatureClass(current, title, Title);

        internal static void Help() => global::WildlifeTools.WebHelp.Open(HelpPage);
    }
}
