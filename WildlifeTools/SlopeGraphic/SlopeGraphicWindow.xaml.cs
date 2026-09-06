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
using WildlifeTools.PolarPlots;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.SlopeGraphic
{
    /// <summary>
    /// Non-modal Slope Graphic window: live-updating preview of the classic
    /// Grid Tools slope graphic (mean line / SD wedge / range wedge) extended
    /// with a percentile wedge, dashed median, protractor, value labels, and a
    /// 95% CI arc. Sources, raster streaming/caching, color plumbing, in/cm
    /// units, presets, and the three outputs all follow the Polar Plots window.
    /// </summary>
    public partial class SlopeGraphicWindow : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private bool _loading = true;
        private bool _busy;
        private bool _rereadPending;
        private List<PolarPlotData.Source> _sources = new List<PolarPlotData.Source>();
        private SlopeData.Result _lastResult;
        private bool _srcIsRaster;
        private readonly Dictionary<string, SlopeData.RasterCache> _rasterCache =
            new Dictionary<string, SlopeData.RasterCache>();

        private RgbaColor _colMean = new RgbaColor(0, 0, 0, 255);
        private RgbaColor _colMedian = new RgbaColor(80, 80, 80, 255);
        private RgbaColor _colSdFill = new RgbaColor(0, 255, 255, 255);
        private RgbaColor _colSdOutline = new RgbaColor(0, 90, 90, 255);
        private RgbaColor _colRangeFill = new RgbaColor(255, 200, 200, 255);
        private RgbaColor _colRangeOutline = new RgbaColor(150, 70, 70, 255);
        private RgbaColor _colPctlFill = new RgbaColor(255, 235, 170, 255);
        private RgbaColor _colPctlOutline = new RgbaColor(160, 120, 40, 255);
        private RgbaColor _colCi = new RgbaColor(200, 30, 30, 255);
        private RgbaColor _colProtractor = new RgbaColor(120, 120, 120, 255);
        private RgbaColor _colLabel = new RgbaColor(60, 60, 60, 255);
        private RgbaColor _colTitle = new RgbaColor(0, 0, 0, 255);
        private RgbaColor _colSec = new RgbaColor(80, 80, 80, 255);

        private static readonly string[] FontStyleNames =
            { "Regular", "Bold", "Italic", "Bold Italic" };
        private static readonly CultureInfo CI = CultureInfo.InvariantCulture;

        public SlopeGraphicWindow()
        {
            InitializeComponent();
            var fonts = Fonts.SystemFontFamilies
                .Select(f => f.Source).OrderBy(n => n).ToList();
            string dflt = fonts.Contains("Segoe UI") ? "Segoe UI" : fonts.FirstOrDefault();
            cboLabelFont.ItemsSource = fonts;
            cboLabelFont.SelectedItem = dflt;
            cboTitleFont.ItemsSource = fonts.ToList();
            cboTitleFont.SelectedItem = dflt;
            cboSecFont.ItemsSource = fonts.ToList();
            cboSecFont.SelectedItem = dflt;
            cboLabelStyle.ItemsSource = FontStyleNames;
            cboLabelStyle.SelectedIndex = 0;
            cboTitleStyle.ItemsSource = FontStyleNames;
            cboTitleStyle.SelectedIndex = 1;
            cboSecStyle.ItemsSource = FontStyleNames;
            cboSecStyle.SelectedIndex = 2;
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
                if (_sources.Count > 0) cboSource.SelectedIndex = 0;   // triggers read
                else { RenderPreview(); SetStatus("No layers or tables in the map."); }
            }
            catch (Exception ex) { _loading = false; SetStatus("Error: " + ex.Message); }
        }

        // ---- color plumbing (same sentinel-refresh idiom as Polar Plots) ----
        private bool _swatchRefresh;

        private void PickerLoaded(object sender, RoutedEventArgs e)
        {
            if (sender is ColorPickerControl cp)
            {
                cp.SizeChanged -= PickerSizeChanged;
                cp.SizeChanged += PickerSizeChanged;
                RefreshSwatch(cp);
            }
        }

        private void PickerSizeChanged(object sender, SizeChangedEventArgs e)
        {
            if (sender is ColorPickerControl cp &&
                e.NewSize.Width > 0 && e.NewSize.Height > 0)
                RefreshSwatch(cp);
        }

        private void RefreshSwatch(ColorPickerControl cp)
        {
            Dispatcher.BeginInvoke(new Action(() =>
            {
                _swatchRefresh = true;
                try
                {
                    cp.SelectedColor = new CIMRGBColor { R = 1, G = 2, B = 3, Alpha = 0.5f };
                    ApplyColor(cp, ColorFieldFor(cp));
                }
                finally { _swatchRefresh = false; }
            }), System.Windows.Threading.DispatcherPriority.Loaded);
        }

        private RgbaColor ColorFieldFor(ColorPickerControl cp)
        {
            if (ReferenceEquals(cp, cpMean)) return _colMean;
            if (ReferenceEquals(cp, cpMedian)) return _colMedian;
            if (ReferenceEquals(cp, cpSdFill)) return _colSdFill;
            if (ReferenceEquals(cp, cpSdOutline)) return _colSdOutline;
            if (ReferenceEquals(cp, cpRangeFill)) return _colRangeFill;
            if (ReferenceEquals(cp, cpRangeOutline)) return _colRangeOutline;
            if (ReferenceEquals(cp, cpPctlFill)) return _colPctlFill;
            if (ReferenceEquals(cp, cpPctlOutline)) return _colPctlOutline;
            if (ReferenceEquals(cp, cpCi)) return _colCi;
            if (ReferenceEquals(cp, cpProtractor)) return _colProtractor;
            if (ReferenceEquals(cp, cpLabelColor)) return _colLabel;
            if (ReferenceEquals(cp, cpSecColor)) return _colSec;
            return _colTitle;
        }

        private void StoreColor(ColorPickerControl cp, RgbaColor c)
        {
            if (ReferenceEquals(cp, cpMean)) _colMean = c;
            else if (ReferenceEquals(cp, cpMedian)) _colMedian = c;
            else if (ReferenceEquals(cp, cpSdFill)) _colSdFill = c;
            else if (ReferenceEquals(cp, cpSdOutline)) _colSdOutline = c;
            else if (ReferenceEquals(cp, cpRangeFill)) _colRangeFill = c;
            else if (ReferenceEquals(cp, cpRangeOutline)) _colRangeOutline = c;
            else if (ReferenceEquals(cp, cpPctlFill)) _colPctlFill = c;
            else if (ReferenceEquals(cp, cpPctlOutline)) _colPctlOutline = c;
            else if (ReferenceEquals(cp, cpCi)) _colCi = c;
            else if (ReferenceEquals(cp, cpProtractor)) _colProtractor = c;
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

        private static double Clamp(double v) => Math.Min(Math.Max(v, 0), 255);

        // ---- event handlers (one per delegate type) -------------------------
        private async void SourceSelChanged(object s, SelectionChangedEventArgs e)
        {
            if (_loading) return;
            await PopulateFieldsAsync();
            await RereadAsync();
        }
        private async void RereadSel(object s, SelectionChangedEventArgs e)
        {
            if (_loading) return;
            if (ReferenceEquals(s, cboSlopeUnit))
                _unitDetectNote = null;    // user overrode the auto-detected unit
            await RereadAsync();
        }
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
            if (_swatchRefresh) return;
            if (s is ColorPickerControl cp)
            {
                if (e?.Color == null)
                    StoreColor(cp, new RgbaColor(0, 0, 0, 0));
                else if (TryCimToRgba(e.Color, out var c))
                    StoreColor(cp, c);
            }
            if (!_loading) RenderPreview();
        }

        // ---- size units (in / cm) ------------------------------------------
        private const double CmPerIn = 2.54;
        private bool _cm;
        private double DisplayToInches(double v) => _cm ? v / CmPerIn : v;

        private void UnitChanged(object s, SelectionChangedEventArgs e)
        {
            if (lblSize == null) return;           // fires during XAML parse
            bool cm = cboUnit.SelectedIndex == 1;
            if (cm == _cm) return;
            _cm = cm;
            bool was = _loading;
            _loading = true;
            try
            {
                if (double.TryParse(txtSize.Text, NumberStyles.Float, CI, out double v))
                    txtSize.Text = Math.Round(v * (cm ? CmPerIn : 1.0 / CmPerIn), 3)
                        .ToString(CI);
                lblSize.Content = "Graphic size (" + (cm ? "cm" : "in") + ")";
            }
            finally { _loading = was; }
            if (!_loading) RenderPreview();
        }

        // ---- data flow ------------------------------------------------------
        private string _unitDetectNote;    // shown in the status line

        private async System.Threading.Tasks.Task PopulateFieldsAsync()
        {
            var src = cboSource.SelectedItem as PolarPlotData.Source;
            if (src == null) return;
            _srcIsRaster = src.IsRaster;
            pnlVectorSource.Visibility = _srcIsRaster ? Visibility.Collapsed : Visibility.Visible;
            pnlRasterSource.Visibility = _srcIsRaster ? Visibility.Visible : Visibility.Collapsed;
            _unitDetectNote = null;
            if (_srcIsRaster)
            {
                // Default the unit from the raster's geoprocessing history
                // (Esri's Slope / Surface Parameters record DEGREE or
                // PERCENT_RISE in the metadata lineage). Runs BEFORE the read
                // so the detected unit governs it; the user can still override.
                var detected = await QueuedTask.Run(() => SlopeData.DetectRasterUnit(src));
                if (detected != null)
                {
                    bool was = _loading;
                    _loading = true;
                    cboSlopeUnit.SelectedIndex = (int)detected.Value;
                    _loading = was;
                    _unitDetectNote = "slope unit set to " +
                        SlopeStats.UnitLabel(detected.Value) +
                        " from the raster's geoprocessing history";
                }
            }
            var fields = await QueuedTask.Run(() => PolarPlotData.NumericFields(src));
            _loading = true;
            cboField.ItemsSource = fields;
            cboWField.ItemsSource = fields.ToList();
            if (fields.Count > 0) { cboField.SelectedIndex = 0; cboWField.SelectedIndex = 0; }
            rbWLength.IsEnabled = src.IsPolyline;
            if (!src.IsPolyline && rbWLength.IsChecked == true) rbWNone.IsChecked = true;
            _loading = false;
            SyncEnabled();
        }

        private void SyncEnabled()
        {
            cboWField.IsEnabled = rbWField.IsChecked == true;
        }

        private async System.Threading.Tasks.Task RereadAsync()
        {
            if (_busy) { _rereadPending = true; return; }
            var src = cboSource.SelectedItem as PolarPlotData.Source;
            if (src == null) { RenderPreview(); return; }
            _busy = true;
            SetStatus(src.IsRaster ? "Reading raster..." : "Reading data...");
            if (src.IsRaster)
            {
                barProgress.Value = 0;
                barProgress.Visibility = Visibility.Visible;
            }
            try
            {
                var o = BuildOptions();
                var prog = new Progress<double>(f =>
                {
                    barProgress.Value = f * 100.0;
                    SetStatus("Reading raster... " + ((int)Math.Round(f * 100.0)) + "%");
                });
                var res = await QueuedTask.Run(() => SlopeData.Read(
                    src, o, src.IsRaster ? prog : null, _rasterCache));
                _lastResult = res;
                RenderPreview();
            }
            catch (Exception ex) { SetStatus("Error reading data: " + ex.Message); }
            finally { _busy = false; barProgress.Visibility = Visibility.Collapsed; }
            if (_rereadPending)
            {
                _rereadPending = false;
                await RereadAsync();
            }
        }

        private SlopeUnit UnitFromCombo() =>
            cboSlopeUnit.SelectedIndex == 1 ? SlopeUnit.Percent
            : cboSlopeUnit.SelectedIndex == 2 ? SlopeUnit.Ratio
            : cboSlopeUnit.SelectedIndex == 3 ? SlopeUnit.Gradians
            : SlopeUnit.Degrees;

        private SlopeData.Options BuildOptions()
        {
            var o = new SlopeData.Options
            {
                FieldName = cboField.SelectedItem as string ?? "",
                WeightOption = rbWLength.IsChecked == true ? PolarWeightOption.ByLength
                    : rbWField.IsChecked == true ? PolarWeightOption.ByAttribute
                    : PolarWeightOption.None,
                WeightField = cboWField.SelectedItem as string ?? "",
                UseSelectedOnly = chkSelected.IsChecked == true,
                Unit = UnitFromCombo(),
            };
            switch (cboRasterSample.SelectedIndex)
            {
                case 1: o.RasterSampleStep = 1; break;
                case 2: o.RasterSampleStep = 2; break;
                case 3: o.RasterSampleStep = 4; break;
                case 4: o.RasterSampleStep = 8; break;
                case 5: o.RasterSampleStep = 16; break;
                default: o.RasterSampleStep = 0; break;
            }
            return o;
        }

        private SlopeGraphicSettings BuildSettings()
        {
            var s = new SlopeGraphicSettings
            {
                SizeIn = Math.Max(0.5, DisplayToInches(ParseD(txtSize.Text, _cm ? 7.6 : 3.0))),
                Unit = UnitFromCombo(),
                ShowMeanLine = chkMean.IsChecked == true,
                MeanColor = _colMean,
                MeanWidthPt = Math.Max(0.2, ParseD(txtMeanWidth.Text, 2.0)),
                ShowMedianLine = chkMedian.IsChecked == true,
                MedianColor = _colMedian,
                ShowSdWedge = chkSd.IsChecked == true,
                SdFill = _colSdFill,
                SdOutline = _colSdOutline,
                ShowRangeWedge = chkRange.IsChecked == true,
                RangeFill = _colRangeFill,
                RangeOutline = _colRangeOutline,
                ShowPercentileWedge = chkPctl.IsChecked == true,
                PercentileLo = Math.Min(Math.Max(ParseD(txtPctlLo.Text, 5.0), 0.0), 100.0),
                PercentileHi = Math.Min(Math.Max(ParseD(txtPctlHi.Text, 95.0), 0.0), 100.0),
                PercentileFill = _colPctlFill,
                PercentileOutline = _colPctlOutline,
                ShowCiArc = chkCi.IsChecked == true,
                CiColor = _colCi,
                ShowProtractor = chkProtractor.IsChecked == true,
                ShowProtractorLabels = chkProtLabels.IsChecked == true,
                ProtractorColor = _colProtractor,
                ShowBoundaryLabels = chkBoundaryLabels.IsChecked == true,
                LabelFontFamily = cboLabelFont.SelectedItem as string ?? "Segoe UI",
                LabelFontSizePt = Math.Max(4.0, ParseD(txtLabelSize.Text, 9.0)),
                LabelColor = _colLabel,
                ShowPrimaryTitle = chkPrimaryTitle.IsChecked == true,
                PrimaryTitle = txtPrimaryTitle.Text ?? "",
                TitleFontFamily = cboTitleFont.SelectedItem as string ?? "Segoe UI",
                TitleFontSizePt = Math.Max(6.0, ParseD(txtTitleSize.Text, 16.0)),
                TitleColor = _colTitle,
                ShowSecondaryTitles = chkSecTitles.IsChecked == true,
                SecondaryTitleFontFamily = cboSecFont.SelectedItem as string ?? "Segoe UI",
                SecondaryTitleFontSizePt = Math.Max(4.0, ParseD(txtSecSize.Text, 9.0)),
                SecondaryTitleColor = _colSec,
                DatasetName = (cboSource.SelectedItem as PolarPlotData.Source)?.Name ?? "",
            };
            StyleToFlags(cboLabelStyle.SelectedItem as string, out bool lb, out bool li);
            s.LabelBold = lb; s.LabelItalic = li;
            StyleToFlags(cboTitleStyle.SelectedItem as string, out bool tb, out bool ti);
            s.TitleBold = tb; s.TitleItalic = ti;
            StyleToFlags(cboSecStyle.SelectedItem as string, out bool sb, out bool si);
            s.SecondaryTitleBold = sb; s.SecondaryTitleItalic = si;
            if (s.PercentileHi < s.PercentileLo)
            { double t = s.PercentileLo; s.PercentileLo = s.PercentileHi; s.PercentileHi = t; }
            return s;
        }

        private List<string> SecondaryLines(SlopeGraphicSettings s)
        {
            var lines = new List<string>();
            if (!s.ShowSecondaryTitles) return lines;
            var st = _lastResult?.Stats;
            lines.Add("Source: " + s.DatasetName +
                (_srcIsRaster ? " (cell values, full extent)"
                              : " (field [" + (cboField.SelectedItem as string ?? "") + "])"));
            if (st != null)
                lines.Add((_srcIsRaster ? "Cells analyzed: " : "Count analyzed: ") +
                    st.Count.ToString("N0", CI) +
                    (_lastResult?.SamplingNote != null
                        ? " (" + _lastResult.SamplingNote + ")" : ""));
            return lines;
        }

        // ---- render ---------------------------------------------------------
        private PolarPlotModel _model;
        private void RenderPreview()
        {
            try
            {
                var s = BuildSettings();
                var st = _lastResult?.Stats;
                var model = SlopeGraphicBuilder.Build(s, st, SecondaryLines(s));
                _model = model;
                double ppd = VisualTreeHelper.GetDpi(this).PixelsPerDip;
                var dg = PolarPlotWpf.Render(model, ppd, showRulers: true, rulersCm: _cm);
                imgPreview.Source = new DrawingImage(dg);
                if (st == null)
                {
                    SetStatus("No usable slope values.");
                    return;
                }
                SetStatus(st.Count.ToString("N0", CI) +
                    (_srcIsRaster ? " cells analyzed" : " value(s) analyzed") +
                    (_lastResult.SamplingNote != null
                        ? " (" + _lastResult.SamplingNote + ")" : "") +
                    (_lastResult.ExcludedInvalid > 0
                        ? "; " + _lastResult.ExcludedInvalid.ToString("N0", CI) +
                          " invalid (negative / beyond vertical) excluded" : "") +
                    (_unitDetectNote != null ? "; " + _unitDetectNote : ""));
            }
            catch (Exception ex) { SetStatus("Render error: " + ex.Message); }
        }

        // ---- actions --------------------------------------------------------
        private void OnCalcStats(object sender, RoutedEventArgs e)
        {
            var st = _lastResult?.Stats;
            if (st == null) { MessageBox.Show("No data to analyze.", "Slope Graphic"); return; }
            var header = new List<string>
            {
                "Dataset = " + ((cboSource.SelectedItem as PolarPlotData.Source)?.Name ?? ""),
                "Slope Unit = " + SlopeStats.UnitLabel(st.Unit),
            };
            if (_srcIsRaster)
            {
                header.Add("Source = Raster cell values (full raster extent; " +
                           "clip the raster first to analyze a specific area)");
                if (_lastResult.SamplingNote != null)
                    header.Add("Sampling = " + _lastResult.SamplingNote);
            }
            else
            {
                header.Add("Slope Field = [" + (cboField.SelectedItem as string ?? "") + "]");
                header.Add("Weighting = " + (rbWLength.IsChecked == true ? "By polyline length"
                    : rbWField.IsChecked == true
                        ? "Field [" + (cboWField.SelectedItem as string ?? "") + "]" : "None"));
            }
            new SlopeStatsWindow(st, header) { Owner = this }.Show();
        }

        private void OnSaveSvg(object sender, RoutedEventArgs e)
        {
            if (_model == null) { MessageBox.Show("Nothing to save yet.", "Slope Graphic"); return; }
            var dlg = new Microsoft.Win32.SaveFileDialog
            {
                Filter = "SVG file (*.svg)|*.svg",
                FileName = MakeFileBase() + ".svg",
                Title = "Save slope graphic as SVG",
            };
            if (dlg.ShowDialog() != true) return;
            try
            {
                File.WriteAllText(dlg.FileName, PolarPlotSvg.Render(_model));
                SetStatus("Saved " + Path.GetFileName(dlg.FileName));
            }
            catch (Exception ex)
            { MessageBox.Show("Could not save SVG: " + ex.Message, "Slope Graphic"); }
        }

        private async void OnAddGraphic(object sender, RoutedEventArgs e)
        {
            if (_model == null) { MessageBox.Show("Nothing to add yet.", "Slope Graphic"); return; }
            var model = _model;
            string name = "Slope Graphic - " +
                ((cboSource.SelectedItem as PolarPlotData.Source)?.Name ?? "slope");
            btnAddGraphic.IsEnabled = false;
            try
            {
                string where = await LayoutPlacement.AddChart(this, model,
                    name);
                if (where != null) SetStatus(where);
            }
            catch (Exception ex) { MessageBox.Show(ex.Message, "Slope Graphic"); }
            finally { btnAddGraphic.IsEnabled = true; }
        }

        private void OnClose(object sender, RoutedEventArgs e) => Close();

        // ---- style presets --------------------------------------------------
        private SlopeStyleDto CaptureStyle() => new SlopeStyleDto
        {
            SizeIn = DisplayToInches(ParseD(txtSize.Text, _cm ? 7.6 : 3.0)),
            Units = _cm ? "cm" : "in",
            ShowMeanLine = chkMean.IsChecked == true,
            MeanColor = SlopeStyleDto.FromColor(_colMean),
            MeanWidthPt = ParseD(txtMeanWidth.Text, 2.0),
            ShowMedianLine = chkMedian.IsChecked == true,
            MedianColor = SlopeStyleDto.FromColor(_colMedian),
            ShowSdWedge = chkSd.IsChecked == true,
            SdFill = SlopeStyleDto.FromColor(_colSdFill),
            SdOutline = SlopeStyleDto.FromColor(_colSdOutline),
            ShowRangeWedge = chkRange.IsChecked == true,
            RangeFill = SlopeStyleDto.FromColor(_colRangeFill),
            RangeOutline = SlopeStyleDto.FromColor(_colRangeOutline),
            ShowPercentileWedge = chkPctl.IsChecked == true,
            PercentileLo = ParseD(txtPctlLo.Text, 5.0),
            PercentileHi = ParseD(txtPctlHi.Text, 95.0),
            PercentileFill = SlopeStyleDto.FromColor(_colPctlFill),
            PercentileOutline = SlopeStyleDto.FromColor(_colPctlOutline),
            ShowCiArc = chkCi.IsChecked == true,
            CiColor = SlopeStyleDto.FromColor(_colCi),
            ShowProtractor = chkProtractor.IsChecked == true,
            ShowProtractorLabels = chkProtLabels.IsChecked == true,
            ProtractorColor = SlopeStyleDto.FromColor(_colProtractor),
            ShowBoundaryLabels = chkBoundaryLabels.IsChecked == true,
            LabelFontFamily = cboLabelFont.SelectedItem as string ?? "Segoe UI",
            LabelFontSizePt = ParseD(txtLabelSize.Text, 9),
            LabelFontStyle = cboLabelStyle.SelectedItem as string ?? "Regular",
            LabelColor = SlopeStyleDto.FromColor(_colLabel),
            ShowPrimaryTitle = chkPrimaryTitle.IsChecked == true,
            TitleFontFamily = cboTitleFont.SelectedItem as string ?? "Segoe UI",
            TitleFontSizePt = ParseD(txtTitleSize.Text, 16),
            TitleFontStyle = cboTitleStyle.SelectedItem as string ?? "Bold",
            TitleColor = SlopeStyleDto.FromColor(_colTitle),
            ShowSecondaryTitles = chkSecTitles.IsChecked == true,
            SecondaryTitleFontFamily = cboSecFont.SelectedItem as string ?? "Segoe UI",
            SecondaryTitleFontSizePt = ParseD(txtSecSize.Text, 9),
            SecondaryTitleFontStyle = cboSecStyle.SelectedItem as string ?? "Italic",
            SecondaryTitleColor = SlopeStyleDto.FromColor(_colSec),
        };

        private void ApplyStyle(SlopeStyleDto d)
        {
            _loading = true;
            try
            {
                _cm = string.Equals(d.Units, "cm", StringComparison.OrdinalIgnoreCase);
                cboUnit.SelectedIndex = _cm ? 1 : 0;
                lblSize.Content = "Graphic size (" + (_cm ? "cm" : "in") + ")";
                txtSize.Text = Math.Round(d.SizeIn * (_cm ? CmPerIn : 1.0), 3).ToString(CI);
                chkMean.IsChecked = d.ShowMeanLine;
                _colMean = SlopeStyleDto.ToColor(d.MeanColor, _colMean);
                txtMeanWidth.Text = d.MeanWidthPt.ToString(CI);
                chkMedian.IsChecked = d.ShowMedianLine;
                _colMedian = SlopeStyleDto.ToColor(d.MedianColor, _colMedian);
                chkSd.IsChecked = d.ShowSdWedge;
                _colSdFill = SlopeStyleDto.ToColor(d.SdFill, _colSdFill);
                _colSdOutline = SlopeStyleDto.ToColor(d.SdOutline, _colSdOutline);
                chkRange.IsChecked = d.ShowRangeWedge;
                _colRangeFill = SlopeStyleDto.ToColor(d.RangeFill, _colRangeFill);
                _colRangeOutline = SlopeStyleDto.ToColor(d.RangeOutline, _colRangeOutline);
                chkPctl.IsChecked = d.ShowPercentileWedge;
                txtPctlLo.Text = d.PercentileLo.ToString(CI);
                txtPctlHi.Text = d.PercentileHi.ToString(CI);
                _colPctlFill = SlopeStyleDto.ToColor(d.PercentileFill, _colPctlFill);
                _colPctlOutline = SlopeStyleDto.ToColor(d.PercentileOutline, _colPctlOutline);
                chkCi.IsChecked = d.ShowCiArc;
                _colCi = SlopeStyleDto.ToColor(d.CiColor, _colCi);
                chkProtractor.IsChecked = d.ShowProtractor;
                chkProtLabels.IsChecked = d.ShowProtractorLabels;
                _colProtractor = SlopeStyleDto.ToColor(d.ProtractorColor, _colProtractor);
                chkBoundaryLabels.IsChecked = d.ShowBoundaryLabels;
                SelectItem(cboLabelFont, d.LabelFontFamily);
                txtLabelSize.Text = d.LabelFontSizePt.ToString(CI);
                SelectStyle(cboLabelStyle, d.LabelFontStyle);
                _colLabel = SlopeStyleDto.ToColor(d.LabelColor, _colLabel);
                chkPrimaryTitle.IsChecked = d.ShowPrimaryTitle;
                SelectItem(cboTitleFont, d.TitleFontFamily);
                txtTitleSize.Text = d.TitleFontSizePt.ToString(CI);
                SelectStyle(cboTitleStyle, d.TitleFontStyle);
                _colTitle = SlopeStyleDto.ToColor(d.TitleColor, _colTitle);
                chkSecTitles.IsChecked = d.ShowSecondaryTitles;
                SelectItem(cboSecFont, d.SecondaryTitleFontFamily);
                txtSecSize.Text = d.SecondaryTitleFontSizePt.ToString(CI);
                SelectStyle(cboSecStyle, d.SecondaryTitleFontStyle);
                _colSec = SlopeStyleDto.ToColor(d.SecondaryTitleColor, _colSec);
                foreach (var cp in new[] { cpMean, cpMedian, cpSdFill, cpSdOutline,
                    cpRangeFill, cpRangeOutline, cpPctlFill, cpPctlOutline, cpCi,
                    cpProtractor, cpLabelColor, cpTitleColor, cpSecColor })
                    ApplyColor(cp, ColorFieldFor(cp));
            }
            finally { _loading = false; }
            RenderPreview();
        }

        private static void SelectItem(ComboBox cbo, string value)
        {
            if (string.IsNullOrWhiteSpace(value)) return;
            foreach (var it in cbo.Items)
                if (string.Equals(it as string, value, StringComparison.OrdinalIgnoreCase))
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
                Filter = "Slope graphic style (*.json)|*.json",
                InitialDirectory = SlopeStyleStore.StoreDir(),
                FileName = "MySlopeGraphicStyle.json",
                Title = "Save slope graphic style preset",
            };
            if (dlg.ShowDialog() != true) return;
            try
            {
                SlopeStyleStore.Save(dlg.FileName, CaptureStyle());
                SetStatus("Style saved: " + Path.GetFileName(dlg.FileName));
            }
            catch (Exception ex)
            { MessageBox.Show("Could not save the style: " + ex.Message, "Slope Graphic"); }
        }

        private void OnLoadStyle(object sender, RoutedEventArgs e)
        {
            var dlg = new Microsoft.Win32.OpenFileDialog
            {
                Filter = "Slope graphic style (*.json)|*.json",
                InitialDirectory = SlopeStyleStore.StoreDir(),
                Title = "Load slope graphic style preset",
            };
            if (dlg.ShowDialog() != true) return;
            try
            {
                var d = SlopeStyleStore.Load(dlg.FileName);
                if (d == null || d.Format != "JennessSlopeGraphicStyle")
                {
                    MessageBox.Show("This file is not a slope graphic style preset.",
                        "Slope Graphic");
                    return;
                }
                ApplyStyle(d);
                SetStatus("Style loaded: " + Path.GetFileName(dlg.FileName));
            }
            catch (Exception ex)
            { MessageBox.Show("Could not load the style: " + ex.Message, "Slope Graphic"); }
        }

        // ---- misc -----------------------------------------------------------
        private string MakeFileBase()
        {
            string n = (cboSource.SelectedItem as PolarPlotData.Source)?.Name ?? "Slope";
            var sb = new System.Text.StringBuilder();
            foreach (char c in n) sb.Append(char.IsLetterOrDigit(c) ? c : '_');
            return "SlopeGraphic_" + sb.ToString().Trim('_');
        }

        private void SetStatus(string t) => txtStatus.Text = t;

        private static double ParseD(string t, double fallback) =>
            double.TryParse(t, NumberStyles.Float, CI, out double v) ? v : fallback;
    }
}
