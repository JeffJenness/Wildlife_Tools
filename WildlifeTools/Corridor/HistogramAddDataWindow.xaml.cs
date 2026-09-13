using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using ArcGIS.Core.CIM;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using WildlifeTools.PolarPlots;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// The histogram tool's Add Data dialog: pick a raster layer, feature
    /// layer or standalone table (and a numeric field), choose all/selected
    /// records, geodesic size weighting, an optional clip polygon, and --
    /// for rasters read by pixel -- a sampling stride. Returns a validated
    /// AddRequest; the main window performs the (possibly long) read.
    /// </summary>
    public partial class HistogramAddDataWindow
        : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private const string NumericCells = "(numeric cell values)";
        private List<PolarPlotData.Source> _sources = new List<PolarPlotData.Source>();
        private List<FeatureLayer> _clipLayers = new List<FeatureLayer>();
        internal HistogramData.AddRequest Result;   // null = cancelled

        private sealed class StrideChoice
        {
            public int Stride;
            public string Label;
            public override string ToString() => Label;
        }

        internal HistogramAddDataWindow(Layer preselect = null)
        {
            InitializeComponent();
            cboStride.ItemsSource = new[]
            {
                new StrideChoice { Stride = 1, Label = "Every cell (exact -- default)" },
                new StrideChoice { Stride = 2, Label = "Every 2nd row and column (1/4 of cells)" },
                new StrideChoice { Stride = 4, Label = "Every 4th row and column (1/16 of cells)" },
                new StrideChoice { Stride = 8, Label = "Every 8th row and column (1/64 of cells)" },
            };
            cboStride.SelectedIndex = 0;
            Loaded += async (_, __) =>
            {
                var map = MapView.Active?.Map;
                var sources = await QueuedTask.Run(() =>
                    PolarPlotData.ListSources(map));
                _sources = sources;
                cboSource.ItemsSource = _sources;
                _clipLayers = map == null ? new List<FeatureLayer>()
                    : map.GetLayersAsFlattenedList().OfType<FeatureLayer>()
                        .Where(l => l.ShapeType ==
                            esriGeometryType.esriGeometryPolygon).ToList();
                var clipItems = new List<object> { "(no clip)" };
                clipItems.AddRange(_clipLayers);
                cboClip.ItemsSource = clipItems;
                cboClip.SelectedIndex = 0;
                if (preselect != null)
                {
                    var hit = _sources.FirstOrDefault(s =>
                        ReferenceEquals(s.FeatureLayer, preselect) ||
                        ReferenceEquals(s.RasterLayer, preselect));
                    if (hit != null) cboSource.SelectedItem = hit;
                }
                else if (_sources.Count > 0)
                {
                    cboSource.SelectedIndex = 0;
                }
            };
        }

        private async void OnSourceChanged(object sender,
                                           SelectionChangedEventArgs e)
        {
            var src = cboSource.SelectedItem as PolarPlotData.Source;
            if (src == null) return;
            bool raster = src.IsRaster;
            pnlField.Visibility = raster ? Visibility.Collapsed
                                         : Visibility.Visible;
            pnlClass.Visibility = raster ? Visibility.Visible
                                         : Visibility.Collapsed;
            pnlRecords.Visibility = pnlField.Visibility;
            expSample.Visibility = raster ? Visibility.Visible
                                          : Visibility.Collapsed;
            bool weightable = src.FeatureLayer != null &&
                (src.FeatureLayer.ShapeType ==
                     esriGeometryType.esriGeometryPolygon ||
                 src.FeatureLayer.ShapeType ==
                     esriGeometryType.esriGeometryPolyline);
            chkWeight.Visibility = weightable ? Visibility.Visible
                                              : Visibility.Collapsed;
            txtName.Text = src.Name;
            if (!raster)
            {
                var fields = await QueuedTask.Run(() =>
                    HistogramData.ValueFields(src));
                cboField.ItemsSource = fields;
                if (fields.Count > 0) cboField.SelectedIndex = 0;
            }
            else
            {
                // numeric cell values, or the layer's symbology labels /
                // a VAT text column for a categorical raster
                var items = new List<string> { NumericCells };
                items.AddRange(await QueuedTask.Run(() =>
                    HistogramData.RasterClassChoices(src)));
                cboClass.ItemsSource = items;
                cboClass.SelectedIndex = 0;
                cboClass.IsEnabled = items.Count > 1;
            }
        }

        private void OnOk(object sender, RoutedEventArgs e)
        {
            var src = cboSource.SelectedItem as PolarPlotData.Source;
            if (src == null)
            {
                MessageBoxText("Choose a layer or table first.");
                return;
            }
            if (!src.IsRaster && cboField.SelectedItem == null)
            {
                MessageBoxText("Choose a value field.");
                return;
            }
            string cls = cboClass.SelectedItem as string;
            Result = new HistogramData.AddRequest
            {
                Source = src,
                Field = (cboField.SelectedItem as HistogramData.FieldChoice)?.Name,
                ClassField = src.IsRaster && cls != null && cls != NumericCells
                             ? cls : null,
                SelectedOnly = rbSelected.IsChecked == true,
                WeightBySize = chkWeight.Visibility == Visibility.Visible
                               && chkWeight.IsChecked == true,
                SampleStride = (cboStride.SelectedItem as StrideChoice)
                               ?.Stride ?? 1,
            };
            ClipLayer = cboClip.SelectedItem as FeatureLayer;
            RequestedName = txtName.Text?.Trim();
            DialogResult = true;
            Close();
        }

        /// <summary>The chosen clip layer (null = none); the main window
        /// unions its polygons on the MCT before the read.</summary>
        internal FeatureLayer ClipLayer { get; private set; }
        internal string RequestedName { get; private set; }

        private void OnCancel(object sender, RoutedEventArgs e)
        {
            DialogResult = false;
            Close();
        }

        private void MessageBoxText(string t) =>
            ArcGIS.Desktop.Framework.Dialogs.MessageBox.Show(t,
                "Add Data to Histogram");
    }
}
