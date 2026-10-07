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
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;

namespace WildlifeTools.TpiSampler
{
    /// <summary>
    /// The content of the TPI Neighborhood Sampler window. Holds one coarse
    /// snapshot of a DEM, paints its TPI for the neighborhood on the bottom
    /// slider, draws the threshold on the left slider as a contour, tints the
    /// cells on the chosen side of it, and plots a pinned cell's TPI against
    /// the radius. Everything that needs ArcGIS Pro (the map's layers, the
    /// DEM read, the help page) is in <see cref="TpiSamplerActions"/>,
    /// reached from clicks, so the panel can be built and drawn outside Pro.
    /// </summary>
    public partial class TpiSamplerPanel : UserControl
    {
        private const double StepCells = 0.5;        // one slider tick, in snapshot cells
        private const double RangeDev = 3.0;         // the threshold slider's reach in SD units
        private const int MaxRadiusCells = 60;

        private SamplerGrid _grid;
        private FocalEngine _engine;
        private byte[] _hill;
        private WriteableBitmap _bmp;
        private double _rangeRaw = 1.0;              // the threshold slider's reach
        private double _colorRaw = 1.0;              // where the colors saturate
        private double _radius;                      // in the chosen units
        private double _thresholdRaw;
        private double _thresholdDev = 1.0;
        private (int c, int r)? _pin;
        private bool _syncing;
        private CancellationTokenSource _warm;
        private int _rmaxSteps = 2;

        /// <summary>Raised by the Close button.</summary>
        public event EventHandler CloseRequested;

        public TpiSamplerPanel()
        {
            InitializeComponent();
            _syncing = true;
            cmbUnits.ItemsSource = RadiusUnits.Names;
            cmbUnits.SelectedIndex = 1;              // Meters
            _syncing = false;
            Loaded += OnLoaded;
            Unloaded += (_, _) => _warm?.Cancel();
            cvCurve.SizeChanged += (_, _) => UpdatePinCurve();
            MakeDirect(sldThreshold);
            MakeDirect(sldRadius);
            stage.RenderTransform = _stageXf;
        }

        /// <summary>
        /// Makes a slider follow the mouse directly: press anywhere on it and
        /// the thumb goes there and drags from there. WPF's own behavior, a
        /// jump of LargeChange when the press misses the thumb, is what a user
        /// sees as "the slider jumps by 50 when I click it", and Pro's slider
        /// template makes the thumb easy to miss.
        /// </summary>
        private static void MakeDirect(Slider s)
        {
            bool dragging = false;
            s.IsMoveToPointEnabled = true;
            s.PreviewMouseLeftButtonDown += (_, e) =>
            {
                if (!s.IsEnabled) return;
                dragging = true;
                s.Focus();
                s.CaptureMouse();
                SetFromMouse(s, e);
                e.Handled = true;
            };
            s.PreviewMouseMove += (_, e) =>
            {
                if (dragging && e.LeftButton == MouseButtonState.Pressed) SetFromMouse(s, e);
            };
            s.PreviewMouseLeftButtonUp += (_, e) =>
            {
                if (!dragging) return;
                dragging = false;
                s.ReleaseMouseCapture();
                e.Handled = true;
            };
            s.LostMouseCapture += (_, _) => dragging = false;
        }

        private static void SetFromMouse(Slider s, MouseEventArgs e)
        {
            double v;
            if (s.Template?.FindName("PART_Track", s) is Track track)
            {
                v = track.ValueFromPoint(e.GetPosition(track));
            }
            else
            {
                Point p = e.GetPosition(s);
                double f = s.Orientation == Orientation.Horizontal
                    ? p.X / Math.Max(1.0, s.ActualWidth)
                    : 1.0 - p.Y / Math.Max(1.0, s.ActualHeight);
                v = s.Minimum + Math.Max(0.0, Math.Min(1.0, f)) * (s.Maximum - s.Minimum);
            }
            if (double.IsNaN(v) || double.IsInfinity(v)) return;
            v = Math.Max(s.Minimum, Math.Min(s.Maximum, v));
            if (s.IsSnapToTickEnabled && s.TickFrequency > 0)
            {
                v = s.Minimum + Math.Round((v - s.Minimum) / s.TickFrequency) * s.TickFrequency;
            }
            if (Math.Abs(v - s.Value) > 1e-12) s.Value = v;
        }

        // ---- the current choices ----------------------------------------------

        private string Unit => cmbUnits.SelectedItem as string ?? "Meters";
        private TpiScale Scale => rbDev.IsChecked == true ? TpiScale.NeighborhoodSd : TpiScale.Raw;
        private enum Side { Above, Below, None }
        private Side Classify => rbNone?.IsChecked == true ? Side.None
            : (rbBelow?.IsChecked == true ? Side.Below : Side.Above);
        private bool Above => Classify != Side.Below;
        /// <summary>False when the user asked for the TPI colors alone.</summary>
        private bool Classifying => Classify != Side.None;
        private double Threshold
        {
            get => Scale == TpiScale.Raw ? _thresholdRaw : _thresholdDev;
            set
            {
                if (Scale == TpiScale.Raw) _thresholdRaw = value;
                else _thresholdDev = value;
            }
        }
        private double Range => Scale == TpiScale.Raw ? _rangeRaw : RangeDev;
        private int Detail
        {
            get
            {
                var item = cmbDetail.SelectedItem as ComboBoxItem;
                return int.TryParse(item?.Content as string, out int n) ? n : 400;
            }
        }

        /// <summary>The radius, in the chosen units, of slider step k
        /// (k half-cells of the snapshot).</summary>
        private double RadiusForStep(double k, string unit) => k * StepCells * _grid.CellSizeIn(unit);

        private Neighborhood CurrentNeighborhood() => Neighborhood.Resolve(_grid, _radius, Unit);

        // ---- inside Pro: layers and the snapshot -------------------------------

        private void OnLoaded(object sender, RoutedEventArgs e)
        {
            if (cmbDem.Items.Count == 0) RefreshLayers();
        }

        private void OnRefreshLayers(object sender, RoutedEventArgs e) => RefreshLayers();

        private async void RefreshLayers()
        {
            try
            {
                object was = cmbDem.SelectedItem;
                List<TpiSamplerActions.DemLayer> layers = await TpiSamplerActions.ListDemLayersAsync();
                cmbDem.ItemsSource = layers;
                if (layers.Count == 0)
                {
                    txtStatus.Text = "The active map has no raster layer. Add the DEM to the map, then click the refresh button.";
                    return;
                }
                // the layer selected in the Contents pane first, then the one chosen before
                cmbDem.SelectedItem = layers.FirstOrDefault(l => l.Selected)
                    ?? layers.FirstOrDefault(l => was != null && l.ToString() == was.ToString())
                    ?? layers[0];
                txtStatus.Text = layers.Count == 1
                    ? "One raster layer found. Zoom to the terrain to test and click Take Snapshot."
                    : layers.Count + " raster layers found. Choose the DEM, zoom to the terrain to test and click Take Snapshot.";
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Could not list the map's layers: " + ex.Message;
            }
        }

        private async void OnSnapshot(object sender, RoutedEventArgs e)
        {
            if (!(cmbDem.SelectedItem is TpiSamplerActions.DemLayer dem))
            {
                txtStatus.Text = "Choose the DEM first.";
                return;
            }
            btnSnapshot.IsEnabled = false;
            txtStatus.Text = "Reading the DEM inside the view...";
            try
            {
                SamplerGrid g = await TpiSamplerActions.SnapshotAsync(dem, Detail);
                LoadGrid(g);
                txtStatus.Text = "Snapshot ready. Slide the radius and the threshold, or click a cell to pin it.";
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Could not take the snapshot: " + ex.Message;
            }
            finally
            {
                btnSnapshot.IsEnabled = true;
            }
        }

        // ---- the snapshot ------------------------------------------------------

        /// <summary>Takes a new snapshot into the window. (Also what the
        /// headless harness calls, with a made-up DEM.)</summary>
        internal void LoadGrid(SamplerGrid g)
        {
            _warm?.Cancel();
            _grid = g;
            _engine = new FocalEngine(g);
            _hill = Painter.Hillshade(g);
            _bmp = new WriteableBitmap(g.Width, g.Height, 96, 96, PixelFormats.Bgra32, null);
            imgTpi.Source = _bmp;
            stage.Width = g.Width;
            stage.Height = g.Height;
            txtEmpty.Visibility = Visibility.Collapsed;
            _pen = Math.Max(0.6, g.Width / 400.0);
            ResetZoom();

            int rmaxCells = Math.Max(2, Math.Min(MaxRadiusCells, Math.Min(g.Width, g.Height) / 3));
            _rmaxSteps = rmaxCells * 2;
            _syncing = true;
            sldRadius.Minimum = 2;
            sldRadius.Maximum = _rmaxSteps;
            sldRadius.IsEnabled = true;
            sldThreshold.IsEnabled = Classifying;
            _syncing = false;

            // one fixed range for the threshold slider, from the smallest and
            // the largest neighborhoods
            string unit = Unit;
            TpiGrids small = _engine.Get(Neighborhood.Resolve(g, RadiusForStep(2, unit), unit));
            TpiGrids large = _engine.Get(Neighborhood.Resolve(g, RadiusForStep(_rmaxSteps, unit), unit));
            _rangeRaw = Painter.NiceRange(new[] { small.Raw, large.Raw });
            _colorRaw = Painter.NiceRange(new[] { small.Raw, large.Raw }, 0.98);
            _thresholdRaw = _colorRaw / 4.0;
            _thresholdDev = 1.0;

            _radius = RadiusForStep(Math.Max(2, _rmaxSteps / 4), unit);
            _pin = null;
            btnClearPin.IsEnabled = false;
            curveBox.Visibility = Visibility.Collapsed;
            txtPin.Text = "Click a cell in the picture to pin it.";
            txtPinSpan.Text = "";
            btnCopy.IsEnabled = true;
            txtSnapshot.Text = Captions.Snapshot(g);
            SyncControls();
            Redraw();
            WarmCache();
        }

        /// <summary>Computes the grids for every slider step in the background,
        /// smallest first, so the slider never waits.</summary>
        private void WarmCache()
        {
            _warm?.Cancel();
            var cts = new CancellationTokenSource();
            _warm = cts;
            SamplerGrid g = _grid;
            FocalEngine engine = _engine;
            string unit = Unit;
            int steps = _rmaxSteps;
            Task.Run(() =>
            {
                for (int k = 2; k <= steps && !cts.IsCancellationRequested; k++)
                {
                    engine.Get(Neighborhood.Resolve(g, k * StepCells * g.CellSizeIn(unit), unit));
                }
            }, cts.Token);
        }

        // ---- the controls ------------------------------------------------------

        /// <summary>Puts the sliders and boxes where the current radius and
        /// threshold are, without firing their handlers.</summary>
        private void SyncControls()
        {
            if (_grid == null) return;
            _syncing = true;
            try
            {
                double step = _radius / (StepCells * _grid.CellSizeIn(Unit));
                sldRadius.Value = Math.Max(sldRadius.Minimum, Math.Min(sldRadius.Maximum, Math.Round(step)));
                txtRadius.Text = Captions.Number(_radius, 4);
                sldThreshold.Minimum = -Range;
                sldThreshold.Maximum = Range;
                sldThreshold.LargeChange = Range / 10.0;
                sldThreshold.SmallChange = Range / 100.0;
                sldThreshold.Value = Math.Max(-Range, Math.Min(Range, Threshold));
                txtThreshold.Text = Captions.Number(Threshold, 4);
                txtThresholdTop.Text = "+" + Captions.Number(Range, 3);
                txtThresholdBottom.Text = "−" + Captions.Number(Range, 3);
                txtThresholdAxis.Text = "Classification threshold ("
                                        + (Scale == TpiScale.Raw ? "elevation units" : "standard deviations") + ")";
            }
            finally
            {
                _syncing = false;
            }
        }

        private void OnRadiusSlider(object sender, RoutedPropertyChangedEventArgs<double> e)
        {
            if (_syncing || _grid == null) return;
            _radius = RadiusForStep(sldRadius.Value, Unit);
            _syncing = true;
            txtRadius.Text = Captions.Number(_radius, 4);
            _syncing = false;
            Redraw();
        }

        private void OnThresholdSlider(object sender, RoutedPropertyChangedEventArgs<double> e)
        {
            if (_syncing || _grid == null) return;
            Threshold = sldThreshold.Value;
            _syncing = true;
            txtThreshold.Text = Captions.Number(Threshold, 4);
            _syncing = false;
            Redraw();
        }

        private void OnRadiusKey(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.Enter)
            {
                ApplyTypedRadius();
                e.Handled = true;
            }
        }

        private void OnRadiusTyped(object sender, RoutedEventArgs e) => ApplyTypedRadius();

        private void ApplyTypedRadius()
        {
            if (_syncing || _grid == null) return;
            if (!double.TryParse(txtRadius.Text.Replace(",", ""), NumberStyles.Float, CultureInfo.CurrentCulture, out double v) || v <= 0)
            {
                txtStatus.Text = "The radius must be a number greater than 0.";
                return;
            }
            double maxR = RadiusForStep(_rmaxSteps, Unit);
            if (v > maxR * 1.5)
            {
                txtStatus.Text = "The largest neighborhood this snapshot can test is about "
                                 + Captions.Number(maxR * 1.5) + " " + Unit.ToLowerInvariant()
                                 + ". Zoom the map out and take a new snapshot for larger ones.";
                v = maxR * 1.5;
            }
            _radius = v;
            SyncControls();
            Redraw();
        }

        private void OnThresholdKey(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.Enter)
            {
                ApplyTypedThreshold();
                e.Handled = true;
            }
        }

        private void OnThresholdTyped(object sender, RoutedEventArgs e) => ApplyTypedThreshold();

        private void ApplyTypedThreshold()
        {
            if (_syncing || _grid == null) return;
            if (!double.TryParse(txtThreshold.Text.Replace(",", ""), NumberStyles.Float, CultureInfo.CurrentCulture, out double v))
            {
                txtStatus.Text = "The threshold must be a number.";
                return;
            }
            Threshold = v;
            SyncControls();
            Redraw();
        }

        private void OnUnitsChanged(object sender, SelectionChangedEventArgs e)
        {
            if (_syncing || _grid == null) return;
            // keep the same ground radius in the new units
            string from = e.RemovedItems.Count > 0 ? e.RemovedItems[0] as string : null;
            if (from != null)
            {
                double meters = from == "Cells"
                    ? _radius / _grid.Factor * _grid.MeanCellM
                    : _radius * RadiusUnits.MetersPer(from);
                _radius = Unit == "Cells"
                    ? meters / _grid.MeanCellM * _grid.Factor
                    : meters / RadiusUnits.MetersPer(Unit);
            }
            SyncControls();
            Redraw();
            WarmCache();
        }

        private void OnScaleChanged(object sender, RoutedEventArgs e)
        {
            if (_grid == null) return;
            SyncControls();
            Redraw();
        }

        private void OnSideChanged(object sender, RoutedEventArgs e)
        {
            // the threshold controls rest while no classification is drawn
            // (the controls after this one in the XAML are null while it loads)
            bool on = Classifying;
            if (sldThreshold != null) sldThreshold.IsEnabled = on && _grid != null;
            if (txtThreshold != null) txtThreshold.IsEnabled = on;
            if (chkMirror != null) chkMirror.IsEnabled = on;
            if (chkTint != null) chkTint.IsEnabled = on;
            Redraw();
        }

        private void OnDisplayChanged(object sender, RoutedEventArgs e) => Redraw();

        // ---- the picture -------------------------------------------------------

        /// <summary>Repaints the TPI, the threshold line and the pin for the
        /// current choices. (Also what the headless harness calls.)</summary>
        internal void Redraw()
        {
            if (_grid == null || _engine == null) return;
            int w = _grid.Width, h = _grid.Height;
            Neighborhood nb = CurrentNeighborhood();
            float[] v = _engine.Get(nb).For(Scale);
            double colorRange = Scale == TpiScale.Raw ? _colorRaw : 2.0;
            int edgeX = (int)Math.Ceiling(nb.RxRows.Length > 0 ? nb.RxRows.Max() : 0), edgeY = (int)Math.Ceiling(nb.Ry);
            bool classify = Classifying;
            byte[] px = Painter.Paint(v, w, h, colorRange, Threshold, Above,
                classify && chkTint.IsChecked == true, chkHillshade.IsChecked == true ? _hill : null, edgeX, edgeY);
            _bmp.WritePixels(new Int32Rect(0, 0, w, h), px, w * 4, 0);
            pathContour.Data = classify ? ToGeometry(Contour.Trace(v, w, h, Threshold)) : null;
            pathMirror.Data = classify && chkMirror.IsChecked == true ? ToGeometry(Contour.Trace(v, w, h, -Threshold)) : null;
            txtRadiusAxis.Text = "Neighborhood radius: " + Captions.Radius(nb, _grid);
            txtRadiusInfo.Text = "One snapshot cell is " + Captions.Number(_grid.CellSizeIn(Unit), 3)
                                 + (Unit == "Cells" ? " DEM cells" : " " + Unit.ToLowerInvariant())
                                 + "; the slider runs from one cell to " + Captions.Number(RadiusForStep(_rmaxSteps, Unit), 3)
                                 + (Unit == "Cells" ? " DEM cells" : " " + Unit.ToLowerInvariant()) + ".";
            DrawPinMarker();
            UpdatePinCurve();
        }

        private static Geometry ToGeometry(List<(double x1, double y1, double x2, double y2)> segs)
        {
            if (segs.Count == 0) return null;
            var geo = new StreamGeometry();
            using (StreamGeometryContext ctx = geo.Open())
            {
                foreach (var s in segs)
                {
                    ctx.BeginFigure(new Point(s.x1, s.y1), false, false);
                    ctx.LineTo(new Point(s.x2, s.y2), true, false);
                }
            }
            geo.Freeze();
            return geo;
        }

        // ---- the pinned feature --------------------------------------------------

        private void OnStageClick(object sender, MouseButtonEventArgs e)
        {
            if (_grid == null) return;
            Point p = e.GetPosition(stage);
            int c = (int)Math.Floor(p.X), r = (int)Math.Floor(p.Y);
            if (c < 0 || c >= _grid.Width || r < 0 || r >= _grid.Height) return;
            SetPin(c, r);
        }

        /// <summary>Pins a cell. (Also what the headless harness calls.)</summary>
        internal void SetPin(int c, int r)
        {
            if (_grid == null) return;
            if (!_grid.Valid[r * _grid.Width + c])
            {
                txtStatus.Text = "That cell has no data.";
                return;
            }
            _pin = (c, r);
            btnClearPin.IsEnabled = true;
            curveBox.Visibility = Visibility.Visible;
            DrawPinMarker();
            UpdatePinCurve();
        }

        private void OnPinHelp(object sender, RoutedEventArgs e) => popPinHelp.IsOpen = true;

        private void OnClearPin(object sender, RoutedEventArgs e)
        {
            _pin = null;
            btnClearPin.IsEnabled = false;
            curveBox.Visibility = Visibility.Collapsed;
            txtPin.Text = "Click a cell in the picture to pin it.";
            txtPinSpan.Text = "";
            DrawPinMarker();
        }

        private void DrawPinMarker()
        {
            if (_pin == null || _grid == null)
            {
                pathPin.Data = null;
                pathPinInner.Data = null;
                return;
            }
            double x = _pin.Value.c + 0.5, y = _pin.Value.r + 0.5;
            double a = Math.Max(4.0, _grid.Width / 50.0);
            var geo = new StreamGeometry();
            using (StreamGeometryContext ctx = geo.Open())
            {
                ctx.BeginFigure(new Point(x - a, y), false, false);
                ctx.LineTo(new Point(x + a, y), true, false);
                ctx.BeginFigure(new Point(x, y - a), false, false);
                ctx.LineTo(new Point(x, y + a), true, false);
            }
            geo.Freeze();
            pathPin.Data = geo;
            pathPinInner.Data = geo;
        }

        /// <summary>The pinned cell's TPI at every slider step, in the current scale.</summary>
        internal double[] PinCurve()
        {
            if (_pin == null || _grid == null) return null;
            var curve = new double[_rmaxSteps + 1];
            for (int k = 0; k <= _rmaxSteps; k++)
            {
                if (k < 2)
                {
                    curve[k] = double.NaN;
                    continue;
                }
                Neighborhood nb = Neighborhood.Resolve(_grid, RadiusForStep(k, Unit), Unit);
                (double raw, double dev) = _engine.At(nb, _pin.Value.c, _pin.Value.r);
                curve[k] = Scale == TpiScale.Raw ? raw : dev;
            }
            return curve;
        }

        private void UpdatePinCurve()
        {
            cvCurve.Children.Clear();
            if (_pin == null || _grid == null) return;
            double[] curve = PinCurve();
            double cw = cvCurve.ActualWidth, ch = cvCurve.ActualHeight;
            if (cw < 20 || ch < 20) cw = Math.Max(cw, 240);
            if (ch < 20) ch = 128;
            const double padL = 6, padR = 6, padT = 8, padB = 16;
            // the plot's own vertical range: the curve and the threshold, with a little room
            double range = Math.Abs(Threshold);
            foreach (double vv in curve)
            {
                if (!double.IsNaN(vv) && Math.Abs(vv) > range) range = Math.Abs(vv);
            }
            range = range > 0 ? range * 1.15 : 1.0;
            double X(double k) => padL + (k - 2) / Math.Max(1, _rmaxSteps - 2) * (cw - padL - padR);
            double Y(double v) => padT + (range - Math.Max(-range, Math.Min(range, v))) / (2 * range) * (ch - padT - padB);

            // the span of radii that classifies the cell as the feature
            bool classify = Classifying;
            var spans = new List<(int from, int to)>();
            int start = -1;
            for (int k = 2; k <= _rmaxSteps && classify; k++)
            {
                bool pass = !double.IsNaN(curve[k]) && (Above ? curve[k] >= Threshold : curve[k] <= Threshold);
                if (pass && start < 0) start = k;
                if (!pass && start >= 0)
                {
                    spans.Add((start, k - 1));
                    start = -1;
                }
            }
            if (start >= 0) spans.Add((start, _rmaxSteps));
            foreach (var s in spans)
            {
                var rect = new Rectangle
                {
                    Fill = new SolidColorBrush(Color.FromArgb(70, 255, 230, 0)),
                    Width = Math.Max(1, X(s.to) - X(s.from) + (cw - padL - padR) / Math.Max(1, _rmaxSteps - 2)),
                    Height = ch - padT - padB,
                };
                Canvas.SetLeft(rect, X(s.from) - (cw - padL - padR) / Math.Max(1, _rmaxSteps - 2) / 2);
                Canvas.SetTop(rect, padT);
                cvCurve.Children.Add(rect);
            }
            // zero line and threshold line
            cvCurve.Children.Add(new Line { X1 = padL, X2 = cw - padR, Y1 = Y(0), Y2 = Y(0), Stroke = Brushes.Gray, StrokeThickness = 0.8, StrokeDashArray = new DoubleCollection { 2, 2 } });
            if (classify)
            {
                cvCurve.Children.Add(new Line { X1 = padL, X2 = cw - padR, Y1 = Y(Threshold), Y2 = Y(Threshold), Stroke = Brushes.Black, StrokeThickness = 1.2 });
            }
            // the curve
            var poly = new Polyline { Stroke = new SolidColorBrush(Color.FromRgb(208, 31, 31)), StrokeThickness = 1.8 };
            for (int k = 2; k <= _rmaxSteps; k++)
            {
                if (double.IsNaN(curve[k])) continue;
                poly.Points.Add(new Point(X(k), Y(curve[k])));
            }
            cvCurve.Children.Add(poly);
            // the current radius
            double kNow = _radius / (StepCells * _grid.CellSizeIn(Unit));
            cvCurve.Children.Add(new Line { X1 = X(kNow), X2 = X(kNow), Y1 = padT, Y2 = ch - padB, Stroke = Brushes.DimGray, StrokeThickness = 1.0 });
            // axis labels
            var lo = new TextBlock { Text = Captions.Number(RadiusForStep(2, Unit), 2), FontSize = 10, Foreground = Brushes.DimGray };
            Canvas.SetLeft(lo, padL);
            Canvas.SetTop(lo, ch - padB + 1);
            cvCurve.Children.Add(lo);
            var hi = new TextBlock { Text = Captions.Number(RadiusForStep(_rmaxSteps, Unit), 2) + " " + (Unit == "Cells" ? "DEM cells" : Unit.ToLowerInvariant()), FontSize = 10, Foreground = Brushes.DimGray };
            hi.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
            Canvas.SetLeft(hi, cw - padR - hi.DesiredSize.Width);
            Canvas.SetTop(hi, ch - padB + 1);
            cvCurve.Children.Add(hi);

            // the words
            Neighborhood now = CurrentNeighborhood();
            (double rawNow, double devNow) = _engine.At(now, _pin.Value.c, _pin.Value.r);
            double vNow = Scale == TpiScale.Raw ? rawNow : devNow;
            string unitWord = Unit == "Cells" ? " DEM cells" : " " + Unit.ToLowerInvariant();
            txtPin.Text = "Pinned cell: column " + (_pin.Value.c + 1) + ", row " + (_pin.Value.r + 1)
                          + ", elevation " + Captions.Number(_grid.Z[_pin.Value.r * _grid.Width + _pin.Value.c], 5)
                          + ". At the current radius its TPI is " + Captions.Number(vNow, 3)
                          + (Scale == TpiScale.Raw ? " elevation units" : " SD")
                          + (double.IsNaN(vNow) || !classify ? "." : (Above ? vNow >= Threshold : vNow <= Threshold) ? ": it IS the feature." : ": it is NOT the feature.");
            if (!classify)
            {
                txtPinSpan.Text = "No threshold is applied; the curve is the cell's TPI at every radius.";
            }
            else if (spans.Count == 0)
            {
                txtPinSpan.Text = "No radius on the slider classifies this cell as the feature at this threshold.";
            }
            else if (spans.Count == 1 && spans[0].from == 2 && spans[0].to == _rmaxSteps)
            {
                txtPinSpan.Text = "Every radius on the slider classifies this cell as the feature at this threshold.";
            }
            else
            {
                var parts = spans.Select(s => Captions.Number(RadiusForStep(s.from, Unit), 3) + " to "
                                              + Captions.Number(RadiusForStep(s.to, Unit), 3));
                txtPinSpan.Text = "This cell is the feature at this threshold for radii from " + string.Join(", and from ", parts) + unitWord + ".";
            }
        }

        // ---- zoom and pan of the picture ------------------------------------------

        private readonly MatrixTransform _stageXf = new MatrixTransform();
        private double _zoom = 1.0;
        private double _pen = 1.0;
        private bool _panning;
        private Point _panLast;

        /// <summary>Line widths that stay the same on screen at any zoom, and the
        /// zoom readout. The stage is drawn in cell units and the Viewbox scales
        /// it, so a zoom is a transform on the stage itself.</summary>
        private void ApplyZoom()
        {
            pathContour.StrokeThickness = 1.1 * _pen / _zoom;
            pathMirror.StrokeThickness = 0.9 * _pen / _zoom;
            pathPin.StrokeThickness = 2.2 * _pen / _zoom;
            pathPinInner.StrokeThickness = 1.0 * _pen / _zoom;
            btnFullExtent.IsEnabled = _zoom > 1.0001;
            txtZoom.Text = _zoom > 1.0001
                ? "Zoomed " + Captions.Number(_zoom, 2) + "×. Wheel zooms; middle or right drag pans."
                : "Wheel zooms the picture; middle or right drag pans.";
        }

        private void ResetZoom()
        {
            _zoom = 1.0;
            _stageXf.Matrix = Matrix.Identity;
            ApplyZoom();
        }

        private void OnFullExtent(object sender, RoutedEventArgs e) => ResetZoom();

        /// <summary>Screen pixels per stage unit, from the Viewbox.</summary>
        private double ViewboxScale()
        {
            if (stage.Width <= 0 || stage.Height <= 0) return 1.0;
            return Math.Max(1e-9, Math.Min(viewbox.ActualWidth / stage.Width, viewbox.ActualHeight / stage.Height));
        }

        private void OnPictureWheel(object sender, MouseWheelEventArgs e)
        {
            if (_grid == null) return;
            double f = e.Delta > 0 ? 1.25 : 1.0 / 1.25;
            double z = Math.Max(1.0, Math.Min(32.0, _zoom * f));
            e.Handled = true;
            if (Math.Abs(z - _zoom) < 1e-9) return;
            if (z <= 1.0001)
            {
                ResetZoom();
                return;
            }
            f = z / _zoom;
            Point p = e.GetPosition(stage);                 // the cell under the mouse
            Matrix m = _stageXf.Matrix;
            Point q = m.Transform(p);                       // where it is drawn now
            m.ScaleAt(f, f, q.X, q.Y);                      // zoom about that spot, so it stays put
            _stageXf.Matrix = m;
            _zoom = z;
            ApplyZoom();
        }

        private void OnPictureMouseDown(object sender, MouseButtonEventArgs e)
        {
            if (_grid == null || _zoom <= 1.0001) return;
            if (e.ChangedButton != MouseButton.Middle && e.ChangedButton != MouseButton.Right) return;
            _panning = true;
            _panLast = e.GetPosition(picBox);
            picBox.CaptureMouse();
            e.Handled = true;
        }

        private void OnPictureMouseMove(object sender, MouseEventArgs e)
        {
            if (!_panning) return;
            if (e.MiddleButton != MouseButtonState.Pressed && e.RightButton != MouseButtonState.Pressed)
            {
                EndPan();
                return;
            }
            Point now = e.GetPosition(picBox);
            double s = ViewboxScale();
            Matrix m = _stageXf.Matrix;
            m.Translate((now.X - _panLast.X) / s, (now.Y - _panLast.Y) / s);
            _stageXf.Matrix = m;
            _panLast = now;
        }

        private void OnPictureMouseUp(object sender, MouseButtonEventArgs e)
        {
            if (!_panning) return;
            if (e.ChangedButton == MouseButton.Middle || e.ChangedButton == MouseButton.Right)
            {
                EndPan();
                e.Handled = true;
            }
        }

        private void EndPan()
        {
            _panning = false;
            picBox.ReleaseMouseCapture();
        }

        // ---- the bottom bar ----------------------------------------------------------

        private void OnCopySettings(object sender, RoutedEventArgs e)
        {
            if (_grid == null) return;
            try
            {
                Clipboard.SetText(Captions.Settings(_grid, CurrentNeighborhood(), Scale, Threshold, Above, Classifying));
                txtStatus.Text = "The settings are on the clipboard.";
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Could not copy: " + ex.Message;
            }
        }

        private void OnHelp(object sender, RoutedEventArgs e) => TpiSamplerActions.OpenHelp();

        private void OnClose(object sender, RoutedEventArgs e) =>
            CloseRequested?.Invoke(this, EventArgs.Empty);
    }
}
