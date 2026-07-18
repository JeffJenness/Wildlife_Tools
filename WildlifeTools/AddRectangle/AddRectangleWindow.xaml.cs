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

        /// <summary>The collected options, valid after the dialog returns true.</summary>
        internal AddRectangleOptions Result { get; private set; }

        /// <param name="defaultSymbol">The starting polygon symbol reference.</param>
        /// <param name="prefs">Last-used parameters (persisted with the project);
        /// initialized from and written back to on OK. The name is never persisted.</param>
        public AddRectangleWindow(CIMSymbolReference defaultSymbol, BackgroundToolPrefs prefs)
        {
            InitializeComponent();
            _symbol = defaultSymbol;
            _prefs = prefs ?? new BackgroundToolPrefs();
            FillTranspBox.Text = _prefs.FillTransparency.ToString(CultureInfo.CurrentCulture);
            OutlineTranspBox.Text = _prefs.OutlineTransparency.ToString(CultureInfo.CurrentCulture);
            HorizExtentBox.Text = _prefs.HorizontalExtentPct.ToString(CultureInfo.CurrentCulture);
            VertExtentBox.Text = _prefs.VerticalExtentPct.ToString(CultureInfo.CurrentCulture);
            RoundingBox.Text = _prefs.RoundingPct.ToString(CultureInfo.CurrentCulture);
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
            if (!TryParseNumber(FillTranspBox.Text, "Fill transparency", 0, 100, out double fillT) ||
                !TryParseNumber(OutlineTranspBox.Text, "Outline transparency", 0, 100, out double outT) ||
                !TryParseNumber(HorizExtentBox.Text, "Horizontal extent", 0.01, 100000, out double horiz) ||
                !TryParseNumber(VertExtentBox.Text, "Vertical extent", 0.01, 100000, out double vert) ||
                !TryParseNumber(RoundingBox.Text, "Rounding", 0, 100, out double rounding))
                return;

            Result = new AddRectangleOptions
            {
                Name = NameBox.Text,
                SymbolReference = _symbol,
                FillTransparency = fillT,
                OutlineTransparency = outT,
                HorizontalExtentPct = horiz,
                VerticalExtentPct = vert,
                RoundingPct = rounding,     // values above 50 are capped in the operation
            };

            // Remember the choices (not the name) for the next run in this project.
            _prefs.FillTransparency = fillT;
            _prefs.OutlineTransparency = outT;
            _prefs.HorizontalExtentPct = horiz;
            _prefs.VerticalExtentPct = vert;
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
