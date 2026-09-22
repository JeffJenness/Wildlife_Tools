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
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Internal.Mapping;
using System;
using System.Globalization;
using System.Windows;
using System.Windows.Media;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools
{
    /// <summary>
    /// Parameter dialog for the Rectangle Background tool: an optional element name,
    /// the Format Polygon Symbol editor (<see cref="SymbolEditorWindow"/>) with a live
    /// symbol preview swatch, separate fill / outline transparencies, and the
    /// rectangle's horizontal extent, vertical extent, and corner rounding (all in
    /// percent). No focal-point option — the rectangle always centers on the
    /// selection's extent center.
    /// </summary>
    public partial class AddRectangleWindow : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private CIMSymbolReference _symbol;
        private readonly BackgroundToolPrefs _prefs;
        private readonly bool _isLayout;
        private readonly double _metersPerPageUnit;

        /// <summary>The collected options, valid after the dialog returns true.</summary>
        internal AddRectangleOptions Result { get; private set; }

        /// <param name="defaultSymbol">The starting polygon symbol reference.</param>
        /// <param name="isLayout">True on a layout (page units offered); false on a
        /// map (the map unit offered).</param>
        /// <param name="mapUnitName">Map only: the coordinate system's unit name.</param>
        /// <param name="metersPerPageUnit">Layout only: meters per page unit.</param>
        /// <param name="prefs">Last-used parameters (persisted with the project);
        /// initialized from and written back to on OK. The name is never persisted.</param>
        public AddRectangleWindow(CIMSymbolReference defaultSymbol, bool isLayout,
            string mapUnitName, double metersPerPageUnit, BackgroundToolPrefs prefs)
        {
            InitializeComponent();
            _symbol = defaultSymbol;
            _prefs = prefs ?? new BackgroundToolPrefs();
            _isLayout = isLayout;
            _metersPerPageUnit = metersPerPageUnit > 0 ? metersPerPageUnit : 0.0254;
            FillTranspBox.Text = _prefs.FillTransparency.ToString(CultureInfo.CurrentCulture);
            OutlineTranspBox.Text = _prefs.OutlineTransparency.ToString(CultureInfo.CurrentCulture);
            foreach (string label in BackgroundUnits.Choices(isLayout, mapUnitName))
                UnitCombo.Items.Add(label);
            UnitCombo.SelectedIndex = Math.Clamp(_prefs.SizeUnitIndex, 0, UnitCombo.Items.Count - 1);
            HorizExtentBox.Text = _prefs.HorizontalExtentPct.ToString(CultureInfo.CurrentCulture);
            VertExtentBox.Text = _prefs.VerticalExtentPct.ToString(CultureInfo.CurrentCulture);
            RoundingBox.Text = _prefs.RoundingPct.ToString(CultureInfo.CurrentCulture);
            UpdateSwatch();
        }

        /// <summary>Relabel the two size rows for the chosen unit, and swap their
        /// values between the percent default (100) and the margin default (0) when
        /// the user crosses from one kind of unit to the other.</summary>
        private void OnUnitChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e)
        {
            if (HorizLabel == null || VertLabel == null || HorizExtentBox == null || VertExtentBox == null)
                return;
            bool linear = BackgroundUnits.IsLinear(UnitCombo.SelectedIndex);
            HorizLabel.Text = linear ? "Horizontal margin:" : "Horizontal extent (%):";
            VertLabel.Text = linear ? "Vertical margin:" : "Vertical extent (%):";
            bool wasLinear = e.RemovedItems.Count > 0 &&
                             !string.Equals(e.RemovedItems[0] as string, BackgroundUnits.PercentLabel);
            if (e.RemovedItems.Count > 0 && wasLinear != linear)
                HorizExtentBox.Text = VertExtentBox.Text = linear ? "0" : "100";
        }

        private void OnDesignSymbology(object sender, RoutedEventArgs e)
        {
            try
            {
                var win = new SymbolEditorWindow(_symbol)
                {
                    Owner = FrameworkApplication.Current.MainWindow,
                };
                if (win.ShowDialog() == true && win.Result != null)
                {
                    _symbol = win.Result;
                    UpdateSwatch();
                }
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "The symbol editor could not be opened:" + Environment.NewLine + ex.Message,
                    "Rectangle Background");
            }
        }

        /// <summary>Renders a true preview of the chosen symbol into the swatch (any
        /// fill type) with Esri's preview renderer; must run on the MCT.</summary>
        private async void UpdateSwatch()
        {
            CIMSymbol sym = _symbol?.Symbol;
            if (sym == null)
                return;
            try
            {
                const int w = 92, h = 48;   // 2x the 46x24 swatch, for crisp downscaling
                ImageSource img = await QueuedTask.Run(() =>
                {
                    Envelope env = EnvelopeBuilderEx.CreateEnvelope(0, 0, w, h);
                    Polygon patch = PolygonBuilderEx.CreatePolygon(env);
                    return MappingHelpers.GetImageSourceFromGeometry(patch, sym, h, w);
                });
                SwatchImage.Source = img;
            }
            catch
            {
                // Leave the swatch blank if preview rendering fails.
            }
        }

        private void OnOk(object sender, RoutedEventArgs e)
        {
            bool linear = BackgroundUnits.IsLinear(UnitCombo.SelectedIndex);
            double lo = linear ? -1e9 : 0.01, hi = linear ? 1e9 : 100000;
            if (!TryParseNumber(FillTranspBox.Text, "Fill transparency", 0, 100, out double fillT) ||
                !TryParseNumber(OutlineTranspBox.Text, "Outline transparency", 0, 100, out double outT) ||
                !TryParseNumber(HorizExtentBox.Text, linear ? "Horizontal margin" : "Horizontal extent", lo, hi, out double horiz) ||
                !TryParseNumber(VertExtentBox.Text, linear ? "Vertical margin" : "Vertical extent", lo, hi, out double vert) ||
                !TryParseNumber(RoundingBox.Text, "Rounding", 0, 100, out double rounding))
                return;

            int unit = Math.Max(0, UnitCombo.SelectedIndex);
            Result = new AddRectangleOptions
            {
                Name = NameBox.Text,
                SymbolReference = _symbol,
                FillTransparency = fillT,
                OutlineTransparency = outT,
                HorizontalExtentPct = horiz,
                VerticalExtentPct = vert,
                UseMargins = linear,
                HorizontalMargin = BackgroundUnits.ToContainerUnits(horiz, unit, _isLayout, _metersPerPageUnit),
                VerticalMargin = BackgroundUnits.ToContainerUnits(vert, unit, _isLayout, _metersPerPageUnit),
                RoundingPct = rounding,     // values above 50 are capped in the operation
            };

            // Remember the choices (not the name) for the next run in this project.
            _prefs.FillTransparency = fillT;
            _prefs.OutlineTransparency = outT;
            _prefs.HorizontalExtentPct = horiz;
            _prefs.VerticalExtentPct = vert;
            _prefs.SizeUnitIndex = unit;
            _prefs.RoundingPct = rounding;

            DialogResult = true;
        }

        private void OnCancel(object sender, RoutedEventArgs e) => DialogResult = false;

        private static bool TryParseNumber(string text, string label, double min, double max, out double value)
        {
            if (!double.TryParse(text, NumberStyles.Float, CultureInfo.CurrentCulture, out value) &&
                !double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out value))
            {
                MessageBox.Show($"{label} must be a number.", "Rectangle Background");
                return false;
            }
            if (value < min || value > max)
            {
                MessageBox.Show($"{label} must be between {min} and {max}.", "Rectangle Background");
                return false;
            }
            return true;
        }
    }
}
