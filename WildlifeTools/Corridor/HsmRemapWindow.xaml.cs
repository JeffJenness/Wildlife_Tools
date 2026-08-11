using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Shapes;
using ArcGIS.Core.Data;
using ArcGIS.Core.Data.DDL;
using ArcGIS.Core.Data.Raster;
using ArcGIS.Desktop.Catalog;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using DdlField = ArcGIS.Core.Data.DDL.FieldDescription;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// "Create Reclass Tables for the Create Habitat Suitability Model Tool":
    /// pick a single-band raster (map layer or disk/geodatabase), see its
    /// histogram and statistics, build a reclassify-by-range (From/To/New) or
    /// reclassify-by-value (Original/New) remap, watch translucent washes
    /// cover the histogram as rows are entered, and save the result where the
    /// Create Habitat Suitability Model GP tool expects it: an ASCII .txt in
    /// the original CorridorDesigner format next to a file raster, or a
    /// geodatabase TABLE (FromValue/ToValue/NewValue or OriginalValue/
    /// NewValue) next to a geodatabase raster. Warns when the remap does not
    /// cover the raster's numeric range or the new values miss/exceed 0-100.
    /// Parchment skin, matching the suite's other non-modal windows.
    /// </summary>
    public partial class HsmRemapWindow : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        // ---- remap row --------------------------------------------------
        public sealed class RemapRow : INotifyPropertyChanged
        {
            private string _from = "", _to = "", _new = "";
            private Brush _swatch = Brushes.Transparent;
            public string FromText
            { get => _from; set { _from = value; Ping(nameof(FromText)); } }
            public string ToText
            { get => _to; set { _to = value; Ping(nameof(ToText)); } }
            public string NewText
            { get => _new; set { _new = value; Ping(nameof(NewText)); } }
            public Brush Swatch
            { get => _swatch; set { _swatch = value; Ping(nameof(Swatch)); } }

            public double? From => Num(_from);
            public double? To => Num(_to);
            public double? NewValue => Num(_new);
            private static double? Num(string s) =>
                double.TryParse(s, NumberStyles.Float,
                                CultureInfo.CurrentCulture, out double d)
                    ? d
                    : (double.TryParse(s, NumberStyles.Float,
                                       CultureInfo.InvariantCulture, out d)
                        ? d : (double?)null);

            public event PropertyChangedEventHandler PropertyChanged;
            private void Ping(string n) =>
                PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(n));
        }

        // one entry in the raster combo
        private sealed class RasterSource
        {
            public string Name;
            public RasterLayer Layer;      // map layer, or...
            public string Path;            // ...full catalog path from Browse
            public override string ToString() => Name;
        }

        private readonly ObservableCollection<RemapRow> _rows = new();

        // scan results (set on the MCT, read on the UI thread after dispatch)
        private double _min, _max, _mean, _sd;
        private long _nValid, _nNoData;
        private double[] _hist;                 // binned counts
        private List<double> _uniques;          // null when not enumerable
        private Dictionary<double, long> _uniqueCounts;   // with _uniques
        private double _halfw = 0.5;            // half a discrete bin, value units
        private bool _newValuesOutside;         // outside 0-100 (vs just partial)

        // Remap schemes saved THIS SESSION, keyed by raster path -- switching
        // back to a raster you already made a table for restores it.
        private readonly Dictionary<string, (bool range,
            List<(double lo, double hi, double nv)> rows)> _session =
            new(StringComparer.OrdinalIgnoreCase);
        private bool _haveRaster;
        private string _rasterPath;             // catalog path (default output)
        private string _rasterBase;             // file/table base name

        // wash palette for the coverage overlays (translucent watercolors)
        private static readonly Color[] Wash =
        {
            Color.FromRgb(196, 106, 74),    // terracotta
            Color.FromRgb(201, 162, 39),    // ochre
            Color.FromRgb(122, 143, 90),    // sage
            Color.FromRgb(90, 122, 143),    // slate
            Color.FromRgb(143, 90, 122),    // plum
            Color.FromRgb(160, 124, 62),    // umber
        };

        public HsmRemapWindow()
        {
            InitializeComponent();
            grdRemap.ItemsSource = _rows;
            _rows.CollectionChanged += (s, e) => Dirty();
            LoadMapLayers();
            UpdateModeColumns();
        }

        // ---- raster choosing --------------------------------------------
        private void LoadMapLayers()
        {
            var map = MapView.Active?.Map;
            cboRaster.Items.Clear();
            if (map == null) return;
            foreach (var rl in map.GetLayersAsFlattenedList().OfType<RasterLayer>())
                cboRaster.Items.Add(new RasterSource { Name = rl.Name, Layer = rl });
        }

        private void OnBrowseRaster(object sender, RoutedEventArgs e)
        {
            var dlg = new OpenItemDialog
            {
                Title = "Choose a single-band raster",
                MultiSelect = false,
                BrowseFilter = ArcGIS.Desktop.Core.BrowseProjectFilter
                    .GetFilter(ItemFilters.Rasters),
            };
            if (dlg.ShowDialog() != true || dlg.Items == null) return;
            var item = dlg.Items.FirstOrDefault();
            if (item == null) return;
            var src = new RasterSource { Name = item.Path, Path = item.Path };
            cboRaster.Items.Add(src);
            cboRaster.SelectedItem = src;      // triggers OnRasterChosen
        }

        private void OnRasterChosen(object sender, SelectionChangedEventArgs e)
        {
            if (cboRaster.SelectedItem is RasterSource src)
                _ = ScanRasterAsync(src);
        }

        // ---- the scan: statistics, histogram, unique values -------------
        private const int HistBins = 120;
        private const long MaxScanCells = 40_000_000;
        private const int UniqueCap = 4096;
        private const int DiscreteBarMax = 32;   // per-value bars up to here

        /// <summary>Gaussian-smoothed copy of ``data`` (sigma in bins) --
        /// the cheap kernel-density approximation drawn over the bars.</summary>
        private static double[] GaussianSmooth(double[] data, double sigma)
        {
            int radius = (int)Math.Ceiling(sigma * 3);
            var kernel = new double[2 * radius + 1];
            double ksum = 0;
            for (int i = -radius; i <= radius; i++)
            {
                kernel[i + radius] = Math.Exp(-(i * i) / (2 * sigma * sigma));
                ksum += kernel[i + radius];
            }
            var outp = new double[data.Length];
            for (int i = 0; i < data.Length; i++)
            {
                double acc = 0, wsum = 0;
                for (int k = -radius; k <= radius; k++)
                {
                    int j = i + k;
                    if (j < 0 || j >= data.Length) continue;
                    acc += data[j] * kernel[k + radius];
                    wsum += kernel[k + radius];
                }
                outp[i] = wsum > 0 ? acc * (ksum / wsum) / ksum : 0;
            }
            return outp;
        }

        private async System.Threading.Tasks.Task ScanRasterAsync(RasterSource src)
        {
            txtSampleNote.Text = "Reading the raster...";
            btnSave.IsEnabled = false;
            btnUnique.IsEnabled = false;
            string problem = null;
            string sampleNote = "";
            try
            {
                await QueuedTask.Run(() =>
                {
                    Raster raster;
                    if (src.Layer != null)
                    {
                        // Prefer the DATASET's full raster over the layer's
                        // configured one: a layer raster (notably an integer
                        // GRID) may fail to declare its NoData value, so
                        // masked cells would surface as phantom zeros.
                        raster = (src.Layer.GetRaster()?.GetRasterDataset()
                                     as RasterDataset)?.CreateFullRaster()
                                 ?? src.Layer.GetRaster();
                        _rasterPath = (src.Layer.GetRaster()?.GetRasterDataset()
                            ?.GetDatastore()?.GetConnectionString() != null)
                            ? LayerCatalogPath(src.Layer) : src.Layer.Name;
                    }
                    else
                    {
                        raster = OpenFromPath(src.Path);
                        _rasterPath = src.Path;
                    }
                    if (raster == null)
                        throw new InvalidOperationException(
                            "The raster could not be opened.");
                    if (raster.GetBandCount() != 1)
                        throw new InvalidOperationException(
                            "That raster has " + raster.GetBandCount()
                            + " bands; this tool needs a SINGLE-band raster "
                            + "(pick one band, or a single-band product).");
                    _rasterBase = BaseName(_rasterPath);

                    int w = raster.GetWidth(), h = raster.GetHeight();
                    long total = (long)w * h;
                    if (total > MaxScanCells)
                    {
                        // Downsample by nearest neighbour (never averaged --
                        // values must stay exact for uniques and NoData).
                        double f = Math.Sqrt((double)total / MaxScanCells);
                        raster.SetWidth(Math.Max(1, (int)(w / f)));
                        raster.SetHeight(Math.Max(1, (int)(h / f)));
                        raster.SetResamplingType(
                            ArcGIS.Core.CIM.RasterResamplingType.NearestNeighbor);
                        raster.Refresh();
                        w = raster.GetWidth(); h = raster.GetHeight();
                        sampleNote = string.Format(CultureInfo.CurrentCulture,
                            "Large raster: statistics and histogram computed on a "
                            + "systematic sample of {0:N0} of {1:N0} cells.",
                            (long)w * h, total);
                    }
                    object nod = raster.GetNoDataValue();
                    ScanPixels(raster, w, h, nod);
                });
            }
            catch (Exception ex)
            {
                problem = ex.Message;
            }
            if (problem != null)
            {
                _haveRaster = false;
                txtSampleNote.Text = problem;
                MessageBox.Show(this, problem, Title);
                Redraw();
                return;
            }
            _haveRaster = true;
            txtMin.Text = Fmt(_min);
            txtMax.Text = Fmt(_max);
            txtMean.Text = Fmt(_mean);
            txtSd.Text = Fmt(_sd);
            txtCells.Text = _nValid.ToString("N0", CultureInfo.CurrentCulture);
            txtNoData.Text = _nNoData.ToString("N0", CultureInfo.CurrentCulture);
            txtSampleNote.Text = sampleNote;
            btnUnique.IsEnabled = _uniques != null;
            btnSave.IsEnabled = true;
            txtOutput.Text = DefaultOutputPath();
            UpdateOutputNote();
            await RestoreRowsForRasterAsync();
            Redraw();
            RecomputeNotes();
        }

        // ---- per-raster scheme memory + autoload ------------------------
        /// <summary>Replace the grid with a scheme (mode + rows).</summary>
        private void ApplyScheme(bool range,
            List<(double lo, double hi, double nv)> rows)
        {
            rbRange.IsChecked = range;
            rbValue.IsChecked = !range;
            _rows.Clear();
            foreach (var r in rows)
                _rows.Add(new RemapRow
                {
                    FromText = Raw(r.lo),
                    ToText = range ? Raw(r.hi) : "",
                    NewText = Raw(r.nv),
                });
        }

        private static string Raw(double v) =>
            v.ToString("0.######", CultureInfo.InvariantCulture);

        /// <summary>On switching rasters: clear the grid, then restore --
        /// first a scheme saved for this raster THIS session, else a remap
        /// table already sitting at the default output location (this
        /// tool's own naming), else leave the grid empty.</summary>
        private async System.Threading.Tasks.Task RestoreRowsForRasterAsync()
        {
            _rows.Clear();
            if (_session.TryGetValue(_rasterPath ?? "", out var saved))
            {
                ApplyScheme(saved.range, saved.rows);
                txtSampleNote.Text = (txtSampleNote.Text + "  Restored the "
                    + "scheme you saved for this raster earlier this "
                    + "session.").Trim();
                return;
            }
            string dflt = DefaultOutputPath();
            if (string.IsNullOrEmpty(dflt)) return;
            try
            {
                (bool range, List<(double, double, double)> rows)? found =
                    null;
                if (InGdbContainer(dflt))
                    found = await QueuedTask.Run(() => ReadGdbScheme(dflt));
                else if (File.Exists(dflt))
                    found = ReadAsciiScheme(dflt);
                if (found != null)
                {
                    ApplyScheme(found.Value.range, found.Value.rows);
                    txtSampleNote.Text = (txtSampleNote.Text + "  Loaded "
                        + "the existing remap table "
                        + System.IO.Path.GetFileName(dflt) + ".").Trim();
                }
            }
            catch { /* an unreadable table just means an empty grid */ }
        }

        private static (bool range, List<(double, double, double)> rows)?
            ReadAsciiScheme(string path)
        {
            var rows = new List<(double, double, double)>();
            bool anyRange = false;
            foreach (string raw in File.ReadAllLines(path))
            {
                string line = raw.Trim();
                if (line.Length == 0 || line.StartsWith("#")) continue;
                int colon = line.IndexOf(':');
                if (colon < 0) return null;
                if (!TryNum(line.Substring(colon + 1), out double nv))
                    return null;
                var nums = line.Substring(0, colon)
                    .Split((char[])null, StringSplitOptions.RemoveEmptyEntries);
                if (nums.Length == 1 && TryNum(nums[0], out double v))
                    rows.Add((v, v, nv));
                else if (nums.Length == 2 && TryNum(nums[0], out double lo)
                         && TryNum(nums[1], out double hi))
                {
                    rows.Add((lo, hi, nv));
                    anyRange = true;
                }
                else return null;
            }
            return rows.Count > 0 ? (anyRange, rows) : null;
        }

        private static bool TryNum(string s, out double d) =>
            double.TryParse(s.Trim(), NumberStyles.Float,
                            CultureInfo.InvariantCulture, out d);

        /// <summary>Read a remap TABLE of either schema, or null. MCT.</summary>
        private static (bool range, List<(double, double, double)> rows)?
            ReadGdbScheme(string outPath)
        {
            string gdbPath = System.IO.Path.GetDirectoryName(outPath);
            string name = System.IO.Path.GetFileName(outPath);
            if (!IsGdbWorkspace(gdbPath)) return null;
            try
            {
                using var gdb = new Geodatabase(
                    new FileGeodatabaseConnectionPath(new Uri(gdbPath)));
                var def = gdb.GetDefinitions<TableDefinition>()
                    .FirstOrDefault(d => string.Equals(d.GetName(), name,
                        StringComparison.OrdinalIgnoreCase));
                if (def == null) return null;
                var names = def.GetFields().Select(f => f.Name.ToLowerInvariant())
                    .ToHashSet();
                bool range = names.Contains("fromvalue")
                    && names.Contains("tovalue") && names.Contains("newvalue");
                bool byValue = names.Contains("originalvalue")
                    && names.Contains("newvalue");
                if (!range && !byValue) return null;
                var rows = new List<(double, double, double)>();
                using var table = gdb.OpenDataset<Table>(def.GetName());
                using var cur = table.Search(null, false);
                while (cur.MoveNext())
                {
                    using var r = cur.Current;
                    double? nv = FieldNum(r, "NewValue");
                    if (!nv.HasValue) continue;
                    if (range)
                    {
                        double? lo = FieldNum(r, "FromValue");
                        double? hi = FieldNum(r, "ToValue");
                        if (lo.HasValue && hi.HasValue)
                            rows.Add((lo.Value, hi.Value, nv.Value));
                    }
                    else
                    {
                        double? v = FieldNum(r, "OriginalValue");
                        if (v.HasValue) rows.Add((v.Value, v.Value, nv.Value));
                    }
                }
                return rows.Count > 0 ? (range, rows) : null;
            }
            catch { return null; }
        }

        private static double? FieldNum(Row r, string field)
        {
            try
            {
                object v = r[field];
                return v == null || v is DBNull
                    ? (double?)null : Convert.ToDouble(v);
            }
            catch { return null; }
        }

        /// <summary>Does a table by this name already exist? MCT.</summary>
        private static bool GdbTableExists(string outPath)
        {
            try
            {
                string gdbPath = System.IO.Path.GetDirectoryName(outPath);
                string name = System.IO.Path.GetFileName(outPath);
                if (!IsGdbWorkspace(gdbPath)) return false;
                using var gdb = new Geodatabase(
                    new FileGeodatabaseConnectionPath(new Uri(gdbPath)));
                return gdb.GetDefinitions<TableDefinition>().Any(
                    d => string.Equals(d.GetName(), name,
                        StringComparison.OrdinalIgnoreCase));
            }
            catch { return false; }
        }

        /// <summary>Streaming pass over the (possibly downsampled) raster:
        /// min/max/mean/sd (Welford), NoData count, histogram, and -- while
        /// the raster stays integer-valued with few distincts -- the unique
        /// value list that powers "List Unique Values".</summary>
        /// <summary>``Raster.GetNoDataValue()`` may return a SCALAR or a
        /// per-band ARRAY (e.g. a byte[] for a Byte-type GRID) -- normalize
        /// to a nullable double (first band). Convert failures = no usable
        /// NoData declaration.</summary>
        private static double? NoDataAsDouble(object nod)
        {
            try
            {
                if (nod == null) return null;
                if (nod is Array a)
                    return a.Length > 0
                        ? Convert.ToDouble(a.GetValue(0)) : (double?)null;
                return Convert.ToDouble(nod);
            }
            catch { return null; }
        }

        private void ScanPixels(Raster raster, int w, int h, object noData)
        {
            const int strip = 256;
            double? ndOpt = NoDataAsDouble(noData);
            bool hasNd = ndOpt.HasValue;
            double nd = ndOpt ?? 0.0;

            double mn = double.PositiveInfinity, mx = double.NegativeInfinity;
            double mean = 0, m2 = 0;
            long n = 0, nNoData = 0;
            var uniq = new Dictionary<double, long>();
            bool uniqOk = true;
            var values = new List<double[]>();      // kept per strip for pass 2? no --
            // Two passes would re-read; instead bin into a FIXED provisional
            // range after pass 1 is impossible in one pass, so: collect a
            // reservoir for the histogram while streaming the exact stats.
            // A 2M-cell uniform reservoir is plenty for a 120-bin histogram.
            const int ReservoirSize = 2_000_000;
            var reservoir = new List<double>(Math.Min((int)Math.Min(
                (long)w * h, ReservoirSize), ReservoirSize));
            var rng = new Random(12345);

            for (int y0 = 0; y0 < h; y0 += strip)
            {
                int hh = Math.Min(strip, h - y0);
                var pb = raster.CreatePixelBlock(w, hh);
                raster.Read(0, y0, pb);
                Array data = pb.GetPixelData(0, true);
                int n0 = data.GetLength(0), n1 = data.GetLength(1);
                bool rowFirst = false;
                if (n0 != n1) rowFirst = (n0 == hh);
                // The pixel block's own NoData MASK is authoritative -- an
                // integer GRID's masked cells otherwise read as value 0
                // with no declared NoData value to compare against.
                bool useMask = true;
                try { pb.GetNoDataMaskValue(0, 0, 0); }
                catch { useMask = false; }
                for (int r = 0; r < hh; r++)
                {
                    for (int c = 0; c < w; c++)
                    {
                        if (useMask && Convert.ToInt32(
                                pb.GetNoDataMaskValue(0, c, r)) == 0)
                        { nNoData++; continue; }
                        int i0 = rowFirst ? r : c, i1 = rowFirst ? c : r;
                        double v = Convert.ToDouble(data.GetValue(i0, i1));
                        bool ok = !double.IsNaN(v) && !double.IsInfinity(v)
                                  && Math.Abs(v) < 1e30;
                        if (ok && hasNd && v == nd) ok = false;
                        if (!ok) { nNoData++; continue; }
                        n++;
                        if (v < mn) mn = v;
                        if (v > mx) mx = v;
                        double d1 = v - mean;
                        mean += d1 / n;
                        m2 += d1 * (v - mean);
                        if (uniqOk)
                        {
                            if (v != Math.Floor(v) ||
                                (uniq.Count >= UniqueCap
                                 && !uniq.ContainsKey(v)))
                                uniqOk = false;
                            else
                                uniq[v] = uniq.TryGetValue(v, out long k)
                                    ? k + 1 : 1;
                        }
                        if (reservoir.Count < ReservoirSize)
                            reservoir.Add(v);
                        else
                        {
                            // classic reservoir sampling keeps it uniform
                            long j = LongRandom(rng, n);
                            if (j < ReservoirSize) reservoir[(int)j] = v;
                        }
                    }
                }
            }

            if (n == 0)
                throw new InvalidOperationException(
                    "The raster contains no data cells at all (everything is "
                    + "NoData).");
            _min = mn; _max = mx; _mean = mean;
            _sd = n > 1 ? Math.Sqrt(m2 / n) : 0.0;
            _nValid = n; _nNoData = nNoData;
            _uniques = uniqOk && uniq.Count > 0
                ? uniq.Keys.OrderBy(v => v).ToList() : null;
            _uniqueCounts = uniqOk && uniq.Count > 0 ? uniq : null;
            _halfw = 0.5;
            if (_uniques != null && _uniques.Count > 1)
            {
                double gap = double.PositiveInfinity;
                for (int i = 1; i < _uniques.Count; i++)
                    gap = Math.Min(gap, _uniques[i] - _uniques[i - 1]);
                if (!double.IsInfinity(gap) && gap > 0) _halfw = gap / 2;
            }

            _hist = new double[HistBins];
            double span = mx - mn;
            if (span <= 0)
                _hist[0] = reservoir.Count;
            else
                foreach (double v in reservoir)
                {
                    int b = (int)((v - mn) / span * (HistBins - 1));
                    if (b >= 0 && b < HistBins) _hist[b]++;
                }
        }

        private static long LongRandom(Random rng, long maxExclusive)
        {
            return (long)(rng.NextDouble() * maxExclusive);
        }

        private static Raster OpenFromPath(string path)
        {
            string dir = System.IO.Path.GetDirectoryName(path);
            string name = System.IO.Path.GetFileName(path);
            if (IsGdbWorkspace(dir))
            {
                var gdb = new Geodatabase(
                    new FileGeodatabaseConnectionPath(new Uri(dir)));
                var rd = gdb.OpenDataset<RasterDataset>(name);
                return rd.CreateFullRaster();
            }
            var store = new FileSystemDatastore(new FileSystemConnectionPath(
                new Uri(dir), FileSystemDatastoreType.Raster));
            var rds = store.OpenDataset<RasterDataset>(name);
            return rds.CreateFullRaster();
        }

        private static string LayerCatalogPath(RasterLayer rl)
        {
            try
            {
                var ds = rl.GetRaster()?.GetRasterDataset();
                if (ds == null) return rl.Name;
                string cs = ds.GetDatastore()?.GetConnectionString() ?? "";
                // DATABASE=<path> for both file gdb and raster folders
                int i = cs.IndexOf('=');
                string root = i >= 0 ? cs.Substring(i + 1) : cs;
                return System.IO.Path.Combine(root, ds.GetName());
            }
            catch { return rl.Name; }
        }

        // ---- gdb-vs-folder decisions (the standing .gdb suffix rule) ----
        private static bool IsGdbWorkspace(string path)
        {
            string low = (path ?? "").ToLowerInvariant()
                .Replace('/', '\\').TrimEnd('\\');
            return low.EndsWith(".gdb") || low.EndsWith(".sde");
        }

        private static bool InGdbContainer(string path)
        {
            string low = (path ?? "").ToLowerInvariant()
                .Replace('/', '\\').TrimEnd('\\');
            return low.EndsWith(".gdb") || low.Contains(".gdb\\")
                || low.EndsWith(".sde") || low.Contains(".sde\\");
        }

        private static string BaseName(string path)
        {
            string name = System.IO.Path.GetFileName(path ?? "raster");
            int dot = name.LastIndexOf('.');
            return dot > 0 ? name.Substring(0, dot) : name;
        }

        private string DefaultOutputPath()
        {
            if (string.IsNullOrEmpty(_rasterPath)) return "";
            string dir = System.IO.Path.GetDirectoryName(_rasterPath) ?? "";
            return InGdbContainer(_rasterPath)
                ? System.IO.Path.Combine(dir, _rasterBase + "_reclass")
                : System.IO.Path.Combine(dir, _rasterBase + "_reclass.txt");
        }

        private void UpdateOutputNote()
        {
            string o = txtOutput.Text ?? "";
            if (string.IsNullOrWhiteSpace(o)) { txtOutputNote.Text = ""; return; }
            txtOutputNote.Text = InGdbContainer(o)
                ? "Will be written as a GEODATABASE TABLE ("
                  + (rbRange.IsChecked == true
                      ? "FromValue / ToValue / NewValue"
                      : "OriginalValue / NewValue")
                  + " fields) — the field names the Create Habitat "
                  + "Suitability Model tool recognizes."
                : "Will be written as an ASCII remap file in the original "
                  + "CorridorDesigner format.";
        }

        // ---- mode + row helpers -----------------------------------------
        private bool RangeMode => rbRange.IsChecked == true;

        private void OnModeChanged(object sender, RoutedEventArgs e)
        {
            if (!IsLoaded) return;
            UpdateModeColumns();
            Dirty();
        }

        private void UpdateModeColumns()
        {
            if (colFrom == null) return;
            colFrom.Header = RangeMode ? "From" : "Original Value";
            colTo.Visibility = RangeMode ? Visibility.Visible
                                         : Visibility.Collapsed;
            UpdateOutputNote();
        }

        private void OnCellEdited(object sender, DataGridCellEditEndingEventArgs e)
        {
            // commit lands after this event; refresh once it has
            Dispatcher.BeginInvoke(new Action(Dirty),
                System.Windows.Threading.DispatcherPriority.Background);
        }

        private void OnRowSelected(object sender, SelectionChangedEventArgs e) =>
            Redraw();

        private void OnClearRows(object sender, RoutedEventArgs e)
        {
            _rows.Clear();
            Dirty();
        }

        /// <summary>Load an existing remap table (ASCII file or gdb table)
        /// into the grid as an editable starting point -- for reusing a
        /// scheme built for a similar dataset with a few tweaks. The output
        /// path is left alone: the tweaked scheme saves under THIS raster's
        /// name.</summary>
        private async void OnLoadTemplate(object sender, RoutedEventArgs e)
        {
            var dlg = new OpenItemDialog
            {
                Title = "Choose an existing remap table (ASCII file or "
                        + "geodatabase table)",
                MultiSelect = false,
                BrowseFilter = ArcGIS.Desktop.Core.BrowseProjectFilter
                    .GetFilter(ItemFilters.Tables_All),
            };
            if (dlg.ShowDialog() != true || dlg.Items == null) return;
            var item = dlg.Items.FirstOrDefault();
            if (item == null) return;
            string path = item.Path;
            (bool range, List<(double, double, double)> rows)? scheme = null;
            string problem = null;
            try
            {
                if (InGdbContainer(path))
                    scheme = await QueuedTask.Run(() => ReadGdbScheme(path));
                else if (File.Exists(path))
                    scheme = ReadAsciiScheme(path);
            }
            catch (Exception ex) { problem = ex.Message; }
            if (scheme == null)
            {
                MessageBox.Show(this,
                    "That table could not be read as a remap scheme"
                    + (problem == null ? "." : ":\n" + problem)
                    + "\n\nExpected either the original CorridorDesigner "
                    + "ASCII format ('value : score' / 'from to : score') "
                    + "or a table with FromValue / ToValue / NewValue or "
                    + "OriginalValue / NewValue fields.", Title);
                return;
            }
            ApplyScheme(scheme.Value.range, scheme.Value.rows);
            txtSampleNote.Text = "Template loaded from "
                + System.IO.Path.GetFileName(path)
                + " — tweak the rows, then save.";
            Dirty();
        }

        private void OnEqualIntervals(object sender, RoutedEventArgs e)
        {
            if (!_haveRaster)
            {
                MessageBox.Show(this, "Pick a raster first.", Title);
                return;
            }
            if (!int.TryParse(txtClasses.Text, out int k) || k < 1 || k > 200)
            {
                MessageBox.Show(this,
                    "Enter a class count between 1 and 200.", Title);
                return;
            }
            rbRange.IsChecked = true;
            _rows.Clear();
            double step = (_max - _min) / k;
            for (int i = 0; i < k; i++)
            {
                _rows.Add(new RemapRow
                {
                    FromText = Fmt(_min + i * step),
                    ToText = Fmt(i == k - 1 ? _max : _min + (i + 1) * step),
                });
            }
            Dirty();
        }

        private void OnListUnique(object sender, RoutedEventArgs e)
        {
            if (_uniques == null) return;
            rbValue.IsChecked = true;
            _rows.Clear();
            foreach (double v in _uniques)
                _rows.Add(new RemapRow { FromText = Fmt(v) });
            Dirty();
        }

        /// <summary>Valid, parsed rows in grid order: (from, to, new).</summary>
        private List<(double lo, double hi, double nv, RemapRow row)> ParsedRows(
            bool needNew)
        {
            var outRows = new List<(double, double, double, RemapRow)>();
            foreach (var r in _rows)
            {
                double? lo = r.From;
                double? hi = RangeMode ? r.To : r.From;
                double? nv = r.NewValue;
                if (!lo.HasValue || !hi.HasValue) continue;
                if (needNew && !nv.HasValue) continue;
                outRows.Add((lo.Value, hi.Value, nv ?? 0.0, r));
            }
            return outRows;
        }

        private void Dirty()
        {
            Redraw();
            RecomputeNotes();
        }

        // ---- drawing -----------------------------------------------------
        private void OnHistSize(object sender, SizeChangedEventArgs e) => Redraw();

        private void Redraw()
        {
            cnvHist.Children.Clear();
            cnvStrip.Children.Clear();
            pnlAxis.Children.Clear();
            pnlAxis.ColumnDefinitions.Clear();
            if (!_haveRaster || _hist == null) return;
            double W = cnvHist.ActualWidth, H = cnvHist.ActualHeight;
            if (W < 20 || H < 20) return;
            var inkSolid = new SolidColorBrush(Color.FromRgb(0x33, 0x26, 0x12));
            var inkBar = new SolidColorBrush(Color.FromArgb(150, 0x4A, 0x38, 0x1E));

            // A DISCRETE raster (an enumerable set of integer values, e.g. a
            // land cover or a classified surface) gets one full-bin-width
            // bar per value -- a smoothed curve over a handful of classes
            // would suggest values that do not exist. The axis domain is
            // padded by half a bin on each side so the first and last bars
            // sit fully inside the plot. A continuous raster gets the
            // binned histogram as bars with a kernel-smoothed density curve
            // drawn over them (no padding; the bins already tile the plot).
            bool discrete = _uniqueCounts != null
                && _uniqueCounts.Count <= DiscreteBarMax;
            double pad = discrete ? _halfw : 0.0;
            double axisLo = _min - pad, axisHi = _max + pad;
            double dspan = Math.Max(axisHi - axisLo, double.Epsilon);
            double X(double v) =>
                (Math.Min(Math.Max(v, axisLo), axisHi) - axisLo) / dspan * W;

            if (discrete)
            {
                long peakN = Math.Max(_uniqueCounts.Values.Max(), 1);
                foreach (var kv in _uniqueCounts)
                {
                    double x0 = X(kv.Key - _halfw) + 0.5;
                    double x1 = X(kv.Key + _halfw) - 0.5;
                    double hb = (double)kv.Value / peakN * (H - 6);
                    var bar = new Rectangle
                    {
                        Width = Math.Max(x1 - x0, 1.5),
                        Height = Math.Max(hb, 1.0),
                        Fill = inkBar,
                        Stroke = inkSolid,
                        StrokeThickness = 0.8,
                        ToolTip = "Value " + Fmt(kv.Key) + ": "
                                  + kv.Value.ToString("N0",
                                        CultureInfo.CurrentCulture)
                                  + " cells",
                    };
                    Canvas.SetLeft(bar, x0);
                    Canvas.SetTop(bar, H - bar.Height);
                    cnvHist.Children.Add(bar);
                }
            }
            else
            {
                double peak = _hist.Max();
                if (peak <= 0) peak = 1;
                // the binned histogram as bars...
                double bw = W / HistBins;
                for (int i = 0; i < HistBins; i++)
                {
                    if (_hist[i] <= 0) continue;
                    double hb = _hist[i] / peak * (H - 6);
                    var bar = new Rectangle
                    {
                        Width = Math.Max(bw - 0.6, 0.8),
                        Height = Math.Max(hb, 0.8),
                        Fill = inkBar,
                    };
                    Canvas.SetLeft(bar, i * bw);
                    Canvas.SetTop(bar, H - hb);
                    cnvHist.Children.Add(bar);
                }
                // ...with a kernel-smoothed density curve over them
                double[] smooth = GaussianSmooth(_hist, 2.5);
                double sPeak = smooth.Max();
                if (sPeak > 0)
                {
                    var line = new Polyline
                    {
                        Stroke = inkSolid,
                        StrokeThickness = 1.6,
                        StrokeLineJoin = PenLineJoin.Round,
                    };
                    for (int i = 0; i < HistBins; i++)
                        line.Points.Add(new Point(
                            (i + 0.5) / HistBins * W,
                            H - smooth[i] / sPeak * (H - 6)));
                    cnvHist.Children.Add(line);
                }
            }

            // coverage washes, one per parsed row (selection brightens). A
            // value-mode row (lo == hi) covers its whole discrete bin, so
            // its wash spans that bin instead of a hairline.
            double vpad = discrete ? _halfw : dspan / HistBins;
            var parsed = ParsedRows(needNew: false);
            var sel = grdRemap.SelectedItem as RemapRow;
            for (int i = 0; i < parsed.Count; i++)
            {
                var (lo, hi, _, row) = parsed[i];
                if (!RangeMode || hi == lo) { lo -= vpad; hi += vpad; }
                var c = Wash[i % Wash.Length];
                row.Swatch = new SolidColorBrush(
                    Color.FromArgb(170, c.R, c.G, c.B));
                double x0 = X(lo);
                double x1 = X(hi);
                byte a = (byte)(ReferenceEquals(row, sel) ? 150 : 80);
                var rect = new Rectangle
                {
                    Width = Math.Max(x1 - x0, 2.0), Height = H,
                    Fill = new SolidColorBrush(Color.FromArgb(a, c.R, c.G, c.B)),
                };
                if (ReferenceEquals(row, sel))
                {
                    rect.Stroke = new SolidColorBrush(
                        Color.FromRgb(0x33, 0x26, 0x12));
                    rect.StrokeThickness = 1.0;
                }
                Canvas.SetLeft(rect, x0);
                Canvas.SetTop(rect, 0);
                cnvHist.Children.Add(rect);
            }
            // grey out swatches on rows that did not parse
            foreach (var r in _rows)
                if (!parsed.Any(p => ReferenceEquals(p.row, r)))
                    r.Swatch = Brushes.Transparent;

            // mean marker: a small ink triangle on the baseline
            double xm = X(_mean);
            cnvHist.Children.Add(new Polygon
            {
                Points = new PointCollection
                {
                    new Point(xm - 5, H), new Point(xm + 5, H),
                    new Point(xm, H - 8),
                },
                Fill = new SolidColorBrush(Color.FromRgb(0x33, 0x26, 0x12)),
                ToolTip = "Mean = " + Fmt(_mean),
            });

            // suitability preview strip: covered ranges tinted by NEW value
            double SW = cnvStrip.ActualWidth > 0 ? cnvStrip.ActualWidth : W;
            double SH = cnvStrip.ActualHeight > 0 ? cnvStrip.ActualHeight : 12;
            double XS(double v) =>
                (Math.Min(Math.Max(v, axisLo), axisHi) - axisLo) / dspan * SW;
            foreach (var (lo0, hi0, nv, row) in ParsedRows(needNew: true))
            {
                double lo = lo0, hi = hi0;
                if (!RangeMode || hi == lo) { lo -= vpad; hi += vpad; }
                double x0 = XS(lo);
                double x1 = XS(hi);
                var rect = new Rectangle
                {
                    Width = Math.Max(x1 - x0, 2.0),
                    Height = SH - 2,
                    Fill = new SolidColorBrush(SuitabilityColor(nv)),
                    ToolTip = "New value " + Fmt(nv),
                };
                Canvas.SetLeft(rect, x0);
                Canvas.SetTop(rect, 1);
                cnvStrip.Children.Add(rect);
            }

            // axis: min | mean | max labels
            foreach (var (text, align) in new[]
            {
                (Fmt(_min), HorizontalAlignment.Left),
                (Fmt(_mean), HorizontalAlignment.Center),
                (Fmt(_max), HorizontalAlignment.Right),
            })
            {
                pnlAxis.ColumnDefinitions.Add(new ColumnDefinition());
                var tb = new TextBlock
                {
                    Text = text,
                    FontSize = 11,
                    HorizontalAlignment = align,
                    Foreground = new SolidColorBrush(
                        Color.FromRgb(0x8A, 0x75, 0x50)),
                };
                Grid.SetColumn(tb, pnlAxis.ColumnDefinitions.Count - 1);
                pnlAxis.Children.Add(tb);
            }
        }

        private double Clamp(double v) => Math.Min(Math.Max(v, _min), _max);

        private static Color SuitabilityColor(double newValue)
        {
            double t = Math.Min(Math.Max(newValue / 100.0, 0.0), 1.0);
            byte L(byte a, byte b) => (byte)(a + (b - a) * t);
            return Color.FromRgb(L(0xEF, 0x2F), L(0xE3, 0x5D), L(0xC4, 0x33));
        }

        private static string Fmt(double v) =>
            v.ToString(Math.Abs(v) >= 1000 ? "N1" : "0.####",
                       CultureInfo.CurrentCulture);

        // ---- live validation --------------------------------------------
        private void RecomputeNotes()
        {
            txtCoverage.Text = "";
            txtNewRange.Text = "";
            if (!_haveRaster) return;
            var parsed = ParsedRows(needNew: false);
            if (parsed.Count == 0) return;

            // input coverage against the raster's actual values -- checked
            // per MODE: value rows are exact matches, not degenerate ranges
            // (interval-gap analysis over points reported phantom gaps like
            // "1 to 2" on a fully covered categorical raster).
            if (!RangeMode)
            {
                if (_uniques != null)
                {
                    var listed = new HashSet<double>(parsed.Select(p => p.lo));
                    var missing = _uniques.Where(v => !listed.Contains(v))
                                          .ToList();
                    if (missing.Count > 0)
                        txtCoverage.Text =
                            "Not fully covered: raster value" +
                            (missing.Count == 1 ? " " : "s ") +
                            string.Join(", ", missing.Take(8).Select(Fmt)) +
                            (missing.Count > 8 ? ", ..." : "") +
                            (missing.Count == 1 ? " matches" : " match") +
                            " no remap row and would become NoData in "
                            + "the HSM.";
                }
                else
                {
                    txtCoverage.Text =
                        "Reclassify by Value matches EXACT values, but this "
                        + "raster is continuous -- most cells will match no "
                        + "row and become NoData in the HSM. Consider "
                        + "Reclassify by Range.";
                }
            }
            else
            {
                var ivals = parsed.Select(p => (lo: Math.Min(p.lo, p.hi),
                                                hi: Math.Max(p.lo, p.hi)))
                                  .OrderBy(p => p.lo).ThenBy(p => p.hi)
                                  .ToList();
                var gaps = new List<string>();
                double eps = (_max - _min) * 1e-9 + 1e-12;
                double reach = _min;
                foreach (var (lo, hi) in ivals)
                {
                    if (lo > reach + eps && reach < _max)
                        gaps.Add(Fmt(Math.Max(reach, _min)) + " to "
                                 + Fmt(Math.Min(lo, _max)));
                    reach = Math.Max(reach, hi);
                    if (reach >= _max) break;
                }
                if (reach < _max - eps)
                    gaps.Add(Fmt(reach) + " to " + Fmt(_max));
                if (gaps.Count > 0)
                    txtCoverage.Text =
                        "Not fully covered: raster values " +
                        string.Join("; ", gaps.Take(4)) +
                        (gaps.Count > 4 ? "; ..." : "") +
                        " match no remap row and would become NoData in the "
                        + "HSM.";
            }

            // new-value scale: OUTSIDE 0-100 is a real problem (such tables
            // will not work in the Create Habitat Suitability Model tool)
            // and joins the save-time warning; using only PART of the scale
            // is fine (Jeff, 2026-08-10) but keeps its red on-screen note.
            _newValuesOutside = false;
            var withNew = ParsedRows(needNew: true);
            if (withNew.Count > 0)
            {
                double nMin = withNew.Min(p => p.nv);
                double nMax = withNew.Max(p => p.nv);
                if (nMin < 0 || nMax > 100)
                {
                    _newValuesOutside = true;
                    txtNewRange.Text =
                        "New values run outside 0-100 (" + Fmt(nMin) + " to "
                        + Fmt(nMax) + ") — the HSM suitability scale is 0 "
                        + "(non-habitat) to 100 (best habitat), and values "
                        + "outside it will not work in the Create Habitat "
                        + "Suitability Model tool.";
                }
                else if (nMin > 0 || nMax < 100)
                    txtNewRange.Text =
                        "Note: the new values only span " + Fmt(nMin) + " to "
                        + Fmt(nMax) + " of the 0-100 suitability scale.";
            }
        }

        // ---- save --------------------------------------------------------
        private async void OnSave(object sender, RoutedEventArgs e)
        {
            string outPath = (txtOutput.Text ?? "").Trim();
            if (string.IsNullOrEmpty(outPath))
            {
                MessageBox.Show(this, "Name the output table first.", Title);
                return;
            }
            var rows = ParsedRows(needNew: true);
            if (rows.Count == 0)
            {
                MessageBox.Show(this,
                    "There are no complete remap rows to save (each row "
                    + "needs numeric values in every column).", Title);
                return;
            }
            int nIncomplete = _rows.Count(r =>
                !string.IsNullOrWhiteSpace(r.FromText + r.ToText + r.NewText))
                - rows.Count;
            RecomputeNotes();
            var warnings = new List<string>();
            if (nIncomplete > 0)
                warnings.Add(nIncomplete + " partially filled row(s) will be "
                             + "skipped.");
            if (!string.IsNullOrEmpty(txtCoverage.Text))
                warnings.Add(txtCoverage.Text);
            // the partial-scale NOTE stays on screen only; just genuinely
            // out-of-range new values block-warn at save time
            if (_newValuesOutside && !string.IsNullOrEmpty(txtNewRange.Text))
                warnings.Add(txtNewRange.Text);
            foreach (var r in rows)
                if (RangeMode && r.hi < r.lo)
                {
                    MessageBox.Show(this, "A row's To value is less than its "
                        + "From value (" + Fmt(r.lo) + " to " + Fmt(r.hi)
                        + "). Fix it before saving.", Title);
                    return;
                }
            if (warnings.Count > 0 && MessageBox.Show(this,
                    string.Join("\n\n", warnings) + "\n\nSave anyway?",
                    Title, MessageBoxButton.YesNo, MessageBoxImage.Warning)
                != MessageBoxResult.Yes)
                return;

            // an existing table by this name is only replaced on request
            bool gdbOut = InGdbContainer(outPath);
            bool exists = gdbOut
                ? await QueuedTask.Run(() => GdbTableExists(outPath))
                : File.Exists(outPath);
            if (exists && MessageBox.Show(this,
                    "A remap table already exists at\n\n" + outPath
                    + "\n\nOverwrite it?", Title, MessageBoxButton.YesNo,
                    MessageBoxImage.Warning) != MessageBoxResult.Yes)
                return;

            var sorted = rows.OrderBy(r => r.lo).ThenBy(r => r.hi).ToList();
            bool range = RangeMode;
            string problem = null;
            try
            {
                if (gdbOut)
                    await QueuedTask.Run(() => WriteGdbTable(outPath, sorted,
                                                             range));
                else
                    WriteAscii(outPath, sorted, range);
            }
            catch (Exception ex) { problem = ex.Message; }
            if (problem != null)
            {
                MessageBox.Show(this, "Could not write the remap table:\n"
                    + problem, Title);
                return;
            }
            _session[_rasterPath ?? ""] =
                (range, sorted.Select(r => (r.lo, r.hi, r.nv)).ToList());
            txtSampleNote.Text = "Saved: " + outPath;
            MessageBox.Show(this,
                "Remap table saved.\n\n" + outPath + "\n\nThe Create Habitat "
                + "Suitability Model tool accepts it directly — and because "
                + "its name contains the raster's name, that tool's dialog "
                + "will fill it in automatically when you pick this raster.",
                Title);
        }

        private static void WriteAscii(string path,
            List<(double lo, double hi, double nv, RemapRow row)> rows,
            bool range)
        {
            var lines = rows.Select(r => range
                ? string.Format(CultureInfo.InvariantCulture,
                                "{0:0.######}\t{1:0.######}\t:\t{2:0.######}",
                                r.lo, r.hi, r.nv)
                : string.Format(CultureInfo.InvariantCulture,
                                "{0:0.######}\t:\t{1:0.######}", r.lo, r.nv));
            File.WriteAllLines(path, lines);
        }

        /// <summary>Create (replacing if present) the geodatabase remap table
        /// and fill it. MCT only.</summary>
        private static void WriteGdbTable(string outPath,
            List<(double lo, double hi, double nv, RemapRow row)> rows,
            bool range)
        {
            string gdbPath = System.IO.Path.GetDirectoryName(outPath);
            string name = System.IO.Path.GetFileName(outPath);
            if (!IsGdbWorkspace(gdbPath))
                throw new InvalidOperationException(
                    "A geodatabase table path must sit directly inside a "
                    + ".gdb workspace: " + outPath);
            using var gdb = new Geodatabase(
                new FileGeodatabaseConnectionPath(new Uri(gdbPath)));

            var existing = gdb.GetDefinitions<TableDefinition>()
                .FirstOrDefault(d => string.Equals(d.GetName(), name,
                    StringComparison.OrdinalIgnoreCase));
            if (existing != null)
            {
                var del = new SchemaBuilder(gdb);
                del.Delete(new TableDescription(existing));
                if (!del.Build())
                    throw new InvalidOperationException(
                        "Could not replace the existing table " + name + ": "
                        + string.Join("; ", del.ErrorMessages));
            }

            var fields = range
                ? new List<DdlField>
                {
                    new DdlField("FromValue", FieldType.Double),
                    new DdlField("ToValue", FieldType.Double),
                    new DdlField("NewValue", FieldType.Double),
                }
                : new List<DdlField>
                {
                    new DdlField("OriginalValue", FieldType.Double),
                    new DdlField("NewValue", FieldType.Double),
                };
            var sb = new SchemaBuilder(gdb);
            sb.Create(new TableDescription(name, fields));
            if (!sb.Build())
                throw new InvalidOperationException(
                    "Could not create the table " + name + ": "
                    + string.Join("; ", sb.ErrorMessages));

            using var table = gdb.OpenDataset<Table>(name);
            gdb.ApplyEdits(() =>
            {
                foreach (var r in rows)
                {
                    using var buf = table.CreateRowBuffer();
                    if (range)
                    {
                        buf["FromValue"] = r.lo;
                        buf["ToValue"] = r.hi;
                        buf["NewValue"] = r.nv;
                    }
                    else
                    {
                        buf["OriginalValue"] = r.lo;
                        buf["NewValue"] = r.nv;
                    }
                    using var _ = table.CreateRow(buf);
                }
            });
        }

        private void OnOutputChanged(object sender,
                                     TextChangedEventArgs e) =>
            UpdateOutputNote();

        private void OnClose(object sender, RoutedEventArgs e) => Close();
    }
}
