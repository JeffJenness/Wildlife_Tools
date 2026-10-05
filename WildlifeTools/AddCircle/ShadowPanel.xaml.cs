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

using ArcGIS.Core.CIM;
using System;
using System.Globalization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools
{
    /// <summary>
    /// The "Drop shadow" box shared by the Circle, Rectangle, Polygon and Minimum
    /// Convex Polygon Background dialogs: a check box, Esri's color picker,
    /// Opacity / Distance / Spread / Size sliders with number boxes, a light-angle
    /// dial that can be clicked or dragged, and the number of layers. Distance and
    /// Size are typed in POINTS on a layout (the nearest thing to Photoshop's
    /// pixels) and in map units on a map; <see cref="TryGetOptions"/> hands them
    /// to the engine (<see cref="BackgroundShadow"/>) in container units.
    /// </summary>
    public partial class ShadowPanel : UserControl
    {
        private double _angle = 135;      // direction the light comes from, deg CCW from east
        private bool _angleSync;
        private CIMRGBColor _color = new CIMRGBColor { R = 0, G = 0, B = 0, Alpha = 100 };
        private bool _isLayout = true;
        private double _metersPerPageUnit = 0.0254;

        public ShadowPanel()
        {
            InitializeComponent();
            SetAngle(_angle, false);
        }

        /// <summary>Load the last-used settings and set the units for the surface.</summary>
        /// <param name="mapShadowMax">Map only: the top of the Distance and Size
        /// sliders, in map units (a layout uses 50 points).</param>
        public void Init(BackgroundToolPrefs prefs, bool isLayout, string mapUnitName,
            double metersPerPageUnit, double mapShadowMax)
        {
            prefs ??= new BackgroundToolPrefs();
            _isLayout = isLayout;
            _metersPerPageUnit = metersPerPageUnit > 0 ? metersPerPageUnit : 0.0254;
            if (!isLayout)
            {
                double top = mapShadowMax > 0 ? mapShadowMax : 100;
                DistanceSlider.Maximum = SizeSlider.Maximum = top;
                DistanceUnit.Text = SizeUnit.Text =
                    string.IsNullOrEmpty(mapUnitName) ? "map units" : mapUnitName;
            }
            ShadowCheck.IsChecked = prefs.ShadowEnabled;
            _color = new CIMRGBColor
            {
                R = (float)prefs.ShadowR, G = (float)prefs.ShadowG, B = (float)prefs.ShadowB, Alpha = 100,
            };
            OpacitySlider.Value = Math.Clamp(prefs.ShadowOpacity, 0, 100);
            DistanceSlider.Value = Math.Clamp(prefs.ShadowDistance, 0, DistanceSlider.Maximum);
            SpreadSlider.Value = Math.Clamp(prefs.ShadowSpread, 0, 100);
            SizeSlider.Value = Math.Clamp(prefs.ShadowSize, 0, SizeSlider.Maximum);
            LayersBox.Text = Math.Clamp(prefs.ShadowLayers, 1, BackgroundShadow.MaxLayers)
                .ToString(CultureInfo.CurrentCulture);
            SetAngle(prefs.ShadowAngle, false);
        }

        /// <summary>
        /// The shadow settings in container units; <paramref name="options"/> is
        /// null when the box is unchecked. Returns false (after telling the user)
        /// when the Layers box does not hold a usable number.
        /// </summary>
        internal bool TryGetOptions(string dialogTitle, out ShadowOptions options)
        {
            options = null;
            if (ShadowCheck.IsChecked != true)
                return true;
            if ((!double.TryParse(LayersBox.Text, NumberStyles.Float, CultureInfo.CurrentCulture, out double layers) &&
                 !double.TryParse(LayersBox.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out layers)) ||
                layers < 1 || layers > BackgroundShadow.MaxLayers)
            {
                MessageBox.Show($"Layers must be a number between 1 and {BackgroundShadow.MaxLayers}.", dialogTitle);
                return false;
            }
            if (ShadowColor.SelectedColor is CIMRGBColor picked)
                _color = picked;
            // points -> page units on a layout; map units pass through on a map
            double toContainer = _isLayout ? (0.0254 / 72.0) / _metersPerPageUnit : 1.0;
            options = new ShadowOptions
            {
                Enabled = true,
                R = _color.R, G = _color.G, B = _color.B,
                OpacityPct = OpacitySlider.Value,
                AngleDeg = _angle,
                Distance = DistanceSlider.Value * toContainer,
                SpreadPct = SpreadSlider.Value,
                Size = SizeSlider.Value * toContainer,
                Layers = (int)Math.Round(layers),
            };
            return true;
        }

        /// <summary>Remember the settings for the next run in this project.</summary>
        public void Save(BackgroundToolPrefs prefs)
        {
            if (prefs == null)
                return;
            if (ShadowColor.SelectedColor is CIMRGBColor picked)
                _color = picked;
            prefs.ShadowEnabled = ShadowCheck.IsChecked == true;
            prefs.ShadowR = _color.R;
            prefs.ShadowG = _color.G;
            prefs.ShadowB = _color.B;
            prefs.ShadowOpacity = OpacitySlider.Value;
            prefs.ShadowAngle = _angle;
            prefs.ShadowDistance = DistanceSlider.Value;
            prefs.ShadowSpread = SpreadSlider.Value;
            prefs.ShadowSize = SizeSlider.Value;
            if (double.TryParse(LayersBox.Text, NumberStyles.Float, CultureInfo.CurrentCulture, out double layers) ||
                double.TryParse(LayersBox.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out layers))
                prefs.ShadowLayers = (int)Math.Clamp(Math.Round(layers), 1, BackgroundShadow.MaxLayers);
        }

        // ---- the angle dial ---------------------------------------------------
        /// <summary>Set the light angle; redraw the needle and (unless the text
        /// box is the source) rewrite the box. Kept in (-180, 180].</summary>
        private void SetAngle(double deg, bool fromText)
        {
            if (double.IsNaN(deg) || double.IsInfinity(deg))
                return;
            deg %= 360.0;
            if (deg > 180.0) deg -= 360.0;
            if (deg <= -180.0) deg += 360.0;
            _angle = deg;
            double a = deg * Math.PI / 180.0;
            AngleNeedle.X2 = 20.0 + 17.0 * Math.Cos(a);
            AngleNeedle.Y2 = 20.0 - 17.0 * Math.Sin(a);     // screen y runs downward
            if (!fromText)
            {
                _angleSync = true;
                try { AngleBox.Text = Math.Round(deg).ToString(CultureInfo.CurrentCulture); }
                finally { _angleSync = false; }
            }
        }

        private void DialFromMouse(MouseEventArgs e)
        {
            Point p = e.GetPosition(AngleDial);
            double dx = p.X - 20.0, dy = 20.0 - p.Y;
            if (dx == 0 && dy == 0)
                return;
            SetAngle(Math.Round(Math.Atan2(dy, dx) * 180.0 / Math.PI), false);
        }

        private void OnDialDown(object sender, MouseButtonEventArgs e)
        {
            AngleDial.CaptureMouse();
            DialFromMouse(e);
        }

        private void OnDialMove(object sender, MouseEventArgs e)
        {
            if (AngleDial.IsMouseCaptured && e.LeftButton == MouseButtonState.Pressed)
                DialFromMouse(e);
        }

        private void OnDialUp(object sender, MouseButtonEventArgs e) => AngleDial.ReleaseMouseCapture();

        private void OnAngleText(object sender, TextChangedEventArgs e)
        {
            if (_angleSync || AngleNeedle == null)
                return;
            if (double.TryParse(AngleBox.Text, NumberStyles.Float, CultureInfo.CurrentCulture, out double v) ||
                double.TryParse(AngleBox.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out v))
                SetAngle(v, true);
        }

        // ---- the color picker -------------------------------------------------
        // Esri's ColorPickerControl draws its swatch as a bitmap sized to the
        // control and skips the first build from 0x0, so a color applied before
        // layout leaves the swatch blank. Cycle through a sentinel color and back
        // once layout has finished (the Polar Plots window's recipe).
        private void OnShadowPickerLoaded(object sender, RoutedEventArgs e)
        {
            Dispatcher.BeginInvoke(new Action(() =>
            {
                ShadowColor.SelectedColor = new CIMRGBColor { R = 1, G = 2, B = 3, Alpha = 50 };
                ShadowColor.SelectedColor = _color;
            }), System.Windows.Threading.DispatcherPriority.Loaded);
        }
    }
}
