// Draws the content of the Manage Custom Graphics and North Arrows window
// (WildlifeTools.CustomGraphics.ManageGraphicsPanel) to PNG files WITHOUT
// ArcGIS Pro. The panel is plain WPF and needs nothing from Pro to draw
// itself. It reads the built add-in DLL, so build the add-in (Release) first.
// It registers a few sample images in a TEMPORARY store folder; the real
// store in the user profile is never touched.
//   dotnet run --project claude_tests/ManageGraphicsRender -- <output folder>
//
// Drawn outside Pro, the panel has WPF's plain look (no Pro theme, no window
// title bar). It is the layout that this checks: nothing clipped, nothing
// overlapping, the crosshair where the focal point says.

using System;
using System.Globalization;
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

    private static string SavePicture(string path, int w, int h, Action<DrawingContext> draw)
    {
        var dv = new DrawingVisual();
        using (DrawingContext dc = dv.RenderOpen())
        {
            draw(dc);
        }
        var bmp = new RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32);
        bmp.Render(dv);
        var enc = new PngBitmapEncoder();
        enc.Frames.Add(BitmapFrame.Create(bmp));
        using (FileStream fs = File.Create(path))
        {
            enc.Save(fs);
        }
        return path;
    }

    private static void SaveVisual(FrameworkElement host, string path)
    {
        const double Scale = 1.5;
        var bmp = new RenderTargetBitmap(
            (int)Math.Ceiling(host.ActualWidth * Scale),
            (int)Math.Ceiling(host.ActualHeight * Scale),
            96 * Scale, 96 * Scale, PixelFormats.Pbgra32);
        bmp.Render(host);
        var enc = new PngBitmapEncoder();
        enc.Frames.Add(BitmapFrame.Create(bmp));
        using (FileStream fs = File.Create(path))
        {
            enc.Save(fs);
        }
        Console.WriteLine(path);
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
        // An Application object registers the pack:// scheme the built-in
        // previews are loaded through.
        _ = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };

        Assembly asm = Assembly.LoadFrom(dll);
        Type libType = asm.GetType("WildlifeTools.CustomGraphics.GraphicLibrary", true);
        Type kindType = asm.GetType("WildlifeTools.CustomGraphics.GraphicKind", true);
        Type panelType = asm.GetType("WildlifeTools.CustomGraphics.ManageGraphicsPanel", true);
        object graphic = Enum.ToObject(kindType, 0), arrow = Enum.ToObject(kindType, 1);

        string work = Path.Combine(Path.GetTempPath(), "ManageGraphicsRender_" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(work);
        try
        {
            libType.GetMethod("UseStoreFolder", Any).Invoke(null, new object[] { Path.Combine(work, "store") });
            MethodInfo register = libType.GetMethod("Register", Any);
            MethodInfo setFocal = libType.GetMethod("SetFocal", Any);

            // ---- sample images: a wide logo, a square seal, a tall north arrow
            var green = new SolidColorBrush(Color.FromRgb(0x2E, 0x5D, 0x43));
            var gold = new SolidColorBrush(Color.FromRgb(0xC9, 0xA2, 0x27));
            var face = new Typeface("Georgia");
            string logo = SavePicture(Path.Combine(work, "Field Station Logo.png"), 720, 300, dc =>
            {
                dc.DrawRoundedRectangle(green, null, new Rect(0, 0, 720, 300), 30, 30);
                dc.DrawEllipse(gold, null, new Point(150, 150), 96, 96);
                dc.DrawText(new FormattedText("FIELD STATION", CultureInfo.InvariantCulture,
                    FlowDirection.LeftToRight, face, 50, Brushes.White, 1.0), new Point(280, 118));
            });
            string seal = SavePicture(Path.Combine(work, "Agency Seal.png"), 400, 400, dc =>
            {
                dc.DrawEllipse(Brushes.White, new Pen(green, 16), new Point(200, 200), 188, 188);
                dc.DrawEllipse(gold, null, new Point(200, 200), 120, 120);
            });
            string north = SavePicture(Path.Combine(work, "Arrow with N.png"), 240, 520, dc =>
            {
                var g = new StreamGeometry();
                using (StreamGeometryContext c = g.Open())
                {
                    c.BeginFigure(new Point(120, 130), true, true);
                    c.LineTo(new Point(220, 500), true, false);
                    c.LineTo(new Point(120, 410), true, false);
                    c.LineTo(new Point(20, 500), true, false);
                }
                dc.DrawGeometry(Brushes.Black, null, g);
                dc.DrawText(new FormattedText("N", CultureInfo.InvariantCulture,
                    FlowDirection.LeftToRight, face, 110, Brushes.Black, 1.0), new Point(80, -6));
            });

            object eLogo = register.Invoke(null, new[] { graphic, logo });
            register.Invoke(null, new[] { graphic, seal });
            object eNorth = register.Invoke(null, new[] { arrow, north });
            // the logo's emblem, and the hub of the arrow below its letter
            setFocal.Invoke(null, new[] { graphic, eLogo, 0.208, 0.5 });
            setFocal.Invoke(null, new[] { arrow, eNorth, 0.5, 0.39 });
            string logoId = (string)eLogo.GetType().GetProperty("Id").GetValue(eLogo);
            string northId = (string)eNorth.GetType().GetProperty("Id").GetValue(eNorth);

            var shots = new (string File, object Kind, string Id)[]
            {
                ("manage_graphics_custom.png", graphic, logoId),
                ("manage_graphics_builtin.png", graphic, "builtin:CompassRoseDot"),
                ("manage_north_arrows.png", arrow, northId),
            };
            bool failed = false;
            foreach (var shot in shots)
            {
                var panel = (FrameworkElement)Activator.CreateInstance(panelType);
                panelType.GetMethod("ShowKind", Any).Invoke(panel, new[] { shot.Kind, shot.Id });
                // the client area of the window at its starting size
                var host = new Border { Background = Brushes.White, Child = panel, Width = 884, Height = 620 };
                for (int pass = 0; pass < 3; pass++)
                {
                    host.Measure(new Size(host.Width, host.Height));
                    host.Arrange(new Rect(0, 0, host.Width, host.Height));
                    host.UpdateLayout();
                }
                SaveVisual(host, Path.Combine(outDir, shot.File));

                // The arrow keys: one pixel of the preview, right and up positive.
                // (A built-in design must not move.)
                var frame = (FrameworkElement)panel.FindName("previewFrame");
                object entry = panelType.GetField("_sel", Any).GetValue(panel);
                PropertyInfo fxP = entry.GetType().GetProperty("FocalX"), fyP = entry.GetType().GetProperty("FocalY");
                double x0 = (double)fxP.GetValue(entry), y0 = (double)fyP.GetValue(entry);
                MethodInfo nudge = panelType.GetMethod("NudgeFocal", Any);
                nudge.Invoke(panel, new object[] { 1, 0 });
                nudge.Invoke(panel, new object[] { 0, 10 });
                double x1 = (double)fxP.GetValue(entry), y1 = (double)fyP.GetValue(entry);
                bool builtIn = shot.Id.StartsWith("builtin:");
                bool ok = builtIn
                    ? x1 == x0 && y1 == y0
                    : Math.Abs((x1 - x0) - 1.0 / frame.ActualWidth) < 1e-4
                      && Math.Abs((y1 - y0) - 10.0 / frame.ActualHeight) < 1e-4;
                Console.WriteLine(string.Format(CultureInfo.InvariantCulture,
                    "   nudge right 1, up 10 on a {0:0} x {1:0} preview: X {2} -> {3}, Y {4} -> {5}   {6}",
                    frame.ActualWidth, frame.ActualHeight, x0, x1, y0, y1,
                    ok ? "ok" : "WRONG"));
                if (!ok)
                {
                    failed = true;
                }
            }
            return failed ? 1 : 0;
        }
        finally
        {
            try { Directory.Delete(work, true); } catch { }
        }
    }
}
