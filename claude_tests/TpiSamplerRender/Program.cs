// Draws the content of the TPI Neighborhood Sampler window
// (WildlifeTools.TpiSampler.TpiSamplerPanel) to a PNG WITHOUT ArcGIS Pro,
// with a made-up DEM as its snapshot: three hills of different sizes, a
// valley, a NoData corner. It reads the built add-in DLL, so build the
// add-in (Release) first. The map's layer list is not read outside Pro, so
// the DEM box is empty.
//   dotnet run (project claude_tests/TpiSamplerRender) -- <output folder>

using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.Loader;
using System.Threading;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;

internal static class Program
{
    private const string ProBin = @"C:\Program Files\ArcGIS\Pro\bin";
    private const BindingFlags Any = BindingFlags.Instance | BindingFlags.Static
        | BindingFlags.Public | BindingFlags.NonPublic;

    private static int Main(string[] args)
    {
        string outDir = args.Length > 0 ? args[0] : Directory.GetCurrentDirectory();
        Directory.CreateDirectory(outDir);
        int rc = 1;
        var t = new Thread(() =>
        {
            try { rc = Run(outDir); }
            catch (Exception ex) { Console.WriteLine("THREW: " + ex); }
        });
        t.SetApartmentState(ApartmentState.STA);
        t.Start();
        t.Join();
        return rc;
    }

    private static string FindDll()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir != null && !Directory.Exists(Path.Combine(dir.FullName, "WildlifeTools")))
        {
            dir = dir.Parent;
        }
        return dir == null ? null : Path.Combine(dir.FullName, "WildlifeTools",
            "bin", "Release", "net10.0-windows", "WildlifeTools.dll");
    }

    private static int Run(string outDir)
    {
        string dll = FindDll();
        if (dll == null || !File.Exists(dll))
        {
            Console.WriteLine("Built add-in DLL not found: " + dll);
            return 1;
        }
        AssemblyLoadContext.Default.Resolving += (ctx, name) =>
        {
            string hit = Directory.EnumerateFiles(ProBin, name.Name + ".dll",
                SearchOption.AllDirectories).FirstOrDefault();
            return hit == null ? null : ctx.LoadFromAssemblyPath(hit);
        };
        _ = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };

        Assembly asm = Assembly.LoadFrom(dll);
        Type panelType = asm.GetType("WildlifeTools.TpiSampler.TpiSamplerPanel", true);
        Type gridType = asm.GetType("WildlifeTools.TpiSampler.SamplerGrid", true);

        // a made-up DEM: 400 x 300 cells of 90 m (3 x 3 blocks of a 30 m DEM)
        int w = 400, h = 300;
        var z = new float[w * h];
        var rnd = new Random(3);
        for (int r = 0; r < h; r++)
        {
            for (int c = 0; c < w; c++)
            {
                double x = c * 90.0, y = r * 90.0;
                double v = 1500 + 0.02 * x;                                        // a gentle regional slope
                v += 180 * Math.Exp(-(Sq(x - 9000) + Sq(y - 12000)) / Sq(2500));   // a broad hill
                v += 90 * Math.Exp(-(Sq(x - 24000) + Sq(y - 8000)) / Sq(900));     // a small sharp hill
                v += 60 * Math.Exp(-(Sq(x - 16000) + Sq(y - 20000)) / Sq(1500));   // a medium knoll
                v -= 120 * Math.Exp(-Sq(y - 0.35 * x - 6000) / Sq(1200));          // a diagonal valley
                v += rnd.NextDouble() * 4;
                if (c > w - 40 && r < 30) v = float.NaN;                            // a NoData corner
                z[r * w + c] = (float)v;
            }
        }
        object grid = gridType.GetMethod("FromCoarse", Any).Invoke(null, new object[] { z, w, h, 3, false });
        gridType.GetMethod("SetUniformCells", Any).Invoke(grid, new object[] { 90.0, 90.0 });
        gridType.GetField("DemName", Any).SetValue(grid, "made_up_dem_30m");
        gridType.GetField("NativeCellSize", Any).SetValue(grid, 30.0);
        gridType.GetField("NativeUnitName", Any).SetValue(grid, "Meter");

        var panel = (FrameworkElement)Activator.CreateInstance(panelType);
        var host = new Border { Background = Brushes.White, Child = panel, Width = 1120, Height = 700 };
        Layout(host);
        var sw = System.Diagnostics.Stopwatch.StartNew();
        panelType.GetMethod("LoadGrid", Any).Invoke(panel, new object[] { grid });
        Console.WriteLine("LoadGrid (two grids + range + first paint): " + sw.ElapsedMilliseconds + " ms");
        Layout(host);
        // pin the crest of the small sharp hill, as a user would by clicking
        panelType.GetMethod("SetPin", Any).Invoke(panel, new object[] { 24000 / 90, 8000 / 90 });
        Layout(host);
        sw.Restart();
        panelType.GetMethod("Redraw", Any).Invoke(panel, null);
        Console.WriteLine("Redraw at a cached radius: " + sw.ElapsedMilliseconds + " ms");
        Layout(host);

        Save(host, Path.Combine(outDir, "tpi_sampler.png"));

        var axis = (TextBlock)panel.FindName("txtRadiusAxis");
        var pin = (TextBlock)panel.FindName("txtPin");
        var span = (TextBlock)panel.FindName("txtPinSpan");
        var snap = (TextBlock)panel.FindName("txtSnapshot");
        var sldR = (Slider)panel.FindName("sldRadius");
        var sldT = (Slider)panel.FindName("sldThreshold");
        Console.WriteLine(snap.Text);
        Console.WriteLine(axis.Text);
        Console.WriteLine(pin.Text);
        Console.WriteLine(span.Text);
        Console.WriteLine("radius slider " + sldR.Minimum + ".." + sldR.Maximum + " at " + sldR.Value
            + "; threshold slider " + sldT.Minimum + ".." + sldT.Maximum + " at " + sldT.Value);
        var curve = (double[])panelType.GetMethod("PinCurve", Any).Invoke(panel, null);
        Console.WriteLine("pin curve: " + string.Join(" ", curve.Skip(2).Where((v, i) => i % 10 == 0).Select(v => v.ToString("0.0"))));

        // the dev scale and the mirror line, a second picture
        var rbDev = (RadioButton)panel.FindName("rbDev");
        var chkMirror = (CheckBox)panel.FindName("chkMirror");
        rbDev.IsChecked = true;
        chkMirror.IsChecked = true;
        sldR.Value = sldR.Maximum * 0.6;
        Layout(host);
        Save(host, Path.Combine(outDir, "tpi_sampler_dev.png"));
        Console.WriteLine(axis.Text);
        Console.WriteLine(span.Text);
        bool ok = sldR.IsEnabled && sldT.IsEnabled && curve.Skip(2).All(v => !double.IsNaN(v)) && snap.Text.Contains("400 × 300");
        Console.WriteLine(ok ? "RENDER OK" : "RENDER PROBLEM");
        return ok ? 0 : 1;
    }

    private static double Sq(double v) => v * v;

    private static void Layout(FrameworkElement host)
    {
        for (int pass = 0; pass < 3; pass++)
        {
            host.Measure(new Size(host.Width, host.Height));
            host.Arrange(new Rect(0, 0, host.Width, host.Height));
            host.UpdateLayout();
        }
    }

    private static void Save(FrameworkElement host, string path)
    {
        const double Scale = 1.25;
        var bmp = new RenderTargetBitmap((int)Math.Ceiling(host.ActualWidth * Scale),
            (int)Math.Ceiling(host.ActualHeight * Scale), 96 * Scale, 96 * Scale, PixelFormats.Pbgra32);
        bmp.Render(host);
        var enc = new PngBitmapEncoder();
        enc.Frames.Add(BitmapFrame.Create(bmp));
        using (FileStream fs = File.Create(path)) enc.Save(fs);
        Console.WriteLine(path);
    }
}
