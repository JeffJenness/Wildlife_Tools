using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Media;
using Ellipse = System.Windows.Shapes.Ellipse;
using Polyline = System.Windows.Shapes.Polyline;
using Line = System.Windows.Shapes.Line;
using ArcGIS.Core.Data;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// The Land Facet Clustering wizard: an all-in-Pro replacement for the Land
    /// Facet Corridor Designer's export-to-R / cluster-in-R / import-from-R round
    /// trip. Step 1 (Setup) reads and aligns the categorical template raster plus
    /// the variable rasters (optionally clipped) into per-class matrices; the
    /// later steps (outliers, clusters, facets) drive the shared
    /// <see cref="LandFacetEngine"/>. One shared non-modal window.
    /// </summary>
    public partial class LandFacetWindow
        : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private sealed class LayerItem
        {
            public string Name { get; set; }
            public BasicRasterLayer Raster { get; set; }
            public FeatureLayer Feature { get; set; }
            public bool IsCategorical { get; set; }   // integer or has an attribute table
            public override string ToString() => Name;
        }

        private sealed class CategoryItem
        {
            public string Label { get; set; }
            public RasterStack.ClassData Data { get; set; }
        }

        private sealed class VarFlag
        {
            public string Name;
            public bool PercentSlope;   // GP history shows Slope in PERCENT_RISE
            public bool AspectLike;     // GP history shows Aspect or D-infinity
        }

        // One row of the "Classes found" grid: the Value and Cells columns plus any
        // number of descriptive attribute fields, reached through a string indexer so
        // extra columns can bind to [FieldName].
        private sealed class ClassRow
        {
            public string Category { get; set; }
            public string Cells { get; set; }
            private readonly Dictionary<string, string> _extra =
                new(StringComparer.OrdinalIgnoreCase);
            public void Set(string key, string value) => _extra[key] = value;
            public string this[string key] =>
                _extra.TryGetValue(key, out var v) ? v : "";
        }

        // A template raster's attribute table: its descriptive fields, one preview
        // row per class, and a Value -> descriptive-field lookup so the same names
        // can be shown next to the complete-case counts after Read inputs.
        private sealed class VatInfo
        {
            public List<(string Name, string Alias)> Fields = new();     // extra columns
            public List<ClassRow> Rows = new();
            public Dictionary<string, Dictionary<string, string>> ByValue = new();
        }

        private List<LayerItem> _rasters = new();
        private VatInfo _vat;                    // attribute table of the chosen template
        private RasterStack _stack;
        private List<VarFlag> _varFlags = new();
        private int _step = 1;

        // Step 2 (outliers): the analysis sample of the active class, its density,
        // and the current outlier flags.
        private RasterStack.ClassData _activeClass;
        private double[][] _sample;
        private double[] _sampleDensity;
        private bool[] _sampleOutlier;
        private LandFacetEngine.KdeModel _kdeModel;   // reused to score all cells in step 4
        private const int MaxSample = 4000;

        // Step 3 (clusters): standardization params, the per-c results, chosen k.
        private double[] _stdMean, _stdSd;
        private double _psi = 1.5;
        private List<int> _cValues = new();
        private Dictionary<int, LandFacetEngine.ValidityIndices> _indicesByC = new();
        private Dictionary<int, double[][]> _centroidsByC = new();
        private int _chosenK;

        // 3 maximized, then 3 minimized, then 2 scree -- matching the R plot set.
        private static readonly (string name, Func<LandFacetEngine.ValidityIndices, double> get, bool higherBetter)[] IndexInfo =
        {
            ("PBM", v => v.PBM, true),
            ("Calinski-Harabasz", v => v.CH, true),
            ("Fuzzy Silhouette", v => v.FSil, true),
            ("Xie-Beni", v => v.XB, false),
            ("Xie-Beni*", v => v.XBstar, false),
            ("Davies-Bouldin", v => v.DB, false),
            ("Fukuyama-Sugeno", v => v.FS, false),
            ("Avg Within-Cluster Dist", v => v.AWCD, false),
        };

        private static readonly Brush InlierBrush =
            Frozen(Color.FromArgb(150, 91, 141, 184));
        private static readonly Brush OutlierBrush =
            Frozen(Color.FromArgb(225, 217, 83, 79));
        private static Brush Frozen(Color c)
        {
            var b = new SolidColorBrush(c); b.Freeze(); return b;
        }

        public LandFacetWindow()
        {
            InitializeComponent();
            Loaded += (_, __) => PopulateLayers();
        }

        private void SetStatus(string t) => txtStatus.Text = t;

        // ---- setup: enumerate the map's layers -------------------------------
        private async void PopulateLayers()
        {
            var map = MapView.Active?.Map;
            if (map == null) { SetStatus("Open a map with your raster layers first."); return; }

            var rasterLayers = map.GetLayersAsFlattenedList().OfType<BasicRasterLayer>().ToList();
            var polyLayers = map.GetLayersAsFlattenedList().OfType<FeatureLayer>()
                .Where(IsPolygon).ToList();

            // A raster is a valid categorical template if it is integer or has an
            // attribute table -- determine that on the MCT.
            var eligible = await QueuedTask.Run(() =>
            {
                var set = new HashSet<BasicRasterLayer>();
                foreach (var l in rasterLayers)
                {
                    try
                    {
                        var r = l.GetRaster();
                        if (r.IsInteger() || r.GetAttributeTable() != null) set.Add(l);
                    }
                    catch { /* skip unreadable */ }
                }
                return set;
            });

            _rasters = rasterLayers.Select(l => new LayerItem
            {
                Name = l.Name, Raster = l, IsCategorical = eligible.Contains(l)
            }).ToList();

            cboCat.ItemsSource = _rasters.Where(li => li.IsCategorical).ToList();
            lstVars.ItemsSource = _rasters;

            var polys = polyLayers.Select(l => new LayerItem { Name = l.Name, Feature = l }).ToList();
            polys.Insert(0, new LayerItem { Name = "(none)", Feature = null });
            cboClip.ItemsSource = polys;
            cboClip.SelectedIndex = 0;

            SetStatus(_rasters.Any(li => li.IsCategorical)
                ? "Choose the categorical raster and the variable rasters, then Read inputs."
                : "No integer / attribute-table rasters found to use as the categorical template.");
        }

        private static bool IsPolygon(FeatureLayer fl)
        {
            try { return fl.ShapeType == ArcGIS.Core.CIM.esriGeometryType.esriGeometryPolygon; }
            catch { return false; }
        }

        private async void OnCatChanged(object sender, SelectionChangedEventArgs e)
        {
            // Keep the chosen categorical raster out of the variable list.
            var cat = cboCat.SelectedItem as LayerItem;
            _stack = null;                       // inputs changed; a re-read is needed
            _varFlags = new();
            _vat = null;
            lstClasses.ItemsSource = null;
            BuildClassColumns(null);
            // Repopulate variables after the reset so the resulting selection-change
            // does not try to invalidate a stack that is already gone.
            lstVars.ItemsSource = _rasters.Where(r => r != cat).ToList();
            if (cat == null) return;

            // Preview the categories from the raster's attribute table, if any.
            try
            {
                var vat = await QueuedTask.Run(() => ReadVat(cat.Raster));
                _vat = vat;
                BuildClassColumns(vat?.Fields);
                if (vat != null && vat.Rows.Count > 0)
                {
                    lstClasses.ItemsSource = vat.Rows;
                    SetStatus($"{vat.Rows.Count} categories in the raster's attribute table "
                              + "(raw counts). Select variables, then Read inputs for the "
                              + "complete-case counts.");
                }
                else
                {
                    SetStatus("This raster has no attribute table; class counts will appear "
                              + "after Read inputs.");
                }
            }
            catch { /* leave the preview empty */ }
        }

        // Changing the variable selection or the clip layer after a read makes the
        // loaded stack (and its slope/aspect flags) stale -- force a fresh Read inputs
        // so the later steps never run on, or warn about, data that is no longer selected.
        private void OnVarsChanged(object sender, SelectionChangedEventArgs e)
            => InvalidateRead("Variable selection changed");

        private void OnClipChanged(object sender, SelectionChangedEventArgs e)
            => InvalidateRead("Clip layer changed");

        private void InvalidateRead(string reason)
        {
            if (_stack == null) return;          // nothing loaded to invalidate
            _stack = null;
            _varFlags = new();
            lstClasses.ItemsSource = _vat?.Rows;  // fall back to the raw VAT preview
            SetStatus(reason + " — click Read inputs to update.");
        }

        /// <summary>Read a raster's attribute table (MCT): the descriptive fields
        /// (everything but the OID, Value and Count), one preview row per class, and a
        /// Value -> descriptive-field lookup. Null if the raster has no attribute
        /// table.</summary>
        private static VatInfo ReadVat(BasicRasterLayer layer)
        {
            var raster = layer.GetRaster();
            var vat = raster.GetAttributeTable();
            if (vat == null) return null;

            var info = new VatInfo();
            var descriptive = new List<Field>();
            foreach (var fld in vat.GetDefinition().GetFields())
            {
                if (fld.FieldType == FieldType.OID || fld.FieldType == FieldType.Geometry ||
                    fld.FieldType == FieldType.Blob || fld.FieldType == FieldType.Raster)
                    continue;
                if (fld.Name.Equals("Value", StringComparison.OrdinalIgnoreCase) ||
                    fld.Name.Equals("Count", StringComparison.OrdinalIgnoreCase))
                    continue;
                descriptive.Add(fld);
            }
            info.Fields = descriptive
                .Select(f => (f.Name, string.IsNullOrEmpty(f.AliasName) ? f.Name : f.AliasName))
                .ToList();

            using var cursor = vat.Search(null, false);
            while (cursor.MoveNext())
            {
                using var row = cursor.Current;
                object val;
                try { val = row["Value"]; } catch { continue; }
                double dv;
                try { dv = Convert.ToDouble(val, CultureInfo.InvariantCulture); } catch { continue; }
                string key = FormatCode(dv);

                object cnt = null;
                try { cnt = row["Count"]; } catch { /* no Count field */ }

                var rowItem = new ClassRow
                {
                    Category = key,
                    Cells = cnt != null ? Convert.ToInt64(cnt).ToString("N0") : "",
                };
                var extra = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                foreach (var f in descriptive)
                {
                    object v = null;
                    try { v = row[f.Name]; } catch { /* field missing on this row */ }
                    string s = v == null ? "" : Convert.ToString(v, CultureInfo.InvariantCulture);
                    rowItem.Set(f.Name, s);
                    extra[f.Name] = s;
                }
                info.Rows.Add(rowItem);
                info.ByValue[key] = extra;
            }
            return info;
        }

        /// <summary>Rebuild the "Classes found" grid: the fixed Value and Cells
        /// columns followed by one column per descriptive attribute field.</summary>
        private void BuildClassColumns(IReadOnlyList<(string Name, string Alias)> fields)
        {
            if (gvClasses == null) return;
            while (gvClasses.Columns.Count > 2)
                gvClasses.Columns.RemoveAt(gvClasses.Columns.Count - 1);
            if (fields == null) return;
            foreach (var (name, alias) in fields)
            {
                gvClasses.Columns.Add(new GridViewColumn
                {
                    Header = alias,
                    Width = 130,
                    DisplayMemberBinding = new System.Windows.Data.Binding($"[{name}]"),
                });
            }
        }

        private async void OnReadInputs(object sender, RoutedEventArgs e)
        {
            var cat = cboCat.SelectedItem as LayerItem;
            if (cat == null) { MessageBox.Show("Choose the categorical raster.", "Land Facet Clustering"); return; }
            var vars = lstVars.SelectedItems.Cast<LayerItem>().ToList();
            if (vars.Count == 0) { MessageBox.Show("Select at least one variable raster.", "Land Facet Clustering"); return; }
            var clipItem = cboClip.SelectedItem as LayerItem;

            btnRead.IsEnabled = false;
            SetStatus("Reading and aligning rasters...");
            try
            {
                var res = await QueuedTask.Run(() =>
                {
                    var catRaster = cat.Raster.GetRaster();
                    var vlist = vars.Select(v => (v.Raster.GetRaster(), v.Name)).ToList();
                    Polygon clip = clipItem?.Feature != null ? UnionPolygon(clipItem.Feature) : null;
                    var st = RasterStack.Build(catRaster, vlist, clip);
                    var flags = vars.Select(v => DetectVarType(v.Name, GetMetadataXml(v.Raster))).ToList();
                    return (st, flags);
                });
                var stack = res.st;
                _stack = stack;
                _varFlags = res.flags;
                BuildClassColumns(_vat?.Fields);
                lstClasses.ItemsSource = stack.Classes.Values.OrderBy(c => c.Value)
                    .Select(c =>
                    {
                        var rowItem = new ClassRow
                        {
                            Category = FormatCode(c.Value),
                            Cells = c.Count.ToString("N0"),
                        };
                        if (_vat != null &&
                            _vat.ByValue.TryGetValue(rowItem.Category, out var extra))
                            foreach (var kv in extra) rowItem.Set(kv.Key, kv.Value);
                        return rowItem;
                    })
                    .ToList();
                long total = stack.Classes.Values.Sum(c => (long)c.Count);
                SetStatus($"{stack.Classes.Count} class(es), {stack.VariableNames.Length} variable(s), "
                          + $"{total:N0} complete-case cells.");
            }
            catch (Exception ex)
            {
                SetStatus("Error: " + ex.Message);
                MessageBox.Show(ex.Message, "Land Facet Clustering");
            }
            finally { btnRead.IsEnabled = true; }
        }

        /// <summary>Union all polygon features of a layer into one clip polygon
        /// (MCT).</summary>
        private static Polygon UnionPolygon(FeatureLayer layer)
        {
            using var fc = layer.GetFeatureClass();
            var geoms = new List<ArcGIS.Core.Geometry.Geometry>();
            using (var cursor = fc.Search(null, false))
            {
                while (cursor.MoveNext())
                {
                    using var row = cursor.Current as Feature;
                    var shape = row?.GetShape();
                    if (shape != null) geoms.Add(shape);
                }
            }
            if (geoms.Count == 0) return null;
            return GeometryEngine.Instance.Union(geoms) as Polygon;
        }

        private static string FormatCode(double code)
        {
            if (Math.Abs(code - Math.Round(code)) < 1e-9)
                return ((long)Math.Round(code)).ToString(CultureInfo.InvariantCulture);
            return code.ToString("R", CultureInfo.InvariantCulture);
        }

        // ---- metadata / geoprocessing-history detection ---------------------
        /// <summary>The layer's metadata XML (which carries the geoprocessing
        /// history), or empty if unavailable.</summary>
        private static string GetMetadataXml(BasicRasterLayer layer)
        {
            try { return (layer as ArcGIS.Desktop.Core.IMetadataInfo)?.GetMetadata() ?? ""; }
            catch { return ""; }
        }

        /// <summary>Inspect a variable raster's geoprocessing history for evidence
        /// it is percent-rise slope, or aspect / D-infinity (a circular variable).
        /// No evidence -> both flags false (assume the raster is fine).</summary>
        private static VarFlag DetectVarType(string name, string xml)
        {
            var f = new VarFlag { Name = name };
            if (string.IsNullOrEmpty(xml)) return f;
            foreach (Match m in Regex.Matches(xml, "<Process[^>]*>(.*?)</Process>",
                     RegexOptions.Singleline | RegexOptions.IgnoreCase))
            {
                string p = m.Groups[1].Value.Trim().ToLowerInvariant();
                if (p.Contains("percent_rise")) f.PercentSlope = true;
                if (Regex.IsMatch(p, @"^\s*aspect\b")
                    || (p.Contains("surfaceparameters") && Regex.IsMatch(p, @"\baspect\b"))
                    || Regex.IsMatch(p, @"\bdinf\b"))
                    f.AspectLike = true;
            }
            return f;
        }

        private enum SlopeChoice { ProceedAsIs, ConvertDegrees, DoNotProceed }

        /// <summary>A small three-option dialog for a detected percent-slope
        /// variable (proceed as-is / convert to degrees / do not proceed).</summary>
        private SlopeChoice AskSlope(string vars)
        {
            var result = SlopeChoice.DoNotProceed;
            var dlg = new Window
            {
                Title = "Land Facet Clustering",
                Owner = this,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                ResizeMode = ResizeMode.NoResize,
                SizeToContent = SizeToContent.WidthAndHeight,
                ShowInTaskbar = false,
            };
            var sp = new StackPanel { Margin = new Thickness(18), Width = 440 };
            sp.Children.Add(new TextBlock
            {
                Text = $"The variable raster(s) {vars} appear to be PERCENT-slope rasters "
                     + "(their geoprocessing history uses PERCENT_RISE).\n\n"
                     + "Percent slope is a non-linear measure of angle (it rises without bound "
                     + "on steep ground), so clustering on it weights steep slopes very "
                     + "differently than clustering on degrees. You can proceed with the percent "
                     + "values, or have the tool convert them to degrees internally for the "
                     + "analysis.",
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 0, 0, 14),
            });
            void Add(string text, SlopeChoice choice, bool ok)
            {
                var b = new Button { Content = text, Margin = new Thickness(0, 3, 0, 0), Padding = new Thickness(10, 5, 10, 5) };
                b.Click += (_, __) => { result = choice; dlg.DialogResult = ok; };
                sp.Children.Add(b);
            }
            Add("Proceed with the percent values", SlopeChoice.ProceedAsIs, true);
            Add("Convert to degrees and proceed", SlopeChoice.ConvertDegrees, true);
            Add("Do not proceed", SlopeChoice.DoNotProceed, false);
            dlg.Content = sp;
            dlg.ShowDialog();
            return result;
        }

        /// <summary>Convert the named variables from percent slope to degrees in the
        /// loaded stack (in place): degrees = atan(percent/100).</summary>
        private void ConvertPercentToDegrees(IEnumerable<string> names)
        {
            var idxs = names
                .Select(n => Array.IndexOf(_stack.VariableNames, n))
                .Where(i => i >= 0).ToList();
            if (idxs.Count == 0) return;
            double toDeg = 180.0 / Math.PI;
            foreach (var cd in _stack.Classes.Values)
                foreach (var row in cd.Rows)
                    foreach (int j in idxs)
                        row[j] = Math.Atan(row[j] / 100.0) * toDeg;
        }

        // ---- step 2: outliers -----------------------------------------------
        private void PrepareStep2()
        {
            if (_stack == null) return;
            if (cboCategory.Items.Count == 0)
            {
                cboCategory.ItemsSource = _stack.Classes.Values.OrderBy(c => c.Value)
                    .Select(c => new CategoryItem
                    {
                        Label = $"Class {FormatCode(c.Value)}  ({c.Count:N0} cells)",
                        Data = c
                    }).ToList();
                if (cboCategory.Items.Count > 0) cboCategory.SelectedIndex = 0;
            }
        }

        private void OnCategoryChanged(object sender, SelectionChangedEventArgs e)
        {
            _activeClass = (cboCategory.SelectedItem as CategoryItem)?.Data;
            _sample = null; _sampleDensity = null; _sampleOutlier = null;
            scatterCanvas.Children.Clear();
            txtOutlierInfo.Text = _activeClass != null ? "Click Detect outliers." : "";
        }

        private async void OnDetectOutliers(object sender, RoutedEventArgs e)
        {
            if (_activeClass == null) { MessageBox.Show("Pick a category.", "Land Facet Clustering"); return; }
            if (_activeClass.Count < 10)
            {
                MessageBox.Show("This class has too few cells to analyze.", "Land Facet Clustering");
                return;
            }
            btnDetect.IsEnabled = false;
            SetStatus("Estimating density...");
            try
            {
                var sample = Subsample(_activeClass, MaxSample, new Random(12345));
                var model = LandFacetEngine.KdePrepare(sample, 2000, new Random(999));
                var density = await Task.Run(() => LandFacetEngine.KdeEval(sample, model));
                _sample = sample;
                _kdeModel = model;
                _sampleDensity = density;
                ApplyFraction();
                RenderScatter();
            }
            catch (Exception ex) { MessageBox.Show(ex.Message, "Land Facet Clustering"); }
            finally { btnDetect.IsEnabled = true; SetStatus(""); }
        }

        private static double[][] Subsample(RasterStack.ClassData cd, int max, Random rng)
        {
            var rows = cd.Rows;
            if (rows.Count <= max) return rows.ToArray();
            var idx = Enumerable.Range(0, rows.Count).ToArray();
            for (int i = 0; i < max; i++)
            {
                int j = i + rng.Next(rows.Count - i);
                (idx[i], idx[j]) = (idx[j], idx[i]);
            }
            return idx.Take(max).Select(i => rows[i]).ToArray();
        }

        private void OnFractionChanged(object sender, System.Windows.RoutedPropertyChangedEventArgs<double> e)
        {
            if (txtFractionEdit == null) return;   // fires during XAML init
            if (!txtFractionEdit.IsKeyboardFocused)   // don't fight the user's typing
                txtFractionEdit.Text = $"{sldFraction.Value * 100:F1}";
            ApplyFraction();
            RenderScatter();
        }

        // Let the user type an exact percentage; commit on Enter or when focus leaves.
        private void OnFractionKeyDown(object sender, System.Windows.Input.KeyEventArgs e)
        {
            if (e.Key == System.Windows.Input.Key.Enter) { CommitFraction(); e.Handled = true; }
        }

        private void OnFractionCommit(object sender, RoutedEventArgs e) => CommitFraction();

        private void CommitFraction()
        {
            if (txtFractionEdit == null) return;
            string t = txtFractionEdit.Text.Trim().TrimEnd('%').Trim();
            double frac;
            if (double.TryParse(t, NumberStyles.Any, CultureInfo.CurrentCulture, out double pct) ||
                double.TryParse(t, NumberStyles.Any, CultureInfo.InvariantCulture, out pct))
                frac = Math.Max(sldFraction.Minimum, Math.Min(sldFraction.Maximum, pct / 100.0));
            else
                frac = sldFraction.Value;                    // bad input -> revert
            txtFractionEdit.Text = $"{frac * 100:F1}";        // always show the normalized value
            if (Math.Abs(frac - sldFraction.Value) > 1e-9)
                sldFraction.Value = frac;                    // -> OnFractionChanged re-renders
        }

        private void ApplyFraction()
        {
            double frac = sldFraction.Value;
            if (_sampleDensity == null) return;
            _sampleOutlier = LandFacetEngine.Outliers(_sampleDensity, frac);
            int no = _sampleOutlier.Count(b => b);
            txtOutlierInfo.Text = $"{_sample.Length:N0} sample cells; {no:N0} outliers "
                                  + $"({(_sample.Length > 0 ? 100.0 * no / _sample.Length : 0):F1}%)";
        }

        private void OnScatterSizeChanged(object sender, SizeChangedEventArgs e) => RenderScatter();

        private void RenderScatter()
        {
            scatterCanvas.Children.Clear();
            if (_sample == null || _sampleOutlier == null) return;
            double w = scatterCanvas.ActualWidth, h = scatterCanvas.ActualHeight;
            if (w < 20 || h < 20) return;

            int d = _sample[0].Length;
            int vx = 0, vy = d >= 2 ? 1 : 0;
            bool oneD = d < 2;
            var xv = _sample.Select(r => r[vx]).ToArray();
            var yv = oneD ? _sampleDensity : _sample.Select(r => r[vy]).ToArray();
            double minx = xv.Min(), maxx = xv.Max(), miny = yv.Min(), maxy = yv.Max();
            if (maxx <= minx) maxx = minx + 1;
            if (maxy <= miny) maxy = miny + 1;
            const double pad = 26;

            for (int i = 0; i < xv.Length; i++)
            {
                double px = pad + (xv[i] - minx) / (maxx - minx) * (w - 2 * pad);
                double py = h - pad - (yv[i] - miny) / (maxy - miny) * (h - 2 * pad);
                var dot = new Ellipse
                {
                    Width = 3.5,
                    Height = 3.5,
                    Fill = _sampleOutlier[i] ? OutlierBrush : InlierBrush,
                };
                Canvas.SetLeft(dot, px - 1.75);
                Canvas.SetTop(dot, py - 1.75);
                scatterCanvas.Children.Add(dot);
            }

            AddLabel(_stack.VariableNames[vx], w / 2 - 30, h - 16);
            AddLabel(oneD ? "density" : _stack.VariableNames[vy], 4, 4);
        }

        private void AddLabel(string text, double left, double top)
        {
            var t = new TextBlock { Text = text, Foreground = Brushes.Gray, FontSize = 11 };
            Canvas.SetLeft(t, left);
            Canvas.SetTop(t, top);
            scatterCanvas.Children.Add(t);
        }

        // ---- step 3: clusters -----------------------------------------------
        private async void OnRunClustering(object sender, RoutedEventArgs e)
        {
            if (_sample == null || _sampleOutlier == null)
            { MessageBox.Show("Detect outliers first (step 2).", "Land Facet Clustering"); return; }
            if (!double.TryParse(txtPsi.Text, out double psi) || psi <= 1.0)
            { MessageBox.Show("Fuzziness must be greater than 1 (for example 1.5).", "Land Facet Clustering"); return; }
            if (!int.TryParse(txtRestarts.Text, out int restarts) || restarts < 1) restarts = 12;

            var raw = new List<double[]>();
            for (int i = 0; i < _sample.Length; i++)
                if (!_sampleOutlier[i]) raw.Add(_sample[i]);
            if (raw.Count < 10)
            { MessageBox.Show("Too few non-outlier cells to cluster.", "Land Facet Clustering"); return; }

            _psi = psi;
            btnCluster.IsEnabled = false;
            SetStatus("Running fuzzy c-means across cluster counts...");
            try
            {
                var res = await Task.Run(() => Cluster(raw.ToArray(), psi, restarts));
                _stdMean = res.mean; _stdSd = res.sd; _cValues = res.cValues;
                _indicesByC = res.indices; _centroidsByC = res.centroids;
                int suggested = SuggestK();
                cboK.ItemsSource = _cValues;
                cboK.SelectedItem = suggested;      // -> OnKChanged -> render
                txtSuggestK.Text = $"suggested: {suggested}";
                txtClusterInfo.Text = $"{raw.Count:N0} non-outlier cells; "
                    + $"c = {_cValues.First()}..{_cValues.Last()}, {restarts} restarts each";
            }
            catch (Exception ex) { MessageBox.Show(ex.Message, "Land Facet Clustering"); }
            finally { btnCluster.IsEnabled = true; SetStatus(""); }
        }

        private static (double[] mean, double[] sd, List<int> cValues,
                        Dictionary<int, LandFacetEngine.ValidityIndices> indices,
                        Dictionary<int, double[][]> centroids)
            Cluster(double[][] raw, double psi, int restarts)
        {
            var z = LandFacetEngine.Standardize(raw, out var mean, out var sd);
            int maxC = Math.Min(7, z.Length - 1);
            var cValues = new List<int>();
            for (int c = 2; c <= maxC; c++) cValues.Add(c);
            var indices = new Dictionary<int, LandFacetEngine.ValidityIndices>();
            var centroids = new Dictionary<int, double[][]>();
            var rng = new Random(2024);
            foreach (int c in cValues)
            {
                var best = LandFacetEngine.FcmBest(z, c, psi, restarts, 1000, 1e-6, rng);
                indices[c] = LandFacetEngine.Validity(z, best.Centers, best.Membership, best.Cluster, psi);
                centroids[c] = best.Centers;
            }
            return (mean, sd, cValues, indices, centroids);
        }

        /// <summary>Consensus of the six clear indices (argmax of PBM/CH/FSil,
        /// argmin of XB/XB*/DB): the most-voted cluster count.</summary>
        private int SuggestK()
        {
            if (_cValues.Count == 0) return 2;
            var votes = new List<int>
            {
                ArgBest(v => v.PBM, true), ArgBest(v => v.CH, true), ArgBest(v => v.FSil, true),
                ArgBest(v => v.XB, false), ArgBest(v => v.XBstar, false), ArgBest(v => v.DB, false),
            };
            return votes.GroupBy(x => x).OrderByDescending(g => g.Count())
                        .ThenBy(g => g.Key).First().Key;
        }

        private int ArgBest(Func<LandFacetEngine.ValidityIndices, double> get, bool higher)
        {
            int best = _cValues[0];
            double bv = get(_indicesByC[best]);
            foreach (int c in _cValues)
            {
                double v = get(_indicesByC[c]);
                if (higher ? v > bv : v < bv) { bv = v; best = c; }
            }
            return best;
        }

        private void OnKChanged(object sender, SelectionChangedEventArgs e)
        {
            if (cboK.SelectedItem is int k) { _chosenK = k; RenderValidity(); }
        }

        private void OnClusterSizeChanged(object sender, SizeChangedEventArgs e) => RenderValidity();

        private void RenderValidity()
        {
            clusterCanvas.Children.Clear();
            if (_indicesByC.Count == 0 || _cValues.Count < 2) return;
            double W = clusterCanvas.ActualWidth, H = clusterCanvas.ActualHeight;
            if (W < 60 || H < 60) return;

            const int cols = 4, rows = 2;
            double cw = W / cols, ch = H / rows;
            for (int p = 0; p < IndexInfo.Length; p++)
            {
                int col = p % cols, row = p / cols;
                double x0 = col * cw + 6, y0 = row * ch + 4, pw = cw - 12, ph = ch - 26;
                var info = IndexInfo[p];
                var vals = _cValues.Select(c => info.get(_indicesByC[c])).ToArray();
                double mn = vals.Min(), mx = vals.Max();
                if (mx <= mn) mx = mn + 1;

                var poly = new Polyline { Stroke = Brushes.SteelBlue, StrokeThickness = 1.5 };
                for (int i = 0; i < _cValues.Count; i++)
                {
                    double px = x0 + (_cValues.Count > 1 ? (double)i / (_cValues.Count - 1) : 0) * pw;
                    double py = y0 + ph - (vals[i] - mn) / (mx - mn) * ph;
                    poly.Points.Add(new Point(px, py));
                    var dot = new Ellipse { Width = 4, Height = 4, Fill = Brushes.SteelBlue };
                    Canvas.SetLeft(dot, px - 2); Canvas.SetTop(dot, py - 2);
                    clusterCanvas.Children.Add(dot);
                }
                clusterCanvas.Children.Add(poly);

                int ki = _cValues.IndexOf(_chosenK);
                if (ki >= 0)
                {
                    double kx = x0 + (_cValues.Count > 1 ? (double)ki / (_cValues.Count - 1) : 0) * pw;
                    clusterCanvas.Children.Add(new Line
                    {
                        X1 = kx, X2 = kx, Y1 = y0, Y2 = y0 + ph,
                        Stroke = Brushes.OrangeRed, StrokeThickness = 1,
                        StrokeDashArray = new DoubleCollection { 2, 2 },
                    });
                }

                var t = new TextBlock
                {
                    Text = info.name + (info.higherBetter ? "  (max)" : "  (min)"),
                    Foreground = Brushes.Gray, FontSize = 10,
                };
                Canvas.SetLeft(t, x0); Canvas.SetTop(t, y0 + ph + 4);
                clusterCanvas.Children.Add(t);
            }
        }

        // ---- step 4: facets --------------------------------------------------
        private void OnBrowseOut(object sender, RoutedEventArgs e)
        {
            var dlg = new Microsoft.Win32.SaveFileDialog
            {
                Filter = "GeoTIFF (*.tif)|*.tif",
                FileName = "land_facets.tif",
                Title = "Save the land-facet raster",
            };
            try
            {
                var proj = ArcGIS.Desktop.Core.Project.Current;
                if (proj != null && !string.IsNullOrEmpty(proj.HomeFolderPath)
                    && System.IO.Directory.Exists(proj.HomeFolderPath))
                    dlg.InitialDirectory = proj.HomeFolderPath;
            }
            catch { /* no project home */ }
            if (dlg.ShowDialog() == true) txtOut.Text = dlg.FileName;
        }

        // Where the raster is written (a GeoTIFF the GDAL helper produces) and where
        // it finally lives (the same, unless a geodatabase target needs a copy step).
        private sealed class OutputTarget
        {
            public string WritePath;   // GeoTIFF the helper writes
            public string FinalPath;   // final raster location added to the map
            public bool CopyToGdb;     // WritePath is a temp GeoTIFF to copy into a gdb
        }

        /// <summary>Turn what the user typed into a fully-qualified output. A bare name
        /// goes into the project's default geodatabase; a full path is honoured as
        /// given. Must run on the MCT (uses Project.Current).</summary>
        private static OutputTarget ResolveOutputTarget(string typed)
        {
            typed = (typed ?? "").Trim().Trim('"');
            if (typed.Length == 0) return null;

            var proj = ArcGIS.Desktop.Core.Project.Current;
            string gdb = proj?.DefaultGeodatabasePath;
            string home = proj?.HomeFolderPath;

            bool hasDir = typed.IndexOf('\\') >= 0 || typed.IndexOf('/') >= 0
                          || System.IO.Path.IsPathRooted(typed);

            if (hasDir)
            {
                if (InGdb(typed)) return GdbTarget(typed);
                if (!System.IO.Path.HasExtension(typed)) typed += ".tif";
                return new OutputTarget { WritePath = typed, FinalPath = typed };
            }

            // Bare name: a file-raster name (has an extension, e.g. facets.tif) goes to
            // the project home folder; otherwise it becomes a default-geodatabase raster.
            if (!string.IsNullOrEmpty(System.IO.Path.GetExtension(typed)))
            {
                string dir = FirstExistingDir(home,
                    gdb != null ? System.IO.Path.GetDirectoryName(gdb) : null);
                string p = System.IO.Path.Combine(dir, typed);
                return new OutputTarget { WritePath = p, FinalPath = p };
            }
            if (!string.IsNullOrEmpty(gdb) && System.IO.Directory.Exists(gdb))
                return GdbTarget(System.IO.Path.Combine(gdb, SanitizeGdbName(typed)));

            // No usable default geodatabase: fall back to a GeoTIFF in the home folder.
            string fp = System.IO.Path.Combine(FirstExistingDir(home), SanitizeGdbName(typed) + ".tif");
            return new OutputTarget { WritePath = fp, FinalPath = fp };
        }

        private static bool InGdb(string p) =>
            p.IndexOf(".gdb", StringComparison.OrdinalIgnoreCase) >= 0;

        private static OutputTarget GdbTarget(string path)
        {
            int idx = path.IndexOf(".gdb", StringComparison.OrdinalIgnoreCase);
            string gdbPath = path.Substring(0, idx + 4);
            string name = path.Length > idx + 4 ? path.Substring(idx + 4).Trim('\\', '/') : "";
            name = SanitizeGdbName(System.IO.Path.GetFileNameWithoutExtension(name));
            string finalPath = System.IO.Path.Combine(gdbPath, name);
            string scratch = System.IO.Path.Combine(System.IO.Path.GetTempPath(),
                                                    "WildlifeTools_LandFacet");
            System.IO.Directory.CreateDirectory(scratch);
            string temp = System.IO.Path.Combine(scratch, "gdb_" + Guid.NewGuid().ToString("N") + ".tif");
            return new OutputTarget { WritePath = temp, FinalPath = finalPath, CopyToGdb = true };
        }

        private static string FirstExistingDir(params string[] dirs)
        {
            foreach (var d in dirs)
                if (!string.IsNullOrEmpty(d) && System.IO.Directory.Exists(d)) return d;
            return System.IO.Path.GetTempPath();
        }

        /// <summary>A valid geodatabase raster-dataset name: letters, digits and
        /// underscores, not starting with a digit.</summary>
        private static string SanitizeGdbName(string n)
        {
            if (string.IsNullOrEmpty(n)) return "land_facets";
            var sb = new StringBuilder();
            foreach (char ch in n) sb.Append(char.IsLetterOrDigit(ch) || ch == '_' ? ch : '_');
            if (sb.Length == 0) return "land_facets";
            if (char.IsDigit(sb[0])) sb.Insert(0, 'r');
            return sb.ToString();
        }

        /// <summary>Copy a temporary GeoTIFF into a geodatabase raster (Copy Raster),
        /// overwriting an existing dataset. Returns the geodatabase path.</summary>
        private static async Task<string> CopyIntoGeodatabase(string tempTif, string gdbTarget)
        {
            var args = ArcGIS.Desktop.Core.Geoprocessing.Geoprocessing.MakeValueArray(tempTif, gdbTarget);
            var env = ArcGIS.Desktop.Core.Geoprocessing.Geoprocessing.MakeEnvironmentArray(overwriteoutput: true);
            var res = await ArcGIS.Desktop.Core.Geoprocessing.Geoprocessing.ExecuteToolAsync(
                "management.CopyRaster", args, env);
            if (res.IsFailed)
                throw new InvalidOperationException(
                    "Copying the raster into the geodatabase failed: "
                    + string.Join("; ", res.ErrorMessages.Select(m => m.Text)));
            return gdbTarget;
        }

        private async void OnGenerate(object sender, RoutedEventArgs e)
        {
            if (_activeClass == null || _centroidsByC.Count == 0 || _chosenK == 0)
            { MessageBox.Show("Run the clustering and choose k first.", "Land Facet Clustering"); return; }
            if (string.IsNullOrWhiteSpace(txtOut.Text))
            { MessageBox.Show("Choose an output raster path.", "Land Facet Clustering"); return; }
            if (!double.TryParse(txtConf.Text, out double confThresh) || confThresh <= 0 || confThresh > 1)
            { MessageBox.Show("The confusion-index threshold must be between 0 and 1 (for example 0.6).", "Land Facet Clustering"); return; }

            var centers = _centroidsByC[_chosenK];
            double psi = _psi, frac = sldFraction.Value;
            // outlier density cutoff: the frac-th lowest sample density.
            var sorted = (double[])_sampleDensity.Clone();
            Array.Sort(sorted);
            int oc = (int)Math.Round(frac * sorted.Length);
            double densThresh = oc >= 1 ? sorted[oc - 1] : double.NegativeInfinity;

            int W = _stack.Width, H = _stack.Height, k = _chosenK;
            var cls = _activeClass;

            // Resolve the output: a bare name -> the project's default geodatabase; a
            // full path -> used as given. Rasters can only be written as GeoTIFF, so a
            // geodatabase target is written to a temp GeoTIFF and then copied in.
            OutputTarget target;
            try { target = await QueuedTask.Run(() => ResolveOutputTarget(txtOut.Text)); }
            catch (Exception ex)
            { MessageBox.Show(ex.Message, "Land Facet Clustering"); return; }
            if (target == null)
            { MessageBox.Show("Enter an output raster name or path.", "Land Facet Clustering"); return; }

            btnGenerate.IsEnabled = false;
            pbGen.Value = 0;
            txtGenInfo.Text = "";
            SetStatus("Classifying cells...");
            try
            {
                var r = await Task.Run(() => ClassifyAll(
                    cls, W, H, centers, _stdMean, _stdSd, psi, _kdeModel, densThresh, confThresh, k));

                SetStatus("Writing the land-facet raster...");
                double[] gt = { _stack.Extent.XMin, _stack.CellSizeX, 0.0,
                                _stack.Extent.YMax, 0.0, -_stack.CellSizeY };
                string wkt = _stack.SpatialRef?.Wkt ?? string.Empty;
                var progress = new Progress<double>(f => pbGen.Value = f);
                string written = await CorridorRasterWriter.WriteAsync(
                    r.arr, W, H, gt, wkt, -9999, target.WritePath, progress);

                string finalPath = written;
                if (target.CopyToGdb)
                {
                    SetStatus("Copying the raster into the geodatabase...");
                    finalPath = await CopyIntoGeodatabase(written, target.FinalPath);
                    foreach (var tmp in new[] { written, written + ".aux.xml", written + ".ovr" })
                        try { if (System.IO.File.Exists(tmp)) System.IO.File.Delete(tmp); } catch { }
                }

                await QueuedTask.Run(() =>
                {
                    var map = MapView.Active?.Map;
                    if (map != null)
                        LayerFactory.Instance.CreateLayer(new Uri(finalPath), map);
                });
                pbGen.Value = 1;

                var sb = new StringBuilder();
                sb.AppendLine($"Land facets written for category {FormatCode(cls.Value)} "
                              + $"({k} clusters, fuzziness {psi:0.##}).");
                sb.AppendLine($"{r.nWritten:N0} of {cls.Count:N0} cells classified; "
                              + $"{r.nOut:N0} outliers and {r.nConf:N0} above the confusion "
                              + "threshold were left NoData.");
                for (int c = 0; c < r.counts.Length; c++)
                    sb.AppendLine($"    Cluster {c + 1}:  {r.counts[c]:N0} cells");
                txtFacetSummary.Text = sb.ToString();
                txtGenInfo.Text = "Done.";
                SetStatus("Land facets written and added to the map: " + finalPath);
            }
            catch (Exception ex)
            {
                SetStatus("Error: " + ex.Message);
                MessageBox.Show(ex.Message, "Land Facet Clustering");
            }
            finally { btnGenerate.IsEnabled = true; }
        }

        /// <summary>Classify every cell of the class into its highest-membership
        /// cluster, leaving outliers and over-the-confusion-threshold cells NoData;
        /// returns the full-grid cluster-ID array (1-based, -9999 NoData) plus
        /// counts.</summary>
        private static (int[] arr, long[] counts, long nOut, long nConf, long nWritten)
            ClassifyAll(RasterStack.ClassData cls, int w, int h, double[][] centers,
                        double[] mean, double[] sd, double psi, LandFacetEngine.KdeModel kde,
                        double densThresh, double confThresh, int k)
        {
            int n = cls.Count;
            var arr = new int[w * h];
            for (int i = 0; i < arr.Length; i++) arr[i] = -9999;

            var dens = LandFacetEngine.KdeEval(cls.Rows.ToArray(), kde);
            var counts = new long[k];
            long nOut = 0, nConf = 0, nWritten = 0;
            for (int i = 0; i < n; i++)
            {
                bool outlier = dens[i] <= densThresh;
                var z = LandFacetEngine.StandardizePoint(cls.Rows[i], mean, sd);
                var memb = LandFacetEngine.MembershipOf(z, centers, psi);
                var c = LandFacetEngine.Classify(memb);
                bool confused = c.ConfusionIndex > confThresh;
                if (outlier) nOut++;
                if (confused) nConf++;
                if (!outlier && !confused)
                {
                    arr[(int)cls.CellIndex[i]] = c.TopCluster + 1;
                    counts[c.TopCluster]++;
                    nWritten++;
                }
            }
            return (arr, counts, nOut, nConf, nWritten);
        }

        // ---- wizard navigation ----------------------------------------------
        private void ShowStep(int s)
        {
            _step = s;
            if (s == 2) PrepareStep2();
            panelSetup.Visibility = s == 1 ? Visibility.Visible : Visibility.Collapsed;
            panelOutliers.Visibility = s == 2 ? Visibility.Visible : Visibility.Collapsed;
            panelClusters.Visibility = s == 3 ? Visibility.Visible : Visibility.Collapsed;
            panelFacets.Visibility = s == 4 ? Visibility.Visible : Visibility.Collapsed;

            step1lbl.Style = StepStyle(s == 1);
            step2lbl.Style = StepStyle(s == 2);
            step3lbl.Style = StepStyle(s == 3);
            step4lbl.Style = StepStyle(s == 4);

            btnBack.IsEnabled = s > 1;
            btnNext.IsEnabled = s < 4;
        }

        private Style StepStyle(bool active) =>
            (Style)FindResource(active ? "StepLabelActive" : "StepLabel");

        private void OnBack(object sender, RoutedEventArgs e)
        {
            if (_step > 1) ShowStep(_step - 1);
        }

        private void OnNext(object sender, RoutedEventArgs e)
        {
            if (_step == 1)
            {
                if (_stack == null)
                {
                    MessageBox.Show("Read the inputs first.", "Land Facet Clustering");
                    return;
                }
                // Aspect / circular-variable warning (non-blocking; can proceed).
                var aspectVars = _varFlags.Where(f => f.AspectLike).Select(f => f.Name).ToList();
                if (aspectVars.Count > 0)
                {
                    var r = MessageBox.Show(
                        $"The variable raster(s) {string.Join(", ", aspectVars)} appear to be "
                        + "ASPECT (or D-infinity flow-direction) rasters.\n\n"
                        + "This tool clusters variables with ordinary linear distance, which is "
                        + "not valid for a circular variable like aspect (0 and 360 degrees are the "
                        + "same direction). Consider transforming aspect to a linear form -- for "
                        + "example northness and eastness (cosine and sine of aspect) with the "
                        + "Aspect Transformation tool -- or use Solar Insolation instead.\n\n"
                        + "Proceed anyway?",
                        "Land Facet Clustering", MessageBoxButton.YesNo, MessageBoxImage.Warning);
                    if (r != MessageBoxResult.Yes) return;
                }
                // Percent-slope three-option confirmation.
                var slopeVars = _varFlags.Where(f => f.PercentSlope).Select(f => f.Name).ToList();
                if (slopeVars.Count > 0)
                {
                    var choice = AskSlope(string.Join(", ", slopeVars));
                    if (choice == SlopeChoice.DoNotProceed) return;
                    if (choice == SlopeChoice.ConvertDegrees) ConvertPercentToDegrees(slopeVars);
                }
                ShowStep(2);
                return;
            }
            if (_step == 2 && _sampleOutlier == null)
            {
                MessageBox.Show("Detect outliers first.", "Land Facet Clustering");
                return;
            }
            if (_step == 3 && (_centroidsByC.Count == 0 || _chosenK == 0))
            {
                MessageBox.Show("Run the clustering and choose k first.", "Land Facet Clustering");
                return;
            }
            if (_step < 4) ShowStep(_step + 1);
        }

        private void OnClose(object sender, RoutedEventArgs e) => Close();

        // ---- help windows ----------------------------------------------------
        private void OnClusterHelp(object sender, RoutedEventArgs e)
            => ShowHelp("Reading the clustering charts", BuildClusterHelp());

        private void OnFacetHelp(object sender, RoutedEventArgs e)
            => ShowHelp("About the land-facet output", BuildFacetHelp());

        /// <summary>Show formatted help text in a resizable, non-modal report window
        /// so it can be read alongside the charts.</summary>
        private void ShowHelp(string title, FlowDocument doc)
        {
            var viewer = new FlowDocumentScrollViewer
            {
                Document = doc,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
            };
            new Window
            {
                Title = title,
                Owner = this,
                Width = 660,
                Height = 660,
                MinWidth = 420,
                MinHeight = 300,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                ShowInTaskbar = false,
                Content = viewer,
            }.Show();
        }

        // ---- flow-document helpers ----
        private static FlowDocument HelpDoc() => new()
        {
            FontFamily = new FontFamily("Segoe UI"),
            FontSize = 13.5,
            PagePadding = new Thickness(22),
            Background = Brushes.White,
            Foreground = Brushes.Black,
            TextAlignment = TextAlignment.Left,
        };

        private static Paragraph HeadPara(string t) => new(new Run(t))
        {
            FontSize = 17,
            FontWeight = FontWeights.SemiBold,
            Foreground = new SolidColorBrush(Color.FromRgb(0x1B, 0x3A, 0x5B)),
            Margin = new Thickness(0, 14, 0, 4),
        };

        private static Paragraph BodyPara(params Inline[] inlines)
        {
            var p = new Paragraph { Margin = new Thickness(0, 0, 0, 8), LineHeight = 19 };
            p.Inlines.AddRange(inlines);
            return p;
        }

        private static System.Windows.Documents.List Bullets(
            params (string label, string text)[] items)
        {
            var list = new System.Windows.Documents.List
            {
                MarkerStyle = TextMarkerStyle.Disc,
                Margin = new Thickness(0, 0, 0, 8),
                Padding = new Thickness(22, 0, 0, 0),
            };
            foreach (var (label, text) in items)
            {
                var para = new Paragraph { Margin = new Thickness(0, 1, 0, 1), LineHeight = 18 };
                if (!string.IsNullOrEmpty(label)) para.Inlines.Add(new Bold(new Run(label + " — ")));
                para.Inlines.Add(new Run(text));
                list.ListItems.Add(new ListItem(para));
            }
            return list;
        }

        private static FlowDocument BuildClusterHelp()
        {
            var d = HelpDoc();
            d.Blocks.Add(BodyPara(
                new Run("Fuzzy c-means clustering is run for every candidate number of clusters "),
                new Bold(new Run("c = 2, 3, 4, …")),
                new Run(". For each c the tool measures how good that grouping is with eight "),
                new Bold(new Run("cluster-validity indices")),
                new Run(". Each small chart plots one index against the number of clusters.")));

            d.Blocks.Add(HeadPara("Reading a chart"));
            d.Blocks.Add(Bullets(
                ("Horizontal axis", "the number of clusters c, increasing left to right (the leftmost point is c = 2, the rightmost is the largest c tried)."),
                ("Vertical axis", "the value of that index, rescaled to fill the chart so its shape is visible; heights are not comparable between different charts."),
                ("“(max)” in the title", "higher is better — the best c is where the curve peaks."),
                ("“(min)” in the title", "lower is better — the best c is where the curve dips lowest.")));

            d.Blocks.Add(HeadPara("The red dashed line"));
            d.Blocks.Add(BodyPara(
                new Run("The vertical "),
                new Run("red dashed line") { Foreground = Brushes.OrangeRed, FontWeight = FontWeights.SemiBold },
                new Run(" marks the number of clusters currently chosen in the "),
                new Bold(new Run("Number of clusters (k)")),
                new Run(" box below the charts. Its left-to-right position is that k on the horizontal axis — the same k drawn on every chart. "),
                new Run("Change k and every line moves together, so you can see at a glance whether your chosen k lands on, or near, each index’s best value.")));

            d.Blocks.Add(HeadPara("How the suggested k is chosen"));
            d.Blocks.Add(BodyPara(
                new Run("The value the tool preselects (shown as “suggested”) is a "),
                new Bold(new Run("consensus vote")),
                new Run(" of the six clearest indices: PBM, Calinski-Harabasz and Fuzzy Silhouette each vote for the c that "),
                new Bold(new Run("maximizes")),
                new Run(" them, and Xie-Beni, Xie-Beni* and Davies-Bouldin each vote for the c that "),
                new Bold(new Run("minimizes")),
                new Run(" them. The c with the most votes wins; ties break toward the smaller c (a simpler model). "),
                new Run("Fukuyama-Sugeno and Average Within-Cluster Distance are shown for context but do not vote — they tend to fall steadily as clusters increase and rarely mark a clear best value.")));
            d.Blocks.Add(BodyPara(new Run(
                "The suggestion is only a starting point. If several indices agree on a different c, or you have a landscape reason to prefer more or fewer facets, choose that k — the red line shows how it compares to each index.")));

            d.Blocks.Add(HeadPara("What each index measures"));
            d.Blocks.Add(Bullets(
                ("PBM (max)", "rewards clusters that are both compact and well separated."),
                ("Calinski-Harabasz (max)", "ratio of between-cluster spread to within-cluster spread."),
                ("Fuzzy Silhouette (max)", "how clearly each cell belongs to its own cluster rather than a neighbour."),
                ("Xie-Beni & Xie-Beni* (min)", "compactness divided by the separation of the closest clusters."),
                ("Davies-Bouldin (min)", "average similarity between each cluster and its most similar neighbour."),
                ("Fukuyama-Sugeno (min)", "compactness minus separation (context only)."),
                ("Avg Within-Cluster Distance (min)", "raw compactness; keeps improving with more clusters, so context only.")));

            d.Blocks.Add(HeadPara("A practical approach"));
            d.Blocks.Add(Bullets(
                ("", "Start from the suggested k."),
                ("", "Look for a c where several charts peak or dip together — that is a robust choice."),
                ("", "Prefer the smaller k when two values look almost equally good; simpler facet maps are easier to interpret."),
                ("", "Use the next step’s confusion index to see how cleanly the cells actually divide at your chosen k.")));
            return d;
        }

        private static FlowDocument BuildFacetHelp()
        {
            var d = HelpDoc();
            d.Blocks.Add(BodyPara(
                new Run("This step turns the clustering into a "),
                new Bold(new Run("land-facet raster")),
                new Run(": every retained cell of the selected category is labelled with the cluster it belongs to most strongly (1, 2, … up to k). "),
                new Run("Land facets are patches of ground with similar terrain — the building blocks the Corridor Designer approach uses to model how animals move across a landscape.")));

            d.Blocks.Add(HeadPara("Confusion-index threshold"));
            d.Blocks.Add(BodyPara(
                new Run("Fuzzy clustering gives each cell a membership in every cluster. The "),
                new Bold(new Run("confusion index")),
                new Run(" is the ratio of a cell’s second-highest membership to its highest (Burrough et al. 1997):")));
            d.Blocks.Add(Bullets(
                ("Near 0", "the cell clearly belongs to a single facet."),
                ("Near 1", "the cell is split almost evenly between two facets — its assignment is unreliable.")));
            d.Blocks.Add(BodyPara(
                new Run("Cells whose confusion index is "),
                new Bold(new Run("above the threshold")),
                new Run(" (default 0.6) are treated as too ambiguous and left as NoData rather than forced into a facet. "),
                new Run("Lower the threshold to keep only the most clear-cut cells; raise it to fill in more of the map at the cost of some uncertain assignments.")));

            d.Blocks.Add(HeadPara("What is left out"));
            d.Blocks.Add(Bullets(
                ("Outliers", "cells flagged in the Outliers step (unusual terrain that fits no facet well) are left NoData."),
                ("Ambiguous cells", "cells above the confusion threshold, as above."),
                ("Other categories", "only the selected category is written; every other cell is NoData.")));

            d.Blocks.Add(HeadPara("Output raster"));
            d.Blocks.Add(Bullets(
                ("A plain name", "for example land_facets, is written into the project’s current default geodatabase."),
                ("A full path", "for example D:\\Project\\facets.tif, or a dataset name inside a .gdb, is used exactly as given."),
                ("File vs geodatabase", "a name ending in .tif is written as a GeoTIFF; a geodatabase raster is written to a temporary GeoTIFF and copied in automatically.")));
            d.Blocks.Add(BodyPara(new Run(
                "The result is added to the map with integer facet IDs 1…k. You can run the tool again for another category and combine the outputs, or symbolize each facet with its own colour.")));
            return d;
        }
    }
}
