using System;
using System.Collections.Generic;
using System.Linq;
using ArcGIS.Core.Data.Raster;
using ArcGIS.Core.Geometry;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Reads the Land Facet analysis inputs into memory: it takes a categorical
    /// "template" raster (whose grid, extent and projection define the analysis)
    /// plus one or more continuous variable rasters, aligns every variable raster
    /// onto the template grid ON THE FLY (reproject + resample, no Spatial Analyst,
    /// via the SDK Raster.Set* configuration), and builds -- for each category
    /// value in the template -- a cell x variable matrix of complete-case cells.
    /// Phase C's clustering runs on those matrices; the stored cell indices let
    /// the classified result be written back to the template grid.
    ///
    /// Must be called on the MCT (QueuedTask). Raster I/O is validated in Pro, not
    /// headlessly.
    /// </summary>
    internal sealed class RasterStack
    {
        public int Width { get; private set; }
        public int Height { get; private set; }
        public Envelope Extent { get; private set; }
        public SpatialReference SpatialRef { get; private set; }
        public double CellSizeX { get; private set; }
        public double CellSizeY { get; private set; }
        public string[] VariableNames { get; private set; }

        /// <summary>One category class of the template raster.</summary>
        public sealed class ClassData
        {
            public double Value;                       // the category code
            public List<long> CellIndex = new();       // row*Width + col on the template grid
            public List<double[]> Rows = new();        // one value vector per complete-case cell
            public double[][] Matrix() => Rows.ToArray();
            public int Count => Rows.Count;
        }

        /// <summary>Category code -> its cells. Only classes with cells appear.</summary>
        public Dictionary<double, ClassData> Classes { get; } = new();

        private const int StripRows = 256;             // read this many rows at a time

        public static RasterStack Build(Raster categorical,
                                        IReadOnlyList<(Raster raster, string name)> variables,
                                        Polygon clip = null,
                                        Action<double> report = null)
        {
            if (categorical == null) throw new ArgumentNullException(nameof(categorical));
            if (variables == null || variables.Count == 0)
                throw new ArgumentException("At least one variable raster is required.");

            var stack = new RasterStack
            {
                Width = categorical.GetWidth(),
                Height = categorical.GetHeight(),
                Extent = categorical.GetExtent(),
                SpatialRef = categorical.GetSpatialReference(),
                VariableNames = variables.Select(v => v.name).ToArray(),
            };
            var cell = categorical.GetMeanCellSize();
            stack.CellSizeX = cell.Item1;
            stack.CellSizeY = cell.Item2;
            int W = stack.Width, H = stack.Height, V = variables.Count;

            // Optional clip to the Wildland-Blocks polygon: rasterize it to a
            // cell mask on the template grid; cells outside are excluded.
            bool[] clipMask = clip != null
                ? BuildClipMask(clip, stack.SpatialRef, stack.Extent.XMin, stack.CellSizeX,
                                stack.Extent.YMax, stack.CellSizeY, W, H)
                : null;

            // Align every variable raster onto the template grid. Use NEAREST
            // resampling, not bilinear: bilinear blends a NoData cell (e.g. an aspect
            // transform's -9999 flat/NoData) with its valid neighbours, producing
            // spurious large-magnitude values that are not equal to the NoData value
            // and so slip past the NoData test and wreck the analysis. Nearest keeps
            // every value exact (NoData stays NoData), which is what clustering needs.
            var aligned = new Raster[V];
            var varNoData = new object[V];
            for (int i = 0; i < V; i++)
            {
                var r = variables[i].raster;
                varNoData[i] = r.GetNoDataValue();   // capture BEFORE reconfiguring
                r.SetSpatialReference(stack.SpatialRef);
                r.SetExtent(stack.Extent);
                r.SetWidth(W);
                r.SetHeight(H);
                r.SetResamplingType(ArcGIS.Core.CIM.RasterResamplingType.NearestNeighbor);
                r.Refresh();
                aligned[i] = r;
            }

            object catNoData = categorical.GetNoDataValue();

            for (int y0 = 0; y0 < H; y0 += StripRows)
            {
                int h = Math.Min(StripRows, H - y0);

                ReadStrip(categorical, W, h, y0,
                          out double[,] catVals, out bool[,] catValid, catNoData,
                          useSentinel: false);
                var varVals = new double[V][,];
                var varValid = new bool[V][,];
                for (int i = 0; i < V; i++)
                    ReadStrip(aligned[i], W, h, y0, out varVals[i], out varValid[i],
                              varNoData[i], useSentinel: true);

                for (int row = 0; row < h; row++)
                {
                    for (int col = 0; col < W; col++)
                    {
                        if (!catValid[row, col]) continue;      // categorical NoData
                        if (clipMask != null && !clipMask[(y0 + row) * W + col])
                            continue;                            // outside the clip polygon
                        // complete-case: drop the cell if any variable is NoData
                        bool ok = true;
                        var vec = new double[V];
                        for (int i = 0; i < V; i++)
                        {
                            if (!varValid[i][row, col]) { ok = false; break; }
                            vec[i] = varVals[i][row, col];
                        }
                        if (!ok) continue;

                        double code = catVals[row, col];
                        if (!stack.Classes.TryGetValue(code, out var cd))
                        {
                            cd = new ClassData { Value = code };
                            stack.Classes[code] = cd;
                        }
                        cd.CellIndex.Add((long)(y0 + row) * W + col);
                        cd.Rows.Add(vec);
                    }
                }
                report?.Invoke((double)Math.Min(y0 + h, H) / H);
            }
            return stack;
        }

        /// <summary>Rasterize the clip polygon to a cell mask on the template grid,
        /// projecting it to the template projection first. Internal so the
        /// histogram tool's clip-to-polygon option shares it.</summary>
        internal static bool[] BuildClipMask(Polygon clip, SpatialReference templateSr,
            double originX, double cellX, double originY, double cellY, int w, int h)
        {
            Polygon poly = clip;
            try
            {
                if (templateSr != null && clip.SpatialReference != null)
                    poly = (Polygon)GeometryEngine.Instance.Project(clip, templateSr);
            }
            catch { poly = clip; }

            // True-curve polygons (e.g. geodatabase buffer circles) expose only
            // their arc endpoints when their segments are walked, which would
            // collapse a circle to a couple of points. Densify to about half a
            // cell so the rasterized boundary follows the curve.
            if (poly.HasCurves)
            {
                try
                {
                    double step = Math.Min(cellX, cellY) * 0.5;
                    if (step > 0)
                        poly = (Polygon)GeometryEngine.Instance.DensifyByLength(poly, step);
                }
                catch { /* fall back to the un-densified polygon */ }
            }

            var rings = new List<double[][]>();
            foreach (var part in poly.Parts)
            {
                var pts = new List<double[]>();
                foreach (var seg in part)
                    pts.Add(new[] { seg.StartPoint.X, seg.StartPoint.Y });
                if (pts.Count >= 3) rings.Add(pts.ToArray());
            }
            return PolygonRasterizer.Rasterize(rings, originX, cellX, originY, cellY, w, h);
        }

        /// <summary>Read one horizontal strip of a raster into a double grid plus a
        /// validity (not-NoData) mask, tolerant of the underlying pixel type. NoData
        /// is identified by the band's NoData value and non-finite checks -- the
        /// SDK's per-block NoData mask is a bit-packed 1-D byte array (8 pixels per
        /// byte), not worth unpacking when the NoData value suffices.</summary>
        // The -9999 NoData sentinel shared by every Jenness Enterprises raster tool
        // (NODATA_OUT). Some inputs (e.g. an aspect transform whose NoData value was
        // never stamped on the file) carry -9999 as a plain pixel value; on a
        // continuous VARIABLE that is never a real measurement, so it is excluded
        // even when the raster declares no NoData value.
        private const double SentinelNoData = -9999.0;

        /// <summary>Typed per-cell accessor over GetPixelData's 2-D array.
        /// Array.GetValue + Convert.ToDouble boxed EVERY pixel (reflection
        /// plus IConvertible dispatch) -- the dominant per-cell cost of
        /// every raster read in the suite. Exotic element types keep the
        /// boxed fallback.</summary>
        internal static Func<int, int, double> CellReader(Array data)
        {
            switch (data)
            {
                case float[,] a: return (i, j) => a[i, j];
                case double[,] a: return (i, j) => a[i, j];
                case int[,] a: return (i, j) => a[i, j];
                case short[,] a: return (i, j) => a[i, j];
                case byte[,] a: return (i, j) => a[i, j];
                case ushort[,] a: return (i, j) => a[i, j];
                case sbyte[,] a: return (i, j) => a[i, j];
                case uint[,] a: return (i, j) => a[i, j];
                case long[,] a: return (i, j) => a[i, j];
                default: return (i, j) => Convert.ToDouble(data.GetValue(i, j));
            }
        }

        /// <summary>(col, row) -> cell-is-valid accessor for one pixel
        /// block, or null when the block carries no NoData mask. Fetches
        /// the whole mask ONCE (GetNoDataMask) instead of one SDK call per
        /// pixel; the bulk array's orientation is VERIFIED against a few
        /// per-pixel probes (corners + center), falling back to the
        /// per-pixel path on any surprise.</summary>
        internal static Func<int, int, bool> MaskReader(PixelBlock pb,
                                                        int w, int h)
        {
            bool perPixelWorks = true;
            try { pb.GetNoDataMaskValue(0, 0, 0); }
            catch { perPixelWorks = false; }

            Array m = null;
            try { m = pb.GetNoDataMask(0, true); }
            catch { }
            if (m != null)
            {
                Func<int, int, bool> cand = null;
                if (m.Rank == 2)
                {
                    int n0 = m.GetLength(0), n1 = m.GetLength(1);
                    bool rowFirst = n0 != n1 ? (n0 == h) : false;
                    if ((rowFirst ? n0 : n1) == h && (rowFirst ? n1 : n0) == w)
                    {
                        if (m is byte[,] b2)
                            cand = rowFirst
                                ? (Func<int, int, bool>)((c, r) => b2[r, c] != 0)
                                : ((c, r) => b2[c, r] != 0);
                        else
                        {
                            var mm = m; bool rf = rowFirst;
                            cand = (c, r) => Convert.ToInt32(
                                mm.GetValue(rf ? r : c, rf ? c : r)) != 0;
                        }
                    }
                }
                else if (m.Rank == 1 && m.LongLength == (long)w * h
                         && m is byte[] b1)
                    cand = (c, r) => b1[(long)r * w + c] != 0;

                if (cand != null && perPixelWorks)
                {
                    // verify orientation on a handful of probe cells
                    bool agrees = true;
                    foreach (var (c, r) in new[]
                    {
                        (0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
                        (w / 2, h / 2),
                    })
                    {
                        try
                        {
                            if ((Convert.ToInt32(
                                    pb.GetNoDataMaskValue(0, c, r)) != 0)
                                != cand(c, r))
                            { agrees = false; break; }
                        }
                        catch { agrees = false; break; }
                    }
                    if (agrees) return cand;
                }
                else if (cand != null)
                    return cand;
            }

            if (!perPixelWorks) return null;
            return (c, r) =>
                Convert.ToInt32(pb.GetNoDataMaskValue(0, c, r)) != 0;
        }

        internal static void ReadStrip(Raster raster, int w, int h, int y0,
                                       out double[,] values, out bool[,] valid,
                                       object noData, bool useSentinel)
        {
            var pb = raster.CreatePixelBlock(w, h);
            raster.Read(0, y0, pb);
            Array data = pb.GetPixelData(0, true);   // 2-D array (own copy)

            // GetPixelData returns a 2-D array indexed [column, row] -- the order the
            // SDK's own PixelBlock samples use. Matching a length against the strip
            // height can only decide the order when the strip is NOT square: a raster
            // exactly StripRows wide yields a square block, where a plain (n0 == h)
            // test would silently transpose the strip. So follow the documented order
            // and let the lengths override it only when they are unambiguous.
            int n0 = data.GetLength(0), n1 = data.GetLength(1);
            bool rowFirst = false;
            if (n0 != n1) rowFirst = (n0 == h);
            if ((rowFirst ? n0 : n1) != h || (rowFirst ? n1 : n0) != w)
                throw new InvalidOperationException(
                    $"Unexpected pixel block shape {n0} x {n1} for a {w} x {h} strip.");
            values = new double[h, w];
            valid = new bool[h, w];
            // GetNoDataValue() may return a scalar OR a per-band array (a
            // byte[] for a Byte-type GRID); Convert.ToDouble on the array
            // throws. Normalize to the first band's scalar.
            if (noData is Array ndArr)
                noData = ndArr.Length > 0 ? ndArr.GetValue(0) : null;
            bool hasNd = noData != null;
            double nd = 0.0;
            if (hasNd)
            {
                try { nd = Convert.ToDouble(noData); }
                catch { hasNd = false; }
            }

            // The pixel block's own NoData MASK is authoritative -- an
            // integer GRID accessed through a layer may declare no NoData
            // value at all, its masked cells reading as phantom zeros.
            var maskOk = MaskReader(pb, w, h);
            var read = CellReader(data);

            for (int r = 0; r < h; r++)
            {
                for (int c = 0; c < w; c++)
                {
                    int i0 = rowFirst ? r : c, i1 = rowFirst ? c : r;
                    double v = read(i0, i1);
                    bool ok = !double.IsNaN(v) && !double.IsInfinity(v);
                    if (ok && maskOk != null && !maskOk(c, r))
                        ok = false;
                    if (ok && hasNd && v == nd) ok = false;
                    // On a continuous variable, exclude the -9999 NoData sentinel and
                    // any absurd-magnitude fill (e.g. the Float NoData 1.79e308 / FLT
                    // MAX that the SDK may surface) -- never a real measurement.
                    if (ok && useSentinel && (v == SentinelNoData || Math.Abs(v) >= 1e30))
                        ok = false;
                    values[r, c] = v;
                    valid[r, c] = ok;
                }
            }
        }
    }
}
