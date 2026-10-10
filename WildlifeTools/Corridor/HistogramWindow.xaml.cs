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
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using ArcGIS.Desktop.Catalog;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;
using WildlifeTools.PolarPlots;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Histograms and Habitat Suitability Statistics: compare the
    /// distribution of habitat suitability (or any numeric data) across
    /// corridor alternatives as side-by-side histograms with optional
    /// weighted kernel-density curves. Port of the CorridorDesigner
    /// Evaluation Tools' histogram tool, modernized: live options (no
    /// Apply button), automatic Y-unit conversion, exact full-raster
    /// reads, clip-to-polygon at add time, colorblind-safe palette,
    /// JSON dataset presets, statistics export to CSV / dBASE /
    /// geodatabase, and Add to Layout as native editable graphics.
    /// </summary>
    public partial class HistogramWindow
        : ArcGIS.Desktop.Framework.Controls.ProWindow,
          ArcGIS.Desktop.Framework.DragDrop.IDropTarget
    {
        private readonly List<HistDataset> _datasets = new List<HistDataset>();
        private readonly HistogramOptions _options = new HistogramOptions();
        private PolarPlotModel _model;
        private bool _loading = true;      // suppress events during setup
        private bool _closedDown;
        private int _palette;              // next palette slot
        private ArcGIS.Core.Events.SubscriptionToken _appClosingToken;

        public HistogramWindow()
        {
            InitializeComponent();
            global::WildlifeTools.Corridor.JennessIcon.Apply(this);
            ArcGIS.Desktop.Framework.DragDrop.DragDrop.SetDropHandler(this, this);
            AllowDrop = true;
            Loaded += (_, __) =>
            {
                _loading = false;
                txtTitle.Text = _options.Title;
                txtXLabel.Text = _options.XLabel;
                txtBins.Text = _options.Bins.ToString(CultureInfo.InvariantCulture);
                RefreshUnitCombo();
                Rebuild();
                // when PRO closes with this window open, stop all worker
                // activity immediately (standing shutdown rule)
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
                if (_appClosingToken != null)
                {
                    try
                    {
                        ArcGIS.Desktop.Framework.Events.ApplicationClosingEvent
                            .Unsubscribe(_appClosingToken);
                    }
                    catch { }
                    _appClosingToken = null;
                }
            };
        }

        // =================================================================
        // options plumbing (all controls converge on Rebuild)
        // =================================================================
        private void OnOption(object sender, RoutedEventArgs e) => Rebuild();
        private void OnOptionText(object sender, TextChangedEventArgs e) => Rebuild();

        private void OnSlider(object sender,
            RoutedPropertyChangedEventArgs<double> e)
        {
            if (lblGap != null)
                lblGap.Text = ((int)sldGap.Value) + "%";
            if (lblBw != null)
                lblBw.Text = sldBw.Value.ToString("0.0",
                    CultureInfo.InvariantCulture) + "×";
            Rebuild();
        }

        private void OnUnitChanged(object sender, SelectionChangedEventArgs e)
        {
            if (pnlMult != null && cboUnit.SelectedItem is UnitChoice uc)
                pnlMult.Visibility = uc.Unit == YUnit.Custom
                    ? Visibility.Visible : Visibility.Collapsed;
            Rebuild();
        }

        private void OnChartSized(object sender, SizeChangedEventArgs e)
        {
            double win = Math.Max(e.NewSize.Width, 200) / 96.0;
            double hin = Math.Max(e.NewSize.Height, 150) / 96.0;
            win = Math.Min(Math.Max(win, 3.0), 14.0);
            hin = Math.Min(Math.Max(hin, 2.2), 10.0);
            if (Math.Abs(win - _options.WidthIn) < 0.06 &&
                Math.Abs(hin - _options.HeightIn) < 0.06) return;
            _options.WidthIn = win;
            _options.HeightIn = hin;
            Rebuild();
        }

        private sealed class UnitChoice
        {
            public YUnit Unit;
            public string Label;
            public override string ToString() => Label;
        }

        private void RefreshUnitCombo()
        {
            var keep = (cboUnit.SelectedItem as UnitChoice)?.Unit;
            var units = HistogramMath.AvailableUnits(_datasets);
            var items = units.Select(u => new UnitChoice
            { Unit = u, Label = HistogramMath.UnitLabel(u, _datasets) }).ToList();
            cboUnit.ItemsSource = items;
            var hit = items.FirstOrDefault(i => i.Unit == keep);
            cboUnit.SelectedItem = hit ?? items[0];
        }

        private void ParseOptions()
        {
            _options.Title = txtTitle.Text;
            _options.ShowTitle = chkTitle.IsChecked == true;
            _options.XLabel = txtXLabel.Text;
            _options.ShowXLabel = chkXLabel.IsChecked == true;
            _options.YLabel = txtYLabel.Text;
            _options.ShowYLabel = chkYLabel.IsChecked == true;
            _options.ShowLegend = chkLegend.IsChecked == true;
            _options.ShowRefLines = chkRefLines.IsChecked == true;
            if (int.TryParse(txtBins.Text, out int nb))
                _options.Bins = Math.Min(Math.Max(nb, 1), 200);
            _options.AutoRange = chkAutoRange.IsChecked == true;
            if (_options.AutoRange && _datasets.Any(d => d.Stats.SumWeights > 0))
            {
                double mn = _datasets.Min(d => d.Stats.Min);
                double mx = _datasets.Max(d => d.Stats.Max);
                if (mx - mn > 2) { mn = Math.Floor(mn); mx = Math.Ceiling(mx); }
                else if (mx <= mn) { mx = mn + 1; }
                _options.AxisMin = mn;
                _options.AxisMax = mx;
                _loading = true;
                txtMin.Text = mn.ToString("0.####", CultureInfo.InvariantCulture);
                txtMax.Text = mx.ToString("0.####", CultureInfo.InvariantCulture);
                _loading = false;
            }
            else
            {
                if (double.TryParse(txtMin.Text, NumberStyles.Float,
                        CultureInfo.InvariantCulture, out double mn))
                    _options.AxisMin = mn;
                if (double.TryParse(txtMax.Text, NumberStyles.Float,
                        CultureInfo.InvariantCulture, out double mx))
                    _options.AxisMax = mx;
            }
            _options.GapFraction = sldGap.Value / 100.0;
            _options.YPercent = chkPercent.IsChecked == true;
            _options.Unit = (cboUnit.SelectedItem as UnitChoice)?.Unit
                            ?? YUnit.Native;
            if (double.TryParse(txtMult.Text, NumberStyles.Float,
                    CultureInfo.InvariantCulture, out double mult) && mult > 0)
                _options.CustomMultiplier = mult;
            _options.CurvesOnly = chkCurvesOnly.IsChecked == true;
            _options.KdeBandwidthMult = sldBw.Value;
        }

        // =================================================================
        // rebuild + render
        // =================================================================
        private void Rebuild()
        {
            if (_loading || _closedDown) return;
            ParseOptions();
            bool any = _datasets.Any(d => d.Stats.SumWeights > 0);
            txtChartHint.Visibility = any ? Visibility.Collapsed
                                          : Visibility.Visible;
            foreach (var b in new[] { btnExportTables, btnSaveGraph,
                                      btnAddToLayout, btnCopyStats,
                                      btnSavePreset })
                b.IsEnabled = any;
            if (!any) { imgChart.Source = null; return; }
            _model = HistogramBuilder.Build(_datasets, _options,
                                            MeasureInches);
            var dg = PolarPlotWpf.Render(_model);
            dg.Freeze();
            imgChart.Source = new DrawingImage(dg);
            RenderStats();
        }

        /// <summary>Real WPF text width in inches for the chart builder --
        /// the legend centers exactly instead of by estimate.</summary>
        private double MeasureInches(string text, double sizePt, string family)
        {
            try
            {
                var tf = new Typeface(
                    new FontFamily(string.IsNullOrEmpty(family)
                        ? "Segoe UI" : family),
                    FontStyles.Normal, FontWeights.Normal,
                    FontStretches.Normal);
                var ft = new FormattedText(text ?? "",
                    CultureInfo.InvariantCulture, FlowDirection.LeftToRight,
                    tf, sizePt * 96.0 / 72.0, Brushes.Black,
                    VisualTreeHelper.GetDpi(this).PixelsPerDip);
                return ft.WidthIncludingTrailingWhitespace / 96.0;
            }
            catch
            {
                return 0.075 * (text?.Length ?? 0) * sizePt / 9.0;
            }
        }

        private void OnFonts(object sender, RoutedEventArgs e)
        {
            if (FontsPrompt.Show(this, _options)) Rebuild();
        }

        private void RefreshDatasetList(int? select = null)
        {
            int keep = select ?? lstDatasets.SelectedIndex;
            lstDatasets.ItemsSource = _datasets.Select((d, i) =>
                string.Format("{0}]  {1}   ({2})", i + 1, d.Name,
                              HistogramStore.BasisWord(d))).ToList();
            if (keep >= 0 && keep < _datasets.Count)
                lstDatasets.SelectedIndex = keep;
            RefreshUnitCombo();
            ApplyModeUi();
        }

        // =================================================================
        // categorical mode: one bar per class, no bins or curves
        // =================================================================
        private bool Categorical => _datasets.Any(d => d.IsCategorical);

        /// <summary>Bins, axis range and density-curve controls apply only
        /// to continuous data; in categorical mode they are disabled and a
        /// one-line note says why.</summary>
        private void ApplyModeUi()
        {
            bool cat = Categorical;
            foreach (var c in new Control[] { txtBins, txtMin, txtMax,
                chkAutoRange, chkCurvesOnly, sldBw, btnToggleKde })
                c.IsEnabled = !cat;
            int n = cat ? HistogramMath.UnionCategories(_datasets).Count : 0;
            lblCategorical.Text = cat
                ? string.Format("Categorical data: one bar per class ({0} "
                    + "class{1}). Bins, range and density curves do not "
                    + "apply.", n, n == 1 ? "" : "es")
                : "";
            lblCategorical.Visibility = cat ? Visibility.Visible
                                            : Visibility.Collapsed;
        }

        /// <summary>A categorical dataset cannot share the X axis with a
        /// continuous one (the axis is either a number line or a list of
        /// classes). Null = compatible; else the message to show.</summary>
        private string MixMessage(IEnumerable<HistDataset> incoming)
        {
            var all = _datasets.Concat(incoming).ToList();
            bool anyCat = all.Any(d => d.IsCategorical);
            bool anyNum = all.Any(d => !d.IsCategorical);
            if (!(anyCat && anyNum)) return null;
            bool haveCat = Categorical;
            return haveCat
                ? "This histogram holds categorical datasets (one bar per "
                  + "class). A numeric dataset cannot share its axis. Remove "
                  + "the categorical datasets first, or open a second "
                  + "Histogram window for the numeric data."
                : "This histogram holds numeric datasets. A categorical "
                  + "dataset (one bar per class) cannot share their axis. "
                  + "Remove the numeric datasets first, or open a second "
                  + "Histogram window for the categorical data.";
        }

        /// <summary>Jeff: warn when the categorical datasets do not carry
        /// the same set of classes -- an empty slot then means "none of
        /// that class here", which is only meaningful if both datasets were
        /// classified the same way.</summary>
        private void WarnCategoryMismatch()
        {
            if (!Categorical || _datasets.Count(d => d.IsCategorical) < 2) return;
            var miss = HistogramMath.MissingCategories(_datasets);
            if (miss.Count == 0) return;
            var sb = new StringBuilder();
            sb.AppendLine("The datasets do not all carry the same classes:");
            sb.AppendLine();
            foreach (var (d, m) in miss)
                sb.AppendLine("  '" + d.Name + "' has no:  "
                              + string.Join(", ", m));
            sb.AppendLine();
            sb.Append("An empty slot on the chart means the dataset has none "
                + "of that class. That is a real finding when both datasets "
                + "were classified the same way (same attribute column or "
                + "symbology); if they were not, the classes will not "
                + "correspond and the comparison is not meaningful.");
            txtStatus.Text = "Note: the datasets' class sets differ ("
                + string.Join("; ", miss.Select(x =>
                    "'" + x.d.Name + "' lacks " + x.missing.Count)) + ").";
            MessageBox.Show(sb.ToString(), "Class sets differ");
        }

        // =================================================================
        // add data
        // =================================================================
        private void OnAddData(object sender, RoutedEventArgs e) =>
            _ = AddDataFlow(null);

        private async Task AddDataFlow(Layer preselect)
        {
            var dlg = new HistogramAddDataWindow(preselect) { Owner = this };
            if (dlg.ShowDialog() != true || dlg.Result == null) return;
            var req = dlg.Result;
            btnAdd.IsEnabled = false;
            prgRead.Visibility = Visibility.Visible;
            prgRead.IsIndeterminate = false;
            prgRead.Value = 0;
            txtStatus.Text = "Reading " + req.Source.Name + "...";
            try
            {
                var clipLayer = dlg.ClipLayer;
                var d = await QueuedTask.Run(() =>
                {
                    if (_closedDown) return null;
                    if (clipLayer != null)
                    {
                        req.ClipPolygon = HistogramData.BuildClipPolygon(
                            clipLayer, out string cn);
                        req.ClipName = "clipped to " + cn;
                    }
                    return HistogramData.Read(req, p =>
                    {
                        if (p >= 0)
                            Dispatcher.InvokeAsync(() => prgRead.Value = p * 100);
                    });
                });
                if (d == null || _closedDown) return;
                string mix = MixMessage(new[] { d });
                if (mix != null)
                {
                    txtStatus.Text = "Not added: " + d.Name;
                    MessageBox.Show(mix, "Add Data to Histogram");
                    return;
                }
                if (!string.IsNullOrEmpty(dlg.RequestedName) &&
                    req.ClipPolygon == null)
                    d.Name = dlg.RequestedName;
                else if (!string.IsNullOrEmpty(dlg.RequestedName))
                    d.Name = dlg.RequestedName + " [" + req.ClipName + "]";
                d.Fill = HistogramMath.PaletteFill(_palette);
                d.Outline = HistogramMath.PaletteOutline(_palette);
                _palette++;
                if (d.IsCategorical && _datasets.Count == 0 &&
                    txtXLabel.Text == "Habitat Suitability")
                    txtXLabel.Text = "Class";     // the numeric default
                _datasets.Add(d);
                RefreshDatasetList(_datasets.Count - 1);
                Rebuild();
                txtStatus.Text = d.IsCategorical
                    ? string.Format(CultureInfo.InvariantCulture,
                        "Added '{0}': {1:#,##0} class(es), total weight "
                        + "{2:#,##0.###} ({3}).", d.Name, d.Values.Length,
                        d.Stats.SumWeights, HistogramStore.BasisWord(d))
                    : string.Format(CultureInfo.InvariantCulture,
                        "Added '{0}': {1:#,##0} distinct value(s), total weight "
                        + "{2:#,##0.###} ({3}).", d.Name, d.Values.Length,
                        d.Stats.SumWeights, HistogramStore.BasisWord(d));
                WarnCategoryMismatch();
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Could not add the dataset: " + ex.Message;
                MessageBox.Show(ex.Message, "Add Data to Histogram");
            }
            finally
            {
                btnAdd.IsEnabled = true;
                prgRead.Visibility = Visibility.Collapsed;
            }
        }

        // =================================================================
        // dataset list buttons
        // =================================================================
        private HistDataset SelectedDataset =>
            lstDatasets.SelectedIndex >= 0 &&
            lstDatasets.SelectedIndex < _datasets.Count
                ? _datasets[lstDatasets.SelectedIndex] : null;

        private void OnDatasetSelected(object sender, SelectionChangedEventArgs e) { }

        private void OnRename(object sender, RoutedEventArgs e)
        {
            var d = SelectedDataset;
            if (d == null) { txtStatus.Text = "Select a dataset first."; return; }
            string name = TextPrompt.Show(this, "Rename Dataset",
                "New name for the legend and reports:", d.Name);
            if (string.IsNullOrWhiteSpace(name)) return;
            d.Name = name.Trim();
            RefreshDatasetList();
            Rebuild();
        }

        private void OnRecolor(object sender, RoutedEventArgs e)
        {
            var d = SelectedDataset;
            if (d == null) { txtStatus.Text = "Select a dataset first."; return; }
            var picked = ColorSwatchPrompt.Show(this,
                "Color for '" + d.Name + "'");
            if (picked == null) return;
            var c = picked.Value;
            d.Fill = new RgbaColor(c.R, c.G, c.B, 210);
            d.Outline = new RgbaColor((byte)(c.R * 0.6), (byte)(c.G * 0.6),
                                      (byte)(c.B * 0.6), 255);
            Rebuild();
        }

        private void OnToggleBars(object sender, RoutedEventArgs e)
        {
            var d = SelectedDataset;
            if (d == null) { txtStatus.Text = "Select a dataset first."; return; }
            d.ShowBars = !d.ShowBars;
            Rebuild();
        }

        private void OnToggleKde(object sender, RoutedEventArgs e)
        {
            var d = SelectedDataset;
            if (d == null) { txtStatus.Text = "Select a dataset first."; return; }
            d.ShowKde = !d.ShowKde;
            Rebuild();
        }

        private void MoveDataset(int delta)
        {
            int i = lstDatasets.SelectedIndex;
            if (i < 0 || i >= _datasets.Count) return;
            int j = i + delta;
            if (j < 0 || j >= _datasets.Count) return;
            (_datasets[i], _datasets[j]) = (_datasets[j], _datasets[i]);
            RefreshDatasetList(j);
            Rebuild();
        }

        private void OnMoveUp(object sender, RoutedEventArgs e) => MoveDataset(-1);
        private void OnMoveDown(object sender, RoutedEventArgs e) => MoveDataset(1);

        private void OnRemove(object sender, RoutedEventArgs e)
        {
            int i = lstDatasets.SelectedIndex;
            if (i < 0 || i >= _datasets.Count)
            { txtStatus.Text = "Select a dataset first."; return; }
            _datasets.RemoveAt(i);
            RefreshDatasetList(Math.Min(i, _datasets.Count - 1));
            Rebuild();
        }

        // =================================================================
        // presets
        // =================================================================
        private static string PresetDir()
        {
            string b = Environment.GetFolderPath(
                Environment.SpecialFolder.LocalApplicationData);
            string dir = Path.Combine(b, "JennessEnterprises", "WildlifeTools",
                                      "histogram_presets");
            Directory.CreateDirectory(dir);
            return dir;
        }

        /// <summary>Default save location: the current PROJECT's home
        /// folder (Jeff: not the Windows user profile), falling back to the
        /// per-user preset store.</summary>
        private static string DefaultDir()
        {
            try
            {
                string p = ArcGIS.Desktop.Core.Project.Current?.HomeFolderPath;
                if (!string.IsNullOrEmpty(p) && Directory.Exists(p)) return p;
            }
            catch { }
            return PresetDir();
        }

        private void OnSavePreset(object sender, RoutedEventArgs e)
        {
            var dlg = new Microsoft.Win32.SaveFileDialog
            {
                Filter = "Histogram datasets (*.json)|*.json",
                InitialDirectory = DefaultDir(),
                FileName = "MyHistogramDatasets.json",
                Title = "Save histogram datasets",
                DefaultExt = ".json",
                AddExtension = true,
            };
            if (dlg.ShowDialog() != true) return;
            if (string.IsNullOrEmpty(Path.GetExtension(dlg.FileName)))
                dlg.FileName += ".json";   // typed without an extension
            try
            {
                HistogramStore.Save(dlg.FileName, _datasets);
                txtStatus.Text = "Saved " + _datasets.Count + " dataset(s): "
                    + Path.GetFileName(dlg.FileName);
            }
            catch (Exception ex)
            { MessageBox.Show("Could not save: " + ex.Message, Title); }
        }

        private void OnLoadPreset(object sender, RoutedEventArgs e)
        {
            var dlg = new Microsoft.Win32.OpenFileDialog
            {
                Filter = "Histogram datasets (*.json)|*.json",
                InitialDirectory = DefaultDir(),
                Title = "Load histogram datasets",
            };
            if (dlg.ShowDialog() != true) return;
            try
            {
                var loaded = HistogramStore.Load(dlg.FileName);
                if (loaded == null)
                {
                    MessageBox.Show("This file is not a histogram dataset "
                        + "preset.", Title);
                    return;
                }
                string mix = MixMessage(loaded);
                if (mix != null)
                {
                    MessageBox.Show(loaded.Any(d => d.IsCategorical) &&
                                    loaded.Any(d => !d.IsCategorical)
                        ? "The preset mixes categorical and numeric datasets, "
                          + "which cannot share one chart. Nothing was loaded."
                        : mix, Title);
                    return;
                }
                if (_datasets.Count == 0 && loaded.Any(d => d.IsCategorical)
                    && txtXLabel.Text == "Habitat Suitability")
                    txtXLabel.Text = "Class";
                _datasets.AddRange(loaded);
                _palette += loaded.Count;
                RefreshDatasetList(_datasets.Count - 1);
                Rebuild();
                txtStatus.Text = "Loaded " + loaded.Count + " dataset(s) from "
                    + Path.GetFileName(dlg.FileName);
                WarnCategoryMismatch();
            }
            catch (Exception ex)
            { MessageBox.Show("Could not load: " + ex.Message, Title); }
        }

        // =================================================================
        // exports
        // =================================================================
        private async void OnExportTables(object sender, RoutedEventArgs e)
        {
            // our OWN dialog builds the paths from explicit parts -- the
            // browse dialog's FilePath repeatedly surprised us (lost
            // suffixes, phantom names), so it is out of the loop entirely
            var choice = ExportTablesPrompt.Show(this);
            if (choice == null) return;
            try
            {
                txtStatus.Text = "Exporting statistics tables...";
                ParseOptions();
                var gen = HistogramStore.GeneralTable(_datasets);
                var bins = HistogramStore.BinsTable(_datasets, _options);
                string p1, p2;
                if (choice.Csv)
                {
                    string UniqueCsv(string b)
                    {
                        string cand = Path.Combine(choice.Workspace,
                                                   b + ".csv");
                        for (int i = 1; i <= 999 && File.Exists(cand); i++)
                            cand = Path.Combine(choice.Workspace,
                                                b + "_" + i + ".csv");
                        return cand;
                    }
                    p1 = UniqueCsv(choice.BaseName + "_stats");
                    p2 = UniqueCsv(choice.BaseName + "_bins");
                    File.WriteAllText(p1, HistogramStore.ToCsv(gen));
                    File.WriteAllText(p2, HistogramStore.ToCsv(bins));
                }
                else
                {
                    p1 = await GpTables.WriteAsync(choice.Workspace,
                        choice.BaseName + "_stats", choice.Gdb, gen);
                    p2 = await GpTables.WriteAsync(choice.Workspace,
                        choice.BaseName + "_bins", choice.Gdb, bins);
                }
                // Jeff: the exported tables join the map automatically
                await QueuedTask.Run(() =>
                {
                    var map = MapView.Active?.Map;
                    if (map == null) return;
                    foreach (var p in new[] { p1, p2 })
                    {
                        try
                        {
                            StandaloneTableFactory.Instance
                                .CreateStandaloneTable(
                                    new StandaloneTableCreationParams(
                                        new Uri(p)), map);
                        }
                        catch { }
                    }
                });
                txtStatus.Text = "Exported and added to the map: " + p1
                    + "  and  " + p2;
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Export failed: " + ex.Message;
                MessageBox.Show(ex.Message, Title);
            }
        }

        private void OnSaveGraph(object sender, RoutedEventArgs e)
        {
            if (_model == null) return;
            var dlg = new Microsoft.Win32.SaveFileDialog
            {
                Title = "Save the histogram graphic",
                Filter = "PNG image (*.png)|*.png|SVG vector (*.svg)|*.svg",
                FileName = "Habitat_Histogram.png",
                InitialDirectory = ImageSaveFolder.Initial(),
                DefaultExt = ".png",
                AddExtension = true,
            };
            if (dlg.ShowDialog() != true) return;
            if (string.IsNullOrEmpty(Path.GetExtension(dlg.FileName)))
                dlg.FileName += ".png";   // typed without an extension
            ImageSaveFolder.Remember(dlg.FileName);
            try
            {
                if (Path.GetExtension(dlg.FileName)
                        .Equals(".svg", StringComparison.OrdinalIgnoreCase))
                {
                    File.WriteAllText(dlg.FileName, PolarPlotSvg.Render(_model));
                }
                else
                {
                    const double scale = 300.0 / 96.0;   // 300 dpi
                    var dg = PolarPlotWpf.Render(_model);
                    // pixel dimensions FIRST; the white background must
                    // cover the whole (ceiled) bitmap or the last row and
                    // column of pixels stay transparent
                    int pxW = (int)Math.Ceiling(
                        _model.HalfWidthIn * 2 * 96 * scale);
                    int pxH = (int)Math.Ceiling(
                        _model.HalfHeightIn * 2 * 96 * scale);
                    var dv = new DrawingVisual();
                    using (var dc = dv.RenderOpen())
                    {
                        dc.DrawRectangle(Brushes.White, null,
                            new Rect(0, 0, pxW, pxH));
                        dc.PushTransform(new ScaleTransform(scale, scale));
                        dc.DrawDrawing(dg);
                        dc.Pop();
                    }
                    var rtb = new RenderTargetBitmap(pxW, pxH, 96, 96,
                        PixelFormats.Pbgra32);
                    rtb.Render(dv);
                    var enc = new PngBitmapEncoder();
                    enc.Frames.Add(BitmapFrame.Create(rtb));
                    using (var fs = File.Create(dlg.FileName)) enc.Save(fs);
                }
                txtStatus.Text = "Graph saved: " + dlg.FileName;
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Graph save failed: " + ex.Message;
                MessageBox.Show(ex.Message, Title);
            }
        }

        private async void OnAddToLayout(object sender, RoutedEventArgs e)
        {
            if (_model == null) return;
            try
            {
                string where = await LayoutPlacement.AddChart(this, _model,
                    "Habitat Histogram", BuildTaggedStats(),
                    "Histogram Statistics");
                if (where != null) txtStatus.Text = "✓ " + where;
            }
            catch (Exception ex)
            { MessageBox.Show(ex.Message, Title); }
        }

        // =================================================================
        // statistics report
        // =================================================================
        private void RenderStats()
        {
            var doc = new FlowDocument
            {
                FontFamily = new FontFamily("Segoe UI"),
                FontSize = 12.5,
                PagePadding = new Thickness(6),
            };
            foreach (var d in _datasets)
            {
                var p = new Paragraph { Margin = new Thickness(0, 2, 0, 6) };
                p.Inlines.Add(new Bold(new Run(d.Name)));
                p.Inlines.Add(new LineBreak());
                foreach (var line in StatsLines(d))
                {
                    p.Inlines.Add(new Run("   " + line));
                    p.Inlines.Add(new LineBreak());
                }
                doc.Blocks.Add(p);
            }
            statsViewer.Document = doc;
        }

        private IEnumerable<string> StatsLines(HistDataset d)
        {
            var s = d.Stats;
            string N(double v) => v.ToString("#,##0.####",
                CultureInfo.InvariantCulture);
            yield return "Source:  " + d.SourceDesc;
            yield return "Weight basis:  " + HistogramStore.BasisWord(d);
            string total = "Total weight:  " + N(s.SumWeights);
            double ha = HistogramMath.ToUnit(s.SumWeights, d, YUnit.Hectares, 1);
            if (!double.IsNaN(ha))
                total += "   (" + N(ha) + " ha = " + N(ha / 100.0) + " km2"
                    + (d.ApproxCellArea ? ", approx. cell area" : "") + ")";
            yield return total;
            if (d.IsCategorical)
            {
                // per-class totals and shares; mean/SD of class CODES would
                // be meaningless and are not shown
                var totals = d.CategoryTotals();
                var names = d.CategoryNames();
                yield return "Classes: " + names.Count;
                foreach (var name in names)
                {
                    totals.TryGetValue(name, out double w);
                    double pct = s.SumWeights > 0 ? w / s.SumWeights * 100 : 0;
                    double haC = HistogramMath.ToUnit(w, d, YUnit.Hectares, 1);
                    yield return "   " + name + ":  " + N(w)
                        + (double.IsNaN(haC) ? "" : " (" + N(haC) + " ha)")
                        + "   " + pct.ToString("0.0#",
                            CultureInfo.InvariantCulture) + "%";
                }
                var missing = HistogramMath.MissingCategories(_datasets)
                    .FirstOrDefault(x => ReferenceEquals(x.d, d)).missing;
                if (missing != null && missing.Count > 0)
                    yield return "Classes present in other datasets but not "
                        + "this one: " + string.Join(", ", missing);
                yield break;
            }
            yield return "Mean: " + N(s.Mean) + "   Median: " + N(s.Median)
                + "   SD: " + N(s.SD) + "   SE: " + N(s.SE);
            yield return "Min: " + N(s.Min) + "   Max: " + N(s.Max)
                + "   Range: " + N(s.Range) + "   Sum(value×w): " + N(s.Sum);
        }

        private string BuildPlainStats()
        {
            var sb = new StringBuilder();
            sb.AppendLine("Histogram Statistics Report");
            sb.AppendLine(new string('-', 45));
            foreach (var d in _datasets)
            {
                sb.AppendLine(d.Name);
                foreach (var line in StatsLines(d))
                    sb.AppendLine("   " + line);
                sb.AppendLine();
            }
            return sb.ToString();
        }

        private string BuildTaggedStats()
        {
            string esc(string s) => (s ?? "").Replace("&", "&amp;")
                .Replace("<", "&lt;").Replace(">", "&gt;");
            var sb = new StringBuilder();
            sb.AppendLine("<FNT size=\"14\"><BOL>Histogram Statistics</BOL></FNT>");
            foreach (var d in _datasets)
            {
                sb.AppendLine();
                sb.Append("<BOL>").Append(esc(d.Name)).AppendLine("</BOL>");
                foreach (var line in StatsLines(d))
                    sb.AppendLine(esc("  " + line));
            }
            return sb.ToString();
        }

        private void OnCopyStats(object sender, RoutedEventArgs e)
        {
            try
            {
                Clipboard.SetText(BuildPlainStats());
                txtStatus.Text = "✓ Statistics copied to the clipboard.";
            }
            catch (Exception ex)
            { MessageBox.Show("Could not copy: " + ex.Message, Title); }
        }

        private void OnClose(object sender, RoutedEventArgs e) => Close();

        private void OnHelp(object sender, RoutedEventArgs e)
            => WebHelp.Open("corridor-designer-tools/histograms-and-statistics.html");

        // =================================================================
        // drag and drop (whole window; standing rules: answer an ALLOWED
        // effect, set Handled, expect the TOC's internal payload)
        // =================================================================
        void ArcGIS.Desktop.Framework.DragDrop.IDropTarget.OnDragOver(
            ArcGIS.Desktop.Framework.DragDrop.DropInfo dropInfo)
        {
            var allowed = dropInfo.DragEventArgs?.AllowedEffects
                          ?? DragDropEffects.Copy;
            dropInfo.Effects =
                (allowed & DragDropEffects.Copy) != 0 ? DragDropEffects.Copy :
                (allowed & DragDropEffects.Move) != 0 ? DragDropEffects.Move :
                (allowed & DragDropEffects.Link) != 0 ? DragDropEffects.Link :
                allowed;
            dropInfo.Handled = true;
        }

        void ArcGIS.Desktop.Framework.DragDrop.IDropTarget.OnDrop(
            ArcGIS.Desktop.Framework.DragDrop.DropInfo dropInfo)
        {
            dropInfo.Handled = true;
            var lyr = ExtractLayer(dropInfo.Data);
            if (lyr == null)
            {
                txtStatus.Text = "Drop a raster or feature layer from the "
                    + "Contents pane (or use Add Data...).";
                return;
            }
            _ = AddDataFlow(lyr);
        }

        /// <summary>The dropped layer, however packaged: a Layer directly, a
        /// collection, or the TOC's internal TOCDragPayload whose public
        /// DraggedContent / MapMemberURIs are reached by reflection.</summary>
        private static Layer ExtractLayer(object data, int depth = 0)
        {
            if (data == null || depth > 4) return null;
            if (data is Layer l) return l;
            if (data is string s)
            {
                var map = MapView.Active?.Map;
                if (map == null) return null;
                return map.GetLayersAsFlattenedList()
                        .FirstOrDefault(x => x.URI == s)
                    ?? map.GetLayersAsFlattenedList()
                        .FirstOrDefault(x => string.Equals(x.Name, s.Trim(),
                            StringComparison.OrdinalIgnoreCase));
            }
            if (data is System.Collections.IEnumerable seq && !(data is string))
                foreach (var item in seq)
                {
                    var got = ExtractLayer(item, depth + 1);
                    if (got != null) return got;
                }
            foreach (var propName in new[] { "DraggedContent", "MapMemberURIs" })
            {
                try
                {
                    var val = data.GetType().GetProperty(propName)
                        ?.GetValue(data);
                    if (val != null && !ReferenceEquals(val, data))
                    {
                        var got = ExtractLayer(val, depth + 1);
                        if (got != null) return got;
                    }
                }
                catch { }
            }
            return null;
        }
    }

    /// <summary>The Export Tables destination dialog: workspace (browsed
    /// geodatabase or folder) + base name, from which the tool constructs
    /// "base_stats" and "base_bins" itself -- no dialog-path parsing.</summary>
    internal sealed class ExportTablesChoice
    {
        public string Workspace;
        public string BaseName;
        public bool Gdb;         // geodatabase tables
        public bool Csv;         // folder + CSV (else folder = dBASE)
    }

    internal static class ExportTablesPrompt
    {
        public static ExportTablesChoice Show(Window owner,
            string defaultBase = "Histogram", string blurb = null)
        {
            ExportTablesChoice result = null;
            string ws = null;
            bool gdb = true;
            try
            {
                ws = ArcGIS.Desktop.Core.Project.Current
                    ?.DefaultGeodatabasePath;
            }
            catch { }

            var win = new Window
            {
                Title = "Export Statistics Tables",
                Owner = owner,
                SizeToContent = SizeToContent.Height,
                Width = 470,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                ResizeMode = ResizeMode.NoResize,
                ShowInTaskbar = false,
            };
            JennessIcon.Apply(win);
            var panel = new StackPanel { Margin = new Thickness(12) };
            panel.Children.Add(new TextBlock
            {
                Text = blurb ?? "Two tables are written: <base name>_stats "
                       + "(one row per dataset) and <base name>_bins (one "
                       + "row per dataset and bin).",
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 0, 0, 8),
            });
            panel.Children.Add(new TextBlock { Text = "Destination:" });
            var txtWs = new TextBox
            {
                Text = ws ?? "",
                IsReadOnly = true,
                Margin = new Thickness(0, 2, 0, 4),
            };
            panel.Children.Add(txtWs);
            var fmtPanel = new StackPanel
            { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 2, 0, 8) };
            var rbDbf = new RadioButton
            { Content = "dBASE tables", GroupName = "F", IsChecked = true,
              Margin = new Thickness(0, 0, 12, 0) };
            var rbCsv = new RadioButton
            { Content = "CSV files", GroupName = "F" };
            var lblFolderFmt = new TextBlock
            { Text = "Folder format:  ", Opacity = 0.8 };
            fmtPanel.Children.Add(lblFolderFmt);
            fmtPanel.Children.Add(rbDbf);
            fmtPanel.Children.Add(rbCsv);
            fmtPanel.Visibility = Visibility.Collapsed;

            var browse = new StackPanel
            { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 0, 4) };
            var btnGdb = new Button
            { Content = "Geodatabase...", Padding = new Thickness(8, 2, 8, 2) };
            btnGdb.Click += (_, __) =>
            {
                var d = new OpenItemDialog
                {
                    Title = "Choose the destination geodatabase",
                    MultiSelect = false,
                    Filter = ItemFilters.Geodatabases,
                };
                if (d.ShowDialog() == true && d.Items?.FirstOrDefault() != null)
                {
                    txtWs.Text = d.Items.First().Path;
                    gdb = true;
                    fmtPanel.Visibility = Visibility.Collapsed;
                }
            };
            var btnFolder = new Button
            {
                Content = "Folder...",
                Padding = new Thickness(8, 2, 8, 2),
                Margin = new Thickness(8, 0, 0, 0),
            };
            btnFolder.Click += (_, __) =>
            {
                var d = new OpenItemDialog
                {
                    Title = "Choose the destination folder",
                    MultiSelect = false,
                    Filter = ItemFilters.Folders,
                };
                if (d.ShowDialog() == true && d.Items?.FirstOrDefault() != null)
                {
                    txtWs.Text = d.Items.First().Path;
                    gdb = false;
                    fmtPanel.Visibility = Visibility.Visible;
                }
            };
            browse.Children.Add(btnGdb);
            browse.Children.Add(btnFolder);
            panel.Children.Add(browse);
            panel.Children.Add(fmtPanel);

            panel.Children.Add(new TextBlock { Text = "Base name:" });
            var txtBase = new TextBox
            { Text = defaultBase, Margin = new Thickness(0, 2, 0, 8) };
            panel.Children.Add(txtBase);

            var buttons = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                HorizontalAlignment = HorizontalAlignment.Right,
            };
            var ok = new Button
            { Content = "OK", Width = 80, IsDefault = true };
            ok.Click += (_, __) =>
            {
                string w = (txtWs.Text ?? "").Trim();
                string b = (txtBase.Text ?? "").Trim();
                if (w.Length == 0 || b.Length == 0)
                {
                    MessageBox.Show(
                        "Choose a destination and enter a base name.",
                        "Export Statistics Tables");
                    return;
                }
                result = new ExportTablesChoice
                {
                    Workspace = w.Replace('/', '\\').TrimEnd('\\'),
                    BaseName = b,
                    Gdb = gdb,
                    Csv = !gdb && rbCsv.IsChecked == true,
                };
                win.Close();
            };
            var cancel = new Button
            {
                Content = "Cancel",
                Width = 80,
                Margin = new Thickness(8, 0, 0, 0),
                IsCancel = true,
            };
            cancel.Click += (_, __) => win.Close();
            buttons.Children.Add(ok);
            buttons.Children.Add(cancel);
            panel.Children.Add(buttons);
            win.Content = panel;
            txtBase.Focus();
            txtBase.SelectAll();
            win.ShowDialog();
            return result;
        }
    }

    /// <summary>The Jenness Enterprises logo as a window icon: embedded
    /// WPF resource first (immune to Assembly.Location quirks in the
    /// add-in load context -- the file-path attempt silently failed in
    /// Pro), loose Images file as fallback.</summary>
    internal static class JennessIcon
    {
        private static ImageSource _icon;
        private static bool _tried;

        public static void Apply(Window w)
        {
            if (!_tried)
            {
                _tried = true;
                try
                {
                    var bi = new BitmapImage(new Uri(
                        "pack://application:,,,/WildlifeTools;component/"
                        + "Corridor/JennessLogoRes.png"));
                    bi.Freeze();
                    _icon = bi;
                }
                catch
                {
                    try
                    {
                        string dir = Path.GetDirectoryName(
                            typeof(JennessIcon).Assembly.Location);
                        string png = Path.Combine(dir ?? "", "Images",
                                                  "JennessLogo32.png");
                        if (File.Exists(png))
                        {
                            var bi = new BitmapImage();
                            bi.BeginInit();
                            bi.UriSource = new Uri(png);
                            bi.CacheOption = BitmapCacheOption.OnLoad;
                            bi.EndInit();
                            bi.Freeze();
                            _icon = bi;
                        }
                    }
                    catch { }
                }
            }
            if (_icon != null) w.Icon = _icon;
        }
    }

    /// <summary>Font settings dialog: any number of independent styles,
    /// each with family, size, bold and italic. Returns true when applied
    /// (the FontSpec objects are edited in place on OK).</summary>
    internal static class FontsPrompt
    {
        public static bool Show(Window owner, HistogramOptions o) =>
            Show(owner,
                 ("Title:", o.TitleFont),
                 ("Axis labels:", o.LabelFont),
                 ("Legend text:", o.LegendFont),
                 ("Axis numbers:", o.TickFont));

        public static bool Show(Window owner,
                                params (string label, FontSpec spec)[] items)
        {
            bool applied = false;
            var win = new Window
            {
                Title = "Chart Fonts",
                Owner = owner,
                SizeToContent = SizeToContent.WidthAndHeight,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                ResizeMode = ResizeMode.NoResize,
                ShowInTaskbar = false,
            };
            JennessIcon.Apply(win);
            var grid = new Grid { Margin = new Thickness(12) };
            foreach (double wcol in new[] { 0, 200, 60, 0, 0 })
                grid.ColumnDefinitions.Add(new ColumnDefinition
                {
                    Width = wcol > 0 ? new GridLength(wcol)
                                     : GridLength.Auto,
                });
            for (int i = 0; i < items.Length + 2; i++)
                grid.RowDefinitions.Add(new RowDefinition
                { Height = GridLength.Auto });

            void Put(FrameworkElement el, int row, int colIdx)
            {
                el.Margin = new Thickness(0, 0, 8, 6);
                Grid.SetRow(el, row);
                Grid.SetColumn(el, colIdx);
                grid.Children.Add(el);
            }
            void Head(string t, int colIdx) => Put(new TextBlock
            { Text = t, FontWeight = FontWeights.SemiBold }, 0, colIdx);
            Head("", 0);
            Head("Font", 1);
            Head("Size (pt)", 2);
            Head("Bold", 3);
            Head("Italic", 4);

            var families = System.Windows.Media.Fonts.SystemFontFamilies
                .Select(f => f.Source).OrderBy(x => x).ToList();
            var rows = new List<(FontSpec spec, ComboBox fam, TextBox size,
                                 CheckBox bold, CheckBox ital)>();
            for (int i = 0; i < items.Length; i++)
            {
                int row = i + 1;
                Put(new TextBlock
                {
                    Text = items[i].label,
                    VerticalAlignment = VerticalAlignment.Center,
                }, row, 0);
                var fam = new ComboBox { ItemsSource = families };
                fam.SelectedItem = families.Contains(items[i].spec.Family)
                    ? items[i].spec.Family : "Segoe UI";
                Put(fam, row, 1);
                var size = new TextBox
                {
                    Text = items[i].spec.SizePt.ToString("0.#",
                        CultureInfo.InvariantCulture),
                };
                Put(size, row, 2);
                var bold = new CheckBox
                {
                    IsChecked = items[i].spec.Bold,
                    VerticalAlignment = VerticalAlignment.Center,
                    HorizontalAlignment = HorizontalAlignment.Center,
                };
                Put(bold, row, 3);
                var ital = new CheckBox
                {
                    IsChecked = items[i].spec.Italic,
                    VerticalAlignment = VerticalAlignment.Center,
                    HorizontalAlignment = HorizontalAlignment.Center,
                };
                Put(ital, row, 4);
                rows.Add((items[i].spec, fam, size, bold, ital));
            }

            var panel = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                HorizontalAlignment = HorizontalAlignment.Right,
                Margin = new Thickness(0, 6, 0, 0),
            };
            var ok = new Button { Content = "OK", Width = 72, IsDefault = true };
            ok.Click += (_, __) =>
            {
                foreach (var (spec, fam, size, bold, ital) in rows)
                {
                    spec.Family = fam.SelectedItem as string ?? spec.Family;
                    if (double.TryParse(size.Text, NumberStyles.Float,
                            CultureInfo.InvariantCulture, out double x)
                        && x >= 5 && x <= 48)
                        spec.SizePt = x;
                    spec.Bold = bold.IsChecked == true;
                    spec.Italic = ital.IsChecked == true;
                }
                applied = true;
                win.Close();
            };
            var cancel = new Button
            {
                Content = "Cancel",
                Width = 72,
                Margin = new Thickness(8, 0, 0, 0),
                IsCancel = true,
            };
            cancel.Click += (_, __) => win.Close();
            panel.Children.Add(ok);
            panel.Children.Add(cancel);
            Grid.SetRow(panel, items.Length + 1);
            Grid.SetColumnSpan(panel, 5);
            grid.Children.Add(panel);
            win.Content = grid;
            win.ShowDialog();
            return applied;
        }
    }

    /// <summary>Modal color swatch picker: the Okabe-Ito colorblind-safe
    /// row on top, then a hue x lightness grid. Returns the chosen color
    /// or null.</summary>
    internal static class ColorSwatchPrompt
    {
        public static System.Windows.Media.Color? Show(Window owner,
                                                       string title)
        {
            System.Windows.Media.Color? result = null;
            var win = new Window
            {
                Title = title,
                Owner = owner,
                SizeToContent = SizeToContent.WidthAndHeight,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                ResizeMode = ResizeMode.NoResize,
                ShowInTaskbar = false,
            };
            JennessIcon.Apply(win);
            var panel = new StackPanel { Margin = new Thickness(12) };
            panel.Children.Add(new TextBlock
            {
                Text = "Colorblind-safe palette:",
                Margin = new Thickness(0, 0, 0, 4),
            });
            var rowKO = new StackPanel { Orientation = Orientation.Horizontal };
            foreach (var (r, g, b) in HistogramMath.Palette)
                rowKO.Children.Add(Swatch(r, g, b));
            panel.Children.Add(rowKO);
            panel.Children.Add(new TextBlock
            {
                Text = "More colors:",
                Margin = new Thickness(0, 8, 0, 4),
            });
            foreach (double light in new[] { 0.30, 0.45, 0.60, 0.75 })
            {
                var row = new StackPanel { Orientation = Orientation.Horizontal };
                for (int hstep = 0; hstep < 12; hstep++)
                {
                    var (r, g, b) = Hsl(hstep * 30.0, 0.75, light);
                    row.Children.Add(Swatch(r, g, b));
                }
                panel.Children.Add(row);
            }
            var grays = new StackPanel
            { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 4, 0, 0) };
            for (int i = 0; i < 12; i++)
            {
                byte v = (byte)(20 + i * 20);
                grays.Children.Add(Swatch(v, v, v));
            }
            panel.Children.Add(grays);
            var cancel = new Button
            {
                Content = "Cancel",
                Width = 80,
                Margin = new Thickness(0, 10, 0, 0),
                HorizontalAlignment = HorizontalAlignment.Right,
                IsCancel = true,
            };
            cancel.Click += (_, __) => win.Close();
            panel.Children.Add(cancel);
            win.Content = panel;
            win.ShowDialog();
            return result;

            Button Swatch(byte r, byte g, byte b)
            {
                var btn = new Button
                {
                    Width = 24,
                    Height = 24,
                    Margin = new Thickness(2),
                    Background = new SolidColorBrush(
                        System.Windows.Media.Color.FromRgb(r, g, b)),
                    BorderBrush = Brushes.Gray,
                    BorderThickness = new Thickness(1),
                    ToolTip = string.Format("R {0}, G {1}, B {2}", r, g, b),
                };
                btn.Click += (_, __) =>
                {
                    result = System.Windows.Media.Color.FromRgb(r, g, b);
                    win.Close();
                };
                return btn;
            }
        }

        private static (byte, byte, byte) Hsl(double h, double s, double l)
        {
            double c = (1 - Math.Abs(2 * l - 1)) * s;
            double hp = h / 60.0;
            double x = c * (1 - Math.Abs(hp % 2 - 1));
            double r1 = 0, g1 = 0, b1 = 0;
            if (hp < 1) { r1 = c; g1 = x; }
            else if (hp < 2) { r1 = x; g1 = c; }
            else if (hp < 3) { g1 = c; b1 = x; }
            else if (hp < 4) { g1 = x; b1 = c; }
            else if (hp < 5) { r1 = x; b1 = c; }
            else { r1 = c; b1 = x; }
            double m = l - c / 2;
            return ((byte)Math.Round((r1 + m) * 255),
                    (byte)Math.Round((g1 + m) * 255),
                    (byte)Math.Round((b1 + m) * 255));
        }
    }

    /// <summary>Minimal modal text prompt (rename dialog).</summary>
    internal static class TextPrompt
    {
        public static string Show(Window owner, string title, string prompt,
                                  string initial)
        {
            var win = new Window
            {
                Title = title,
                Owner = owner,
                Width = 380,
                Height = 150,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                ResizeMode = ResizeMode.NoResize,
                ShowInTaskbar = false,
            };
            JennessIcon.Apply(win);
            var grid = new Grid { Margin = new Thickness(12) };
            grid.RowDefinitions.Add(new RowDefinition
            { Height = GridLength.Auto });
            grid.RowDefinitions.Add(new RowDefinition
            { Height = GridLength.Auto });
            grid.RowDefinitions.Add(new RowDefinition
            { Height = new GridLength(1, GridUnitType.Star) });
            var lbl = new TextBlock { Text = prompt };
            Grid.SetRow(lbl, 0);
            var box = new TextBox
            { Text = initial ?? "", Margin = new Thickness(0, 6, 0, 0) };
            Grid.SetRow(box, 1);
            string result = null;
            var panel = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                HorizontalAlignment = HorizontalAlignment.Right,
                VerticalAlignment = VerticalAlignment.Bottom,
            };
            var ok = new Button
            { Content = "OK", Width = 72, IsDefault = true };
            ok.Click += (_, __) => { result = box.Text; win.Close(); };
            var cancel = new Button
            {
                Content = "Cancel",
                Width = 72,
                Margin = new Thickness(8, 0, 0, 0),
                IsCancel = true,
            };
            cancel.Click += (_, __) => win.Close();
            panel.Children.Add(ok);
            panel.Children.Add(cancel);
            Grid.SetRow(panel, 2);
            grid.Children.Add(lbl);
            grid.Children.Add(box);
            grid.Children.Add(panel);
            win.Content = grid;
            box.Focus();
            box.SelectAll();
            win.ShowDialog();
            return result;
        }
    }
}
