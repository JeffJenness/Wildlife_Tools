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
using System.Globalization;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Media;
using ArcGIS.Core.CIM;
using ArcGIS.Desktop.Mapping.Controls;

namespace WildlifeTools.CropPlots
{
    /// <summary>
    /// The content of the Point Coordinates Settings window: edits a copy of
    /// the saved <see cref="PointCoordinatesSettings"/>, shows a preview of
    /// the callout's lines for a sample point, and saves on request.
    /// </summary>
    public partial class PointCoordinatesSettingsPanel : UserControl
    {
        private static readonly string[] StyleNames = { "Regular", "Bold", "Italic", "Bold Italic" };

        public event EventHandler CloseRequested;

        private PointCoordinatesSettings _s = PointCoordinatesSettings.Load();
        private bool _loading = true;
        private bool _swatchRefresh;

        public PointCoordinatesSettingsPanel()
        {
            InitializeComponent();
            var fonts = Fonts.SystemFontFamilies.Select(f => f.Source).OrderBy(s => s).ToList();
            cboCoordFont.ItemsSource = fonts;
            cboNameFont.ItemsSource = fonts;
            cboCoordStyle.ItemsSource = StyleNames;
            cboNameStyle.ItemsSource = StyleNames;
            foreach (string c in PointCoordinatesSettings.CalloutStyles)
            {
                cboCallout.Items.Add(c);
            }
            Loaded += (_, _) => { Show(_s); };
        }

        // =====================================================================
        // settings <-> controls
        // =====================================================================
        private void Show(PointCoordinatesSettings s)
        {
            _loading = true;
            chkLatDD.IsChecked = s.ShowLatDD;
            chkLonDD.IsChecked = s.ShowLonDD;
            chkLatDMS.IsChecked = s.ShowLatDMS;
            chkLonDMS.IsChecked = s.ShowLonDMS;
            chkGcs.IsChecked = s.ShowGcsName;
            chkX.IsChecked = s.ShowX;
            chkY.IsChecked = s.ShowY;
            chkPcs.IsChecked = s.ShowPcsName;
            chkLabels.IsChecked = s.LabelLines;
            txtDdDec.Text = s.DdDecimals.ToString(CultureInfo.CurrentCulture);
            txtDmsDec.Text = s.DmsDecimals.ToString(CultureInfo.CurrentCulture);
            txtXyDec.Text = s.XyDecimals.ToString(CultureInfo.CurrentCulture);
            cboCoordFont.SelectedItem = FontOrDefault(s.Coordinates.Family);
            txtCoordSize.Text = s.Coordinates.Size.ToString("0.#", CultureInfo.CurrentCulture);
            cboCoordStyle.SelectedItem = StyleName(s.Coordinates);
            cboNameFont.SelectedItem = FontOrDefault(s.Names.Family);
            txtNameSize.Text = s.Names.Size.ToString("0.#", CultureInfo.CurrentCulture);
            cboNameStyle.SelectedItem = StyleName(s.Names);
            cboCallout.SelectedItem = s.Callout;
            chkMarker.IsChecked = s.Marker;
            txtMarkerSize.Text = s.MarkerSize.ToString("0.#", CultureInfo.CurrentCulture);
            ApplyColor(cpCoord, s.Coordinates.Color);
            ApplyColor(cpName, s.Names.Color);
            ApplyColor(cpBackground, s.BackgroundColor);
            ApplyColor(cpOutline, s.OutlineColor);
            ApplyColor(cpLeader, s.LeaderColor);
            ApplyColor(cpMarker, s.MarkerColor);
            _loading = false;
            Preview();
        }

        private string FontOrDefault(string family)
        {
            var items = cboCoordFont.ItemsSource as System.Collections.Generic.List<string>;
            if (items != null && items.Contains(family ?? ""))
            {
                return family;
            }
            return items != null && items.Contains("Arial") ? "Arial" : items?.FirstOrDefault();
        }

        private static string StyleName(CalloutTextStyle st)
        {
            if (st.Bold && st.Italic) return "Bold Italic";
            if (st.Bold) return "Bold";
            if (st.Italic) return "Italic";
            return "Regular";
        }

        private static void StyleFrom(string name, CalloutTextStyle st)
        {
            st.Bold = name == "Bold" || name == "Bold Italic";
            st.Italic = name == "Italic" || name == "Bold Italic";
        }

        private static int Int(TextBox box, int fallback, int min, int max)
        {
            if (int.TryParse((box.Text ?? "").Trim(), out int v))
            {
                return Math.Max(min, Math.Min(max, v));
            }
            return fallback;
        }

        private static double Dbl(TextBox box, double fallback)
        {
            string t = (box.Text ?? "").Trim();
            if (double.TryParse(t, NumberStyles.Float, CultureInfo.CurrentCulture, out double v)
                || double.TryParse(t, NumberStyles.Float, CultureInfo.InvariantCulture, out v))
            {
                return v > 0 ? v : fallback;
            }
            return fallback;
        }

        /// <summary>Read the controls into the working settings.</summary>
        private void Gather()
        {
            _s.ShowLatDD = chkLatDD.IsChecked == true;
            _s.ShowLonDD = chkLonDD.IsChecked == true;
            _s.ShowLatDMS = chkLatDMS.IsChecked == true;
            _s.ShowLonDMS = chkLonDMS.IsChecked == true;
            _s.ShowGcsName = chkGcs.IsChecked == true;
            _s.ShowX = chkX.IsChecked == true;
            _s.ShowY = chkY.IsChecked == true;
            _s.ShowPcsName = chkPcs.IsChecked == true;
            _s.LabelLines = chkLabels.IsChecked == true;
            _s.DdDecimals = Int(txtDdDec, _s.DdDecimals, 0, 10);
            _s.DmsDecimals = Int(txtDmsDec, _s.DmsDecimals, 0, 6);
            _s.XyDecimals = Int(txtXyDec, _s.XyDecimals, 0, 8);
            _s.Coordinates.Family = cboCoordFont.SelectedItem as string ?? _s.Coordinates.Family;
            _s.Coordinates.Size = Dbl(txtCoordSize, _s.Coordinates.Size);
            StyleFrom(cboCoordStyle.SelectedItem as string ?? "Bold", _s.Coordinates);
            _s.Names.Family = cboNameFont.SelectedItem as string ?? _s.Names.Family;
            _s.Names.Size = Dbl(txtNameSize, _s.Names.Size);
            StyleFrom(cboNameStyle.SelectedItem as string ?? "Italic", _s.Names);
            _s.Callout = cboCallout.SelectedItem as string ?? _s.Callout;
            _s.Marker = chkMarker.IsChecked == true;
            _s.MarkerSize = Dbl(txtMarkerSize, _s.MarkerSize);
        }

        private void OnChanged(object sender, RoutedEventArgs e)
        {
            if (_loading)
            {
                return;
            }
            Gather();
            Preview();
        }

        // =====================================================================
        // colors (Esri's ColorPickerControl, with the swatch-refresh cycle
        // the Polar Plots window needed: its swatch bitmap is built at the
        // control's size, so a color applied before layout leaves it blank
        // unless the color is cycled once layout has finished)
        // =====================================================================
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
            if (sender is ColorPickerControl cp && e.NewSize.Width > 0 && e.NewSize.Height > 0)
            {
                RefreshSwatch(cp);
            }
        }

        private void RefreshSwatch(ColorPickerControl cp)
        {
            Dispatcher.BeginInvoke(new Action(() =>
            {
                _swatchRefresh = true;
                try
                {
                    cp.SelectedColor = new CIMRGBColor { R = 1, G = 2, B = 3, Alpha = 50 };
                    ApplyColor(cp, ColorFor(cp));
                }
                finally
                {
                    _swatchRefresh = false;
                }
            }), System.Windows.Threading.DispatcherPriority.Loaded);
        }

        private string ColorFor(ColorPickerControl cp)
        {
            if (ReferenceEquals(cp, cpCoord)) return _s.Coordinates.Color;
            if (ReferenceEquals(cp, cpName)) return _s.Names.Color;
            if (ReferenceEquals(cp, cpBackground)) return _s.BackgroundColor;
            if (ReferenceEquals(cp, cpOutline)) return _s.OutlineColor;
            if (ReferenceEquals(cp, cpLeader)) return _s.LeaderColor;
            return _s.MarkerColor;
        }

        private void StoreColor(ColorPickerControl cp, string hex)
        {
            if (ReferenceEquals(cp, cpCoord)) _s.Coordinates.Color = hex;
            else if (ReferenceEquals(cp, cpName)) _s.Names.Color = hex;
            else if (ReferenceEquals(cp, cpBackground)) _s.BackgroundColor = hex;
            else if (ReferenceEquals(cp, cpOutline)) _s.OutlineColor = hex;
            else if (ReferenceEquals(cp, cpLeader)) _s.LeaderColor = hex;
            else if (ReferenceEquals(cp, cpMarker)) _s.MarkerColor = hex;
        }

        private static void ApplyColor(ColorPickerControl cp, string hex)
        {
            var (r, g, b) = Rgb(hex);
            cp.SelectedColor = new CIMRGBColor { R = r, G = g, B = b, Alpha = 100 };
        }

        private static (int r, int g, int b) Rgb(string hex)
        {
            string h = (hex ?? "#000000").TrimStart('#');
            if (h.Length != 6)
            {
                return (0, 0, 0);
            }
            return (Convert.ToInt32(h.Substring(0, 2), 16), Convert.ToInt32(h.Substring(2, 2), 16),
                Convert.ToInt32(h.Substring(4, 2), 16));
        }

        private void PickerChanged(object sender, ColorChangedEventArgs e)
        {
            if (_swatchRefresh || _loading)
            {
                return;
            }
            if (sender is ColorPickerControl cp && e?.Color is CIMRGBColor rgb)
            {
                StoreColor(cp, "#" + ((int)Math.Round(rgb.R)).ToString("X2") + ((int)Math.Round(rgb.G)).ToString("X2")
                              + ((int)Math.Round(rgb.B)).ToString("X2"));
                Preview();
            }
        }

        // =====================================================================
        // preview, save, reset
        // =====================================================================
        private void Preview()
        {
            txtPreview.Inlines.Clear();
            var lines = _s.Lines(-111.631389, 35.198056, "GCS_North_American_1983", 441762.33, 3895342.80,
                "NAD_1983_UTM_Zone_12N", "m");
            if (lines.Count == 0)
            {
                txtPreview.Inlines.Add(new Run("(no lines selected)") { FontStyle = FontStyles.Italic });
                return;
            }
            bool first = true;
            foreach (var l in lines)
            {
                if (!first)
                {
                    txtPreview.Inlines.Add(new LineBreak());
                }
                first = false;
                CalloutTextStyle st = l.IsName ? _s.Names : _s.Coordinates;
                var (r, g, b) = Rgb(st.Color);
                var run = new Run(l.Text)
                {
                    FontSize = Math.Max(6, st.Size) * 96.0 / 72.0,
                    FontWeight = st.Bold ? FontWeights.Bold : FontWeights.Normal,
                    FontStyle = st.Italic ? FontStyles.Italic : FontStyles.Normal,
                    Foreground = new SolidColorBrush(Color.FromRgb((byte)r, (byte)g, (byte)b)),
                };
                try { run.FontFamily = new FontFamily(st.Family ?? "Arial"); } catch { }
                txtPreview.Inlines.Add(run);
            }
            var (br, bg, bb) = Rgb(_s.BackgroundColor);
            txtPreview.Background = _s.Callout == PointCoordinatesSettings.CalloutNone
                ? null : new SolidColorBrush(Color.FromRgb((byte)br, (byte)bg, (byte)bb));
            txtPreview.Padding = _s.Callout == PointCoordinatesSettings.CalloutNone ? new Thickness(0) : new Thickness(5);
        }

        private void OnSave(object sender, RoutedEventArgs e)
        {
            Gather();
            _s.Save();
            txtStatus.Text = "Saved. The Point Coordinates tool uses these settings from its next click.";
        }

        private void OnReset(object sender, RoutedEventArgs e)
        {
            _s = new PointCoordinatesSettings();
            Show(_s);
            txtStatus.Text = "Defaults restored (click Save to keep them).";
        }

        private void OnHelp(object sender, RoutedEventArgs e) => WebHelp.Open("cartography/point-coordinates.html");

        private void OnClose(object sender, RoutedEventArgs e) => CloseRequested?.Invoke(this, EventArgs.Empty);
    }
}
