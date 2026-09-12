using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;
using WildlifeTools.PolarPlots;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// The Cross-Tabulation Table window: a live-rendered contingency
    /// table over paired spatial observations, with editable variable
    /// names and classes, counts/percent display, automatic unit
    /// conversion, row/column flip, and the suite's export set (copy
    /// table / image / full report, gdb-dBASE-CSV table export, PNG/SVG,
    /// editable layout graphics). Port of the CorridorDesigner Evaluation
    /// Tools' Cross-Tab Statistics window (manual pp. 67-87).
    /// </summary>
    public partial class CrosstabWindow
        : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private CrosstabData _data;
        private readonly CrosstabAxis _h = new CrosstabAxis();
        private readonly CrosstabAxis _v = new CrosstabAxis();
        private CrosstabResult _result;
        private PolarPlotModel _model;
        private readonly CrosstabStyle _style = new CrosstabStyle();
        private bool _loading = true;
        private bool _closedDown;
        private ArcGIS.Core.Events.SubscriptionToken _appClosingToken;

        public CrosstabWindow()
        {
            InitializeComponent();
            Loaded += (_, __) =>
            {
                _loading = false;
                if (_appClosingToken == null)
                    _appClosingToken = ArcGIS.Desktop.Framework.Events
                        .ApplicationClosingEvent.Subscribe(args =>
                        {
                            _closedDown = true;
                            try { Close(); } catch { }
                            return Task.CompletedTask;
                        });
                if (_data == null) OnNewAnalysis(null, null);
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

        // =================================================================
        // new analysis
        // =================================================================
        private async void OnNewAnalysis(object sender, RoutedEventArgs e)
        {
            var dlg = new CrosstabInputWindow { Owner = this };
            if (dlg.ShowDialog() != true || dlg.Result == null)
            {
                // cancelling the dialog that opened with the window means
                // "never mind": close the empty window too
                if (_data == null && !_closedDown) Close();
                return;
            }
            var req = dlg.Result;
            var aoiLayer = dlg.AoiLayer;
            btnNew.IsEnabled = false;
            prgRead.Visibility = Visibility.Visible;
            prgRead.IsIndeterminate = false;
            prgRead.Value = 0;
            txtStatus.Text = "Reading and pairing the data...";
            try
            {
                var d = await QueuedTask.Run(() =>
                {
                    if (_closedDown) return null;
                    if (aoiLayer != null)
                    {
                        req.Aoi = HistogramData.BuildClipPolygon(aoiLayer,
                            out string an);
                        req.AoiName = an;
                    }
                    return CrosstabDataReader.Read(req, p =>
                    {
                        if (p >= 0)
                            Dispatcher.InvokeAsync(() =>
                                prgRead.Value = p * 100);
                    });
                });
                if (d == null || _closedDown) return;
                _data = d;
                _binDirty = true;
                // defaults: a variable with at most 5 distinct values is
                // DISCRETE -- one single-value class per value (GRIDCODE
                // 1,2,3 makes exactly three classes); otherwise the
                // original's 4 equal-interval classes. A LABELED variable
                // (text field, coded-value domain, raster attribute table)
                // is categorical outright: one named class per value.
                var u1 = d.UniqueValues1(d.Labels1 != null ? 500 : 5);
                var u2 = d.UniqueValues2(d.Labels2 != null ? 500 : 5);
                var (mn1, mx1) = d.Range1();
                var (mn2, mx2) = d.Range2();
                _h.VariableName = d.Name1;
                _h.Classes = u1 != null
                    ? CrosstabAxis.ForValues(u1, d.Labels1)
                    : CrosstabAxis.EqualInterval(4, mn1, mx1);
                _v.VariableName = d.Name2;
                _v.Classes = u2 != null
                    ? CrosstabAxis.ForValues(u2, d.Labels2)
                    : CrosstabAxis.EqualInterval(4, mn2, mx2);
                _loading = true;
                txtVar1.Text = _h.VariableName;
                txtVar2.Text = _v.VariableName;
                chkFlip.IsChecked = false;
                RefreshUnitCombo();
                _loading = false;
                foreach (var b in new[] { btnCopyTable, btnCopyImage,
                                          btnFullReport, btnExport,
                                          btnSaveImage, btnLayout,
                                          btnEditH, btnEditV })
                    b.IsEnabled = true;
                txtHint.Visibility = Visibility.Collapsed;
                Rebuild();
                txtStatus.Text = string.Format(CultureInfo.InvariantCulture,
                    "{0:#,##0} observation(s) paired; weights: {1}.",
                    d.Observations, d.WeightDesc);
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Analysis failed: " + ex.Message;
                MessageBox.Show(ex.Message, Title);
            }
            finally
            {
                btnNew.IsEnabled = true;
                prgRead.Visibility = Visibility.Collapsed;
            }
        }

        // =================================================================
        // display options
        // =================================================================
        private sealed class UnitChoice
        {
            public YUnit Unit;
            public string Label;
            public double Factor;
            public override string ToString() => Label;
        }

        private void RefreshUnitCombo()
        {
            var items = new List<UnitChoice>
            {
                new UnitChoice
                {
                    Unit = YUnit.Native,
                    Label = _data == null ? "Native" : _data.WeightDesc,
                    Factor = 1.0,
                },
            };
            if (_data != null)
            {
                double m2 = _data.Basis == WeightBasis.AreaM2 ? 1.0
                    : _data.Basis == WeightBasis.Cells &&
                      _data.CellAreaM2 > 0 ? _data.CellAreaM2 : 0;
                if (m2 > 0)
                {
                    items.Add(new UnitChoice
                    { Unit = YUnit.SqMeters, Label = "Square Meters",
                      Factor = m2 });
                    items.Add(new UnitChoice
                    { Unit = YUnit.Hectares, Label = "Hectares",
                      Factor = m2 / 1e4 });
                    items.Add(new UnitChoice
                    { Unit = YUnit.Acres, Label = "Acres",
                      Factor = m2 / 4046.8564224 });
                    items.Add(new UnitChoice
                    { Unit = YUnit.SqKilometers, Label = "Square Kilometers",
                      Factor = m2 / 1e6 });
                    items.Add(new UnitChoice
                    { Unit = YUnit.SqMiles, Label = "Square Miles",
                      Factor = m2 / 2589988.110336 });
                }
                else if (_data.Basis == WeightBasis.LengthM)
                {
                    items.Add(new UnitChoice
                    { Unit = YUnit.Meters, Label = "Meters", Factor = 1.0 });
                    items.Add(new UnitChoice
                    { Unit = YUnit.Kilometers, Label = "Kilometers",
                      Factor = 1e-3 });
                    items.Add(new UnitChoice
                    { Unit = YUnit.Miles, Label = "Miles",
                      Factor = 1.0 / 1609.344 });
                }
            }
            cboUnit.ItemsSource = items;
            // sensible default: hectares for large areas
            var def = items.FirstOrDefault(u => u.Unit == YUnit.Hectares
                && _data != null && SumW() * FactorOf(u) is double t
                && t >= 1 && t < 1e7) ?? items[0];
            cboUnit.SelectedItem = def;
        }

        private double SumW() => _data?.W.Where(x => x > 0).Sum() ?? 0;

        private static double FactorOf(UnitChoice u) => u?.Factor ?? 1.0;

        private double CurrentFactor() =>
            FactorOf(cboUnit.SelectedItem as UnitChoice);

        private string CurrentUnitWord()
        {
            var u = cboUnit.SelectedItem as UnitChoice;
            return u == null || u.Unit == YUnit.Native
                ? _data?.WeightDesc ?? "Count" : u.Label;
        }

        private void OnOption(object sender, RoutedEventArgs e) => Rebuild();
        private void OnOptionText(object sender, TextChangedEventArgs e) => Rebuild();
        private void OnOptionSel(object sender, SelectionChangedEventArgs e) => Rebuild();

        private int Decimals()
        {
            return int.TryParse(txtDecimals.Text, out int d)
                ? Math.Min(Math.Max(d, 0), 8)
                : 3;
        }

        // =================================================================
        // rebuild
        // =================================================================
        // Bin depends ONLY on the data and the class definitions --
        // formatting options (names, decimals, percent, unit, flip,
        // fonts) reuse the cached result. Without the cache, every
        // KEYSTROKE in the variable-name and decimals boxes re-walked
        // every paired observation (millions on raster pairings).
        private bool _binDirty = true;

        private void Rebuild()
        {
            if (_loading || _closedDown || _data == null) return;
            _h.VariableName = txtVar1.Text;
            _v.VariableName = txtVar2.Text;
            if (_binDirty || _result == null)
            {
                _result = CrosstabMath.Bin(_data, _h, _v);
                _binDirty = false;
            }
            var (grid, colVar, rowVar) = CrosstabReport.Grid(_result, _h, _v,
                rbPercent.IsChecked == true, Decimals(),
                chkFlip.IsChecked == true, CurrentFactor());
            _model = CrosstabBuilder.Build(grid, colVar, rowVar, _style,
                                           MeasureInches);
            var dg = PolarPlotWpf.Render(_model);
            dg.Freeze();
            imgTable.Source = new DrawingImage(dg);
        }

        private void OnFonts(object sender, RoutedEventArgs e)
        {
            if (FontsPrompt.Show(this,
                    ("Variable names:", _style.VariableFont),
                    ("Header row / column:", _style.HeaderFont),
                    ("Numbers:", _style.CellFont)))
                Rebuild();
        }

        private double MeasureInches(string text, FontSpec f)
        {
            try
            {
                var tf = new Typeface(
                    new FontFamily(string.IsNullOrEmpty(f.Family)
                        ? "Segoe UI" : f.Family),
                    f.Italic ? FontStyles.Italic : FontStyles.Normal,
                    f.Bold ? FontWeights.Bold : FontWeights.Normal,
                    FontStretches.Normal);
                var ft = new FormattedText(text ?? "",
                    CultureInfo.InvariantCulture, FlowDirection.LeftToRight,
                    tf, f.SizePt * 96.0 / 72.0, Brushes.Black,
                    VisualTreeHelper.GetDpi(this).PixelsPerDip);
                return ft.WidthIncludingTrailingWhitespace / 96.0;
            }
            catch
            {
                return 0.075 * (text?.Length ?? 0) * f.SizePt / 9.0;
            }
        }

        // =================================================================
        // class editors
        // =================================================================
        private void OnEditH(object sender, RoutedEventArgs e)
        {
            var (mn, mx) = _data.Range1();
            if (CrosstabClassEditor.Show(this,
                    "Edit Column Classes (" + _h.VariableName + ")",
                    _h.Classes, mn, mx))
            {
                _binDirty = true;
                Rebuild();
            }
        }

        private void OnEditV(object sender, RoutedEventArgs e)
        {
            var (mn, mx) = _data.Range2();
            if (CrosstabClassEditor.Show(this,
                    "Edit Row Classes (" + _v.VariableName + ")",
                    _v.Classes, mn, mx))
            {
                _binDirty = true;
                Rebuild();
            }
        }

        // =================================================================
        // exports
        // =================================================================
        private void OnCopyTable(object sender, RoutedEventArgs e)
        {
            try
            {
                Clipboard.SetText(CrosstabReport.Tsv(_result, _h, _v,
                    rbPercent.IsChecked == true, Decimals(),
                    chkFlip.IsChecked == true, CurrentFactor()));
                txtStatus.Text = "✓ Table copied -- paste into Excel or "
                    + "Word.";
            }
            catch (Exception ex)
            { MessageBox.Show("Could not copy: " + ex.Message, Title); }
        }

        private void OnFullReport(object sender, RoutedEventArgs e)
        {
            try
            {
                Clipboard.SetText(CrosstabReport.FullReport(_data, _result,
                    _h, _v, Decimals(), chkFlip.IsChecked == true,
                    CurrentFactor(), CurrentUnitWord()));
                txtStatus.Text = "✓ Full report copied (sources, both "
                    + "tables, class definitions).";
            }
            catch (Exception ex)
            { MessageBox.Show("Could not copy: " + ex.Message, Title); }
        }

        private RenderTargetBitmap RenderBitmap(double scale)
        {
            var dg = PolarPlotWpf.Render(_model);
            // pixel dimensions FIRST; the white background must cover the
            // whole (ceiled) bitmap, or the last pixel row/column stays
            // transparent (a black edge once the clipboard drops alpha)
            int pxW = (int)Math.Ceiling(_model.HalfWidthIn * 2 * 96 * scale);
            int pxH = (int)Math.Ceiling(_model.HalfHeightIn * 2 * 96 * scale);
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
            return rtb;
        }

        private void OnCopyImage(object sender, RoutedEventArgs e)
        {
            try
            {
                Clipboard.SetImage(RenderBitmap(2.0));
                txtStatus.Text = "✓ Table image copied.";
            }
            catch (Exception ex)
            { MessageBox.Show("Could not copy: " + ex.Message, Title); }
        }

        private void OnSaveImage(object sender, RoutedEventArgs e)
        {
            var dlg = new Microsoft.Win32.SaveFileDialog
            {
                Title = "Save the cross-tabulation table image",
                Filter = "PNG image (*.png)|*.png|SVG vector (*.svg)|*.svg",
                FileName = "Crosstab_Table.png",
                InitialDirectory = ImageSaveFolder.Initial(),
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
                    var enc = new PngBitmapEncoder();
                    enc.Frames.Add(BitmapFrame.Create(
                        RenderBitmap(300.0 / 96.0)));
                    using (var fs = File.Create(dlg.FileName))
                        enc.Save(fs);
                }
                ImageSaveFolder.Remember(dlg.FileName);
                txtStatus.Text = "Image saved: " + dlg.FileName;
            }
            catch (Exception ex)
            { MessageBox.Show("Could not save: " + ex.Message, Title); }
        }

        private static string DefaultDir()
        {
            try
            {
                string p = ArcGIS.Desktop.Core.Project.Current
                    ?.HomeFolderPath;
                if (!string.IsNullOrEmpty(p) && Directory.Exists(p))
                    return p;
            }
            catch { }
            return Environment.GetFolderPath(
                Environment.SpecialFolder.MyDocuments);
        }

        private async void OnExport(object sender, RoutedEventArgs e)
        {
            var choice = ExportTablesPrompt.Show(this, "Crosstab",
                "One table is written: <base name> -- one row per table "
                + "cell, with the cell value and its percent of the total.");
            if (choice == null) return;
            try
            {
                txtStatus.Text = "Exporting the cross-tabulation table...";
                var spec = CrosstabReport.ExportTable(_data, _result, _h, _v);
                string p1;
                if (choice.Csv)
                {
                    string cand = Path.Combine(choice.Workspace,
                        choice.BaseName + ".csv");
                    for (int i = 1; i <= 999 && File.Exists(cand); i++)
                        cand = Path.Combine(choice.Workspace,
                            choice.BaseName + "_" + i + ".csv");
                    File.WriteAllText(cand, HistogramStore.ToCsv(spec));
                    p1 = cand;
                }
                else
                {
                    p1 = await GpTables.WriteAsync(choice.Workspace,
                        choice.BaseName, choice.Gdb, spec);
                }
                await QueuedTask.Run(() =>
                {
                    var map = MapView.Active?.Map;
                    if (map == null) return;
                    try
                    {
                        StandaloneTableFactory.Instance
                            .CreateStandaloneTable(
                                new StandaloneTableCreationParams(
                                    new Uri(p1)), map);
                    }
                    catch { }
                });
                txtStatus.Text = "Exported and added to the map: " + p1;
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Export failed: " + ex.Message;
                MessageBox.Show(ex.Message, Title);
            }
        }

        private async void OnAddToLayout(object sender, RoutedEventArgs e)
        {
            if (_model == null) return;
            try
            {
                string where = await LayoutPlacement.AddChart(this, _model,
                    "Cross-Tabulation Table");
                if (where != null) txtStatus.Text = "✓ " + where;
            }
            catch (Exception ex)
            { MessageBox.Show(ex.Message, Title); }
        }

        private void OnClose(object sender, RoutedEventArgs e) => Close();

        private void OnHelp(object sender, RoutedEventArgs e)
            => WebHelp.Open("corridor-designer-tools/cross-tab-statistics.html");
    }

    /// <summary>Modal per-axis class editor: a row per class (name, min,
    /// max), a class-count box, and the original's equal-interval
    /// autofill. Returns true when applied (the list is edited in
    /// place).</summary>
    internal static class CrosstabClassEditor
    {
        public static bool Show(Window owner, string title,
            List<CrosstabClass> classes, double dataMin, double dataMax)
        {
            bool applied = false;
            var win = new Window
            {
                Title = title,
                Owner = owner,
                Width = 470,
                SizeToContent = SizeToContent.Height,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                ResizeMode = ResizeMode.NoResize,
                ShowInTaskbar = false,
            };
            JennessIcon.Apply(win);
            var root = new StackPanel { Margin = new Thickness(12) };
            root.Children.Add(new TextBlock
            {
                Text = string.Format(CultureInfo.InvariantCulture,
                    "Observed data range: {0:0.###} to {1:0.###}. Classes "
                    + "are [min, max); the last class includes its "
                    + "maximum; equal min and max makes a single-value "
                    + "class. Values outside every class fall into "
                    + "\"Other\".", dataMin, dataMax),
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 0, 0, 8),
            });

            var countPanel = new StackPanel
            { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 0, 6) };
            countPanel.Children.Add(new TextBlock
            {
                Text = "Number of classes:",
                VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(0, 0, 6, 0),
            });
            var txtCount = new TextBox
            {
                Width = 44,
                Text = classes.Count.ToString(CultureInfo.InvariantCulture),
            };
            countPanel.Children.Add(txtCount);
            var btnAutofill = new Button
            {
                Content = "Autofill Class Definitions",
                Padding = new Thickness(8, 2, 8, 2),
                Margin = new Thickness(10, 0, 0, 0),
            };
            countPanel.Children.Add(btnAutofill);
            root.Children.Add(countPanel);

            var headers = new Grid();
            for (int c = 0; c < 3; c++)
                headers.ColumnDefinitions.Add(new ColumnDefinition
                {
                    Width = c == 0 ? new GridLength(2, GridUnitType.Star)
                                   : new GridLength(1, GridUnitType.Star),
                });
            void Head(string t, int col)
            {
                var b = new TextBlock
                { Text = t, FontWeight = FontWeights.SemiBold };
                Grid.SetColumn(b, col);
                headers.Children.Add(b);
            }
            Head("Class name", 0);
            Head("Minimum", 1);
            Head("Maximum", 2);
            root.Children.Add(headers);

            var listPanel = new StackPanel();
            var scroll = new ScrollViewer
            {
                Content = listPanel,
                MaxHeight = 320,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                Margin = new Thickness(0, 2, 0, 8),
            };
            root.Children.Add(scroll);

            var rows = new List<(TextBox name, TextBox min, TextBox max)>();
            void AddRow(CrosstabClass c)
            {
                var g = new Grid { Margin = new Thickness(0, 1, 0, 1) };
                for (int i = 0; i < 3; i++)
                    g.ColumnDefinitions.Add(new ColumnDefinition
                    {
                        Width = i == 0 ? new GridLength(2, GridUnitType.Star)
                                       : new GridLength(1, GridUnitType.Star),
                    });
                var tn = new TextBox
                { Text = c.Name, Margin = new Thickness(0, 0, 4, 0) };
                var tmin = new TextBox
                {
                    Text = c.Min.ToString("0.######",
                        CultureInfo.InvariantCulture),
                    Margin = new Thickness(0, 0, 4, 0),
                };
                var tmax = new TextBox
                {
                    Text = c.Max.ToString("0.######",
                        CultureInfo.InvariantCulture),
                };
                Grid.SetColumn(tn, 0);
                Grid.SetColumn(tmin, 1);
                Grid.SetColumn(tmax, 2);
                g.Children.Add(tn);
                g.Children.Add(tmin);
                g.Children.Add(tmax);
                listPanel.Children.Add(g);
                rows.Add((tn, tmin, tmax));
            }
            foreach (var c in classes) AddRow(c);

            void RebuildRows(List<CrosstabClass> cls)
            {
                listPanel.Children.Clear();
                rows.Clear();
                foreach (var c in cls) AddRow(c);
            }

            btnAutofill.Click += (_, __) =>
            {
                int n = int.TryParse(txtCount.Text, out int x)
                    ? Math.Min(Math.Max(x, 1), 40) : classes.Count;
                RebuildRows(CrosstabAxis.EqualInterval(n, dataMin, dataMax));
            };
            txtCount.LostFocus += (_, __) =>
            {
                // grow/shrink the row list to the requested count, keeping
                // existing entries
                if (!int.TryParse(txtCount.Text, out int n)) return;
                n = Math.Min(Math.Max(n, 1), 40);
                var current = Snapshot();
                while (current.Count > n)
                    current.RemoveAt(current.Count - 1);
                while (current.Count < n)
                {
                    double lastMax = current.Count > 0
                        ? current[current.Count - 1].Max : dataMin;
                    current.Add(new CrosstabClass
                    {
                        Min = lastMax,
                        Max = Math.Max(lastMax, dataMax),
                        Name = "Class " + (current.Count + 1),
                    });
                }
                RebuildRows(current);
            };

            List<CrosstabClass> Snapshot()
            {
                var list = new List<CrosstabClass>();
                foreach (var (tn, tmin, tmax) in rows)
                {
                    double.TryParse(tmin.Text, NumberStyles.Float,
                        CultureInfo.InvariantCulture, out double mn);
                    double.TryParse(tmax.Text, NumberStyles.Float,
                        CultureInfo.InvariantCulture, out double mx);
                    list.Add(new CrosstabClass
                    { Name = tn.Text, Min = mn, Max = mx });
                }
                return list;
            }

            var buttons = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                HorizontalAlignment = HorizontalAlignment.Right,
            };
            var ok = new Button
            { Content = "OK", Width = 80, IsDefault = true };
            ok.Click += (_, __) =>
            {
                var snap = Snapshot();
                if (snap.Any(c => c.Max < c.Min))
                {
                    MessageBox.Show("Every class needs Maximum >= Minimum "
                        + "(equal min and max makes a single-value class).",
                        title);
                    return;
                }
                classes.Clear();
                classes.AddRange(snap);
                applied = true;
                win.Close();
            };
            var cancel = new Button
            {
                Content = "Cancel",
                Width = 80,
                Margin = new Thickness(8, 0, 0, 0),
                IsCancel = true,
            };
            cancel.Click += (_, __) => win.Close();
            buttons.Children.Add(ok);
            buttons.Children.Add(cancel);
            root.Children.Add(buttons);
            win.Content = root;
            win.ShowDialog();
            return applied;
        }
    }
}
