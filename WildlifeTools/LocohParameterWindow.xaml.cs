using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;
using WildlifeTools.PolarPlots;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools
{
    /// <summary>
    /// LoCoH Parameter Explorer: the Getz et al. (2007) leveling-off
    /// analysis as an interactive window. Sweep k, a or r across a sequence
    /// of values for a point layer (selection honored) and chart the areas
    /// of the 50 / 75 / 95 percent cumulative isopleths against the
    /// parameter, with the published starting heuristic marked -- the
    /// parameter-selection procedure of the LoCoH literature, ready to run
    /// before committing to the LoCoH geoprocessing tools.
    /// </summary>
    public partial class LocohParameterWindow
        : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private sealed class LayerItem
        {
            public FeatureLayer Layer;
            public override string ToString() => Layer?.Name ?? "";
        }

        private LocohSweepResult _result;
        private PolarPlotModel _model;
        private CancellationTokenSource _cts;
        private bool _closedDown;
        private double _chartWIn = 8.5, _chartHIn = 5.2;
        private ArcGIS.Core.Events.SubscriptionToken _appClosingToken;

        public LocohParameterWindow()
        {
            InitializeComponent();
            Loaded += (_, __) =>
            {
                OnRefreshLayers(null, null);
                // when Pro closes with this window open, stop the sweep and
                // close immediately (standing shutdown rule)
                if (_appClosingToken == null)
                    _appClosingToken = ArcGIS.Desktop.Framework.Events
                        .ApplicationClosingEvent.Subscribe(args =>
                        {
                            _closedDown = true;
                            try { _cts?.Cancel(); } catch { }
                            try { Close(); } catch { }
                            return Task.CompletedTask;
                        });
            };
            Closed += (_, __) =>
            {
                _closedDown = true;
                try { _cts?.Cancel(); } catch { }
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

        // =================================================================
        // layer list
        // =================================================================
        private void OnRefreshLayers(object sender, RoutedEventArgs e)
        {
            var keep = (cboLayer.SelectedItem as LayerItem)?.Layer;
            cboLayer.Items.Clear();
            var map = MapView.Active?.Map;
            if (map == null)
            {
                txtStatus.Text = "No active map.";
                return;
            }
            foreach (var lyr in map.GetLayersAsFlattenedList()
                .OfType<FeatureLayer>()
                .Where(l => l.ShapeType ==
                    ArcGIS.Core.CIM.esriGeometryType.esriGeometryPoint ||
                    l.ShapeType ==
                    ArcGIS.Core.CIM.esriGeometryType.esriGeometryMultipoint))
            {
                var item = new LayerItem { Layer = lyr };
                cboLayer.Items.Add(item);
                if (lyr == keep) cboLayer.SelectedItem = item;
            }
            if (cboLayer.SelectedIndex < 0 && cboLayer.Items.Count > 0)
                cboLayer.SelectedIndex = 0;
        }

        // =================================================================
        // the sweep
        // =================================================================
        private async void OnRun(object sender, RoutedEventArgs e)
        {
            var lyr = (cboLayer.SelectedItem as LayerItem)?.Layer;
            if (lyr == null)
            {
                MessageBox.Show("Pick a point feature layer first.", Title);
                return;
            }
            string method = radA.IsChecked == true ? "a"
                : radR.IsChecked == true ? "r" : "k";
            if (!int.TryParse(txtSteps.Text.Trim(), out int steps))
                steps = 12;
            steps = Math.Max(4, Math.Min(steps, 40));
            txtSteps.Text = steps.ToString(CultureInfo.InvariantCulture);

            _cts = new CancellationTokenSource();
            var ct = _cts.Token;
            SetRunning(true);
            txtStatus.Text = "Reading points...";
            try
            {
                var result = await QueuedTask.Run(() =>
                    LocohSweepEngine.Run(lyr, method, steps,
                        (done, total, label) =>
                        {
                            if (_closedDown) return;
                            Dispatcher.BeginInvoke(new Action(() =>
                            {
                                prgRun.Maximum = total;
                                prgRun.Value = Math.Min(done, total);
                                txtStatus.Text = label;
                            }));
                        }, ct));
                if (_closedDown) return;
                _result = result;
                RebuildChart();
                txtStatus.Text = "Swept " + result.Steps.Count +
                    " values of " + result.Method + " over " +
                    result.N.ToString("N0") + " points." +
                    (result.Note.Length > 0 ? "  " + result.Note : "");
            }
            catch (OperationCanceledException)
            {
                txtStatus.Text = "Sweep canceled.";
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Sweep failed: " + ex.Message;
                MessageBox.Show(ex.Message, Title);
            }
            finally
            {
                if (!_closedDown) SetRunning(false);
                _cts.Dispose();
                _cts = null;
            }
        }

        private void OnCancel(object sender, RoutedEventArgs e)
        {
            try { _cts?.Cancel(); } catch { }
        }

        private void SetRunning(bool running)
        {
            btnRun.IsEnabled = !running;
            btnCancel.Visibility = running ? Visibility.Visible
                                           : Visibility.Collapsed;
            prgRun.Visibility = running ? Visibility.Visible
                                        : Visibility.Collapsed;
            if (running) { prgRun.Value = 0; }
            cboLayer.IsEnabled = !running;
            radK.IsEnabled = radA.IsEnabled = radR.IsEnabled = !running;
            txtSteps.IsEnabled = !running;
        }

        // =================================================================
        // chart
        // =================================================================
        private void RebuildChart()
        {
            if (_result == null || _closedDown) return;
            _model = LocohSweepChart.Build(_result, _chartWIn, _chartHIn,
                                           MeasureInches);
            var dg = PolarPlotWpf.Render(_model);
            dg.Freeze();
            imgChart.Source = new DrawingImage(dg);
            txtChartHint.Visibility = Visibility.Collapsed;
            btnSaveGraph.IsEnabled = btnExportCsv.IsEnabled =
                btnAddToLayout.IsEnabled = true;
        }

        private void OnChartSized(object sender, SizeChangedEventArgs e)
        {
            double win = Math.Max(e.NewSize.Width, 200) / 96.0;
            double hin = Math.Max(e.NewSize.Height, 150) / 96.0;
            win = Math.Min(Math.Max(win, 3.0), 14.0);
            hin = Math.Min(Math.Max(hin, 2.4), 10.0);
            if (Math.Abs(win - _chartWIn) < 0.06 &&
                Math.Abs(hin - _chartHIn) < 0.06) return;
            _chartWIn = win;
            _chartHIn = hin;
            RebuildChart();
        }

        private double MeasureInches(string text, double sizePt, string family)
        {
            try
            {
                var tf = new Typeface(
                    new FontFamily(string.IsNullOrEmpty(family)
                        ? "Segoe UI" : family),
                    FontStyles.Normal, FontWeights.Normal,
                    FontStretches.Normal);
                var ft = new FormattedText(text ?? "",
                    CultureInfo.InvariantCulture, FlowDirection.LeftToRight,
                    tf, sizePt * 96.0 / 72.0, Brushes.Black,
                    VisualTreeHelper.GetDpi(this).PixelsPerDip);
                return ft.WidthIncludingTrailingWhitespace / 96.0;
            }
            catch
            {
                return 0.075 * (text?.Length ?? 0) * sizePt / 9.0;
            }
        }

        // =================================================================
        // exports
        // =================================================================
        private void OnSaveGraph(object sender, RoutedEventArgs e)
        {
            if (_model == null) return;
            var dlg = new Microsoft.Win32.SaveFileDialog
            {
                Title = "Save the sweep chart",
                Filter = "PNG image (*.png)|*.png|SVG vector (*.svg)|*.svg",
                FileName = SafeName() + "_locoh_sweep.png",
            };
            if (dlg.ShowDialog() != true) return;
            try
            {
                if (Path.GetExtension(dlg.FileName)
                        .Equals(".svg", StringComparison.OrdinalIgnoreCase))
                {
                    File.WriteAllText(dlg.FileName,
                        PolarPlotSvg.Render(_model));
                }
                else
                {
                    const double scale = 300.0 / 96.0;   // 300 dpi
                    var dg = PolarPlotWpf.Render(_model);
                    int pxW = (int)Math.Ceiling(
                        _model.HalfWidthIn * 2 * 96 * scale);
                    int pxH = (int)Math.Ceiling(
                        _model.HalfHeightIn * 2 * 96 * scale);
                    var dv = new DrawingVisual();
                    using (var dc = dv.RenderOpen())
                    {
                        dc.DrawRectangle(Brushes.White, null,
                            new Rect(0, 0, pxW, pxH));
                        dc.PushTransform(new ScaleTransform(scale, scale));
                        dc.DrawDrawing(dg);
                        dc.Pop();
                    }
                    var rtb = new RenderTargetBitmap(pxW, pxH, 96, 96,
                        PixelFormats.Pbgra32);
                    rtb.Render(dv);
                    var enc = new PngBitmapEncoder();
                    enc.Frames.Add(BitmapFrame.Create(rtb));
                    using (var fs = File.Create(dlg.FileName)) enc.Save(fs);
                }
                txtStatus.Text = "Graph saved: " + dlg.FileName;
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Graph save failed: " + ex.Message;
                MessageBox.Show(ex.Message, Title);
            }
        }

        private void OnExportCsv(object sender, RoutedEventArgs e)
        {
            if (_result == null) return;
            var dlg = new Microsoft.Win32.SaveFileDialog
            {
                Title = "Export the sweep table",
                Filter = "CSV file (*.csv)|*.csv",
                FileName = SafeName() + "_locoh_sweep.csv",
            };
            if (dlg.ShowDialog() != true) return;
            try
            {
                var sb = new StringBuilder();
                sb.AppendLine("# LoCoH leveling-off sweep -- " +
                    _result.MethodTitle);
                sb.AppendLine("# Layer: " + _result.LayerName +
                    "; n = " + _result.N +
                    "; heuristic: " + _result.HeuristicLabel);
                sb.AppendLine("Parameter,Area50_km2,Area75_km2,Area95_km2");
                foreach (var s in _result.Steps.OrderBy(s2 => s2.Param))
                    sb.AppendLine(string.Format(CultureInfo.InvariantCulture,
                        "{0:0.######},{1:0.######},{2:0.######},{3:0.######}",
                        s.Param, s.AreaKm50, s.AreaKm75, s.AreaKm95));
                File.WriteAllText(dlg.FileName, sb.ToString());
                txtStatus.Text = "Table saved: " + dlg.FileName;
            }
            catch (Exception ex)
            {
                txtStatus.Text = "CSV export failed: " + ex.Message;
                MessageBox.Show(ex.Message, Title);
            }
        }

        private async void OnAddToLayout(object sender, RoutedEventArgs e)
        {
            if (_model == null) return;
            try
            {
                string where = await LayoutPlacement.AddChart(this, _model,
                    "LoCoH Parameter Sweep");
                if (where != null) txtStatus.Text = "✓ " + where;
            }
            catch (Exception ex)
            { MessageBox.Show(ex.Message, Title); }
        }

        private void OnHelp(object sender, RoutedEventArgs e)
            => WebHelp.Open("home-range/locoh-parameter-explorer.html");

        private string SafeName()
        {
            var name = _result?.LayerName ?? "points";
            foreach (var c in Path.GetInvalidFileNameChars())
                name = name.Replace(c, '_');
            return name.Replace(' ', '_');
        }

        private void OnClose(object sender, RoutedEventArgs e) => Close();
    }
}
