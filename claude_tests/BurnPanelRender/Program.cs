// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Award notice, marking of modified versions, use of names,
// and an additional permission to link with ArcGIS Pro).
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

// Draws the Burn Severity panel (MTBS, RAVG, BAER) to PNGs outside ArcGIS
// Pro, from the BUILT add-in DLL, driving it the way a user would: list
// fires by name and year, add more, uncheck some. Live requests to the
// Burn Severity Portal's fire catalog (internet needed); nothing is
// fetched and no bundle is requested.
//
//   dotnet build WildlifeTools -c Release        (ArcGIS Pro closed)
//   dotnet run --project claude_tests/BurnPanelRender -- <output folder>

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
using System.Windows.Threading;

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

    /// <summary>Lets the dispatcher run (async continuations, bindings) for a while.</summary>
    private static void Pump(double seconds, Func<bool> done = null)
    {
        DateTime until = DateTime.UtcNow.AddSeconds(seconds);
        while (DateTime.UtcNow < until && (done == null || !done()))
        {
            var frame = new DispatcherFrame();
            Dispatcher.CurrentDispatcher.BeginInvoke(DispatcherPriority.Background,
                new Action(() => frame.Continue = false));
            Dispatcher.PushFrame(frame);
            Thread.Sleep(30);
        }
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
        SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext(Dispatcher.CurrentDispatcher));
        Assembly asm = Assembly.LoadFrom(dll);
        Type panelType = asm.GetType("WildlifeTools.OnlineData.BurnSeverity.BurnPanel", true);
        bool ok = true;
        foreach (string program in new[] { "MTBS", "RAVG", "BAER" })
        {
            var panel = (FrameworkElement)Activator.CreateInstance(panelType);
            panelType.GetMethod("SetProgram", Any).Invoke(panel, new object[] { program });
            var host = new Border { Background = Brushes.White, Child = panel, Width = 1080, Height = 800 };
            Layout(host);
            T Find<T>(string name) where T : class => (T)panel.FindName(name);
            void Click(string handler, object sender = null) => panelType.GetMethod(handler, Any)
                .Invoke(panel, new object[] { sender ?? panel, new RoutedEventArgs() });
            var grid = Find<DataGrid>("FireGrid");
            var note = Find<TextBlock>("ListNote");
            string Prop(object row, string prop) => Convert.ToString(row.GetType().GetProperty(prop).GetValue(row));

            // nothing given: a plain message, no request
            Click("OnList");
            Console.WriteLine(program + " nothing given -> " + Find<TextBlock>("StatusText").Text);
            ok &= Find<TextBlock>("StatusText").Text.StartsWith("Give at least");

            // by name and year
            Find<TextBox>("NameBox").Text = "museum";
            Find<TextBox>("YearFromBox").Text = "2019";
            Find<TextBox>("YearToBox").Text = "2019";
            Click("OnList");
            Pump(90, () => note.Text.Contains("match") || note.Text.StartsWith("No "));
            Console.WriteLine(program + " museum 2019: " + grid.Items.Count + " rows; " + note.Text);
            ok &= grid.Items.Count >= 1 && grid.Items.Cast<object>().Any(r => Prop(r, "Name") == "MUSEUM");

            // add another search to the list
            Find<TextBox>("NameBox").Text = "telegraph";
            Find<TextBox>("YearFromBox").Text = "2021";
            Find<TextBox>("YearToBox").Text = "2021";
            int before = grid.Items.Count;
            Click("OnAddToList");
            Pump(90, () => note.Text.Contains("added to the list") || note.Text.StartsWith("No "));
            Console.WriteLine(program + " + telegraph 2021: " + grid.Items.Count + " rows; " + note.Text);
            ok &= grid.Items.Count > before;

            // the same search again adds nothing twice
            int n = grid.Items.Count;
            Click("OnAddToList");
            Pump(90, () => note.Text.Contains("; 0 added"));
            ok &= grid.Items.Count == n;

            // uncheck one: the counts follow, on both tabs
            object first = grid.Items[0];
            first.GetType().GetProperty("Checked").SetValue(first, false);
            Click("OnFireChecked");
            Console.WriteLine(program + " checked: " + Find<TextBlock>("CheckedNote").Text);
            Console.WriteLine(program + " tab 2 says: " + Find<TextBlock>("ChosenText").Text);
            ok &= Find<TextBlock>("CheckedNote").Text.StartsWith((n - 1) + " of " + n);
            Layout(host);
            Save(host, Path.Combine(outDir, "burn_" + program.ToLowerInvariant() + "_tab1.png"));

            // remove the unchecked, then tab 2 with an email address typed
            Click("OnRemoveUnchecked");
            ok &= grid.Items.Count == n - 1;
            Find<TabControl>("Tabs").SelectedIndex = 1;
            Find<TextBox>("PerimetersBox").Text = @"C:\Data\Fire.gdb\" + program + "_fires";
            Find<TextBox>("EmailBox").Text = "you@example.org";
            Console.WriteLine(program + " products: " + string.Join(" | ", Find<StackPanel>("ProductPanel").Children.OfType<CheckBox>().Select(c => c.Content))
                + "; images enabled: " + Find<CheckBox>("ImagesBox").IsEnabled + "; email note: " + Find<TextBlock>("EmailNote").Text);
            ok &= Find<CheckBox>("ImagesBox").IsEnabled;
            Layout(host);
            Save(host, Path.Combine(outDir, "burn_" + program.ToLowerInvariant() + "_tab2.png"));

            // clear
            Find<TabControl>("Tabs").SelectedIndex = 0;
            Click("OnClearList");
            ok &= grid.Items.Count == 0 && Find<TextBlock>("ChosenText").Text.StartsWith("No fire is checked");
        }
        Console.WriteLine(ok ? "PANEL OK" : "PANEL FAILED");
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
