using System;
using System.Collections.Generic;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Scanline rasterization of a polygon (with parts/holes) to a per-cell
    /// boolean mask on a raster grid, using the even-odd rule so holes and
    /// multipart rings are handled by parity. Pure geometry (no ArcGIS types) so
    /// it is unit-testable; the SpatialReference-aware ring extraction lives in the
    /// caller. Used to restrict the Land Facet analysis to the Wildland-Blocks
    /// polygon.
    /// </summary>
    internal static class PolygonRasterizer
    {
        /// <summary>
        /// Rasterize the given rings onto a grid. Each ring is an array of [x, y]
        /// vertices (the closing edge back to the first vertex is implied). The
        /// grid's top-left corner is (<paramref name="originX"/>,
        /// <paramref name="originY"/>); cells are <paramref name="cellSizeX"/> wide
        /// and <paramref name="cellSizeY"/> tall (both positive). Returns a
        /// row-major mask of length width*height, true where the cell CENTER is
        /// inside the polygon.
        /// </summary>
        public static bool[] Rasterize(IReadOnlyList<double[][]> rings,
            double originX, double cellSizeX, double originY, double cellSizeY,
            int width, int height)
        {
            var mask = new bool[width * height];
            if (rings == null || rings.Count == 0) return mask;

            var xs = new List<double>();
            for (int row = 0; row < height; row++)
            {
                double y = originY - (row + 0.5) * cellSizeY;   // cell-center Y
                xs.Clear();
                foreach (var ring in rings)
                {
                    int n = ring.Length;
                    if (n < 3) continue;
                    for (int i = 0; i < n; i++)
                    {
                        double[] a = ring[i];
                        double[] b = ring[(i + 1) % n];
                        double y1 = a[1], y2 = b[1];
                        // Half-open crossing test: counts each edge once and
                        // avoids double-counting shared vertices.
                        bool crosses = (y1 <= y && y2 > y) || (y2 <= y && y1 > y);
                        if (!crosses) continue;
                        double t = (y - y1) / (y2 - y1);
                        xs.Add(a[0] + t * (b[0] - a[0]));
                    }
                }
                if (xs.Count < 2) continue;
                xs.Sort();
                // Fill spans between consecutive intersection pairs (even-odd).
                for (int k = 0; k + 1 < xs.Count; k += 2)
                {
                    double xa = xs[k], xb = xs[k + 1];
                    int c0 = (int)Math.Ceiling((xa - originX) / cellSizeX - 0.5);
                    int c1 = (int)Math.Floor((xb - originX) / cellSizeX - 0.5);
                    if (c0 < 0) c0 = 0;
                    if (c1 > width - 1) c1 = width - 1;
                    int baseIdx = row * width;
                    for (int c = c0; c <= c1; c++) mask[baseIdx + c] = true;
                }
            }
            return mask;
        }
    }
}
