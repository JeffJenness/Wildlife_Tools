using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using ArcGIS.Core.CIM;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using WildlifeTools.PolarPlots;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// The Cross-Tab input dialog: single data source (one table / feature
    /// class, two numeric fields, weighting choice) or two data sources
    /// (polygon layers and/or rasters, with the raster-statistic choice
    /// for the polygon x raster case), plus the optional analysis-area
    /// polygon. Validates and returns a CrosstabDataReader.Request; the
    /// main window performs the read.
    /// </summary>
    public partial class CrosstabInputWindow
        : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private List<PolarPlotData.Source> _tableSources =
            new List<PolarPlotData.Source>();
        private List<PolarPlotData.Source> _twoSources =
            new List<PolarPlotData.Source>();
        private List<FeatureLayer> _aoiLayers = new List<FeatureLayer>();
        internal CrosstabDataReader.Request Result;
        internal FeatureLayer AoiLayer;

        private sealed class StatChoice
        {
            public CrosstabDataReader.RasterStatKind Kind;
            public override string ToString() =>
                CrosstabDataReader.StatWord(Kind);
        }

        public CrosstabInputWindow()
        {
            InitializeComponent();
            JennessIcon.Apply(this);
            cboStat.ItemsSource = new[]
            {
                CrosstabDataReader.RasterStatKind.CellValues,
                CrosstabDataReader.RasterStatKind.Min,
                CrosstabDataReader.RasterStatKind.Mean,
                CrosstabDataReader.RasterStatKind.Max,
                CrosstabDataReader.RasterStatKind.Range,
                CrosstabDataReader.RasterStatKind.StdDev,
                CrosstabDataReader.RasterStatKind.Sum,
            }.Select(k => new StatChoice { Kind = k }).ToList();
            cboStat.SelectedIndex = 0;
            Loaded += async (_, __) =>
            {
                var map = MapView.Active?.Map;
                var sources = await QueuedTask.Run(() =>
                    PolarPlotData.ListSources(map));
                // single source: feature layers + standalone tables
                _tableSources = sources.Where(s => !s.IsRaster).ToList();
                cboSource.ItemsSource = _tableSources;
                if (_tableSources.Count > 0) cboSource.SelectedIndex = 0;
                // two sources: polygon layers + rasters
                _twoSources = sources.Where(s => s.IsRaster ||
                    (s.FeatureLayer != null && s.FeatureLayer.ShapeType ==
                     esriGeometryType.esriGeometryPolygon)).ToList();
                cboSourceA.ItemsSource = _twoSources;
                cboSourceB.ItemsSource = _twoSources.ToList();
                if (_twoSources.Count > 0) cboSourceA.SelectedIndex = 0;
                if (_twoSources.Count > 1) cboSourceB.SelectedIndex = 1;
                else if (_twoSources.Count > 0) cboSourceB.SelectedIndex = 0;
                _aoiLayers = map == null ? new List<FeatureLayer>()
                    : map.GetLayersAsFlattenedList().OfType<FeatureLayer>()
                        .Where(l => l.ShapeType ==
                            esriGeometryType.esriGeometryPolygon).ToList();
                var aoiItems = new List<object> { "(no restriction)" };
                aoiItems.AddRange(_aoiLayers);
                cboAoi.ItemsSource = aoiItems;
                cboAoi.SelectedIndex = 0;
            };
        }

        private void OnModeChanged(object sender, RoutedEventArgs e)
        {
            if (pnlSingle == null) return;
            bool single = rbSingle.IsChecked == true;
            pnlSingle.Visibility = single ? Visibility.Visible
                                          : Visibility.Collapsed;
            pnlTwo.Visibility = single ? Visibility.Collapsed
                                       : Visibility.Visible;
        }

        private void OnWeightChanged(object sender, RoutedEventArgs e)
        {
            if (cboWeightField != null)
                cboWeightField.IsEnabled = rbWeightField.IsChecked == true;
        }

        private async void OnSourceChanged(object sender,
                                           SelectionChangedEventArgs e)
        {
            var src = cboSource.SelectedItem as PolarPlotData.Source;
            if (src == null) return;
            var fields = await QueuedTask.Run(() =>
                HistogramData.NumericFields(src));
            cboField1.ItemsSource = fields;
            cboField2.ItemsSource = fields.ToList();
            cboWeightField.ItemsSource = fields.ToList();
            if (fields.Count > 0) cboField1.SelectedIndex = 0;
            if (fields.Count > 1) cboField2.SelectedIndex = 1;
            else if (fields.Count > 0) cboField2.SelectedIndex = 0;
            if (fields.Count > 0) cboWeightField.SelectedIndex = 0;
            bool isFc = src.FeatureLayer != null;
            rbWeightSize.IsEnabled = isFc && src.FeatureLayer.ShapeType !=
                esriGeometryType.esriGeometryPoint;
            if (!rbWeightSize.IsEnabled && rbWeightSize.IsChecked == true)
                rbWeightNone.IsChecked = true;
        }

        private async void OnTwoSourceChanged(object sender,
                                              SelectionChangedEventArgs e)
        {
            var a = cboSourceA.SelectedItem as PolarPlotData.Source;
            var b = cboSourceB.SelectedItem as PolarPlotData.Source;
            if (ReferenceEquals(sender, cboSourceA))
                await FillTwoFields(a, cboFieldA);
            else if (ReferenceEquals(sender, cboSourceB))
                await FillTwoFields(b, cboFieldB);
            // the raster-statistic applies only to polygon x raster
            bool oneEach = a != null && b != null &&
                           a.IsRaster != b.IsRaster;
            pnlStat.Visibility = oneEach ? Visibility.Visible
                                         : Visibility.Collapsed;
        }

        private async System.Threading.Tasks.Task FillTwoFields(
            PolarPlotData.Source src, ComboBox fieldCombo)
        {
            if (src == null) return;
            if (src.IsRaster)
            {
                fieldCombo.ItemsSource = new[] { "(raster cell values)" };
                fieldCombo.SelectedIndex = 0;
                fieldCombo.IsEnabled = false;
                return;
            }
            var fields = await QueuedTask.Run(() =>
                HistogramData.NumericFields(src));
            fieldCombo.ItemsSource = fields;
            fieldCombo.IsEnabled = true;
            if (fields.Count > 0) fieldCombo.SelectedIndex = 0;
        }

        private void OnOk(object sender, RoutedEventArgs e)
        {
            var req = new CrosstabDataReader.Request();
            if (rbSingle.IsChecked == true)
            {
                req.Mode = CrosstabDataReader.Mode.SingleSource;
                req.Source = cboSource.SelectedItem as PolarPlotData.Source;
                req.Field1 = cboField1.SelectedItem as string;
                req.Field2 = cboField2.SelectedItem as string;
                if (req.Source == null || req.Field1 == null ||
                    req.Field2 == null)
                {
                    MessageBox.Show("Choose the dataset and both variable "
                        + "fields.", Title);
                    return;
                }
                req.SelectedOnly = chkSelected.IsChecked == true;
                if (rbWeightField.IsChecked == true)
                {
                    req.Weight = CrosstabDataReader.WeightMode.ByField;
                    req.WeightField = cboWeightField.SelectedItem as string;
                    if (req.WeightField == null)
                    {
                        MessageBox.Show("Choose the weighting field.", Title);
                        return;
                    }
                }
                else
                {
                    req.Weight = rbWeightSize.IsChecked == true
                        ? CrosstabDataReader.WeightMode.BySize
                        : CrosstabDataReader.WeightMode.None;
                }
            }
            else
            {
                req.Mode = CrosstabDataReader.Mode.TwoDatasets;
                req.SourceA = cboSourceA.SelectedItem as PolarPlotData.Source;
                req.SourceB = cboSourceB.SelectedItem as PolarPlotData.Source;
                if (req.SourceA == null || req.SourceB == null)
                {
                    MessageBox.Show("Choose both datasets.", Title);
                    return;
                }
                if (ReferenceEquals(req.SourceA, req.SourceB))
                {
                    MessageBox.Show("Choose two DIFFERENT datasets (use the "
                        + "Single data source option to cross-tabulate two "
                        + "fields of one dataset).", Title);
                    return;
                }
                if (!req.SourceA.IsRaster)
                {
                    req.FieldA = cboFieldA.SelectedItem as string;
                    if (req.FieldA == null)
                    {
                        MessageBox.Show("Choose the Variable #1 attribute "
                            + "field.", Title);
                        return;
                    }
                }
                if (!req.SourceB.IsRaster)
                {
                    req.FieldB = cboFieldB.SelectedItem as string;
                    if (req.FieldB == null)
                    {
                        MessageBox.Show("Choose the Variable #2 attribute "
                            + "field.", Title);
                        return;
                    }
                }
                req.RasterStat = req.SourceA.IsRaster == req.SourceB.IsRaster
                    ? CrosstabDataReader.RasterStatKind.CellValues
                    : (cboStat.SelectedItem as StatChoice)?.Kind
                      ?? CrosstabDataReader.RasterStatKind.CellValues;
            }
            AoiLayer = cboAoi.SelectedItem as FeatureLayer;
            Result = req;
            DialogResult = true;
            Close();
        }

        private void OnCancel(object sender, RoutedEventArgs e)
        {
            DialogResult = false;
            Close();
        }
    }
}
