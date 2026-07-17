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
    /// Parameter dialog for the Add Graphic Circle tool: an optional element name, a
    /// button that opens the Format Polygon Symbol editor (<see cref="SymbolEditorWindow"/>),
    /// separate fill / outline transparency values, the circle radius (percent of the
    /// selected extent), and — only when a single element with a stamped focal point is
    /// selected — an option to center the circle on that focal point.
    /// </summary>
    public partial class AddCircleWindow : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private CIMSymbolReference _symbol;

        /// <summary>The collected options, valid after the dialog returns true.</summary>
        internal AddCircleOptions Result { get; private set; }

        /// <param name="showFocalOption">Show the focal-point checkbox (single element
        /// with a focal point selected).</param>
        /// <param name="defaultSymbol">The starting polygon symbol reference.</param>
        public AddCircleWindow(bool showFocalOption, CIMSymbolReference defaultSymbol)
        {
            InitializeComponent();
            _symbol = defaultSymbol;
            FocalCheck.Visibility = showFocalOption ? Visibility.Visible : Visibility.Collapsed;
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
                    "Circle Background");
            }
        }

        /// <summary>
        /// Renders a true preview of the chosen symbol into the swatch — any fill type
        /// (solid, gradient, hatch, picture, marker) — by drawing it on a rectangle
        /// patch with Esri's own preview renderer. The renderer must run on the MCT.
        /// </summary>
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
            if (!TryParsePercent(FillTranspBox.Text, "Fill transparency", 0, 100, out double fillT) ||
                !TryParsePercent(OutlineTranspBox.Text, "Outline transparency", 0, 100, out double outT) ||
                !TryParsePercent(RadiusBox.Text, "Radius", 0.01, 100000, out double radius))
                return;

            Result = new AddCircleOptions
            {
                Name = NameBox.Text,
                SymbolReference = _symbol,
                FillTransparency = fillT,
                OutlineTransparency = outT,
                RadiusPercent = radius,
                UseFocalPoint = FocalCheck.Visibility == Visibility.Visible && FocalCheck.IsChecked == true,
            };
            DialogResult = true;
        }

        private void OnCancel(object sender, RoutedEventArgs e) => DialogResult = false;

        private static bool TryParsePercent(string text, string label, double min, double max, out double value)
        {
            if (!double.TryParse(text, NumberStyles.Float, CultureInfo.CurrentCulture, out value) &&
                !double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out value))
            {
                MessageBox.Show($"{label} must be a number.", "Circle Background");
                return false;
            }
            if (value < min || value > max)
            {
                MessageBox.Show($"{label} must be between {min} and {max}.", "Circle Background");
                return false;
            }
            return true;
        }
    }
}
