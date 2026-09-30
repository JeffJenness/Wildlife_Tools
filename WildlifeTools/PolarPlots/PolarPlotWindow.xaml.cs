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
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using ArcGIS.Core.CIM;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using ArcGIS.Desktop.Mapping.Controls;
using ArcGIS.Desktop.Mapping.Events;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.PolarPlots
{
    /// <summary>
    /// Interactive Polar Plots window: pick a dataset and options, watch the
    /// plot update live, then save it as SVG, add it to a layout, or view
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
        private readonly List<double> _mag = new List<double>();
        private bool _hasMag;
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
        private RgbaColor _colBearing = new RgbaColor(60, 140, 60, 255);
        private RgbaColor _colWindLow = new RgbaColor(255, 244, 191, 255);
        private RgbaColor _colWindHigh = new RgbaColor(178, 44, 28, 255);
        private RgbaColor _colMean = new RgbaColor(200, 30, 30, 255);

        private static readonly string[] FontStyleNames =
            { "Regular", "Bold", "Italic", "Bold Italic" };

        public PolarPlotWindow()
        {
            InitializeComponent();
            cboPlotType.ItemsSource = AllPlotTypes;
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

            // A layer renamed in the Contents pane: the list and the
            // secondary title follow it. Layers or tables added or removed:
            // the list is rebuilt. A changed selection: the data are read
            // again when only the selected records are in use.
            try
            {
                var tokens = new List<ArcGIS.Core.Events.SubscriptionToken>
                {
                    MapMemberPropertiesChangedEvent.Subscribe(OnMapMemberChanged),
                    LayersAddedEvent.Subscribe(_ => QueueRefresh()),
                    LayersRemovedEvent.Subscribe(_ => QueueRefresh()),
                    StandaloneTablesAddedEvent.Subscribe(_ => QueueRefresh()),
                    StandaloneTablesRemovedEvent.Subscribe(_ => QueueRefresh()),
                    MapSelectionChangedEvent.Subscribe(OnMapSelectionChanged),
                };
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
                    }
                    catch { }
                };
            }
            catch { }
        }

        private void QueueRefresh()
        {
            try { Dispatcher.BeginInvoke(new Action(RefreshSources)); }
            catch { }
        }

        private bool _selectionRereadQueued;

        /// <summary>The map's selection changed: with "Use selected records
        /// only" on, the current dataset is read again (once, however many
        /// layers the selection touched).</summary>
        private void OnMapSelectionChanged(MapSelectionChangedEventArgs args)
        {
            try
            {
                if (_selectionRereadQueued) return;
                _selectionRereadQueued = true;
                Dispatcher.BeginInvoke(new Action(async () =>
                {
                    _selectionRereadQueued = false;
                    if (_loading || _srcIsRaster || chkSelected.IsChecked != true) return;
                    await RereadAsync();
                }));
            }
            catch { }
        }

        private void OnMapMemberChanged(MapMemberPropertiesChangedEventArgs args)
        {
            try
            {
                Dispatcher.BeginInvoke(new Action(ApplyRenames));
            }
            catch { }
        }

        /// <summary>Re-reads every listed member's current name; redraws the
        /// list and the preview when one changed. Nothing is read again.</summary>
        private void ApplyRenames()
        {
            try
            {
                bool any = false;
                foreach (var src in _sources)
                {
                    string nm = src.ListName();
                    if (!string.IsNullOrEmpty(nm) && nm != src.Name)
                    { src.Name = nm; any = true; }
                }
                if (!any) return;
                var cur = cboSource.SelectedItem;
                bool was = _loading;
                _loading = true;
                try
                {
                    cboSource.ItemsSource = null;
                    cboSource.ItemsSource = _sources;
                    cboSource.SelectedItem = cur;
                }
                finally { _loading = was; }
                if (!_loading && !_busy) RenderPreview();
            }
            catch { }
        }

        /// <summary>The ribbon button was clicked while the window is open:
        /// list the active map's layers and tables again, keeping the current
        /// dataset (and its data) when it is still there.</summary>
        private bool _refreshPending;      // a change arrived during a read

        internal async void RefreshSources()
        {
            if (_loading) return;
            if (_busy) { _refreshPending = true; return; }
            try
            {
                var map = MapView.Active?.Map;
                if (map == null) return;     // a layout is active: keep the list
                var fresh = await QueuedTask.Run(() => PolarPlotData.ListSources(map));
                var cur = cboSource.SelectedItem as PolarPlotData.Source;
                var keep = cur == null ? null
                    : fresh.FirstOrDefault(x => x.SameMember(cur));
                _sources = fresh;
                _loading = true;
                try
                {
                    cboSource.ItemsSource = _sources;
                    if (keep != null) cboSource.SelectedItem = keep;
                }
                finally { _loading = false; }
                if (keep != null) { RenderPreview(); return; }
                if (_sources.Count > 0) cboSource.SelectedIndex = 0;   // reads
                else
                {
                    _az.Clear(); _wt.Clear(); _mag.Clear(); _lastResult = null;
                    RenderPreview();
                    SetStatus("No feature layers or tables in the map.");
                }
            }
            catch (Exception ex) { _loading = false; SetStatus("Error: " + ex.Message); }
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
        // Esri's ColorPickerControl draws its swatch as a BITMAP sized to the
        // control (DropDownColorPicker.BuildImage uses ActualWidth - 20), and
        // it skips rebuilding on the first SizeChanged from 0x0. So a color
        // applied while the picker is unsized (a collapsed Expander) leaves a
        // permanently blank swatch UNLESS something re-fires the SelectedColor
        // change at real size. Worse, CIM colors compare by value, so
        // re-applying an equal color is a no-op on the dependency property.
        // Fix: on load AND on the first real SizeChanged, cycle the color
        // through a sentinel (guaranteed different) then back, deferred until
        // layout has finished, with StyleColor suppressed during the cycle.
        private bool _swatchRefresh;

        private void PickerLoaded(object sender, RoutedEventArgs e)
        {
            if (sender is ColorPickerControl cp)
            {
                cp.SizeChanged -= PickerSizeChanged;   // no double-hook on re-load
                cp.SizeChanged += PickerSizeChanged;
                RefreshSwatch(cp);
            }
        }

        private void PickerSizeChanged(object sender, System.Windows.SizeChangedEventArgs e)
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
            if (ReferenceEquals(cp, cpBinFill)) return _colBinFill;
            if (ReferenceEquals(cp, cpBinOutline)) return _colBinOutline;
            if (ReferenceEquals(cp, cpLow)) return _colLow;
            if (ReferenceEquals(cp, cpHigh)) return _colHigh;
            if (ReferenceEquals(cp, cpLabelColor)) return _colLabel;
            if (ReferenceEquals(cp, cpSecColor)) return _colSec;
            if (ReferenceEquals(cp, cpBearing)) return _colBearing;
            if (ReferenceEquals(cp, cpWindLow)) return _colWindLow;
            if (ReferenceEquals(cp, cpWindHigh)) return _colWindHigh;
            if (ReferenceEquals(cp, cpMean)) return _colMean;
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
            else if (ReferenceEquals(cp, cpBearing)) _colBearing = c;
            else if (ReferenceEquals(cp, cpWindLow)) _colWindLow = c;
            else if (ReferenceEquals(cp, cpWindHigh)) _colWindHigh = c;
            else if (ReferenceEquals(cp, cpMean)) _colMean = c;
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
        {
            if (_loading) return;
            if (ReferenceEquals(s, cboWField)) SuggestCounts();
            await RereadAsync();
        }

        /// <summary>A weight field named Count is, by convention, a raster
        /// attribute table's cell count: offer the counts reading.</summary>
        private void SuggestCounts()
        {
            string n = cboWField.SelectedItem as string ?? "";
            bool was = _loading;
            _loading = true;
            try
            {
                chkWCounts.IsChecked =
                    n.IndexOf("count", StringComparison.OrdinalIgnoreCase) >= 0;
            }
            finally { _loading = was; }
        }
        private async void RereadRouted(object s, RoutedEventArgs e)
        { if (!_loading) { SyncEnabled(); await RereadAsync(); } }
        private void StyleSel(object s, SelectionChangedEventArgs e)
        { if (!_loading) RenderPreview(); }
        private void StyleText(object s, TextChangedEventArgs e)
        { if (!_loading) RenderPreview(); }
        private void StyleRouted(object s, RoutedEventArgs e)
        { if (!_loading) { SyncEnabled(); RenderPreview(); } }

        // ---- diameter units (inches / centimeters) --------------------------
        // The model is ALWAYS inches internally; the unit choice only changes
        // how the two diameter boxes are interpreted and how the preview
        // rulers are drawn.
        private const double CmPerIn = 2.54;
        private bool _cm;
        private double DisplayToInches(double v) => _cm ? v / CmPerIn : v;

        private void UnitChanged(object s, SelectionChangedEventArgs e)
        {
            if (lblDiameter == null) return;      // fires during XAML parse
            bool cm = cboUnit.SelectedIndex == 1;
            if (cm == _cm) return;
            _cm = cm;
            bool was = _loading;
            _loading = true;                       // one render at the end
            try
            {
                ConvertBox(txtDiameter, cm ? CmPerIn : 1.0 / CmPerIn);
                ConvertBox(txtInner, cm ? CmPerIn : 1.0 / CmPerIn);
                ConvertBox(txtDot, cm ? CmPerIn : 1.0 / CmPerIn);
                UpdateUnitLabels();
            }
            finally { _loading = was; }
            if (!_loading) RenderPreview();
        }

        private void UpdateUnitLabels()
        {
            string u = _cm ? "cm" : "in";
            lblDiameter.Content = "Plot diameter (" + u + ")";
            lblInner.Content = "Inside circle diameter (" + u + ")";
            lblDot.Content = "Dot diameter (" + u + ")";
        }

        private static void ConvertBox(TextBox tb, double factor)
        {
            if (double.TryParse(tb.Text, NumberStyles.Float,
                                CultureInfo.InvariantCulture, out double v))
                tb.Text = Math.Round(v * factor, 3)
                    .ToString(CultureInfo.InvariantCulture);
        }
        private void StyleColor(object s, ColorChangedEventArgs e)
        {
            if (_swatchRefresh) return;    // ignore the swatch-rebuild cycle
            if (s is ColorPickerControl cp)
            {
                if (e?.Color == null)
                    StoreColor(cp, new RgbaColor(0, 0, 0, 0));        // "No color"
                else if (TryCimToRgba(e.Color, out var c))
                    StoreColor(cp, c);
            }
            if (!_loading) RenderPreview();
        }

        private bool _srcIsRaster;

        // Canonical plot-type list. The combo shows a FILTERED copy for raster
        // sources (no Bearings / Wind Rose -- they need individual observations
        // / a second field), so selections are mapped by NAME, never by the
        // combo's own index.
        private static readonly string[] AllPlotTypes =
        {
            "Peaks and Valleys", "Circular Bins (Rose)", "Shade by Density",
            "External Peaks and Valleys", "External Bins", "Bearings",
            "Wind Rose (stacked bands)", "Smoothed Density (kernel)", "Dot Plot",
        };
        private const int PlotIdxBearings = 5, PlotIdxWindRose = 6, PlotIdxDot = 8;

        /// <summary>Canonical index (into AllPlotTypes) of the current combo
        /// selection, regardless of which filtered list is showing.</summary>
        private int PlotTypeIdx()
        {
            int i = Array.IndexOf(AllPlotTypes, cboPlotType.SelectedItem as string);
            return i >= 0 ? i : 1;                  // default: Rose
        }

        /// <summary>Swap the combo between the full list (vector/table) and the
        /// raster-filtered list, keeping the selection when it survives and
        /// falling back to Rose when it does not. Call with _loading true.</summary>
        private void UpdatePlotTypeItems()
        {
            string cur = cboPlotType.SelectedItem as string;
            string[] items = _srcIsRaster
                ? AllPlotTypes.Where((n, i) =>
                    i != PlotIdxBearings && i != PlotIdxWindRose && i != PlotIdxDot)
                    .ToArray()
                : AllPlotTypes;
            if (cboPlotType.ItemsSource is string[] old && old.Length == items.Length)
                return;                             // already showing this list
            cboPlotType.ItemsSource = items;
            cboPlotType.SelectedItem = items.Contains(cur)
                ? cur : "Circular Bins (Rose)";
        }

        // Binned raster reads cached for the life of THIS window, so switching
        // back to an already-read raster is instant; discarded when the window
        // closes (the button releases the window instance).
        private readonly Dictionary<string, PolarPlotData.RasterBinCache>
            _rasterCache = new Dictionary<string, PolarPlotData.RasterBinCache>();

        private async System.Threading.Tasks.Task PopulateFieldsAsync()
        {
            var src = cboSource.SelectedItem as PolarPlotData.Source;
            if (src == null) return;
            _srcIsRaster = src.IsRaster;
            // Rasters: cell value IS the azimuth and cell counts are the
            // weights, so the field/weight/selection rows do not apply.
            pnlVectorSource.Visibility = _srcIsRaster ? Visibility.Collapsed : Visibility.Visible;
            pnlRasterSource.Visibility = _srcIsRaster ? Visibility.Visible : Visibility.Collapsed;
            var fields = await QueuedTask.Run(() => PolarPlotData.NumericFields(src));
            _loading = true;
            UpdatePlotTypeItems();
            cboAzField.ItemsSource = fields;
            cboWField.ItemsSource = fields.ToList();
            cboWindField.ItemsSource = fields.ToList();
            if (fields.Count > 0)
            { cboAzField.SelectedIndex = 0; cboWField.SelectedIndex = 0;
              cboWindField.SelectedIndex = 0; }
            rbAzPolyline.IsEnabled = src.IsPolyline;
            rbWLength.IsEnabled = src.IsPolyline;
            if (!src.IsPolyline && rbAzPolyline.IsChecked == true) rbAzField.IsChecked = true;
            if (!src.IsPolyline && rbWLength.IsChecked == true) rbWNone.IsChecked = true;
            SuggestCounts();
            _loading = false;
            SyncEnabled();
        }

        private void SyncEnabled()
        {
            cboAzField.IsEnabled = rbAzField.IsChecked == true;
            cboWField.IsEnabled = rbWField.IsChecked == true;
            chkWCounts.IsEnabled = rbWField.IsChecked == true;
            chkExcludeNeg1.IsEnabled = chkExcludeNeg.IsChecked == true;
            // The arc is drawn only with the arrow.
            chkMeanCI.IsEnabled = chkMeanArrow.IsChecked == true;
        }

        /// <summary>Each weight is a count of identical observations: a
        /// raster's binned cells, or a table weighted by a count field.</summary>
        private bool CountsMode =>
            _srcIsRaster || (_lastResult != null && _lastResult.WeightsAreCounts);

        private bool _rereadPending;    // a change arrived during a long read

        private async System.Threading.Tasks.Task RereadAsync()
        {
            // Never DROP a reread request: if one arrives while a (possibly
            // minutes-long) raster read is running, remember it and run again
            // when the current read finishes, so the controls and the plot can
            // never end up silently out of sync.
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
                var s = BuildSettings();
                // Progress<T> marshals back to this (UI) thread automatically.
                var prog = new Progress<double>(f =>
                {
                    barProgress.Value = f * 100.0;
                    SetStatus("Reading raster... " +
                              ((int)Math.Round(f * 100.0)) + "%");
                });
                var res = await QueuedTask.Run(() => PolarPlotData.Read(
                    src, s, src.IsRaster ? prog : null, _rasterCache));
                _lastResult = res;
                _az.Clear(); _az.AddRange(res.Azimuths);
                _wt.Clear(); _wt.AddRange(res.Weights);
                _mag.Clear(); _mag.AddRange(res.Magnitudes);
                _hasMag = res.HasMagnitudes;
                _weighted = res.Weighted;
                RenderPreview();
            }
            catch (Exception ex) { SetStatus("Error reading data: " + ex.Message); }
            finally { _busy = false; barProgress.Visibility = Visibility.Collapsed; }
            if (_refreshPending)
            {
                _refreshPending = false;
                RefreshSources();
            }
            if (_rereadPending)
            {
                _rereadPending = false;
                await RereadAsync();    // settings are re-captured fresh
            }
        }

        // ---- settings from controls ----------------------------------------
        private PolarPlotSettings BuildSettings()
        {
            var s = new PolarPlotSettings();
            switch (PlotTypeIdx())
            {
                case 0: s.PlotType = PolarPlotType.PeaksAndValleys; break;
                case 2: s.PlotType = PolarPlotType.ShadeByDensity; break;
                case 3: s.PlotType = PolarPlotType.ExternalPeaksAndValleys; break;
                case 4: s.PlotType = PolarPlotType.ExternalBins; break;
                case 5: s.PlotType = PolarPlotType.Bearings; break;
                case 6: s.PlotType = PolarPlotType.WindRose; break;
                case 7: s.PlotType = PolarPlotType.SmoothedDensity; break;
                case 8: s.PlotType = PolarPlotType.DotPlot; break;
                default: s.PlotType = PolarPlotType.CircularBins; break;
            }
            switch (cboRasterSample.SelectedIndex)
            {
                case 1: s.RasterSampleStep = 1; break;
                case 2: s.RasterSampleStep = 2; break;
                case 3: s.RasterSampleStep = 4; break;
                case 4: s.RasterSampleStep = 8; break;
                case 5: s.RasterSampleStep = 16; break;
                default: s.RasterSampleStep = 0; break;   // auto
            }
            s.MagnitudeField = cboWindField.SelectedItem as string ?? "";
            s.WindClasses = Math.Min(Math.Max(ParseInt(txtWindClasses.Text, 4), 1), 9);
            s.WindLow = _colWindLow;
            s.WindHigh = _colWindHigh;
            s.SmoothingDeg = Math.Min(Math.Max(ParseDouble(txtSmooth.Text, 20.0), 1.0), 90.0);
            s.DotDiameterIn = Math.Max(0.02,
                DisplayToInches(ParseDouble(txtDot.Text, _cm ? 0.2 : 0.08)));
            s.EqualArea = chkSqrt.IsChecked == true;
            s.ShowMeanArrow = chkMeanArrow.IsChecked == true;
            s.ShowMeanCI = chkMeanCI.IsChecked == true;
            s.MeanArrowColor = _colMean;
            s.BinCount = Math.Max(1, ParseInt(txtBins.Text, 72));
            s.PlotDiameterIn = Math.Max(0.5,
                DisplayToInches(ParseDouble(txtDiameter.Text, _cm ? 10.0 : 4.0)));
            s.OuterDiameterIn = s.PlotDiameterIn;
            s.InnerDiameterIn = Math.Max(0.2,
                DisplayToInches(ParseDouble(txtInner.Text, _cm ? 4.0 : 1.5)));
            s.BearingLine = _colBearing;
            s.BearingLineWidthPt = Math.Max(0.2, ParseDouble(txtBearingWidth.Text, 0.5));
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
            s.DatasetName = src?.LayerName ?? "";
            s.AzimuthOption = rbAzPolyline.IsChecked == true
                ? PolarAzimuthOption.ByPolyline : PolarAzimuthOption.ByAttribute;
            s.AzimuthField = cboAzField.SelectedItem as string ?? "";
            s.WeightOption = rbWLength.IsChecked == true ? PolarWeightOption.ByLength
                : rbWField.IsChecked == true ? PolarWeightOption.ByAttribute
                : PolarWeightOption.None;
            s.WeightField = cboWField.SelectedItem as string ?? "";
            s.WeightsAreCounts = rbWField.IsChecked == true &&
                                 chkWCounts.IsChecked == true;
            s.UseSelectedOnly = chkSelected.IsChecked == true;
            s.AddReverse = chkReverse.IsChecked == true;
            s.ExcludeNegative = chkExcludeNeg.IsChecked == true;
            // The narrower box applies only while the exclusion itself is on;
            // grayed out but still checked, it must not go on excluding -1.
            s.ExcludeMinusOneOnly = s.ExcludeNegative &&
                                    chkExcludeNeg1.IsChecked == true;
            return s;
        }

        private List<string> SecondaryTitleLines(PolarPlotSettings s, double[] bins)
        {
            var lines = new List<string>();
            if (!s.ShowSecondaryTitles) return lines;
            if (_srcIsRaster)
            {
                lines.Add("Source: " + s.DatasetName + " (cell values, full extent)");
                lines.Add("Cells analyzed: " +
                    _wt.Sum().ToString("N0", CultureInfo.InvariantCulture) +
                    (_lastResult?.SamplingNote != null
                        ? " (" + _lastResult.SamplingNote + ")" : ""));
            }
            else
            {
                string azSrc = s.AzimuthOption == PolarAzimuthOption.ByPolyline
                    ? "polyline direction" : "field [" + s.AzimuthField + "]";
                lines.Add("Source: " + s.DatasetName + " (" + azSrc + ")");
                if (CountsMode)
                {
                    lines.Add("Count analyzed: " +
                        Math.Round(_wt.Sum()).ToString("N0", CultureInfo.InvariantCulture) +
                        " (" + _az.Count.ToString("N0", CultureInfo.InvariantCulture) +
                        " records, counts in field [" + s.WeightField + "])");
                }
                else
                {
                    lines.Add("Count analyzed: " + _az.Count.ToString("N0", CultureInfo.InvariantCulture));
                    lines.Add(s.WeightOption == PolarWeightOption.None
                        ? "Values not weighted"
                        : "Total weight: " + _wt.Sum().ToString("N2", CultureInfo.InvariantCulture));
                }
            }
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
                // Plot-type-dependent option rows: only the rows relevant to
                // the current plot type exist in the dialog.
                int idx = PlotTypeIdx();
                bool shade = idx == 2;
                bool external = idx == 3 || idx == 4;
                bool bearings = idx == 5;
                bool wind = idx == 6;
                bool kde = idx == 7;
                bool dots = idx == 8;
                pnlDensity.Visibility = shade ? Visibility.Visible : Visibility.Collapsed;
                pnlWindColors.Visibility = wind ? Visibility.Visible : Visibility.Collapsed;
                pnlBinFill.Visibility = (shade || bearings || wind)
                    ? Visibility.Collapsed : Visibility.Visible;
                pnlOutline.Visibility = bearings ? Visibility.Collapsed : Visibility.Visible;
                pnlBearing.Visibility = bearings ? Visibility.Visible : Visibility.Collapsed;
                pnlInner.Visibility = external ? Visibility.Visible : Visibility.Collapsed;
                pnlWind.Visibility = wind ? Visibility.Visible : Visibility.Collapsed;
                pnlSmooth.Visibility = kde ? Visibility.Visible : Visibility.Collapsed;
                pnlDot.Visibility = dots ? Visibility.Visible : Visibility.Collapsed;

                var s = BuildSettings();
                // Bearings, Wind Rose and Dot Plot are removed from the combo
                // for raster sources; this is only a safety net (e.g. against
                // a stale selection mid-switch).
                if (_srcIsRaster && (s.PlotType == PolarPlotType.DotPlot ||
                                     s.PlotType == PolarPlotType.Bearings ||
                                     s.PlotType == PolarPlotType.WindRose))
                    s.PlotType = PolarPlotType.CircularBins;
                var st = new CircularStats(_az, _wt, _weighted, CountsMode, _srcIsRaster);
                var bins = st.Bin(s.BinCount);
                var model = PolarPlotBuilder.Build(s, bins, SecondaryTitleLines(s, bins),
                                                   _az, _wt, _mag, st);
                _model = model; _modelSettings = s;
                double ppd = VisualTreeHelper.GetDpi(this).PixelsPerDip;
                var dg = PolarPlotWpf.Render(model, ppd, showRulers: true,
                                             rulersCm: _cm);
                imgPreview.Source = new DrawingImage(dg);
                string plotted = _srcIsRaster
                    ? st.Count.ToString("N0", CultureInfo.InvariantCulture) +
                      " cells plotted (binned to 0.1°" +
                      (_lastResult?.SamplingNote != null
                          ? "; " + _lastResult.SamplingNote : "") + ")"
                    : CountsMode
                    ? Math.Round(_wt.Sum()).ToString("N0", CultureInfo.InvariantCulture) +
                      " observations plotted from " +
                      _az.Count.ToString("N0", CultureInfo.InvariantCulture) +
                      " records (counts in [" + s.WeightField + "])"
                    : _az.Count.ToString("N0", CultureInfo.InvariantCulture) +
                      " value(s) plotted";
                SetStatus(plotted +
                          (_lastResult != null && _lastResult.ExcludedNegative > 0
                              ? "; " + _lastResult.ExcludedNegative.ToString("N0",
                                    CultureInfo.InvariantCulture) +
                                (_srcIsRaster ? " flat/negative cells excluded"
                                              : " negative excluded") : "") +
                          (!_srcIsRaster && bearings && _az.Count > 500
                              ? "  (Bearings plots draw one line per value; large " +
                                "counts render slowly)" : "") +
                          (!_srcIsRaster && dots && _az.Count > 2000
                              ? "  (Dot plots draw one dot per value; consider " +
                                "Smoothed Density for large samples)" : ""));
            }
            catch (Exception ex) { SetStatus("Render error: " + ex.Message); }
        }

        // ---- actions --------------------------------------------------------
        private void OnCalcStats(object sender, RoutedEventArgs e)
        {
            if (_az.Count == 0) { MessageBox.Show("No data to analyze.", "Polar Plots"); return; }
            var s = BuildSettings();
            var st = new CircularStats(_az, _wt, _weighted, CountsMode, _srcIsRaster);
            var inv = CultureInfo.InvariantCulture;
            var header = _srcIsRaster
                ? new List<string>
                {
                    "Dataset = " + s.DatasetName,
                    "Azimuth Source = Raster cell values (full raster extent; " +
                        "clip the raster first to analyze a specific area)",
                    "Cells = " + (_lastResult?.RawCount ?? 0).ToString("N0",
                        CultureInfo.InvariantCulture) + " examined" +
                        (_lastResult?.SamplingNote != null
                            ? "; " + _lastResult.SamplingNote : ""),
                }
                : new List<string>
                {
                    "Dataset = " + s.DatasetName,
                    "Azimuth Source = " + (s.AzimuthOption == PolarAzimuthOption.ByPolyline
                        ? "Derived from polylines" : "Field [" + s.AzimuthField + "]"),
                    "Weighting = " + (s.WeightOption == PolarWeightOption.None ? "None"
                        : s.WeightOption == PolarWeightOption.ByLength ? "By polyline length"
                        : "Field [" + s.WeightField + "]" +
                          (CountsMode ? " (counts of observations)" : "")),
                };
            var r = _lastResult;
            if (!_srcIsRaster && r != null)
            {
                string n = r.RawCount.ToString("N0", inv);
                if (r.SelectionUsed)
                    header.Add("Records = Analyzing selected records (n = " + n +
                        (r.TableRows >= 0
                            ? " of " + r.TableRows.ToString("N0", inv) : "") + ")");
                else if (r.SelectionEmpty)
                    header.Add("Records = No records selected, analyzing all (n = " +
                               n + ")");
                else
                    header.Add("Records = Analyzing all records (n = " + n + ")");
                long dropped = r.RawCount - r.KeptCount;
                if (dropped > 0)
                    header.Add("   Note: " + dropped.ToString("N0", inv) +
                        " records excluded from analysis (no direction, no " +
                        "weight, or an excluded value). Final count = " +
                        r.KeptCount.ToString("N0", inv));
            }
            if (s.ExcludeMinusOneOnly)
                header.Add("** Analysis excludes all '-1' values" +
                    (r != null && r.ExcludedNegative > 0
                        ? " (" + r.ExcludedNegative.ToString("N0", inv) + " excluded)"
                        : ""));
            else if (s.ExcludeNegative)
                header.Add("** Analysis excludes all negative values" +
                    (r != null && r.ExcludedNegative > 0
                        ? " (" + r.ExcludedNegative.ToString("N0", inv) + " excluded)"
                        : ""));
            if (s.AddReverse)
            {
                header.Add("** NOTE: Reverse azimuths have been added for all records.");
                header.Add("   Every direction is cancelled by its opposite, so the mean");
                header.Add("   direction, dispersion, concentration, confidence interval");
                header.Add("   and Rayleigh test below describe the added reverses, not the data.");
            }
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
                InitialDirectory = ImageSaveFolder.Initial(),
            };
            if (dlg.ShowDialog() != true) return;
            ImageSaveFolder.Remember(dlg.FileName);
            try
            {
                File.WriteAllText(dlg.FileName, PolarPlotSvg.Render(_model));
                SetStatus("Saved " + Path.GetFileName(dlg.FileName));
            }
            catch (Exception ex) { MessageBox.Show("Could not save SVG: " + ex.Message, "Polar Plots"); }
        }

        private async void OnAddGraphic(object sender, RoutedEventArgs e)
        {
            if (_model == null) { MessageBox.Show("Nothing to add yet.", "Polar Plots"); return; }
            var model = _model;                     // snapshot for the MCT call
            string name = "Polar Plot - " +
                ((cboSource.SelectedItem as PolarPlotData.Source)?.Name ?? "plot");
            btnAddGraphic.IsEnabled = false;
            try
            {
                string where = await LayoutPlacement.AddChart(this, model,
                    name);
                if (where != null) SetStatus(where);
            }
            catch (Exception ex)
            {
                MessageBox.Show(ex.Message, "Polar Plots");
            }
            finally { btnAddGraphic.IsEnabled = true; }
        }

        private void OnClose(object sender, RoutedEventArgs e) => Close();

        // ---- style presets --------------------------------------------------
        private PolarPlotStyleDto CaptureStyle() => new PolarPlotStyleDto
        {
            PlotTypeIndex = PlotTypeIdx(),
            BinCount = ParseInt(txtBins.Text, 72),
            Units = _cm ? "cm" : "in",
            PlotDiameterIn = DisplayToInches(ParseDouble(txtDiameter.Text, _cm ? 10.0 : 4.0)),
            InnerDiameterIn = DisplayToInches(ParseDouble(txtInner.Text, _cm ? 4.0 : 1.5)),
            BearingLine = PolarPlotStyleDto.FromColor(_colBearing),
            BearingLineWidthPt = ParseDouble(txtBearingWidth.Text, 0.5),
            WindClasses = ParseInt(txtWindClasses.Text, 4),
            WindLow = PolarPlotStyleDto.FromColor(_colWindLow),
            WindHigh = PolarPlotStyleDto.FromColor(_colWindHigh),
            SmoothingDeg = ParseDouble(txtSmooth.Text, 20.0),
            DotDiameterIn = DisplayToInches(ParseDouble(txtDot.Text, _cm ? 0.2 : 0.08)),
            EqualArea = chkSqrt.IsChecked == true,
            ShowMeanArrow = chkMeanArrow.IsChecked == true,
            ShowMeanCI = chkMeanCI.IsChecked == true,
            MeanArrowColor = PolarPlotStyleDto.FromColor(_colMean),
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
                if (d.PlotTypeIndex >= 0 && d.PlotTypeIndex < AllPlotTypes.Length)
                {
                    // Select by NAME: the combo may be showing the raster-
                    // filtered list; a preset naming a hidden type falls back
                    // to Rose.
                    string want = AllPlotTypes[d.PlotTypeIndex];
                    cboPlotType.SelectedItem =
                        (cboPlotType.ItemsSource as string[])?.Contains(want) == true
                            ? want : "Circular Bins (Rose)";
                }
                txtBins.Text = d.BinCount.ToString(CultureInfo.InvariantCulture);
                // Units first: the DTO's diameters are always INCHES; convert
                // them into the preset's display unit.
                _cm = string.Equals(d.Units, "cm", StringComparison.OrdinalIgnoreCase);
                cboUnit.SelectedIndex = _cm ? 1 : 0;
                UpdateUnitLabels();
                double f = _cm ? CmPerIn : 1.0;
                txtDiameter.Text = Math.Round(d.PlotDiameterIn * f, 3)
                    .ToString(CultureInfo.InvariantCulture);
                txtInner.Text = Math.Round(d.InnerDiameterIn * f, 3)
                    .ToString(CultureInfo.InvariantCulture);
                _colBearing = PolarPlotStyleDto.ToColor(d.BearingLine, _colBearing);
                ApplyColor(cpBearing, _colBearing);
                txtBearingWidth.Text = d.BearingLineWidthPt.ToString(CultureInfo.InvariantCulture);
                txtWindClasses.Text = d.WindClasses.ToString(CultureInfo.InvariantCulture);
                _colWindLow = PolarPlotStyleDto.ToColor(d.WindLow, _colWindLow);
                _colWindHigh = PolarPlotStyleDto.ToColor(d.WindHigh, _colWindHigh);
                _colMean = PolarPlotStyleDto.ToColor(d.MeanArrowColor, _colMean);
                ApplyColor(cpWindLow, _colWindLow);
                ApplyColor(cpWindHigh, _colWindHigh);
                ApplyColor(cpMean, _colMean);
                txtSmooth.Text = d.SmoothingDeg.ToString(CultureInfo.InvariantCulture);
                txtDot.Text = Math.Round(d.DotDiameterIn * f, 3)
                    .ToString(CultureInfo.InvariantCulture);
                chkSqrt.IsChecked = d.EqualArea;
                chkMeanArrow.IsChecked = d.ShowMeanArrow;
                chkMeanCI.IsChecked = d.ShowMeanCI;
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
            string n = (cboSource.SelectedItem as PolarPlotData.Source)?.LayerName ?? "PolarPlot";
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
