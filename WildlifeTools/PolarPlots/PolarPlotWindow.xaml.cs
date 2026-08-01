using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using ArcGIS.Core.CIM;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using ArcGIS.Desktop.Mapping.Controls;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.PolarPlots
{
    /// <summary>
    /// Interactive Polar Plots window: pick a dataset and options, watch the
    /// plot update live, then save it as SVG, add it to the map/layout, or view
    /// the circular statistics. Modernized recreation of the ArcMap Polar Plots
    /// extension.
    ///
    /// COLOR PICKERS: Esri's ColorPickerControl only shows its swatch once its
    /// template is applied, which for controls inside a collapsed Expander
    /// happens the first time the section is opened. The window therefore keeps
    /// the CURRENT colors in plain fields (_colBinFill etc.) as the source of
    /// truth: each picker re-applies its color in its Loaded event (fires on
    /// first expand), and user picks write back through StyleColor. Rendering
    /// always reads the fields, never the controls.
    /// </summary>
    public partial class PolarPlotWindow : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private bool _loading = true;
        private List<PolarPlotData.Source> _sources = new List<PolarPlotData.Source>();
        private readonly List<double> _az = new List<double>();
        private readonly List<double> _wt = new List<double>();
        private bool _weighted;
        private PolarPlotData.Result _lastResult;
        private bool _busy;

        // Source-of-truth colors (see class remarks).
        private RgbaColor _colBinFill = new RgbaColor(150, 170, 210, 255);
        private RgbaColor _colBinOutline = new RgbaColor(40, 70, 120, 255);
        private RgbaColor _colLow = new RgbaColor(230, 240, 255, 255);
        private RgbaColor _colHigh = new RgbaColor(30, 70, 140, 255);
        private RgbaColor _colLabel = new RgbaColor(60, 60, 60, 255);
        private RgbaColor _colTitle = new RgbaColor(0, 0, 0, 255);
        private RgbaColor _colSec = new RgbaColor(80, 80, 80, 255);

        private static readonly string[] FontStyleNames =
            { "Regular", "Bold", "Italic", "Bold Italic" };

        public PolarPlotWindow()
        {
            InitializeComponent();
            cboPlotType.ItemsSource = new[]
            {
                "Peaks and Valleys", "Circular Bins (Rose)", "Shade by Density",
            };
            cboPlotType.SelectedIndex = 1;

            var fonts = System.Windows.Media.Fonts.SystemFontFamilies
                .Select(f => f.Source).OrderBy(n => n).ToList();
            string dflt = fonts.Contains("Segoe UI") ? "Segoe UI" : fonts.FirstOrDefault();
            cboLabelFont.ItemsSource = fonts;
            cboLabelFont.SelectedItem = dflt;
            cboTitleFont.ItemsSource = fonts.ToList();
            cboTitleFont.SelectedItem = dflt;
            cboSecFont.ItemsSource = fonts.ToList();
            cboSecFont.SelectedItem = dflt;
            cboLabelStyle.ItemsSource = FontStyleNames;
            cboLabelStyle.SelectedIndex = 0;                  // Regular
            cboTitleStyle.ItemsSource = FontStyleNames;
            cboTitleStyle.SelectedIndex = 1;                  // Bold
            cboSecStyle.ItemsSource = FontStyleNames;
            cboSecStyle.SelectedIndex = 2;                    // Italic

            Loaded += async (_, __) => await LoadSourcesAsync();
        }

        private static void StyleToFlags(string style, out bool bold, out bool italic)
        {
            style = style ?? "Regular";
            bold = style.IndexOf("Bold", StringComparison.OrdinalIgnoreCase) >= 0;
            italic = style.IndexOf("Italic", StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static string FlagsToStyle(bool bold, bool italic) =>
            bold && italic ? "Bold Italic" : bold ? "Bold" : italic ? "Italic" : "Regular";

        private async System.Threading.Tasks.Task LoadSourcesAsync()
        {
            try
            {
                var map = MapView.Active?.Map;
                _sources = await QueuedTask.Run(() => PolarPlotData.ListSources(map));
                cboSource.ItemsSource = _sources;
                _loading = false;
                if (_sources.Count > 0) cboSource.SelectedIndex = 0;  // triggers read
                else { RenderPreview(); SetStatus("No feature layers or tables in the map."); }
            }
            catch (Exception ex) { _loading = false; SetStatus("Error: " + ex.Message); }
        }

        // ---- color plumbing -------------------------------------------------
        /// <summary>Re-apply the stored color when a picker's template loads
        /// (first expand of its section) so the swatch is never blank.</summary>
        private void PickerLoaded(object sender, RoutedEventArgs e)
        {
            if (sender is ColorPickerControl cp)
                ApplyColor(cp, ColorFieldFor(cp));
        }

        private RgbaColor ColorFieldFor(ColorPickerControl cp)
        {
            if (ReferenceEquals(cp, cpBinFill)) return _colBinFill;
            if (ReferenceEquals(cp, cpBinOutline)) return _colBinOutline;
            if (ReferenceEquals(cp, cpLow)) return _colLow;
            if (ReferenceEquals(cp, cpHigh)) return _colHigh;
            if (ReferenceEquals(cp, cpLabelColor)) return _colLabel;
            if (ReferenceEquals(cp, cpSecColor)) return _colSec;
            return _colTitle;
        }

        private void StoreColor(ColorPickerControl cp, RgbaColor c)
        {
            if (ReferenceEquals(cp, cpBinFill)) _colBinFill = c;
            else if (ReferenceEquals(cp, cpBinOutline)) _colBinOutline = c;
            else if (ReferenceEquals(cp, cpLow)) _colLow = c;
            else if (ReferenceEquals(cp, cpHigh)) _colHigh = c;
            else if (ReferenceEquals(cp, cpLabelColor)) _colLabel = c;
            else if (ReferenceEquals(cp, cpSecColor)) _colSec = c;
            else if (ReferenceEquals(cp, cpTitleColor)) _colTitle = c;
        }

        private static void ApplyColor(ColorPickerControl cp, RgbaColor c)
        {
            if (c.IsTransparent) { cp.SelectedColor = null; return; }
            cp.SelectedColor = new CIMRGBColor
            { R = c.R, G = c.G, B = c.B, Alpha = (float)(c.A / 255.0 * 100.0) };
        }

        private static bool TryCimToRgba(CIMColor cim, out RgbaColor c)
        {
            if (cim is CIMRGBColor rgb)
            {
                c = new RgbaColor((byte)Clamp(rgb.R), (byte)Clamp(rgb.G),
                    (byte)Clamp(rgb.B), (byte)Clamp(rgb.Alpha / 100.0 * 255.0));
                return true;
            }
            c = default;
            return false;
        }

        // ---- event handlers (one per event-arg type) ------------------------
        private async void SourceSelChanged(object s, SelectionChangedEventArgs e)
        {
            if (_loading) return;
            await PopulateFieldsAsync();
            await RereadAsync();
        }
        private async void RereadSel(object s, SelectionChangedEventArgs e)
        { if (!_loading) await RereadAsync(); }
        private async void RereadRouted(object s, RoutedEventArgs e)
        { if (!_loading) { SyncEnabled(); await RereadAsync(); } }
        private void StyleSel(object s, SelectionChangedEventArgs e)
        { if (!_loading) RenderPreview(); }
        private void StyleText(object s, TextChangedEventArgs e)
        { if (!_loading) RenderPreview(); }
        private void StyleRouted(object s, RoutedEventArgs e)
        { if (!_loading) RenderPreview(); }
        private void StyleColor(object s, ColorChangedEventArgs e)
        {
            if (s is ColorPickerControl cp)
            {
                if (e?.Color == null)
                    StoreColor(cp, new RgbaColor(0, 0, 0, 0));        // "No color"
                else if (TryCimToRgba(e.Color, out var c))
                    StoreColor(cp, c);
            }
            if (!_loading) RenderPreview();
        }

        private async System.Threading.Tasks.Task PopulateFieldsAsync()
        {
            var src = cboSource.SelectedItem as PolarPlotData.Source;
            if (src == null) return;
            var fields = await QueuedTask.Run(() => PolarPlotData.NumericFields(src));
            _loading = true;
            cboAzField.ItemsSource = fields;
            cboWField.ItemsSource = fields.ToList();
            if (fields.Count > 0) { cboAzField.SelectedIndex = 0; cboWField.SelectedIndex = 0; }
            rbAzPolyline.IsEnabled = src.IsPolyline;
            rbWLength.IsEnabled = src.IsPolyline;
            if (!src.IsPolyline && rbAzPolyline.IsChecked == true) rbAzField.IsChecked = true;
            if (!src.IsPolyline && rbWLength.IsChecked == true) rbWNone.IsChecked = true;
            _loading = false;
            SyncEnabled();
        }

        private void SyncEnabled()
        {
            cboAzField.IsEnabled = rbAzField.IsChecked == true;
            cboWField.IsEnabled = rbWField.IsChecked == true;
            chkExcludeNeg1.IsEnabled = chkExcludeNeg.IsChecked == true;
        }

        private async System.Threading.Tasks.Task RereadAsync()
        {
            if (_busy) return;
            var src = cboSource.SelectedItem as PolarPlotData.Source;
            if (src == null) { RenderPreview(); return; }
            _busy = true;
            SetStatus("Reading data...");
            try
            {
                var s = BuildSettings();
                var res = await QueuedTask.Run(() => PolarPlotData.Read(src, s));
                _lastResult = res;
                _az.Clear(); _az.AddRange(res.Azimuths);
                _wt.Clear(); _wt.AddRange(res.Weights);
                _weighted = res.Weighted;
                RenderPreview();
            }
            catch (Exception ex) { SetStatus("Error reading data: " + ex.Message); }
            finally { _busy = false; }
        }

        // ---- settings from controls ----------------------------------------
        private PolarPlotSettings BuildSettings()
        {
            var s = new PolarPlotSettings();
            switch (cboPlotType.SelectedIndex)
            {
                case 0: s.PlotType = PolarPlotType.PeaksAndValleys; break;
                case 2: s.PlotType = PolarPlotType.ShadeByDensity; break;
                default: s.PlotType = PolarPlotType.CircularBins; break;
            }
            s.BinCount = Math.Max(1, ParseInt(txtBins.Text, 72));
            s.PlotDiameterIn = Math.Max(0.5, ParseDouble(txtDiameter.Text, 4.0));
            s.BinFill = _colBinFill;
            s.BinOutline = _colBinOutline;
            s.LowDensity = _colLow;
            s.HighDensity = _colHigh;
            s.LabelColor = _colLabel;
            s.TitleColor = _colTitle;
            s.BinOutlineWidthPt = Math.Max(0.0, ParseDouble(txtOutlineWidth.Text, 0.5));

            s.ShowPrimaryRefLines = chkPrimaryRef.IsChecked == true;
            s.ShowSecondaryRefRadius = chkSecRays.IsChecked == true;
            s.SecondaryRefBearings = Math.Max(3, ParseInt(txtSecRays.Text, 12));
            s.ShowSecondaryRefCircles = chkSecCircles.IsChecked == true;
            s.SecondaryRefCircles = Math.Max(1, ParseInt(txtSecCircles.Text, 4));
            s.ShowAzimuthLabels = chkAzLabels.IsChecked == true;
            s.AzimuthLabelsRotated = chkAzRotated.IsChecked == true;
            s.ShowInnerLabels = chkInnerLabels.IsChecked == true;
            s.InnerLabelNorth = chkInnerN.IsChecked == true;
            s.InnerLabelEast = chkInnerE.IsChecked == true;
            s.InnerLabelSouth = chkInnerS.IsChecked == true;
            s.InnerLabelWest = chkInnerW.IsChecked == true;
            s.LabelFontFamily = cboLabelFont.SelectedItem as string ?? "Segoe UI";
            s.LabelFontSizePt = Math.Max(4.0, ParseDouble(txtLabelSize.Text, 9.0));
            StyleToFlags(cboLabelStyle.SelectedItem as string,
                         out bool lb, out bool li);
            s.LabelBold = lb; s.LabelItalic = li;

            s.ShowPrimaryTitle = chkPrimaryTitle.IsChecked == true;
            s.PrimaryTitle = txtPrimaryTitle.Text ?? "";
            s.TitleFontFamily = cboTitleFont.SelectedItem as string ?? "Segoe UI";
            s.TitleFontSizePt = Math.Max(6.0, ParseDouble(txtTitleSize.Text, 16.0));
            StyleToFlags(cboTitleStyle.SelectedItem as string,
                         out bool tb, out bool ti);
            s.TitleBold = tb; s.TitleItalic = ti;
            s.ShowSecondaryTitles = chkSecTitles.IsChecked == true;
            s.SecondaryTitleFontFamily = cboSecFont.SelectedItem as string ?? "Segoe UI";
            s.SecondaryTitleFontSizePt = Math.Max(4.0, ParseDouble(txtSecSize.Text, 9.0));
            StyleToFlags(cboSecStyle.SelectedItem as string,
                         out bool sb2, out bool si2);
            s.SecondaryTitleBold = sb2; s.SecondaryTitleItalic = si2;
            s.SecondaryTitleColor = _colSec;

            var src = cboSource.SelectedItem as PolarPlotData.Source;
            s.DatasetName = src?.Name ?? "";
            s.AzimuthOption = rbAzPolyline.IsChecked == true
                ? PolarAzimuthOption.ByPolyline : PolarAzimuthOption.ByAttribute;
            s.AzimuthField = cboAzField.SelectedItem as string ?? "";
            s.WeightOption = rbWLength.IsChecked == true ? PolarWeightOption.ByLength
                : rbWField.IsChecked == true ? PolarWeightOption.ByAttribute
                : PolarWeightOption.None;
            s.WeightField = cboWField.SelectedItem as string ?? "";
            s.UseSelectedOnly = chkSelected.IsChecked == true;
            s.AddReverse = chkReverse.IsChecked == true;
            s.ExcludeNegative = chkExcludeNeg.IsChecked == true;
            s.ExcludeMinusOneOnly = chkExcludeNeg1.IsChecked == true;
            return s;
        }

        private List<string> SecondaryTitleLines(PolarPlotSettings s, double[] bins)
        {
            var lines = new List<string>();
            if (!s.ShowSecondaryTitles) return lines;
            string azSrc = s.AzimuthOption == PolarAzimuthOption.ByPolyline
                ? "polyline direction" : "field [" + s.AzimuthField + "]";
            lines.Add("Source: " + s.DatasetName + " (" + azSrc + ")");
            lines.Add("Count analyzed: " + _az.Count.ToString("N0", CultureInfo.InvariantCulture));
            lines.Add(s.WeightOption == PolarWeightOption.None
                ? "Values not weighted"
                : "Total weight: " + _wt.Sum().ToString("N2", CultureInfo.InvariantCulture));
            double max = bins != null && bins.Length > 0 ? bins.Max() : 0;
            lines.Add("Maximum bin value: " + max.ToString("N2", CultureInfo.InvariantCulture));
            return lines;
        }

        // ---- render ---------------------------------------------------------
        private PolarPlotModel _model;      // last-built model (for save)
        private PolarPlotSettings _modelSettings;

        private void RenderPreview()
        {
            try
            {
                // Plot-type-dependent symbology: the density ramp only applies
                // to Shade by Density; the bin fill only to the other types.
                bool shade = cboPlotType.SelectedIndex == 2;
                cpLow.IsEnabled = shade;
                cpHigh.IsEnabled = shade;
                cpBinFill.IsEnabled = !shade;

                var s = BuildSettings();
                var st = new CircularStats(_az, _wt, _weighted);
                var bins = st.Bin(s.BinCount);
                var model = PolarPlotBuilder.Build(s, bins, SecondaryTitleLines(s, bins));
                _model = model; _modelSettings = s;
                double ppd = VisualTreeHelper.GetDpi(this).PixelsPerDip;
                var dg = PolarPlotWpf.Render(model, ppd, showRulers: true);
                imgPreview.Source = new DrawingImage(dg);
                SetStatus(_az.Count.ToString("N0", CultureInfo.InvariantCulture) +
                          " value(s) plotted" +
                          (_lastResult != null && _lastResult.ExcludedNegative > 0
                              ? "; " + _lastResult.ExcludedNegative + " negative excluded" : ""));
            }
            catch (Exception ex) { SetStatus("Render error: " + ex.Message); }
        }

        // ---- actions --------------------------------------------------------
        private void OnCalcStats(object sender, RoutedEventArgs e)
        {
            if (_az.Count == 0) { MessageBox.Show("No data to analyze.", "Polar Plots"); return; }
            var s = BuildSettings();
            var st = new CircularStats(_az, _wt, _weighted);
            var header = new List<string>
            {
                "Dataset = " + s.DatasetName,
                "Azimuth Source = " + (s.AzimuthOption == PolarAzimuthOption.ByPolyline
                    ? "Derived from polylines" : "Field [" + s.AzimuthField + "]"),
                "Weighting = " + (s.WeightOption == PolarWeightOption.None ? "None"
                    : s.WeightOption == PolarWeightOption.ByLength ? "By polyline length"
                    : "Field [" + s.WeightField + "]"),
            };
            string plain = st.BuildReport(header);
            var win = new PolarStatsWindow(st, header, plain) { Owner = this };
            win.Show();
        }

        private void OnSaveSvg(object sender, RoutedEventArgs e)
        {
            if (_model == null) { MessageBox.Show("Nothing to save yet.", "Polar Plots"); return; }
            var dlg = new Microsoft.Win32.SaveFileDialog
            {
                Filter = "SVG file (*.svg)|*.svg",
                FileName = MakeFileBase() + ".svg",
                Title = "Save polar plot as SVG",
            };
            if (dlg.ShowDialog() != true) return;
            try
            {
                File.WriteAllText(dlg.FileName, PolarPlotSvg.Render(_model));
                SetStatus("Saved " + Path.GetFileName(dlg.FileName));
            }
            catch (Exception ex) { MessageBox.Show("Could not save SVG: " + ex.Message, "Polar Plots"); }
        }

        private void OnAddGraphic(object sender, RoutedEventArgs e)
        {
            // Phase 2: place the plot as a CIM group element on the active map or
            // layout, and optionally the statistics as a text element.
            MessageBox.Show(
                "Adding the plot as a graphic on the map or layout is coming in " +
                "the next phase. For now, use Save as SVG (highest fidelity) or " +
                "Calculate statistics.", "Polar Plots");
        }

        private void OnClose(object sender, RoutedEventArgs e) => Close();

        // ---- style presets --------------------------------------------------
        private PolarPlotStyleDto CaptureStyle() => new PolarPlotStyleDto
        {
            PlotTypeIndex = cboPlotType.SelectedIndex,
            BinCount = ParseInt(txtBins.Text, 72),
            PlotDiameterIn = ParseDouble(txtDiameter.Text, 4.0),
            BinFill = PolarPlotStyleDto.FromColor(_colBinFill),
            BinOutline = PolarPlotStyleDto.FromColor(_colBinOutline),
            LowDensity = PolarPlotStyleDto.FromColor(_colLow),
            HighDensity = PolarPlotStyleDto.FromColor(_colHigh),
            BinOutlineWidthPt = ParseDouble(txtOutlineWidth.Text, 0.5),
            ShowPrimaryRefLines = chkPrimaryRef.IsChecked == true,
            ShowSecondaryRefRays = chkSecRays.IsChecked == true,
            SecondaryRefBearings = ParseInt(txtSecRays.Text, 12),
            ShowSecondaryRefCircles = chkSecCircles.IsChecked == true,
            SecondaryRefCircles = ParseInt(txtSecCircles.Text, 4),
            ShowAzimuthLabels = chkAzLabels.IsChecked == true,
            AzimuthLabelsRotated = chkAzRotated.IsChecked == true,
            ShowInnerLabels = chkInnerLabels.IsChecked == true,
            InnerN = chkInnerN.IsChecked == true,
            InnerE = chkInnerE.IsChecked == true,
            InnerS = chkInnerS.IsChecked == true,
            InnerW = chkInnerW.IsChecked == true,
            LabelFontFamily = cboLabelFont.SelectedItem as string ?? "Segoe UI",
            LabelFontSizePt = ParseDouble(txtLabelSize.Text, 9),
            LabelFontStyle = cboLabelStyle.SelectedItem as string ?? "Regular",
            LabelColor = PolarPlotStyleDto.FromColor(_colLabel),
            ShowPrimaryTitle = chkPrimaryTitle.IsChecked == true,
            TitleFontFamily = cboTitleFont.SelectedItem as string ?? "Segoe UI",
            TitleFontSizePt = ParseDouble(txtTitleSize.Text, 16),
            TitleFontStyle = cboTitleStyle.SelectedItem as string ?? "Bold",
            TitleColor = PolarPlotStyleDto.FromColor(_colTitle),
            ShowSecondaryTitles = chkSecTitles.IsChecked == true,
            SecondaryTitleFontFamily = cboSecFont.SelectedItem as string ?? "Segoe UI",
            SecondaryTitleFontSizePt = ParseDouble(txtSecSize.Text, 9),
            SecondaryTitleFontStyle = cboSecStyle.SelectedItem as string ?? "Italic",
            SecondaryTitleColor = PolarPlotStyleDto.FromColor(_colSec),
        };

        private void ApplyStyle(PolarPlotStyleDto d)
        {
            _loading = true;
            try
            {
                if (d.PlotTypeIndex >= 0 && d.PlotTypeIndex < cboPlotType.Items.Count)
                    cboPlotType.SelectedIndex = d.PlotTypeIndex;
                txtBins.Text = d.BinCount.ToString(CultureInfo.InvariantCulture);
                txtDiameter.Text = d.PlotDiameterIn.ToString(CultureInfo.InvariantCulture);
                _colBinFill = PolarPlotStyleDto.ToColor(d.BinFill, _colBinFill);
                _colBinOutline = PolarPlotStyleDto.ToColor(d.BinOutline, _colBinOutline);
                _colLow = PolarPlotStyleDto.ToColor(d.LowDensity, _colLow);
                _colHigh = PolarPlotStyleDto.ToColor(d.HighDensity, _colHigh);
                _colLabel = PolarPlotStyleDto.ToColor(d.LabelColor, _colLabel);
                _colTitle = PolarPlotStyleDto.ToColor(d.TitleColor, _colTitle);
                _colSec = PolarPlotStyleDto.ToColor(d.SecondaryTitleColor, _colSec);
                ApplyColor(cpBinFill, _colBinFill);
                ApplyColor(cpBinOutline, _colBinOutline);
                ApplyColor(cpLow, _colLow);
                ApplyColor(cpHigh, _colHigh);
                ApplyColor(cpLabelColor, _colLabel);
                ApplyColor(cpTitleColor, _colTitle);
                ApplyColor(cpSecColor, _colSec);
                txtOutlineWidth.Text = d.BinOutlineWidthPt.ToString(CultureInfo.InvariantCulture);
                chkPrimaryRef.IsChecked = d.ShowPrimaryRefLines;
                chkSecRays.IsChecked = d.ShowSecondaryRefRays;
                txtSecRays.Text = d.SecondaryRefBearings.ToString(CultureInfo.InvariantCulture);
                chkSecCircles.IsChecked = d.ShowSecondaryRefCircles;
                txtSecCircles.Text = d.SecondaryRefCircles.ToString(CultureInfo.InvariantCulture);
                chkAzLabels.IsChecked = d.ShowAzimuthLabels;
                chkAzRotated.IsChecked = d.AzimuthLabelsRotated;
                chkInnerLabels.IsChecked = d.ShowInnerLabels;
                chkInnerN.IsChecked = d.InnerN;
                chkInnerE.IsChecked = d.InnerE;
                chkInnerS.IsChecked = d.InnerS;
                chkInnerW.IsChecked = d.InnerW;
                SelectFont(cboLabelFont, d.LabelFontFamily);
                txtLabelSize.Text = d.LabelFontSizePt.ToString(CultureInfo.InvariantCulture);
                SelectStyle(cboLabelStyle, d.LabelFontStyle);
                chkPrimaryTitle.IsChecked = d.ShowPrimaryTitle;
                SelectFont(cboTitleFont, d.TitleFontFamily);
                txtTitleSize.Text = d.TitleFontSizePt.ToString(CultureInfo.InvariantCulture);
                SelectStyle(cboTitleStyle, d.TitleFontStyle);
                chkSecTitles.IsChecked = d.ShowSecondaryTitles;
                SelectFont(cboSecFont, d.SecondaryTitleFontFamily);
                txtSecSize.Text = d.SecondaryTitleFontSizePt.ToString(CultureInfo.InvariantCulture);
                SelectStyle(cboSecStyle, d.SecondaryTitleFontStyle);
            }
            finally { _loading = false; }
            RenderPreview();
        }

        private static void SelectFont(ComboBox cbo, string family)
        {
            if (string.IsNullOrWhiteSpace(family)) return;
            foreach (var it in cbo.Items)
                if (string.Equals(it as string, family, StringComparison.OrdinalIgnoreCase))
                { cbo.SelectedItem = it; return; }
        }

        private static void SelectStyle(ComboBox cbo, string style)
        {
            StyleToFlags(style, out bool b, out bool i);
            cbo.SelectedItem = FlagsToStyle(b, i);
        }

        private void OnSaveStyle(object sender, RoutedEventArgs e)
        {
            var dlg = new Microsoft.Win32.SaveFileDialog
            {
                Filter = "Polar plot style (*.json)|*.json",
                InitialDirectory = PolarPlotStyleStore.StoreDir(),
                FileName = "MyPolarPlotStyle.json",
                Title = "Save polar plot style preset",
            };
            if (dlg.ShowDialog() != true) return;
            try
            {
                PolarPlotStyleStore.Save(dlg.FileName, CaptureStyle());
                SetStatus("Style saved: " + Path.GetFileName(dlg.FileName));
            }
            catch (Exception ex)
            { MessageBox.Show("Could not save the style: " + ex.Message, "Polar Plots"); }
        }

        private void OnLoadStyle(object sender, RoutedEventArgs e)
        {
            var dlg = new Microsoft.Win32.OpenFileDialog
            {
                Filter = "Polar plot style (*.json)|*.json",
                InitialDirectory = PolarPlotStyleStore.StoreDir(),
                Title = "Load polar plot style preset",
            };
            if (dlg.ShowDialog() != true) return;
            try
            {
                var d = PolarPlotStyleStore.Load(dlg.FileName);
                if (d == null || d.Format != "JennessPolarPlotStyle")
                {
                    MessageBox.Show("This file is not a polar plot style preset.",
                        "Polar Plots");
                    return;
                }
                ApplyStyle(d);
                SetStatus("Style loaded: " + Path.GetFileName(dlg.FileName));
            }
            catch (Exception ex)
            { MessageBox.Show("Could not load the style: " + ex.Message, "Polar Plots"); }
        }

        // ---- helpers --------------------------------------------------------
        private string MakeFileBase()
        {
            string n = (cboSource.SelectedItem as PolarPlotData.Source)?.Name ?? "PolarPlot";
            var sb = new System.Text.StringBuilder();
            foreach (char c in n) sb.Append(char.IsLetterOrDigit(c) ? c : '_');
            return "PolarPlot_" + sb.ToString().Trim('_');
        }

        private void SetStatus(string t) => txtStatus.Text = t;

        private static int ParseInt(string t, int fallback) =>
            int.TryParse(t, NumberStyles.Integer, CultureInfo.InvariantCulture, out int v) ? v : fallback;
        private static double ParseDouble(string t, double fallback) =>
            double.TryParse(t, NumberStyles.Float, CultureInfo.InvariantCulture, out double v) ? v : fallback;

        private static double Clamp(double v) => v < 0 ? 0 : (v > 255 ? 255 : v);
    }
}
