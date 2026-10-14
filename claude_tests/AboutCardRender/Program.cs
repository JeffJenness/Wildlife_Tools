// Draws the About card (the add-in's WildlifeTools.About.AboutWindow) to PNG
// files WITHOUT ArcGIS Pro: the card is a plain WPF Window that needs nothing
// from Pro to draw itself, and its version check runs only on Loaded, which
// never fires here. It reads the built add-in DLL, so build the add-in
// (Release) first.
//   dotnet run --project claude_tests/AboutCardRender -- <output folder>

using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.Loader;
using System.Threading;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;

internal static class Program
{
    private const string ProBin = @"C:\Program Files\ArcGIS\Pro\bin";

    private static int Main(string[] args)
    {
        string outDir = args.Length > 0 ? args[0] : Directory.GetCurrentDirectory();
        Directory.CreateDirectory(outDir);
        int rc = 1;
        var t = new Thread(() => rc = Run(outDir));
        t.SetApartmentState(ApartmentState.STA);
        t.Start();
        t.Join();
        return rc;
    }

    private static string FindDll()
    {
        // The repo root is the first folder upward that holds WildlifeTools\.
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
        // ArcGIS assemblies the add-in names are found in Pro's bin if asked for.
        AssemblyLoadContext.Default.Resolving += (ctx, name) =>
        {
            string hit = Directory.EnumerateFiles(ProBin, name.Name + ".dll",
                SearchOption.AllDirectories).FirstOrDefault();
            return hit == null ? null : ctx.LoadFromAssemblyPath(hit);
        };
        // An Application object registers the pack:// scheme the card's logo
        // and font are loaded through.
        _ = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };

        Assembly asm = Assembly.LoadFrom(dll);
        Type winType = asm.GetType("WildlifeTools.About.AboutWindow", true);
        const BindingFlags Any = BindingFlags.Instance | BindingFlags.Static
            | BindingFlags.Public | BindingFlags.NonPublic;

        // (tag to apply, file suffix): null = as the card opens, before the check answers
        var states = new (string Tag, string Name)[]
        {
            (null, "opening"),
            ("v1.113.61.0", "newer_available"),
            ("v1.112.60.0", "current"),
        };
        foreach (var (tag, name) in states)
        {
            var win = (Window)Activator.CreateInstance(winType);
            // Outside an installed add-in there is no Config.daml beside the
            // DLL and the process is not Pro: show the card as it looks there.
            winType.GetMethod("SetVersion", Any).Invoke(win,
                new object[] { "1.112.60.0", new DateTime(2026, 10, 13), "3.7.1" });
            if (tag != null)
            {
                winType.GetMethod("ApplyUpdateResult", Any).Invoke(win, new object[] { tag });
            }

            // Lay the card out at the window's own width, as tall as it
            // wants to be, and draw it on white at 144 DPI.
            var card = (FrameworkElement)win.Content;
            win.Content = null;
            var host = new System.Windows.Controls.Border
            {
                Background = Brushes.White,
                Child = card,
                Width = win.Width,
            };
            foreach (object key in win.Resources.Keys)
            {
                host.Resources[key] = win.Resources[key];
            }
            host.Measure(new Size(win.Width, double.PositiveInfinity));
            host.Arrange(new Rect(host.DesiredSize));
            host.UpdateLayout();

            const double Scale = 1.5;
            var bmp = new RenderTargetBitmap(
                (int)Math.Ceiling(host.ActualWidth * Scale),
                (int)Math.Ceiling(host.ActualHeight * Scale),
                96 * Scale, 96 * Scale, PixelFormats.Pbgra32);
            bmp.Render(host);
            var enc = new PngBitmapEncoder();
            enc.Frames.Add(BitmapFrame.Create(bmp));
            string path = Path.Combine(outDir, "about_card_" + name + ".png");
            using (var fs = File.Create(path))
            {
                enc.Save(fs);
            }
            Console.WriteLine(path + "  " + host.ActualWidth + " x "
                + host.ActualHeight + " (window max height " + win.MaxHeight + ")");
        }
        return 0;
    }
}
