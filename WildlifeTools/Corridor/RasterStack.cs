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
        /// projecting it to the template projection first.</summary>
        private static bool[] BuildClipMask(Polygon clip, SpatialReference templateSr,
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

        private static void ReadStrip(Raster raster, int w, int h, int y0,
                                      out double[,] values, out bool[,] valid,
                                      object noData, bool useSentinel)
        {
            var pb = raster.CreatePixelBlock(w, h);
            raster.Read(0, y0, pb);
            Array data = pb.GetPixelData(0, true);   // 2-D array (own copy)

            // The pixel block is (width x height); GetPixelData returns a 2-D array.
            // Map [d0, d1] -> [row, col] by matching the known height, so we do not
            // depend on the axis order.
            int n0 = data.GetLength(0);
            bool rowFirst = (n0 == h);
            values = new double[h, w];
            valid = new bool[h, w];
            bool hasNd = noData != null;
            double nd = hasNd ? Convert.ToDouble(noData) : 0.0;

            for (int r = 0; r < h; r++)
            {
                for (int c = 0; c < w; c++)
                {
                    int i0 = rowFirst ? r : c, i1 = rowFirst ? c : r;
                    double v = Convert.ToDouble(data.GetValue(i0, i1));
                    bool ok = !double.IsNaN(v) && !double.IsInfinity(v);
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
