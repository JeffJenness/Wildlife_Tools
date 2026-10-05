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
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using ArcGIS.Core.CIM;
using ArcGIS.Core.Data;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Catalog;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using WildlifeTools.PolarPlots;
using EsriPolyline = ArcGIS.Core.Geometry.Polyline;
using WpfPolyline = System.Windows.Shapes.Polyline;
using WpfLine = System.Windows.Shapes.Line;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// The interactive Bottleneck Analysis window: pick a corridor and two
    /// wildland blocks (from map layers, feature classes on disk, named
    /// groups, individual polygons by OID, SQL expressions, or by clicking
    /// the map), run the medial-axis maximin engine (the BottleneckAnalysis
    /// GP tool headlessly), then explore the width profile -- the threshold
    /// is adjustable by typing, clicking the graph, or dragging the dashed
    /// line, with below-threshold stretches red on both the graph and the
    /// map. Port of the CorridorDesigner Evaluation Tools' Bottleneck
    /// Analysis (frmBottleneck + frmGraph).
    /// </summary>
    public partial class BottleneckWindow : ArcGIS.Desktop.Framework.Controls.ProWindow,
        ArcGIS.Desktop.Framework.DragDrop.IDropTarget
    {
        // ---- input model ----------------------------------------------------
        /// <summary>A source: a map layer OR a feature class on disk.</summary>
        private sealed class SourceChoice
        {
            public FeatureLayer Layer;
            public string Path;           // set for disk sources
            public string Name;
            public override string ToString() => Name;
            public string GpValue => Layer != null ? Layer.Name : Path;
            public bool SameSource(SourceChoice o) =>
                o != null && (Layer != null
                    ? ReferenceEquals(Layer, o.Layer)
                    : string.Equals(Path, o.Path,
                        StringComparison.OrdinalIgnoreCase));
        }

        private sealed class Slot
        {
            public char Id;
            public ComboBox Source;
            public TextBlock Status;
            public Button Sql;
            public string Where;          // SQL... expression; null = none
        }

        private Slot[] _slots;

        // ---- results state --------------------------------------------------
        private double[] _stations;
        private double[] _widths;
        private EsriPolyline _routeWorking;
        private List<MapPoint> _routePts;
        private double _threshold = double.NaN;
        private bool _thresholdFromCode;
        private bool _draggingThreshold;
        private string _scratchDir;
        private string _routeFc, _pointsFc;
        private string _recordJson;      // the linkage record, for stamping
                                         // onto datasets the user SAVES
        private JsonElement _stats;
        private bool _haveStats;
        private const string RouteLayerName =
            "Bottleneck Route (Temporary In-Memory Workspace)";
        private const string PointsLayerName =
            "Bottleneck Width Points (Temporary In-Memory Workspace)";
        private readonly List<IDisposable> _overlays = new List<IDisposable>();

        public BottleneckWindow()
        {
            InitializeComponent();
            DataContext = this;      // lets the framework's drag-drop
                                     // behavior find IDropTarget below
            // IsDropTarget alone only MARKS an element; the handler must
            // be registered explicitly (the missing piece behind the
            // "prohibited" drop cursor Jeff hit) -- and a plain-WPF
            // fallback catches drags that arrive as ordinary OLE drops
            foreach (var cbo in new[] { cboCorridor, cboLayerA, cboLayerB })
            {
                ArcGIS.Desktop.Framework.DragDrop.DragDrop
                    .SetDropHandler(cbo, this);
                cbo.AllowDrop = true;
                cbo.PreviewDragOver += OnWpfDragOver;
                cbo.PreviewDrop += OnWpfDrop;
            }
            // the WHOLE WINDOW accepts drops -- requiring a pixel-precise
            // hit on a combo showed users a prohibited cursor over 95% of
            // the dialog; drops are routed to the row they land on, else
            // the first empty slot
            ArcGIS.Desktop.Framework.DragDrop.DragDrop
                .SetIsDropTarget(this, true);
            ArcGIS.Desktop.Framework.DragDrop.DragDrop
                .SetDropHandler(this, this);
            AllowDrop = true;
            PreviewDragOver += OnWpfDragOver;
            PreviewDrop += OnWpfDrop;
            _slots = new[]
            {
                new Slot { Id = 'C', Source = cboCorridor,
                           Status = lblStatusC, Sql = btnSqlC },
                new Slot { Id = 'A', Source = cboLayerA,
                           Status = lblStatusA, Sql = btnSqlA },
                new Slot { Id = 'B', Source = cboLayerB,
                           Status = lblStatusB, Sql = btnSqlB },
            };
            Loaded += (_, __) =>
            {
                PopulateLayers();
                // the per-row status labels track the MAP SELECTION live
                if (_selToken == null)
                    _selToken = ArcGIS.Desktop.Mapping.Events
                        .MapSelectionChangedEvent.Subscribe(
                            OnMapSelectionChanged);
                // when PRO closes with this window still open, stop all
                // worker-thread activity IMMEDIATELY -- dataset reads
                // racing Pro's teardown are the leading suspect for the
                // won't-close hangs
                if (_appClosingToken == null)
                    _appClosingToken = ArcGIS.Desktop.Framework.Events
                        .ApplicationClosingEvent.Subscribe(args =>
                        {
                            _closedDown = true;
                            try { Close(); } catch { }
                            return Task.CompletedTask;
                        });
            };
            Closed += (_, __) =>
            {
                _closedDown = true;
                // shutdown hygiene: nothing of this window may linger
                ClearOverlays();
                if (_selToken != null)
                {
                    try
                    {
                        ArcGIS.Desktop.Mapping.Events
                            .MapSelectionChangedEvent.Unsubscribe(_selToken);
                    }
                    catch { }
                    _selToken = null;
                }
                if (_appClosingToken != null)
                {
                    try
                    {
                        ArcGIS.Desktop.Framework.Events
                            .ApplicationClosingEvent
                            .Unsubscribe(_appClosingToken);
                    }
                    catch { }
                    _appClosingToken = null;
                }
            };
        }

        private ArcGIS.Core.Events.SubscriptionToken _selToken;
        private ArcGIS.Core.Events.SubscriptionToken _appClosingToken;

        private void OnMapSelectionChanged(
            ArcGIS.Desktop.Mapping.Events.MapSelectionChangedEventArgs a)
        {
            // arrives off the UI thread; InvokeAsync, never Invoke
            Dispatcher.InvokeAsync(() =>
            {
                if (_closedDown) return;
                foreach (var s in _slots) _ = UpdateSlotStatus(s);
            });
        }

        private Slot SlotOf(object sourceCombo) =>
            _slots.First(s => ReferenceEquals(s.Source, sourceCombo));

        // =====================================================================
        // sources
        // =====================================================================
        private void PopulateLayers()
        {
            var map = MapView.Active?.Map;
            var layers = map == null
                ? new List<FeatureLayer>()
                : map.GetLayersAsFlattenedList().OfType<FeatureLayer>()
                    .Where(l => l.ShapeType
                        == esriGeometryType.esriGeometryPolygon)
                    .ToList();
            foreach (var slot in _slots)
            {
                var keepDisk = (slot.Source.ItemsSource as List<SourceChoice>)
                    ?.Where(c => c.Path != null).ToList()
                    ?? new List<SourceChoice>();
                var selected = slot.Source.SelectedItem as SourceChoice;
                var items = layers.Select(l => new SourceChoice
                { Layer = l, Name = l.Name }).Concat(keepDisk).ToList();
                slot.Source.ItemsSource = items;
                if (selected != null)
                    slot.Source.SelectedItem =
                        items.FirstOrDefault(i => i.SameSource(selected));
            }
            if (layers.Count == 0)
                txtStatus.Text = "No polygon layers in the active map -- "
                    + "use Browse to pick feature classes from disk.";
        }

        private void OnSourceChanged(object sender, SelectionChangedEventArgs e)
        {
            var slot = SlotOf(sender);
            var src = slot.Source.SelectedItem as SourceChoice;
            slot.Where = null;    // an expression never survives a source change
            slot.Sql.IsEnabled = src != null;
            _ = UpdateSlotStatus(slot);
        }

        /// <summary>Workspace + dataset name from a catalog path, honoring
        /// the standing rule: ".gdb"/".sde" must be followed by a separator
        /// (or end the string) to count, so feature-dataset paths and
        /// folders merely NAMED like a gdb both resolve correctly.</summary>
        private static (string ws, string name, bool gdb) SplitFcPath(string path)
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
                    if (end == low.Length) break;     // path IS the gdb
                    if (low[end] == '\\')
                        return (norm.Substring(0, end),
                                norm.Substring(norm.LastIndexOf('\\') + 1),
                                true);
                    at = end;
                }
            }
            return (System.IO.Path.GetDirectoryName(norm),
                    System.IO.Path.GetFileNameWithoutExtension(norm), false);
        }

        private static ArcGIS.Core.Data.Table OpenTableFromPath(
            string path, out Datastore ds)
        {
            var (ws, name, gdb) = SplitFcPath(path);
            if (gdb)
            {
                var g = new Geodatabase(
                    new FileGeodatabaseConnectionPath(new Uri(ws)));
                ds = g;
                return g.OpenDataset<FeatureClass>(name);
            }
            var f = new FileSystemDatastore(new FileSystemConnectionPath(
                new Uri(ws), FileSystemDatastoreType.Shapefile));
            ds = f;
            return f.OpenDataset<FeatureClass>(name);
        }

        private static SolidColorBrush Frozen(byte r, byte g, byte b)
        {
            var br = new SolidColorBrush(Color.FromRgb(r, g, b));
            br.Freeze();
            return br;
        }
        private static readonly SolidColorBrush StatusBlue =
            Frozen(31, 119, 180);
        private static readonly SolidColorBrush StatusRed =
            Frozen(198, 40, 40);

        /// <summary>Jeff's one-glance input feedback: BLUE when the row
        /// resolves to a single polygon (the ready state), RED with a
        /// "Select 1" instruction when it does not. The map selection or a
        /// SQL... expression narrows a multi-polygon source.</summary>
        private async Task UpdateSlotStatus(Slot slot)
        {
            var src = slot.Source.SelectedItem as SourceChoice;
            var lbl = slot.Status;
            if (src == null) { lbl.Text = ""; return; }
            string where = slot.Where;
            string name = src.Name.Replace("  (on disk)", "");
            long total = 0, sel = 0;
            bool ok = false, badSql = false;
            await QueuedTask.Run(() =>
            {
                if (_closedDown) return;
                try
                {
                    Datastore ds = null;
                    ArcGIS.Core.Data.Table t = null;
                    try
                    {
                        t = src.Layer != null
                            ? src.Layer.GetTable()
                            : OpenTableFromPath(src.Path, out ds);
                        // a layer is counted AS THE MAP SHOWS IT (its
                        // definition query applied); a path has none
                        total = src.Layer != null
                            ? LayerRows.Count(src.Layer) : t.GetCount();
                        if (!string.IsNullOrEmpty(where))
                        {
                            try
                            {
                                // OID-only subfields: the recycling cursor
                                // otherwise materializes every row WITH its
                                // polygon geometry just to be counted --
                                // a visible stall per selection change on
                                // large layers.
                                var qf = new QueryFilter
                                {
                                    WhereClause = where,
                                    SubFields = t.GetDefinition()
                                        .GetObjectIDField(),
                                };
                                using (var cur = src.Layer != null
                                    ? LayerRows.Search(src.Layer, qf, true)
                                    : t.Search(qf, true))
                                    while (cur.MoveNext())
                                        using (cur.Current) sel++;
                            }
                            catch { badSql = true; }
                        }
                        else if (src.Layer != null)
                        {
                            sel = src.Layer.SelectionCount;
                        }
                        ok = true;
                    }
                    finally { t?.Dispose(); ds?.Dispose(); }
                }
                catch { }
            });
            if (_closedDown) return;
            // stale guard: the source or expression changed while counting
            if (!ReferenceEquals(slot.Source.SelectedItem, src)
                || slot.Where != where) return;
            string text;
            bool ready = false;
            if (!ok)
                text = string.Format("Could not read '{0}'", name);
            else if (badSql)
                text = string.Format(
                    "The SQL expression is not valid for '{0}': Select 1",
                    name);
            else if (total == 0)
                text = string.Format("No polygons in '{0}'", name);
            else if (!string.IsNullOrEmpty(where))
            {
                if (sel == 1) { ready = true; text = "Single polygon selected"; }
                else if (sel == 0)
                    text = string.Format("No polygons match the SQL "
                        + "expression in {0}: Select 1", name);
                else
                    text = string.Format(CultureInfo.InvariantCulture,
                        "{0:#,##0} of {1:#,##0} Polygons currently selected "
                        + "in {2}: Select 1", sel, total, name);
            }
            else if (total == 1) { ready = true; text = "Single polygon"; }
            else if (sel == 1) { ready = true; text = "Single polygon selected"; }
            else if (sel == 0)
                text = string.Format(CultureInfo.InvariantCulture,
                    "{0:#,##0} Polygons in {1}: Select 1", total, name);
            else
                text = string.Format(CultureInfo.InvariantCulture,
                    "{0:#,##0} of {1:#,##0} Polygons currently selected "
                    + "in {2}: Select 1", sel, total, name);
            lbl.Foreground = ready ? StatusBlue : StatusRed;
            lbl.Text = text;
        }

        /// <summary>The where clause a slot contributes to the run: its
        /// SQL... expression when set, else the layer's current map
        /// selection (by OID), else empty = all features.</summary>
        private async Task<string> ResolveWhereAsync(Slot slot)
        {
            if (!string.IsNullOrEmpty(slot.Where)) return slot.Where;
            var src = slot.Source.SelectedItem as SourceChoice;
            if (src?.Layer == null) return "";
            return await QueuedTask.Run(() =>
            {
                try
                {
                    if (_closedDown || src.Layer.SelectionCount == 0)
                        return "";
                    var oids = src.Layer.GetSelection().GetObjectIDs()
                        .ToList();
                    if (oids.Count == 0) return "";
                    string oidField;
                    using (var t = src.Layer.GetTable())
                        oidField = t.GetDefinition().GetObjectIDField();
                    return oids.Count == 1
                        ? string.Format("{0} = {1}", oidField, oids[0])
                        : string.Format("{0} IN ({1})", oidField,
                            string.Join(",", oids));
                }
                catch { return ""; }
            });
        }

        // ---- browse ---------------------------------------------------------
        private void OnBrowseC(object sender, RoutedEventArgs e) =>
            Browse(_slots[0]);
        private void OnBrowseA(object sender, RoutedEventArgs e) =>
            Browse(_slots[1]);
        private void OnBrowseB(object sender, RoutedEventArgs e) =>
            Browse(_slots[2]);

        private async void Browse(Slot slot)
        {
            var dlg = new OpenItemDialog
            {
                Title = "Choose a polygon feature class",
                MultiSelect = false,
                Filter = ItemFilters.FeatureClasses_All,
            };
            if (dlg.ShowDialog() != true) return;
            var item = dlg.Items?.FirstOrDefault();
            if (item == null) return;
            string path = item.Path;
            // confirm it is polygon before accepting
            bool isPolygon = await QueuedTask.Run(() =>
            {
                try
                {
                    Datastore ds = null;
                    ArcGIS.Core.Data.Table t = null;
                    try
                    {
                        t = OpenTableFromPath(path, out ds);
                        var fcd = t.GetDefinition()
                            as FeatureClassDefinition;
                        return fcd != null && fcd.GetShapeType()
                            == GeometryType.Polygon;
                    }
                    finally { t?.Dispose(); ds?.Dispose(); }
                }
                catch { return false; }
            });
            if (!isPolygon)
            {
                txtStatus.Text = string.Format(
                    "'{0}' is not a polygon feature class.", item.Name);
                return;
            }
            var items = (slot.Source.ItemsSource as List<SourceChoice>)
                ?? new List<SourceChoice>();
            var existing = items.FirstOrDefault(i => i.Path != null
                && string.Equals(i.Path, path,
                    StringComparison.OrdinalIgnoreCase));
            if (existing == null)
            {
                existing = new SourceChoice
                { Path = path, Name = item.Name + "  (on disk)" };
                items = new List<SourceChoice>(items) { existing };
                slot.Source.ItemsSource = items;
            }
            slot.Source.SelectedItem = existing;
        }

        // ---- SQL / query builder --------------------------------------------
        private void OnSqlC(object sender, RoutedEventArgs e) =>
            OpenSql(_slots[0]);
        private void OnSqlA(object sender, RoutedEventArgs e) =>
            OpenSql(_slots[1]);
        private void OnSqlB(object sender, RoutedEventArgs e) =>
            OpenSql(_slots[2]);

        private async void OpenSql(Slot slot)
        {
            var src = slot.Source.SelectedItem as SourceChoice;
            if (src == null) return;

            // Esri's interactive Query Builder control binds only to a MAP
            // MEMBER, so disk sources used to get a plain SQL text box
            // (Jeff, 2026-08-20). Stand up a TEMPORARY invisible layer on
            // the active map for the dialog's lifetime instead -- the
            // builder then offers the same fields/unique values as for a
            // map layer -- and remove it in the finally, even on failure.
            // Only when no map is open (or the layer cannot be created)
            // does the text-box fallback remain.
            MapMember member = src.Layer;
            FeatureLayer temp = null;
            var map = MapView.Active?.Map;
            if (member == null && !string.IsNullOrEmpty(src.Path)
                && map != null)
            {
                try
                {
                    temp = await QueuedTask.Run(() =>
                    {
                        var p = new LayerCreationParams(new Uri(src.Path))
                        {
                            IsVisible = false,
                            Name = src.Name + " (temporary - SQL builder)",
                        };
                        return LayerFactory.Instance
                            .CreateLayer<FeatureLayer>(p, map);
                    });
                    member = temp;
                }
                catch { temp = null; }   // fall back to the text box
            }
            try
            {
                if (_closedDown) return;
                var win = new BottleneckQueryWindow(member, slot.Where,
                    src.Name)
                { Owner = this };
                if (win.ShowDialog() == true)
                {
                    string expr = win.Expression;
                    slot.Where = string.IsNullOrWhiteSpace(expr)
                        ? null : expr;
                    _ = UpdateSlotStatus(slot);
                }
            }
            finally
            {
                if (temp != null)
                    await QueuedTask.Run(() =>
                    {
                        try { map.RemoveLayer(temp); } catch { }
                    });
            }
        }

        // =====================================================================
        // run
        // =====================================================================
        private async void OnRun(object sender, RoutedEventArgs e)
        {
            var cor = cboCorridor.SelectedItem as SourceChoice;
            var la = cboLayerA.SelectedItem as SourceChoice;
            var lb = cboLayerB.SelectedItem as SourceChoice;
            if (cor == null || la == null || lb == null)
            {
                txtRunStatus.Text =
                    "Pick the corridor and both wildland blocks first.";
                return;
            }
            string wc = await ResolveWhereAsync(_slots[0]);
            string wa = await ResolveWhereAsync(_slots[1]);
            string wb = await ResolveWhereAsync(_slots[2]);
            if (la.SameSource(lb) && wa == wb)
            {
                // the map selection is PER LAYER, so the same layer in
                // both block rows needs SQL... to tell them apart
                txtRunStatus.Text = "Blocks A and B resolve to the same "
                    + "polygons -- use the SQL... buttons to select a "
                    + "different single polygon for each block.";
                return;
            }
            string toolPath = ToolboxPaths.ToolPath("BottleneckAnalysis");
            if (toolPath == null)
            {
                txtRunStatus.Text = "Toolbox not found next to the add-in.";
                return;
            }

            btnRun.IsEnabled = false;
            prgRun.Visibility = Visibility.Visible;
            txtRunStatus.Text = "Running the Bottleneck Analysis -- "
                + "usually a minute or two on a real corridor...";
            var sw = System.Diagnostics.Stopwatch.StartNew();
            try
            {
                // quick-look outputs live in the MEMORY workspace -- no
                // stray shapefiles in the Windows profile, gone when Pro
                // closes; Save... writes them wherever the user chooses.
                // New names stay LOCAL until the run succeeds: overwriting
                // the fields (and removing the previous layers) up front
                // meant a FAILED rerun left the old graph showing with
                // Save buttons pointing at never-created datasets, and the
                // previous quick-look layers already gone.
                string stampName = DateTime.Now.ToString("HHmmss");
                string routeFc = @"memory\Bottleneck_Route_" + stampName;
                string pointsFc = @"memory\Bottleneck_Points_" + stampName;
                _scratchDir = System.IO.Path.Combine(
                    System.IO.Path.GetTempPath(),
                    "WildlifeTools_Bottleneck");
                Directory.CreateDirectory(_scratchDir);
                string profileJson = System.IO.Path.Combine(_scratchDir,
                    "profile_" + stampName + ".json");

                double thr;
                bool hasThr = double.TryParse(txtThreshold.Text,
                    NumberStyles.Float, CultureInfo.InvariantCulture,
                    out thr) && thr > 0;
                var args = Geoprocessing.MakeValueArray(
                    cor.GpValue, wc,
                    la.GpValue, wa,
                    lb.GpValue, wb,
                    hasThr ? (object)thr : "",
                    routeFc, pointsFc, "",
                    ParseIntOr(txtStations.Text, 1000),
                    ParseOrEmpty(txtBoundaryStep.Text),
                    ParseOrEmpty(txtSimplify.Text),
                    profileJson);
                IGPResult result;
                try
                {
                    // flags overload: also records the run in History
                    result = await Geoprocessing.ExecuteToolAsync(
                        toolPath, (IEnumerable<string>)args, null,
                        (System.Threading.CancellationToken?)null, null,
                        GPExecuteToolFlags.Default
                        | GPExecuteToolFlags.AddToHistory);
                }
                catch (NullReferenceException)
                {
                    // the flags overloads have misfired before
                    // (see the Land Facet window); plain call works
                    result = await Geoprocessing.ExecuteToolAsync(
                        toolPath, args);
                }
                if (result.IsFailed || !File.Exists(profileJson))
                {
                    // The previous run's layers, datasets, graph and Save
                    // buttons are all still intact -- nothing was replaced.
                    txtRunStatus.Text = "Failed: " + string.Join("; ",
                        result.ErrorMessages.Select(m => m.Text).Take(3));
                    return;
                }
                // Success: retire the previous run's quick-look layers
                // (still findable through the OLD field values), then
                // commit the new names.
                await RemovePreviousRunLayers();
                _routeFc = routeFc;
                _pointsFc = pointsFc;
                LoadProfile(profileJson);
                await RenameQuickLookLayers();
                txtRunStatus.Text = string.Format(CultureInfo
                    .InvariantCulture,
                    "Done in {0:0} s. The route and station points were "
                    + "added to the map (temporary, in-memory -- use the "
                    + "Save buttons to keep them); drag the threshold "
                    + "line to explore.", sw.Elapsed.TotalSeconds);
                txtGraphHint.Visibility = Visibility.Collapsed;
                foreach (var b in new[] { btnSaveRoute, btnSavePoints,
                                          btnSaveSegments, btnSaveGraph,
                                          btnAddToLayout, btnCopyStats })
                    b.IsEnabled = true;
                DrawGraph();
                RenderStats();
                await UpdateOverlayAsync();
            }
            catch (Exception ex)
            {
                txtRunStatus.Text = "Failed: " + ex.Message;
            }
            finally
            {
                prgRun.Visibility = Visibility.Collapsed;
                btnRun.IsEnabled = true;
            }
        }

        /// <summary>Remove the layers the previous run auto-added (they
        /// point at this window's memory datasets).</summary>
        private async Task RemovePreviousRunLayers()
        {
            string prevRoute = _routeFc, prevPoints = _pointsFc;
            var map = MapView.Active?.Map;
            if (map == null) return;
            string RName(string p) =>
                p?.Substring(p.LastIndexOf('\\') + 1);
            var names = new[] { RName(prevRoute), RName(prevPoints),
                                RouteLayerName, PointsLayerName }
                .Where(n => n != null).ToList();
            await QueuedTask.Run(() =>
            {
                var doomed = map.GetLayersAsFlattenedList()
                    .OfType<FeatureLayer>()
                    .Where(l => names.Contains(l.Name)).ToList();
                if (doomed.Count > 0) map.RemoveLayers(doomed);
            });
        }

        /// <summary>Rename the freshly auto-added quick-look layers so
        /// their temporary nature is visible in the Contents pane.</summary>
        private async Task RenameQuickLookLayers()
        {
            var map = MapView.Active?.Map;
            if (map == null) return;
            string routeDs = _routeFc.Substring(
                _routeFc.LastIndexOf('\\') + 1);
            string ptsDs = _pointsFc.Substring(
                _pointsFc.LastIndexOf('\\') + 1);
            await QueuedTask.Run(() =>
            {
                foreach (var l in map.GetLayersAsFlattenedList()
                         .OfType<FeatureLayer>())
                {
                    if (l.Name == routeDs) l.SetName(RouteLayerName);
                    else if (l.Name == ptsDs) l.SetName(PointsLayerName);
                }
            });
        }

        /// <summary>Write the analysis record into a SAVED dataset's
        /// metadata (Supplemental Information -- the same carrier every
        /// tool in the chain uses, read by the Corridor Analysis Data
        /// Report). The quick-look memory datasets cannot carry metadata,
        /// so only what the user chooses to save gets stamped.</summary>
        private async Task StampSavedDataset(string path)
        {
            if (string.IsNullOrWhiteSpace(_recordJson)) return;
            string json = _recordJson;
            await QueuedTask.Run(() =>
            {
                try
                {
                    var item = ArcGIS.Desktop.Core.ItemFactory.Instance
                        .Create(path);
                    var md = item as ArcGIS.Desktop.Core.IMetadata;
                    if (md == null || !md.CanEdit()) return;
                    string xml = md.GetXml();
                    if (string.IsNullOrWhiteSpace(xml))
                        xml = "<metadata xml:lang=\"en\"></metadata>";
                    var doc = new System.Xml.XmlDocument();
                    doc.LoadXml(xml);
                    var root = doc.DocumentElement;
                    var dataId = root.SelectSingleNode("dataIdInfo")
                        ?? root.AppendChild(doc.CreateElement("dataIdInfo"));
                    var supp = dataId.SelectSingleNode("suppInfo")
                        ?? dataId.AppendChild(doc.CreateElement("suppInfo"));
                    supp.InnerText =
                        "JENNESS-LANDFACET-LINKAGE: " + json;
                    md.SetXml(doc.OuterXml);
                }
                catch { }
            });
        }

        private static object ParseOrEmpty(string text)
        {
            double v;
            return double.TryParse(text, NumberStyles.Float,
                CultureInfo.InvariantCulture, out v) ? (object)v : "";
        }

        private static int ParseIntOr(string text, int fallback)
        {
            int v;
            return int.TryParse(text, out v) && v >= 10 ? v : fallback;
        }

        private void LoadProfile(string path)
        {
            using (var doc = JsonDocument.Parse(File.ReadAllText(path)))
            {
                var root = doc.RootElement;
                _stations = root.GetProperty("stations_m").EnumerateArray()
                    .Select(v => v.GetDouble()).ToArray();
                _widths = root.GetProperty("widths_m").EnumerateArray()
                    .Select(v => v.GetDouble()).ToArray();
                string wkt = root.GetProperty("working_sr_wkt")
                    .GetString() ?? "";
                int cut = wkt.IndexOf(';');
                if (cut > 0) wkt = wkt.Substring(0, cut);
                var sr = SpatialReferenceBuilder.CreateSpatialReference(wkt);
                var pts = root.GetProperty("route_xy_working")
                    .EnumerateArray()
                    .Select(p =>
                    {
                        var a = p.EnumerateArray()
                            .Select(v => v.GetDouble()).ToArray();
                        return MapPointBuilderEx.CreateMapPoint(
                            a[0], a[1], sr);
                    }).ToList();
                _routePts = pts;
                _routeWorking = PolylineBuilderEx.CreatePolyline(pts, sr);
                _stats = root.GetProperty("stats").Clone();
                _haveStats = true;
                JsonElement recEl;
                _recordJson = root.TryGetProperty("record", out recEl)
                    ? recEl.GetRawText() : null;
            }
            // default threshold: midway between min and max, like the
            // original
            if (double.IsNaN(_threshold))
            {
                double def = Math.Round(
                    (_widths.Min() + _widths.Max()) / 2.0, 0);
                SetThreshold(def, updateText: true);
            }
        }

        // =====================================================================
        // threshold plumbing (textbox / click / drag all converge here)
        // =====================================================================
        private void SetThreshold(double value, bool updateText)
        {
            _threshold = value;
            if (updateText)
            {
                _thresholdFromCode = true;
                txtThreshold.Text = value.ToString(
                    value >= 100 ? "0" : "0.#", CultureInfo.InvariantCulture);
                _thresholdFromCode = false;
            }
            if (_widths != null)
            {
                DrawGraph();
                RenderStats();
                _ = UpdateOverlayAsync();
            }
        }

        private void OnThresholdText(object sender, TextChangedEventArgs e)
        {
            if (_thresholdFromCode) return;
            double v;
            if (double.TryParse(txtThreshold.Text, NumberStyles.Float,
                    CultureInfo.InvariantCulture, out v) && v > 0)
                SetThreshold(v, updateText: false);
        }

        private void OnGraphMouseDown(object sender, MouseButtonEventArgs e)
        {
            if (_widths == null) return;
            _draggingThreshold = true;
            cnvGraph.CaptureMouse();
            SetThreshold(Math.Max(0.1, YToWidth(e.GetPosition(cnvGraph).Y)),
                updateText: true);
        }

        private void OnGraphMouseMove(object sender, MouseEventArgs e)
        {
            if (!_draggingThreshold || _widths == null) return;
            SetThreshold(Math.Max(0.1, YToWidth(e.GetPosition(cnvGraph).Y)),
                updateText: true);
        }

        private void OnGraphMouseUp(object sender, MouseButtonEventArgs e)
        {
            _draggingThreshold = false;
            cnvGraph.ReleaseMouseCapture();
        }

        private void OnGraphSize(object sender, SizeChangedEventArgs e)
        {
            if (_widths != null) DrawGraph();
        }

        // =====================================================================
        // the graph
        // =====================================================================
        private const double MarL = 62, MarR = 14, MarT = 14, MarB = 42;
        private double _maxX, _maxY;

        private double XToPix(double x) =>
            MarL + (cnvGraph.ActualWidth - MarL - MarR) * (x / _maxX);
        private double YToPix(double w) =>
            cnvGraph.ActualHeight - MarB
            - (cnvGraph.ActualHeight - MarT - MarB) * (w / _maxY);
        private double YToWidth(double pix) =>
            (_maxY * (cnvGraph.ActualHeight - MarB - pix)
             / (cnvGraph.ActualHeight - MarT - MarB));

        private static double NiceStep(double range, int target)
        {
            if (range <= 0) return 1;
            double raw = range / Math.Max(1, target);
            double mag = Math.Pow(10, Math.Floor(Math.Log10(raw)));
            foreach (double m in new[] { 1.0, 2.0, 5.0, 10.0 })
                if (raw <= m * mag) return m * mag;
            return 10 * mag;
        }

        private void DrawGraph()
        {
            var c = cnvGraph;
            c.Children.Clear();
            if (_widths == null || c.ActualWidth < 80 || c.ActualHeight < 80)
                return;
            _maxX = _stations[_stations.Length - 1];
            _maxY = _widths.Max() * 1.06;
            bool km = _maxX >= 10000;

            var axisBrush = new SolidColorBrush(Color.FromRgb(70, 70, 70));
            var gridBrush = new SolidColorBrush(Color.FromArgb(36, 0, 0, 0));
            var aboveBrush = new SolidColorBrush(Color.FromRgb(31, 119, 180));
            var belowBrush = new SolidColorBrush(Color.FromRgb(198, 40, 40));
            var thrBrush = new SolidColorBrush(Color.FromRgb(85, 85, 85));

            // gridlines + labels
            double ystep = NiceStep(_maxY, 6);
            for (double w = 0; w <= _maxY + 1e-9; w += ystep)
            {
                double y = YToPix(w);
                c.Children.Add(new WpfLine
                {
                    X1 = MarL, X2 = c.ActualWidth - MarR, Y1 = y, Y2 = y,
                    Stroke = w == 0 ? axisBrush : gridBrush,
                    StrokeThickness = 1,
                });
                var lab = new TextBlock
                {
                    Text = w.ToString("#,##0", CultureInfo.InvariantCulture),
                    FontSize = 11, Foreground = axisBrush,
                };
                lab.Measure(new Size(999, 999));
                Canvas.SetLeft(lab, MarL - 6 - lab.DesiredSize.Width);
                Canvas.SetTop(lab, y - lab.DesiredSize.Height / 2);
                c.Children.Add(lab);
            }
            double xstep = NiceStep(_maxX, 8);
            for (double x = 0; x <= _maxX + 1e-9; x += xstep)
            {
                double px = XToPix(x);
                c.Children.Add(new WpfLine
                {
                    X1 = px, X2 = px, Y1 = MarT, Y2 = c.ActualHeight - MarB,
                    Stroke = x == 0 ? axisBrush : gridBrush,
                    StrokeThickness = 1,
                });
                var lab = new TextBlock
                {
                    Text = (km ? x / 1000.0 : x).ToString(
                        "#,##0.##", CultureInfo.InvariantCulture),
                    FontSize = 11, Foreground = axisBrush,
                };
                lab.Measure(new Size(999, 999));
                Canvas.SetLeft(lab, px - lab.DesiredSize.Width / 2);
                Canvas.SetTop(lab, c.ActualHeight - MarB + 4);
                c.Children.Add(lab);
            }
            // axis titles
            var xt = new TextBlock
            {
                Text = km ? "Distance along route (km)"
                          : "Distance along route (m)",
                FontSize = 12, Foreground = axisBrush,
            };
            xt.Measure(new Size(999, 999));
            Canvas.SetLeft(xt, MarL + (c.ActualWidth - MarL - MarR) / 2
                               - xt.DesiredSize.Width / 2);
            Canvas.SetTop(xt, c.ActualHeight - 20);
            c.Children.Add(xt);
            var yt = new TextBlock
            {
                Text = "Corridor width (m)", FontSize = 12,
                Foreground = axisBrush,
                LayoutTransform = new RotateTransform(-90),
            };
            yt.Measure(new Size(999, 999));
            Canvas.SetLeft(yt, 4);
            Canvas.SetTop(yt, MarT + (c.ActualHeight - MarT - MarB) / 2
                              - yt.DesiredSize.Width / 2);
            c.Children.Add(yt);

            // the profile, split at threshold crossings
            bool hasThr = !double.IsNaN(_threshold);
            var run = new List<Point>();
            bool runBelow = hasThr && _widths[0] < _threshold;
            Action flush = () =>
            {
                if (run.Count < 2) { return; }
                c.Children.Add(new WpfPolyline
                {
                    Points = new PointCollection(run),
                    Stroke = runBelow ? belowBrush : aboveBrush,
                    StrokeThickness = runBelow ? 2.4 : 1.8,
                });
            };
            for (int i = 0; i < _widths.Length; i++)
            {
                bool below = hasThr && _widths[i] < _threshold;
                var pt = new Point(XToPix(_stations[i]), YToPix(_widths[i]));
                if (below != runBelow && run.Count > 0 && hasThr)
                {
                    double f = (_threshold - _widths[i - 1])
                               / (_widths[i] - _widths[i - 1]);
                    f = Math.Max(0, Math.Min(1, f));
                    var xc = _stations[i - 1]
                             + f * (_stations[i] - _stations[i - 1]);
                    var cross = new Point(XToPix(xc), YToPix(_threshold));
                    run.Add(cross);
                    flush();
                    run = new List<Point> { cross };
                    runBelow = below;
                }
                run.Add(pt);
            }
            flush();

            // threshold line (dashed, draggable)
            if (hasThr && _threshold <= _maxY)
            {
                double ty = YToPix(_threshold);
                c.Children.Add(new WpfLine
                {
                    X1 = MarL, X2 = c.ActualWidth - MarR, Y1 = ty, Y2 = ty,
                    Stroke = thrBrush, StrokeThickness = 1.6,
                    StrokeDashArray = new DoubleCollection { 5, 3 },
                    Cursor = Cursors.SizeNS,
                });
                var lab = new TextBlock
                {
                    Text = string.Format(CultureInfo.InvariantCulture,
                        "threshold {0:#,##0.#} m", _threshold),
                    FontSize = 11, Foreground = thrBrush,
                    Background = new SolidColorBrush(
                        Color.FromArgb(210, 255, 255, 255)),
                };
                lab.Measure(new Size(999, 999));
                Canvas.SetLeft(lab, c.ActualWidth - MarR - 8
                                    - lab.DesiredSize.Width);
                Canvas.SetTop(lab, ty - lab.DesiredSize.Height - 2);
                c.Children.Add(lab);
            }
        }

        // =====================================================================
        // stats panel
        // =====================================================================
        private static Paragraph Para(string text, bool bold = false,
                                      double size = 12, Brush brush = null)
        {
            var p = new Paragraph(new Run(text))
            { Margin = new Thickness(0, 0, 0, 2), FontSize = size };
            if (bold) p.FontWeight = FontWeights.SemiBold;
            if (brush != null) p.Foreground = brush;
            return p;
        }

        private void RenderStats()
        {
            if (!_haveStats) return;
            var doc = new FlowDocument
            {
                FontFamily = new FontFamily("Segoe UI"),
                PagePadding = new Thickness(10),
            };
            Func<string, double> S = name =>
                _stats.GetProperty(name).GetDouble();
            doc.Blocks.Add(Para("Bottleneck Width Statistics", true, 13.5));
            doc.Blocks.Add(Para(string.Format(CultureInfo.InvariantCulture,
                "Route length:  {0:#,##0.#} m", S("route_length_m"))));
            doc.Blocks.Add(Para(string.Format(CultureInfo.InvariantCulture,
                "Narrowest point:  {0:#,##0.#} m", S("min_width_m")), true));
            doc.Blocks.Add(Para(string.Format(CultureInfo.InvariantCulture,
                "  at {0:#,##0.#} m along the route",
                S("min_width_station_m"))));
            doc.Blocks.Add(Para(string.Format(CultureInfo.InvariantCulture,
                "Maximum width:  {0:#,##0.#} m", S("max_width_m"))));
            doc.Blocks.Add(Para(string.Format(CultureInfo.InvariantCulture,
                "Mean width:  {0:#,##0.#} m", S("mean_width_m"))));
            doc.Blocks.Add(Para(string.Format(CultureInfo.InvariantCulture,
                "Median width:  {0:#,##0.#} m", S("median_width_m"))));
            doc.Blocks.Add(Para(string.Format(CultureInfo.InvariantCulture,
                "Std. deviation:  {0:#,##0.##} m", S("sd_width_m"))));

            if (!double.IsNaN(_threshold) && _widths != null)
            {
                var below = ThresholdPieces().Where(p => p.below).ToList();
                double belowLen = below.Sum(p => p.s1 - p.s0);
                double total = _stations[_stations.Length - 1] - _stations[0];
                doc.Blocks.Add(Para(""));
                doc.Blocks.Add(Para(string.Format(CultureInfo.InvariantCulture,
                    "Threshold:  {0:#,##0.#} m", _threshold), true, 13.5,
                    new SolidColorBrush(Color.FromRgb(85, 85, 85))));
                doc.Blocks.Add(Para(string.Format(CultureInfo.InvariantCulture,
                    "Below threshold:  {0:0.#}% of the route",
                    total > 0 ? 100.0 * belowLen / total : 0)));
                doc.Blocks.Add(Para(string.Format(CultureInfo.InvariantCulture,
                    "Length below:  {0:#,##0.#} m in {1} stretch{2}",
                    belowLen, below.Count, below.Count == 1 ? "" : "es")));
                if (below.Count > 0)
                {
                    doc.Blocks.Add(Para("Stretches, longest first:", false, 12,
                        new SolidColorBrush(Color.FromRgb(198, 40, 40))));
                    foreach (var p in below.OrderByDescending(p => p.s1 - p.s0)
                             .Take(12))
                        doc.Blocks.Add(Para(string.Format(
                            CultureInfo.InvariantCulture,
                            "   {0:#,##0.#} m  (at {1:#,##0} m)",
                            p.s1 - p.s0, p.s0)));
                }
            }
            statsViewer.Document = doc;
        }

        // client-side above/below pieces with interpolated crossings
        private List<(bool below, double s0, double s1, List<int> idx)>
            ThresholdPieces()
        {
            var pieces = new List<(bool, double, double, List<int>)>();
            if (_widths == null || double.IsNaN(_threshold)) return pieces;
            int n = _widths.Length;
            int i = 0;
            while (i < n)
            {
                bool below = _widths[i] < _threshold;
                int j = i;
                while (j + 1 < n && (_widths[j + 1] < _threshold) == below)
                    j++;
                double s0 = _stations[i], s1 = _stations[j];
                if (i > 0)
                {
                    double f = (_threshold - _widths[i - 1])
                               / (_widths[i] - _widths[i - 1]);
                    f = Math.Max(0, Math.Min(1, f));
                    s0 = _stations[i - 1]
                         + f * (_stations[i] - _stations[i - 1]);
                }
                if (j + 1 < n)
                {
                    double f = (_threshold - _widths[j])
                               / (_widths[j + 1] - _widths[j]);
                    f = Math.Max(0, Math.Min(1, f));
                    s1 = _stations[j] + f * (_stations[j + 1] - _stations[j]);
                }
                pieces.Add((below, s0, s1,
                    Enumerable.Range(i, j - i + 1).ToList()));
                i = j + 1;
            }
            return pieces;
        }

        // =====================================================================
        // map overlay
        // =====================================================================
        private void ClearOverlays()
        {
            // dispose INLINE: overlay handles are disposable from any
            // thread (Esri's own samples dispose them in button
            // handlers), and queueing close-time work to the worker is
            // exactly the kind of shutdown-race that has locked Pro up
            var doomed = _overlays.ToList();
            _overlays.Clear();
            foreach (var o in doomed)
            { try { o.Dispose(); } catch { } }
        }

        private void OnOverlayToggle(object sender, RoutedEventArgs e)
        {
            if (_routePts != null) _ = UpdateOverlayAsync();
        }

        private bool _overlayBusy;
        private bool _overlayDirty;
        private bool _closedDown;

        /// <summary>Refresh the map overlay. Coalesced (threshold drags
        /// fire many per second) and free of synchronous UI-thread calls
        /// from the worker: state is captured on the UI thread, the MCT
        /// builds the overlays, and the list is stored back after the
        /// await -- no Dispatcher.Invoke anywhere.</summary>
        private async Task UpdateOverlayAsync()
        {
            if (_overlayBusy) { _overlayDirty = true; return; }
            _overlayBusy = true;
            try
            {
                do
                {
                    _overlayDirty = false;
                    // never start (or continue) overlay work once the
                    // window is closed -- worker rounds queued after
                    // close race Pro's shutdown
                    if (_closedDown) return;
                    var mv = MapView.Active;
                    if (mv == null || _routePts == null) return;
                    bool show = chkOverlay.IsChecked == true;
                    var pieces = ThresholdPieces();
                    var old = _overlays.ToList();
                    _overlays.Clear();
                    var route = _routeWorking;
                    var routePts = _routePts;
                    var news = await QueuedTask.Run(() =>
                    {
                        foreach (var o in old)
                        { try { o.Dispose(); } catch { } }
                        var made = new List<IDisposable>();
                        try
                        {
                            if (!show) return made;
                            var sr = routePts[0].SpatialReference;
                            CIMSymbolReference MakeSym(bool below) =>
                                SymbolFactory.Instance.ConstructLineSymbol(
                                    below ? ColorFactory.Instance
                                                .CreateRGBColor(198, 40, 40)
                                          : ColorFactory.Instance
                                                .CreateRGBColor(31, 119, 180),
                                    below ? 4.0 : 2.5).MakeSymbolReference();
                            if (pieces.Count == 0)
                            {
                                made.Add(mv.AddOverlay(route,
                                    MakeSym(false)));
                            }
                            else
                            {
                                foreach (var (below, _s0, _s1, idx)
                                         in pieces)
                                {
                                    if (idx.Count < 2) continue;
                                    var pts = idx.Select(k => routePts[k])
                                        .ToList();
                                    var line = PolylineBuilderEx
                                        .CreatePolyline(pts, sr);
                                    made.Add(mv.AddOverlay(line,
                                        MakeSym(below)));
                                }
                            }
                        }
                        catch { }
                        return made;
                    });
                    if (_closedDown)
                    {
                        // closed while the worker was building: these
                        // must not outlive the window on the map
                        foreach (var o in news)
                        { try { o.Dispose(); } catch { } }
                        return;
                    }
                    _overlays.AddRange(news);
                } while (_overlayDirty);
            }
            finally
            {
                _overlayBusy = false;
            }
        }

        // =====================================================================
        // exports
        // =====================================================================
        private static string AskSavePath(string title)
        {
            var dlg = new SaveItemDialog
            {
                Title = title,
                DefaultExt = "",
                Filter = ItemFilters.FeatureClasses_All,
            };
            try
            {
                dlg.InitialLocation = ArcGIS.Desktop.Core.Project
                    .Current?.DefaultGeodatabasePath;
            }
            catch { }
            return dlg.ShowDialog() == true
                ? NormalizeSaveTarget(dlg.FilePath) : null;
        }

        /// <summary>Make a browse-dialog target safe for the GP tools.
        /// An extensionless name in a FOLDER is created as a legacy
        /// ArcInfo coverage, whose 13-character lower-case name rules
        /// rejected Jeff's perfectly reasonable "Blackbear_Route" with
        /// ERROR 000354 -- folder targets get .shp. Inside a geodatabase
        /// the opposite holds: a name must carry NO extension and only
        /// letters/digits/underscores, so anything else is cleaned.</summary>
        private static string NormalizeSaveTarget(string raw)
        {
            if (string.IsNullOrWhiteSpace(raw)) return null;
            string path = raw.Trim().Replace('/', '\\').TrimEnd('\\');
            var (ws, name, gdb) = SplitFcPath(path);
            if (string.IsNullOrEmpty(name)) return path;
            if (gdb)
            {
                int dot = name.IndexOf('.');
                if (dot > 0) name = name.Substring(0, dot);
                var sb = new StringBuilder(name.Length);
                foreach (char c in name)
                    sb.Append(char.IsLetterOrDigit(c) || c == '_'
                        ? c : '_');
                name = sb.ToString();
                if (name.Length == 0 || !char.IsLetter(name[0]))
                    name = "FC_" + name;
            }
            else if (!name.EndsWith(".shp",
                     StringComparison.OrdinalIgnoreCase))
            {
                name += ".shp";
            }
            return string.IsNullOrEmpty(ws) ? name : ws + "\\" + name;
        }

        /// <summary>Run a GP tool WITHOUT adding its outputs to the map:
        /// the DEFAULT flags auto-add them, which put a second,
        /// unsymbolized copy of every saved dataset beside the symbolized
        /// layer AddSavedLayer creates.</summary>
        private static Task<IGPResult> RunGpQuiet(string tool,
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
                // the flags overloads have misfired before (LandFacet);
                // the plain call may auto-add, but it always works
                return Geoprocessing.ExecuteToolAsync(tool, args);
            }
        }

        /// <summary>The threshold formatted exactly as the graph shows it
        /// ("954" / "853.3") -- used in the Threshold/Class attribute
        /// values so legend text and graph agree.</summary>
        private string ThresholdLabel() => _threshold.ToString(
            _threshold >= 100 ? "0" : "0.#", CultureInfo.InvariantCulture);

        private static readonly Tuple<byte, byte, byte> BelowRgb =
            Tuple.Create((byte)198, (byte)40, (byte)40);
        private static readonly Tuple<byte, byte, byte> AboveRgb =
            Tuple.Create((byte)31, (byte)119, (byte)180);

        /// <summary>Unique-Values renderer in the window's colors: red for
        /// the "Below ..." class, blue for "Above ...". MCT only.</summary>
        private static CIMUniqueValueRenderer MakeRedBlueRenderer(
            string field, string belowVal, string aboveVal, bool line)
        {
            CIMSymbolReference Sym(bool isBelow)
            {
                var rgb = isBelow ? BelowRgb : AboveRgb;
                var color = ColorFactory.Instance.CreateRGBColor(
                    rgb.Item1, rgb.Item2, rgb.Item3);
                return line
                    ? SymbolFactory.Instance.ConstructLineSymbol(color,
                        isBelow ? 4.0 : 2.5).MakeSymbolReference()
                    : SymbolFactory.Instance.ConstructPointSymbol(color,
                        isBelow ? 6.0 : 4.5, SimpleMarkerStyle.Circle)
                        .MakeSymbolReference();
            }
            CIMUniqueValueClass Cls(string v, bool isBelow) =>
                new CIMUniqueValueClass
                {
                    Values = new[] { new CIMUniqueValue
                        { FieldValues = new[] { v } } },
                    Label = v,
                    Visible = true,
                    Symbol = Sym(isBelow),
                };
            return new CIMUniqueValueRenderer
            {
                Fields = new[] { field },
                Groups = new[] { new CIMUniqueValueGroup
                    { Classes = new[] { Cls(belowVal, true),
                                        Cls(aboveVal, false) } } },
                UseDefaultSymbol = false,
            };
        }

        /// <summary>Add a just-saved dataset to the active map, symbolized
        /// in the window's colors: red/blue Unique Values on
        /// <paramref name="field" /> when given, else a plain blue
        /// symbol.</summary>
        private async Task AddSavedLayer(string path, string field,
            string belowVal, string aboveVal, bool line)
        {
            var map = MapView.Active?.Map;
            if (map == null) return;
            await QueuedTask.Run(() =>
            {
                try
                {
                    var fl = LayerFactory.Instance.CreateLayer<FeatureLayer>(
                        new FeatureLayerCreationParams(new Uri(path)), map);
                    if (fl == null) return;
                    if (field != null)
                    {
                        fl.SetRenderer(MakeRedBlueRenderer(field,
                            belowVal, aboveVal, line));
                    }
                    else
                    {
                        var blue = ColorFactory.Instance.CreateRGBColor(
                            AboveRgb.Item1, AboveRgb.Item2, AboveRgb.Item3);
                        fl.SetRenderer(new CIMSimpleRenderer
                        {
                            Symbol = line
                                ? SymbolFactory.Instance
                                    .ConstructLineSymbol(blue, 2.5)
                                    .MakeSymbolReference()
                                : SymbolFactory.Instance
                                    .ConstructPointSymbol(blue, 4.5,
                                        SimpleMarkerStyle.Circle)
                                    .MakeSymbolReference(),
                        });
                    }
                }
                catch { }
            });
        }

        private async void OnSaveRoute(object sender, RoutedEventArgs e)
        {
            string target = AskSavePath("Save the widest route");
            if (target == null) return;
            try
            {
            if (await CopyFc(_routeFc, target, "route"))
            {
                await StampSavedDataset(target);
                await AddSavedLayer(target, null, null, null, line: true);
                txtStatus.Text = "Saved route (with analysis record): "
                    + target;
            }
            }
            catch (Exception ex)
            {
                // an async void handler must not let an exception escape
                txtStatus.Text = "Save failed: " + ex.Message;
            }
        }

        private async void OnSavePoints(object sender, RoutedEventArgs e)
        {
            string target = AskSavePath("Save the width station points");
            if (target == null) return;
            try
            {
            if (await CopyFc(_pointsFc, target, "points"))
            {
                string belowVal = null, aboveVal = null;
                if (!double.IsNaN(_threshold))
                {
                    // refresh the Below flag against the CURRENT threshold
                    var expr = string.Format(CultureInfo.InvariantCulture,
                        "int(!Width_m! < {0})", _threshold);
                    await RunGpQuiet("management.CalculateField",
                        Geoprocessing.MakeValueArray(target, "Below", expr,
                            "PYTHON3"));
                    // Jeff: a readable text twin of Below -- "Below 954" /
                    // "Above 954" -- to carry the Unique Values legend
                    string tl = ThresholdLabel();
                    belowVal = "Below " + tl;
                    aboveVal = "Above " + tl;
                    await RunGpQuiet("management.AddField",
                        Geoprocessing.MakeValueArray(target, "Threshold",
                            "TEXT", "", "", 32));
                    var texpr = string.Format(CultureInfo.InvariantCulture,
                        "'{0}' if !Width_m! < {1} else '{2}'",
                        belowVal, _threshold, aboveVal);
                    await RunGpQuiet("management.CalculateField",
                        Geoprocessing.MakeValueArray(target, "Threshold",
                            texpr, "PYTHON3"));
                }
                await StampSavedDataset(target);
                await AddSavedLayer(target,
                    belowVal != null ? "Threshold" : null,
                    belowVal, aboveVal, line: false);
                txtStatus.Text = "Saved points (with analysis record): "
                    + target;
            }
            }
            catch (Exception ex)
            {
                // an async void handler must not let an exception escape
                txtStatus.Text = "Save failed: " + ex.Message;
            }
        }

        private async void OnSaveSegments(object sender, RoutedEventArgs e)
        {
            if (double.IsNaN(_threshold))
            {
                txtStatus.Text = "Set a width threshold first -- segments "
                    + "are the above/below-threshold stretches.";
                return;
            }
            string target = AskSavePath(
                "Save the above/below-threshold segments");
            if (target == null) return;
            try
            {
                txtStatus.Text = "Writing segments...";
                var pieces = ThresholdPieces();
                string tl = ThresholdLabel();
                string belowVal = "Below " + tl, aboveVal = "Above " + tl;
                var (ws, name, _gdb) = SplitFcPath(target);
                if (_gdb)
                {
                    // SplitFcPath returns the geodatabase ROOT (what the
                    // open-by-name callers need). Create the segments in
                    // the folder the user actually chose, so a target
                    // inside a FEATURE DATASET lands there and not at the
                    // top level of the geodatabase (review of 2026-10-04).
                    string full = target.Replace('/', '\\').TrimEnd('\\');
                    int cut = full.LastIndexOf('\\');
                    if (cut > 0) ws = full.Substring(0, cut);
                }
                var r1 = await RunGpQuiet("management.CreateFeatureclass",
                    Geoprocessing.MakeValueArray(ws, name, "POLYLINE", "", "",
                        "", _routePts[0].SpatialReference));
                if (r1.IsFailed) throw new InvalidOperationException(
                    string.Join("; ", r1.ErrorMessages.Select(m => m.Text)));
                // one AddFields call, not one AddField per field (each is a
                // full GP launch -- the standing schema-building rule).
                // Min/Max_Distance (Jeff): nearest/farthest the corridor
                // EDGE gets along each segment -- half the min/max corridor
                // width over its stations (width = 2 x the edge distance)
                await RunGpQuiet("management.AddFields",
                    Geoprocessing.MakeValueArray(target,
                        "Class TEXT # 32 # #;Length_m DOUBLE # # # #;"
                        + "Min_Distance DOUBLE # # # #;"
                        + "Max_Distance DOUBLE # # # #"));
                // Captured BEFORE the write: the EditOperation below leaves
                // the new rows PENDING in the project's edit session (Pro
                // then shows a "Pending Edits" alert in every other tool).
                // Same rule as GpTables.WriteTable: commit them ourselves,
                // but never silently commit the user's own unrelated
                // pending edits along with them.
                bool hadPriorEdits = false;
                try { hadPriorEdits = ArcGIS.Desktop.Core.Project.Current.HasEdits; }
                catch { }
                await QueuedTask.Run(() =>
                {
                    using (var gdb = OpenDatastore(target))
                    using (var fc = OpenFc(gdb, target))
                    {
                        var fcDef = fc.GetDefinition();
                        string shapeField = fcDef.GetShapeField();
                        // shapefile targets truncate field names to 10
                        // characters (Min_Distance -> Min_Distan)
                        string minF = fcDef.GetFields().Any(f =>
                                f.Name == "Min_Distance")
                            ? "Min_Distance" : "Min_Distan";
                        string maxF = fcDef.GetFields().Any(f =>
                                f.Name == "Max_Distance")
                            ? "Max_Distance" : "Max_Distan";
                        var op = new ArcGIS.Desktop.Editing.EditOperation
                        { Name = "Bottleneck segments", ShowProgressor = false };
                        op.Callback(context =>
                        {
                            foreach (var (below, s0, s1, idx) in pieces)
                            {
                                if (idx.Count < 2) continue;
                                var line = PolylineBuilderEx.CreatePolyline(
                                    idx.Select(k => _routePts[k]).ToList(),
                                    _routePts[0].SpatialReference);
                                using (var buf = fc.CreateRowBuffer())
                                {
                                    buf[shapeField] = line;
                                    buf["Class"] = below ? belowVal
                                                         : aboveVal;
                                    buf["Length_m"] = s1 - s0;
                                    buf[minF] = idx.Min(k => _widths[k])
                                        / 2.0;
                                    buf[maxF] = idx.Max(k => _widths[k])
                                        / 2.0;
                                    using (var row = fc.CreateRow(buf))
                                        context.Invalidate(row);
                                }
                            }
                        }, fc);
                        if (!op.Execute())
                            throw new InvalidOperationException(
                                op.ErrorMessage ?? "edit failed");
                    }
                });
                if (!hadPriorEdits)
                {
                    try { await ArcGIS.Desktop.Core.Project.Current.SaveEditsAsync(); }
                    catch { }
                }
                await StampSavedDataset(target);
                await AddSavedLayer(target, "Class", belowVal, aboveVal,
                    line: true);
                txtStatus.Text = "Saved segments (with analysis record): "
                    + target;
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Segments failed: " + ex.Message;
            }
        }

        private static Datastore OpenDatastore(string fcPath)
        {
            var (ws, _name, gdb) = SplitFcPath(fcPath);
            if (gdb)
                return new Geodatabase(new FileGeodatabaseConnectionPath(
                    new Uri(ws)));
            return new FileSystemDatastore(new FileSystemConnectionPath(
                new Uri(ws ?? "."), FileSystemDatastoreType.Shapefile));
        }

        private static FeatureClass OpenFc(Datastore ds, string fcPath)
        {
            var (_ws, name, _gdb) = SplitFcPath(fcPath);
            if (ds is Geodatabase gdb)
                return gdb.OpenDataset<FeatureClass>(name);
            return ((FileSystemDatastore)ds).OpenDataset<FeatureClass>(name);
        }

        private async Task<bool> CopyFc(string src, string target, string what)
        {
            try
            {
                txtStatus.Text = string.Format("Saving {0}...", what);
                var res = await RunGpQuiet("management.CopyFeatures",
                    Geoprocessing.MakeValueArray(src, target));
                if (res.IsFailed)
                {
                    txtStatus.Text = string.Format(
                        "Save failed ({0}): {1}", target,
                        string.Join("; ",
                            res.ErrorMessages.Select(m => m.Text).Take(2)));
                    return false;
                }
                txtStatus.Text = string.Format("Saved {0}: {1}", what, target);
                return true;
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Save failed: " + ex.Message;
                return false;
            }
        }

        private void OnSaveGraph(object sender, RoutedEventArgs e)
        {
            var dlg = new Microsoft.Win32.SaveFileDialog
            {
                Title = "Save the width graph",
                Filter = "PNG image (*.png)|*.png",
                FileName = "Bottleneck_Width_Graph.png",
                InitialDirectory = ImageSaveFolder.Initial(),
            };
            if (dlg.ShowDialog() != true) return;
            ImageSaveFolder.Remember(dlg.FileName);
            try
            {
                double scale = 2.0;   // crisp export
                var rtb = new RenderTargetBitmap(
                    (int)(cnvGraph.ActualWidth * scale),
                    (int)(cnvGraph.ActualHeight * scale),
                    96 * scale, 96 * scale, PixelFormats.Pbgra32);
                var dv = new DrawingVisual();
                using (var dc = dv.RenderOpen())
                {
                    dc.DrawRectangle(Brushes.White, null, new Rect(0, 0,
                        cnvGraph.ActualWidth, cnvGraph.ActualHeight));
                    dc.DrawRectangle(new VisualBrush(cnvGraph), null,
                        new Rect(0, 0, cnvGraph.ActualWidth,
                                 cnvGraph.ActualHeight));
                }
                rtb.Render(dv);
                var enc = new PngBitmapEncoder();
                enc.Frames.Add(BitmapFrame.Create(rtb));
                using (var fs = File.Create(dlg.FileName)) enc.Save(fs);
                txtStatus.Text = "Graph saved: " + dlg.FileName;
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Graph save failed: " + ex.Message;
            }
        }

        private void OnCopyStats(object sender, RoutedEventArgs e)
        {
            if (!_haveStats) return;
            var sb = new StringBuilder();
            var range = new TextRange(statsViewer.Document.ContentStart,
                                      statsViewer.Document.ContentEnd);
            sb.Append(range.Text);
            // Guarded like every sibling window: another process holding
            // the clipboard open throws CLIPBRD_E_CANT_OPEN.
            try
            {
                Clipboard.SetText(sb.ToString());
                txtStatus.Text = "Statistics copied to the clipboard.";
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Could not copy to the clipboard: "
                    + ex.Message;
            }
        }

        private void OnClose(object sender, RoutedEventArgs e)
        {
            ClearOverlays();
            Close();
        }

        private void OnHelp(object sender, RoutedEventArgs e)
            => WebHelp.Open("corridor-designer-tools/bottleneck-analysis.html");

        private async void OnAddGraphToLayout(object sender, RoutedEventArgs e)
        {
            if (_widths == null) return;
            try
            {
                var model = BottleneckGraphBuilder.Build(_stations, _widths,
                    _threshold);
                string where = await LayoutPlacement.AddChart(this, model,
                    "Bottleneck Width Profile", BuildTaggedStats(),
                    "Bottleneck Width Statistics");
                if (where != null) txtStatus.Text = "✓ " + where;
            }
            catch (Exception ex)
            {
                ArcGIS.Desktop.Framework.Dialogs.MessageBox.Show(
                    ex.Message, Title);
            }
        }

        private string BuildTaggedStats()
        {
            if (!_haveStats) return "";
            string esc(string t) => (t ?? "").Replace("&", "&amp;")
                .Replace("<", "&lt;").Replace(">", "&gt;");
            double S(string name) => _stats.GetProperty(name).GetDouble();
            var sb = new StringBuilder();
            sb.AppendLine(
                "<FNT size=\"14\"><BOL>Bottleneck Width Statistics</BOL></FNT>");
            sb.AppendLine();
            sb.AppendLine(esc(string.Format(CultureInfo.InvariantCulture,
                "Route length:  {0:#,##0.#} m", S("route_length_m"))));
            sb.AppendLine("<BOL>" + esc(string.Format(
                CultureInfo.InvariantCulture,
                "Narrowest point:  {0:#,##0.#} m", S("min_width_m")))
                + "</BOL>");
            sb.AppendLine(esc(string.Format(CultureInfo.InvariantCulture,
                "  at {0:#,##0.#} m along the route",
                S("min_width_station_m"))));
            sb.AppendLine(esc(string.Format(CultureInfo.InvariantCulture,
                "Maximum width:  {0:#,##0.#} m", S("max_width_m"))));
            sb.AppendLine(esc(string.Format(CultureInfo.InvariantCulture,
                "Mean width:  {0:#,##0.#} m", S("mean_width_m"))));
            sb.AppendLine(esc(string.Format(CultureInfo.InvariantCulture,
                "Median width:  {0:#,##0.#} m", S("median_width_m"))));
            sb.AppendLine(esc(string.Format(CultureInfo.InvariantCulture,
                "Std. deviation:  {0:#,##0.##} m", S("sd_width_m"))));
            if (!double.IsNaN(_threshold) && _widths != null)
            {
                var below = ThresholdPieces().Where(pc => pc.below).ToList();
                double belowLen = below.Sum(pc => pc.s1 - pc.s0);
                double total = _stations[_stations.Length - 1] - _stations[0];
                sb.AppendLine();
                sb.AppendLine("<BOL>" + esc(string.Format(
                    CultureInfo.InvariantCulture,
                    "Threshold:  {0:#,##0.#} m", _threshold)) + "</BOL>");
                sb.AppendLine(esc(string.Format(CultureInfo.InvariantCulture,
                    "Below threshold:  {0:0.#}% of the route",
                    total > 0 ? 100.0 * belowLen / total : 0)));
                sb.AppendLine(esc(string.Format(CultureInfo.InvariantCulture,
                    "Length below:  {0:#,##0.#} m in {1} stretch{2}",
                    belowLen, below.Count, below.Count == 1 ? "" : "es")));
                if (below.Count > 0)
                {
                    sb.AppendLine("<BOL>Stretches, longest first:</BOL>");
                    foreach (var pc in below.OrderByDescending(
                                 pc => pc.s1 - pc.s0).Take(12))
                        sb.AppendLine(esc(string.Format(
                            CultureInfo.InvariantCulture,
                            "   {0:#,##0.#} m  (at {1:#,##0} m)",
                            pc.s1 - pc.s0, pc.s0)));
                }
            }
            return sb.ToString();
        }

        // =====================================================================
        // drag-and-drop from the Contents pane
        // =====================================================================
        void ArcGIS.Desktop.Framework.DragDrop.IDropTarget.OnDragOver(
            ArcGIS.Desktop.Framework.DragDrop.DropInfo dropInfo)
        {
            // Answer with an effect FROM THE DRAG'S ALLOWED SET: OLE
            // intersects the target's answer with the source's allowed
            // effects, so replying "Copy" to a Move-only drag (the TOC's
            // reorder drag) yields None = the prohibited cursor no matter
            // what else is right. We never actually move anything -- the
            // effect is only cursor feedback.
            dropInfo.Effects = PickAllowedEffect(
                dropInfo.DragEventArgs?.AllowedEffects
                ?? DragDropEffects.Copy);
            // CRITICAL (decompiled from the framework): when Handled is
            // left false, the framework consults its GLOBAL drop handlers
            // next, and their combined verdict OVERWRITES our effect with
            // None -- the "prohibited" cursor Jeff kept seeing.
            dropInfo.Handled = true;
            ShowDragDiagnostic(dropInfo.DragEventArgs, dropInfo.Data);
        }

        private static DragDropEffects PickAllowedEffect(
            DragDropEffects allowed)
        {
            if ((allowed & DragDropEffects.Copy) != 0)
                return DragDropEffects.Copy;
            if ((allowed & DragDropEffects.Move) != 0)
                return DragDropEffects.Move;
            if ((allowed & DragDropEffects.Link) != 0)
                return DragDropEffects.Link;
            return allowed;
        }

        private string _lastDragDiag;

        /// <summary>Debug-only drag diagnostic (was live in txtStatus during
        /// development; Jeff, 2026-09-10, saw it fire while dragging the
        /// graph's threshold line -- OnDragOver is registered for the whole
        /// WINDOW, not just the three input rows, so any drag-like mouse
        /// movement anywhere in the window trips it, not just an actual
        /// layer drag). Kept as a Debug.WriteLine in case drag-and-drop from
        /// the Contents pane ever needs re-diagnosing after a Pro update;
        /// no longer shown to the user.</summary>
        [System.Diagnostics.Conditional("DEBUG")]
        private void ShowDragDiagnostic(DragEventArgs e, object data)
        {
            string msg = string.Format(
                "Drag detected (allowed: {0}; payload: {1}) -- release "
                + "over an input row to assign the layer.",
                e != null ? e.AllowedEffects.ToString() : "?",
                data == null ? "none" : data.GetType().Name);
            if (msg != _lastDragDiag)
            {
                _lastDragDiag = msg;
                System.Diagnostics.Debug.WriteLine(msg);
            }
        }

        void ArcGIS.Desktop.Framework.DragDrop.IDropTarget.OnDrop(
            ArcGIS.Desktop.Framework.DragDrop.DropInfo dropInfo)
        {
            _lastFrameworkDrop = DateTime.UtcNow;
            var e = dropInfo.DragEventArgs;
            var under = (e?.OriginalSource as FrameworkElement)
                ?? dropInfo.VisualTarget as FrameworkElement;
            var pos = e != null ? e.GetPosition(this) : new Point(0, 0);
            var slot = ResolveDropSlot(under, pos);
            ApplyLayerDrop(slot, ExtractDroppedLayer(dropInfo));
            dropInfo.Handled = true;
        }

        private DateTime _lastFrameworkDrop = DateTime.MinValue;

        private Slot SlotForElement(FrameworkElement el)
        {
            ComboBox combo = el as ComboBox;
            for (var p = el; combo == null && p != null;
                 p = VisualTreeHelper.GetParent(p) as FrameworkElement)
                combo = p as ComboBox;
            if (combo == null) return null;
            return _slots.FirstOrDefault(s =>
                ReferenceEquals(s.Source, combo));
        }

        /// <summary>The slot a drop belongs to: the element under the
        /// cursor when it is one of the boxes, else the input row the
        /// drop's Y position falls on, else the first EMPTY slot.</summary>
        private Slot ResolveDropSlot(FrameworkElement under, Point posInWindow)
        {
            var byElement = SlotForElement(under);
            if (byElement != null) return byElement;
            foreach (var s in _slots)
            {
                try
                {
                    var top = s.Source.TranslatePoint(new Point(0, 0), this);
                    if (posInWindow.Y >= top.Y - 4
                        && posInWindow.Y <= top.Y
                            + s.Source.ActualHeight + 4)
                        return s;
                }
                catch { }
            }
            return _slots.FirstOrDefault(s => s.Source.SelectedItem == null)
                ?? _slots[0];
        }

        private void ApplyLayerDrop(Slot slot, FeatureLayer lyr)
        {
            if (slot == null) return;
            if (lyr == null)
            {
                txtStatus.Text = "Drop a polygon layer from the Contents "
                    + "pane here (or use Browse for data on disk).";
                return;
            }
            if (lyr.ShapeType != esriGeometryType.esriGeometryPolygon)
            {
                txtStatus.Text = string.Format(
                    "'{0}' is not a polygon layer.", lyr.Name);
                return;
            }
            var items = slot.Source.ItemsSource as List<SourceChoice>;
            if (items == null || !items.Any(i => ReferenceEquals(i.Layer, lyr)))
            {
                PopulateLayers();
                items = slot.Source.ItemsSource as List<SourceChoice>;
            }
            var match = items?.FirstOrDefault(i =>
                ReferenceEquals(i.Layer, lyr))
                ?? items?.FirstOrDefault(i => i.Layer != null
                    && i.Layer.URI == lyr.URI);
            if (match != null)
            {
                slot.Source.SelectedItem = match;
                txtStatus.Text = string.Format("Dropped '{0}'.", lyr.Name);
            }
        }

        // ---- plain-WPF fallback (Pro drags arriving as OLE drops) -----------
        private void OnWpfDragOver(object sender, DragEventArgs e)
        {
            e.Effects = PickAllowedEffect(e.AllowedEffects);
            e.Handled = true;
            ShowDragDiagnostic(e, null);
        }

        private void OnWpfDrop(object sender, DragEventArgs e)
        {
            e.Handled = true;
            // the framework route already handled this physical drop
            if ((DateTime.UtcNow - _lastFrameworkDrop).TotalMilliseconds
                < 500) return;
            var slot = ResolveDropSlot(e.OriginalSource as FrameworkElement,
                                       e.GetPosition(this));
            var lyr = ExtractFromDataObject(e.Data);
            if (lyr == null)
            {
                // self-diagnosing: name the formats so a failed drop can
                // be reported precisely
                string fmts;
                try { fmts = string.Join(", ", e.Data.GetFormats()); }
                catch { fmts = "(unreadable)"; }
                txtStatus.Text = "Could not read the dropped item "
                    + "(formats: " + fmts + ") -- use the dropdown or "
                    + "Browse instead.";
                return;
            }
            ApplyLayerDrop(slot, lyr);
        }

        private static FeatureLayer ExtractFromDataObject(IDataObject data)
        {
            var map = MapView.Active?.Map;
            if (data == null || map == null) return null;
            FeatureLayer FromText(string s)
            {
                if (string.IsNullOrWhiteSpace(s)) return null;
                var all = map.GetLayersAsFlattenedList()
                    .OfType<FeatureLayer>().ToList();
                return all.FirstOrDefault(l => l.URI == s)
                    ?? all.FirstOrDefault(l => string.Equals(l.Name,
                        s.Trim(), StringComparison.OrdinalIgnoreCase));
            }
            try
            {
                foreach (var fmt in data.GetFormats())
                {
                    object o;
                    try { o = data.GetData(fmt); }
                    catch { continue; }
                    if (o is FeatureLayer fl) return fl;
                    if (o is string s)
                    {
                        var got = FromText(s);
                        if (got != null) return got;
                        // multi-line payloads: try each line
                        foreach (var line in s.Split('\n'))
                        {
                            got = FromText(line.Trim('\r', ' '));
                            if (got != null) return got;
                        }
                    }
                    if (o is System.Collections.IEnumerable seq
                        && !(o is string))
                        foreach (var item in seq)
                        {
                            if (item is FeatureLayer fl2) return fl2;
                            var got = FromText(item as string);
                            if (got != null) return got;
                        }
                    // TOCDragPayload arriving through the raw DataObject
                    if (o != null && !(o is string))
                        foreach (var propName in new[]
                                 { "DraggedContent", "MapMemberURIs" })
                        {
                            try
                            {
                                var val = o.GetType().GetProperty(propName)
                                    ?.GetValue(o);
                                if (val is System.Collections.IEnumerable sq2)
                                    foreach (var item in sq2)
                                    {
                                        if (item is FeatureLayer fl3)
                                            return fl3;
                                        var got = FromText(item as string);
                                        if (got != null) return got;
                                    }
                            }
                            catch { }
                        }
                }
            }
            catch { }
            return null;
        }

        /// <summary>The dropped FeatureLayer, however it was packaged. The
        /// Contents pane drags an internal TOCDragPayload whose public
        /// DraggedContent (MapMembers) and MapMemberURIs properties are
        /// reached by reflection (established by decompiling
        /// ArcGIS.Desktop.Internal.Mapping.TOC.TOCDragPayload); plain
        /// MapMembers, collections, and URI/name strings also work.</summary>
        private static FeatureLayer ExtractDroppedLayer(
            ArcGIS.Desktop.Framework.DragDrop.DropInfo dropInfo)
        {
            var map = MapView.Active?.Map;
            FeatureLayer FromObject(object o, int depth = 0)
            {
                if (o == null || depth > 4) return null;
                if (o is FeatureLayer fl) return fl;
                if (o is System.Collections.IEnumerable seq
                    && !(o is string))
                    foreach (var item in seq)
                    {
                        var got = FromObject(item, depth + 1);
                        if (got != null) return got;
                    }
                if (o is string s && map != null)
                {
                    var all = map.GetLayersAsFlattenedList()
                        .OfType<FeatureLayer>().ToList();
                    var byUri = all.FirstOrDefault(l => l.URI == s);
                    if (byUri != null) return byUri;
                    return all.FirstOrDefault(l => string.Equals(l.Name, s,
                        StringComparison.OrdinalIgnoreCase));
                }
                // TOCDragPayload (internal): probe its payload properties
                if (!(o is string))
                {
                    var t = o.GetType();
                    foreach (var propName in new[]
                             { "DraggedContent", "MapMemberURIs" })
                    {
                        try
                        {
                            var pi = t.GetProperty(propName);
                            if (pi == null) continue;
                            var got = FromObject(pi.GetValue(o), depth + 1);
                            if (got != null) return got;
                        }
                        catch { }
                    }
                }
                return null;
            }

            try
            {
                if (dropInfo.Items != null)
                    foreach (var item in dropInfo.Items)
                    {
                        var got = FromObject(item.Data);
                        if (got != null) return got;
                    }
                return FromObject(dropInfo.Data);
            }
            catch { return null; }
        }
    }
}
