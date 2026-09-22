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
        private bool _loading = true;

        public AttributeSummaryWindow()
        {
            InitializeComponent();
            var s = SummaryFormatStore.LoadSettings();
            txtFormat.Text = string.IsNullOrWhiteSpace(s.FormatText)
                ? AttributeSummary.DefaultFormat : s.FormatText;
            chkCombine.IsChecked = s.Combine;
            chkSort.IsChecked = s.Sort;
            chkGeometry.IsChecked = s.IncludeGeometry;
            rbSelected.IsChecked = s.SelectedOnly;
            rbAll.IsChecked = !s.SelectedOnly;
            Loaded += async (_, __) => await LoadSourcesAsync();
            UpdateSample();
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
            List<string> fields;
            try
            {
                fields = await QueuedTask.Run(
                    () => AttributeSummary.ListSummaryFields(src));
            }
            catch { fields = new List<string>(); }
            _available.Clear();
            _available.AddRange(fields);
            _analysis.Clear();
            RefreshLists();
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
        private void OnToken(object sender, RoutedEventArgs e)
        {
            string tok = (sender as FrameworkElement)?.Tag as string;
            if (string.IsNullOrEmpty(tok)) return;
            int caret = txtFormat.CaretIndex;
            txtFormat.Text = txtFormat.Text.Insert(caret, tok);
            txtFormat.CaretIndex = caret + tok.Length;
            txtFormat.Focus();
        }

        private void OnResetFormat(object sender, RoutedEventArgs e) =>
            txtFormat.Text = AttributeSummary.DefaultFormat;

        private void OnFormatChanged(object sender, TextChangedEventArgs e) =>
            UpdateSample();

        private void OnOptionChanged(object sender, RoutedEventArgs e) =>
            UpdateSample();

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
                txtSample.Text = AttributeSummary.Render(
                    txtFormat.Text, vals, 3, 1234, 25230,
                    weight: 456789.5, totalWeight: 3456789.5,
                    kind: SampleKind(), cellAreaM2: 900.0);
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

            btnRun.IsEnabled = false;
            SetStatus("Summarizing " + string.Join(", ", fields) + " ...");
            try
            {
                var result = await QueuedTask.Run(() =>
                    AttributeSummary.Gather(src, fields, selOnly, combine,
                                            sort, geometry));
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
                });
            }
            catch (Exception ex)
            {
                SetStatus("Error: " + ex.Message);
                MessageBox.Show(ex.Message, "Attribute Summary Reports");
            }
            finally { btnRun.IsEnabled = true; }
        }

        private void OnCloseClick(object sender, RoutedEventArgs e) => Close();
    }
}
