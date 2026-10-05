// Headless test of WildlifeTools.DataTools.SummaryStats (the per-class
// statistics engine of Attribute Summary Reports). It compiles the add-in's
// own source file, so what passes here is what ships.
//   dotnet run --project claude_tests/SummaryStatsTest

using System;
using System.Collections.Generic;
using System.Linq;
using WildlifeTools.DataTools;

internal static class Program
{
    private static int _pass, _fail;

    private static void Check(string name, bool ok, string detail = "")
    {
        if (ok) _pass++;
        else { _fail++; Console.WriteLine("FAIL  " + name + "  " + detail); }
    }

    private static void Near(string name, double got, double want, double tol = 1e-9)
    {
        bool ok = double.IsNaN(want) ? double.IsNaN(got) : Math.Abs(got - want) <= tol;
        Check(name, ok, "got " + got + ", want " + want);
    }

    private static SummaryStats.FieldStats Stats(IEnumerable<double> xs,
                                                 IEnumerable<double> ws = null)
    {
        var acc = new SummaryStats.Accumulator();
        if (ws == null) foreach (var x in xs) acc.Add(x);
        else foreach (var p in xs.Zip(ws, (x, w) => (x, w))) acc.Add(p.x, p.w);
        return acc.Finish();
    }

    private static int Main()
    {
        // ---- parsing ------------------------------------------------------
        var t = SummaryStats.Parse(
            "{val1}{tab}{Mean:ElevationM:1} {min:elevationm} {N:Parcels.Acres}" +
            " {StDev:X:0} {Count} {Prop2} {Mean:} {Bogus:X}");
        Check("parse count", t.Count == 4, "got " + t.Count);
        Check("parse stat", t[0].Stat == SummaryStats.Kind.Mean);
        Check("parse field", t[0].Field == "ElevationM");
        Check("parse decimals", t[0].Decimals == 1);
        Check("parse case-insensitive", t[1].Stat == SummaryStats.Kind.Min
                                        && t[1].Field == "elevationm");
        Check("parse no decimals", t[1].Decimals == -1);
        Check("parse dotted field", t[2].Stat == SummaryStats.Kind.N
                                    && t[2].Field == "Parcels.Acres");
        Check("parse zero decimals", t[3].Decimals == 0);
        Check("parse position", "{val1}{tab}{Mean:ElevationM:1}".Length
                                == t[0].Index + t[0].Length);
        Check("token text", SummaryStats.TokenText(
            SummaryStats.Kind.StDev, "Slope", 3) == "{StDev:Slope:3}");
        Check("token text N", SummaryStats.TokenText(
            SummaryStats.Kind.N, "Slope", 3) == "{N:Slope}");
        Check("token round trip", SummaryStats.Parse(SummaryStats.TokenText(
            SummaryStats.Kind.Median, "A.B", 2)).Single().Field == "A.B");

        // ---- one record = one observation ---------------------------------
        var s = Stats(new double[] { 2, 4, 4, 4, 5, 5, 7, 9 });
        Near("N", s.N, 8);
        Near("sum", s.Sum, 40);
        Near("mean", s.Mean, 5);
        Near("median even", s.Median, 4.5);
        Near("min", s.Min, 2);
        Near("max", s.Max, 9);
        Near("range", s.Get(SummaryStats.Kind.Range), 7);
        Near("sample sd", s.StDev, Math.Sqrt(32.0 / 7.0), 1e-12);

        s = Stats(new double[] { 9, 1, 5 });
        Near("median odd", s.Median, 5);
        s = Stats(new double[] { 7.5 });
        Near("one value mean", s.Mean, 7.5);
        Near("one value median", s.Median, 7.5);
        Near("one value sd undefined", s.StDev, double.NaN);
        s = Stats(new double[0]);
        Near("empty N", s.N, 0);
        Near("empty mean", s.Mean, double.NaN);
        s = Stats(new double[] { 3, double.NaN, 5 });
        Near("NaN skipped", s.N, 2);
        s = Stats(new double[] { -4, -2, 0, 2 });
        Near("negatives mean", s.Mean, -1);
        Near("negatives median", s.Median, -1);

        // ---- raster rows: each row is Count cells -------------------------
        // weighted statistics must equal those of the expanded list of cells
        var xs = new double[] { 30, 10, 20, 40 };
        var ws = new double[] { 2, 3, 1, 4 };
        var cells = new List<double>();
        for (int i = 0; i < xs.Length; i++)
            for (int k = 0; k < (int)ws[i]; k++) cells.Add(xs[i]);
        var w = Stats(xs, ws);
        var e = Stats(cells);
        Near("weighted N", w.N, e.N);
        Near("weighted sum", w.Sum, e.Sum);
        Near("weighted mean", w.Mean, e.Mean, 1e-12);
        Near("weighted sd", w.StDev, e.StDev, 1e-12);
        Near("weighted median", w.Median, e.Median);
        Near("weighted min", w.Min, e.Min);
        Near("weighted max", w.Max, e.Max);

        // an even number of cells whose two middle cells differ
        w = Stats(new double[] { 1, 2 }, new double[] { 3, 3 });
        Near("weighted median straddle", w.Median, 1.5);
        // ... and a single row of many cells
        w = Stats(new double[] { 6 }, new double[] { 1000 });
        Near("one row mean", w.Mean, 6);
        Near("one row sd", w.StDev, 0);
        Near("one row median", w.Median, 6);
        Near("one row sum", w.Sum, 6000);
        // a random check against expansion
        var rng = new Random(12345);
        for (int trial = 0; trial < 200; trial++)
        {
            int n = rng.Next(1, 12);
            var x2 = Enumerable.Range(0, n).Select(_ => Math.Round(rng.NextDouble() * 50, 1)).ToArray();
            var w2 = Enumerable.Range(0, n).Select(_ => (double)rng.Next(1, 9)).ToArray();
            var ex = new List<double>();
            for (int i = 0; i < n; i++) for (int k = 0; k < (int)w2[i]; k++) ex.Add(x2[i]);
            var a = Stats(x2, w2); var b = Stats(ex);
            bool ok = Math.Abs(a.Mean - b.Mean) < 1e-9 && Math.Abs(a.Median - b.Median) < 1e-9
                && a.N == b.N && Math.Abs(a.Sum - b.Sum) < 1e-6
                && (double.IsNaN(b.StDev) ? double.IsNaN(a.StDev) : Math.Abs(a.StDev - b.StDev) < 1e-9);
            if (!ok) { Check("random weighted trial " + trial, false); break; }
        }
        Check("random weighted trials", true);
        // weights of zero or less carry no cells
        w = Stats(new double[] { 5, 99 }, new double[] { 2, 0 });
        Near("zero weight skipped", w.Max, 5);

        // ---- rendering ----------------------------------------------------
        var st = Stats(new double[] { 1000.5, 2000.25, 3000 });
        Func<string, SummaryStats.FieldStats> look =
            f => string.Equals(f, "Elev", StringComparison.OrdinalIgnoreCase) ? st : null;
        string R(string f, bool commas = false) => SummaryStats.Replace(f, look, commas);
        Check("render mean 1", R("{Mean:Elev:1}") == "2000.3", R("{Mean:Elev:1}"));
        Check("render default decimals", R("{Mean:Elev}") == "2000.25", R("{Mean:Elev}"));
        Check("render commas", R("{Sum:Elev:0}", true) == "6,001", R("{Sum:Elev:0}", true));
        Check("render N is whole", R("{N:Elev:3}") == "3", R("{N:Elev:3}"));
        Check("render case", R("{mean:ELEV:0}") == "2000");
        Check("render unknown field empty", R("[{Mean:Nope:1}]") == "[]");
        Check("render range", R("{Range:Elev:1}") == "1999.5", R("{Range:Elev:1}"));
        Check("render leaves others", R("{val1} {Count} {Min:Elev:0}") == "{val1} {Count} 1001",
              R("{val1} {Count} {Min:Elev:0}"));
        var one = Stats(new double[] { 4 });
        Check("render undefined sd empty", SummaryStats.Replace(
            "sd={StDev:A:2};", _ => one, false) == "sd=;");
        Check("render no lookup", SummaryStats.Replace("a{Mean:X}b", null, false) == "ab");
        Check("render no stats for class", SummaryStats.Replace(
            "a{Mean:X}b", _ => new SummaryStats.FieldStats(), false) == "ab");
        // a class with no values: N is a real zero, the rest stay empty
        var none = Stats(new double[0]);
        Check("render empty class N is 0", SummaryStats.Replace(
            "n={N:X};r={Range:X:1};s={Sum:X:0};", _ => none, false) == "n=0;r=;s=;",
            SummaryStats.Replace("n={N:X};r={Range:X:1};s={Sum:X:0};", _ => none, false));

        Console.WriteLine($"SummaryStats: {_pass} passed, {_fail} failed");
        return _fail == 0 ? 0 : 1;
    }
}
