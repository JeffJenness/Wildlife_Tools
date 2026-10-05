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
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media.Imaging;
using ArcGIS.Desktop.Catalog;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;

namespace WildlifeTools.Geometric
{
    /// <summary>
    /// The friendly front end for the Repeating Shapes GEOPROCESSING
    /// tool: a shape gallery with the classic pattern images, per-shape
    /// size boxes with the measure diagram alongside, live unit-coupled
    /// interlock, a view-extent option the GP pane cannot offer, and a
    /// Randomize-orientation button -- everything the original dialogs
    /// had. Run executes the REAL GP tool through ExecuteToolAsync, so
    /// each run lands in Geoprocessing History with environments honored,
    /// exactly as a pane run would (the Bottleneck window's proven
    /// pattern); the pane and ModelBuilder paths remain untouched.
    /// </summary>
    public partial class RepeatingShapesWindow
        : ArcGIS.Desktop.Framework.Controls.ProWindow,
          ArcGIS.Desktop.Framework.DragDrop.IDropTarget
    {
        private static RepeatingShapesWindow _instance;

        public static void ShowSingleton()
        {
            if (_instance != null)
            {
                try { _instance.Activate(); return; } catch { }
            }
            _instance = new RepeatingShapesWindow
            {
                Owner = ArcGIS.Desktop.Framework.FrameworkApplication
                    .Current.MainWindow,
            };
            _instance.Closed += (_, __) => _instance = null;
            _instance.Show();
        }

        private bool _closedDown;
        private bool _updating;
        private string _shape = RsMath.Hexagons;
        private readonly Dictionary<string, TextBox> _boxes = new();
        private readonly List<TextBlock> _areaUnitLabels = new();
        private readonly List<TextBlock> _linUnitLabels = new();
        private TextBox _txtOffset;
        private readonly Random _rng = new();
        private ArcGIS.Core.Events.SubscriptionToken _appClosingToken;

        private static readonly (string Shape, string Img)[] _tiles =
        {
            (RsMath.Hexagons, "patterns_hex.gif"),
            (RsMath.Squares, "patterns_sqr.gif"),
            (RsMath.Triangles, "patterns_tri.gif"),
            (RsMath.Rectangles, "patterns_rect.gif"),
            (RsMath.CirclesSq, "patterns_cirsqr.gif"),
            (RsMath.CirclesTri, "patterns_cirstri.gif"),
            (RsMath.PointsSq, "patterns_ptsqr.gif"),
            (RsMath.PointsTri, "patterns_pttri.gif"),
        };

        // vector-rendered at 3x (Inkscape, from authored SVGs) and shown
        // at 1/3 -- crisp at 100% scaling and on high-DPI monitors alike
        private static readonly Dictionary<string, string> _diagrams =
            new()
            {
                [RsMath.Hexagons] = "measure_hex.png",
                [RsMath.Squares] = "measure_square.png",
                [RsMath.Triangles] = "measure_triangle.png",
                [RsMath.Rectangles] = "measure_rect.png",
                [RsMath.CirclesSq] = "measure_circle.png",
                [RsMath.CirclesTri] = "measure_circle.png",
                [RsMath.PointsSq] = "patterns_ptsqr.gif",
                [RsMath.PointsTri] = "patterns_pttri.gif",
            };

        private static readonly string[] _edgePolicies =
        {
            "Keep whole shapes intersecting the area (original)",
            "Keep shapes whose center is inside the area",
            "Clip shapes to the area",
        };

        public RepeatingShapesWindow()
        {
            InitializeComponent();
            BuildTiles();
            cboAreaUnits.ItemsSource = RsMath.AreaUnits;
            cboAreaUnits.SelectedItem = "Hectares";
            cboLinUnits.ItemsSource = RsMath.LinearUnits;
            cboLinUnits.SelectedItem = "Meters";
            cboEdgePolicy.ItemsSource = _edgePolicies;
            cboEdgePolicy.SelectedIndex = 0;
            SelectShape(RsMath.Hexagons);
            // drag a layer from the Contents pane anywhere onto the
            // window (Bottleneck's proven route: answer with an effect
            // from the drag's ALLOWED set and mark Handled, else the
            // framework's global handlers overwrite it with None)
            ArcGIS.Desktop.Framework.DragDrop.DragDrop
                .SetDropHandler(cboBoundary, this);
            cboBoundary.AllowDrop = true;
            cboBoundary.PreviewDragOver += OnWpfDragOver;
            cboBoundary.PreviewDrop += OnWpfDrop;
            ArcGIS.Desktop.Framework.DragDrop.DragDrop
                .SetIsDropTarget(this, true);
            ArcGIS.Desktop.Framework.DragDrop.DragDrop
                .SetDropHandler(this, this);
            AllowDrop = true;
            PreviewDragOver += OnWpfDragOver;
            PreviewDrop += OnWpfDrop;
            Loaded += (_, __) =>
            {
                PopulateBoundaryLayers();
                SuggestOutput();
                if (_appClosingToken == null)
                    _appClosingToken = ArcGIS.Desktop.Framework.Events
                        .ApplicationClosingEvent.Subscribe(args =>
                        {
                            _closedDown = true;
                            try { Close(); } catch { }
                            return Task.CompletedTask;
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

        private static BitmapImage Res(string name)
        {
            return new BitmapImage(new Uri(
                "pack://application:,,,/WildlifeTools;component/"
                + "Geometric/Img/" + name));
        }

        private void BuildTiles()
        {
            foreach (var (shape, imgName) in _tiles)
            {
                var stack = new StackPanel();
                stack.Children.Add(new Image
                {
                    Source = Res(imgName),
                    Width = 92,
                    Stretch = System.Windows.Media.Stretch.Uniform,
                });
                stack.Children.Add(new TextBlock
                {
                    Text = shape.Replace(" pattern", ""),
                    TextAlignment = TextAlignment.Center,
                    TextWrapping = TextWrapping.Wrap,
                    MaxWidth = 96,
                    FontSize = 11,
                });
                var rb = new RadioButton
                {
                    Style = (Style)FindResource("Tile"),
                    Content = stack,
                    Tag = shape,
                    IsChecked = shape == RsMath.Hexagons,
                };
                rb.Checked += (_, __) => SelectShape((string)rb.Tag);
                grdTiles.Children.Add(rb);
            }
        }

        // =================================================================
        // shape selection + measure boxes
        // =================================================================
        private static readonly Dictionary<string, string> _labels = new()
        {
            ["area"] = "Area =",
            ["edge"] = "Edge =",
            ["width"] = "Width =",
            ["height"] = "Height =",
            ["diameter"] = "Diameter =",
            ["radius"] = "Radius =",
            ["circumference"] = "Circumference =",
            ["spacing"] = "Spacing between centers =",
        };

        private void SelectShape(string shape)
        {
            _shape = shape;
            if (grdMeasures == null) return;
            grdMeasures.Children.Clear();
            grdMeasures.RowDefinitions.Clear();
            _boxes.Clear();
            _areaUnitLabels.Clear();
            _linUnitLabels.Clear();
            _txtOffset = null;

            var measures = RsMath.MeasuresFor(shape).ToList();
            // rectangle-specific labels
            string Lab(string m)
            {
                if (shape == RsMath.Rectangles)
                    return m == "width" ? "Edge 1 (width) ="
                         : m == "height" ? "Edge 2 (height) =" : _labels[m];
                return _labels[m];
            }

            int row = 0;
            foreach (var m in measures)
            {
                grdMeasures.RowDefinitions.Add(new RowDefinition());
                var lbl = new TextBlock
                {
                    Text = Lab(m),
                    Style = (Style)FindResource("MeasureLbl"),
                };
                Grid.SetRow(lbl, row);
                Grid.SetColumn(lbl, 0);
                grdMeasures.Children.Add(lbl);
                var box = new TextBox
                { Style = (Style)FindResource("MeasureBox"), Tag = m };
                box.TextChanged += OnMeasureChanged;
                Grid.SetRow(box, row);
                Grid.SetColumn(box, 1);
                grdMeasures.Children.Add(box);
                var unit = new TextBlock
                {
                    Opacity = 0.65,
                    VerticalAlignment = VerticalAlignment.Center,
                };
                (m == "area" ? _areaUnitLabels : _linUnitLabels)
                    .Add(unit);
                Grid.SetRow(unit, row);
                Grid.SetColumn(unit, 2);
                grdMeasures.Children.Add(unit);
                _boxes[m] = box;
                row++;
            }
            if (shape == RsMath.Rectangles)
            {
                grdMeasures.RowDefinitions.Add(new RowDefinition());
                var lbl = new TextBlock
                {
                    Text = "% Offset (0-100) =",
                    Style = (Style)FindResource("MeasureLbl"),
                    ToolTip = "How far each course shifts relative to the "
                        + "course below, as a percent of Edge 1. 50 = "
                        + "running bond (bricks), 33 = staircase.",
                };
                Grid.SetRow(lbl, row);
                grdMeasures.Children.Add(lbl);
                _txtOffset = new TextBox
                {
                    Style = (Style)FindResource("MeasureBox"),
                    Text = "50",
                };
                Grid.SetRow(_txtOffset, row);
                Grid.SetColumn(_txtOffset, 1);
                grdMeasures.Children.Add(_txtOffset);
            }
            try { imgDiagram.Source = Res(_diagrams[shape]); }
            catch { imgDiagram.Source = null; }
            cboAreaUnits.IsEnabled = measures.Contains("area");
            RefreshUnitLabels();
            UpdateEdgePolicyEnabled();
        }

        /// <summary>The "(hectares)" / "(meters)" hints beside the
        /// boxes track the unit dropdowns live.</summary>
        private void RefreshUnitLabels()
        {
            string au = (cboAreaUnits.SelectedItem as string
                         ?? "area units").ToLowerInvariant();
            string lu = (cboLinUnits.SelectedItem as string
                         ?? "length units").ToLowerInvariant();
            foreach (var tb in _areaUnitLabels) tb.Text = "(" + au + ")";
            foreach (var tb in _linUnitLabels) tb.Text = "(" + lu + ")";
        }

        private static double? Num(string s)
        {
            if (string.IsNullOrWhiteSpace(s)) return null;
            if (double.TryParse(s, NumberStyles.Float,
                                CultureInfo.CurrentCulture, out double d)
                || double.TryParse(s, NumberStyles.Float,
                                   CultureInfo.InvariantCulture, out d))
                return d;
            return null;
        }

        private double A2L()
        {
            string au = cboAreaUnits.SelectedItem as string ?? "Hectares";
            string lu = cboLinUnits.SelectedItem as string ?? "Meters";
            double lm = RsMath.LinM[lu];
            return RsMath.AreaM2[au] / (lm * lm);
        }

        private void OnMeasureChanged(object sender, TextChangedEventArgs e)
        {
            if (_updating || sender is not TextBox box) return;
            string m = (string)box.Tag;
            double? v = Num(box.Text);
            if (v is not > 0) return;
            double a2l = A2L();
            double vLin = m == "area" ? v.Value * a2l : v.Value;
            var derived = RsMath.Derive(_shape, m, vLin);
            if (derived.Count == 0) return;
            _updating = true;
            try
            {
                foreach (var (name, dv) in derived)
                {
                    if (name == m || !_boxes.TryGetValue(name, out var b))
                        continue;
                    double show = name == "area" ? dv / a2l : dv;
                    b.Text = show.ToString("G8",
                                           CultureInfo.CurrentCulture);
                }
            }
            finally { _updating = false; }
        }

        private void ReDerive()
        {
            // after a unit change: numbers keep, physical sizes rescale --
            // re-derive from the area box when filled, else the first
            // filled length box (mirrors the GP dialog)
            var order = RsMath.MeasuresFor(_shape);
            foreach (var m in order)
            {
                if (_boxes.TryGetValue(m, out var b) && Num(b.Text) > 0)
                {
                    OnMeasureChanged(b, null);
                    return;
                }
            }
        }

        private void OnAreaUnitsChanged(object sender,
                                        SelectionChangedEventArgs e)
        {
            if (_updating) return;
            string au = cboAreaUnits.SelectedItem as string;
            if (au != null
                && RsMath.AreaToLinear.TryGetValue(au, out string lu))
            {
                _updating = true;
                cboLinUnits.SelectedItem = lu;
                _updating = false;
            }
            RefreshUnitLabels();
            ReDerive();
        }

        private void OnLinUnitsChanged(object sender,
                                       SelectionChangedEventArgs e)
        {
            if (_updating) return;
            RefreshUnitLabels();
            ReDerive();
        }

        private void OnRandomize(object sender, RoutedEventArgs e)
        {
            double period = RsMath.OrientationPeriod(_shape);
            txtOrient.Text = (_rng.NextDouble() * period)
                .ToString("0.##", CultureInfo.CurrentCulture);
        }

        // =================================================================
        // area of interest
        // =================================================================
        private sealed class BoundaryChoice
        {
            public FeatureLayer Layer;
            public string Path;
            public string Name;
            public override string ToString() => Name;
            public string GpValue => Layer != null ? Layer.Name : Path;
        }

        private static string GeomWord(FeatureLayer fl) =>
            fl.ShapeType switch
            {
                ArcGIS.Core.CIM.esriGeometryType.esriGeometryPolygon
                    => "polygon",
                ArcGIS.Core.CIM.esriGeometryType.esriGeometryPolyline
                    => "line",
                ArcGIS.Core.CIM.esriGeometryType.esriGeometryPoint
                    => "point",
                ArcGIS.Core.CIM.esriGeometryType.esriGeometryMultipoint
                    => "point",
                _ => null,
            };

        private void PopulateBoundaryLayers()
        {
            // polygons enclose; line/point layers select the shapes
            // that touch them -- so every feature layer qualifies
            var items = new List<BoundaryChoice>();
            var map = MapView.Active?.Map;
            if (map != null)
                foreach (var fl in map.GetLayersAsFlattenedList()
                         .OfType<FeatureLayer>()
                         .Where(l => GeomWord(l) != null))
                    items.Add(new BoundaryChoice
                    {
                        Layer = fl,
                        Name = fl.Name + "  (" + GeomWord(fl) + "s)",
                    });
            cboBoundary.ItemsSource = items;
            var firstPoly = items.FindIndex(c =>
                GeomWord(c.Layer) == "polygon");
            if (items.Count > 0)
                cboBoundary.SelectedIndex = firstPoly >= 0 ? firstPoly : 0;
            rbViewExtent.IsEnabled = MapView.Active != null;
        }

        private void OnBrowseBoundary(object sender, RoutedEventArgs e)
        {
            var dlg = new OpenItemDialog
            {
                Title = "Choose the area-of-interest polygon feature class",
                MultiSelect = false,
                Filter = ItemFilters.FeatureClasses_All,
            };
            if (dlg.ShowDialog() != true || !dlg.Items.Any()) return;
            var item = dlg.Items.First();
            var items = (cboBoundary.ItemsSource as List<BoundaryChoice>)
                ?? new List<BoundaryChoice>();
            var choice = new BoundaryChoice
            { Path = item.Path, Name = item.Name + "  (on disk)" };
            items = items.ToList();
            items.Add(choice);
            cboBoundary.ItemsSource = items;
            cboBoundary.SelectedItem = choice;
            rbLayer.IsChecked = true;
        }

        private async void OnBoundaryChanged(object sender,
                                             SelectionChangedEventArgs e)
        {
            var src = cboBoundary.SelectedItem as BoundaryChoice;
            txtBoundaryStatus.Text = "";
            UpdateEdgePolicyEnabled();
            if (src?.Layer == null) return;
            string word = GeomWord(src.Layer) ?? "feature";
            try
            {
                var (total, sel) = await QueuedTask.Run(() =>
                {
                    // counted as the map shows it (definition query applied)
                    return ((int)LayerRows.Count(src.Layer),
                            (int)src.Layer.SelectionCount);
                });
                if (_closedDown) return;
                string counts = sel > 0
                    ? string.Format("{0} of {1} {2} features selected -- "
                        + "only the selection is used.", sel, total, word)
                    : string.Format("All {0} {1} feature(s) used.",
                                    total, word);
                txtBoundaryStatus.Text = word == "polygon"
                    ? counts
                    : counts + "  Every shape touching the " + word
                        + "s is kept whole.";
            }
            catch { }
        }

        /// <summary>Edge policies only mean something when an inside
        /// exists: a polygon AOI (or the view extent). Points patterns
        /// and line/point AOIs disable the choice.</summary>
        private void UpdateEdgePolicyEnabled()
        {
            if (cboEdgePolicy == null) return;
            bool isPts = _shape == RsMath.PointsSq
                || _shape == RsMath.PointsTri;
            bool polyAoi = rbLayer.IsChecked != true
                || (cboBoundary.SelectedItem is BoundaryChoice c
                    && (c.Layer == null || GeomWord(c.Layer) == "polygon"));
            cboEdgePolicy.IsEnabled = !isPts && polyAoi;
        }

        private void OnAoiMode(object sender, RoutedEventArgs e)
        {
            if (cboBoundary == null) return;
            bool layerMode = rbLayer.IsChecked == true;
            cboBoundary.IsEnabled = layerMode;
            txtBoundaryStatus.Opacity = layerMode ? 0.85 : 0.4;
            UpdateEdgePolicyEnabled();
        }

        // =================================================================
        // output
        // =================================================================
        private void SuggestOutput()
        {
            try
            {
                string gdb = Project.Current?.DefaultGeodatabasePath;
                if (!string.IsNullOrEmpty(gdb))
                    txtOutput.Text = System.IO.Path.Combine(
                        gdb, "RepeatingShapes");
            }
            catch { }
        }

        private void OnBrowseOutput(object sender, RoutedEventArgs e)
        {
            var dlg = new SaveItemDialog
            {
                Title = "Output feature class",
                Filter = ItemFilters.FeatureClasses_All,
            };
            if (dlg.ShowDialog() != true) return;
            txtOutput.Text = dlg.FilePath;
        }

        // =================================================================
        // run (through the real GP tool -- history + environments intact)
        // =================================================================
        private string Val(string m)
        {
            return _boxes.TryGetValue(m, out var b) ? b.Text.Trim() : "";
        }

        private async void OnRun(object sender, RoutedEventArgs e)
        {
            string toolPath = ToolboxPaths.ToolPath("RepeatingShapes");
            if (toolPath == null)
            {
                txtStatus.Text = "Toolbox not found next to the add-in.";
                return;
            }
            string outFc = txtOutput.Text.Trim();
            if (outFc.Length == 0)
            {
                txtStatus.Text = "Give an output feature class.";
                return;
            }
            object boundaryArg = "";
            object extentArg = "";
            if (rbLayer.IsChecked == true)
            {
                var src = cboBoundary.SelectedItem as BoundaryChoice;
                if (src == null)
                {
                    txtStatus.Text = "Pick an area-of-interest layer "
                        + "(or switch to the view extent).";
                    return;
                }
                boundaryArg = src.GpValue;
            }
            else
            {
                var mv = MapView.Active;
                if (mv == null)
                {
                    txtStatus.Text = "No active map view.";
                    return;
                }
                extentArg = await QueuedTask.Run(
                    () => (object)mv.Extent);
            }
            bool anyMeasure = RsMath.MeasuresFor(_shape)
                .Any(m => Num(Val(m)) > 0);
            if (!anyMeasure)
            {
                txtStatus.Text = "Fill in at least one size box.";
                return;
            }

            btnRun.IsEnabled = false;
            prgRun.Visibility = Visibility.Visible;
            txtStatus.Text = "Running Repeating Shapes...";
            var sw = System.Diagnostics.Stopwatch.StartNew();
            try
            {
                // POSITIONAL order = the GP tool's parameter order
                var args = Geoprocessing.MakeValueArray(
                    _shape,
                    boundaryArg,
                    extentArg,
                    Val("area"),
                    cboAreaUnits.SelectedItem as string ?? "Hectares",
                    cboLinUnits.SelectedItem as string ?? "Meters",
                    Val("edge"),
                    Val("width"),
                    Val("height"),
                    Val("diameter"),
                    Val("radius"),
                    Val("circumference"),
                    Val("spacing"),
                    _txtOffset?.Text.Trim() ?? "50",
                    txtOrient.Text.Trim(),
                    cboEdgePolicy.SelectedItem as string
                        ?? _edgePolicies[0],
                    outFc);
                IGPResult result;
                try
                {
                    result = await Geoprocessing.ExecuteToolAsync(
                        toolPath, (IEnumerable<string>)args, null,
                        (System.Threading.CancellationToken?)null, null,
                        GPExecuteToolFlags.Default
                        | GPExecuteToolFlags.AddToHistory);
                }
                catch (NullReferenceException)
                {
                    // the flags overloads have misfired before; the
                    // plain call works (LandFacet/Bottleneck precedent)
                    result = await Geoprocessing.ExecuteToolAsync(
                        toolPath, args);
                }
                if (_closedDown) return;
                if (result.IsFailed)
                {
                    txtStatus.Text = "Failed: " + string.Join("; ",
                        result.ErrorMessages.Select(m => m.Text).Take(3));
                    return;
                }
                string summary = result.Messages
                    .Select(m => m.Text)
                    .FirstOrDefault(t => t.Contains("written")) ?? "done";
                txtStatus.Text = string.Format(CultureInfo
                    .InvariantCulture,
                    "Done in {0:0.0} s -- {1} (also recorded in "
                    + "Geoprocessing History).",
                    sw.Elapsed.TotalSeconds, summary);
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Failed: " + ex.Message;
            }
            finally
            {
                prgRun.Visibility = Visibility.Collapsed;
                btnRun.IsEnabled = true;
            }
        }

        private void OnClose(object sender, RoutedEventArgs e) => Close();

        private void OnHelp(object sender, RoutedEventArgs e)
            => WebHelp.Open("geometric-tools/repeating-shapes.html");

        // =================================================================
        // drag-and-drop from the Contents pane (Bottleneck's proven route)
        // =================================================================
        private DateTime _lastFrameworkDrop = DateTime.MinValue;

        private static DragDropEffects PickAllowedEffect(
            DragDropEffects allowed)
        {
            // reply with an effect FROM THE DRAG'S ALLOWED SET: OLE
            // intersects the answers, so "Copy" to a Move-only drag (the
            // TOC reorder drag) yields the prohibited cursor
            if ((allowed & DragDropEffects.Copy) != 0)
                return DragDropEffects.Copy;
            if ((allowed & DragDropEffects.Move) != 0)
                return DragDropEffects.Move;
            if ((allowed & DragDropEffects.Link) != 0)
                return DragDropEffects.Link;
            return DragDropEffects.None;
        }

        void ArcGIS.Desktop.Framework.DragDrop.IDropTarget.OnDragOver(
            ArcGIS.Desktop.Framework.DragDrop.DropInfo dropInfo)
        {
            dropInfo.Effects = PickAllowedEffect(
                dropInfo.DragEventArgs?.AllowedEffects
                ?? DragDropEffects.Copy);
            // leave Handled false and the framework's GLOBAL handlers
            // overwrite the effect with None (the prohibited cursor)
            dropInfo.Handled = true;
        }

        void ArcGIS.Desktop.Framework.DragDrop.IDropTarget.OnDrop(
            ArcGIS.Desktop.Framework.DragDrop.DropInfo dropInfo)
        {
            _lastFrameworkDrop = DateTime.UtcNow;
            ApplyLayerDrop(ExtractDropped(dropInfo.Data));
            dropInfo.Handled = true;
        }

        private void OnWpfDragOver(object sender, DragEventArgs e)
        {
            e.Effects = PickAllowedEffect(e.AllowedEffects);
            e.Handled = true;
        }

        private void OnWpfDrop(object sender, DragEventArgs e)
        {
            e.Handled = true;
            // the framework route already handled this physical drop
            if ((DateTime.UtcNow - _lastFrameworkDrop).TotalMilliseconds
                < 500) return;
            var lyr = ExtractFromDataObject(e.Data);
            if (lyr == null)
            {
                txtStatus.Text = "Could not read the dropped item -- "
                    + "use the dropdown or Browse instead.";
                return;
            }
            ApplyLayerDrop(lyr);
        }

        private void ApplyLayerDrop(FeatureLayer lyr)
        {
            if (lyr == null || GeomWord(lyr) == null)
            {
                txtStatus.Text = "Drop a polygon, line or point layer "
                    + "from the Contents pane.";
                return;
            }
            var items = (cboBoundary.ItemsSource as List<BoundaryChoice>
                         ?? new List<BoundaryChoice>()).ToList();
            var choice = items.FirstOrDefault(c => c.Layer == lyr);
            if (choice == null)
            {
                choice = new BoundaryChoice
                {
                    Layer = lyr,
                    Name = lyr.Name + "  (" + GeomWord(lyr) + "s)",
                };
                items.Add(choice);
                cboBoundary.ItemsSource = items;
            }
            rbLayer.IsChecked = true;
            cboBoundary.SelectedItem = choice;
            txtStatus.Text = "Area of interest: " + lyr.Name;
        }

        /// <summary>The dropped FeatureLayer, however it was packaged
        /// (MapMember, collection, URI/name string, or the Contents
        /// pane's internal TOCDragPayload probed by reflection --
        /// established in the Bottleneck window).</summary>
        private static FeatureLayer ExtractDropped(object data)
        {
            var map = MapView.Active?.Map;
            FeatureLayer FromObject(object o, int depth = 0)
            {
                if (o == null || depth > 4) return null;
                if (o is FeatureLayer fl) return fl;
                if (o is System.Collections.IEnumerable seq
                    && !(o is string))
                    foreach (var item in seq)
                    {
                        var got = FromObject(item, depth + 1);
                        if (got != null) return got;
                    }
                if (o is string s && map != null)
                {
                    var all = map.GetLayersAsFlattenedList()
                        .OfType<FeatureLayer>().ToList();
                    var byUri = all.FirstOrDefault(l => l.URI == s);
                    if (byUri != null) return byUri;
                    return all.FirstOrDefault(l => string.Equals(l.Name,
                        s.Trim(), StringComparison.OrdinalIgnoreCase));
                }
                if (!(o is string))
                    foreach (var propName in new[]
                             { "DraggedContent", "MapMemberURIs" })
                    {
                        try
                        {
                            var pi = o.GetType().GetProperty(propName);
                            if (pi == null) continue;
                            var got = FromObject(pi.GetValue(o),
                                                 depth + 1);
                            if (got != null) return got;
                        }
                        catch { }
                    }
                return null;
            }
            try { return FromObject(data); }
            catch { return null; }
        }

        private static FeatureLayer ExtractFromDataObject(IDataObject data)
        {
            if (data == null) return null;
            try
            {
                foreach (var fmt in data.GetFormats())
                {
                    object o;
                    try { o = data.GetData(fmt); }
                    catch { continue; }
                    var got = ExtractDropped(o);
                    if (got != null) return got;
                }
            }
            catch { }
            return null;
        }
    }
}
