// Draws the three tabs of the Unit Converter window
// (WildlifeTools.UnitConverter.UnitConverterPanel) to PNGs WITHOUT ArcGIS Pro.
// It reads the built add-in DLL, so build the add-in (Release) first. The
// user's units come from a temporary file; nothing is written to the real
// file in the user's profile, because no Save button is pressed.
//   dotnet run (project claude_tests/UnitConverterRender) -- <output folder>

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
        while (dir != null && !Directory.Exists(Path.Combine(dir.FullName, "WildlifeTools"))) dir = dir.Parent;
        return dir == null ? null : Path.Combine(dir.FullName, "WildlifeTools", "bin", "Release", "net10.0-windows", "WildlifeTools.dll");
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
            string hit = Directory.EnumerateFiles(ProBin, name.Name + ".dll", SearchOption.AllDirectories).FirstOrDefault();
            return hit == null ? null : ctx.LoadFromAssemblyPath(hit);
        };
        _ = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        Assembly asm = Assembly.LoadFrom(dll);
        Type panelType = asm.GetType("WildlifeTools.UnitConverter.UnitConverterPanel", true);
        Type catType = asm.GetType("WildlifeTools.UnitConverter.UnitCatalog", true);

        // a catalog with a few units and a saved conversion of "the user's"
        string user = Path.Combine(Path.GetTempPath(), "uc_render_" + Guid.NewGuid().ToString("N") + ".json");
        File.WriteAllText(user, @"{""schemaVersion"":1,
          ""classes"":[{""name"":""Habitat Currency"",""base"":""habitat units"",""baseSingular"":""habitat unit"",""definition"":""A made-up unit for this picture.""}],
          ""units"":[{""name"":""rods (Jeff)"",""singular"":""rod (Jeff)"",""class"":""Length"",""definedAs"":{""unit"":""feet"",""factor"":16.5},""definition"":""A rod, as defined in a field notebook.""},
                     {""name"":""paces (crew)"",""singular"":""pace (crew)"",""class"":""Length"",""definedAs"":{""unit"":""inches"",""factor"":30},""definition"":""One step of a field crew member, measured on a 100-foot tape.""},
                     {""name"":""credits"",""singular"":""credit"",""class"":""Habitat Currency"",""definedAs"":{""unit"":""habitat units"",""factor"":0.25}}],
          ""saved"":[{""name"":""acres to hectares"",""class"":""Area"",""from"":""acres"",""to"":""hectares""},
                     {""name"":""degrees Fahrenheit to degrees Celsius"",""class"":""Temperature"",""from"":""degrees Fahrenheit"",""to"":""degrees Celsius""}]}");
        string bundled = (string)catType.GetMethod("BundledPath", Any).Invoke(null, new object[] { Path.GetDirectoryName(dll) });
        object cat = catType.GetMethod("Load", Any).Invoke(null, new object[] { bundled, user });
        File.Delete(user);

        var panel = (FrameworkElement)Activator.CreateInstance(panelType);
        panelType.GetMethod("SetCatalog", Any).Invoke(panel, new[] { cat });
        var host = new Border { Background = Brushes.White, Child = panel, Width = 980, Height = 720 };
        Layout(host);

        // tab 1: 640 acres to hectares
        panelType.GetMethod("SelectUnits", Any).Invoke(panel, new object[] { false, "acres", "hectares" });
        ((TextBox)panel.FindName("txtInput")).Text = "640";
        Layout(host);
        Save(host, Path.Combine(outDir, "uc_numbers.png"));
        Console.WriteLine("output: " + ((TextBox)panel.FindName("txtOutput")).Text + " " + ((TextBlock)panel.FindName("txtOutUnit")).Text);
        Console.WriteLine(((TextBlock)panel.FindName("txtOneA")).Text);
        Console.WriteLine(((TextBlock)panel.FindName("txtOneB")).Text);
        string out1 = ((TextBox)panel.FindName("txtOutput")).Text;

        // a fraction and a temperature
        panelType.GetMethod("SelectUnits", Any).Invoke(panel, new object[] { false, "inches", "millimeters" });
        ((TextBox)panel.FindName("txtInput")).Text = "5 1/8";
        Layout(host);
        string out2 = ((TextBox)panel.FindName("txtOutput")).Text;
        Console.WriteLine("5 1/8 inches = " + out2 + " " + ((TextBlock)panel.FindName("txtOutUnit")).Text);
        panelType.GetMethod("SelectUnits", Any).Invoke(panel, new object[] { false, "degrees Fahrenheit", "degrees Celsius" });
        ((TextBox)panel.FindName("txtInput")).Text = "98.6";
        Layout(host);
        string out3 = ((TextBox)panel.FindName("txtOutput")).Text;
        Console.WriteLine("98.6 F = " + out3 + " C; " + ((TextBlock)panel.FindName("txtOneB")).Text.Replace(Environment.NewLine, " | "));

        // a separate search for each list (Jeff, 2026-10-08)
        panelType.GetMethod("SelectUnits", Any).Invoke(panel, new object[] { false, "acres", "hectares" });
        ((ComboBox)panel.FindName("cmbClass")).SelectedIndex = 0;
        var findFrom = (TextBox)panel.FindName("txtFindFrom");
        var findTo = (TextBox)panel.FindName("txtFindTo");
        var from = (ListBox)panel.FindName("lstFrom");
        var to = (ListBox)panel.FindName("lstTo");
        findFrom.Text = "acre";
        Layout(host);
        Console.WriteLine("FROM find 'acre' in all classes: " + string.Join(", ", from.Items.Cast<object>().Take(6))
                          + " | TO list still " + to.Items.Count + " units");
        findFrom.Text = "cattle NRCS";
        findTo.Text = "mule deer";
        Layout(host);
        string fromNames = string.Join(", ", from.Items.Cast<object>());
        string toNames = string.Join(", ", to.Items.Cast<object>());
        Console.WriteLine("FROM 'cattle NRCS': " + fromNames);
        Console.WriteLine("TO 'mule deer': " + toNames);
        from.SelectedIndex = from.Items.Cast<object>().ToList().FindIndex(o => o.ToString().StartsWith("cattle months (cow with calf; NRCS)"));
        to.SelectedIndex = to.Items.Cast<object>().ToList().FindIndex(o => o.ToString().StartsWith("mule deer months (mature; NRCS)"));
        ((TextBox)panel.FindName("txtInput")).Text = "1";
        Layout(host);
        string deer = ((TextBox)panel.FindName("txtOutput")).Text;
        Console.WriteLine("1 NRCS cow month = " + deer + " NRCS mule deer months; class now "
                          + ((ComboBox)panel.FindName("cmbClass")).SelectedItem + "; finds kept: '" + findFrom.Text + "', '" + findTo.Text + "'");
        Save(host, Path.Combine(outDir, "uc_find.png"));
        ((Button)panel.FindName("btnReverse")).RaiseEvent(new RoutedEventArgs(System.Windows.Controls.Primitives.ButtonBase.ClickEvent));
        Layout(host);
        Console.WriteLine("after Reverse: FROM find '" + findFrom.Text + "' TO find '" + findTo.Text + "'; output "
                          + ((TextBox)panel.FindName("txtOutput")).Text);
        bool findsOk = fromNames.Contains("cattle months (mature bull; NRCS)") && !fromNames.Contains("NDSU")
                  && toNames.Contains("mule deer months (150 lb; Holechek)") && !toNames.Contains("cattle")
                  && deer.StartsWith("5.0000") && findFrom.Text == "mule deer" && findTo.Text == "cattle NRCS";
        Console.WriteLine(findsOk ? "SEPARATE FINDS OK" : "SEPARATE FINDS FAILED");
        findFrom.Text = "";
        findTo.Text = "";

        // tab 3: my units
        var tabs = (TabControl)panel.FindName("tabs");
        tabs.SelectedIndex = 2;
        Layout(host);
        var mine = (ListView)panel.FindName("lstMine");
        mine.SelectedIndex = 1;
        Layout(host);
        Save(host, Path.Combine(outDir, "uc_myunits.png"));
        Console.WriteLine("my units: " + mine.Items.Count + " rows");

        // tab 2: the table tab (no map outside Pro: the list stays empty)
        tabs.SelectedIndex = 1;
        Layout(host);
        panelType.GetMethod("SelectUnits", Any).Invoke(panel, new object[] { true, "feet", "meters" });
        Layout(host);
        Save(host, Path.Combine(outDir, "uc_field.png"));
        Console.WriteLine("status: " + ((TextBlock)panel.FindName("txtStatus")).Text);

        bool ok = out1 == "258.9988" && out2 == "130.1750" && out3 == "37.0000" && mine.Items.Count == 4;
        Console.WriteLine(ok ? "RENDER OK" : "RENDER PROBLEM");
        return ok ? 0 : 1;
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
        var bmp = new RenderTargetBitmap((int)Math.Ceiling(host.ActualWidth * Scale), (int)Math.Ceiling(host.ActualHeight * Scale),
            96 * Scale, 96 * Scale, PixelFormats.Pbgra32);
        bmp.Render(host);
        var enc = new PngBitmapEncoder();
        enc.Frames.Add(BitmapFrame.Create(bmp));
        using (FileStream fs = File.Create(path)) enc.Save(fs);
        Console.WriteLine(path);
    }
}
