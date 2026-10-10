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
using System.Windows.Media;
using System.Windows.Threading;
using ArcGIS.Core.CIM;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Catalog;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Layouts.Events;
using ArcGIS.Desktop.Mapping;
using ArcGIS.Desktop.Mapping.Events;
using Geometry = ArcGIS.Core.Geometry.Geometry;

namespace WildlifeTools.CropPlots
{
    /// <summary>
    /// The content of the Create Array along Axes window. The source is the
    /// selected graphic (or a layer's selected features), the axes are drawn
    /// on the map, a live overlay shows the copies, and the Create Array
    /// along Axes geoprocessing tool makes them (recorded in History), as a
    /// feature class and/or as graphics added to the map.
    /// </summary>
    public partial class ArrayAlongAxesPanel : UserControl
    {
        private static readonly Brush Green = new SolidColorBrush(Color.FromRgb(0x2E, 0x8B, 0x57));
        private static readonly Brush Gray = new SolidColorBrush(Color.FromRgb(0x9E, 0x9E, 0x9E));

        public event EventHandler CloseRequested;

        private List<CropPlotActions.LayerChoice> _layers = new List<CropPlotActions.LayerChoice>();
        private CropPlotActions.SourceInfo _graphic;
        private List<Geometry> _sources = new List<Geometry>();
        private SpatialReference _mapSr;
        private bool _loading = true;
        private bool _closed;
        private readonly CropPlotActions.Overlay _overlay = new CropPlotActions.Overlay();
        private readonly DispatcherTimer _previewTimer;
        private readonly DispatcherTimer _mapTimer;    // coalesces bursts of map / selection events
        private readonly List<Action> _unhook = new List<Action>();

        public ArrayAlongAxesPanel()
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
        // the feature selection or the graphic selection changed
        // =====================================================================
        private void HookMapEvents()
        {
            try
            {
                var t1 = LayersAddedEvent.Subscribe(_ => MapChanged());
                _unhook.Add(() => LayersAddedEvent.Unsubscribe(t1));
                var t2 = LayersRemovedEvent.Subscribe(_ => MapChanged());
                _unhook.Add(() => LayersRemovedEvent.Unsubscribe(t2));
                var t3 = ActiveMapViewChangedEvent.Subscribe(_ => MapChanged());
                _unhook.Add(() => ActiveMapViewChangedEvent.Unsubscribe(t3));
                var t4 = MapSelectionChangedEvent.Subscribe(_ => MapChanged());
                _unhook.Add(() => MapSelectionChangedEvent.Unsubscribe(t4));
                var t5 = ElementEvent.Subscribe(a =>
                {
                    if (a != null && a.Hint == ElementEventHint.SelectionChanged)
                    {
                        MapChanged();
                    }
                });
                _unhook.Add(() => ElementEvent.Unsubscribe(t5));
            }
            catch
            {
            }
        }

        private void MapChanged()
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
            try
            {
                await ReadMapAsync();
            }
            catch (Exception ex)
            {
                txtStatus.Text = ex.Message;
            }
            Validate();
            QueuePreview();
        }

        private async void OnLoaded(object sender, RoutedEventArgs e)
        {
            Loaded -= OnLoaded;
            try
            {
                string gdb = null;
                try { gdb = Project.Current?.DefaultGeodatabasePath; } catch { }
                if (!string.IsNullOrEmpty(gdb) && string.IsNullOrWhiteSpace(txtOut.Text))
                {
                    txtOut.Text = System.IO.Path.Combine(gdb, "Array_along_axes");
                }
                await ReadMapAsync();
            }
            catch (Exception ex)
            {
                txtStatus.Text = ex.Message;
            }
            _loading = false;
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
        }

        // =====================================================================
        // the source
        // =====================================================================
        private async Task ReadMapAsync()
        {
            _mapSr = await CropPlotActions.MapSpatialReferenceAsync();
            _graphic = await CropPlotActions.SelectedGraphicAsync();
            txtSourceNote.Text = _graphic?.Describe ?? "";
            string keepSrc = (cmbSourceLayer.SelectedItem as CropPlotActions.LayerChoice)?.Name;
            string keepClip = (cmbClip.SelectedItem as CropPlotActions.LayerChoice)?.Name;
            _layers = await CropPlotActions.ListPolygonLayersAsync();
            bool was = _loading;
            _loading = true;
            cmbSourceLayer.Items.Clear();
            cmbClip.Items.Clear();
            cmbClip.Items.Add("(none)");
            foreach (var l in _layers)
            {
                if (l.SelectedCount > 0)
                {
                    cmbSourceLayer.Items.Add(l);
                    cmbClip.Items.Add(l);
                }
            }
            cmbSourceLayer.SelectedIndex = Math.Max(0, cmbSourceLayer.Items.IndexOf(
                _layers.FirstOrDefault(l => l.Name == keepSrc && l.SelectedCount > 0)));
            cmbClip.SelectedIndex = Math.Max(0, cmbClip.Items.IndexOf(
                _layers.FirstOrDefault(l => l.Name == keepClip && l.SelectedCount > 0)));
            if (!was)
            {
                // first look: prefer whichever source exists
                if (_graphic != null && _graphic.Ok)
                {
                    rbGraphic.IsChecked = true;
                }
                else if (cmbSourceLayer.Items.Count > 0)
                {
                    rbLayer.IsChecked = true;
                }
            }
            _loading = was;
            await ReadSourcesAsync();
        }

        private async Task ReadSourcesAsync()
        {
            _sources = new List<Geometry>();
            if (rbGraphic.IsChecked == true)
            {
                if (_graphic != null && _graphic.Ok)
                {
                    _sources.Add(_graphic.Geometry);
                }
            }
            else if (cmbSourceLayer.SelectedItem is CropPlotActions.LayerChoice lc)
            {
                _sources = await CropPlotActions.SelectedFeatureGeometriesAsync(lc.Layer);
                txtSourceNote.Text = _sources.Count.ToString("N0", CultureInfo.CurrentCulture)
                                     + " selected feature(s) of " + lc.Name + ".";
            }
            if (_mapSr == null)
            {
                txtStatus.Text = "Open a map first.";
            }
        }

        private async void OnRefreshSource(object sender, RoutedEventArgs e)
        {
            try
            {
                await ReadMapAsync();
            }
            catch (Exception ex)
            {
                txtStatus.Text = ex.Message;
            }
            Validate();
            QueuePreview();
        }

        private async void OnSourceChanged(object sender, RoutedEventArgs e)
        {
            if (_loading)
            {
                return;
            }
            try
            {
                if (rbGraphic.IsChecked == true)
                {
                    _graphic = await CropPlotActions.SelectedGraphicAsync();
                    txtSourceNote.Text = _graphic?.Describe ?? "";
                }
                await ReadSourcesAsync();
            }
            catch (Exception ex)
            {
                txtStatus.Text = ex.Message;
            }
            Validate();
            QueuePreview();
        }

        // =====================================================================
        // the axes
        // =====================================================================
        private void OnDrawAxis1(object sender, RoutedEventArgs e) => DrawAxis(txtBearing1, 0.0, "Axis 1");

        private void OnDrawAxis2(object sender, RoutedEventArgs e)
        {
            double reference = Num(txtBearing1, out double b1) ? b1 : 0.0;
            DrawAxis(txtBearing2, reference, "Axis 2");
        }

        private void DrawAxis(TextBox target, double reference, string what)
        {
            if (_mapSr == null)
            {
                txtStatus.Text = "Open a map first.";
                return;
            }
            txtStatus.Text = "Draw " + what + " on the map (hold Ctrl at the end to snap to 45 degrees"
                             + (reference != 0.0 ? " from axis 1" : "") + ").";
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
                    bearing = CropPlotActions.SnapBearing(bearing, reference);
                }
                Dispatcher.BeginInvoke(new Action(() =>
                {
                    if (_closed)
                    {
                        return;
                    }
                    target.Text = bearing.ToString("0.0#", CultureInfo.CurrentCulture);
                    txtStatus.Text = what + " set from the drawn line" + (ctrl ? " (snapped)." : ".");
                    Validate();
                    QueuePreview();
                }));
            });
        }

        private void OnAxis2Toggle(object sender, RoutedEventArgs e)
        {
            bool on = chkAxis2.IsChecked == true;
            pnlAxis2.IsEnabled = on;
            pnlAxis2.Opacity = on ? 1.0 : 0.6;
            if (_loading)
            {
                return;
            }
            Validate();
            QueuePreview();
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
            return int.TryParse((box.Text ?? "").Trim(), out n) && n >= 0;
        }

        private bool SourceOk => _sources.Count > 0;
        private bool Axis1Ok => Num(txtBearing1, out _) && Num(txtGap1, out double g) && g >= 0
                                && Count(txtForward, out _) && Count(txtBack, out _);
        private bool Axis2On => chkAxis2.IsChecked == true;
        private bool Axis2Ok => !Axis2On || ((string.IsNullOrWhiteSpace(txtBearing2.Text) || Num(txtBearing2, out _))
                                             && Num(txtGap2, out double g) && g >= 0
                                             && Count(txtRight, out _) && Count(txtLeft, out _));
        private bool SaveFc => chkFc.IsChecked == true;
        private bool AsGraphics => chkGraphics.IsChecked == true;
        private bool OutputOk => (SaveFc && !string.IsNullOrWhiteSpace(txtOut.Text)) || (!SaveFc && AsGraphics);

        /// <summary>Color a light and give it a hover explanation of why it
        /// is green or gray, so the user knows what is still needed.</summary>
        private static void Light(System.Windows.Shapes.Ellipse light, bool ok, string whenGreen, string whenGray)
        {
            light.Fill = ok ? Green : Gray;
            light.ToolTip = (ok ? "Green: " : "Gray: ") + (ok ? whenGreen : whenGray);
        }

        private string Axis1Why()
        {
            if (!Num(txtBearing1, out _)) return "enter the axis 1 bearing in degrees, or draw it on the map.";
            if (!Num(txtGap1, out double g) || g < 0) return "enter a separation of 0 or more.";
            if (!Count(txtForward, out _) || !Count(txtBack, out _))
                return "copies forward and copies backward must be whole numbers of 0 or more.";
            return "";
        }

        private string Axis2Why()
        {
            if (!Axis2On) return "optional: check Axis 2 to copy the whole row along a second axis as well.";
            if (!string.IsNullOrWhiteSpace(txtBearing2.Text) && !Num(txtBearing2, out _))
                return "the axis 2 bearing is not a number; leave it empty for axis 1 plus 90 degrees, or draw it.";
            if (!Num(txtGap2, out double g) || g < 0) return "enter a separation of 0 or more.";
            if (!Count(txtRight, out _) || !Count(txtLeft, out _))
                return "copies to the right and to the left must be whole numbers of 0 or more.";
            return "";
        }

        private void Validate()
        {
            if (_loading)
            {
                return;
            }
            Light(lightSource, SourceOk,
                (rbGraphic.IsChecked == true ? "the selected graphic on the map" : "the "
                    + _sources.Count.ToString("N0", CultureInfo.CurrentCulture) + " selected feature(s) of "
                    + ((cmbSourceLayer.SelectedItem as CropPlotActions.LayerChoice)?.Name ?? "")) + " will be copied.",
                rbGraphic.IsChecked == true
                    ? "select exactly one polygon, line or point graphic on the map (the window notices the "
                      + "selection by itself; the refresh button re-reads it). "
                      + (_graphic?.Describe ?? "")
                    : "choose a polygon layer that has selected features; only layers with a selection are listed.");
            string w1 = Axis1Why();
            Count(txtForward, out int fwd);
            Count(txtBack, out int back);
            Light(lightAxis1, w1.Length == 0,
                "axis 1 runs at " + txtBearing1.Text.Trim() + " degrees with a separation of " + txtGap1.Text.Trim()
                + " " + ((cmbUnits.SelectedItem as string) ?? "Meters").ToLowerInvariant() + ": " + fwd + " cop"
                + (fwd == 1 ? "y" : "ies") + " forward and " + back + " backward" + (txtStep1.Text.Length > 0
                    ? " (" + txtStep1.Text + ")." : "."),
                w1);
            string w2 = Axis2Why();
            Count(txtRight, out int right);
            Count(txtLeft, out int left);
            Light(lightAxis2, Axis2On && w2.Length == 0,
                "axis 2 runs at " + (string.IsNullOrWhiteSpace(txtBearing2.Text) ? "axis 1 plus 90" : txtBearing2.Text.Trim())
                + " degrees with a separation of " + txtGap2.Text.Trim() + ": " + right + " cop" + (right == 1 ? "y" : "ies")
                + " to the right and " + left + " to the left" + (txtStep2.Text.Length > 0 ? " (" + txtStep2.Text + ")." : "."),
                w2);
            var clip = cmbClip.SelectedItem as CropPlotActions.LayerChoice;
            Light(lightClip, clip != null,
                "every copy is cut to the " + (clip?.SelectedCount ?? 0).ToString("N0", CultureInfo.CurrentCulture)
                + " selected polygon(s) of " + (clip?.Name ?? "") + ", and copies entirely outside them are dropped.",
                "optional: no clipping. Choose a polygon layer with a selection to cut the copies to its selected polygons.");
            Light(lightOutput, OutputOk,
                (SaveFc ? "the copies are saved to " + txtOut.Text.Trim() : "")
                + (SaveFc && AsGraphics ? " and " : "")
                + (AsGraphics ? "the copies are added to the map as graphics" : "") + ".",
                SaveFc ? "give the feature class to save the copies to (type a path or click Browse)."
                       : "check 'Save as a feature class' and give a path, or check 'Add to the map as graphics', or both.");
            var missing = new List<string>();
            if (_mapSr == null) missing.Add("an open map");
            if (!SourceOk) missing.Add("something to copy");
            if (w1.Length > 0) missing.Add("axis 1");
            if (Axis2On && w2.Length > 0) missing.Add("axis 2");
            if (!OutputOk) missing.Add("an output");
            btnCreate.IsEnabled = missing.Count == 0;
            btnCreate.ToolTip = missing.Count == 0
                ? "Run the Create Array along Axes tool with these settings. The run is recorded in the geoprocessing History."
                : "Still needed: " + string.Join(", ", missing) + ". Hover over a gray light for details.";
            txtOut.IsEnabled = SaveFc;
        }

        private void OnAnyChanged(object sender, RoutedEventArgs e)
        {
            if (_loading)
            {
                return;
            }
            Validate();
            QueuePreview();
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

        private bool ReadAxes(out double b1, out double gap1M, out int fwd, out int back, out double? b2,
            out double gap2M, out int right, out int left)
        {
            b1 = gap1M = gap2M = 0;
            fwd = back = right = left = 0;
            b2 = null;
            if (!Axis1Ok || !Axis2Ok)
            {
                return false;
            }
            double k = CropPlotActions.MetersPer(cmbUnits.SelectedItem as string);
            Num(txtBearing1, out b1);
            Num(txtGap1, out double g1);
            gap1M = g1 * k;
            Count(txtForward, out fwd);
            Count(txtBack, out back);
            if (Axis2On)
            {
                if (Num(txtBearing2, out double bb2))
                {
                    b2 = bb2;
                }
                Num(txtGap2, out double g2);
                gap2M = g2 * k;
                Count(txtRight, out right);
                Count(txtLeft, out left);
            }
            return true;
        }

        private async Task RefreshPreviewAsync()
        {
            if (_closed || chkPreview.IsChecked != true)
            {
                return;
            }
            if (_mapSr == null || !SourceOk || !ReadAxes(out double b1, out double gap1, out int fwd, out int back,
                    out double? b2, out double gap2, out int right, out int left))
            {
                _overlay.Clear();
                txtStep1.Text = "";
                txtStep2.Text = "";
                return;
            }
            long total = (long)(fwd + back + 1) * (right + left + 1) * _sources.Count;
            if (total > 5000)
            {
                txtStatus.Text = "Preview skipped: more than 5,000 copies.";
                _overlay.Clear();
                return;
            }
            var sources = _sources.ToList();
            SpatialReference sr = _mapSr;
            try
            {
                var (s1, s2) = await ArcGIS.Desktop.Framework.Threading.Tasks.QueuedTask.Run(
                    () => CropPlotActions.ArraySteps(sources, sr, b1, gap1, b2, gap2));
                txtStep1.Text = "step " + s1.ToString("N1", CultureInfo.CurrentCulture) + " m";
                txtStep2.Text = Axis2On ? "step " + s2.ToString("N1", CultureInfo.CurrentCulture) + " m" : "";
            }
            catch
            {
            }
            await _overlay.ShowAsync(() =>
            {
                var list = new List<(Geometry, CIMSymbolReference)>();
                List<Geometry> copies = CropPlotActions.ArrayCopies(sources, sr, b1, gap1, fwd, back, b2, gap2,
                    right, left);
                foreach (Geometry g in copies)
                {
                    list.Add((g, CropPlotActions.PreviewSymbol(g, 31, 119, 180)));
                }
                foreach (Geometry g in sources)
                {
                    list.Add((g, CropPlotActions.PreviewSymbol(g, 46, 139, 87)));
                }
                return list;
            });
        }

        // =====================================================================
        // run / open / browse / close
        // =====================================================================
        private IReadOnlyList<string> Args(string outFc, bool toGraphicsInTool)
        {
            ReadAxes(out double b1, out _, out int fwd, out int back, out double? b2, out _, out int right, out int left);
            Num(txtGap1, out double g1);
            Num(txtGap2, out double g2);
            FeatureLayer inLayer = rbLayer.IsChecked == true
                ? (cmbSourceLayer.SelectedItem as CropPlotActions.LayerChoice)?.Layer : null;
            string json = rbGraphic.IsChecked == true ? _graphic?.Json : null;
            FeatureLayer clip = (cmbClip.SelectedItem as CropPlotActions.LayerChoice)?.Layer;
            return CropPlotActions.CreateArrayArgs(inLayer, b1, g1, cmbUnits.SelectedItem as string, fwd, back, outFc,
                Axis2On ? b2 : null, Axis2On ? g2 : 0.0, Axis2On ? right : 0, Axis2On ? left : 0, clip,
                toGraphicsInTool, json);
        }

        private async void OnCreate(object sender, RoutedEventArgs e)
        {
            btnCreate.IsEnabled = false;
            txtStatus.Text = "Creating the array...";
            string outFc = SaveFc ? (txtOut.Text ?? "").Trim() : "";
            bool temp = false;
            try
            {
                if (!SaveFc)
                {
                    // graphics only: the tool writes a scratch feature class
                    // in the project's geodatabase, which is removed below
                    string gdb = Project.Current?.DefaultGeodatabasePath
                                 ?? throw new InvalidOperationException("No project geodatabase for the scratch output.");
                    outFc = System.IO.Path.Combine(gdb, "Array_scratch_" + DateTime.Now.ToString("HHmmss"));
                    temp = true;
                }
                string done = await CropPlotActions.RunCreateArrayAsync(Args(outFc, false), addOutputsToMap: !temp);
                string note = done;
                if (AsGraphics)
                {
                    int n = await CropPlotActions.AddGraphicsFromFeatureClassAsync(outFc, "Array along axes");
                    note += " " + n.ToString("N0", CultureInfo.CurrentCulture) + " graphic(s) added to the map.";
                }
                if (temp)
                {
                    try
                    {
                        await Geoprocessing.ExecuteToolAsync("management.Delete", Geoprocessing.MakeValueArray(outFc),
                            null, (System.Threading.CancellationToken?)null, null, GPExecuteToolFlags.None);
                    }
                    catch
                    {
                    }
                }
                else
                {
                    note += " Written to " + outFc + ".";
                }
                txtStatus.Text = note;
                _overlay.Clear();
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Could not create the array: " + ex.Message;
            }
            Validate();
        }

        private void OnOpenTool(object sender, RoutedEventArgs e)
        {
            try
            {
                CropPlotActions.OpenCreateArrayTool(Args(SaveFc ? (txtOut.Text ?? "").Trim() : "", AsGraphics));
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
                Title = "Save the array as",
                DefaultExt = "",
                Filter = ItemFilters.FeatureClasses_All,
            };
            try { dlg.InitialLocation = Project.Current?.DefaultGeodatabasePath; } catch { }
            if (dlg.ShowDialog() == true)
            {
                txtOut.Text = PlotDesignerPanel.NormalizeSaveTarget(dlg.FilePath);
                chkFc.IsChecked = true;
            }
        }

        private void OnHelp(object sender, RoutedEventArgs e) => CropPlotActions.OpenCreateArrayHelp();

        private void OnClose(object sender, RoutedEventArgs e) => CloseRequested?.Invoke(this, EventArgs.Empty);
    }
}
