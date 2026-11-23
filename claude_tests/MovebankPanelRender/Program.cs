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

// Draws the Movebank Animal Tracks panel to PNGs outside ArcGIS Pro, from the
// BUILT add-in DLL, driving it the way a user would: search, select a
// dataset, look at its details. Live requests to the Movebank Data
// Repository (internet needed); nothing is downloaded.
//
//   dotnet build WildlifeTools -c Release        (ArcGIS Pro closed)
//   dotnet run --project claude_tests/MovebankPanelRender -- <output folder>

using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.Loader;
using System.Threading;
using System.Threading.Tasks;
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

    private static void Await(object task, double seconds)
    {
        var t = (Task)task;
        Pump(seconds, () => t.IsCompleted);
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
        // awaits in the panel must come back to this thread, as they do in a running WPF application
        SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext(Dispatcher.CurrentDispatcher));
        Assembly asm = Assembly.LoadFrom(dll);
        Type panelType = asm.GetType("WildlifeTools.OnlineData.Movebank.MovebankPanel", true);
        var panel = (FrameworkElement)Activator.CreateInstance(panelType);
        var host = new Border { Background = Brushes.White, Child = panel, Width = 1020, Height = 740 };
        Layout(host);

        T Find<T>(string name) where T : class => (T)panel.FindName(name);
        void Click(string handler) => panelType.GetMethod(handler, Any)
            .Invoke(panel, new object[] { panel, new RoutedEventArgs() });
        var tabs = Find<TabControl>("Tabs");
        bool ok = true;

        // ---- tab 1: search, then select a dataset ------------------------------
        Find<TextBox>("SearchBox").Text = "pronghorn";
        Click("OnSearch");
        var grid = Find<DataGrid>("ResultsGrid");
        var note = Find<TextBlock>("SearchNote");
        Pump(60, () => grid.Items.Count > 0 || note.Text.StartsWith("No published"));
        Console.WriteLine("search 'pronghorn': " + grid.Items.Count + " rows; note: " + note.Text);
        ok &= grid.Items.Count >= 1;
        int pick = -1;
        for (int i = 0; i < grid.Items.Count; i++)
        {
            string title = (string)grid.Items[i].GetType().GetProperty("Title").GetValue(grid.Items[i]);
            if (title.Contains("Pronghorn (Antilocapra americana), Wyoming")) pick = i;
        }
        Console.WriteLine("the Wyoming pronghorn study is row " + pick);
        ok &= pick >= 0;
        grid.SelectedIndex = pick;
        var files = Find<TextBlock>("DetailFiles");
        Pump(60, () => files.Text.StartsWith("Files to fetch"));
        Console.WriteLine("details: " + Find<TextBlock>("DetailFacts").Text);
        Console.WriteLine("files: " + files.Text);
        Console.WriteLine("citation: " + Find<TextBlock>("DetailCitation").Text);
        ok &= files.Text.StartsWith("Files to fetch: 1 data file") && Find<TextBlock>("DetailCitation").Text.Contains("10.5441/001/1.74pf3n2p");
        Layout(host);
        Save(host, Path.Combine(outDir, "movebank_tab1_find.png"));
        // Choose this dataset goes on to tab 2 with it
        var choose = Find<Button>("ChooseButton");
        Console.WriteLine("Choose enabled with a row selected: " + choose.IsEnabled);
        Click("OnChoose");
        Console.WriteLine("after Choose: tab " + (tabs.SelectedIndex + 1) + "; " + Find<TextBlock>("ChosenText").Text);
        ok &= choose.IsEnabled && tabs.SelectedIndex == 1 && Find<TextBlock>("ChosenText").Text.Contains("Pronghorn");
        tabs.SelectedIndex = 0;
        // Clear empties the box, the list and the choice
        Click("OnClear");
        Layout(host);
        Console.WriteLine("after Clear: " + grid.Items.Count + " rows; box '" + Find<TextBox>("SearchBox").Text + "'; Choose enabled: " + choose.IsEnabled
            + "; details: " + Find<TextBlock>("DetailTitle").Text + "; tab 2 says: " + Find<TextBlock>("ChosenText").Text);
        ok &= grid.Items.Count == 0 && !choose.IsEnabled && Find<TextBlock>("ChosenText").Text.StartsWith("No dataset is chosen");
        Save(host, Path.Combine(outDir, "movebank_tab1_cleared.png"));

        // an empty search lists everything, newest first, and can show more
        Find<TextBox>("SearchBox").Text = "";
        Click("OnSearch");
        Pump(60, () => grid.Items.Count >= 25);
        Console.WriteLine("empty search: " + grid.Items.Count + " rows; note: " + note.Text + "; Show more enabled: " + Find<Button>("MoreButton").IsEnabled);
        ok &= grid.Items.Count == 25 && Find<Button>("MoreButton").IsEnabled;
        Click("OnMore");
        Pump(60, () => grid.Items.Count >= 50);
        Console.WriteLine("after Show more: " + grid.Items.Count + " rows");
        ok &= grid.Items.Count == 50;

        // ---- tab 2 ---------------------------------------------------------------
        tabs.SelectedIndex = 1;
        Find<TextBox>("PointsBox").Text = @"C:\Data\Wildlife.gdb\Movebank_points";
        Find<TextBox>("LinesBox").Text = @"C:\Data\Wildlife.gdb\Movebank_tracks";
        Find<RadioButton>("OutLines").IsChecked = true;
        Layout(host);
        Console.WriteLine("track lines only -> points box enabled: " + Find<TextBox>("PointsBox").IsEnabled + ", lines box enabled: " + Find<TextBox>("LinesBox").IsEnabled);
        ok &= !Find<TextBox>("PointsBox").IsEnabled && Find<TextBox>("LinesBox").IsEnabled;
        Find<RadioButton>("OutBoth").IsChecked = true;
        Console.WriteLine("chosen: " + Find<TextBlock>("ChosenText").Text);
        var log = panelType.GetMethod("Log", Any);
        foreach (string line in new[] {
            "Dataset: Data from: Study \"Pronghorn (Antilocapra americana), Wyoming/Colorado, USA\"",
            "The data file holds 99,673 rows: 99,673 location fixes of 32 animals, from 2017-11-14 to 2019-11-07 (UTC).",
            @"Wrote 99,673 points to C:\Data\Wildlife.gdb\Movebank_points.",
            @"Wrote 32 track lines to C:\Data\Wildlife.gdb\Movebank_tracks, one for each animal, through its fixes in order of time." })
        {
            log.Invoke(panel, new object[] { line });
        }
        Layout(host);
        Save(host, Path.Combine(outDir, "movebank_tab2_get.png"));

        // ---- the studies of a signed-in account (only when this computer holds a Movebank sign-in) ----
        tabs.SelectedIndex = 0;
        host.Height = 860;
        string account = Find<TextBlock>("AccountText").Text;
        Console.WriteLine("account line: " + account);
        if (account.StartsWith("Signed in"))
        {
            Find<RadioButton>("SourceStudies").IsChecked = true;
            Pump(120, () => grid.Items.Count > 0 || note.Text.StartsWith("None of") || note.Text.Contains("could not") || note.Text.Contains("did not"));
            Console.WriteLine("studies listed: " + grid.Items.Count + "; note: " + note.Text);
            ok &= grid.Items.Count > 100;
            Find<TextBox>("SearchBox").Text = "Tadarida brasiliensis flight";
            Click("OnSearch");
            Pump(60, () => note.Text.Contains(" of the "));
            Console.WriteLine("search 'Tadarida brasiliensis flight': " + grid.Items.Count + " rows; note: " + note.Text);
            int bats = -1;
            for (int i = 0; i < grid.Items.Count; i++)
            {
                object st = grid.Items[i].GetType().GetProperty("Study").GetValue(grid.Items[i]);
                if ((string)st.GetType().GetField("Id").GetValue(st) == "433126")
                {
                    bats = i;
                }
            }
            ok &= bats >= 0;
            grid.SelectedIndex = bats;
            Pump(1);
            Console.WriteLine("study details: " + Find<TextBlock>("DetailFacts").Text);
            Console.WriteLine("   " + Find<TextBlock>("DetailFiles").Text);
            Console.WriteLine("   " + Find<TextBlock>("DetailMore").Text.Replace(Environment.NewLine, " | "));
            Console.WriteLine("   " + Find<TextBlock>("DetailCitation").Text);
            Console.WriteLine("Choose button: " + Find<Button>("ChooseButton").Content + ", enabled " + Find<Button>("ChooseButton").IsEnabled);
            ok &= Find<TextBlock>("DetailFacts").Text.Contains("Movebank study 433126") && Find<Button>("ChooseButton").IsEnabled;
            Layout(host);
            Save(host, Path.Combine(outDir, "movebank_tab1_studies.png"));
            Click("OnChoose");
            Console.WriteLine("after Choose: tab " + (tabs.SelectedIndex + 1) + "; " + Find<TextBlock>("ChosenText").Text
                + "; terms panel " + Find<StackPanel>("TermsPanel").Visibility + "; accept box enabled " + Find<CheckBox>("AcceptBox").IsEnabled);
            ok &= tabs.SelectedIndex == 1 && Find<StackPanel>("TermsPanel").Visibility == Visibility.Visible && !Find<CheckBox>("AcceptBox").IsEnabled;
            // this account accepted the terms of study 433126 on 2026-11-15, so Movebank sends none and no window opens
            Click("OnShowTerms");
            var termsNote = Find<TextBlock>("TermsNote");
            Pump(60, () => termsNote.Text.StartsWith("Your Movebank account has already") || Find<TextBlock>("StatusText").Text.Contains("could not"));
            Console.WriteLine("terms: " + termsNote.Text);
            ok &= termsNote.Text.StartsWith("Your Movebank account has already");
            Layout(host);
            Save(host, Path.Combine(outDir, "movebank_tab2_study.png"));
            // back to the repository: the list and the labels follow
            tabs.SelectedIndex = 0;
            Find<RadioButton>("SourceRepo").IsChecked = true;
            Pump(1);
            Console.WriteLine("back to the repository: Choose button '" + Find<Button>("ChooseButton").Content + "'; tab 2 says: " + Find<TextBlock>("ChosenText").Text);
            ok &= (string)Find<Button>("ChooseButton").Content == "Choose this dataset" && Find<TextBlock>("ChosenText").Text.StartsWith("No dataset is chosen");
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
