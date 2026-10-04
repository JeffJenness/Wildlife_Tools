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
    /// Parameter dialog for the Polygon Background tool: optional name, the Format
    /// Polygon Symbol editor with live preview, fill / outline transparencies, the
    /// shape source (element geometry vs. symbolized shape), and an optional buffer.
    /// On a map the buffer is in the map coordinate system's units (shown as a label);
    /// on a layout the user picks millimeters / centimeters / inches / points and the
    /// value is converted to page units.
    /// </summary>
    public partial class AddPolygonWindow : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        /// <summary>(label, meters per unit) choices for the layout buffer units.</summary>
        private static readonly (string Label, double MetersPerUnit)[] LayoutUnits =
        {
            ("Millimeters", 0.001),
            ("Centimeters", 0.01),
            ("Inches", 0.0254),
            ("Points", 0.0254 / 72.0),
        };

        private readonly bool _isLayout;
        private readonly double _metersPerPageUnit;
        private readonly BackgroundToolPrefs _prefs;
        private CIMSymbolReference _symbol;

        /// <summary>The collected options, valid after the dialog returns true.</summary>
        internal AddPolygonOptions Result { get; private set; }

        /// <param name="defaultSymbol">The starting polygon symbol reference.</param>
        /// <param name="isLayout">True on a layout (unit dropdown); false on a map (unit label).</param>
        /// <param name="mapUnitName">Map only: the coordinate system's unit name.</param>
        /// <param name="metersPerPageUnit">Layout only: meters per page unit.</param>
        /// <param name="prefs">Last-used parameters (persisted with the project);
        /// initialized from and written back to on OK. The name is never persisted.</param>
        public AddPolygonWindow(CIMSymbolReference defaultSymbol, bool isLayout,
            string mapUnitName, double metersPerPageUnit, BackgroundToolPrefs prefs)
        {
            InitializeComponent();
            _symbol = defaultSymbol;
            _isLayout = isLayout;
            _metersPerPageUnit = metersPerPageUnit > 0 ? metersPerPageUnit : 0.0254;
            _prefs = prefs ?? new BackgroundToolPrefs();

            FillTranspBox.Text = _prefs.FillTransparency.ToString(CultureInfo.CurrentCulture);
            OutlineTranspBox.Text = _prefs.OutlineTransparency.ToString(CultureInfo.CurrentCulture);
            BufferBox.Text = _prefs.BufferValue.ToString(CultureInfo.CurrentCulture);
            SymbolizedRadio.IsChecked = _prefs.UseSymbolizedShape;
            SourceGeometryRadio.IsChecked = !_prefs.UseSymbolizedShape;
            RemoveHolesCheck.IsChecked = _prefs.RemoveHoles;

            if (isLayout)
            {
                foreach (var (label, _) in LayoutUnits)
                    UnitCombo.Items.Add(label);
                UnitCombo.SelectedIndex = Math.Clamp(_prefs.LayoutUnitIndex, 0, LayoutUnits.Length - 1);
                UnitCombo.Visibility = Visibility.Visible;
            }
            else
            {
                MapUnitLabel.Text = string.IsNullOrEmpty(mapUnitName)
                    ? "map units" : mapUnitName;
                MapUnitLabel.Visibility = Visibility.Visible;
            }
            UpdateSwatch();
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
                    "Polygon Background");
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

        private void OnHelp(object sender, RoutedEventArgs e)
            => WebHelp.Open("cartography/polygon-background.html");

        private void OnOk(object sender, RoutedEventArgs e)
        {
            if (!TryParseNumber(FillTranspBox.Text, "Fill transparency", 0, 100, out double fillT) ||
                !TryParseNumber(OutlineTranspBox.Text, "Outline transparency", 0, 100, out double outT) ||
                !TryParseNumber(BufferBox.Text, "Buffer size", -1e9, 1e9, out double buffer))
                return;

            // Convert a layout buffer from the chosen unit to page units.
            double distance = buffer;
            if (_isLayout && buffer != 0)
            {
                int i = Math.Max(0, UnitCombo.SelectedIndex);
                distance = buffer * LayoutUnits[i].MetersPerUnit / _metersPerPageUnit;
            }

            Result = new AddPolygonOptions
            {
                Name = NameBox.Text,
                SymbolReference = _symbol,
                FillTransparency = fillT,
                OutlineTransparency = outT,
                UseSymbolizedShape = SymbolizedRadio.IsChecked == true,
                BufferDistance = distance,
                RemoveHoles = RemoveHolesCheck.IsChecked == true,
            };

            // Remember the choices (not the name) for the next run in this project.
            _prefs.FillTransparency = fillT;
            _prefs.OutlineTransparency = outT;
            _prefs.UseSymbolizedShape = SymbolizedRadio.IsChecked == true;
            _prefs.RemoveHoles = RemoveHolesCheck.IsChecked == true;
            _prefs.BufferValue = buffer;
            if (_isLayout && UnitCombo.SelectedIndex >= 0)
                _prefs.LayoutUnitIndex = UnitCombo.SelectedIndex;

            DialogResult = true;
        }

        private void OnCancel(object sender, RoutedEventArgs e) => DialogResult = false;

        private static bool TryParseNumber(string text, string label, double min, double max, out double value)
        {
            if (!double.TryParse(text, NumberStyles.Float, CultureInfo.CurrentCulture, out value) &&
                !double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out value))
            {
                MessageBox.Show($"{label} must be a number.", "Polygon Background");
                return false;
            }
            if (value < min || value > max)
            {
                MessageBox.Show($"{label} must be between {min} and {max}.", "Polygon Background");
                return false;
            }
            return true;
        }
    }
}
