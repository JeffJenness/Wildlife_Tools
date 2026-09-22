using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows;
using System.Windows.Documents;
using System.Windows.Media;
using ArcGIS.Desktop.Catalog;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;
using R = WildlifeTools.Corridor.LandFacetRecordReader;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// "Corridor Analysis Data Report": pick a dataset (a raster layer in the
    /// map, or browse to one on disk) and get a human-readable report of where
    /// it stands in the Land Facet Corridor analysis -- its sources, how they
    /// were analyzed, staleness warnings when a recorded source has since been
    /// regenerated, and a you-are-here map of the analysis sequence. Datasets
    /// with no Land Facet record fall back to their Jenness provenance summary,
    /// or to a plain statement that nothing is known.
    ///
    /// Read-only: this window never modifies any dataset. All metadata reads
    /// run on the MCT; the report is built there too (the chain walk follows
    /// recorded source paths with more metadata reads) and only rendered on
    /// the UI thread.
    /// </summary>
    public partial class LandFacetDescribeWindow
        : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private sealed class LayerItem
        {
            public string Name;
            public Layer Layer;                 // raster or polygon feature
                                                // layer; null for a browsed path
            public string Path;
            public override string ToString() => Name;
        }

        private List<R.Block> _blocks;          // last report, for copy/save

        // Stop the MCT chain-walk metadata reads when Pro closes with the
        // window open -- the documented shutdown-race class the sibling
        // windows guard against.
        private bool _closedDown;
        private ArcGIS.Core.Events.SubscriptionToken _appClosingToken;

        public LandFacetDescribeWindow()
        {
            InitializeComponent();
            Loaded += (_, __) =>
            {
                PopulateLayers();
                if (_appClosingToken == null)
                    _appClosingToken = ArcGIS.Desktop.Framework.Events
                        .ApplicationClosingEvent.Subscribe(args =>
                        {
                            _closedDown = true;
                            try { Close(); } catch { }
                            return System.Threading.Tasks.Task.CompletedTask;
                        });
            };
            Closed += (_, __) =>
            {
                _closedDown = true;
                if (_appClosingToken != null)
                {
                    try
                    {
                        ArcGIS.Desktop.Framework.Events
                            .ApplicationClosingEvent
                            .Unsubscribe(_appClosingToken);
                    }
                    catch { }
                    _appClosingToken = null;
                }
            };
        }

        private void PopulateLayers()
        {
            var map = MapView.Active?.Map;
            var items = new List<LayerItem>();
            Layer selected = MapView.Active?.GetSelectedLayers()?.FirstOrDefault();
            LayerItem select = null;
            if (map != null)
            {
                // Rasters carry most of the chain's records; polygon feature
                // layers are listed for the Identify Termini Polygons /
                // corridor outputs, and polyline layers for the Patch
                // Analysis connector output (its record was unreachable
                // from this dropdown until 2026-08-16).
                foreach (var l in map.GetLayersAsFlattenedList())
                {
                    bool eligible = l is BasicRasterLayer
                        || (l is FeatureLayer fl
                            && (fl.ShapeType
                                == ArcGIS.Core.CIM.esriGeometryType.esriGeometryPolygon
                                || fl.ShapeType
                                == ArcGIS.Core.CIM.esriGeometryType.esriGeometryPolyline));
                    if (!eligible) continue;
                    var item = new LayerItem { Name = l.Name, Layer = l };
                    items.Add(item);
                    if (ReferenceEquals(l, selected)) select = item;
                }
            }
            cboLayer.ItemsSource = items;
            // Zero-click common case: default to the layer selected in Contents.
            cboLayer.SelectedItem = select ?? items.FirstOrDefault();
            txtStatus.Text = items.Count == 0
                ? "No raster or feature layers in the active map — use Browse."
                : "";
        }

        private void OnWebHelp(object sender, RoutedEventArgs e)
            => WebHelp.Open("corridor-designer-tools/corridor-analysis-data-report.html");

        private void OnBrowse(object sender, RoutedEventArgs e)
        {
            var dlg = new OpenItemDialog
            {
                Title = "Choose a dataset to describe",
                MultiSelect = false,
                // The GP geodataset filter: rasters AND feature classes (the
                // Identify Termini Polygons output is a polygon FC).
                Filter = "esri_browseDialogFilters_gp_geoDatasets",
            };
            if (dlg.ShowDialog() != true || dlg.Items == null) return;
            var item = dlg.Items.FirstOrDefault();
            if (item == null) return;
            var li = new LayerItem { Name = item.Name, Path = item.Path };
            var items = (cboLayer.ItemsSource as List<LayerItem>) ?? new List<LayerItem>();
            items = new List<LayerItem>(items) { li };
            cboLayer.ItemsSource = items;
            cboLayer.SelectedItem = li;
        }

        // ---- metadata fetching (MCT only) -----------------------------------
        private static string XmlForPath(string path)
        {
            try
            {
                if (string.IsNullOrWhiteSpace(path)) return null;
                var item = ItemFactory.Instance.Create(path);
                return (item as IMetadata)?.GetXml();
            }
            catch { return null; }
        }

        private static string XmlForLayer(Layer layer)
        {
            try { return (layer as IMetadataInfo)?.GetMetadata(); }
            catch { return null; }
        }

        private static string PathForLayer(Layer layer)
        {
            try
            {
                string dir = null, name = null;
                if (layer is BasicRasterLayer rl)
                {
                    var ds = rl.GetRaster()?.GetRasterDataset();
                    if (ds == null) return null;
                    dir = ds.GetDatastore()?.GetPath()?.LocalPath;
                    name = ds.GetName();
                }
                else if (layer is FeatureLayer fl)
                {
                    var fc = fl.GetFeatureClass();
                    if (fc == null) return null;
                    dir = fc.GetDatastore()?.GetPath()?.LocalPath;
                    name = fc.GetName();
                }
                if (string.IsNullOrEmpty(dir) || string.IsNullOrEmpty(name))
                    return null;
                string path = System.IO.Path.Combine(dir, name);
                // A shapefile feature class reports its name without the
                // extension; the on-disk item needs it.
                if (!System.IO.Path.HasExtension(path)
                    && File.Exists(path + ".shp"))
                    path += ".shp";
                return path;
            }
            catch { return null; }
        }

        // ---- describe -------------------------------------------------------
        private async void OnDescribe(object sender, RoutedEventArgs e)
        {
            var pick = cboLayer.SelectedItem as LayerItem;
            if (pick == null)
            {
                MessageBox.Show("Choose a raster or polygon layer, or Browse "
                                + "to a dataset.",
                                "Corridor Analysis Data Report");
                return;
            }
            btnDescribe.IsEnabled = false;
            txtStatus.Text = "Reading metadata and following the analysis chain…";
            try
            {
                var blocks = await QueuedTask.Run(() =>
                {
                    string path = pick.Path ?? PathForLayer(pick.Layer);
                    string xml = pick.Layer != null
                        ? (XmlForLayer(pick.Layer) ?? XmlForPath(path))
                        : XmlForPath(path);
                    // The ancestor fetches run right here on the MCT too;
                    // stop following the chain when Pro is tearing down.
                    return R.BuildReport(pick.Name, path, xml,
                        p => _closedDown ? null : XmlForPath(p));
                });
                if (_closedDown) return;
                _blocks = blocks;
                viewer.Document = Render(blocks);
                btnCopyText.IsEnabled = btnCopyRtf.IsEnabled = btnSave.IsEnabled = true;
                int warns = blocks.Count(b => b.Kind == R.BlockKind.Warning);
                txtStatus.Text = warns > 0
                    ? warns + (warns == 1 ? " warning — see the report."
                                          : " warnings — see the report.")
                    : "";
            }
            catch (Exception ex)
            {
                txtStatus.Text = "";
                MessageBox.Show("Could not describe the dataset:"
                                + Environment.NewLine + ex.Message,
                                "Corridor Analysis Data Report");
            }
            finally { btnDescribe.IsEnabled = true; }
        }

        // ---- rendering ------------------------------------------------------
        private static FlowDocument Render(List<R.Block> blocks)
        {
            var doc = new FlowDocument
            {
                FontFamily = new FontFamily("Segoe UI"),
                FontSize = 13,
                PagePadding = new Thickness(10),
                // FlowDocument defaults to Justify, which stretches long paths
                // and prose into big inter-word gaps; ragged-right reads better.
                TextAlignment = TextAlignment.Left,
            };
            var warnBrush = new SolidColorBrush(Color.FromRgb(0xA8, 0x28, 0x10));
            var noteBrush = new SolidColorBrush(Color.FromRgb(0x50, 0x50, 0x50));
            // Ganado Red (Jeff's pick, 2026-08-08): highlights the described
            // dataset's own section heading among the ancestor sections.
            var ganadoBrush = new SolidColorBrush(Color.FromRgb(125, 27, 40));
            foreach (var b in blocks)
            {
                switch (b.Kind)
                {
                    case R.BlockKind.Title:
                        doc.Blocks.Add(new Paragraph(new Bold(new Run(b.Text)))
                        { FontSize = 17, TextAlignment = TextAlignment.Center,
                          Margin = new Thickness(0, 0, 0, 8) });
                        break;
                    case R.BlockKind.Heading:
                        doc.Blocks.Add(new Paragraph(new Bold(new Run(b.Text)))
                        { FontSize = 14.5, Margin = new Thickness(0, 8, 0, 3) });
                        break;
                    case R.BlockKind.HeadingTarget:
                        doc.Blocks.Add(new Paragraph(new Bold(new Run(b.Text)))
                        { FontSize = 14.5, Margin = new Thickness(0, 8, 0, 3),
                          Foreground = ganadoBrush });
                        break;
                    case R.BlockKind.SubHeading:
                        doc.Blocks.Add(new Paragraph(new Bold(new Run(b.Text)))
                        { Margin = new Thickness(0, 5, 0, 1) });
                        break;
                    case R.BlockKind.Body:
                        doc.Blocks.Add(new Paragraph(new Run(b.Text))
                        { Margin = new Thickness(0, 1, 0, 3) });
                        break;
                    case R.BlockKind.Bullet:
                        doc.Blocks.Add(new Paragraph(new Run("•  " + b.Text))
                        { Margin = new Thickness(16, 0, 0, 1) });
                        break;
                    case R.BlockKind.BulletAlert:
                        doc.Blocks.Add(new Paragraph(new Run("•  " + b.Text))
                        { Margin = new Thickness(16, 0, 0, 1),
                          Foreground = warnBrush });
                        break;
                    case R.BlockKind.KeyValue:
                        var kv = new Paragraph { Margin = new Thickness(0, 0, 0, 1) };
                        kv.Inlines.Add(new Bold(new Run(b.Text + ":  ")));
                        kv.Inlines.Add(new Run(b.Value));
                        doc.Blocks.Add(kv);
                        break;
                    case R.BlockKind.Warning:
                        doc.Blocks.Add(new Paragraph(
                            new Bold(new Run("⚠  " + b.Text)))
                        { Foreground = warnBrush,
                          Margin = new Thickness(0, 3, 0, 3) });
                        break;
                    case R.BlockKind.Note:
                        doc.Blocks.Add(new Paragraph(
                            new Italic(new Run(b.Text)))
                        { Foreground = noteBrush,
                          Margin = new Thickness(16, 1, 0, 3) });
                        break;
                    case R.BlockKind.ChainStep:
                        doc.Blocks.Add(new Paragraph(new Run("     " + b.Text))
                        { Margin = new Thickness(8, 0, 0, 0),
                          Foreground = noteBrush });
                        break;
                    case R.BlockKind.ChainStepCurrent:
                        doc.Blocks.Add(new Paragraph(
                            new Bold(new Run("►  " + b.Text)))
                        { Margin = new Thickness(8, 0, 0, 0) });
                        break;
                    case R.BlockKind.Separator:
                        doc.Blocks.Add(new Paragraph
                        { Margin = new Thickness(0, 0, 0, 4),
                          BorderBrush = new SolidColorBrush(
                              Color.FromArgb(45, 0, 0, 0)),
                          BorderThickness = new Thickness(0, 0, 0, 1) });
                        break;
                }
            }
            return doc;
        }

        private string PlainText()
        {
            if (_blocks == null) return "";
            var sb = new StringBuilder();
            foreach (var b in _blocks)
            {
                switch (b.Kind)
                {
                    case R.BlockKind.Title:
                        sb.AppendLine(b.Text).AppendLine(new string('=', b.Text.Length));
                        break;
                    case R.BlockKind.Heading:
                    case R.BlockKind.HeadingTarget:
                        sb.AppendLine().AppendLine(b.Text)
                          .AppendLine(new string('-', b.Text.Length));
                        break;
                    case R.BlockKind.SubHeading: sb.AppendLine(b.Text); break;
                    case R.BlockKind.Body: sb.AppendLine(b.Text); break;
                    case R.BlockKind.Bullet: sb.AppendLine("  * " + b.Text); break;
                    case R.BlockKind.BulletAlert:
                        sb.AppendLine("  * " + b.Text); break;
                    case R.BlockKind.KeyValue:
                        sb.AppendLine(b.Text + ": " + b.Value); break;
                    case R.BlockKind.Warning:
                        sb.AppendLine("!! " + b.Text); break;
                    case R.BlockKind.Note: sb.AppendLine("   (" + b.Text + ")"); break;
                    case R.BlockKind.ChainStep: sb.AppendLine("     " + b.Text); break;
                    case R.BlockKind.ChainStepCurrent:
                        sb.AppendLine(" >>> " + b.Text); break;
                    case R.BlockKind.Separator: sb.AppendLine(); break;
                }
            }
            return sb.ToString();
        }

        // ---- copy / save ----------------------------------------------------
        private void OnCopyText(object sender, RoutedEventArgs e)
        {
            try { Clipboard.SetText(PlainText()); txtStatus.Text = "Report copied as plain text."; }
            catch (Exception ex) { txtStatus.Text = "Copy failed: " + ex.Message; }
        }

        private void OnCopyRtf(object sender, RoutedEventArgs e)
        {
            try
            {
                var range = new TextRange(viewer.Document.ContentStart,
                                          viewer.Document.ContentEnd);
                using var ms = new MemoryStream();
                range.Save(ms, DataFormats.Rtf);
                var data = new DataObject();
                data.SetData(DataFormats.Rtf, Encoding.UTF8.GetString(ms.ToArray()));
                data.SetData(DataFormats.UnicodeText, PlainText());
                Clipboard.SetDataObject(data, true);
                txtStatus.Text = "Report copied with formatting.";
            }
            catch (Exception ex) { txtStatus.Text = "Copy failed: " + ex.Message; }
        }

        private void OnSave(object sender, RoutedEventArgs e)
        {
            var dlg = new Microsoft.Win32.SaveFileDialog
            {
                Filter = "Rich Text (*.rtf)|*.rtf|Plain text (*.txt)|*.txt",
                FileName = "LandFacetDataReport.rtf",
                Title = "Save the report",
                DefaultExt = ".rtf",
                AddExtension = true,
            };
            if (dlg.ShowDialog() != true) return;
            if (string.IsNullOrEmpty(Path.GetExtension(dlg.FileName)))
                dlg.FileName += ".rtf";   // typed without an extension
            try
            {
                if (dlg.FileName.EndsWith(".rtf", StringComparison.OrdinalIgnoreCase))
                {
                    var range = new TextRange(viewer.Document.ContentStart,
                                              viewer.Document.ContentEnd);
                    using var fs = new FileStream(dlg.FileName, FileMode.Create);
                    range.Save(fs, DataFormats.Rtf);
                }
                else
                {
                    File.WriteAllText(dlg.FileName, PlainText(), Encoding.UTF8);
                }
                txtStatus.Text = "Saved: " + dlg.FileName;
            }
            catch (Exception ex)
            {
                MessageBox.Show("Could not save the report:"
                                + Environment.NewLine + ex.Message,
                                "Corridor Analysis Data Report");
            }
        }

        private void OnClose(object sender, RoutedEventArgs e) => Close();
    }
}
