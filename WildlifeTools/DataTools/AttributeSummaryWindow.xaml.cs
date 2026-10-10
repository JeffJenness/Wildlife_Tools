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
using System.Windows;
using System.Windows.Controls;
using ArcGIS.Core.CIM;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using ArcGIS.Desktop.Mapping.Events;
using WildlifeTools.PolarPlots;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.DataTools
{
    /// <summary>
    /// Attribute Summary Reports: the Pro recreation of the SSI ArcMap
    /// "Summarize Attribute Fields" tool. Pick a dataset, build an ORDERED
    /// list of fields to analyze (the order drives the {valN} tokens, the
    /// combined-mode sort precedence, and the section order), design the
    /// per-line format with token buttons and a live sample, and run --
    /// counts and proportions open in a copyable report window.
    /// </summary>
    public partial class AttributeSummaryWindow
        : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private List<PolarPlotData.Source> _sources = new List<PolarPlotData.Source>();
        private readonly List<string> _available = new List<string>();
        private readonly List<string> _analysis = new List<string>();
        // numeric fields of the current dataset (statistics candidates);
        // _fieldsLoaded is false until a dataset's fields have been read
        private readonly List<string> _numeric = new List<string>();
        private bool _fieldsLoaded;
        private bool _loading = true;

        public AttributeSummaryWindow()
        {
            InitializeComponent();
            global::WildlifeTools.Corridor.JennessIcon.Apply(this);
            var s = SummaryFormatStore.LoadSettings();
            txtFormat.Text = string.IsNullOrWhiteSpace(s.FormatText)
                ? AttributeSummary.DefaultFormat : s.FormatText;
            chkCombine.IsChecked = s.Combine;
            chkSort.IsChecked = s.Sort;
            chkGeometry.IsChecked = s.IncludeGeometry;
            rbSelected.IsChecked = s.SelectedOnly;
            rbAll.IsChecked = !s.SelectedOnly;
            for (int d = 0; d <= 6; d++) cboStatDecimals.Items.Add(d);
            cboStatDecimals.SelectedItem =
                Math.Min(Math.Max(s.StatDecimals, 0), 6);
            chkStats.IsChecked = s.IncludeStatistics;
            Loaded += async (_, __) =>
            {
                await LoadSourcesAsync();
                QueueMarks();
            };
            UpdateSample();
            UpdateTokenRows();

            // The window follows the map, like the Terrain Graphs windows
            // (Jeff, 2026-10-05: a layer added after the window opened was
            // not in the list). Layers or tables added or removed: the
            // dataset list is rebuilt. One renamed in the Contents pane:
            // the list follows it. Another map made the active view: ITS
            // datasets are listed. A changed selection: the count beside
            // "Analyze selected records" is brought up to date.
            try
            {
                var tokens = new List<ArcGIS.Core.Events.SubscriptionToken>
                {
                    MapMemberPropertiesChangedEvent.Subscribe(_ => QueueRenames()),
                    LayersAddedEvent.Subscribe(_ => QueueRefresh()),
                    LayersRemovedEvent.Subscribe(_ => QueueRefresh()),
                    StandaloneTablesAddedEvent.Subscribe(_ => QueueRefresh()),
                    StandaloneTablesRemovedEvent.Subscribe(_ => QueueRefresh()),
                    MapSelectionChangedEvent.Subscribe(_ => QueueSelectionNote()),
                    ActiveMapViewChangedEvent.Subscribe(_ => QueueRefresh()),
                };
                // Clicking back into this window (or its ribbon button):
                // make sure the list is current, whatever happened in Pro
                // meanwhile. Nothing changes when nothing changed.
                Activated += (_, __) => QueueRefresh();
                Closed += (_, __) =>
                {
                    try
                    {
                        MapMemberPropertiesChangedEvent.Unsubscribe(tokens[0]);
                        LayersAddedEvent.Unsubscribe(tokens[1]);
                        LayersRemovedEvent.Unsubscribe(tokens[2]);
                        StandaloneTablesAddedEvent.Unsubscribe(tokens[3]);
                        StandaloneTablesRemovedEvent.Unsubscribe(tokens[4]);
                        MapSelectionChangedEvent.Unsubscribe(tokens[5]);
                        ActiveMapViewChangedEvent.Unsubscribe(tokens[6]);
                    }
                    catch { }
                };
            }
            catch { }
        }

        // ---- following the map ---------------------------------------------
        private void QueueRefresh()
        {
            try { Dispatcher.BeginInvoke(new Action(RefreshSources)); }
            catch { }
        }

        private void QueueRenames()
        {
            try { Dispatcher.BeginInvoke(new Action(ApplyRenames)); }
            catch { }
        }

        private void QueueSelectionNote()
        {
            try { Dispatcher.BeginInvoke(new Action(UpdateSelectionNote)); }
            catch { }
        }

        private bool _refreshing, _refreshPending;

        /// <summary>Lists the active map's layers and tables again. The
        /// current dataset stays chosen when it is still there, and its
        /// field lists are then left exactly as they are; only when it has
        /// gone is another dataset chosen (which reads that one's fields).</summary>
        private async void RefreshSources()
        {
            if (_loading) return;
            if (_refreshing) { _refreshPending = true; return; }
            _refreshing = true;
            try
            {
                var map = MapView.Active?.Map;
                if (map == null) return;     // a layout is active: keep the list
                var fresh = await QueuedTask.Run(() => PolarPlotData.ListSources(map));

                bool same = _sources != null && fresh.Count == _sources.Count;
                for (int i = 0; same && i < fresh.Count; i++)
                    same = fresh[i].SameMember(_sources[i]) &&
                           fresh[i].Name == _sources[i].Name;
                if (same) { UpdateSelectionNote(); return; }

                var cur = cboSource.SelectedItem as PolarPlotData.Source;
                var keep = cur == null ? null
                    : fresh.FirstOrDefault(x2 => x2.SameMember(cur));
                _sources = fresh;
                _loading = true;        // re-listing must not reset the fields
                try
                {
                    cboSource.ItemsSource = _sources;
                    if (keep != null) cboSource.SelectedItem = keep;
                }
                finally { _loading = false; }

                if (keep != null) { UpdateSelectionNote(); return; }
                if (_sources.Count > 0)
                    cboSource.SelectedItem = PickInitialSource() ?? _sources[0];
                else
                {
                    _available.Clear();
                    _analysis.Clear();
                    _numeric.Clear();
                    cboStatField.ItemsSource = null;
                    RefreshLists();
                    UpdateTokenRows();
                    UpdateSelectionNote();
                    SetStatus("No layers or tables in the map.");
                }
            }
            catch (Exception ex) { _loading = false; SetStatus("Error: " + ex.Message); }
            finally
            {
                _refreshing = false;
                if (_refreshPending) { _refreshPending = false; QueueRefresh(); }
            }
        }

        /// <summary>A layer or table was renamed in the Contents pane: the
        /// list shows its new name. Nothing is read again.</summary>
        private void ApplyRenames()
        {
            try
            {
                if (_loading || _sources == null) return;
                bool any = false;
                foreach (var src in _sources)
                {
                    string nm = src.ListName();
                    if (!string.IsNullOrEmpty(nm) && nm != src.Name)
                    { src.Name = nm; any = true; }
                }
                if (!any) return;
                var cur = cboSource.SelectedItem;
                _loading = true;
                try
                {
                    cboSource.ItemsSource = null;
                    cboSource.ItemsSource = _sources;
                    cboSource.SelectedItem = cur;
                }
                finally { _loading = false; }
            }
            catch { _loading = false; }
        }

        private int _selNoteVersion;

        /// <summary>Shows how many records of the chosen dataset are selected
        /// right now, in the label of "Analyze selected records". (The run
        /// itself always reads the selection afresh; this is only so the
        /// window shows what it is about to use.)</summary>
        private async void UpdateSelectionNote()
        {
            if (rbSelected == null) return;
            const string label = "Analyze selected records";
            int version = ++_selNoteVersion;
            string text = label;
            try
            {
                var src = cboSource.SelectedItem as PolarPlotData.Source;
                if (src != null && src.IsRaster)
                    text = label + " (a raster has none)";
                else if (src != null)
                {
                    int n = await QueuedTask.Run(() =>
                        src.FeatureLayer != null
                            ? src.FeatureLayer.SelectionCount
                            : src.StandaloneTable != null
                                ? src.StandaloneTable.SelectionCount : 0);
                    text = n > 0
                        ? label + " (" + n.ToString("N0") + " selected)"
                        : label + " (none selected)";
                }
            }
            catch { text = label; }
            if (version == _selNoteVersion) rbSelected.Content = text;
        }

        private void SetStatus(string t) => txtStatus.Text = t;

        private async System.Threading.Tasks.Task LoadSourcesAsync()
        {
            try
            {
                var map = MapView.Active?.Map;
                _sources = await QueuedTask.Run(() => PolarPlotData.ListSources(map));
                cboSource.ItemsSource = _sources;
                _loading = false;
                if (_sources.Count > 0)
                    cboSource.SelectedItem = PickInitialSource() ?? _sources[0];
                else SetStatus("No layers or tables in the map.");
            }
            catch (Exception ex) { _loading = false; SetStatus("Error: " + ex.Message); }
        }

        /// <summary>The source matching the top-most layer (or standalone
        /// table) selected in the Contents pane, if any.</summary>
        private PolarPlotData.Source PickInitialSource()
        {
            try
            {
                var mv = MapView.Active;
                if (mv == null) return null;
                foreach (var lyr in mv.GetSelectedLayers())
                {
                    var hit = _sources.FirstOrDefault(s =>
                        ReferenceEquals(s.FeatureLayer, lyr) ||
                        ReferenceEquals(s.RasterLayer, lyr));
                    if (hit != null) return hit;
                }
                foreach (var st in mv.GetSelectedStandaloneTables())
                {
                    var hit = _sources.FirstOrDefault(s =>
                        ReferenceEquals(s.StandaloneTable, st));
                    if (hit != null) return hit;
                }
            }
            catch { }
            return null;
        }

        // ---- field lists ----------------------------------------------------
        private async void OnSourceChanged(object sender, SelectionChangedEventArgs e)
        {
            if (_loading) return;
            var src = cboSource.SelectedItem as PolarPlotData.Source;
            if (src == null) return;
            List<string> fields, numeric;
            try
            {
                (fields, numeric) = await QueuedTask.Run(() =>
                    (AttributeSummary.ListSummaryFields(src),
                     AttributeSummary.ListNumericFields(src)));
            }
            catch
            {
                fields = new List<string>();
                numeric = new List<string>();
            }
            _available.Clear();
            _available.AddRange(fields);
            _analysis.Clear();

            // the statistics field list follows the dataset; a field of the
            // same name stays chosen
            string keep = cboStatField.SelectedItem as string;
            _numeric.Clear();
            _numeric.AddRange(numeric);
            _fieldsLoaded = true;
            cboStatField.ItemsSource = _numeric.ToList();
            cboStatField.SelectedItem = _numeric.FirstOrDefault(f =>
                string.Equals(f, keep, StringComparison.OrdinalIgnoreCase))
                ?? _numeric.FirstOrDefault();

            RefreshLists();
            UpdateTokenRows();
            QueueMarks();
            UpdateSelectionNote();
            SetStatus(fields.Count > 0
                ? "Add the field(s) to analyze, set their order, then Run Report."
                : "This dataset has no summarizable fields (a raster needs an "
                  + "attribute table).");
        }

        private void RefreshLists(int? selectAnalysis = null)
        {
            lstAvailable.ItemsSource = null;
            lstAvailable.ItemsSource = _available.Where(
                f => !_analysis.Contains(f)).ToList();
            lstAnalysis.ItemsSource = null;
            lstAnalysis.ItemsSource = _analysis
                .Select((f, i) => (i + 1) + "]  " + f).ToList();
            if (selectAnalysis.HasValue && selectAnalysis.Value >= 0 &&
                selectAnalysis.Value < _analysis.Count)
                lstAnalysis.SelectedIndex = selectAnalysis.Value;
            UpdateSample();
        }

        private void OnAddFields(object sender, RoutedEventArgs e)
        {
            var chosen = lstAvailable.SelectedItems.Cast<string>().ToList();
            if (chosen.Count == 0)
            {
                SetStatus("Highlight the field(s) to add first.");
                return;
            }
            foreach (var f in chosen)
                if (!_analysis.Contains(f)) _analysis.Add(f);
            RefreshLists(_analysis.Count - 1);
        }

        private void OnRemoveFields(object sender, RoutedEventArgs e)
        {
            var idx = lstAnalysis.SelectedIndex;
            if (idx < 0)
            {
                SetStatus("Highlight the field to remove first.");
                return;
            }
            _analysis.RemoveAt(idx);
            RefreshLists(Math.Min(idx, _analysis.Count - 1));
        }

        private void OnAvailableDoubleClick(object sender,
            System.Windows.Input.MouseButtonEventArgs e)
        {
            if (lstAvailable.SelectedItem is string f && !_analysis.Contains(f))
            {
                _analysis.Add(f);
                RefreshLists(_analysis.Count - 1);
            }
        }

        private void OnAnalysisDoubleClick(object sender,
            System.Windows.Input.MouseButtonEventArgs e)
        {
            var idx = lstAnalysis.SelectedIndex;
            if (idx >= 0)
            {
                _analysis.RemoveAt(idx);
                RefreshLists();
            }
        }

        // ---- analysis order (Top / Up / Down / Bottom) ----------------------
        private void Move(int delta, bool toEnd)
        {
            int i = lstAnalysis.SelectedIndex;
            if (i < 0 || _analysis.Count < 2) return;
            string f = _analysis[i];
            _analysis.RemoveAt(i);
            int target = toEnd
                ? (delta < 0 ? 0 : _analysis.Count)
                : Math.Min(Math.Max(i + delta, 0), _analysis.Count);
            _analysis.Insert(target, f);
            RefreshLists(target);
        }

        private void OnMoveTop(object sender, RoutedEventArgs e) => Move(-1, true);
        private void OnMoveUp(object sender, RoutedEventArgs e) => Move(-1, false);
        private void OnMoveDown(object sender, RoutedEventArgs e) => Move(1, false);
        private void OnMoveBottom(object sender, RoutedEventArgs e) => Move(1, true);

        // ---- format editing -------------------------------------------------
        private void OnToken(object sender, RoutedEventArgs e) =>
            InsertToken((sender as FrameworkElement)?.Tag as string);

        private void InsertToken(string tok)
        {
            if (string.IsNullOrEmpty(tok)) return;
            int caret = txtFormat.CaretIndex;
            txtFormat.Text = txtFormat.Text.Insert(caret, tok);
            txtFormat.CaretIndex = caret + tok.Length;
            txtFormat.Focus();
        }

        /// <summary>A statistics button: its placeholder is written for the
        /// field and the decimals chosen beside the buttons, e.g.
        /// {Mean:ElevationM:1}.</summary>
        private void OnStatToken(object sender, RoutedEventArgs e)
        {
            string tag = (sender as FrameworkElement)?.Tag as string;
            if (!Enum.TryParse(tag, true, out SummaryStats.Kind kind)) return;
            string field = cboStatField.SelectedItem as string;
            if (string.IsNullOrEmpty(field))
            {
                SetStatus("Choose the numeric field for the statistics first.");
                return;
            }
            int dec = cboStatDecimals.SelectedItem is int d
                ? d : SummaryStats.DefaultDecimals;
            InsertToken(SummaryStats.TokenText(kind, field, dec));
        }

        private void OnResetFormat(object sender, RoutedEventArgs e) =>
            txtFormat.Text = AttributeSummary.DefaultFormat;

        private void OnFormatChanged(object sender, TextChangedEventArgs e)
        {
            UpdateSample();
            QueueMarks();
        }

        private void OnOptionChanged(object sender, RoutedEventArgs e)
        {
            UpdateSample();
            UpdateTokenRows();
            QueueMarks();
        }

        private void OnFormatLayoutChanged(object sender, SizeChangedEventArgs e) =>
            QueueMarks();

        private void OnFormatScrolled(object sender, ScrollChangedEventArgs e) =>
            QueueMarks();

        /// <summary>Shows the placeholder rows that can do something: the
        /// Geometry row while geometry is being totaled, holding only the
        /// buttons the dataset suits, and the Statistics row while
        /// statistics are asked for. (Controls are null while the XAML is
        /// still loading.)</summary>
        private void UpdateTokenRows()
        {
            if (pnlGeom == null || pnlStats == null) return;
            System.Windows.Visibility Show(bool on) => on
                ? System.Windows.Visibility.Visible
                : System.Windows.Visibility.Collapsed;

            var kind = SampleKind();
            pnlGeom.Visibility = Show(kind != AttributeSummary.WeightKind.None);
            btnGeomLength.Visibility =
                Show(kind == AttributeSummary.WeightKind.LengthM);
            btnGeomArea.Visibility =
                Show(kind == AttributeSummary.WeightKind.AreaM2 ||
                     kind == AttributeSummary.WeightKind.Cells);
            btnCells.Visibility =
                Show(kind == AttributeSummary.WeightKind.Cells);

            bool stats = chkStats.IsChecked == true;
            bool any = _numeric.Count > 0;
            pnlStats.Visibility = Show(stats);
            cboStatField.IsEnabled = any;
            pnlStatButtons.IsEnabled = any;
            txtNoNumeric.Visibility = Show(stats && _fieldsLoaded && !any);
        }

        // ---- marking placeholders that are wrong or idle -------------------
        private static readonly System.Windows.Media.Brush ErrBrush =
            new System.Windows.Media.SolidColorBrush(
                System.Windows.Media.Color.FromRgb(0xC2, 0x27, 0x18));
        private static readonly System.Windows.Media.Brush NoteBrush =
            new System.Windows.Media.SolidColorBrush(
                System.Windows.Media.Color.FromRgb(0xB0, 0x6A, 0x00));
        private static readonly System.Text.RegularExpressions.Regex GeomRx =
            new System.Text.RegularExpressions.Regex(
                @"\{(GeomLength_km|GeomLength_m|GeomArea_km2|GeomArea_ha|GeomArea_m2|Cells|GeomProp\d+)\}",
                System.Text.RegularExpressions.RegexOptions.IgnoreCase);

        private sealed class Mark
        {
            public int Index, Length;
            public bool Error;       // red: Run Report refuses; else amber
        }

        /// <summary>The placeholders to underline. RED: a statistics
        /// placeholder whose field this dataset does not have as a numeric
        /// field (a format saved on another dataset, or a typing slip).
        /// AMBER: a placeholder that will print nothing as things stand --
        /// statistics or geometry not being gathered, or geometry of a kind
        /// this dataset does not have.</summary>
        private List<Mark> FindMarks(out string note, out bool anyError)
        {
            var marks = new List<Mark>();
            var missing = new List<string>();
            bool statsOff = false, geomIdle = false;
            string text = txtFormat.Text ?? "";

            bool wantStats = chkStats.IsChecked == true;
            foreach (var tk in SummaryStats.Parse(text))
            {
                if (!wantStats)
                {
                    statsOff = true;
                    marks.Add(new Mark { Index = tk.Index, Length = tk.Length });
                }
                else if (_fieldsLoaded && !_numeric.Any(f => string.Equals(
                             f, tk.Field, StringComparison.OrdinalIgnoreCase)))
                {
                    if (!missing.Contains(tk.Field,
                            StringComparer.OrdinalIgnoreCase))
                        missing.Add(tk.Field);
                    marks.Add(new Mark
                    { Index = tk.Index, Length = tk.Length, Error = true });
                }
            }

            if (cboSource.SelectedItem is PolarPlotData.Source)
            {
                var kind = SampleKind();
                foreach (System.Text.RegularExpressions.Match m
                         in GeomRx.Matches(text))
                {
                    string name = m.Groups[1].Value.ToLowerInvariant();
                    bool prints = kind != AttributeSummary.WeightKind.None;
                    if (prints && name.StartsWith("geomlength"))
                        prints = kind == AttributeSummary.WeightKind.LengthM;
                    else if (prints && name.StartsWith("geomarea"))
                        prints = kind == AttributeSummary.WeightKind.AreaM2 ||
                                 kind == AttributeSummary.WeightKind.Cells;
                    else if (prints && name == "cells")
                        prints = kind == AttributeSummary.WeightKind.Cells;
                    if (prints) continue;
                    geomIdle = true;
                    marks.Add(new Mark { Index = m.Index, Length = m.Length });
                }
            }

            var lines = new List<string>();
            if (missing.Count > 0)
                lines.Add("No numeric field" + (missing.Count == 1 ? "" : "s") +
                          " named " + string.Join(", ", missing) +
                          " in this dataset.");
            if (statsOff)
                lines.Add("The underlined statistics will print nothing: " +
                          "'Include statistics of numeric fields' is not checked.");
            if (geomIdle)
                lines.Add(chkGeometry.IsChecked == true
                    ? "The underlined geometry placeholders will print " +
                      "nothing for this dataset."
                    : "The underlined geometry placeholders will print " +
                      "nothing: 'Include geometry totals' is not checked.");
            note = string.Join(Environment.NewLine, lines);
            anyError = missing.Count > 0;
            return marks;
        }

        /// <summary>Redraw once the text box has laid itself out (the
        /// character rectangles are only right after that).</summary>
        private void QueueMarks()
        {
            if (cvMarks == null) return;
            Dispatcher.BeginInvoke(new Action(DrawMarks),
                System.Windows.Threading.DispatcherPriority.Loaded);
        }

        private void DrawMarks()
        {
            if (cvMarks == null || txtFormat == null || txtFormatNote == null)
                return;
            cvMarks.Children.Clear();
            List<Mark> marks;
            string note;
            bool anyError;
            try { marks = FindMarks(out note, out anyError); }
            catch { return; }

            txtFormatNote.Text = note;
            txtFormatNote.Foreground = anyError ? ErrBrush : NoteBrush;
            txtFormatNote.Visibility = note.Length > 0
                ? System.Windows.Visibility.Visible
                : System.Windows.Visibility.Collapsed;
            if (!txtFormat.IsLoaded) return;

            int len = txtFormat.Text.Length;
            foreach (var mk in marks)
            {
                var brush = mk.Error ? ErrBrush : NoteBrush;
                for (int i = mk.Index; i < mk.Index + mk.Length && i < len; i++)
                {
                    Rect a, b;
                    try
                    {
                        a = txtFormat.GetRectFromCharacterIndex(i, false);
                        b = txtFormat.GetRectFromCharacterIndex(i, true);
                    }
                    catch { continue; }
                    if (a.IsEmpty || double.IsInfinity(a.Left) ||
                        double.IsInfinity(a.Bottom)) continue;
                    double x1 = a.Left;
                    double x2 = b.IsEmpty || Math.Abs(b.Top - a.Top) > 0.5 ||
                                b.Left <= x1 ? x1 + 7.0 : b.Left;
                    cvMarks.Children.Add(new System.Windows.Shapes.Line
                    {
                        X1 = x1, X2 = x2,
                        Y1 = a.Bottom - 0.5, Y2 = a.Bottom - 0.5,
                        Stroke = brush, StrokeThickness = 2,
                        SnapsToDevicePixels = true,
                    });
                }
            }
        }

        /// <summary>The weight kind the CURRENT source would produce -- so
        /// the sample line previews the geometry tokens realistically.</summary>
        private AttributeSummary.WeightKind SampleKind()
        {
            if (chkGeometry?.IsChecked != true)
                return AttributeSummary.WeightKind.None;
            var src = cboSource?.SelectedItem as PolarPlotData.Source;
            if (src == null) return AttributeSummary.WeightKind.AreaM2;
            if (src.IsRaster) return AttributeSummary.WeightKind.Cells;
            switch (src.FeatureLayer?.ShapeType)
            {
                case esriGeometryType.esriGeometryPolyline:
                    return AttributeSummary.WeightKind.LengthM;
                case esriGeometryType.esriGeometryPolygon:
                    return AttributeSummary.WeightKind.AreaM2;
                default:
                    return AttributeSummary.WeightKind.None;
            }
        }

        private void UpdateSample()
        {
            if (txtSample == null) return;
            int n = chkCombine?.IsChecked == true
                ? Math.Max(_analysis.Count, 1) : 1;
            var vals = new List<string>();
            for (int i = 0; i < n; i++)
                vals.Add(i < _analysis.Count
                    ? "<" + _analysis[i] + " value>"
                    : "<value " + (i + 1) + ">");
            try
            {
                Func<string, SummaryStats.FieldStats> stats = null;
                if (chkStats?.IsChecked == true)
                    stats = f => !_fieldsLoaded || _numeric.Any(n =>
                        string.Equals(n, f, StringComparison.OrdinalIgnoreCase))
                        ? SummaryStats.Sample() : null;
                txtSample.Text = AttributeSummary.Render(
                    txtFormat.Text, vals, 3, 1234, 25230,
                    weight: 456789.5, totalWeight: 3456789.5,
                    kind: SampleKind(), cellAreaM2: 900.0, stats: stats);
            }
            catch (Exception ex) { txtSample.Text = "(format error: " + ex.Message + ")"; }
        }

        // ---- format presets -------------------------------------------------
        private void OnSaveFormat(object sender, RoutedEventArgs e)
        {
            var dlg = new Microsoft.Win32.SaveFileDialog
            {
                Filter = "Summary format (*.json)|*.json",
                InitialDirectory = SummaryFormatStore.StoreDir(),
                FileName = "MySummaryFormat.json",
                Title = "Save format preset",
            };
            if (dlg.ShowDialog() != true) return;
            try
            {
                SummaryFormatStore.Save(dlg.FileName, txtFormat.Text);
                SetStatus("Format saved: " +
                          System.IO.Path.GetFileName(dlg.FileName));
            }
            catch (Exception ex)
            { MessageBox.Show("Could not save the format: " + ex.Message,
                "Attribute Summary Reports"); }
        }

        private void OnLoadFormat(object sender, RoutedEventArgs e)
        {
            var dlg = new Microsoft.Win32.OpenFileDialog
            {
                Filter = "Summary format (*.json)|*.json",
                InitialDirectory = SummaryFormatStore.StoreDir(),
                Title = "Load format preset",
            };
            if (dlg.ShowDialog() != true) return;
            try
            {
                string text = SummaryFormatStore.Load(dlg.FileName);
                if (text == null)
                {
                    MessageBox.Show("This file is not a summary format preset.",
                        "Attribute Summary Reports");
                    return;
                }
                txtFormat.Text = text;
                SetStatus("Format loaded: " +
                          System.IO.Path.GetFileName(dlg.FileName));
            }
            catch (Exception ex)
            { MessageBox.Show("Could not load the format: " + ex.Message,
                "Attribute Summary Reports"); }
        }

        // ---- run ------------------------------------------------------------
        private async void OnRunClick(object sender, RoutedEventArgs e)
        {
            var src = cboSource.SelectedItem as PolarPlotData.Source;
            if (src == null)
            {
                MessageBox.Show("Choose a dataset first.", "Attribute Summary Reports");
                return;
            }
            if (_analysis.Count == 0)
            {
                MessageBox.Show("Add at least one field to the analysis list.",
                    "Attribute Summary Reports");
                return;
            }
            bool combine = chkCombine.IsChecked == true;
            bool sort = chkSort.IsChecked == true;
            bool selOnly = rbSelected.IsChecked == true;
            bool geometry = chkGeometry.IsChecked == true;
            string format = txtFormat.Text;
            var fields = _analysis.ToList();

            // statistics: the fields the format asks for, in this dataset's
            // own spelling. A placeholder for a field the dataset lacks
            // would silently print nothing, so the run is refused instead.
            bool wantStats = chkStats.IsChecked == true;
            var statFields = new List<string>();
            if (wantStats)
            {
                var missing = new List<string>();
                foreach (var tk in SummaryStats.Parse(format))
                {
                    string hit = _numeric.FirstOrDefault(f => string.Equals(
                        f, tk.Field, StringComparison.OrdinalIgnoreCase));
                    if (hit == null)
                    {
                        if (!missing.Contains(tk.Field,
                                StringComparer.OrdinalIgnoreCase))
                            missing.Add(tk.Field);
                    }
                    else if (!statFields.Contains(hit)) statFields.Add(hit);
                }
                if (missing.Count > 0)
                {
                    MessageBox.Show(
                        "The format asks for statistics of " +
                        (missing.Count == 1 ? "a field" : "fields") +
                        " that this dataset does not have as a numeric field:" +
                        Environment.NewLine + Environment.NewLine + "    " +
                        string.Join(Environment.NewLine + "    ", missing) +
                        Environment.NewLine + Environment.NewLine +
                        "Correct or remove the placeholders underlined in " +
                        "red in the Format Text box, then run the report again.",
                        "Attribute Summary Reports");
                    return;
                }
            }
            int statDecimals = cboStatDecimals.SelectedItem is int sd
                ? sd : SummaryStats.DefaultDecimals;

            btnRun.IsEnabled = false;
            SetStatus("Summarizing " + string.Join(", ", fields) + " ...");
            try
            {
                var result = await QueuedTask.Run(() =>
                    AttributeSummary.Gather(src, fields, selOnly, combine,
                                            sort, geometry, statFields));
                SetStatus(result.TotalCount.ToString("N0") +
                    " record(s) analyzed; " +
                    result.Sections.Sum(s2 => s2.Classes.Count).ToString("N0") +
                    " class(es) found.");
                new AttributeSummaryReportWindow(result, format)
                { Owner = this }.Show();
                SummaryFormatStore.SaveSettings(new SummaryFormatStore.Settings
                {
                    FormatText = format,
                    Combine = combine,
                    Sort = sort,
                    SelectedOnly = selOnly,
                    IncludeGeometry = geometry,
                    IncludeStatistics = wantStats,
                    StatDecimals = statDecimals,
                });
            }
            catch (Exception ex)
            {
                SetStatus("Error: " + ex.Message);
                MessageBox.Show(ex.Message, "Attribute Summary Reports");
            }
            finally { btnRun.IsEnabled = true; }
        }

        private void OnHelp(object sender, RoutedEventArgs e)
            => WebHelp.Open("data-tools/attribute-summary-reports.html");

        private void OnCloseClick(object sender, RoutedEventArgs e) => Close();
    }
}
