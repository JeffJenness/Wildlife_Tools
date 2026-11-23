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

// Draws the iNaturalist Observations panel to PNGs outside ArcGIS Pro, from
// the BUILT add-in DLL, driving it the way a user would: search for a
// species, add it, choose a place, list the species, count. Live requests
// to iNaturalist.org (internet needed); no observations are fetched.
//
//   dotnet build WildlifeTools -c Release        (ArcGIS Pro closed)
//   dotnet run --project claude_tests/INatPanelRender -- <output folder>

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

    private static void Await2(object task)
    {
        var t = (System.Threading.Tasks.Task)task;
        Pump(60, () => t.IsCompleted);
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
        Type panelType = asm.GetType("WildlifeTools.OnlineData.INaturalist.INatPanel", true);
        var panel = (FrameworkElement)Activator.CreateInstance(panelType);
        var host = new Border { Background = Brushes.White, Child = panel, Width = 1080, Height = 800 };
        Layout(host);

        T Find<T>(string name) where T : class => (T)panel.FindName(name);
        void Click(string handler, object sender = null) => panelType.GetMethod(handler, Any)
            .Invoke(panel, new object[] { sender ?? panel, new RoutedEventArgs() });
        string Name(object row)
        {
            object taxon = row.GetType().GetProperty("Taxon").GetValue(row);
            return (string)taxon.GetType().GetProperty("Name", Any)?.GetValue(taxon)
                ?? (string)taxon.GetType().GetField("Name", Any)?.GetValue(taxon);
        }
        var tabs = Find<TabControl>("Tabs");
        bool ok = true;

        // ---- tab 1: a species and a place --------------------------------------
        var taxonGrid = Find<DataGrid>("TaxonGrid");
        var taxonNote = Find<TextBlock>("TaxonNote");
        Find<TextBox>("TaxonBox").Text = "pronghorn";
        Click("OnTaxonSearch");
        Pump(60, () => taxonGrid.Items.Count > 0 || taxonNote.Text.StartsWith("iNaturalist has no"));
        Console.WriteLine("search 'pronghorn': " + taxonGrid.Items.Count + " rows; note: " + taxonNote.Text);
        ok &= taxonGrid.Items.Count >= 1;
        object pick = taxonGrid.Items.Cast<object>().FirstOrDefault(r => Name(r) == "Antilocapra americana");
        ok &= pick != null;
        var chosen = Find<ItemsControl>("ChosenList");
        // two rows selected at once (Shift or Ctrl in the window) are both added by the one Add button
        object family = taxonGrid.Items.Cast<object>().FirstOrDefault(r => Name(r) == "Antilocapridae");
        taxonGrid.SelectedItems.Clear();
        taxonGrid.SelectedItems.Add(pick);
        taxonGrid.SelectedItems.Add(family);
        Click("OnAddSelectedTaxa");
        Console.WriteLine("two selected at once -> chosen: " + chosen.Items.Count + "; status: " + Find<TextBlock>("StatusText").Text);
        ok &= chosen.Items.Count == 2;
        Click("OnTaxonRemove", new Button { DataContext = chosen.Items.Cast<object>().First(r => Name(r) == "Antilocapridae") });
        taxonGrid.SelectedItems.Clear();
        taxonGrid.SelectedItems.Add(pick);
        Console.WriteLine("chosen: " + chosen.Items.Count + "; note: " + Find<TextBlock>("ChosenNote").Text);
        ok &= chosen.Items.Count == 1;

        // browse the taxonomy: kingdoms, then inside Animals; a name picked in the tree is added by the same Add button
        var nameTabs = Find<TabControl>("NameTabs");
        var tree = Find<TreeView>("BrowseTree");
        nameTabs.SelectedIndex = 1;
        Await2(panelType.GetMethod("LoadTaxonomyAsync", Any).Invoke(panel, null));
        Console.WriteLine("kingdoms in the tree: " + tree.Items.Count + "; first: " + ((TreeViewItem)tree.Items[0]).Header);
        ok &= tree.Items.Count >= 6;
        var animals = (TreeViewItem)tree.Items[0];
        animals.IsExpanded = true;
        Pump(60, () => animals.Items.Count > 1);
        Console.WriteLine("inside it: " + animals.Items.Count + "; first: " + ((TreeViewItem)animals.Items[0]).Header);
        ok &= animals.Items.Count > 5;
        var heads = animals.Items.Cast<TreeViewItem>().Select(n => (string)n.Header).ToList();
        ok &= heads.SequenceEqual(heads.OrderBy(h => h.Split(' ')[0], StringComparer.OrdinalIgnoreCase));
        Console.WriteLine("alphabetical: " + heads.SequenceEqual(heads.OrderBy(h => h.Split(' ')[0], StringComparer.OrdinalIgnoreCase)));
        ((TreeViewItem)animals.Items[0]).IsSelected = true;
        Click("OnAddSelectedTaxa");
        Console.WriteLine("added from the tree -> chosen: " + chosen.Items.Count + "; status: " + Find<TextBlock>("StatusText").Text);
        ok &= chosen.Items.Count == 2;
        Layout(host);
        Save(host, Path.Combine(outDir, "inat_tab1_browse.png"));
        Click("OnTaxonRemove", new Button { DataContext = chosen.Items.Cast<object>().First(r => Name(r) != "Antilocapra americana") });
        nameTabs.SelectedIndex = 0;
        var places = Find<ComboBox>("PlaceList");
        var placeNote = Find<TextBlock>("PlaceNote");
        Find<TextBox>("PlaceBox").Text = "Arizona";
        Click("OnPlaceSearch");
        Pump(60, () => places.Items.Count > 1 || placeNote.Text.StartsWith("iNaturalist has no"));
        places.IsDropDownOpen = false;
        Console.WriteLine("place search: " + (places.Items.Count - 1) + " places; note: " + placeNote.Text);
        for (int i = 0; i < places.Items.Count; i++)
        {
            Console.WriteLine("   " + i + ": " + places.Items[i]);
        }
        int az = Enumerable.Range(0, places.Items.Count).FirstOrDefault(i => places.Items[i].ToString().StartsWith("Arizona, US"));
        ok &= az > 0;
        places.SelectedIndex = az;
        Console.WriteLine("place note: " + placeNote.Text);
        Console.WriteLine("area note: " + Find<TextBlock>("AreaNote").Text);
        var count1 = Find<TextBlock>("CountText1");
        Click("OnCount");
        Pump(60, () => Find<TextBlock>("CountText").Text.StartsWith("iNaturalist has"));
        Layout(host);
        Console.WriteLine("tab 1 count: " + count1.Text);
        ok &= count1.Text.StartsWith("iNaturalist has 1,6");
        Save(host, Path.Combine(outDir, "inat_tab1_what_where.png"));

        // ---- tab 2 -----------------------------------------------------------------
        tabs.SelectedIndex = 1;
        Find<CheckBox>("NeedsIdBox").IsChecked = true;
        Click("OnChoiceChanged");
        Layout(host);
        Save(host, Path.Combine(outDir, "inat_tab2_which.png"));
        Find<CheckBox>("NeedsIdBox").IsChecked = false;
        Click("OnChoiceChanged");

        // ---- tab 4: the count --------------------------------------------------
        tabs.SelectedIndex = 3;
        var count = Find<TextBlock>("CountText");
        Click("OnCount");
        Pump(60, () => count.Text.StartsWith("iNaturalist has"));
        Console.WriteLine("request: " + Find<TextBlock>("RequestText").Text);
        ok &= Find<Button>("GetButton").IsEnabled && Find<Button>("GetButton").ToolTip is string;
        Console.WriteLine("count: " + count.Text);
        Console.WriteLine("status: " + Find<TextBlock>("StatusText").Text);
        ok &= count.Text.StartsWith("iNaturalist has 1,6");
        Find<TextBox>("OutputBox").Text = @"C:\Data\Wildlife.gdb\iNaturalist_observations";
        var log = panelType.GetMethod("Log", Any);
        foreach (string line in new[] {
            "Species or group: Antilocapra americana [Pronghorn] (species; iNaturalist taxon id 42429).",
            "Place: Arizona, US (iNaturalist place id 40).",
            "iNaturalist has 1,616 observations that match.",
            "Fetched 1,616 observations in 17 seconds.",
            @"Wrote 1,616 points to C:\Data\Wildlife.gdb\iNaturalist_observations." })
        {
            log?.Invoke(panel, new object[] { line });
        }
        Layout(host);
        Save(host, Path.Combine(outDir, "inat_tab4_get.png"));

        // ---- tab 3: every species in the place, without the pronghorn ----------
        Click("OnTaxonRemove", new Button { DataContext = chosen.Items[0] });
        tabs.SelectedIndex = 2;
        var speciesGrid = Find<DataGrid>("SpeciesGrid");
        var speciesNote = Find<TextBlock>("SpeciesNote");
        Click("OnListSpecies");
        Pump(90, () => speciesGrid.Items.Count > 0);
        Console.WriteLine("species: " + speciesGrid.Items.Count + " rows; note: " + speciesNote.Text
            + "; Show more enabled: " + Find<Button>("SpeciesMoreButton").IsEnabled);
        ok &= speciesGrid.Items.Count > 100;
        // List only: Mammals
        var mammals = Find<WrapPanel>("GroupPanel").Children.OfType<CheckBox>().First(c => (string)c.Content == "Mammals");
        mammals.IsChecked = true;
        Click("OnGroupToggled", mammals);
        Pump(90, () => speciesNote.Text.Contains("Listing only: Mammals"));
        string Prop(object row, string prop) => (string)row.GetType().GetProperty(prop).GetValue(row);
        Console.WriteLine("mammals only: " + speciesGrid.Items.Count + " rows; note: " + speciesNote.Text + "; first: "
            + Prop(speciesGrid.Items[0], "Name") + " / " + Prop(speciesGrid.Items[0], "Group"));
        ok &= speciesGrid.Items.Count > 20 && speciesGrid.Items.Cast<object>().All(r => Prop(r, "Group") == "Mammalia");
        var photoPanel = Find<StackPanel>("PhotoPanel");
        var photoNote = Find<TextBlock>("PhotoNote");
        Click("OnSpeciesPhotosLink", new System.Windows.Documents.Hyperlink { DataContext = speciesGrid.Items[0] });
        Pump(60, () => photoPanel.Children.Count > 0);
        Pump(30, () => photoPanel.Children.Cast<StackPanel>().All(s => ((Image)s.Children[0]).Source != null));
        int pictures = photoPanel.Children.Cast<StackPanel>().Count(s => ((Image)s.Children[0]).Source != null);
        Console.WriteLine("photographs: " + photoPanel.Children.Count + " frames, " + pictures + " pictures loaded; note: " + photoNote.Text
            + "; More enabled: " + Find<Button>("MorePhotosButton").IsEnabled);
        Console.WriteLine("first credit: " + ((TextBlock)((StackPanel)photoPanel.Children[0]).Children[1]).Text.Replace('\n', '|')
            + " ; tip: " + ((StackPanel)photoPanel.Children[0]).ToolTip);
        ok &= photoPanel.Children.Count == 6 && pictures == 6;
        Click("OnMorePhotos");
        Pump(60, () => photoPanel.Children.Count > 6);
        Console.WriteLine("after More photographs: " + photoPanel.Children.Count + " frames");
        ok &= photoPanel.Children.Count == 12;
        Layout(host);
        Save(host, Path.Combine(outDir, "inat_tab3_species.png"));

        // more than one run fetches: every observation in Arizona
        tabs.SelectedIndex = 3;
        string before = count.Text;
        Click("OnCount");
        Pump(60, () => count.Text.StartsWith("iNaturalist has") && count.Text != before);
        Console.WriteLine("count, whole place: " + count.Text);
        var get = Find<Button>("GetButton");
        string tip = ((get.ToolTip as ToolTip)?.Content as TextBlock)?.Text;
        Console.WriteLine("Get enabled: " + get.IsEnabled + "; hover help: " + tip);
        ok &= !get.IsEnabled && tip != null && tip.StartsWith("Greyed out because");
        ok &= count.Text.Contains("this tool fetches in one run");
        Layout(host);
        Save(host, Path.Combine(outDir, "inat_tab4_large.png"));
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
