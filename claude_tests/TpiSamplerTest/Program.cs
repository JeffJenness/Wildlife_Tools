// Brute-force checks of the TPI Neighborhood Sampler engine. See the csproj.
using System;
using System.Collections.Generic;
using System.Linq;
using WildlifeTools.TpiSampler;

internal static class Program
{
    private static int _fails;

    private static void Check(string name, bool ok, string detail = "")
    {
        Console.WriteLine((ok ? "ok    " : "FAIL  ") + name + (ok ? "" : "  " + detail));
        if (!ok) _fails++;
    }

    private static bool Near(double a, double b, double tol) =>
        double.IsNaN(a) && double.IsNaN(b) || Math.Abs(a - b) <= tol;

    /// <summary>The Python kernel rule applied cell by cell: the mean and SD of
    /// the valid cells with (dx/rx)² + (dy/ry)² ≤ 1 + 1e-9 around (c, r).</summary>
    private static (double tpi, double dev) Brute(SamplerGrid g, Neighborhood nb, int c, int r)
    {
        int w = g.Width, h = g.Height;
        if (!g.Valid[r * w + c]) return (double.NaN, double.NaN);
        double rx = Math.Max(nb.RxRows[r], 0.5), ry = Math.Max(nb.Ry, 0.5);
        int nx = (int)Math.Ceiling(rx), ny = (int)Math.Ceiling(ry);
        double sum = 0, sq = 0;
        int n = 0;
        for (int dy = -ny; dy <= ny; dy++)
        {
            for (int dx = -nx; dx <= nx; dx++)
            {
                if ((dx / rx) * (dx / rx) + (dy / ry) * (dy / ry) > 1.0 + 1e-9) continue;
                int cc = c + dx, rr = r + dy;
                if (cc < 0 || cc >= w || rr < 0 || rr >= h || !g.Valid[rr * w + cc]) continue;
                double z = g.Z[rr * w + cc];
                sum += z;
                sq += z * z;
                n++;
            }
        }
        if (n == 0) return (double.NaN, double.NaN);
        double mean = sum / n;
        double tpi = g.Z[r * w + c] - mean;
        double var = sq / n - mean * mean;
        double sd = var > 0 ? Math.Sqrt(var) : 0;
        return (tpi, sd > 0 ? tpi / sd : 0.0);
    }

    private static SamplerGrid RandomGrid(int w, int h, int seed, double holeFraction)
    {
        var rnd = new Random(seed);
        var z = new float[w * h];
        for (int r = 0; r < h; r++)
        {
            for (int c = 0; c < w; c++)
            {
                // a rolling surface plus noise, with holes
                double v = 1000 + 40 * Math.Sin(c / 5.0) * Math.Cos(r / 7.0) + 15 * Math.Sin((c + r) / 3.0) + rnd.NextDouble() * 6;
                z[r * w + c] = rnd.NextDouble() < holeFraction ? float.NaN : (float)v;
            }
        }
        return SamplerGrid.FromCoarse(z, w, h, 3, sampled: false);
    }

    private static void CompareAll(string name, SamplerGrid g, Neighborhood nb)
    {
        var engine = new FocalEngine(g);
        TpiGrids got = engine.Get(nb);
        double worstRaw = 0, worstDev = 0, worstAt = 0;
        int nanMismatch = 0;
        for (int r = 0; r < g.Height; r++)
        {
            for (int c = 0; c < g.Width; c++)
            {
                (double tpi, double dev) = Brute(g, nb, c, r);
                int i = r * g.Width + c;
                if (double.IsNaN(tpi) != float.IsNaN(got.Raw[i])) { nanMismatch++; continue; }
                if (double.IsNaN(tpi)) continue;
                worstRaw = Math.Max(worstRaw, Math.Abs(tpi - got.Raw[i]));
                worstDev = Math.Max(worstDev, Math.Abs(dev - got.Dev[i]));
                (double atRaw, double atDev) = engine.At(nb, c, r);
                worstAt = Math.Max(worstAt, Math.Max(Math.Abs(atRaw - tpi), Math.Abs(atDev - dev)));
            }
        }
        Check(name + ": NaN pattern matches brute force", nanMismatch == 0, nanMismatch + " cells");
        Check(name + ": raw TPI matches brute force", worstRaw < 1e-3, "worst " + worstRaw);
        Check(name + ": dev TPI matches brute force", worstDev < 1e-3, "worst " + worstDev);
        Check(name + ": single-cell At() matches", worstAt < 1e-6, "worst " + worstAt);
    }

    private static int Main()
    {
        // ---- the kernel rule
        bool ruleOk = true;
        foreach ((double rx, double ry) in new[] { (2.5, 2.5), (3.0, 3.0), (7.0, 4.0), (1.0, 1.0), (0.5, 0.5), (12.0, 12.0) })
        {
            int ny = (int)Math.Ceiling(Math.Max(ry, 0.5)), nx = (int)Math.Ceiling(Math.Max(rx, 0.5));
            for (int dy = -ny; dy <= ny; dy++)
            {
                int half = FocalEngine.ChordHalfWidth(rx, ry, dy);
                for (int dx = -nx - 1; dx <= nx + 1; dx++)
                {
                    bool inside = (dx / Math.Max(rx, 0.5)) * (dx / Math.Max(rx, 0.5)) + (dy / Math.Max(ry, 0.5)) * (dy / Math.Max(ry, 0.5)) <= 1.0 + 1e-9;
                    bool mine = half >= 0 && Math.Abs(dx) <= half;
                    if (inside != mine) { ruleOk = false; Console.WriteLine("  rule mismatch rx=" + rx + " ry=" + ry + " dx=" + dx + " dy=" + dy); }
                }
            }
        }
        Check("chord half-widths reproduce the (dx/rx)^2+(dy/ry)^2 <= 1 kernel", ruleOk);

        // ---- projected grid, several radii, with holes
        SamplerGrid g = RandomGrid(37, 29, 7, 0.08);
        g.SetUniformCells(90.0, 90.0);
        g.DemName = "test_dem";
        foreach (double cells in new[] { 1.0, 2.5, 4.0, 7.5 })
        {
            Neighborhood nb = Neighborhood.Resolve(g, cells * g.Factor, "Cells");
            Check("Cells " + cells * g.Factor + " DEM cells -> " + cells + " snapshot cells", Near(nb.Ry, cells, 1e-12) && Near(nb.RxRows[3], cells, 1e-12));
            CompareAll("projected, r=" + cells + " cells", g, nb);
        }
        Neighborhood nbM = Neighborhood.Resolve(g, 450.0, "Meters");
        Check("450 m on 90 m cells = 5 cells", Near(nbM.Ry, 5.0, 1e-12) && Near(nbM.RxRows[0], 5.0, 1e-12) && Near(nbM.DemCells, 15.0, 1e-9));
        CompareAll("projected, 450 m", g, nbM);
        Neighborhood nbK = Neighborhood.Resolve(g, 0.2025, "Kilometers");
        Check("0.2025 km = 2.25 cells", Near(nbK.Ry, 2.25, 1e-9));
        CompareAll("projected, 2.25 cells", g, nbK);

        // ---- a hole-free grid: no NaN anywhere, SD zero on a flat grid gives dev 0
        var flat = SamplerGrid.FromCoarse(Enumerable.Repeat(500f, 20 * 15).ToArray(), 20, 15, 1, sampled: false);
        flat.SetUniformCells(10, 10);
        TpiGrids fg = new FocalEngine(flat).Get(Neighborhood.Resolve(flat, 3, "Cells"));
        Check("flat grid: raw TPI 0 and dev 0 everywhere", fg.Raw.All(v => v == 0f) && fg.Dev.All(v => v == 0f));

        // ---- geographic grid: per-row east-west radius, rounded to whole cells
        SamplerGrid gg = RandomGrid(41, 33, 11, 0.05);
        gg.SetGeographicCells(6378137.0, 6356752.314245, -111.6, 60.0, 3.0 / 3600.0 * 4, 3.0 / 3600.0 * 4);
        double eq = gg.CellHeightM;
        Check("geographic: at 60 N a cell is about half as wide as tall", gg.RowCellWidthM[0] / eq > 0.45 && gg.RowCellWidthM[0] / eq < 0.55,
            (gg.RowCellWidthM[0] / eq).ToString());
        Check("geographic: a 12 arc-second cell is about 370 m tall", eq > 360 && eq < 380, eq.ToString());
        Neighborhood nbG = Neighborhood.Resolve(gg, 1500.0, "Meters");
        Check("geographic: east-west radius is a whole number of cells", nbG.RxRows.All(x => Math.Abs(x - Math.Round(x)) < 1e-12 && x >= 1));
        Check("geographic: east-west radius exceeds the north-south one", nbG.RxRows[0] > nbG.Ry);
        CompareAll("geographic, 1,500 m at 60 N", gg, nbG);

        // ---- the contour of a tilted plane
        int cw = 12, chh = 9;
        var plane = new float[cw * chh];
        for (int r = 0; r < chh; r++) for (int c = 0; c < cw; c++) plane[r * cw + c] = c - 5;
        var segs = Contour.Trace(plane, cw, chh, 0.5);
        Check("plane contour: one vertical segment per row of squares", segs.Count == chh - 1, segs.Count.ToString());
        Check("plane contour: all at x = 6.0", segs.All(s => Near(s.x1, 6.0, 1e-9) && Near(s.x2, 6.0, 1e-9)));
        plane[3 * cw + 5] = float.NaN;
        Check("plane contour: NaN cell drops its squares", Contour.Trace(plane, cw, chh, 0.5).Count == chh - 3);
        // a bump: a closed ring of segments
        var bump = new float[cw * chh];
        for (int r = 0; r < chh; r++) for (int c = 0; c < cw; c++) bump[r * cw + c] = (float)(10 - Math.Sqrt((c - 5.5) * (c - 5.5) + (r - 4) * (r - 4)));
        var ring = Contour.Trace(bump, cw, chh, 7.0);
        Check("bump contour: a ring of segments", ring.Count >= 12 && ring.Count <= 24, ring.Count.ToString());
        Check("bump contour: every endpoint is shared by two segments", ring.SelectMany(s => new[] { (Math.Round(s.x1, 6), Math.Round(s.y1, 6)), (Math.Round(s.x2, 6), Math.Round(s.y2, 6)) })
            .GroupBy(p => p).All(grp => grp.Count() == 2));

        // ---- block means
        var native = new float[6 * 4];
        for (int i = 0; i < native.Length; i++) native[i] = i;
        native[7] = float.NaN;                            // one hole in block (0,1)... row 1, col 1 -> block (0,0)
        var bm = SamplerGrid.FromBlockMeans(native, 6, 4, 2);
        Check("block means: 3 x 2 blocks", bm.Width == 3 && bm.Height == 2);
        Check("block means: block (0,0) averages its 3 valid cells", Near(bm.Z[0], (0 + 1 + 6) / 3.0, 1e-6), bm.Z[0].ToString());
        Check("block means: block (1,0) = mean of 2,3,8,9", Near(bm.Z[1], 5.5, 1e-6));
        var holes = (float[])native.Clone();
        holes[2] = float.NaN; holes[3] = float.NaN; holes[8] = float.NaN;
        Check("block means: a block more than half NoData is NoData", float.IsNaN(SamplerGrid.FromBlockMeans(holes, 6, 4, 2).Z[1]));
        Check("block means: z range", Near(bm.ZMin, (0 + 1 + 6) / 3.0, 1e-6) && Near(bm.ZMax, (16 + 17 + 22 + 23) / 4.0, 1e-6));

        // ---- painting and the range
        Check("nice range: 0.37 -> 0.5", Near(Painter.NiceRange(new[] { new[] { 0.37f, -0.1f } }), 0.5, 1e-12));
        Check("nice range: 123 -> 200", Near(Painter.NiceRange(new[] { new[] { 123f } }), 200, 1e-12));
        Check("nice range: 7 -> 10", Near(Painter.NiceRange(new[] { new[] { -7f } }), 10, 1e-12));
        var v = new[] { float.NaN, 5f, -5f, 0f };
        byte[] px = Painter.Paint(v, 4, 1, 10, 2.0, true, true, null);
        Check("paint: NaN is transparent", px[3] == 0);
        Check("paint: positive above the threshold is tinted (red with yellow: blue < green < red)", px[7] == 255 && px[4] < px[5] && px[5] < px[6] && px[6] > 200);
        Check("paint: negative is blue", px[11] == 255 && px[8] > px[10]);
        Check("paint: zero is white", px[12] == 255 && px[13] == 255 && px[14] == 255);
        byte[] px2 = Painter.Paint(v, 4, 1, 10, 2.0, false, true, null);
        Check("paint: below mode tints the negative cell instead", px2[8] < px[8] && px2[6] == px[6] - 0 || px2[9] != px[9]);
        byte[] hs = Painter.Hillshade(g);
        Check("hillshade: bytes for every cell, mid-gray on NoData", hs.Length == g.CellCount && Enumerable.Range(0, g.CellCount).All(i => g.Valid[i] || hs[i] == 128));

        // ---- captions and the clipboard text
        string snap = Captions.Snapshot(g);
        Check("snapshot caption names the size and the block", snap.Contains("37 × 29") && snap.Contains("3 × 3 DEM cells"), snap);
        string settings = Captions.Settings(g, nbM, TpiScale.Raw, 12.5, true);
        Check("settings text: the Python call", settings.Contains("arcpy.jenness.SimpleTPI(") && settings.Contains("nb_outer=450") && settings.Contains("nb_units=\"Meters\"") && settings.Contains("out_tpi="));
        Check("settings text: the threshold", settings.Contains("at or above 12.5 elevation units"));
        string settingsDev = Captions.Settings(g, nbM, TpiScale.NeighborhoodSd, -1, false);
        Check("settings text: dev scale uses out_dev and 'at or below'", settingsDev.Contains("out_dev=") && settingsDev.Contains("at or below"));
        Check("number formatting", Captions.Number(1234.567) == "1,235" && Captions.Number(0.004567) == "0.00457" && Captions.Number(45.0) == "45");

        Console.WriteLine();
        Console.WriteLine("TPI SAMPLER ENGINE: " + _fails + " failed");
        return _fails == 0 ? 0 : 1;
    }
}
