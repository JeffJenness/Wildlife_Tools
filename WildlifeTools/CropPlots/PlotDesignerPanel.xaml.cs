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
using System.Text;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;
using ArcGIS.Core.CIM;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Catalog;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using ArcGIS.Desktop.Mapping.Events;
using Geometry = ArcGIS.Core.Geometry.Geometry;
using Polygon = ArcGIS.Core.Geometry.Polygon;

namespace WildlifeTools.CropPlots
{
    /// <summary>
    /// The content of the Plot Designer window. Gathers the parameters
    /// (anchor and direction from the map through the sketch tools), keeps
    /// a live overlay preview of the plot block on the map, and runs the
    /// Plot Designer geoprocessing tool (recorded in History) or opens it
    /// filled in. The lights turn green as each section becomes complete.
    /// </summary>
    public partial class PlotDesignerPanel : UserControl
    {
        private static readonly Brush Green = new SolidColorBrush(Color.FromRgb(0x2E, 0x8B, 0x57));
        private static readonly Brush Gray = new SolidColorBrush(Color.FromRgb(0x9E, 0x9E, 0x9E));

        public event EventHandler CloseRequested;

        private List<CropPlotActions.LayerChoice> _layers = new List<CropPlotActions.LayerChoice>();
        private SpatialReference _mapSr;
        private MapPoint _anchor;
        private bool _loading = true;
        private bool _closed;
        private bool _ratesAuto = true;
        private bool _anchorFromField;          // the anchor is the field's corner, so it follows the selection
        private readonly CropPlotActions.Overlay _overlay = new CropPlotActions.Overlay();
        private readonly DispatcherTimer _previewTimer;
        private readonly DispatcherTimer _mapTimer;    // coalesces bursts of map / selection events
        private bool _mapDirty, _selectionDirty;
        private readonly List<Action> _unhook = new List<Action>();

        public PlotDesignerPanel()
        {
            InitializeComponent();
            foreach (string u in CropPlotActions.Units)
            {
                cmbUnits.Items.Add(u);
            }
            cmbUnits.SelectedIndex = 0;
            _previewTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(300) };
            _previewTimer.Tick += (_, _) => { _previewTimer.Stop(); _ = RefreshPreviewAsync(); };
            _mapTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(250) };
            _mapTimer.Tick += (_, _) => { _mapTimer.Stop(); _ = FollowMapAsync(); };
            Loaded += OnLoaded;
        }

        // =====================================================================
        // following the map: layers added or removed, another map activated,
        // the selection changed (events arrive on any thread; they only mark
        // what changed and start a short timer on the UI thread)
        // =====================================================================
        private void HookMapEvents()
        {
            try
            {
                var t1 = LayersAddedEvent.Subscribe(_ => MapChanged(true, false));
                _unhook.Add(() => LayersAddedEvent.Unsubscribe(t1));
                var t2 = LayersRemovedEvent.Subscribe(_ => MapChanged(true, false));
                _unhook.Add(() => LayersRemovedEvent.Unsubscribe(t2));
                var t3 = ActiveMapViewChangedEvent.Subscribe(_ => MapChanged(true, true));
                _unhook.Add(() => ActiveMapViewChangedEvent.Unsubscribe(t3));
                var t4 = MapSelectionChangedEvent.Subscribe(_ => MapChanged(false, true));
                _unhook.Add(() => MapSelectionChangedEvent.Unsubscribe(t4));
            }
            catch
            {
            }
        }

        private void MapChanged(bool layers, bool selection)
        {
            if (_closed)
            {
                return;
            }
            Dispatcher.BeginInvoke(new Action(() =>
            {
                if (_closed)
                {
                    return;
                }
                _mapDirty |= layers;
                _selectionDirty |= selection;
                _mapTimer.Stop();
                _mapTimer.Start();
            }));
        }

        private async Task FollowMapAsync()
        {
            if (_closed)
            {
                return;
            }
            bool layers = _mapDirty, selection = _selectionDirty;
            _mapDirty = _selectionDirty = false;
            try
            {
                if (layers)
                {
                    _mapSr = await CropPlotActions.MapSpatialReferenceAsync();
                    await LoadLayersAsync();
                }
                else if (selection)
                {
                    await RefreshSelectionCountsAsync();
                }
                UpdateFieldNote();
                if (selection && _anchorFromField && FieldChoice != null)
                {
                    await UseFieldCornerAsync();
                }
            }
            catch (Exception ex)
            {
                txtStatus.Text = ex.Message;
            }
            Validate();
            QueuePreview();
        }

        /// <summary>Re-read the selected counts without rebuilding the
        /// combo (keeps the user's choice and the dropdown state).</summary>
        private async Task RefreshSelectionCountsAsync()
        {
            List<CropPlotActions.LayerChoice> fresh = await CropPlotActions.ListPolygonLayersAsync();
            foreach (var l in _layers)
            {
                var f = fresh.FirstOrDefault(x => ReferenceEquals(x.Layer, l.Layer) || x.Name == l.Name);
                if (f != null)
                {
                    l.SelectedCount = f.SelectedCount;
                }
            }
        }

        private void UpdateFieldNote()
        {
            var fc = FieldChoice;
            if (fc == null)
            {
                txtFieldNote.Text = _layers.Count == 0
                    ? "The active map has no polygon layers."
                    : "";
                return;
            }
            txtFieldNote.Text = fc.SelectedCount > 0
                ? fc.SelectedCount.ToString("N0", CultureInfo.CurrentCulture)
                  + (fc.SelectedCount == 1 ? " polygon is" : " polygons are") + " selected in " + fc.Name
                  + ": the field's corner is the lower-left corner of the selection's extent."
                : "No polygons are selected in " + fc.Name
                  + ": the field's corner is the lower-left corner of the whole layer's extent. Select the "
                  + "field to use just that one.";
        }

        private async void OnLoaded(object sender, RoutedEventArgs e)
        {
            Loaded -= OnLoaded;
            try
            {
                _mapSr = await CropPlotActions.MapSpatialReferenceAsync();
                await LoadLayersAsync();
                string gdb = null;
                try { gdb = Project.Current?.DefaultGeodatabasePath; } catch { }
                if (!string.IsNullOrEmpty(gdb) && string.IsNullOrWhiteSpace(txtOut.Text))
                {
                    txtOut.Text = System.IO.Path.Combine(gdb, "Plots");
                }
                if (_mapSr == null)
                {
                    txtStatus.Text = "Open a map first: the anchor point and the direction are taken from the active map.";
                }
            }
            catch (Exception ex)
            {
                txtStatus.Text = ex.Message;
            }
            _loading = false;
            RefillRates();
            UpdateFieldNote();
            HookMapEvents();
            Validate();
            QueuePreview();
        }

        internal void Shutdown()
        {
            _closed = true;
            _previewTimer.Stop();
            _mapTimer.Stop();
            foreach (Action a in _unhook)
            {
                try { a(); } catch { }
            }
            _unhook.Clear();
            _overlay.Clear();
            AxisSketchTool.Completed = null;
            AnchorSketchTool.Completed = null;
        }

        // =====================================================================
        // layers and the anchor
        // =====================================================================
        private async Task LoadLayersAsync()
        {
            var keep = (cmbField.SelectedItem as CropPlotActions.LayerChoice)?.Name;
            _layers = await CropPlotActions.ListPolygonLayersAsync();
            cmbField.Items.Clear();
            cmbField.Items.Add("(none)");
            foreach (var l in _layers)
            {
                cmbField.Items.Add(l);
            }
            int idx = 0;
            if (keep != null)
            {
                idx = Math.Max(0, cmbField.Items.IndexOf(_layers.FirstOrDefault(l => l.Name == keep)));
            }
            else
            {
                var sel = _layers.FirstOrDefault(l => l.IsSelectedInContents);
                if (sel != null)
                {
                    idx = cmbField.Items.IndexOf(sel);
                }
            }
            cmbField.SelectedIndex = idx;
        }

        private CropPlotActions.LayerChoice FieldChoice => cmbField.SelectedItem as CropPlotActions.LayerChoice;

        private async void OnRefreshLayers(object sender, RoutedEventArgs e)
        {
            try
            {
                _mapSr = await CropPlotActions.MapSpatialReferenceAsync();
                await LoadLayersAsync();
            }
            catch (Exception ex)
            {
                txtStatus.Text = ex.Message;
            }
        }

        private async void OnFieldChanged(object sender, SelectionChangedEventArgs e)
        {
            if (_loading)
            {
                return;
            }
            var fc = FieldChoice;
            if (fc != null)
            {
                // a sensible output name beside the project's data, and the
                // field's corner as the anchor until the user clicks one
                try
                {
                    string gdb = Project.Current?.DefaultGeodatabasePath;
                    if (!string.IsNullOrEmpty(gdb))
                    {
                        txtOut.Text = System.IO.Path.Combine(gdb, Legal(fc.Name) + "_Plots");
                    }
                }
                catch
                {
                }
                if (_anchor == null || _anchorFromField)
                {
                    await UseFieldCornerAsync();
                }
            }
            UpdateFieldNote();
            Validate();
            QueuePreview();
        }

        private static string Legal(string name)
        {
            var sb = new StringBuilder();
            foreach (char c in name ?? "")
            {
                sb.Append(char.IsLetterOrDigit(c) || c == '_' ? c : '_');
            }
            string s = sb.ToString();
            if (s.Length == 0 || !char.IsLetter(s[0]))
            {
                s = "F_" + s;
            }
            return s;
        }

        private async void OnFieldCorner(object sender, RoutedEventArgs e)
        {
            await UseFieldCornerAsync();
            Validate();
            QueuePreview();
        }

        private async Task UseFieldCornerAsync()
        {
            var fc = FieldChoice;
            if (fc == null)
            {
                txtStatus.Text = "Choose a field layer first.";
                return;
            }
            try
            {
                MapPoint corner = await QueuedTask.Run(() =>
                {
                    Envelope ext = null;
                    try
                    {
                        var sel = fc.Layer.GetSelection();
                        if (sel != null && sel.GetCount() > 0)
                        {
                            using (var cur = sel.Search(null, false))
                            {
                                while (cur.MoveNext())
                                {
                                    using (var f = cur.Current as ArcGIS.Core.Data.Feature)
                                    {
                                        Geometry g = f?.GetShape();
                                        if (g == null || g.IsEmpty)
                                        {
                                            continue;
                                        }
                                        ext = ext == null ? g.Extent : ext.Union(g.Extent);
                                    }
                                }
                            }
                        }
                    }
                    catch
                    {
                        ext = null;
                    }
                    if (ext == null)
                    {
                        ext = fc.Layer.QueryExtent();
                    }
                    if (ext == null)
                    {
                        return null;
                    }
                    MapPoint p = MapPointBuilderEx.CreateMapPoint(ext.XMin, ext.YMin, ext.SpatialReference);
                    if (_mapSr != null && p.SpatialReference != null && !p.SpatialReference.IsEqual(_mapSr))
                    {
                        p = (MapPoint)GeometryEngine.Instance.Project(p, _mapSr);
                    }
                    return p;
                });
                if (corner != null)
                {
                    SetAnchor(corner);
                    _anchorFromField = true;
                    txtStatus.Text = fc.SelectedCount > 0
                        ? "Anchor set to the lower-left corner of the selected polygon(s) of " + fc.Name + "."
                        : "Anchor set to the lower-left corner of " + fc.Name + " (no polygons selected).";
                }
            }
            catch (Exception ex)
            {
                txtStatus.Text = ex.Message;
            }
        }

        private void SetAnchor(MapPoint p)
        {
            _anchor = p;
            _loading = true;
            // degrees need far more decimals than meters: 8 decimals of a
            // degree is about a millimeter, 3 decimals is about 100 m
            bool geographic = p.SpatialReference != null && p.SpatialReference.IsGeographic;
            string fmt = geographic ? "0.########" : "0.####";
            txtAnchorX.Text = p.X.ToString(fmt, CultureInfo.CurrentCulture);
            txtAnchorY.Text = p.Y.ToString(fmt, CultureInfo.CurrentCulture);
            _loading = false;
        }

        private void OnClickAnchor(object sender, RoutedEventArgs e)
        {
            if (_mapSr == null)
            {
                txtStatus.Text = "Open a map first.";
                return;
            }
            txtStatus.Text = "Click the map where the first plot's back-left corner goes.";
            AnchorSketchTool.Begin(p =>
            {
                Dispatcher.BeginInvoke(new Action(() =>
                {
                    if (_closed)
                    {
                        return;
                    }
                    SetAnchor(p);
                    _anchorFromField = false;
                    txtStatus.Text = "Anchor set.";
                    Validate();
                    QueuePreview();
                }));
            });
        }

        private void OnDrawAxis(object sender, RoutedEventArgs e)
        {
            if (_mapSr == null)
            {
                txtStatus.Text = "Open a map first.";
                return;
            }
            txtStatus.Text = "Draw a line on the map in the plot direction (hold Ctrl at the end to snap to 45 degrees).";
            AxisSketchTool.Begin((a, b, ctrl) =>
            {
                double bearing;
                try
                {
                    bearing = CropPlotActions.Bearing(a, b);
                }
                catch
                {
                    return;
                }
                if (ctrl)
                {
                    bearing = CropPlotActions.SnapBearing(bearing, 0.0);
                }
                Dispatcher.BeginInvoke(new Action(() =>
                {
                    if (_closed)
                    {
                        return;
                    }
                    txtBearing.Text = bearing.ToString("0.0#", CultureInfo.CurrentCulture);
                    txtAxisNote.Text = ctrl ? "(snapped to 45 degrees)" : "(from the drawn line)";
                    txtStatus.Text = "Direction set from the drawn line.";
                    Validate();
                    QueuePreview();
                }));
            });
        }

        // =====================================================================
        // validation and the lights
        // =====================================================================
        private static bool Num(TextBox box, out double v)
        {
            string s = (box.Text ?? "").Trim();
            return double.TryParse(s, NumberStyles.Float, CultureInfo.CurrentCulture, out v)
                   || double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out v);
        }

        private static bool Count(TextBox box, out int n)
        {
            n = 0;
            return int.TryParse((box.Text ?? "").Trim(), out n) && n >= 1;
        }

        private static List<double> Rates(string text)
        {
            var list = new List<double>();
            foreach (string raw in (text ?? "").Replace(';', ',').Split(','))
            {
                string s = raw.Trim();
                if (s.Length == 0)
                {
                    continue;
                }
                if (!double.TryParse(s, NumberStyles.Float, CultureInfo.CurrentCulture, out double v)
                    && !double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out v))
                {
                    return null;
                }
                list.Add(v);
            }
            return list;
        }

        private bool AnchorOk => Num(txtAnchorX, out _) && Num(txtAnchorY, out _);
        private bool SizeOk => Num(txtLong, out double l) && l > 0 && Num(txtShort, out double s) && s > 0;
        private bool DirectionOk => Num(txtBearing, out _);
        private bool ArrangementOk
        {
            get
            {
                if (!Count(txtNLong, out int a) || !Count(txtNShort, out int b) || !Count(txtNReps, out _))
                {
                    return false;
                }
                var r = Rates(txtRates.Text);
                return r != null && r.Count == a * b;
            }
        }
        private bool LayoutOk => Num(txtGap, out double g) && g >= 0;
        private bool OutputOk => !string.IsNullOrWhiteSpace(txtOut.Text);

        /// <summary>Color a light and give it a hover explanation of why it
        /// is green or gray, so the user knows what is still needed.</summary>
        private static void Light(System.Windows.Shapes.Ellipse light, bool ok, string whenGreen, string whenGray)
        {
            light.Fill = ok ? Green : Gray;
            light.ToolTip = (ok ? "Green: " : "Gray: ") + (ok ? whenGreen : whenGray);
        }

        private string ArrangementWhy()
        {
            if (!Count(txtNLong, out int a) || !Count(txtNShort, out int b))
            {
                return "plots along the direction and plots across must both be whole numbers of 1 or more.";
            }
            if (!Count(txtNReps, out _))
            {
                return "replications must be a whole number of 1 or more.";
            }
            var r = Rates(txtRates.Text);
            if (r == null)
            {
                return "one of the rate values is not a number; separate the values with commas.";
            }
            if (r.Count != a * b)
            {
                return r.Count.ToString(CultureInfo.CurrentCulture) + " rate value" + (r.Count == 1 ? "" : "s")
                       + " given, but each replication has " + (a * b).ToString(CultureInfo.CurrentCulture)
                       + " plots (" + a + " along x " + b + " across): give exactly one rate per plot.";
            }
            return "";
        }

        private void Validate()
        {
            if (_loading)
            {
                return;
            }
            var fc = FieldChoice;
            Light(lightField, fc != null,
                "the field layer is " + (fc?.Name ?? "") + ". It supplies the coordinate system and, through "
                + "'Use the field's corner', a default anchor.",
                "no field layer is chosen. This is optional: type or click an anchor point instead, and the "
                + "output takes the map's coordinate system.");
            Num(txtLong, out double lv);
            Num(txtShort, out double sv);
            Light(lightSize, SizeOk,
                "each plot is " + txtLong.Text.Trim() + " by " + txtShort.Text.Trim() + " "
                + ((cmbUnits.SelectedItem as string) ?? "Meters").ToLowerInvariant() + " (length along the "
                + "direction, width across it).",
                "enter a positive plot length and a positive plot width.");
            Light(lightDirection, DirectionOk,
                "the plots' long edges run at a bearing of " + txtBearing.Text.Trim()
                + " degrees clockwise from north.",
                "enter the plot direction in degrees clockwise from north, or draw it on the map.");
            Light(lightAnchor, AnchorOk,
                "the back-left corner of the first plot is at X " + txtAnchorX.Text.Trim() + ", Y "
                + txtAnchorY.Text.Trim() + (_anchorFromField ? " (the field's corner; it follows the selection)."
                    : _anchor != null ? " (clicked on the map)." : " (typed)."),
                "click the anchor on the map, use the field's corner, or type its X and Y. Without an anchor, a "
                + "field layer's lower-left corner is used.");
            string why = ArrangementWhy();
            Count(txtNLong, out int nl);
            Count(txtNShort, out int ns);
            Count(txtNReps, out int nr);
            Light(lightArrangement, why.Length == 0,
                (nl * ns).ToString(CultureInfo.CurrentCulture) + " plots per replication (" + nl + " along x " + ns
                + " across), " + nr + " replication" + (nr == 1 ? "" : "s") + "; the " + (nl * ns)
                + " rate values are shuffled anew for each replication.",
                why);
            Num(txtGap, out double gap);
            Light(lightLayout, LayoutOk,
                "replications are laid " + (cmbLayout.SelectedIndex != 1 ? "side by side (across the plot direction)"
                    : "end to end (along the plot direction)") + " with a gap of " + txtGap.Text.Trim() + ".",
                "enter a gap between replications of 0 or more.");
            Light(lightOutput, OutputOk,
                "the plot polygons go to " + txtOut.Text.Trim()
                + (chkPoints.IsChecked == true ? ", plus the center points" : "")
                + (chkLines.IsChecked == true ? ", plus the centerlines" : "") + ".",
                "choose where to save the plot polygons (type a path or click Browse).");
            var missing = new List<string>();
            if (_mapSr == null) missing.Add("an open map");
            if (!(AnchorOk || fc != null)) missing.Add("an anchor point or a field layer");
            if (!SizeOk) missing.Add("the plot size");
            if (!DirectionOk) missing.Add("the plot direction");
            if (why.Length > 0) missing.Add("the arrangement and rates");
            if (!LayoutOk) missing.Add("the replication gap");
            if (!OutputOk) missing.Add("an output location");
            btnCreate.IsEnabled = missing.Count == 0;
            btnCreate.ToolTip = missing.Count == 0
                ? "Run the Plot Designer tool with these settings. The run is recorded in the geoprocessing History, "
                  + "so it can be repeated or copied as Python."
                : "Still needed: " + string.Join(", ", missing) + ". Hover over a gray light for details.";
        }

        private void OnAnyChanged(object sender, RoutedEventArgs e)
        {
            if (_loading)
            {
                return;
            }
            if (sender == txtRates)
            {
                _ratesAuto = false;
            }
            if (sender == txtAnchorX || sender == txtAnchorY)
            {
                _anchor = null;      // typed coordinates replace the clicked point
                _anchorFromField = false;
            }
            Validate();
            QueuePreview();
        }

        private void OnCountChanged(object sender, TextChangedEventArgs e)
        {
            if (_loading)
            {
                return;
            }
            RefillRates();
            Validate();
            QueuePreview();
        }

        /// <summary>Keep the rate box in step with the plot count, as the
        /// CropCentric dialog did: existing values are kept, missing ones
        /// become 0, extra ones are dropped.</summary>
        private void RefillRates()
        {
            int n = (Count(txtNLong, out int a) ? a : 0) * (Count(txtNShort, out int b) ? b : 0);
            txtPerRep.Text = n.ToString(CultureInfo.CurrentCulture);
            if (n <= 0)
            {
                return;
            }
            var have = (txtRates.Text ?? "").Replace(';', ',').Split(',').Select(s => s.Trim())
                .Where(s => s.Length > 0).ToList();
            if (have.Count == n && !_ratesAuto)
            {
                return;
            }
            var vals = new List<string>();
            for (int i = 0; i < n; i++)
            {
                vals.Add(i < have.Count ? have[i] : "0");
            }
            bool wasAuto = _ratesAuto;
            _loading = true;
            txtRates.Text = string.Join(", ", vals);
            _loading = false;
            _ratesAuto = wasAuto;
        }

        // =====================================================================
        // the preview
        // =====================================================================
        private void OnPreviewToggle(object sender, RoutedEventArgs e)
        {
            if (_loading)
            {
                return;
            }
            if (chkPreview.IsChecked != true)
            {
                _overlay.Clear();
            }
            else
            {
                QueuePreview();
            }
        }

        private void QueuePreview()
        {
            if (_loading || _closed)
            {
                return;
            }
            _previewTimer.Stop();
            _previewTimer.Start();
        }

        private bool TryAnchor(out MapPoint p)
        {
            p = null;
            if (_mapSr == null || !Num(txtAnchorX, out double x) || !Num(txtAnchorY, out double y))
            {
                return false;
            }
            // the clicked point itself while the boxes still show it (the
            // text is rounded to the display precision, hence the tolerance)
            p = _anchor != null && Math.Abs(_anchor.X - x) < 1e-6 && Math.Abs(_anchor.Y - y) < 1e-6
                ? _anchor : MapPointBuilderEx.CreateMapPoint(x, y, _mapSr);
            return true;
        }

        private async Task RefreshPreviewAsync()
        {
            if (_closed || chkPreview.IsChecked != true)
            {
                return;
            }
            if (!TryAnchor(out MapPoint anchor) || !SizeOk || !DirectionOk || !Count(txtNLong, out int nLong)
                || !Count(txtNShort, out int nShort) || !Count(txtNReps, out int nReps) || !LayoutOk)
            {
                _overlay.Clear();
                return;
            }
            Num(txtLong, out double longV);
            Num(txtShort, out double shortV);
            Num(txtBearing, out double bearing);
            Num(txtGap, out double gap);
            double k = CropPlotActions.MetersPer(cmbUnits.SelectedItem as string);
            bool side = cmbLayout.SelectedIndex != 1;
            if ((long)nLong * nShort * nReps > 5000)
            {
                txtStatus.Text = "Preview skipped: more than 5,000 plots.";
                _overlay.Clear();
                return;
            }
            SpatialReference sr = _mapSr;
            await _overlay.ShowAsync(() =>
            {
                var list = new List<(Geometry, CIMSymbolReference)>();
                List<Polygon> polys = CropPlotActions.PlotPolygons(anchor, sr, longV * k, shortV * k, bearing,
                    nLong, nShort, nReps, side, gap * k);
                CIMSymbolReference fill = CropPlotActions.FillSymbol(46, 139, 87);
                foreach (Polygon p in polys)
                {
                    list.Add(((Geometry)p, fill));
                }
                // the anchor and the direction: a marker and an arrow-less line
                list.Add(((Geometry)anchor, CropPlotActions.PointSymbol(200, 40, 40, 10)));
                if (polys.Count > 0)
                {
                    Polygon first = polys[0];
                    var pts = first.Points.Take(2).ToList();
                    if (pts.Count == 2)
                    {
                        list.Add(((Geometry)PolylineBuilderEx.CreatePolyline(pts, sr),
                            CropPlotActions.LineSymbol(200, 40, 40, 3)));
                    }
                }
                return list;
            });
        }

        // =====================================================================
        // run / open / browse / close
        // =====================================================================
        private IReadOnlyList<string> Args()
        {
            Num(txtLong, out double longV);
            Num(txtShort, out double shortV);
            Num(txtBearing, out double bearing);
            Num(txtGap, out double gap);
            Count(txtNLong, out int nLong);
            Count(txtNShort, out int nShort);
            Count(txtNReps, out int nReps);
            double? ax = Num(txtAnchorX, out double x) ? x : (double?)null;
            double? ay = Num(txtAnchorY, out double y) ? y : (double?)null;
            int? seed = int.TryParse((txtSeed.Text ?? "").Trim(), out int sv) ? sv : (int?)null;
            string outFc = (txtOut.Text ?? "").Trim();
            string pts = chkPoints.IsChecked == true ? Sibling(outFc, "_Points") : "";
            string lines = chkLines.IsChecked == true ? Sibling(outFc, "_Lines") : "";
            string wkt = ax.HasValue && _mapSr != null ? _mapSr.Wkt : "";
            return CropPlotActions.PlotDesignerArgs(outFc, longV, shortV, cmbUnits.SelectedItem as string, bearing,
                nLong, nShort, txtRates.Text, nReps, FieldChoice?.Layer, ax, ay, wkt,
                cmbLayout.SelectedIndex != 1, gap, pts, lines, seed);
        }

        private static string Sibling(string fc, string suffix)
        {
            if (string.IsNullOrWhiteSpace(fc))
            {
                return "";
            }
            bool shp = fc.EndsWith(".shp", StringComparison.OrdinalIgnoreCase);
            string stem = shp ? fc.Substring(0, fc.Length - 4) : fc;
            return stem + suffix + (shp ? ".shp" : "");
        }

        private async void OnCreate(object sender, RoutedEventArgs e)
        {
            btnCreate.IsEnabled = false;
            txtStatus.Text = "Creating the plots...";
            try
            {
                string done = await CropPlotActions.RunPlotDesignerAsync(Args());
                txtStatus.Text = done + " Written to " + (txtOut.Text ?? "").Trim() + ".";
                _overlay.Clear();
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Could not create the plots: " + ex.Message;
            }
            Validate();
        }

        private void OnOpenTool(object sender, RoutedEventArgs e)
        {
            try
            {
                CropPlotActions.OpenPlotDesignerTool(Args());
            }
            catch (Exception ex)
            {
                txtStatus.Text = ex.Message;
            }
        }

        private void OnBrowse(object sender, RoutedEventArgs e)
        {
            var dlg = new SaveItemDialog
            {
                Title = "Save the plot polygons as",
                DefaultExt = "",
                Filter = ItemFilters.FeatureClasses_All,
            };
            try { dlg.InitialLocation = Project.Current?.DefaultGeodatabasePath; } catch { }
            if (dlg.ShowDialog() == true)
            {
                txtOut.Text = NormalizeSaveTarget(dlg.FilePath);
            }
        }

        /// <summary>A browse-dialog target safe for the GP tools: a folder
        /// target gets .shp (an extensionless name in a folder would be a
        /// legacy coverage), a geodatabase name loses any extension.</summary>
        internal static string NormalizeSaveTarget(string raw)
        {
            if (string.IsNullOrWhiteSpace(raw))
            {
                return "";
            }
            string path = raw.Trim().Replace('/', '\\').TrimEnd('\\');
            int cut = path.LastIndexOf('\\');
            string ws = cut >= 0 ? path.Substring(0, cut) : "";
            string name = cut >= 0 ? path.Substring(cut + 1) : path;
            string low = ws.ToLowerInvariant();
            bool gdb = low.EndsWith(".gdb") || low.Contains(".gdb\\") || low.EndsWith(".sde") || low.Contains(".sde\\");
            if (gdb)
            {
                int dot = name.IndexOf('.');
                if (dot > 0)
                {
                    name = name.Substring(0, dot);
                }
                name = Legal(name);
            }
            else if (!name.EndsWith(".shp", StringComparison.OrdinalIgnoreCase))
            {
                name += ".shp";
            }
            return string.IsNullOrEmpty(ws) ? name : ws + "\\" + name;
        }

        private void OnHelp(object sender, RoutedEventArgs e) => CropPlotActions.OpenPlotDesignerHelp();

        private void OnClose(object sender, RoutedEventArgs e) => CloseRequested?.Invoke(this, EventArgs.Empty);
    }
}
