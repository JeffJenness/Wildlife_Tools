// Draws the three CropPlots panels of the built add-in to PNGs outside ArcGIS
// Pro (plain WPF look), so their layout can be checked: clipping, overlap,
// wrapping. Pro-touching code runs only from clicks or the Loaded event, which
// does not fire here (no window), so the panels show their initial state.
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
        int ok = 0;
        foreach (var (type, w, h, file) in new[]
                 {
                     ("WildlifeTools.CropPlots.PlotDesignerPanel", 860, 720, "plot_designer.png"),
                     ("WildlifeTools.CropPlots.ArrayAlongAxesPanel", 860, 640, "array_along_axes.png"),
                     ("WildlifeTools.CropPlots.PointCoordinatesSettingsPanel", 900, 560, "point_coordinates_settings.png"),
                 })
        {
            try
            {
                Type panelType = asm.GetType(type, true);
                var panel = (FrameworkElement)Activator.CreateInstance(panelType);
                var host = new Border { Background = Brushes.White, Child = panel, Width = w, Height = h };
                Layout(host);
                // the settings panel shows its saved settings on Loaded; call Show() directly
                MethodInfo show = panelType.GetMethod("Show", Any, null,
                    new[] { asm.GetType("WildlifeTools.CropPlots.PointCoordinatesSettings") }, null);
                if (show != null)
                {
                    object s = Activator.CreateInstance(asm.GetType("WildlifeTools.CropPlots.PointCoordinatesSettings"));
                    show.Invoke(panel, new[] { s });
                    Layout(host);
                }
                Save(host, Path.Combine(outDir, file));
                ok++;
            }
            catch (Exception ex)
            {
                Console.WriteLine(type + " FAILED: " + (ex.InnerException ?? ex).Message);
            }
        }
        Console.WriteLine(ok == 3 ? "RENDER OK" : "RENDER PROBLEM (" + ok + " of 3)");
        return ok == 3 ? 0 : 1;
    }

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
