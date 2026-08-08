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
            /// <summary>Catalog path of the layer's source dataset, recorded in the
            /// linkage metadata so the later tools can re-find the same variable
            /// rasters even if the layer has since been renamed. Null if it could
            /// not be resolved (a service or in-memory layer), in which case the
            /// name is all downstream has to match on.</summary>
            public string Path { get; set; }
            public override string ToString() => Name;
        }

        /// <summary>Everything the wizard remembers for ONE category so all of the
        /// categories can be tuned (steps 2–4) and then written together in one pass.
        /// The step-2/3 UI edits the "current" category's fields; they are saved back
        /// here on a category switch and reloaded for the newly chosen one.</summary>
        private sealed class ClassAnalysis
        {
            public RasterStack.ClassData Data;
            public double Fraction = 0.10;             // outlier fraction
            public double[][] Sample;                  // step-2 subsample
            public double[] SampleDensity;
            public bool[] SampleOutlier;
            public LandFacetEngine.KdeModel Kde;
            public double[] StdMean, StdSd;            // step-3 standardization
            public double Psi = 1.5;
            public List<int> CValues;
            public Dictionary<int, LandFacetEngine.ValidityIndices> IndicesByC;
            public Dictionary<int, double[][]> CentroidsByC;
            public int ChosenK;
            public bool OutliersReady => SampleOutlier != null;
            public bool Clustered => CentroidsByC != null && CentroidsByC.Count > 0 && ChosenK > 0;
            public bool IsSet => OutliersReady && Clustered;   // ready to write
        }

        /// <summary>A row of the category selectors. The "✓ set" marker is
        /// step-specific: the Step 2 list flags a category whose OUTLIERS are set, the
        /// Step 3 list flags one whose CLUSTERING is set (the Step 4 table shows the
        /// combined readiness).</summary>
        private sealed class CategoryItem : System.ComponentModel.INotifyPropertyChanged
        {
            public double Value;
            public string BaseLabel;
            public ClassAnalysis Analysis;
            public event System.ComponentModel.PropertyChangedEventHandler PropertyChanged;
            public string LabelStep2 => BaseLabel
                + (Analysis != null && Analysis.OutliersReady ? "   ✓ set" : "");
            public string LabelStep3 => BaseLabel
                + (Analysis != null && Analysis.Clustered ? "   ✓ set" : "");
            public void Refresh()
            {
                PropertyChanged?.Invoke(this,
                    new System.ComponentModel.PropertyChangedEventArgs(nameof(LabelStep2)));
                PropertyChanged?.Invoke(this,
                    new System.ComponentModel.PropertyChangedEventArgs(nameof(LabelStep3)));
            }
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
        private string _classNameField;          // VAT field to name classes by (null = value)
        private string _symbologyWarning;        // set if applying the colorizer failed
        private string _analysisId;              // linkage id stamped on the last output
        private RasterStack _stack;
        private List<VarFlag> _varFlags = new();
        private List<string> _varPaths = new();  // catalog paths, in VariableNames order
        private int _step = 1;

        // Batch state: one analysis per category, and the category being edited now.
        private readonly Dictionary<double, ClassAnalysis> _analyses = new();
        private List<CategoryItem> _categoryItems = new();
        private ClassAnalysis _current;
        private bool _syncingCat;                 // guards the two synced selectors

        // Step 2 (outliers): the analysis sample of the active class, its density,
        // and the current outlier flags (a working view of _current).
        private RasterStack.ClassData _activeClass;
        private double[][] _sample;
        private double[] _sampleDensity;
        private bool[] _sampleOutlier;
        private LandFacetEngine.KdeModel _kdeModel;   // reused to score all cells in step 4
        private const int MaxSample = 4000;
        private int _scatterX = 0, _scatterY = 1;     // which variables the 2-D slice plots
        private bool _syncingAxes;

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
            ShowStep(1);                 // paint the step chips for the initial state
            Loaded += (_, __) => PopulateLayers();
        }

        private void SetStatus(string t) => txtStatus.Text = t;

        /// <summary>Catalog path of a raster's source dataset, or null. Call on the
        /// MCT. Mirrors the idiom in SlopeGraphic\SlopeData.DetectRasterUnit:
        /// the datastore gives the workspace, the dataset gives the name.</summary>
        private static string RasterCatalogPath(ArcGIS.Core.Data.Raster.Raster raster)
        {
            try
            {
                var ds = raster?.GetRasterDataset();
                if (ds == null) return null;
                string dir = ds.GetDatastore()?.GetPath()?.LocalPath;
                string name = ds.GetName();
                if (string.IsNullOrEmpty(dir) || string.IsNullOrEmpty(name)) return null;
                return System.IO.Path.Combine(dir, name);
            }
            catch { return null; }
        }

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
            var probe = await QueuedTask.Run(() =>
            {
                var set = new HashSet<BasicRasterLayer>();
                var paths = new Dictionary<BasicRasterLayer, string>();
                foreach (var l in rasterLayers)
                {
                    try
                    {
                        var r = l.GetRaster();
                        if (r.IsInteger() || r.GetAttributeTable() != null) set.Add(l);
                        string p = RasterCatalogPath(r);
                        if (p != null) paths[l] = p;
                    }
                    catch { /* skip unreadable */ }
                }
                return (set, paths);
            });

            _rasters = rasterLayers.Select(l => new LayerItem
            {
                Name = l.Name, Raster = l, IsCategorical = probe.set.Contains(l),
                Path = probe.paths.TryGetValue(l, out var p) ? p : null,
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
            btnExportR.IsEnabled = false;
            _varFlags = new();
            _vat = null;
            ResetBatchState();
            lstClasses.ItemsSource = null;
            BuildClassColumns(null);
            cboClassNameField.ItemsSource = null;
            _classNameField = null;
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
                PopulateClassNameField();
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

        /// <summary>Write the per-class Export-for-R tables from the loaded
        /// stack (the original workflow's branch point: users who prefer to run
        /// the fuzzy clustering in R take these CSVs to LF_fxns.R instead of
        /// continuing in this wizard). Enabled only while a read stack is
        /// current, so the tables always match what the wizard would analyze —
        /// including the clip and the complete-case filtering.</summary>
        private async void OnExportR(object sender, RoutedEventArgs e)
        {
            if (_stack == null)
            {
                MessageBox.Show("Read the inputs first; the tables are written "
                    + "from the data just read.", "Land Facet Clustering");
                return;
            }
            var dlg = new ArcGIS.Desktop.Catalog.OpenItemDialog
            {
                Title = "Choose the folder for the R tables",
                MultiSelect = false,
                Filter = ArcGIS.Desktop.Catalog.ItemFilters.Folders,
            };
            if (dlg.ShowDialog() != true) return;
            string folder = dlg.Items?.FirstOrDefault()?.Path;
            if (string.IsNullOrEmpty(folder)) return;

            var stack = _stack;          // capture: the UI may invalidate later
            btnExportR.IsEnabled = false;
            SetStatus("Writing the R tables…");
            try
            {
                var written = await Task.Run(
                    () => CorridorRExport.WriteTables(stack, folder));
                SetStatus("Wrote " + written.Count + " R table"
                    + (written.Count == 1 ? "" : "s") + " to " + folder);
                MessageBox.Show(
                    "Wrote " + written.Count + " table"
                    + (written.Count == 1 ? "" : "s") + ":\n\n"
                    + string.Join("\n", written.Select(System.IO.Path.GetFileName))
                    + "\n\nSchema matches the original Export-for-R tool: one CSV "
                    + "per class, one row per complete-case cell, one column per "
                    + "variable (raw values; no id or coordinate columns). Use "
                    + "them with the Land Facet R functions (LF_fxns.R), then "
                    + "either continue in R or come back to this wizard.",
                    "Export tables for R");
            }
            catch (Exception ex)
            {
                SetStatus("Export failed.");
                MessageBox.Show("Could not write the R tables:\n" + ex.Message,
                                "Export tables for R");
            }
            finally
            {
                btnExportR.IsEnabled = _stack != null;
            }
        }

        private void InvalidateRead(string reason)
        {
            if (_stack == null) return;          // nothing loaded to invalidate
            _stack = null;
            btnExportR.IsEnabled = false;
            _varFlags = new();
            _varPaths = new();
            ResetBatchState();
            lstClasses.ItemsSource = _vat?.Rows;  // fall back to the raw VAT preview
            SetStatus(reason + " — click Read inputs to update.");
        }

        /// <summary>Clear the per-category analyses so a fresh Read inputs rebuilds
        /// them (the category set, cells, and every tuning depend on the loaded data).</summary>
        private void ResetBatchState()
        {
            _analyses.Clear();
            _categoryItems = new List<CategoryItem>();
            _current = null;
            _syncingCat = true;
            if (cboCategory != null) cboCategory.ItemsSource = null;
            if (cboCategory3 != null) cboCategory3.ItemsSource = null;
            _syncingCat = false;
            _scatterX = 0; _scatterY = 1;
            if (cboScatterX != null)
            {
                _syncingAxes = true;
                cboScatterX.ItemsSource = null;
                cboScatterY.ItemsSource = null;
                _syncingAxes = false;
            }
            _activeClass = null; _sample = null; _sampleDensity = null; _sampleOutlier = null;
            _kdeModel = null; _stdMean = null; _stdSd = null;
            _cValues = new List<int>(); _indicesByC = new(); _centroidsByC = new(); _chosenK = 0;
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

        /// <summary>Fill the "Name classes by" picker with (value) plus the VAT's
        /// descriptive fields. Defaults to the raster value.</summary>
        private void PopulateClassNameField()
        {
            var items = new List<string> { "(value)" };
            if (_vat != null) items.AddRange(_vat.Fields.Select(f => f.Name));
            cboClassNameField.ItemsSource = items;
            cboClassNameField.SelectedIndex = 0;   // (value) -> OnClassNameFieldChanged
            _classNameField = null;
        }

        private void OnClassNameFieldChanged(object sender, SelectionChangedEventArgs e)
        {
            var sel = cboClassNameField.SelectedItem as string;
            _classNameField = string.IsNullOrEmpty(sel) || sel == "(value)" ? null : sel;
            // Re-label the category selectors (and step-4 overview) with the new names.
            foreach (var ci in _categoryItems)
            {
                ci.BaseLabel = $"{CategoryLabel(ci.Value)}  ({ci.Analysis.Data.Count:N0} cells)";
                ci.Refresh();
            }
            if (_step == 4) RefreshFacetStep();
        }

        /// <summary>A display name for a category: the chosen VAT field's value if one
        /// is selected and present, else "Class &lt;value&gt;".</summary>
        private string CategoryLabel(double value)
        {
            string key = FormatCode(value);
            if (_classNameField != null && _vat != null &&
                _vat.ByValue.TryGetValue(key, out var extra) &&
                extra.TryGetValue(_classNameField, out var nm) && !string.IsNullOrWhiteSpace(nm))
                return nm;
            return "Class " + key;
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
                btnExportR.IsEnabled = true;
                _varFlags = res.flags;
                // Same order as stack.VariableNames -- recorded in the linkage
                // metadata so the Mahalanobis tool can re-find these exact rasters.
                _varPaths = vars.Select(v => v.Path).ToList();
                ResetBatchState();               // new data -> rebuild categories on step 2
                int nClasses = stack.Classes.Count, nVars = stack.VariableNames.Length;
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
                SetStatus($"{nClasses} {Plural(nClasses, "class", "classes")}, "
                          + $"{nVars} {Plural(nVars, "variable", "variables")}, "
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

        private static string Plural(int n, string singular, string plural)
            => n == 1 ? singular : plural;

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
        private SlopeChoice AskSlope(List<string> vars)
        {
            string names = string.Join(", ", vars);
            bool one = vars.Count == 1;
            string lead = one
                ? $"The variable raster {names} appears to be a PERCENT-slope raster "
                  + "(its geoprocessing history uses PERCENT_RISE)."
                : $"The variable rasters {names} appear to be PERCENT-slope rasters "
                  + "(their geoprocessing history uses PERCENT_RISE).";
            var result = SlopeChoice.DoNotProceed;
            var dlg = new Window
            {
                Title = "Land Facet Clustering",
                Owner = this,
                Icon = JennessIcon(),
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                ResizeMode = ResizeMode.NoResize,
                SizeToContent = SizeToContent.WidthAndHeight,
                ShowInTaskbar = false,
            };
            var sp = new StackPanel { Margin = new Thickness(18), Width = 440 };
            sp.Children.Add(new TextBlock
            {
                Text = lead + "\n\n"
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
        /// <summary>Build one analysis per category (once, after Read inputs) and fill
        /// the category selectors on steps 2 and 3.</summary>
        private void PrepareStep2()
        {
            if (_stack == null || _categoryItems.Count > 0) return;   // build once per stack

            _analyses.Clear();
            _categoryItems = _stack.Classes.Values.OrderBy(c => c.Value).Select(c =>
            {
                var ca = new ClassAnalysis { Data = c, Psi = _psi };
                _analyses[c.Value] = ca;
                return new CategoryItem
                {
                    Value = c.Value,
                    BaseLabel = $"{CategoryLabel(c.Value)}  ({c.Count:N0} cells)",
                    Analysis = ca,
                };
            }).ToList();

            _syncingCat = true;
            cboCategory.ItemsSource = _categoryItems;
            cboCategory3.ItemsSource = _categoryItems;
            _syncingCat = false;
            PopulateScatterAxes();

            _current = null;
            if (_categoryItems.Count > 0) SelectCategory(_categoryItems[0]);
        }

        private CategoryItem CurrentItem()
            => _categoryItems.FirstOrDefault(ci => ReferenceEquals(ci.Analysis, _current));

        // Either category selector changed -> switch the working category (and, on the
        // clustering step, auto-analyse a category whose outliers are not set yet).
        private async void OnCategoryComboChanged(object sender, SelectionChangedEventArgs e)
        {
            if (_syncingCat) return;
            if ((sender as System.Windows.Controls.Primitives.Selector)?.SelectedItem is CategoryItem ci)
            {
                SelectCategory(ci);
                if (_step == 3 && _current != null && !_current.OutliersReady)
                    await EnsureOutliersAsync(announce: true);
            }
        }

        private void SelectCategory(CategoryItem ci)
        {
            SaveCurrent();
            _current = ci.Analysis;
            LoadCurrent();
            _syncingCat = true;
            cboCategory.SelectedItem = ci;
            cboCategory3.SelectedItem = ci;
            _syncingCat = false;
            RefreshCurrentStep();
        }

        /// <summary>Copy the working fields into the current analysis.</summary>
        private void SaveCurrent()
        {
            if (_current == null) return;
            _current.Fraction = sldFraction.Value;
            _current.Sample = _sample;
            _current.SampleDensity = _sampleDensity;
            _current.SampleOutlier = _sampleOutlier;
            _current.Kde = _kdeModel;
            _current.StdMean = _stdMean;
            _current.StdSd = _stdSd;
            _current.Psi = _psi;
            _current.CValues = _cValues;
            _current.IndicesByC = _indicesByC;
            _current.CentroidsByC = _centroidsByC;
            _current.ChosenK = _chosenK;
        }

        /// <summary>Load the current analysis into the working fields.</summary>
        private void LoadCurrent()
        {
            _activeClass = _current?.Data;
            _sample = _current?.Sample;
            _sampleDensity = _current?.SampleDensity;
            _sampleOutlier = _current?.SampleOutlier;
            _kdeModel = _current?.Kde;
            _stdMean = _current?.StdMean;
            _stdSd = _current?.StdSd;
            _cValues = _current?.CValues ?? new List<int>();
            _indicesByC = _current?.IndicesByC ?? new();
            _centroidsByC = _current?.CentroidsByC ?? new();
            _chosenK = _current?.ChosenK ?? 0;
            if (_current != null)
            {
                sldFraction.Value = _current.Fraction;      // reflect saved fraction
                if (txtFractionEdit != null)
                    txtFractionEdit.Text = $"{_current.Fraction * 100:F1}";
            }
        }

        /// <summary>Redraw whatever the visible step shows for the current category.</summary>
        private void RefreshCurrentStep()
        {
            if (_step == 2)
            {
                if (_sampleOutlier != null) { ApplyFraction(); RenderScatter(); }
                else
                {
                    scatterCanvas.Children.Clear();
                    txtOutlierInfo.Text = _activeClass != null ? "Click Detect outliers." : "";
                }
            }
            else if (_step == 3)
            {
                RefreshClusterStep();
            }
        }

        /// <summary>Show the current category's clustering charts, or a prompt if it
        /// has not been clustered yet.</summary>
        private void RefreshClusterStep()
        {
            if (_centroidsByC != null && _centroidsByC.Count > 0 && _cValues.Count > 0)
            {
                cboK.ItemsSource = _cValues;
                cboK.SelectedItem = _chosenK;
                txtSuggestK.Text = "";
                txtClusterInfo.Text = "";
            }
            else
            {
                cboK.ItemsSource = null;
                txtSuggestK.Text = "";
                clusterCanvas.Children.Clear();
                txtClusterInfo.Text = _activeClass != null
                    ? "Run clustering for this category." : "";
            }
            RenderValidity();
        }

        private async void OnDetectOutliers(object sender, RoutedEventArgs e)
        {
            if (_activeClass == null) { MessageBox.Show("Pick a category.", "Land Facet Clustering"); return; }
            if (_activeClass.Count < 10)
            { MessageBox.Show("This category has too few cells to analyze.", "Land Facet Clustering"); return; }
            await DetectOutliersCoreAsync();
        }

        /// <summary>If the current category has no outliers yet, optionally tell the
        /// user (the auto-run notice) and run detection at the current fraction.
        /// Returns true when outliers are ready.</summary>
        private async System.Threading.Tasks.Task<bool> EnsureOutliersAsync(bool announce)
        {
            if (_sampleOutlier != null) return true;
            if (_activeClass == null || _activeClass.Count < 10) return false;
            if (announce)
                MessageBox.Show(
                    $"Outliers have not been set for {CurrentItem()?.BaseLabel ?? "this category"} yet.\n\n"
                    + $"Running outlier detection now at the current fraction "
                    + $"({sldFraction.Value * 100:F0}%). You can fine-tune it on the Outliers step "
                    + "and re-run the clustering if you change it.",
                    "Land Facet Clustering", MessageBoxButton.OK, MessageBoxImage.Information);
            await DetectOutliersCoreAsync();
            return _sampleOutlier != null;
        }

        private async System.Threading.Tasks.Task DetectOutliersCoreAsync()
        {
            if (_activeClass == null || _activeClass.Count < 10) return;
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
                SaveCurrent();
                CurrentItem()?.Refresh();       // Step 2 list now shows "✓ set"
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

        /// <summary>Fill the X/Y axis pickers with the variable names. Outlier
        /// detection is multidimensional (done once); these only change which 2-D
        /// slice of that distribution is drawn.</summary>
        private void PopulateScatterAxes()
        {
            var names = _stack.VariableNames;
            _syncingAxes = true;
            cboScatterX.ItemsSource = names;
            cboScatterY.ItemsSource = names;
            _scatterX = 0;
            _scatterY = names.Length >= 2 ? 1 : 0;
            cboScatterX.SelectedIndex = _scatterX;
            cboScatterY.SelectedIndex = _scatterY;
            _syncingAxes = false;
            bool multi = names.Length >= 2;
            cboScatterY.IsEnabled = multi;            // 1 variable -> Y is density
            txtScatterHint.Text = names.Length > 2
                ? $"outliers use all {names.Length} variables — this is a 2-D slice"
                : (multi ? "" : "one variable — Y shows density");
        }

        private void OnScatterAxisChanged(object sender, SelectionChangedEventArgs e)
        {
            if (_syncingAxes) return;
            if (cboScatterX.SelectedIndex >= 0) _scatterX = cboScatterX.SelectedIndex;
            if (cboScatterY.SelectedIndex >= 0) _scatterY = cboScatterY.SelectedIndex;
            RenderScatter();
        }

        private static readonly Brush AxisBrush = Frozen(Color.FromRgb(0xCC, 0xCC, 0xCC));
        private static readonly Brush TickBrush = Frozen(Color.FromRgb(0x8A, 0x8A, 0x8A));
        private static readonly Brush AxisTitleBrush = Frozen(Color.FromRgb(0x66, 0x66, 0x66));

        private void RenderScatter()
        {
            scatterCanvas.Children.Clear();
            if (_sample == null || _sampleOutlier == null) return;
            double w = scatterCanvas.ActualWidth, h = scatterCanvas.ActualHeight;

            const double padL = 58, padR = 16, padT = 12, padB = 34;
            if (w < padL + padR + 30 || h < padT + padB + 30) return;
            double x0 = padL, x1 = w - padR, y0 = padT, y1 = h - padB;

            int d = _sample[0].Length;
            bool oneD = d < 2;
            int vx = Math.Min(Math.Max(_scatterX, 0), d - 1);
            int vy = oneD ? vx : Math.Min(Math.Max(_scatterY, 0), d - 1);
            var xv = _sample.Select(r => r[vx]).ToArray();
            var yv = oneD ? _sampleDensity : _sample.Select(r => r[vy]).ToArray();
            double minx = xv.Min(), maxx = xv.Max(), miny = yv.Min(), maxy = yv.Max();
            if (maxx <= minx) maxx = minx + 1;
            if (maxy <= miny) maxy = miny + 1;

            // Axis lines.
            AddAxisLine(x0, y0, x0, y1);
            AddAxisLine(x0, y1, x1, y1);

            // Tick marks + a few numeric labels (4 per axis), subdued.
            for (int i = 0; i < 4; i++)
            {
                double f = i / 3.0;
                double xpx = x0 + f * (x1 - x0);
                AddAxisLine(xpx, y1, xpx, y1 + 3);
                AddTick(FormatTick(minx + f * (maxx - minx)), xpx - 30, y1 + 5, 60,
                        TextAlignment.Center);

                double ypx = y1 - f * (y1 - y0);
                AddAxisLine(x0 - 3, ypx, x0, ypx);
                AddTick(FormatTick(miny + f * (maxy - miny)), 18, ypx - 7, x0 - 22,
                        TextAlignment.Right);
            }

            // Sample points.
            for (int i = 0; i < xv.Length; i++)
            {
                double px = x0 + (xv[i] - minx) / (maxx - minx) * (x1 - x0);
                double py = y1 - (yv[i] - miny) / (maxy - miny) * (y1 - y0);
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

            // Axis titles: X centered under the axis, Y vertical along the left.
            var xt = new TextBlock
            {
                Text = _stack.VariableNames[vx], Foreground = AxisTitleBrush, FontSize = 11,
                Width = Math.Max(10, x1 - x0), TextAlignment = TextAlignment.Center,
            };
            Canvas.SetLeft(xt, x0);
            Canvas.SetTop(xt, h - 15);
            scatterCanvas.Children.Add(xt);

            var yt = new TextBlock
            {
                Text = oneD ? "density" : _stack.VariableNames[vy],
                Foreground = AxisTitleBrush, FontSize = 11,
                Width = Math.Max(10, y1 - y0), TextAlignment = TextAlignment.Center,
                // Rotate -90 (counter-clockwise) about the element's top-left so the
                // label climbs from the bottom of the plot to the top, reading
                // bottom-to-top, in the left margin. (+90 sends it off-screen.)
                RenderTransform = new RotateTransform(-90),
            };
            Canvas.SetLeft(yt, 2);
            Canvas.SetTop(yt, y1);
            scatterCanvas.Children.Add(yt);
        }

        private void AddAxisLine(double x1, double y1, double x2, double y2)
        {
            scatterCanvas.Children.Add(new Line
            {
                X1 = x1, Y1 = y1, X2 = x2, Y2 = y2,
                Stroke = AxisBrush, StrokeThickness = 1,
            });
        }

        private void AddTick(string text, double left, double top, double width, TextAlignment align)
        {
            var t = new TextBlock
            {
                Text = text, Foreground = TickBrush, FontSize = 10,
                Width = Math.Max(10, width), TextAlignment = align,
            };
            Canvas.SetLeft(t, left);
            Canvas.SetTop(t, top);
            scatterCanvas.Children.Add(t);
        }

        private static string FormatTick(double v)
        {
            double a = Math.Abs(v);
            if (a >= 1000) return v.ToString("N0", CultureInfo.InvariantCulture);
            if (a >= 10) return v.ToString("0.#", CultureInfo.InvariantCulture);
            if (a >= 1) return v.ToString("0.##", CultureInfo.InvariantCulture);
            return v.ToString("0.###", CultureInfo.InvariantCulture);
        }

        // ---- step 3: clusters -----------------------------------------------
        private async void OnRunClustering(object sender, RoutedEventArgs e)
        {
            if (!double.TryParse(txtPsi.Text, out double psi) || psi <= 1.0)
            { MessageBox.Show("Fuzziness must be greater than 1 (for example 1.5).", "Land Facet Clustering"); return; }
            if (!int.TryParse(txtRestarts.Text, out int restarts) || restarts < 1) restarts = 12;

            if (!await EnsureOutliersAsync(announce: true)) return;   // outliers must exist first

            var raw = new List<double[]>();
            for (int i = 0; i < _sample.Length; i++)
                if (!_sampleOutlier[i]) raw.Add(_sample[i]);
            if (raw.Count < 10)
            { MessageBox.Show("Too few non-outlier cells to cluster.", "Land Facet Clustering"); return; }

            _psi = psi;
            btnCluster.IsEnabled = false;
            cboK.ItemsSource = null;      // clear k and its suggestion together while computing
            txtSuggestK.Text = "";
            StartSpinner();
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
                SaveCurrent();
                CurrentItem()?.Refresh();           // the category may now show "✓ set"
            }
            catch (Exception ex) { MessageBox.Show(ex.Message, "Land Facet Clustering"); }
            finally { StopSpinner(); btnCluster.IsEnabled = true; SetStatus(""); }
        }

        // A small rotating spinner shown while the clustering runs.
        private void StartSpinner()
        {
            clusterSpinner.Visibility = Visibility.Visible;
            var anim = new System.Windows.Media.Animation.DoubleAnimation(0, 360,
                new Duration(TimeSpan.FromSeconds(0.9)))
            { RepeatBehavior = System.Windows.Media.Animation.RepeatBehavior.Forever };
            spinnerRotate.BeginAnimation(RotateTransform.AngleProperty, anim);
        }

        private void StopSpinner()
        {
            spinnerRotate.BeginAnimation(RotateTransform.AngleProperty, null);
            clusterSpinner.Visibility = Visibility.Collapsed;
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
            // A degenerate partition (an empty crisp cluster, or two centroids that
            // land on the same point) drives the index formulas to infinity or NaN:
            // the min-pairwise-centroid-distance denominator in XB/XB*, the
            // centroid-distance denominator in DB, the fuzzy-distance denominator in
            // PBM. Such a c is not a real optimum, so it does not get a vote -- an
            // infinite PBM would otherwise always "win" the maximise indices, and a
            // NaN first value would freeze the comparison (every NaN test is false).
            int best = -1;
            double bv = 0.0;
            foreach (int c in _cValues)
            {
                double v = get(_indicesByC[c]);
                if (double.IsNaN(v) || double.IsInfinity(v)) continue;
                if (best < 0 || (higher ? v > bv : v < bv)) { bv = v; best = c; }
            }
            return best < 0 ? _cValues[0] : best;   // no usable value -> smallest c
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
                // Scale to the plottable values only. A degenerate partition makes an
                // index infinite or NaN (see ArgBest); including it would make the
                // range infinite and every plotted y NaN, which WPF cannot draw. Such
                // a c is simply left off its chart.
                var finite = vals.Where(v => !double.IsNaN(v) && !double.IsInfinity(v)).ToArray();
                if (finite.Length > 0)      // still label the chart if nothing is plottable
                {
                    double mn = finite.Min(), mx = finite.Max();
                    if (mx <= mn) mx = mn + 1;

                    var poly = new Polyline { Stroke = Brushes.SteelBlue, StrokeThickness = 1.5 };
                    for (int i = 0; i < _cValues.Count; i++)
                    {
                        if (double.IsNaN(vals[i]) || double.IsInfinity(vals[i])) continue;
                        double px = x0 + (_cValues.Count > 1 ? (double)i / (_cValues.Count - 1) : 0) * pw;
                        double py = y0 + ph - (vals[i] - mn) / (mx - mn) * ph;
                        poly.Points.Add(new Point(px, py));
                        var dot = new Ellipse { Width = 4, Height = 4, Fill = Brushes.SteelBlue };
                        Canvas.SetLeft(dot, px - 2); Canvas.SetTop(dot, py - 2);
                        clusterCanvas.Children.Add(dot);
                    }
                    clusterCanvas.Children.Add(poly);
                }

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

        /// <summary>Index of the ".gdb" that actually ends a geodatabase segment
        /// of <paramref name="p"/> -- i.e. followed by a path separator or the
        /// end of the string -- or -1. A bare IndexOf(".gdb") is not enough: a
        /// FOLDER merely named like "My.gdb_exports" would match it, the path
        /// would be split at the wrong place, and the run would fail at the very
        /// end when Copy Raster targets a geodatabase that does not exist.</summary>
        private static int GdbIndex(string p)
        {
            for (int idx = p.IndexOf(".gdb", StringComparison.OrdinalIgnoreCase);
                 idx >= 0;
                 idx = p.IndexOf(".gdb", idx + 1, StringComparison.OrdinalIgnoreCase))
            {
                int after = idx + 4;
                if (after == p.Length || p[after] == '\\' || p[after] == '/')
                    return idx;
            }
            return -1;
        }

        private static bool InGdb(string p) => GdbIndex(p) >= 0;

        private static OutputTarget GdbTarget(string path)
        {
            int idx = GdbIndex(path);
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
            // The plain 3-arg overload is the one that works (passing an explicit flags
            // value makes the GP module's eval_modal throw). It adds the result layer to
            // the map itself, so the caller does NOT create a second layer.
            var res = await ArcGIS.Desktop.Core.Geoprocessing.Geoprocessing.ExecuteToolAsync(
                "management.CopyRaster", args, env);
            if (res.IsFailed)
                throw new InvalidOperationException(
                    "Copying the raster into the geodatabase failed: "
                    + string.Join("; ", res.ErrorMessages.Select(m => m.Text)));
            return gdbTarget;
        }

        // Distinct base hues (degrees) for successive categories.
        private static readonly double[] CategoryHues =
            { 210, 30, 130, 275, 0, 190, 50, 320, 100, 240, 60, 300 };

        /// <summary>Colour the land-facet raster with a unique-value colorizer: one
        /// hue per category, its clusters shaded light-to-dark, labelled
        /// "Class 1, Cluster 1 (N cells)". Must run on the MCT.</summary>
        private static void ApplyFacetSymbology(RasterLayer layer,
            List<(int id, double catValue, string catLabel, int cluster, long count)> legend)
        {
            var groups = new List<ArcGIS.Core.CIM.CIMRasterUniqueValueGroup>();
            int ci = 0;
            foreach (var g in legend.GroupBy(l => l.catValue).OrderBy(g => g.Key))
            {
                double hue = CategoryHues[ci % CategoryHues.Length];
                var items = g.OrderBy(l => l.cluster).ToList();
                int k = items.Count;
                var classes = new List<ArcGIS.Core.CIM.CIMRasterUniqueValueClass>();
                for (int j = 0; j < k; j++)
                {
                    var it = items[j];
                    double t = k > 1 ? (double)j / (k - 1) : 0.0;   // 0 = light, 1 = dark
                    var (r, gg, b) = HsvToRgb(hue, 0.62, 0.94 - 0.46 * t);
                    classes.Add(new ArcGIS.Core.CIM.CIMRasterUniqueValueClass
                    {
                        Values = new[] { it.id.ToString(CultureInfo.InvariantCulture) },
                        Label = $"{it.catLabel}, Cluster {it.cluster} ({it.count:N0} cells)",
                        Color = new ArcGIS.Core.CIM.CIMRGBColor
                            { R = (float)r, G = (float)gg, B = (float)b, Alpha = 100 },
                        Visible = true, Editable = true,
                    });
                }
                groups.Add(new ArcGIS.Core.CIM.CIMRasterUniqueValueGroup
                {
                    Heading = items[0].catLabel,
                    Classes = classes.ToArray(),
                });
                ci++;
            }
            var colorizer = new ArcGIS.Core.CIM.CIMRasterUniqueValueColorizer
            {
                FieldName = "Value",
                Groups = groups.ToArray(),
                UseDefaultColor = false,
                IsDefaultColorVisible = false,
            };
            layer.SetColorizer(colorizer);
        }

        private static (double r, double g, double b) HsvToRgb(double h, double s, double v)
        {
            h = ((h % 360) + 360) % 360;
            double c = v * s;
            double x = c * (1 - Math.Abs(h / 60.0 % 2 - 1));
            double m = v - c;
            double r1, g1, b1;
            if (h < 60) { r1 = c; g1 = x; b1 = 0; }
            else if (h < 120) { r1 = x; g1 = c; b1 = 0; }
            else if (h < 180) { r1 = 0; g1 = c; b1 = x; }
            else if (h < 240) { r1 = 0; g1 = x; b1 = c; }
            else if (h < 300) { r1 = x; g1 = 0; b1 = c; }
            else { r1 = c; g1 = 0; b1 = x; }
            return ((r1 + m) * 255.0, (g1 + m) * 255.0, (b1 + m) * 255.0);
        }

        /// <summary>Populate the step-4 overview of every category and its status.</summary>
        private void RefreshFacetStep()
        {
            SaveCurrent();
            lstFacetStatus.ItemsSource = _categoryItems.Select(ci =>
            {
                var a = ci.Analysis;
                return new
                {
                    Category = CategoryLabel(ci.Value),
                    Outlier = a.OutliersReady ? $"{a.Fraction * 100:F1}%" : "—",
                    K = a.Clustered ? a.ChosenK.ToString() : "—",
                    Status = a.IsSet ? "ready" : (a.OutliersReady ? "needs clustering" : "not set"),
                };
            }).ToList();
            int nSet = _analyses.Values.Count(a => a.IsSet);
            txtFacetSummary.Text = nSet == 0
                ? "No categories are specified yet. Go back to set the outliers and clustering for "
                  + "at least one category, then Generate."
                : $"{nSet} of {_categoryItems.Count} categories are specified. Set the confusion "
                  + "threshold and output raster, then Generate.";
        }

        private async void OnGenerate(object sender, RoutedEventArgs e)
        {
            SaveCurrent();
            var setItems = _categoryItems.Where(ci => ci.Analysis.IsSet).ToList();
            if (setItems.Count == 0)
            { MessageBox.Show("Set the outliers and clustering for at least one category first.", "Land Facet Clustering"); return; }
            if (string.IsNullOrWhiteSpace(txtOut.Text))
            { MessageBox.Show("Enter an output raster name or path.", "Land Facet Clustering"); return; }
            if (!double.TryParse(txtConf.Text, out double confThresh) || confThresh <= 0 || confThresh > 1)
            { MessageBox.Show("The confusion-index threshold must be between 0 and 1 (for example 0.6).", "Land Facet Clustering"); return; }

            int unset = _categoryItems.Count - setItems.Count;
            if (unset > 0)
            {
                var ask = MessageBox.Show(
                    $"{unset} of {_categoryItems.Count} categories are not set and will be left NoData "
                    + $"in the output.\n\nGenerate the {setItems.Count} ready "
                    + $"{Plural(setItems.Count, "category", "categories")} now?",
                    "Land Facet Clustering", MessageBoxButton.OKCancel, MessageBoxImage.Question);
                if (ask != MessageBoxResult.OK) return;
            }

            string typedOut = txtOut.Text;      // read the control on the UI thread
            OutputTarget target;
            try { target = await QueuedTask.Run(() => ResolveOutputTarget(typedOut)); }
            catch (Exception ex) { MessageBox.Show(ex.Message, "Land Facet Clustering"); return; }
            if (target == null)
            { MessageBox.Show("Enter an output raster name or path.", "Land Facet Clustering"); return; }

            int W = _stack.Width, H = _stack.Height;
            // The output grid is held as one flat int[], and cell indices are cast
            // down to int when the classified values are written back, so the cell
            // count has to fit in an int. Fail with an explanation rather than
            // overflowing the allocation into a wrong-sized (or negative) array.
            long nCells = (long)W * H;
            if (nCells > int.MaxValue)
            {
                MessageBox.Show(
                    $"The categorical raster is {W:N0} x {H:N0} = {nCells:N0} cells, which is larger "
                    + $"than this tool can hold in memory ({int.MaxValue:N0} cells).\n\n"
                    + "Clip the analysis to a smaller area (the Wildland Blocks clip polygon on "
                    + "step 1) or resample the categorical raster to a coarser cell size.",
                    "Land Facet Clustering", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }
            var arr = new int[(int)nCells];
            for (int i = 0; i < arr.Length; i++) arr[i] = -9999;

            btnGenerate.IsEnabled = false;
            pbGen.Value = 0;
            txtGenInfo.Text = "";
            var legend = new List<(int id, double catValue, string catLabel, int cluster, long count)>();
            int offset = 0;
            int nSteps = setItems.Count + 4;   // classify each + write + copy + metadata + add
            try
            {
                // Classify each ready category into the shared grid, offsetting its
                // cluster IDs so every facet across every category is unique.
                for (int si = 0; si < setItems.Count; si++)
                {
                    var ci = setItems[si]; var a = ci.Analysis;
                    string label = ci.BaseLabel;
                    string catLabel = CategoryLabel(a.Data.Value);   // name / "Class <value>"
                    txtGenInfo.Text = $"Classifying {label}  ({si + 1} of {setItems.Count})…";
                    SetStatus(txtGenInfo.Text);
                    pbGen.Value = (double)si / nSteps;

                    var sorted = (double[])a.SampleDensity.Clone();
                    Array.Sort(sorted);
                    int oc = (int)Math.Round(a.Fraction * sorted.Length);
                    double densThresh = oc >= 1 ? sorted[oc - 1] : double.NegativeInfinity;

                    int k = a.ChosenK, off = offset;
                    var centers = a.CentroidsByC[k];
                    var kde = a.Kde; var mean = a.StdMean; var sd = a.StdSd; double psi = a.Psi;
                    var data = a.Data;
                    var res = await Task.Run(() => ClassifyInto(
                        arr, off, data, centers, mean, sd, psi, kde, densThresh, confThresh, k));
                    for (int c = 0; c < k; c++)
                        legend.Add((off + c + 1, a.Data.Value, catLabel, c + 1, res.counts[c]));
                    offset += k;
                }

                txtGenInfo.Text = "Writing the combined land-facet raster…";
                SetStatus(txtGenInfo.Text);
                double[] gt = { _stack.Extent.XMin, _stack.CellSizeX, 0.0,
                                _stack.Extent.YMax, 0.0, -_stack.CellSizeY };
                string wkt = _stack.SpatialRef?.Wkt ?? string.Empty;
                var rat = legend.Select(l => (l.id, l.count, l.catLabel, l.cluster)).ToList();
                double writeBase = (double)setItems.Count / nSteps, writeSpan = 1.0 / nSteps;
                var progress = new Progress<double>(f => pbGen.Value = writeBase + f * writeSpan);
                string written = await CorridorRasterWriter.WriteAsync(
                    arr, W, H, gt, wkt, -9999, target.WritePath, progress, rat: rat);

                string finalPath = written;
                bool addedByGp = false;
                if (target.CopyToGdb)
                {
                    txtGenInfo.Text = "Copying the raster into the geodatabase…";
                    SetStatus(txtGenInfo.Text);
                    pbGen.Value = (double)(setItems.Count + 1) / nSteps;
                    finalPath = await CopyIntoGeodatabase(written, target.FinalPath);
                    addedByGp = true;    // Copy Raster already added the layer to the map
                    foreach (var tmp in new[] { written, written + ".aux.xml", written + ".ovr" })
                        try { if (System.IO.File.Exists(tmp)) System.IO.File.Delete(tmp); } catch { }
                }

                // Stamp the linkage record so the Land Facet Density and
                // Mahalanobis tools can identify each facet and rebuild the
                // variables in the SAME ORDER this run used. It has to happen
                // here, after the raster is at its final location: the record
                // rides in the Esri metadata, the only carrier that survives
                // Copy Raster into a geodatabase. Best-effort -- an unstamped
                // raster still works, the later tools just ask the user instead.
                txtGenInfo.Text = "Recording the analysis metadata…";
                SetStatus(txtGenInfo.Text);
                pbGen.Value = (double)(setItems.Count + 2) / nSteps;
                _analysisId = await StampLinkageMetadataAsync(finalPath, legend, W, H, confThresh);

                txtGenInfo.Text = "Adding the result to the map…";
                SetStatus(txtGenInfo.Text);
                pbGen.Value = (double)(setItems.Count + 3) / nSteps;
                var legendForSym = legend
                    .Select(l => (l.id, l.catValue, l.catLabel, l.cluster, l.count)).ToList();
                string dsName = System.IO.Path.GetFileNameWithoutExtension(finalPath);
                _symbologyWarning = null;
                await QueuedTask.Run(() =>
                {
                    var map = MapView.Active?.Map;
                    if (map == null) return;
                    // The geodatabase path was added by Copy Raster; find that layer.
                    // A GeoTIFF path is created here (Copy Raster was not used).
                    RasterLayer lyr = addedByGp
                        ? map.GetLayersAsFlattenedList().OfType<RasterLayer>()
                             .LastOrDefault(l => string.Equals(l.Name, dsName,
                                 StringComparison.OrdinalIgnoreCase))
                        : LayerFactory.Instance.CreateLayer(new Uri(finalPath), map) as RasterLayer;
                    // Custom symbology is best-effort: never let it abort a successful write.
                    if (lyr != null)
                        try { ApplyFacetSymbology(lyr, legendForSym); }
                        catch (Exception sx) { _symbologyWarning = sx.Message; }
                });
                pbGen.Value = 1;

                var sb = new StringBuilder();
                sb.AppendLine($"Combined land-facet raster written: {finalPath}");
                sb.AppendLine($"{legend.Count} facets across {setItems.Count} "
                    + $"{Plural(setItems.Count, "category", "categories")} "
                    + $"(fuzziness {_psi:0.##}, confusion threshold {confThresh:0.##}).");
                sb.AppendLine();
                foreach (var l in legend)
                    sb.AppendLine($"    Facet {l.id}:  {l.catLabel}, cluster {l.cluster}  ({l.count:N0} cells)");
                sb.AppendLine();
                if (_analysisId != null)
                    sb.AppendLine("Analysis metadata recorded (id " + _analysisId
                        + "). The Land Facet Density and Mahalanobis tools will read "
                        + "the facet list and variable order from this raster.");
                else
                    sb.AppendLine("Note: the analysis metadata could not be recorded on "
                        + "this raster. It is still fully usable, but the Land Facet "
                        + "Density and Mahalanobis tools will ask you to identify the "
                        + "facets and re-enter the variables in the same order used here: "
                        + string.Join(", ", _stack?.VariableNames ?? Array.Empty<string>()) + ".");
                if (_symbologyWarning != null)
                    sb.AppendLine().AppendLine("Note: default symbology was applied "
                        + "(custom colors could not be set: " + _symbologyWarning + ").");
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

        /// <summary>Assemble and stamp the land facet linkage record onto the
        /// finished raster. Returns the analysis id, or null if it could not be
        /// written (which is not fatal -- see CorridorMetadataWriter).
        ///
        /// The variable ORDER carried here is the load-bearing part: the
        /// Mahalanobis mean vector and covariance matrix have to be built in the
        /// same order the clustering used, and a mismatch produces distances
        /// that are wrong without looking wrong.</summary>
        private async Task<string> StampLinkageMetadataAsync(
            string finalPath,
            List<(int id, double catValue, string catLabel, int cluster, long count)> legend,
            int width, int height, double confThresh)
        {
            try
            {
                string analysisId = Guid.NewGuid().ToString();
                var facets = legend
                    .Select(l => new CorridorMetadataWriter.FacetEntry(
                        l.id, l.catValue, l.catLabel, l.cluster, l.count))
                    .ToList();

                // Per-category settings, so a later tool (or a puzzled user six
                // months on) can see exactly how each class was clustered.
                var perCategory = new List<object>();
                foreach (var kv in _analyses)
                {
                    var a = kv.Value;
                    if (a == null || a.ChosenK <= 0) continue;
                    perCategory.Add(new Dictionary<string, object>
                    {
                        ["category_value"] = kv.Key,
                        ["category_label"] = CategoryLabel(kv.Key),
                        ["k"] = a.ChosenK,
                        ["outlier_fraction"] = a.Fraction,
                        ["fuzziness"] = a.Psi,
                        ["cells"] = a.Data?.Count ?? 0,
                    });
                }

                var parameters = new Dictionary<string, object>
                {
                    ["fuzziness"] = _psi,
                    ["confusion_threshold"] = confThresh,
                    ["outlier_method"] = "gaussian_kde_scott",
                    ["distance"] = "squared_euclidean_standardized",
                    ["class_name_field"] = _classNameField ?? string.Empty,
                    ["categories"] = perCategory,
                };

                var catItem = cboCat.SelectedItem as LayerItem;
                return await CorridorMetadataWriter.StampFacetsAsync(
                    finalPath, analysisId, _stack?.VariableNames, _varPaths, facets,
                    width, height, parameters,
                    catItem?.Path ?? catItem?.Name ?? string.Empty);
            }
            catch
            {
                return null;   // never let metadata stamping fail a good run
            }
        }

        /// <summary>Classify every cell of one category into its highest-membership
        /// cluster and write <c>idOffset + cluster + 1</c> into the shared grid,
        /// leaving outliers and over-the-confusion-threshold cells untouched.</summary>
        private static (long[] counts, long nOut, long nConf, long nWritten)
            ClassifyInto(int[] arr, int idOffset, RasterStack.ClassData cls,
                        double[][] centers, double[] mean, double[] sd, double psi,
                        LandFacetEngine.KdeModel kde, double densThresh, double confThresh, int k)
        {
            int n = cls.Count;
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
                    arr[(int)cls.CellIndex[i]] = idOffset + c.TopCluster + 1;
                    counts[c.TopCluster]++;
                    nWritten++;
                }
            }
            return (counts, nOut, nConf, nWritten);
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

            if (s == 2) RefreshCurrentStep();
            else if (s == 3) RefreshClusterStep();
            else if (s == 4) RefreshFacetStep();

            StyleStep(step1chip, step1lbl, 0, s == 1);
            StyleStep(step2chip, step2lbl, 1, s == 2);
            StyleStep(step3chip, step3lbl, 2, s == 3);
            StyleStep(step4chip, step4lbl, 3, s == 4);

            btnBack.IsEnabled = s > 1;
            btnNext.IsEnabled = s < 4;
        }

        // Per-step accent colors (matching the tool's facet-mosaic icon palette).
        private static readonly Color[] StepColors =
        {
            Color.FromRgb(0x3B, 0x82, 0xC4),   // 1 Setup   – blue
            Color.FromRgb(0xE8, 0x91, 0x2E),   // 2 Outliers – orange
            Color.FromRgb(0x7E, 0x5E, 0xA8),   // 3 Clusters – purple
            Color.FromRgb(0x57, 0xA6, 0x39),   // 4 Facets   – green
        };

        /// <summary>Paint a step chip: the active step is a filled pill in its accent
        /// color with white text; the others show their accent color as text on a
        /// transparent chip.</summary>
        private void StyleStep(Border chip, TextBlock lbl, int idx, bool active)
        {
            var accent = StepColors[idx];
            if (active)
            {
                chip.Background = new SolidColorBrush(accent);
                lbl.Foreground = Brushes.White;
                lbl.FontWeight = FontWeights.SemiBold;
            }
            else
            {
                chip.Background = Brushes.Transparent;
                lbl.Foreground = new SolidColorBrush(accent);
                lbl.FontWeight = FontWeights.Normal;
            }
        }

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
                    bool oneA = aspectVars.Count == 1;
                    string aLead = oneA
                        ? $"The variable raster {aspectVars[0]} appears to be an "
                          + "ASPECT (or D-infinity flow-direction) raster.\n\n"
                        : $"The variable rasters {string.Join(", ", aspectVars)} appear to be "
                          + "ASPECT (or D-infinity flow-direction) rasters.\n\n";
                    var r = MessageBox.Show(
                        aLead
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
                    var choice = AskSlope(slopeVars);
                    if (choice == SlopeChoice.DoNotProceed) return;
                    if (choice == SlopeChoice.ConvertDegrees) ConvertPercentToDegrees(slopeVars);
                }
                ShowStep(2);
                return;
            }
            if (_step == 2 && _sampleOutlier == null)
            {
                MessageBox.Show("Detect outliers for this category first.", "Land Facet Clustering");
                return;
            }
            if (_step == 3)
            {
                SaveCurrent();
                if (!_analyses.Values.Any(a => a.IsSet))
                {
                    MessageBox.Show("Cluster at least one category and choose k first.", "Land Facet Clustering");
                    return;
                }
            }
            if (_step < 4) ShowStep(_step + 1);
        }

        private void OnClose(object sender, RoutedEventArgs e) => Close();

        // ---- help windows ----------------------------------------------------
        private void OnOutlierHelp(object sender, RoutedEventArgs e)
            => ShowHelp("About excluding outliers", BuildOutlierHelp());

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
                Icon = JennessIcon(),
                Width = 660,
                Height = 660,
                MinWidth = 420,
                MinHeight = 300,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                ShowInTaskbar = false,
                Content = viewer,
            }.Show();
        }

        private static ImageSource _jennessIcon;
        private static bool _jennessIconTried;

        /// <summary>The embedded Jenness Enterprises logo for code-created windows'
        /// title bars (loaded once; null if unavailable).</summary>
        private static ImageSource JennessIcon()
        {
            if (_jennessIconTried) return _jennessIcon;
            _jennessIconTried = true;
            try
            {
                _jennessIcon = System.Windows.Media.Imaging.BitmapFrame.Create(new Uri(
                    "pack://application:,,,/WildlifeTools;component/Images/JennessEnterprises.ico"));
            }
            catch { _jennessIcon = null; }
            return _jennessIcon;
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

        private static FlowDocument BuildOutlierHelp()
        {
            var d = HelpDoc();
            d.Blocks.Add(BodyPara(
                new Run("This step finds and sets aside "),
                new Bold(new Run("outliers")),
                new Run(" — cells whose combination of variable values is unusual for the selected category — before the clustering is run.")));

            d.Blocks.Add(HeadPara("Why exclude outliers"));
            d.Blocks.Add(BodyPara(new Run(
                "Fuzzy c-means places cluster centres to fit the data. A handful of oddball cells (a strange mix of elevation, slope, insolation, …) can drag a centre toward themselves or spawn a tiny, meaningless cluster, distorting the facets that describe the rest of the landscape. Setting these cells aside first lets the clustering describe the typical terrain cleanly.")));

            d.Blocks.Add(HeadPara("How they are found"));
            d.Blocks.Add(BodyPara(
                new Run("The tool standardizes every variable (so each counts equally) and estimates the "),
                new Bold(new Run("multivariate density")),
                new Run(" of the cells with a Gaussian kernel density estimate. Cells sitting in the sparse, low-density tails — the ones least like everything else — are flagged as outliers.")));
            d.Blocks.Add(BodyPara(
                new Run("The "),
                new Bold(new Run("outlier fraction")),
                new Run(" is simply how much of the low-density tail to remove: at 10% (the default) the 10% of cells with the lowest density become outliers. Raise it to be stricter, lower it to keep more cells. In the scatter plot, outliers are drawn in "),
                new Run("red") { Foreground = Brushes.OrangeRed, FontWeight = FontWeights.SemiBold },
                new Run(" and retained cells in "),
                new Run("blue") { Foreground = new SolidColorBrush(Color.FromRgb(0x5B, 0x8D, 0xB8)), FontWeight = FontWeights.SemiBold },
                new Run("; the plot updates as you move the slider or type a percentage.")));

            d.Blocks.Add(HeadPara("Effect on the final land-facet raster"));
            d.Blocks.Add(Bullets(
                ("Outlier cells are left NoData", "they are not assigned to any facet in the output raster for this category."),
                ("A higher fraction", "removes more cells (more NoData) but gives tighter, more representative facets."),
                ("A lower fraction", "keeps more of the map but lets unusual cells influence — and slightly blur — the facets.")));
            d.Blocks.Add(BodyPara(new Run(
                "Outlier detection is done separately for each category, so you can tune it per category. It follows the Land Facet Corridor Designer method (density-based outlier removal before fuzzy clustering).")));
            return d;
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
                new Run(": every retained cell is labelled with the cluster it belongs to most strongly. "),
                new Run("It processes all of the "),
                new Bold(new Run("fully-specified categories")),
                new Run(" at once (those with both outliers and clustering set — marked “✓ set”) and writes them into "),
                new Bold(new Run("one combined raster")),
                new Run(", giving every facet a unique ID across categories. "),
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
                ("Unspecified categories", "facets are written for every fully-specified category; cells in categories you have not specified (no outliers or clustering yet), and any cells outside the analysis, are left NoData.")));

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
