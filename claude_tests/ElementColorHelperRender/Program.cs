// Draws the content of the Element Color Helper window
// (WildlifeTools.ColorHelper.ElementColorHelperPanel) to a PNG WITHOUT ArcGIS
// Pro, with a made-up scan in its table: a dark-map layout with a legend, a
// scale bar, a north arrow, text and a polygon. It reads the built add-in
// DLL, so build the add-in (Release) first. Pro's color picker is not
// created outside Pro (the panel makes it on Loaded), so that slot is empty.
//   dotnet run --project claude_tests/ElementColorHelperRender -- <output folder>

using System;
using System.Collections;
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
        Type panelType = asm.GetType("WildlifeTools.ColorHelper.ElementColorHelperPanel", true);
        Type useType = asm.GetType("WildlifeTools.ColorHelper.ColorUse", true);
        Type roleType = asm.GetType("WildlifeTools.ColorHelper.ColorRole", true);

        // a made-up scan of a dark-map layout
        object Use(string role, byte r, byte g, byte b, int count, bool transparent, params string[] elements)
        {
            object u = Activator.CreateInstance(useType);
            useType.GetProperty("Role").SetValue(u, Enum.Parse(roleType, role));
            useType.GetProperty("R").SetValue(u, r);
            useType.GetProperty("G").SetValue(u, g);
            useType.GetProperty("B").SetValue(u, b);
            useType.GetProperty("Count").SetValue(u, count);
            useType.GetProperty("AnyTransparent").SetValue(u, transparent);
            var set = useType.GetProperty("Elements").GetValue(u);
            MethodInfo add = set.GetType().GetMethod("Add");
            foreach (string e in elements) add.Invoke(set, new object[] { e });
            return u;
        }
        var list = (IList)Activator.CreateInstance(typeof(System.Collections.Generic.List<>).MakeGenericType(useType));
        list.Add(Use("Text", 0, 0, 0, 9, false, "Legend", "Scale Bar", "Title"));
        list.Add(Use("Text", 64, 64, 64, 3, false, "Legend"));
        list.Add(Use("Lines", 0, 0, 0, 5, true, "Scale Bar", "Study Area"));
        list.Add(Use("Fills", 0, 0, 0, 2, false, "Scale Bar"));
        list.Add(Use("Fills", 255, 255, 255, 2, false, "Scale Bar"));
        list.Add(Use("Fills", 200, 30, 30, 1, true, "Study Area"));
        list.Add(Use("Markers", 0, 0, 0, 2, false, "North Arrow"));

        var panel = (FrameworkElement)Activator.CreateInstance(panelType);
        panelType.GetMethod("ShowScan", Any).Invoke(panel, new object[]
        {
            "5 elements selected on the layout: 1 legend, 1 scale bar, 1 north arrow, 1 text, 1 graphic.",
            list,
        });
        // check two rows and give them a new color, as a user would
        var rows = (IEnumerable)panelType.GetField("_rows", Any).GetValue(panel);
        int i = 0;
        foreach (object row in rows)
        {
            Type rt = row.GetType();
            if (i == 0 || i == 2)
            {
                rt.GetProperty("Checked").SetValue(row, true);
                rt.GetProperty("New").SetValue(row, (ValueTuple<byte, byte, byte>?)new ValueTuple<byte, byte, byte>(255, 255, 255));
            }
            i++;
        }
        panelType.GetMethod("UpdateButtons", Any).Invoke(panel, null);

        var host = new Border { Background = Brushes.White, Child = panel, Width = 884, Height = 520 };
        for (int pass = 0; pass < 2; pass++)
        {
            host.Measure(new Size(host.Width, host.Height));
            host.Arrange(new Rect(0, 0, host.Width, host.Height));
            host.UpdateLayout();
        }
        const double Scale = 1.5;
        var bmp = new RenderTargetBitmap((int)Math.Ceiling(host.ActualWidth * Scale),
            (int)Math.Ceiling(host.ActualHeight * Scale), 96 * Scale, 96 * Scale, PixelFormats.Pbgra32);
        bmp.Render(host);
        var enc = new PngBitmapEncoder();
        enc.Frames.Add(BitmapFrame.Create(bmp));
        string path = Path.Combine(outDir, "element_color_helper.png");
        using (FileStream fs = File.Create(path)) enc.Save(fs);
        Console.WriteLine(path);

        var apply = (Button)panel.FindName("btnApply");
        var assign = (Button)panel.FindName("btnAssign");
        Console.WriteLine("Apply enabled (two rows have a new color): " + apply.IsEnabled
            + "; Assign enabled (no picker outside Pro): " + assign.IsEnabled);
        return apply.IsEnabled && !assign.IsEnabled ? 0 : 1;
    }
}
