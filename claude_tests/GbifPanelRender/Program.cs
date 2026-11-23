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

// Draws the GBIF Species Occurrences panel to PNGs outside ArcGIS Pro,
// from the BUILT add-in DLL, driving it the way a user would: type a name,
// add it, count, list species, show photographs, list the account's
// downloads. Live GBIF requests (internet needed); nothing is requested
// that changes the account.
//
//   dotnet build WildlifeTools -c Release        (ArcGIS Pro closed)
//   dotnet run --project claude_tests/GbifPanelRender -- <output folder>

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
        Type panelType = asm.GetType("WildlifeTools.OnlineData.Gbif.GbifPanel", true);
        var panel = (FrameworkElement)Activator.CreateInstance(panelType);
        var host = new Border { Background = Brushes.White, Child = panel, Width = 1020, Height = 740 };
        Layout(host);

        T Find<T>(string name) where T : class => (T)panel.FindName(name);
        void Click(string handler) => panelType.GetMethod(handler, Any)
            .Invoke(panel, new object[] { panel, new RoutedEventArgs() });
        var tabs = Find<TabControl>("Tabs");
        bool ok = true;

        // ---- tab 1: the taxonomy tree, a typed name, a chosen genus -----------
        Await(panelType.GetMethod("LoadKingdomsAsync", Any).Invoke(panel, null), 30);
        var tree = Find<TreeView>("BrowseTree");
        Console.WriteLine("kingdoms in the tree: " + tree.Items.Count);
        ok &= tree.Items.Count >= 6;

        Find<TextBox>("NameBox").Text = "Cyanocit";
        Await(panelType.GetMethod("SuggestAsync", Any).Invoke(panel, null), 30);
        var suggest = Find<DataGrid>("SuggestGrid");
        Console.WriteLine("suggestions for 'Cyanocit': " + suggest.Items.Count + "; first: " + suggest.Items.Cast<object>().FirstOrDefault());
        ok &= suggest.Items.Count > 0;
        // the common name, the group and the number of records fill in a moment later
        string Cell(object row, string prop) => (string)row.GetType().GetProperty(prop).GetValue(row);
        Pump(40, () => suggest.Items.Cast<object>().All(r => Cell(r, "CountText").Length > 0));
        foreach (object r in suggest.Items.Cast<object>().Take(4))
        {
            Console.WriteLine("   " + Cell(r, "Name") + " | " + Cell(r, "Common") + " | " + Cell(r, "Rank") + " | " + Cell(r, "Group") + " | " + Cell(r, "CountText"));
        }
        ok &= suggest.Items.Cast<object>().All(r => Cell(r, "CountText").Length > 0);
        ok &= suggest.Items.Cast<object>().Any(r => Cell(r, "Common").Length > 0);
        var chosen = Find<ListBox>("ChosenList");
        // two names selected at once (Shift or Ctrl in the window) are both added
        suggest.SelectedItems.Clear();
        foreach (object o in suggest.Items.Cast<object>().Where(o => o.ToString().StartsWith("Cyanocitta cristata (") || o.ToString().StartsWith("Cyanocitta stelleri (")).ToList())
        {
            suggest.SelectedItems.Add(o);
        }
        Click("OnAddSuggested");
        Console.WriteLine("two selected at once -> chosen: " + chosen.Items.Count);
        ok &= chosen.Items.Count == 2;
        Click("OnClearChosen");
        suggest.SelectedItems.Clear();
        suggest.SelectedIndex = suggest.Items.Cast<object>().ToList().FindIndex(o => o.ToString().StartsWith("Cyanocitta") && o.ToString().Contains("genus"));
        Click("OnAddSuggested");
        Console.WriteLine("chosen: " + string.Join(" | ", chosen.Items.Cast<object>()));
        ok &= chosen.Items.Count == 1;

        // country and state lists
        Await(panelType.GetMethod("LoadCountriesAsync", Any).Invoke(panel, null), 30);
        var country = Find<ComboBox>("CountryBox");
        Console.WriteLine("countries: " + country.Items.Count);
        country.SelectedIndex = country.Items.Cast<object>().ToList().FindIndex(o => o.ToString().StartsWith("United States of America"));
        Click("OnListStates");
        var state = Find<ComboBox>("StateBox");
        Pump(30, () => state.Items.Count > 1);
        Console.WriteLine("states listed: " + (state.Items.Count - 1) + "; e.g. " + string.Join(" | ", state.Items.Cast<object>().Skip(1).Take(3)));
        Console.WriteLine("state note: " + Find<TextBlock>("StateNote").Text);
        ok &= Find<TextBlock>("StateNote").Text.StartsWith("The matching records use ")
            && Find<TextBlock>("StateNote").TextWrapping == TextWrapping.Wrap
            && Find<TextBlock>("StatusText").TextWrapping == TextWrapping.Wrap
            && Find<TextBlock>("StatusText").Visibility == Visibility.Collapsed;
        // a message shows on its own wrapped line and is cleared when the tab changes
        panelType.GetMethod("Say", Any).Invoke(panel, new object[] { "Check at least one month. " + new string('x', 30) + " A long message must wrap onto further lines, not be cut off with dots at the right edge of the window, however long it becomes." });
        Layout(host);
        var status = Find<TextBlock>("StatusText");
        Console.WriteLine("message line: visible " + (status.Visibility == Visibility.Visible) + ", height " + status.ActualHeight.ToString("0") + " (one line is about 16)");
        ok &= status.Visibility == Visibility.Visible && status.ActualHeight > 28;
        Save(host, Path.Combine(outDir, "gbif_message_line.png"));
        tabs.SelectedIndex = 1;
        tabs.RaiseEvent(new SelectionChangedEventArgs(System.Windows.Controls.Primitives.Selector.SelectionChangedEvent, new object[0], new object[0]) { Source = tabs });
        ok &= status.Visibility == Visibility.Collapsed;
        tabs.SelectedIndex = 0;
        ok &= country.Items.Count > 200 && country.SelectedIndex > 0 && state.Items.Count > 10;
        country.SelectedIndex = 0;
        ok &= state.Items.Count == 1;        // state names go with the country they were listed for
        Find<CheckBox>("IncludeNoCoordBox").IsChecked = true;
        Console.WriteLine("no-coordinates note: " + Find<TextBlock>("NoCoordNote").Text);
        ok &= Find<TextBlock>("NoCoordNote").Text.StartsWith("Records without coordinates are written as rows")
            && Find<TextBlock>("NoCoordNote").Text.Contains("Choose a Country") && Find<CheckBox>("IncludeNoCoordBox").IsEnabled;
        Find<CheckBox>("IncludeNoCoordBox").IsChecked = false;

        Find<TextBox>("YearFromBox").Text = "2015";
        Click("OnCount");
        Pump(20, () => Find<TextBlock>("CountText").Text.StartsWith("GBIF has"));
        Console.WriteLine("count: " + Find<TextBlock>("CountText").Text);
        ok &= Find<TextBlock>("CountText").Text.StartsWith("GBIF has");
        Layout(host);
        Save(host, Path.Combine(outDir, "gbif_tab1_what_and_where.png"));

        // a misspelling
        Find<TextBox>("NameBox").Text = "Cyanocita steleri";      // the typing timer runs the search
        Pump(20, () => Find<TextBlock>("MatchNote").Text.StartsWith("No name"));
        Console.WriteLine("misspelling: " + Find<TextBlock>("MatchNote").Text + " " + suggest.Items.Cast<object>().FirstOrDefault());
        ok &= suggest.Items.Count >= 1 && suggest.Items[0].ToString().StartsWith("Cyanocitta stelleri");

        // ---- tab 2: species and photographs -------------------------------------
        // opening the tab lists the species by itself, for the choices on tab 1
        Find<TextBox>("NameBox").Text = "";
        tabs.SelectedIndex = 1;
        var grid = Find<DataGrid>("SpeciesGrid");
        Pump(40, () => grid.Items.Count > 0 && grid.Items.Cast<object>().All(r => !r.ToString().Contains("looking up")
            && !((string)r.GetType().GetProperty("Name").GetValue(r)).StartsWith("(looking")));
        Console.WriteLine("species: " + grid.Items.Count + "; " + Find<TextBlock>("SpeciesNote").Text);
        foreach (object r in grid.Items.Cast<object>().Take(3))
        {
            Console.WriteLine("   " + r.GetType().GetProperty("Name").GetValue(r) + "  " + r.GetType().GetProperty("CountText").GetValue(r));
        }
        ok &= grid.Items.Count >= 2;
        Pump(1);
        sources_placeholder_check(grid, ref ok);
        // the "show" link of a row passes THAT row, whatever is highlighted
        grid.SelectedIndex = grid.Items.Count - 1;
        Await(panelType.GetMethod("ShowPhotosForAsync", Any).Invoke(panel, new object[] { grid.Items[0] }), 40);
        var photos = Find<StackPanel>("PhotoPanel");
        Pump(40, () => photos.Children.Count >= 6 && photos.Children.OfType<StackPanel>()
            .All(s => ((Image)s.Children[0]).Source != null));
        Console.WriteLine("photographs: " + photos.Children.Count + "; " + Find<TextBlock>("PhotoNote").Text);
        ok &= photos.Children.Count >= 3;
        Layout(host);
        Save(host, Path.Combine(outDir, "gbif_tab2_species_here.png"));

        // the list follows the choices: back on tab 1 choose one species only, return, and it is listed again
        long before2 = (long)grid.Items[0].GetType().GetProperty("Count").GetValue(grid.Items[0]);
        int speciesBefore = grid.Items.Count;
        tabs.SelectedIndex = 0;
        Pump(0.5);
        Click("OnClearChosen");
        Find<TextBox>("NameBox").Text = "Cyanocitta stelleri";
        Pump(20, () => suggest.Items.Count > 0 && suggest.Items[0].ToString().StartsWith("Cyanocitta stelleri ("));
        suggest.SelectedIndex = 0;
        Click("OnAddSuggested");
        Find<TextBox>("NameBox").Text = "";
        tabs.SelectedIndex = 1;
        Pump(40, () => grid.Items.Count == 1 && !((string)grid.Items[0].GetType().GetProperty("Name").GetValue(grid.Items[0])).StartsWith("("));
        Console.WriteLine("after changing tab 1 to one species: " + grid.Items.Count + " species listed (was " + speciesBefore + "), "
            + grid.Items[0].GetType().GetProperty("Name").GetValue(grid.Items[0]) + " "
            + grid.Items[0].GetType().GetProperty("CountText").GetValue(grid.Items[0]) + "; photographs cleared: " + (photos.Children.Count == 0));
        ok &= speciesBefore == 2 && grid.Items.Count == 1 && photos.Children.Count == 0
            && (long)grid.Items[0].GetType().GetProperty("Count").GetValue(grid.Items[0]) < before2;
        // coming back without changing anything does not ask again
        object sameRow = grid.Items[0];
        tabs.SelectedIndex = 0;
        Pump(0.5);
        tabs.SelectedIndex = 1;
        Pump(3);
        ok &= ReferenceEquals(grid.Items[0], sameRow);
        Console.WriteLine("returning with nothing changed keeps the same list: " + ReferenceEquals(grid.Items[0], sameRow));
        // restore the genus for the rest of the run
        tabs.SelectedIndex = 0;
        Pump(0.5);
        Click("OnClearChosen");
        Find<TextBox>("NameBox").Text = "Cyanocit";
        Pump(20, () => suggest.Items.Cast<object>().Any(o => o.ToString().StartsWith("Cyanocitta") && o.ToString().Contains("genus")));
        suggest.SelectedItems.Clear();
        suggest.SelectedIndex = suggest.Items.Cast<object>().ToList().FindIndex(o => o.ToString().StartsWith("Cyanocitta") && o.ToString().Contains("genus"));
        Click("OnAddSuggested");
        Find<TextBox>("NameBox").Text = "";
        Click("OnCount");
        Pump(20, () => Find<TextBlock>("CountText").Text.StartsWith("GBIF has"));

        // ---- tab 3: sources (listed by itself when the tab opens) ---------------------------
        tabs.SelectedIndex = 2;
        var sources = Find<DataGrid>("SourcesGrid");
        Pump(60, () => sources.Items.Count > 0 && sources.Items.Cast<object>()
            .All(r => !((string)r.GetType().GetProperty("Name").GetValue(r)).StartsWith("(looking")));
        Pump(1);
        Console.WriteLine("sources: " + sources.Items.Count + "; " + Find<TextBlock>("SourcesNote").Text);
        Layout(host);
        sources_placeholder_check(sources, ref ok);
        object firstSource = sources.Items[0];
        long firstCount = (long)firstSource.GetType().GetProperty("Count").GetValue(firstSource);
        Console.WriteLine("   largest: " + firstSource.GetType().GetProperty("Name").GetValue(firstSource) + "  " + firstCount);
        // every row's link leads to that row's own dataset, and the drawn name is that row's name
        var rowT = firstSource.GetType();
        MethodInfo urlOf = panelType.GetMethod("SourceUrl", Any);
        int linked = 0, wrong = 0;
        foreach (object item in sources.Items)
        {
            if (!(sources.ItemContainerGenerator.ContainerFromItem(item) is DataGridRow drow)) continue;
            foreach (System.Windows.Documents.Hyperlink h in Descendants<TextBlock>(drow).SelectMany(tb => tb.Inlines.OfType<System.Windows.Documents.Hyperlink>()))
            {
                linked++;
                string key = (string)rowT.GetProperty("Key").GetValue(h.DataContext);
                string shown = string.Concat(h.Inlines.OfType<System.Windows.Documents.Run>().Select(r => r.Text));
                if (!ReferenceEquals(h.DataContext, item) || (string)urlOf.Invoke(null, new[] { item }) != "https://www.gbif.org/dataset/" + key
                    || shown != (string)rowT.GetProperty("Name").GetValue(item)) wrong++;
            }
        }
        Console.WriteLine("   source links drawn: " + linked + ", pointing at the wrong row: " + wrong);
        ok &= linked >= 3 && wrong == 0;
        string before = Find<TextBlock>("CountText").Text;
        firstSource.GetType().GetProperty("Checked").SetValue(firstSource, false);
        Pump(1);
        Console.WriteLine("   " + Find<TextBlock>("ExcludedNote").Text);
        Click("OnCount");
        Pump(20, () => Find<TextBlock>("CountText").Text.StartsWith("GBIF has"));
        Console.WriteLine("   count before: " + before.Split('.')[0] + " | after leaving it out: " + Find<TextBlock>("CountText").Text.Split('.')[0]);
        ok &= sources.Items.Count >= 3 && Find<TextBlock>("ExcludedNote").Text.StartsWith("1 source")
            && Find<TextBlock>("CountText").Text.StartsWith("GBIF has") && Find<TextBlock>("CountText").Text.Split('.')[0] != before.Split('.')[0];
        Layout(host);
        Save(host, Path.Combine(outDir, "gbif_tab3_sources.png"));
        Click("OnCheckSources");
        Pump(1);
        ok &= Find<TextBlock>("ExcludedNote").Text.StartsWith("Every source");

        // ---- tab 4: get the data (account line, nothing run) -----------------------
        tabs.SelectedIndex = 3;
        panelType.GetMethod("ShowAccount", Any).Invoke(panel, null);
        Find<TextBox>("OutBox").Text = @"C:\Data\Wildlife.gdb\GBIF_occurrences";
        // the report: bulleted items with space between them, details indented beneath
        MethodInfo log = panelType.GetMethod("Log", Any);
        foreach (string line in new[]
        {
            "GBIF has 156 records that match.",
            "GBIF finished it in 3 minutes: 156 records from 10 datasets.",
            "Wrote 140 records to C:\\Data\\Wildlife.gdb\\GBIF_occurrences. 65 of them have no coordinates and are rows without a point: look for them in the attribute table, not on the map. Left out 16 outside the true study area.",
            "GBIF's original zip file is kept at C:\\Projects\\Pronghorn\\Data_Downloads\\GBIF_occurrences.zip (83 KB). It holds the 156 records as GBIF supplied them, the citation of every source and their rights statements.",
            "Records per GBIF dataset (dataset key, records):",
            "    50c9509d-22c7-4a22-a47d-8c48425ef4a7\t85",
            "    84aefc7e-f762-11e1-a439-00145eb45e9a\t27",
            "Cite this download as: GBIF.org (13 November 2026) GBIF Occurrence Download https://doi.org/10.15468/dl.gnsmb5",
        })
        {
            log.Invoke(panel, new object[] { line });
        }
        string report = (string)panelType.GetMethod("LogText", Any).Invoke(panel, null);
        var doc = Find<RichTextBox>("LogBox").Document;
        Console.WriteLine("report: " + doc.Blocks.Count + " paragraphs; bulleted " + report.Split('\n').Count(l => l.StartsWith("\u2022")));
        ok &= doc.Blocks.Count == 8 && report.Split('\n').Count(l => l.StartsWith("\u2022")) == 6
            && report.Contains("Data_Downloads") && !report.Contains("\u2022  50c9509d");
        Console.WriteLine("account: " + Find<TextBlock>("AccountText").Text.Replace(Environment.UserName, "<user>"));
        Layout(host);
        Save(host, Path.Combine(outDir, "gbif_tab4_get_the_data.png"));

        // ---- tab 5: my downloads ------------------------------------------------------
        tabs.SelectedIndex = 4;
        Click("OnRefreshDownloads");
        Pump(20, () => !Find<TextBlock>("DownloadsNote").Text.StartsWith("Asking") && Find<TextBlock>("DownloadsNote").Text.Length > 0);
        Console.WriteLine("downloads: " + Find<DataGrid>("DownloadsGrid").Items.Count);
        Layout(host);
        Save(host, Path.Combine(outDir, "gbif_tab5_my_downloads.png"));

        // ---- the sign-in box -----------------------------------------------------------
        Type signIn = asm.GetType("WildlifeTools.OnlineData.Gbif.GbifSignInWindow", true);
        var win = (Window)Activator.CreateInstance(signIn, Any, null, new object[] { "" }, null);
        var content = (FrameworkElement)win.Content;
        foreach (Expander ex in ((Panel)content).Children.OfType<Expander>()) ex.IsExpanded = true;
        win.Content = null;
        var host2 = new Border { Background = Brushes.White, Child = content, Width = 520 };
        host2.Measure(new Size(520, double.PositiveInfinity));
        host2.Arrange(new Rect(host2.DesiredSize));
        host2.UpdateLayout();
        Save(host2, Path.Combine(outDir, "gbif_sign_in.png"));

        // ---- the citation explanation -----------------------------------------------------
        Type citeType = asm.GetType("WildlifeTools.OnlineData.Gbif.GbifCitationWindow", true);
        var cwin = (Window)Activator.CreateInstance(citeType, true);
        var ccontent = (FrameworkElement)cwin.Content;
        cwin.Content = null;
        var host3 = new Border { Background = Brushes.White, Child = ccontent, Width = 720, Height = 1150 };
        Layout(host3);
        Save(host3, Path.Combine(outDir, "gbif_how_to_cite.png"));

        // ---- output path rule -------------------------------------------------------------
        MethodInfo split = panelType.GetMethod("SplitOutput", Any);
        bool Split(string p)
        {
            object[] a = { p, null, null, null };
            bool r = (bool)split.Invoke(null, a);
            Console.WriteLine("   " + p + " -> " + (r ? a[1] + " | " + a[2] : (string)a[3]));
            return r;
        }
        string tmpGdb = Path.Combine(Path.GetTempPath(), "gbif_render_" + Guid.NewGuid().ToString("N").Substring(0, 6) + ".gdb");
        Directory.CreateDirectory(tmpGdb);
        Console.WriteLine("output path rule:");
        ok &= Split(tmpGdb + @"\GBIF_points") && Split(tmpGdb.Replace('\\', '/') + "/GBIF_points/")
            && !Split(tmpGdb) && !Split(@"C:\Data\My.gdb_exports\points") && !Split(tmpGdb + @"\fds\points")
            && !Split(tmpGdb + @"\2bad name") && !Split(@"C:\no\such.gdb\points");
        Directory.Delete(tmpGdb);

        Console.WriteLine(ok ? "PANEL OK" : "PANEL CHECKS FAILED");
        return ok ? 0 : 1;
    }

    /// <summary>What the grid DRAWS, not what the rows hold: no cell may still say "looking up".</summary>
    private static void sources_placeholder_check(DataGrid grid, ref bool ok)
    {
        grid.UpdateLayout();
        int stale = 0, cells = 0;
        foreach (object item in grid.Items)
        {
            if (!(grid.ItemContainerGenerator.ContainerFromItem(item) is DataGridRow row)) continue;
            foreach (TextBlock tb in Descendants<TextBlock>(row))
            {
                cells++;
                if (tb.Text.StartsWith("(looking")) stale++;
            }
        }
        Console.WriteLine("   drawn cells: " + cells + ", still saying 'looking up': " + stale);
        ok &= cells > 0 && stale == 0;
    }

    private static System.Collections.Generic.IEnumerable<T> Descendants<T>(DependencyObject d) where T : DependencyObject
    {
        for (int i = 0; i < VisualTreeHelper.GetChildrenCount(d); i++)
        {
            DependencyObject c = VisualTreeHelper.GetChild(d, i);
            if (c is T t) yield return t;
            foreach (T x in Descendants<T>(c)) yield return x;
        }
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
