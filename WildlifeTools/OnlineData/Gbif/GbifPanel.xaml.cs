// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Award notice, marking of modified versions, and
// use of names).
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace WildlifeTools.OnlineData.Gbif
{
    /// <summary>
    /// The content of the GBIF Species Occurrences window. It talks to
    /// GBIF through <see cref="GbifClient"/> (no ArcGIS types) and to
    /// ArcGIS Pro only through <see cref="GbifActions"/>, from clicks, so
    /// the panel itself can be built and drawn outside Pro.
    /// </summary>
    public partial class GbifPanel : UserControl
    {
        public event EventHandler CloseRequested;

        private readonly GbifClient _gbif = new GbifClient();
        private readonly DispatcherTimer _typing;
        private CancellationTokenSource _suggestCts;
        private readonly ObservableCollection<SuggestRow> _suggest = new ObservableCollection<SuggestRow>();
        private CancellationTokenSource _workCts;
        // each list has its own, so re-listing never cancels a fetch that is running
        private CancellationTokenSource _speciesCts, _sourcesCts;
        // the choices each list was made for; a different signature means the list is out of date
        private string _speciesSig, _sourcesSig;
        private bool _quiet;                    // BuildFilterAsync(quiet): no message boxes
        private readonly ObservableCollection<SpeciesRow> _species = new ObservableCollection<SpeciesRow>();
        private readonly ObservableCollection<DownloadRow> _downloads = new ObservableCollection<DownloadRow>();
        private readonly ObservableCollection<SourceRow> _sources = new ObservableCollection<SourceRow>();
        /// <summary>Sources the user unchecked; kept when the list is rebuilt.</summary>
        private readonly Dictionary<string, string> _excludedSources = new Dictionary<string, string>(StringComparer.Ordinal);
        private readonly List<CheckBox> _months = new List<CheckBox>();
        private readonly List<(CheckBox Box, string[] Codes)> _basis = new List<(CheckBox, string[])>();
        // The polygons of the chosen study-area LAYER, kept for a short while (see BuildFilterCoreAsync).
        private List<List<List<(double Lon, double Lat)>>> _areaPolygons;
        private object _areaPolygonsFor;
        private DateTime _areaPolygonsAt;       // when they were read
        private int _areaVersion;               // raised whenever they may have changed
        private const double AreaKeepSeconds = 60;
        private string _photoKey;
        private int _photoOffset;
        private bool _busy;
        private GbifFeatureWriter.Result _lastResult;       // for Save the source list
        private string _lastResultWhat;

        private static readonly string[] Ranks =
            { "Any rank", "Kingdom", "Phylum", "Class", "Order", "Family", "Genus", "Species" };
        private static readonly string[] MonthNames =
            { "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" };

        public GbifPanel()
        {
            InitializeComponent();
            foreach (string r in Ranks)
            {
                RankBox.Items.Add(r);
            }
            RankBox.SelectedIndex = 0;
            for (int i = 0; i < 12; i++)
            {
                var cb = new CheckBox { Content = MonthNames[i], IsChecked = true, Margin = new Thickness(0, 0, 10, 2) };
                _months.Add(cb);
                MonthPanel.Children.Add(cb);
            }
            AddBasis("Observations", true, "HUMAN_OBSERVATION", "OBSERVATION", "MACHINE_OBSERVATION", "OCCURRENCE",
                "A person or an instrument (a camera trap, a recorder, a tag) recorded the organism alive in the field.");
            AddBasis("Museum and herbarium specimens", true, "PRESERVED_SPECIMEN",
                "A preserved specimen in a collection. Older specimens often have approximate coordinates, and a specimen "
                + "can come from a captive animal or a cultivated plant: polar bears that died in zoos are mapped at "
                + "those zoos. Check the locality and basisOfRecord fields of anything far outside a species' range.");
            AddBasis("Material samples", true, "MATERIAL_SAMPLE", "MATERIAL_CITATION",
                "A sample such as tissue, soil or water (including DNA detections), or a specimen cited in a publication.");
            AddBasis("Fossils", false, "FOSSIL_SPECIMEN",
                "Fossil specimens. Left out by default.");
            AddBasis("Zoos and gardens", false, "LIVING_SPECIMEN",
                "Living organisms in captivity or cultivation: zoos, aquaria, botanical gardens. Left out by default.");

            foreach (GbifSpeciesGroup g in GbifSpeciesGroup.All)
            {
                var cb = new CheckBox
                {
                    Content = g.Name,
                    Tag = g,
                    Margin = new Thickness(0, 0, 12, 2),
                    ToolTip = "List only the species of the checked groups. With none checked, every species is listed.",
                };
                cb.Checked += OnGroupToggled;
                cb.Unchecked += OnGroupToggled;
                _groups.Add(cb);
                GroupPanel.Children.Add(cb);
            }
            SpeciesGrid.ItemsSource = _species;
            SuggestGrid.ItemsSource = _suggest;
            DownloadsGrid.ItemsSource = _downloads;
            SourcesGrid.ItemsSource = _sources;
            _typing = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(350) };
            _typing.Tick += async (_, _) =>
            {
                _typing.Stop();
                await SuggestAsync();
            };
            Loaded += OnLoaded;
            Unloaded += (_, _) =>
            {
                _suggestCts?.Cancel();
                _workCts?.Cancel();
                _speciesCts?.Cancel();
                _sourcesCts?.Cancel();
            };
        }

        private void AddBasis(string label, bool on, params string[] codesAndTip)
        {
            string tip = codesAndTip[codesAndTip.Length - 1];
            string[] codes = codesAndTip.Take(codesAndTip.Length - 1).ToArray();
            var cb = new CheckBox { Content = label, IsChecked = on, Margin = new Thickness(0, 0, 12, 2), ToolTip = tip };
            _basis.Add((cb, codes));
            BasisPanel.Children.Add(cb);
        }

        private bool _loadedOnce;
        private GbifSettings _settings = new GbifSettings();
        private bool _restoring;                // true while remembered choices are put back

        private async void OnLoaded(object sender, RoutedEventArgs e)
        {
            if (_loadedOnce)
            {
                return;
            }
            _loadedOnce = true;
            // open with the choices of the last use
            _settings = GbifSettings.Load();
            _restoring = true;
            try
            {
                CommonNameBox.IsChecked = _settings.SearchCommonNames;
            }
            finally
            {
                _restoring = false;
            }
            ShowAccount();
            await RefreshAreasAsync();
            try
            {
                OutBox.Text = await GbifActions.DefaultOutputPathAsync("GBIF_occurrences");
            }
            catch (Exception)
            {
                // outside Pro, or no project: the user types a path
            }
            await LoadCountriesAsync();
            await LoadKingdomsAsync();
        }

        private async Task LoadCountriesAsync()
        {
            CountryBox.Items.Clear();
            CountryBox.Items.Add(new PickItem(null, "(any country)"));
            StateBox.Items.Clear();
            StateBox.Items.Add(new PickItem(null, "(any state or province)"));
            CountryBox.SelectedIndex = 0;
            StateBox.SelectedIndex = 0;
            ShowNoCoordNote();
            try
            {
                foreach (var c in await _gbif.CountriesAsync())
                {
                    CountryBox.Items.Add(new PickItem(c.Code, c.Name));
                }
            }
            catch (Exception)
            {
                // without the list the country filter is simply not offered
            }
        }

        private void OnCountryChanged(object sender, SelectionChangedEventArgs e)
        {
            ShowNoCoordNote();
            StateNote.Text = "";
            // state names belong to the country they were listed for
            if (StateBox.Items.Count > 1)
            {
                object any = StateBox.Items[0];
                StateBox.Items.Clear();
                StateBox.Items.Add(any);
                StateBox.SelectedIndex = 0;
            }
            CountText.Text = "";
        }

        private void OnStateChanged(object sender, SelectionChangedEventArgs e) => CountText.Text = "";

        private async void OnListStates(object sender, RoutedEventArgs e)
        {
            GbifFilter f = await BuildFilterAsync();
            if (f == null)
            {
                return;
            }
            f.StateProvince = null;
            try
            {
                List<GbifFacetCount> list = await _gbif.FacetAsync(f, "STATE_PROVINCE", 300);
                object any = StateBox.Items[0];
                StateBox.Items.Clear();
                StateBox.Items.Add(any);
                foreach (GbifFacetCount c in list.OrderBy(c => c.Name, StringComparer.OrdinalIgnoreCase))
                {
                    StateBox.Items.Add(new PickItem(c.Name, c.Name + "   (" + c.Count.ToString("N0", CultureInfo.CurrentCulture) + ")"));
                }
                StateBox.SelectedIndex = 0;
                StateBox.IsDropDownOpen = list.Count > 0 && StateBox.IsVisible;
                StateNote.Text = list.Count == 0 ? "No matching record names a state or province."
                    : list.Count == 1 ? "The matching records use 1 state or province name. Open the list to choose it."
                    : "The matching records use " + list.Count.ToString("N0", CultureInfo.CurrentCulture)
                      + " state or province names. Publishers spell them in different ways, and each spelling is listed separately. Open the list to choose one.";
            }
            catch (Exception ex)
            {
                StateNote.Text = Short(ex);
            }
        }

        private void OnNoCoordToggled(object sender, RoutedEventArgs e)
        {
            ShowNoCoordNote();
            CountText.Text = "";
        }

        private bool AreaIsWorld =>
            !(AreaBox.SelectedItem is GbifActions.AreaChoice a) || a.Kind == GbifActions.AreaChoice.KindWorld;

        private void ShowNoCoordNote()
        {
            if (IncludeNoCoordBox == null || NoCoordNote == null)
            {
                return;
            }
            bool world = true;
            try
            {
                world = AreaIsWorld;
            }
            catch (Exception)
            {
            }
            bool limited = (CountryBox.SelectedItem as PickItem)?.Value != null;
            NoCoordNote.Text = IncludeNoCoordBox.IsChecked != true ? ""
                : "Records without coordinates are written as rows that have no point: they appear in the attribute table, not on the map."
                  + (world ? "" : " The study area limits only the records that have coordinates.")
                  + (limited ? "" : (world ? " Choose" : " Without a country, records without coordinates come from anywhere in the world: choose")
                      + " a Country, and a State or province if you can, to limit them.");
        }

        // =====================================================================
        //  names
        // =====================================================================
        private void OnNameChanged(object sender, TextChangedEventArgs e)
        {
            NameHint.Visibility = NameBox.Text.Length == 0 ? Visibility.Visible : Visibility.Collapsed;
            if (CommonNameBox.IsChecked == true)
            {
                return;                               // common names search on Enter
            }
            _typing.Stop();
            _typing.Start();
        }

        private async void OnNameKeyDown(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.Enter)
            {
                e.Handled = true;
                _typing.Stop();
                await SuggestAsync();
            }
            else if (e.Key == Key.Down && _suggest.Count > 0)
            {
                SuggestGrid.SelectedIndex = Math.Max(0, SuggestGrid.SelectedIndex);
                SuggestGrid.Focus();
            }
        }

        private async void OnRankChanged(object sender, SelectionChangedEventArgs e)
        {
            if (_loadedOnce && CommonNameBox.IsChecked != true)
            {
                await SuggestAsync();
            }
        }

        private void OnCommonToggled(object sender, RoutedEventArgs e)
        {
            bool common = CommonNameBox.IsChecked == true;
            NameHint.Text = common ? "Type a common name, e.g. steller's jay, then press Enter"
                                   : "Type a name, e.g. Cyanocitta or Corvidae";
            RankBox.IsEnabled = !common;
            _suggestCts?.Cancel();
            _suggest.Clear();
            MatchNote.Text = "";
            if (_loadedOnce && !_restoring && _settings.SearchCommonNames != common)
            {
                _settings.SearchCommonNames = common;       // remembered for the next time the window opens
                _settings.Save();
            }
        }

        private async Task SuggestAsync()
        {
            string typed = NameBox.Text.Trim();
            _suggestCts?.Cancel();
            var cts = _suggestCts = new CancellationTokenSource();
            if (typed.Length < 2)
            {
                _suggest.Clear();
                MatchNote.Text = "";
                return;
            }
            try
            {
                List<GbifTaxon> list;
                string note = "";
                if (CommonNameBox.IsChecked == true)
                {
                    list = await _gbif.SearchCommonNameAsync(typed, 30, cts.Token);
                    note = list.Count == 0 ? "No common name like that in GBIF." : "";
                }
                else
                {
                    string rank = RankBox.SelectedIndex > 0 ? Ranks[RankBox.SelectedIndex].ToUpperInvariant() : null;
                    list = await _gbif.SuggestAsync(typed, rank, 25, cts.Token);
                    if (!list.Any(t => (t.ScientificName ?? "").StartsWith(typed, StringComparison.OrdinalIgnoreCase)))
                    {
                        // no name starts with exactly that: was it a misspelling?
                        GbifNameMatch m = await _gbif.MatchAsync(typed, cts.Token);
                        if (m.Taxon != null && m.MatchType != "NONE" && m.MatchType != "HIGHERRANK")
                        {
                            list.RemoveAll(t => t.Key == m.Taxon.Key);
                            list.Insert(0, m.Taxon);
                            note = "No name starts with that. The closest match is first (" + m.Confidence.ToString(CultureInfo.InvariantCulture) + "% sure).";
                        }
                        else if (list.Count == 0)
                        {
                            note = "GBIF has no name like that.";
                        }
                    }
                }
                if (cts.IsCancellationRequested)
                {
                    return;
                }
                _suggest.Clear();
                foreach (GbifTaxon t in list)
                {
                    _suggest.Add(new SuggestRow(t));
                }
                if (list.Count > 0)
                {
                    SuggestGrid.SelectedIndex = 0;
                }
                FillSuggestions(_suggest.ToList(), cts.Token);
                MatchNote.Text = note;
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                MatchNote.Text = Short(ex);
            }
        }

        /// <summary>Fills the common name, the group and the number of
        /// records of each suggestion, a few rows at a time. Stops when the
        /// suggestions are asked for again.</summary>
        private void FillSuggestions(List<SuggestRow> rows, CancellationToken ct)
        {
            var queue = new Queue<SuggestRow>(rows);
            for (int w = 0; w < 3; w++)
            {
                _ = Task.Run(async () =>
                {
                    while (!ct.IsCancellationRequested)
                    {
                        SuggestRow row;
                        lock (queue)
                        {
                            if (queue.Count == 0)
                            {
                                return;
                            }
                            row = queue.Dequeue();
                        }
                        if (string.IsNullOrEmpty(row.Taxon.Key))
                        {
                            continue;
                        }
                        try
                        {
                            var (common, cls, kingdom, canonical) = await _gbif.TaxonSummaryAsync(row.Taxon.Key, ct).ConfigureAwait(false);
                            await Dispatcher.InvokeAsync(() =>
                            {
                                row.Common = common;
                                row.Canonical = canonical;
                                if (row.Group.Length == 0)
                                {
                                    row.Group = cls ?? kingdom;
                                }
                            });
                        }
                        catch (OperationCanceledException)
                        {
                            return;
                        }
                        catch (Exception)
                        {
                            // the scientific name alone is enough
                        }
                        try
                        {
                            long n = await _gbif.TaxonRecordCountAsync(row.Taxon.Key, ct).ConfigureAwait(false);
                            await Dispatcher.InvokeAsync(() => row.Count = n);
                        }
                        catch (OperationCanceledException)
                        {
                            return;
                        }
                        catch (Exception)
                        {
                            // the count stays blank
                        }
                    }
                });
            }
        }

        private void OnSuggestLink(object sender, RoutedEventArgs e)
        {
            if ((sender as FrameworkContentElement)?.DataContext is SuggestRow row && !string.IsNullOrEmpty(row.Taxon.Key))
            {
                OpenUrl("https://www.gbif.org/species/" + Uri.EscapeDataString(row.Taxon.Key));
            }
        }

        private void OnSuggestDoubleClick(object sender, MouseButtonEventArgs e)
        {
            // a double-click on a row adds it; on a header or the empty part, nothing
            var from = e.OriginalSource as DependencyObject;
            while (from != null && !(from is DataGridRow))
            {
                from = from is System.Windows.Media.Visual || from is System.Windows.Media.Media3D.Visual3D
                    ? System.Windows.Media.VisualTreeHelper.GetParent(from) : LogicalTreeHelper.GetParent(from);
            }
            if ((from as DataGridRow)?.Item is SuggestRow row)
            {
                AddChosen(row.Taxon);
            }
        }

        private void OnAddSuggested(object sender, RoutedEventArgs e) => AddSuggested();

        private void AddSuggested()
        {
            // the tab that is showing decides: every selected suggestion, or the tree's one name
            if (NameTabs.SelectedIndex == 0)
            {
                // in the order of the table, not the order they were clicked
                foreach (SuggestRow row in SuggestGrid.SelectedItems.OfType<SuggestRow>().OrderBy(r => _suggest.IndexOf(r)).ToList())
                {
                    AddChosen(row.Taxon);
                }
            }
            else if (BrowseTree.SelectedItem is TreeViewItem tv && tv.Tag is GbifTaxon t)
            {
                AddChosen(t);
            }
        }

        private void OnTreeDoubleClick(object sender, MouseButtonEventArgs e)
        {
            // only a leaf adds on double-click; on a branch it opens or closes
            if (BrowseTree.SelectedItem is TreeViewItem tv && tv.Tag is GbifTaxon t && !tv.HasItems)
            {
                AddChosen(t);
            }
        }

        private void AddChosen(GbifTaxon t)
        {
            if (string.IsNullOrEmpty(t.Key) || ChosenList.Items.Cast<TaxonItem>().Any(x => x.Taxon.Key == t.Key))
            {
                return;
            }
            ChosenList.Items.Add(new TaxonItem(t));
            ChosenChanged();
        }

        private void OnRemoveChosen(object sender, RoutedEventArgs e)
        {
            foreach (object o in ChosenList.SelectedItems.Cast<object>().ToList())
            {
                ChosenList.Items.Remove(o);
            }
            ChosenChanged();
        }

        private void OnClearChosen(object sender, RoutedEventArgs e)
        {
            ChosenList.Items.Clear();
            ChosenChanged();
        }

        private void ChosenChanged()
        {
            int n = ChosenList.Items.Count;
            ChosenNote.Text = n == 0
                ? "Nothing chosen: every species in the study area."
                : n == 1 ? "1 name chosen." : n.ToString(CultureInfo.CurrentCulture) + " names chosen; records of any of them are returned.";
            CountText.Text = "";
        }

        private async Task LoadKingdomsAsync()
        {
            try
            {
                List<GbifTaxon> kingdoms = await _gbif.ChildrenAsync(null);
                BrowseTree.Items.Clear();
                foreach (GbifTaxon t in kingdoms.OrderBy(k => k.ScientificName, StringComparer.OrdinalIgnoreCase))
                {
                    BrowseTree.Items.Add(MakeNode(t, true));
                }
            }
            catch (Exception)
            {
                BrowseTree.Items.Clear();
                BrowseTree.Items.Add(new TreeViewItem { Header = "(GBIF could not be reached; reopen the window to try again)" });
            }
        }

        private TreeViewItem MakeNode(GbifTaxon t, bool mayHaveChildren)
        {
            var node = new TreeViewItem { Header = new TaxonItem(t).ToString(), Tag = t };
            if (mayHaveChildren)
            {
                node.Items.Add(new TreeViewItem { Header = "Loading..." });
                node.Expanded += OnNodeExpanded;
            }
            return node;
        }

        private async void OnNodeExpanded(object sender, RoutedEventArgs e)
        {
            var node = (TreeViewItem)sender;
            e.Handled = true;
            if (!(node.Tag is GbifTaxon t) || node.Items.Count != 1
                || !(node.Items[0] is TreeViewItem first) || first.Tag != null)
            {
                return;                               // already loaded
            }
            try
            {
                List<GbifTaxon> kids = await _gbif.ChildrenAsync(t.Key, 2000);
                node.Items.Clear();
                foreach (GbifTaxon k in kids.Where(k => k.Status == null || k.Status == "ACCEPTED" || k.Status == "DOUBTFUL")
                                            .OrderBy(k => k.ScientificName, StringComparer.OrdinalIgnoreCase))
                {
                    bool leaf = k.ChildCount == 0 || k.Rank == "SUBSPECIES" || k.Rank == "VARIETY" || k.Rank == "FORM";
                    node.Items.Add(MakeNode(k, !leaf));
                }
                if (node.Items.Count == 0)
                {
                    node.Items.Add(new TreeViewItem { Header = "(nothing inside)", Tag = "empty", IsEnabled = false });
                }
            }
            catch (Exception ex)
            {
                first.Header = Short(ex);
            }
        }

        // =====================================================================
        //  study area and filter
        // =====================================================================
        private async void OnRefreshAreas(object sender, RoutedEventArgs e) => await RefreshAreasAsync();

        private async Task RefreshAreasAsync()
        {
            object keep = (AreaBox.SelectedItem as GbifActions.AreaChoice)?.Layer;
            int keepKind = (AreaBox.SelectedItem as GbifActions.AreaChoice)?.Kind ?? -1;
            List<GbifActions.AreaChoice> list;
            try
            {
                list = await GbifActions.ListAreasAsync();
            }
            catch (Exception)
            {
                list = new List<GbifActions.AreaChoice> { GbifActions.AreaChoice.World() };
            }
            AreaBox.Items.Clear();
            foreach (GbifActions.AreaChoice a in list)
            {
                AreaBox.Items.Add(a);
            }
            int pick = list.FindIndex(a => a.Kind == keepKind && ReferenceEquals(a.Layer, keep));
            if (pick < 0)
            {
                pick = list.FindIndex(a => a.IsSelectedInContents);
            }
            if (pick < 0)
            {
                pick = list.FindIndex(a => a.Kind == GbifActions.AreaChoice.KindExtent);
            }
            AreaBox.SelectedIndex = pick < 0 ? 0 : pick;
            InvalidateArea();
        }

        /// <summary>Forgets the study area's polygons: they are read again
        /// the next time they are needed. On the window's thread.</summary>
        private void InvalidateArea()
        {
            _areaPolygons = null;
            _areaVersion++;
        }

        /// <summary>Called by the window when something happened that can
        /// change the shape of a study-area layer without changing the
        /// list of layers: an edit, or a layer's properties (its
        /// definition query, its source). Nothing is asked of GBIF: the
        /// polygons are only read again the next time they are needed.</summary>
        internal void AreaMayHaveChanged()
        {
            Dispatcher.BeginInvoke(new Action(InvalidateArea));
        }

        private DispatcherTimer _mapTimer;

        /// <summary>Called by the window when the map changed: a layer was
        /// added or removed, another map became active, or the selection
        /// changed. The study-area list and its notes (which say how many
        /// polygons are selected) are read again, shortly after the last
        /// change, keeping the chosen study area.</summary>
        internal void MapChanged()
        {
            Dispatcher.BeginInvoke(new Action(() =>
            {
                InvalidateArea();                       // at once: the list itself is read again a moment later
                if (_mapTimer == null)
                {
                    _mapTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(400) };
                    _mapTimer.Tick += async (_, _) =>
                    {
                        _mapTimer.Stop();
                        if (AreaBox.IsDropDownOpen)
                        {
                            _mapTimer.Start();          // not while the list is being read
                            return;
                        }
                        try
                        {
                            await RefreshAreasAsync();
                            await RefreshOpenTabAsync();
                        }
                        catch (Exception)
                        {
                        }
                    };
                }
                _mapTimer.Stop();
                _mapTimer.Start();
            }));
        }

        private void OnAreaChanged(object sender, SelectionChangedEventArgs e)
        {
            AreaNote.Text = (AreaBox.SelectedItem as GbifActions.AreaChoice)?.Note ?? "";
            ShowNoCoordNote();
            InvalidateArea();
            CountText.Text = "";
        }

        /// <summary>Reads the tabs into a filter. Returns null (after telling
        /// the user why) when something typed cannot be used.</summary>
        private async Task<GbifFilter> BuildFilterAsync(bool quiet = false)
        {
            _quiet = quiet;
            try
            {
                return await BuildFilterCoreAsync();
            }
            finally
            {
                _quiet = false;
            }
        }

        private async Task<GbifFilter> BuildFilterCoreAsync()
        {
            var f = new GbifFilter();
            foreach (TaxonItem t in ChosenList.Items)
            {
                f.TaxonKeys.Add(t.Taxon.Key);
            }
            if (!TryYear(YearFromBox, out int? y0) || !TryYear(YearToBox, out int? y1))
            {
                Say("The years must be whole numbers such as 1990, or empty.");
                return null;
            }
            if (y0.HasValue && y1.HasValue && y0 > y1)
            {
                Say("The first year is later than the last year.");
                return null;
            }
            f.YearFrom = y0;
            f.YearTo = y1;
            for (int i = 0; i < 12; i++)
            {
                if (_months[i].IsChecked == true)
                {
                    f.Months.Add(i + 1);
                }
            }
            if (f.Months.Count == 0)
            {
                Say("Check at least one month.");
                return null;
            }
            if (_basis.All(b => b.Box.IsChecked != true))
            {
                Say("Check at least one kind of record.");
                return null;
            }
            if (_basis.Any(b => b.Box.IsChecked != true))
            {
                foreach (var b in _basis.Where(b => b.Box.IsChecked == true))
                {
                    f.BasisOfRecord.AddRange(b.Codes);
                }
            }
            f.ExcludedDatasetKeys.AddRange(_excludedSources.Keys);
            f.ExcludeGeospatialIssues = NoIssuesBox.IsChecked == true;
            f.ExcludeCentroids = NoCentroidBox.IsChecked == true;
            f.PresentOnly = PresentBox.IsChecked == true;
            f.WithImagesOnly = ImagesBox.IsChecked == true;
            if (NoNcBox.IsChecked == true)
            {
                f.Licenses.Add("CC0_1_0");
                f.Licenses.Add("CC_BY_4_0");
            }
            string u = UncertBox.Text.Trim();
            if (u.Length > 0)
            {
                if (!double.TryParse(u, NumberStyles.Float, CultureInfo.CurrentCulture, out double meters) || meters <= 0)
                {
                    Say("The coordinate uncertainty must be a number of meters greater than zero, or empty.");
                    return null;
                }
                f.MaxCoordinateUncertaintyMeters = meters;
            }

            var area = AreaBox.SelectedItem as GbifActions.AreaChoice;
            if (area != null && area.Kind != GbifActions.AreaChoice.KindWorld)
            {
                // A stale shape gives wrong counts, so the study area is read from the map whenever it may
                // have changed. The map's extent changes as the user pans and is cheap to read: it is read
                // every time. A LAYER's polygons have to be read, straightened, projected and dissolved,
                // which takes a while for a large layer, and Count, List species, every group toggle, List
                // sources and every page of photographs ask for them: those are kept, until the map's
                // layers or selection change, the layer is edited or its properties change (the window
                // tells this panel: MapChanged and AreaMayHaveChanged), another area is chosen, or a minute
                // has passed, whichever comes first.
                List<List<List<(double Lon, double Lat)>>> polys = null;
                if (area.Kind == GbifActions.AreaChoice.KindLayer && _areaPolygons != null
                    && ReferenceEquals(_areaPolygonsFor, area)
                    && (DateTime.UtcNow - _areaPolygonsAt).TotalSeconds < AreaKeepSeconds)
                {
                    polys = _areaPolygons;
                }
                if (polys == null)
                {
                    int version = _areaVersion;
                    try
                    {
                        polys = await GbifActions.AreaPolygonsAsync(area);
                    }
                    catch (Exception ex)
                    {
                        Say("Could not read the study area: " + Short(ex));
                        return null;
                    }
                    if (version == _areaVersion)        // nothing changed while it was being read
                    {
                        _areaPolygons = polys;
                        _areaPolygonsFor = area;
                        _areaPolygonsAt = DateTime.UtcNow;
                    }
                }
                if (polys.Count == 0)
                {
                    Say("The study area has no polygons to use.");
                    return null;
                }
                f.Polygons.AddRange(polys);
            }
            f.Country = (CountryBox.SelectedItem as PickItem)?.Value;
            f.StateProvince = (StateBox.SelectedItem as PickItem)?.Value;
            f.IncludeNoCoordinates = IncludeNoCoordBox.IsChecked == true;
            if (f.TaxonKeys.Count == 0 && f.Polygons.Count == 0 && f.Country == null)
            {
                Say("Choose at least one species or group, a study area, or a country. With none of them, the request is every record GBIF holds.");
                return null;
            }
            return f;
        }

        private static bool TryYear(TextBox box, out int? year)
        {
            year = null;
            string s = box.Text.Trim();
            if (s.Length == 0)
            {
                return true;
            }
            if (int.TryParse(s, NumberStyles.Integer, CultureInfo.CurrentCulture, out int y) && y >= 1500 && y <= 2200)
            {
                year = y;
                return true;
            }
            return false;
        }

        private async void OnCount(object sender, RoutedEventArgs e)
        {
            GbifFilter f = await BuildFilterAsync();
            if (f == null)
            {
                return;
            }
            CountText.Text = "Asking GBIF...";
            try
            {
                long n = await _gbif.CountAsync(f);
                CountText.Text = CountSentence(n) + " " + Advice(n, f);
                RouteNote.Text = RouteAdvice(n, f);
                _routeSig = Signature(f);
                if (n > GbifClient.RecommendDownloadAbove)
                {
                    RouteDownload.IsChecked = true;
                }
                else
                {
                    RouteSearch.IsChecked = true;
                }
            }
            catch (Exception ex)
            {
                CountText.Text = Short(ex);
            }
        }

        private string _routeSig;               // the choices the note on tab 4 was counted for
        private CancellationTokenSource _routeCts;

        private static string RouteAdvice(long n, GbifFilter f) =>
            CountSentence(n) + " " + Advice(n, f).Replace(" on tab 4", "").Replace(", which tab 4 recommends", "");

        /// <summary>Counts again for the note under "How to get the records"
        /// when the choices are not the ones it was counted for.</summary>
        private async Task RefreshRouteNoteAsync(GbifFilter f)
        {
            string sig = Signature(f);
            if (sig == _routeSig)
            {
                return;
            }
            _routeCts?.Cancel();
            var cts = _routeCts = new CancellationTokenSource();
            RouteNote.Text = "Counting the records that match the choices on tabs 1 to 3...";
            try
            {
                long n = await _gbif.CountAsync(f, cts.Token);
                if (!cts.IsCancellationRequested)
                {
                    RouteNote.Text = RouteAdvice(n, f);
                    _routeSig = sig;
                }
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                RouteNote.Text = Short(ex);
                _routeSig = null;
            }
        }

        private static string CountSentence(long n) =>
            n == 0 ? "GBIF has no records that match."
            : n == 1 ? "GBIF has 1 record that matches."
            : "GBIF has " + n.ToString("N0", CultureInfo.CurrentCulture) + " records that match.";

        private static string Advice(long n, GbifFilter f)
        {
            string s;
            if (n == 0)
            {
                return "Widen the study area, the dates or the kinds of record.";
            }
            if (n > GbifClient.MaxSearchRecords)
            {
                s = "That is more than the 100,000 that can be fetched directly, so request a GBIF download on tab 4.";
            }
            else if (n > GbifClient.RecommendDownloadAbove)
            {
                s = "GBIF asks that requests of more than about 20,000 records go through a download, which tab 4 recommends; fetching directly will be slow.";
            }
            else
            {
                s = "Fetching them directly on tab 4 will be quick. A GBIF download is the better choice if you need a DOI to cite.";
            }
            if (!f.PolygonFitsDownload)
            {
                s += " Separately, and whatever the number of records: the study area polygon is very detailed ("
                    + f.PolygonPointCount().ToString("N0", CultureInfo.CurrentCulture)
                    + " vertices in its outline), and GBIF's download service accepts an outline of at most 10,000 vertices. For a download the tool would therefore simplify the study area first (to its convex hull), download the records inside that simpler shape, and then clip them to the true study area. Fetching directly is not affected.";
            }
            return s;
        }

        // =====================================================================
        //  species list and photographs
        // =====================================================================
        private async void OnListSpecies(object sender, RoutedEventArgs e)
        {
            GbifFilter f = await BuildFilterAsync();
            if (f != null)
            {
                await ListSpeciesAsync(f);
            }
        }

        private static string Signature(GbifFilter f) => f.ToPredicateJson() ?? "";

        private readonly List<CheckBox> _groups = new List<CheckBox>();

        /// <summary>The filter the species list is asked for: the choices
        /// of tab 1, narrowed to the groups checked on tab 2 (if any).</summary>
        private GbifFilter ForSpeciesList(GbifFilter f)
        {
            List<GbifSpeciesGroup> on = _groups.Where(c => c.IsChecked == true).Select(c => (GbifSpeciesGroup)c.Tag).ToList();
            if (on.Count == 0)
            {
                return f;
            }
            GbifFilter g = f.Clone();
            g.Groups.AddRange(on);
            return g;
        }

        /// <summary>A group was checked or unchecked: list again, quietly.</summary>
        private async void OnGroupToggled(object sender, RoutedEventArgs e)
        {
            if (!_loadedOnce)
            {
                return;
            }
            try
            {
                GbifFilter f = await BuildFilterAsync(true);
                if (f != null)
                {
                    await ListSpeciesAsync(f);
                }
            }
            catch (Exception)
            {
            }
        }

        private async Task ListSpeciesAsync(GbifFilter f)
        {
            f = ForSpeciesList(f);
            _speciesSig = Signature(f);
            SpeciesNote.Text = "Asking GBIF...";
            _species.Clear();
            PhotoPanel.Children.Clear();
            MorePhotosButton.IsEnabled = false;
            _photoKey = null;
            PhotoNote.Text = "Click \"show\" beside a species to see photographs of it here.";
            _speciesCts?.Cancel();
            var cts = _speciesCts = new CancellationTokenSource();
            try
            {
                const int Max = 500;
                List<GbifFacetCount> list = await _gbif.FacetAsync(f, "SPECIES_KEY", Max, 0, cts.Token);
                if (cts.IsCancellationRequested || !ReferenceEquals(cts, _speciesCts))
                {
                    return;         // a newer request owns the list now: this answer must not add rows to it
                }
                foreach (GbifFacetCount c in list)
                {
                    _species.Add(new SpeciesRow { Key = c.Name, Count = c.Count, Name = "(looking up the name...)" });
                }
                SpeciesNote.Text = list.Count == 0 ? "No species have matching records."
                    : (list.Count >= Max ? "The " + Max + " species with the most records (there are more)."
                                         : list.Count.ToString("N0", CultureInfo.CurrentCulture) + (list.Count == 1 ? " species" : " species") + ", most records first.")
                      + " Records identified only to a genus or higher are not listed."
                      + (f.Groups.Count > 0 ? " Listing only: " + string.Join(", ", f.Groups.Select(g => g.Name)) + "." : "");
                // name them, a few at a time, most records first
                var queue = new Queue<SpeciesRow>(_species);
                var workers = new List<Task>();
                for (int w = 0; w < 4; w++)
                {
                    workers.Add(Task.Run(async () =>
                    {
                        while (!cts.IsCancellationRequested)
                        {
                            SpeciesRow row;
                            lock (queue)
                            {
                                if (queue.Count == 0)
                                {
                                    return;
                                }
                                row = queue.Dequeue();
                            }
                            try
                            {
                                GbifTaxon t = await _gbif.TaxonAsync(row.Key, cts.Token).ConfigureAwait(false);
                                await Dispatcher.InvokeAsync(() =>
                                {
                                    row.Taxon = t;
                                    row.Name = t.ScientificName ?? row.Key;
                                });
                                try
                                {
                                    // GBIF's common name, where it has one, shown in brackets
                                    string common = await _gbif.CommonNameAsync(row.Key, cts.Token).ConfigureAwait(false);
                                    if (common != null)
                                    {
                                        await Dispatcher.InvokeAsync(() => row.Common = common);
                                    }
                                }
                                catch (OperationCanceledException)
                                {
                                    return;
                                }
                                catch (Exception)
                                {
                                    // the scientific name alone is enough
                                }
                            }
                            catch (OperationCanceledException)
                            {
                                return;
                            }
                            catch (Exception)
                            {
                                await Dispatcher.InvokeAsync(() => row.Name = "(GBIF key " + row.Key + ")");
                            }
                        }
                    }));
                }
                await Task.WhenAll(workers);
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                if (ReferenceEquals(cts, _speciesCts))
                {
                    SpeciesNote.Text = Short(ex);
                }
            }
        }

        private void OnAddCheckedSpecies(object sender, RoutedEventArgs e)
        {
            List<SpeciesRow> picked = _species.Where(r => r.Checked).ToList();
            if (picked.Count == 0)
            {
                SpeciesNote.Text = "Check one or more species first.";
                return;
            }
            // REPLACE the chosen list: adding a species to a list that already holds its
            // genus or family would not narrow anything.
            ChosenList.Items.Clear();
            foreach (SpeciesRow r in picked)
            {
                AddChosen(r.Taxon ?? new GbifTaxon { Key = r.Key, ScientificName = r.Name, Rank = "SPECIES" });
            }
            ChosenChanged();
            // this list was made for the wider choice and still shows it; keep it, with a note
            _speciesSig = null;
            SpeciesNote.Text = "The Chosen list on tab 1 now holds only the " + picked.Count
                + (picked.Count == 1 ? " checked species" : " checked species")
                + ". Counts, sources and the records you get are limited to "
                + (picked.Count == 1 ? "it" : "them") + ". Click List again to see the list for that choice.";
        }

        private async void OnSpeciesPhotosLink(object sender, RoutedEventArgs e)
        {
            // the row the link sits in, whatever row is highlighted or checked
            if ((sender as FrameworkContentElement)?.DataContext is SpeciesRow row)
            {
                SpeciesGrid.SelectedItem = row;
                await ShowPhotosForAsync(row);
            }
        }

        private async Task ShowPhotosForAsync(SpeciesRow row)
        {
            _photoKey = row.Key;
            _photoOffset = 0;
            PhotoPanel.Children.Clear();
            await LoadPhotosAsync(row.Name);
        }

        private async void OnMorePhotos(object sender, RoutedEventArgs e) => await LoadPhotosAsync(null);

        private async Task LoadPhotosAsync(string name)
        {
            const int Batch = 6;
            GbifFilter f = await BuildFilterAsync();
            if (f == null || _photoKey == null)
            {
                return;
            }
            f.TaxonKeys.Clear();
            f.TaxonKeys.Add(_photoKey);
            f.WithImagesOnly = true;
            PhotoNote.Text = "Asking GBIF...";
            try
            {
                GbifSearchPage page = await _gbif.SearchPageAsync(f, Batch, _photoOffset);
                _photoOffset += page.Results.Count;
                MorePhotosButton.IsEnabled = !page.EndOfRecords && _photoOffset < page.Count;
                PhotoNote.Text = page.Count == 0
                    ? "No matching records of this species have a photograph."
                    : (name ?? "") + (name == null ? "" : ": ") + page.Count.ToString("N0", CultureInfo.CurrentCulture)
                      + " matching records have photographs; showing " + _photoOffset.ToString("N0", CultureInfo.CurrentCulture)
                      + ". Click a picture to open its record on GBIF.org.";
                foreach (GbifOccurrence o in page.Results)
                {
                    if (o.Media.Count == 0)
                    {
                        continue;
                    }
                    GbifMedia m = o.Media[0];
                    var img = new Image { Width = 180, Height = 135, Stretch = System.Windows.Media.Stretch.Uniform };
                    string who = m.Creator ?? m.RightsHolder ?? "unknown photographer";
                    string lic = LicenseShort(m.License);
                    var credit = new TextBlock
                    {
                        Text = who + "\n" + lic,
                        TextWrapping = TextWrapping.Wrap,
                        Width = 180,
                        FontSize = 11,
                        Opacity = 0.85,
                        TextTrimming = TextTrimming.CharacterEllipsis,
                        MaxHeight = 32,
                    };
                    var stack = new StackPanel { Margin = new Thickness(0, 0, 10, 0), Cursor = Cursors.Hand };
                    stack.Children.Add(img);
                    stack.Children.Add(credit);
                    long key = o.Key;
                    stack.ToolTip = "Photograph by " + who + ". License: " + lic + ". Click to open the record on GBIF.org.";
                    stack.MouseLeftButtonUp += (_, _) => OpenUrl("https://www.gbif.org/occurrence/" + key.ToString(CultureInfo.InvariantCulture));
                    PhotoPanel.Children.Add(stack);
                    _ = LoadThumbAsync(img, GbifClient.ThumbnailUrl(o.Key, m.Url, 360));
                }
            }
            catch (Exception ex)
            {
                PhotoNote.Text = Short(ex);
            }
        }

        private async Task LoadThumbAsync(Image img, string url)
        {
            try
            {
                byte[] bytes = await _gbif.GetBytesAsync(url);
                var bmp = new BitmapImage();
                bmp.BeginInit();
                bmp.CacheOption = BitmapCacheOption.OnLoad;
                bmp.StreamSource = new MemoryStream(bytes);
                bmp.EndInit();
                bmp.Freeze();
                img.Source = bmp;
            }
            catch (Exception)
            {
                // a missing thumbnail leaves an empty frame; the credit still shows
            }
        }

        internal static string LicenseShort(string license)
        {
            string s = (license ?? "").ToLowerInvariant();
            if (s.Length == 0) return "license not stated";
            if (s.Contains("zero") || s.Contains("cc0")) return "CC0 (no restrictions)";
            if (s.Contains("by-nc-nd") || s.Contains("by_nc_nd")) return "CC BY-NC-ND";
            if (s.Contains("by-nc-sa") || s.Contains("by_nc_sa")) return "CC BY-NC-SA";
            if (s.Contains("by-nc") || s.Contains("by_nc")) return "CC BY-NC (credit, non-commercial)";
            if (s.Contains("by-sa") || s.Contains("by_sa")) return "CC BY-SA";
            if (s.Contains("by-nd") || s.Contains("by_nd")) return "CC BY-ND";
            if (s.Contains("/by/") || s.Contains("cc_by") || s.Contains("cc-by") || s == "cc by") return "CC BY (credit required)";
            return license;
        }

        // =====================================================================
        //  sources (GBIF datasets)
        // =====================================================================
        private async void OnListSources(object sender, RoutedEventArgs e)
        {
            GbifFilter f = await BuildFilterAsync();
            if (f != null)
            {
                await ListSourcesAsync(f);
            }
        }

        /// <summary>The sources list ignores which sources are unchecked: it lists them all.</summary>
        private static string SourcesSignature(GbifFilter f)
        {
            GbifFilter g = f.Clone();
            g.ExcludedDatasetKeys.Clear();
            return Signature(g);
        }

        private async Task ListSourcesAsync(GbifFilter f)
        {
            f.ExcludedDatasetKeys.Clear();          // list every source, unchecked ones included
            _sourcesSig = Signature(f);
            SourcesNote.Text = "Asking GBIF...";
            _sources.Clear();
            _sourcesCts?.Cancel();
            var cts = _sourcesCts = new CancellationTokenSource();
            try
            {
                const int Max = 300;
                List<GbifFacetCount> list = await _gbif.FacetAsync(f, "DATASET_KEY", Max, 0, cts.Token);
                if (cts.IsCancellationRequested || !ReferenceEquals(cts, _sourcesCts))
                {
                    return;         // a newer request owns the list now: this answer must not add rows to it
                }
                foreach (GbifFacetCount c in list)
                {
                    var row = new SourceRow
                    {
                        Key = c.Name,
                        Count = c.Count,
                        Name = "(looking up the name...)",
                        Checked = !_excludedSources.ContainsKey(c.Name),
                    };
                    row.PropertyChanged += OnSourceRowChanged;
                    _sources.Add(row);
                }
                SourcesNote.Text = list.Count == 0 ? "No sources have matching records."
                    : (list.Count >= Max ? "The " + Max + " sources with the most records (there are more)."
                                         : list.Count.ToString("N0", CultureInfo.CurrentCulture) + (list.Count == 1 ? " source" : " sources") + ", most records first.");
                ShowExcluded();
                var queue = new Queue<SourceRow>(_sources);
                var workers = new List<Task>();
                for (int w = 0; w < 4; w++)
                {
                    workers.Add(Task.Run(async () =>
                    {
                        while (!cts.IsCancellationRequested)
                        {
                            SourceRow row;
                            lock (queue)
                            {
                                if (queue.Count == 0)
                                {
                                    return;
                                }
                                row = queue.Dequeue();
                            }
                            try
                            {
                                var info = await _gbif.DatasetInfoAsync(row.Key, cts.Token).ConfigureAwait(false);
                                await Dispatcher.InvokeAsync(() =>
                                {
                                    row.License = LicenseShort(info.License);
                                    row.Name = info.Title ?? row.Key;
                                });
                            }
                            catch (OperationCanceledException)
                            {
                                return;
                            }
                            catch (Exception)
                            {
                                await Dispatcher.InvokeAsync(() => row.Name = "(GBIF dataset " + row.Key + ")");
                            }
                        }
                    }));
                }
                await Task.WhenAll(workers);
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                if (ReferenceEquals(cts, _sourcesCts))
                {
                    SourcesNote.Text = Short(ex);
                }
            }
        }

        private void OnSourceRowChanged(object sender, PropertyChangedEventArgs e)
        {
            var row = (SourceRow)sender;
            string what = e.PropertyName;
            if (what != nameof(SourceRow.Checked) && what != nameof(SourceRow.Name))
            {
                return;
            }
            // names arrive on worker threads: keep the list of left-out sources on the UI thread
            Dispatcher.BeginInvoke(new Action(() =>
            {
                if (row.Checked)
                {
                    _excludedSources.Remove(row.Key);
                }
                else
                {
                    _excludedSources[row.Key] = row.Name;
                }
                if (what == nameof(SourceRow.Checked))
                {
                    ShowExcluded();
                    CountText.Text = "";
                }
            }));
        }

        private void ShowExcluded()
        {
            int n = _excludedSources.Count;
            ExcludedNote.Text = n == 0 ? "Every source is included."
                : n == 1 ? "1 source is left out of everything this window asks GBIF for."
                : n.ToString("N0", CultureInfo.CurrentCulture) + " sources are left out of everything this window asks GBIF for.";
        }

        private void OnCheckSources(object sender, RoutedEventArgs e)
        {
            foreach (SourceRow r in _sources)
            {
                r.Checked = true;
            }
            // after the rows' own updates: also drop sources unchecked earlier and no longer listed
            Dispatcher.BeginInvoke(new Action(() =>
            {
                _excludedSources.Clear();
                ShowExcluded();
            }));
        }

        private void OnUncheckSources(object sender, RoutedEventArgs e)
        {
            foreach (SourceRow r in _sources)
            {
                r.Checked = false;
            }
        }

        private void OnSourceLink(object sender, RoutedEventArgs e)
        {
            // the row the link sits in, whatever row is highlighted or checked
            if ((sender as FrameworkContentElement)?.DataContext is SourceRow row)
            {
                OpenUrl(SourceUrl(row));
            }
        }

        internal static string SourceUrl(SourceRow row) => "https://www.gbif.org/dataset/" + row.Key;

        // =====================================================================
        //  account
        // =====================================================================
        private void ShowAccount()
        {
            bool has = false;
            try
            {
                has = _gbif.HasAccount || _gbif.LoadStoredAccount();
            }
            catch (Exception)
            {
            }
            bool stored = false;
            try
            {
                stored = has && CredentialStore.UserName(GbifClient.Service) == _gbif.UserName;
            }
            catch (Exception)
            {
            }
            AccountText.Text = has
                ? "Signed in as " + _gbif.UserName + (stored ? " (kept in Windows Credential Manager)." : " (for this session only).")
                : "Not signed in. A GBIF account is free at gbif.org.";
            // one button signs in or, when someone is signed in, switches to another account
            SignInButton.Content = has ? "Switch account..." : "Sign in...";
            SignOutButton.IsEnabled = has;
        }

        private async void OnSignIn(object sender, RoutedEventArgs e)
        {
            var dlg = new GbifSignInWindow(_gbif.UserName) { Owner = Window.GetWindow(this) };
            if (dlg.ShowDialog() != true)
            {
                return;
            }
            var probe = new GbifClient();
            probe.SetAccount(dlg.UserName, dlg.Password);
            try
            {
                AccountText.Text = "Checking with GBIF...";
                if (!await probe.CheckAccountAsync())
                {
                    Say("GBIF did not accept that user name and password. Use your GBIF user name, not your email address.");
                    ShowAccount();
                    return;
                }
                _gbif.SetAccount(dlg.UserName, dlg.Password);
                if (dlg.Remember)
                {
                    try
                    {
                        CredentialStore.Save(GbifClient.Service, dlg.UserName, dlg.Password);
                    }
                    catch (Exception ex)
                    {
                        Say("You are signed in for this session, but Windows would not store the sign-in ("
                            + ex.Message + "). You will be asked again next time.");
                    }
                }
            }
            catch (Exception ex)
            {
                Say("Could not check the account: " + Short(ex));
            }
            finally
            {
                probe.Dispose();
            }
            ShowAccount();
        }

        private void OnForgetAccount(object sender, RoutedEventArgs e)
        {
            try
            {
                CredentialStore.Delete(GbifClient.Service);
            }
            catch (Exception ex)
            {
                Say("Could not remove the stored sign-in: " + Short(ex));
            }
            _gbif.SetAccount(null, null);
            _downloads.Clear();
            ShowAccount();
        }

        // =====================================================================
        //  getting the records
        // =====================================================================
        private async void OnGet(object sender, RoutedEventArgs e)
        {
            if (_busy)
            {
                return;
            }
            if (!SplitOutput(OutBox.Text, out string gdb, out string name, out string why))
            {
                Say(why);
                return;
            }
            // Busy from here, BEFORE the first await. Reading a polygon study area takes a moment, and a
            // second click on Get in that moment would otherwise pass the test above too and start a second
            // run: two tool runs at once, Cancel reaching only one of them, and, for a GBIF download, two
            // downloads requested on the user's account.
            SetBusy(true);
            var cts = _workCts = new CancellationTokenSource();
            try
            {
                GbifFilter f = await BuildFilterAsync();
                if (f == null || cts.IsCancellationRequested)
                {
                    return;
                }
                bool download = RouteDownload.IsChecked == true;
                if (download && !_gbif.HasAccount)
                {
                    Say("A GBIF download needs your GBIF account. Click Sign in first.");
                    return;
                }
                if (download && !SignInIsStored())
                {
                    Say("To request a GBIF download, the sign-in must be kept in Windows Credential Manager: the "
                        + "geoprocessing tool that makes the request reads it from there, so that the password is never "
                        + "one of its parameters. Click Sign in and leave \"Remember\" checked.");
                    return;
                }
                GbifActions.ToolRequest q = BuildRequest(f, download ? GbifActions.MethodDownload : GbifActions.MethodFetch,
                    gdb, name);
                ClearLog();
                // counted here first, whichever way the records are got, so that a request with
                // nothing to get (or too much to fetch) stops before a download is requested from
                // GBIF or an existing feature class is replaced
                Step(0, "Counting...");
                long total = await _gbif.CountAsync(f, cts.Token);
                RouteNote.Text = RouteAdvice(total, f);
                _routeSig = Signature(f);
                if (total == 0)
                {
                    Log(CountSentence(total) + (download ? " No download was requested from GBIF." : "")
                        + " Nothing was written.");
                    Step(0, download ? "Nothing to download." : "Nothing to fetch.");
                    return;
                }
                if (!download && total > GbifClient.MaxSearchRecords)
                {
                    throw new InvalidOperationException("There are " + total.ToString("N0", CultureInfo.CurrentCulture)
                        + " records, more than the 100,000 that can be fetched directly. Choose the GBIF download instead.");
                }
                await RunToolAsync(q, gdb, name, cts.Token);
            }
            catch (OperationCanceledException)
            {
                Log("Cancelled.");
                ProgressText.Text = "Cancelled.";
            }
            catch (Exception ex)
            {
                Log("Stopped: " + Short(ex));
                ProgressText.Text = "Stopped: " + Short(ex);
            }
            finally
            {
                SetBusy(false);
            }
        }

        private void OnCancel(object sender, RoutedEventArgs e) => _workCts?.Cancel();

        private void SetBusy(bool busy)
        {
            _busy = busy;
            GetButton.IsEnabled = !busy;
            CancelButton.IsEnabled = busy;
            if (!busy)
            {
                Progress.Value = 0;
            }
        }

        /// <summary>True when the account in use is the one kept in Windows
        /// Credential Manager, which is where the geoprocessing tool reads it.</summary>
        private bool SignInIsStored()
        {
            try
            {
                return _gbif.HasAccount && CredentialStore.UserName(GbifClient.Service) == _gbif.UserName;
            }
            catch (Exception)
            {
                return false;
            }
        }

        /// <summary>A chosen species or group as the tool takes it: the
        /// name, then its GBIF key in brackets. The tool uses the key; the
        /// name makes the copied Python command readable.</summary>
        private static string TaxonLabel(GbifTaxon t)
        {
            string name = (t.ScientificName ?? "").Replace(';', ',').Trim();
            return name.Length == 0 ? t.Key : name + " (" + t.Key + ")";
        }

        /// <summary>The choices on the tabs as a request for the
        /// geoprocessing tool. <paramref name="f"/> is the filter just built
        /// from the same tabs (null when an existing download is loaded).</summary>
        private GbifActions.ToolRequest BuildRequest(GbifFilter f, string method, string gdb, string name)
        {
            var q = new GbifActions.ToolRequest
            {
                Method = method,
                OutputPath = Path.Combine(gdb, name),
                IncludeNoCoordinates = IncludeNoCoordBox.IsChecked == true,
            };
            if (f == null)
            {
                return q;
            }
            foreach (TaxonItem t in ChosenList.Items)
            {
                q.Taxa.Add(TaxonLabel(t.Taxon));
            }
            q.Area = AreaBox.SelectedItem as GbifActions.AreaChoice;
            q.Country = f.Country;
            q.StateProvince = f.StateProvince;
            q.IncludeNoCoordinates = f.IncludeNoCoordinates;
            q.YearFrom = f.YearFrom;
            q.YearTo = f.YearTo;
            if (f.Months.Count < 12)
            {
                q.Months.AddRange(f.Months);
            }
            foreach (var b in _basis.Where(b => b.Box.IsChecked == true))
            {
                q.RecordKinds.Add((string)b.Box.Content);
            }
            q.NoLocationProblems = f.ExcludeGeospatialIssues;
            q.NoCentroids = f.ExcludeCentroids;
            q.PresentOnly = f.PresentOnly;
            q.NoNonCommercial = f.Licenses.Count > 0;
            q.ImagesOnly = f.WithImagesOnly;
            q.MaxUncertaintyMeters = f.MaxCoordinateUncertaintyMeters;
            q.ExcludedSources.AddRange(f.ExcludedDatasetKeys);
            return q;
        }

        private int _toolMessages;
        private bool _toolSaidCancelled;

        /// <summary>One message of the geoprocessing tool, into the report.</summary>
        private void OnToolMessage(string text, int severity)
        {
            _toolMessages++;
            if (OnlineReport.IsBoilerplate(text))
            {
                return;
            }
            if (OnlineReport.SaysCancelled(text))
            {
                _toolSaidCancelled = true;
            }
            Log(OnlineReport.Line(text, severity));
        }

        /// <summary>Runs the GBIF Species Occurrences geoprocessing tool
        /// with the request and shows its messages and progress here. The
        /// tool fetches the records, writes the feature class and adds it to
        /// the map, and the run is recorded in the Geoprocessing History.</summary>
        private async Task RunToolAsync(GbifActions.ToolRequest q, string gdb, string name, CancellationToken ct)
        {
            Step(1, "Starting the geoprocessing tool...");
            _toolMessages = 0;
            _toolSaidCancelled = false;
            bool isDownload = q.Method == GbifActions.MethodDownload;
            var result = await GbifActions.RunToolAsync(q,
                (text, severity) => Dispatcher.BeginInvoke(new Action(() => OnToolMessage(text, severity))),
                (label, percent) => Dispatcher.BeginInvoke(new Action(() =>
                {
                    if (!string.IsNullOrEmpty(label))
                    {
                        ProgressText.Text = label;
                    }
                    if (percent >= 0)
                    {
                        Progress.Value = Math.Min(100, percent);
                    }
                })), ct);
            // let the messages still queued for this thread be shown first
            await Dispatcher.InvokeAsync(() => { }, DispatcherPriority.Background);
            if (_toolMessages == 0)
            {
                // the messages did not arrive as the tool ran: show them now
                foreach (var m in result.Messages)
                {
                    OnToolMessage(m.Text ?? "", m.Type == ArcGIS.Desktop.Core.Geoprocessing.GPMessageType.Error ? 2
                        : m.Type == ArcGIS.Desktop.Core.Geoprocessing.GPMessageType.Warning ? 1 : 0);
                }
            }
            if (isDownload)
            {
                RefreshDownloadsQuietly();      // tab 5 shows the request just made
            }
            if (result.IsCanceled || ct.IsCancellationRequested)
            {
                if (!_toolSaidCancelled)
                {
                    Log("Cancelled.");
                }
                Step(0, "Cancelled.");
                return;
            }
            if (result.IsFailed)
            {
                Step(0, "Stopped. The report below says why.");
                return;
            }
            GbifFeatureWriter.Result r = null;
            try
            {
                r = await GbifActions.SummarizeAsync(gdb, name);
            }
            catch (Exception)
            {
                // the feature class is written; only Save the source list is unavailable
            }
            string key = null, doi = null;
            try
            {
                var values = result.Values?.ToList();
                if (values != null && values.Count >= 3)
                {
                    key = values[1];
                    doi = values[2];
                }
            }
            catch (Exception)
            {
            }
            _lastResult = r;
            _lastResultWhat = Path.Combine(gdb, name) + (string.IsNullOrEmpty(key)
                ? " (fetched directly from GBIF's search service)"
                : " (GBIF download " + key + (string.IsNullOrEmpty(doi) ? "" : ", DOI " + doi) + ")");
            SaveSourcesButton.IsEnabled = r != null && r.ByDataset.Count > 0;
            Log("This run is recorded in the Geoprocessing History (Analysis tab, History). Right-click its entry there "
                + "and choose Copy Python Command to repeat the same request from a script. ArcGIS Pro does not record "
                + "where the toolbox is for a run started from a window, so the first line of the copied command names "
                + "the wrong toolbox. Replace that line with this one:");
            Log("    arcpy.ImportToolbox(r\"" + GbifActions.ToolboxPath + "\")");
            Step(100, r == null ? "Done." : "Done: " + r.Written.ToString("N0", CultureInfo.CurrentCulture)
                + (r.WrittenWithoutLocation > 0 ? " records." : " points."));
        }

        internal static string SourceLine(GbifFeatureWriter.Result r, string key, long count) =>
            (r.DatasetNames.TryGetValue(key, out string name) && !string.IsNullOrEmpty(name) ? name : "(name not given)")
            + "   [" + key + "]   " + count.ToString("N0", CultureInfo.CurrentCulture);

        /// <summary>Splits "...\x.gdb\name" (a feature dataset in between is
        /// allowed for reading but not written to here).</summary>
        internal static bool SplitOutput(string path, out string gdb, out string name, out string why)
        {
            gdb = name = why = null;
            string p = (path ?? "").Trim().Trim('"').Replace('/', '\\').TrimEnd('\\');
            int at = -1;
            for (int i = p.IndexOf(".gdb", StringComparison.OrdinalIgnoreCase); i >= 0;
                 i = p.IndexOf(".gdb", i + 1, StringComparison.OrdinalIgnoreCase))
            {
                if (i + 4 < p.Length && p[i + 4] == '\\')
                {
                    at = i;
                    break;
                }
            }
            if (at < 0)
            {
                why = "The output must be a feature class inside a file geodatabase, such as C:\\Data\\Wildlife.gdb\\GBIF_occurrences.";
                return false;
            }
            gdb = p.Substring(0, at + 4);
            name = p.Substring(at + 5);
            if (name.Contains("\\"))
            {
                why = "Put the output directly in the geodatabase, not inside a feature dataset.";
                return false;
            }
            if (name.Length == 0 || !(char.IsLetter(name[0]) || name[0] == '_') || name.Any(c => !(char.IsLetterOrDigit(c) || c == '_')))
            {
                why = "The feature class name must start with a letter and hold only letters, digits and underscores.";
                return false;
            }
            if (!Directory.Exists(gdb))
            {
                why = "The geodatabase " + gdb + " does not exist.";
                return false;
            }
            return true;
        }

        // =====================================================================
        //  my downloads
        // =====================================================================
        /// <summary>Lists the account's downloads again without a message
        /// when nobody is signed in.</summary>
        private void RefreshDownloadsQuietly()
        {
            if (_gbif.HasAccount)
            {
                OnRefreshDownloads(this, new RoutedEventArgs());
            }
        }

        private async void OnRefreshDownloads(object sender, RoutedEventArgs e)
        {
            if (!_gbif.HasAccount)
            {
                DownloadsNote.Text = "Sign in on tab 4 to see the downloads on your GBIF account.";
                return;
            }
            DownloadsNote.Text = "Asking GBIF...";
            try
            {
                List<GbifDownload> list = await _gbif.ListDownloadsAsync(100);
                _downloads.Clear();
                foreach (GbifDownload d in list)
                {
                    _downloads.Add(new DownloadRow(d));
                }
                DownloadsNote.Text = list.Count == 0 ? "No downloads on the account " + _gbif.UserName + "."
                    : list.Count + (list.Count == 1 ? " download" : " downloads") + " on the account " + _gbif.UserName + ", newest first.";
            }
            catch (Exception ex)
            {
                DownloadsNote.Text = Short(ex);
            }
        }

        private async void OnLoadDownload(object sender, RoutedEventArgs e)
        {
            if (_busy || !(DownloadsGrid.SelectedItem is DownloadRow row))
            {
                DownloadsNote.Text = _busy ? "Another request is still running on tab 4." : "Select a download first.";
                return;
            }
            if (!row.Download.IsFinished)
            {
                DownloadsNote.Text = "That download is not ready (" + row.StatusText + ").";
                return;
            }
            if (row.Download.Format == "SPECIES_LIST")
            {
                DownloadsNote.Text = "A species-list download has no coordinates, so it cannot be loaded as points.";
                return;
            }
            await LoadExistingAsync(row.Download.Key);
        }

        /// <summary>Loads a GBIF download zip file that is already on disk.</summary>
        private async void OnLoadZip(object sender, RoutedEventArgs e)
        {
            if (_busy)
            {
                DownloadsNote.Text = "Another request is still running on tab 4.";
                return;
            }
            var dlg = new Microsoft.Win32.OpenFileDialog
            {
                Title = "Choose a GBIF download zip file",
                Filter = "GBIF download (*.zip)|*.zip",
                CheckFileExists = true,
            };
            try
            {
                if (!string.IsNullOrEmpty(_settings.LastZipFolder) && Directory.Exists(_settings.LastZipFolder))
                {
                    dlg.InitialDirectory = _settings.LastZipFolder;
                }
            }
            catch (Exception)
            {
            }
            if (dlg.ShowDialog(Window.GetWindow(this)) != true)
            {
                return;
            }
            _settings.LastZipFolder = Path.GetDirectoryName(dlg.FileName);
            _settings.Save();
            await LoadExistingAsync(dlg.FileName);
        }

        /// <summary>Runs the geoprocessing tool on an existing download: its
        /// GBIF key or the path of its zip file. The records go to the output
        /// feature class named on tab 4; no study area is applied.</summary>
        private async Task LoadExistingAsync(string keyOrPath)
        {
            if (!SplitOutput(OutBox.Text, out string gdb, out string name, out string why))
            {
                Tabs.SelectedIndex = 3;
                Say(why);
                return;
            }
            Tabs.SelectedIndex = 3;
            SetBusy(true);
            var cts = _workCts = new CancellationTokenSource();
            ClearLog();
            try
            {
                GbifActions.ToolRequest q = BuildRequest(null, GbifActions.MethodLoad, gdb, name);
                q.ExistingDownload = keyOrPath;
                await RunToolAsync(q, gdb, name, cts.Token);
            }
            catch (OperationCanceledException)
            {
                Log("Cancelled.");
            }
            catch (Exception ex)
            {
                Log("Stopped: " + Short(ex));
                ProgressText.Text = "Stopped: " + Short(ex);
            }
            finally
            {
                SetBusy(false);
            }
        }

        private async void OnCancelDownload(object sender, RoutedEventArgs e)
        {
            if (!(DownloadsGrid.SelectedItem is DownloadRow row))
            {
                DownloadsNote.Text = "Select a download first.";
                return;
            }
            if (!row.Download.IsRunning)
            {
                DownloadsNote.Text = "Only a download GBIF is still preparing can be cancelled.";
                return;
            }
            try
            {
                await _gbif.CancelDownloadAsync(row.Download.Key);
                DownloadsNote.Text = "Cancel requested.";
                OnRefreshDownloads(sender, e);
            }
            catch (Exception ex)
            {
                DownloadsNote.Text = Short(ex);
            }
        }

        private void OnOpenDownloadPage(object sender, RoutedEventArgs e)
        {
            if (DownloadsGrid.SelectedItem is DownloadRow row)
            {
                OpenUrl("https://www.gbif.org/occurrence/download/" + row.Download.Key);
            }
            else
            {
                DownloadsNote.Text = "Select a download first.";
            }
        }

        private async void OnCopyCitation(object sender, RoutedEventArgs e)
        {
            if (!(DownloadsGrid.SelectedItem is DownloadRow row))
            {
                DownloadsNote.Text = "Select a download first.";
                return;
            }
            try
            {
                string cite = await _gbif.CitationAsync(row.Download.Key)
                    ?? "GBIF.org GBIF Occurrence Download https://doi.org/" + row.Download.Doi;
                Clipboard.SetText(cite);
                DownloadsNote.Text = "Copied: " + cite;
                try
                {
                    GbifActions.Info("The citation has been copied to the clipboard:" + Environment.NewLine + Environment.NewLine
                        + cite + Environment.NewLine + Environment.NewLine
                        + "Paste it into the references of your report or the credits of your map. "
                        + "Click \"How to cite GBIF data\" for what it means.");
                }
                catch (Exception)
                {
                    // outside Pro the note above is all there is
                }
            }
            catch (Exception ex)
            {
                DownloadsNote.Text = Short(ex);
            }
        }

        // =====================================================================
        //  small things
        // =====================================================================
        private void Step(double percent, string text)
        {
            Progress.Value = Math.Max(0, Math.Min(100, percent));
            ProgressText.Text = text;
        }

        private void ClearLog() => LogBox.Document.Blocks.Clear();

        /// <summary>Adds one item to the report on tab 4: a bulleted paragraph
        /// with a hanging indent and half a line of space after it, so one
        /// item is easy to tell from the next. A line that starts with spaces
        /// is a detail of the item above it: indented, no bullet, set close.</summary>
        private void Log(string line)
        {
            bool detail = line.StartsWith("    ", StringComparison.Ordinal);
            var para = new System.Windows.Documents.Paragraph
            {
                Margin = detail ? new Thickness(34, 0, 0, 1) : new Thickness(16, LogBox.Document.Blocks.Count == 0 ? 0 : 8, 0, 0),
                TextIndent = detail ? 0 : -14,
                LineHeight = double.NaN,
            };
            para.Inlines.Add(new System.Windows.Documents.Run(detail ? line.Trim().Replace("\t", "     ") : "\u2022  " + line));
            LogBox.Document.Blocks.Add(para);
            LogBox.ScrollToEnd();
        }

        /// <summary>The report as plain text (for tests and for copying).</summary>
        internal string LogText() =>
            new System.Windows.Documents.TextRange(LogBox.Document.ContentStart, LogBox.Document.ContentEnd).Text;

        /// <summary>The message line above the bottom bar; empty hides it.</summary>
        private void SetStatus(string message)
        {
            StatusText.Text = message ?? "";
            StatusText.Visibility = string.IsNullOrEmpty(message) ? Visibility.Collapsed : Visibility.Visible;
        }

        private void OnTabChanged(object sender, SelectionChangedEventArgs e)
        {
            // only the tab strip itself: lists and boxes inside the tabs raise this event too.
            // A message belongs to the tab it appeared on.
            if (ReferenceEquals(e.OriginalSource, Tabs) && StatusText != null)
            {
                SetStatus(null);
                _ = RefreshOpenTabAsync();
            }
        }

        /// <summary>The species and sources lists describe the choices on tab 1.
        /// When one of those tabs is opened and the choices are not the ones its
        /// list was made for (or it has no list yet), the list is asked for again,
        /// quietly: an incomplete choice on tab 1 just leaves the list as it is.</summary>
        private async Task RefreshOpenTabAsync()
        {
            int tab = Tabs.SelectedIndex;
            if (tab != 1 && tab != 2 && tab != 3)
            {
                return;
            }
            if (tab == 3 && _busy)
            {
                return;                         // the note is not touched while records are being got
            }
            try
            {
                GbifFilter f = await BuildFilterAsync(true);
                if (f == null || Tabs.SelectedIndex != tab)
                {
                    return;
                }
                if (tab == 3)
                {
                    await RefreshRouteNoteAsync(f);
                }
                else if (tab == 1)
                {
                    if (Signature(ForSpeciesList(f)) != _speciesSig)
                    {
                        await ListSpeciesAsync(f);
                    }
                }
                else if (SourcesSignature(f) != _sourcesSig)
                {
                    await ListSourcesAsync(f);
                }
            }
            catch (Exception)
            {
                // a refresh that fails leaves the list and its own note as they are
            }
        }

        private void Say(string message)
        {
            if (_quiet)
            {
                return;                         // a background refresh never interrupts
            }
            SetStatus(message);
            try
            {
                GbifActions.Info(message);
            }
            catch (Exception)
            {
                // outside Pro the status line is all there is
            }
        }

        private static string Short(Exception ex)
        {
            if (ex is TaskCanceledException || ex is OperationCanceledException)
            {
                return "GBIF did not answer in time. Try again.";
            }
            if (ex is System.Net.Http.HttpRequestException)
            {
                return "GBIF could not be reached. Check the internet connection. (" + ex.Message + ")";
            }
            return ex.Message;
        }

        private static string Span(double seconds)
        {
            if (seconds < 90)
            {
                return Math.Max(1, Math.Round(seconds)).ToString("0", CultureInfo.CurrentCulture) + " seconds";
            }
            if (seconds < 5400)
            {
                return Math.Round(seconds / 60).ToString("0", CultureInfo.CurrentCulture) + " minutes";
            }
            return (seconds / 3600).ToString("0.0", CultureInfo.CurrentCulture) + " hours";
        }

        internal static string Bytes(long n) =>
            n < 1 << 20 ? (n / 1024.0).ToString("0", CultureInfo.CurrentCulture) + " KB"
            : n < 1L << 30 ? (n / 1048576.0).ToString("0.0", CultureInfo.CurrentCulture) + " MB"
            : (n / 1073741824.0).ToString("0.00", CultureInfo.CurrentCulture) + " GB";

        private static void OpenUrl(string url)
        {
            try
            {
                global::WildlifeTools.SafeLink.Open(url);
            }
            catch (Exception)
            {
            }
        }

        private void OnLink(object sender, System.Windows.Navigation.RequestNavigateEventArgs e)
        {
            OpenUrl(e.Uri.AbsoluteUri);
            e.Handled = true;
        }

        private void OnCiteHelp(object sender, RoutedEventArgs e)
        {
            var w = new GbifCitationWindow { Owner = Window.GetWindow(this) };
            w.Show();
        }

        /// <summary>The table GBIF asks for when a derived dataset is
        /// registered: one line per source dataset, with its record count.</summary>
        internal static string SourceListCsv(GbifFeatureWriter.Result r) =>
            "datasetKey,count" + Environment.NewLine
            + string.Join(Environment.NewLine, r.ByDataset.OrderByDescending(kv => kv.Value)
                .Select(kv => kv.Key + "," + kv.Value.ToString(CultureInfo.InvariantCulture)))
            + Environment.NewLine;

        private void OnSaveSources(object sender, RoutedEventArgs e)
        {
            if (_lastResult == null || _lastResult.ByDataset.Count == 0)
            {
                return;
            }
            var dlg = new Microsoft.Win32.SaveFileDialog
            {
                Title = "Save the source list",
                Filter = "Comma-separated table (*.csv)|*.csv",
                FileName = "GBIF_source_datasets.csv",
                AddExtension = true,
                OverwritePrompt = true,
            };
            if (dlg.ShowDialog(Window.GetWindow(this)) != true)
            {
                return;
            }
            try
            {
                File.WriteAllText(dlg.FileName, SourceListCsv(_lastResult), new System.Text.UTF8Encoding(false));
                Log("Saved the source list of " + _lastResultWhat + " to " + dlg.FileName + ": "
                    + _lastResult.ByDataset.Count.ToString("N0", CultureInfo.CurrentCulture) + " datasets, "
                    + _lastResult.ByDataset.Values.Sum().ToString("N0", CultureInfo.CurrentCulture) + " records.");
            }
            catch (Exception ex)
            {
                Say("Could not save the source list: " + ex.Message);
            }
        }

        private void OnHelp(object sender, RoutedEventArgs e)
        {
            try
            {
                GbifActions.Help();
            }
            catch (Exception)
            {
            }
        }

        /// <summary>Browse: choose the geodatabase and the name of the output.</summary>
        private void OnBrowseOutput(object sender, RoutedEventArgs e)
        {
            try
            {
                string path = GbifActions.BrowseOutputFeatureClass(OutBox.Text, "Save the GBIF records as");
                if (!string.IsNullOrEmpty(path))
                {
                    OutBox.Text = path;
                }
            }
            catch (Exception)
            {
                // outside ArcGIS Pro there is no such dialog: the path is typed
            }
        }

        private void OnClose(object sender, RoutedEventArgs e) => CloseRequested?.Invoke(this, EventArgs.Empty);

        // ---- row and item types -------------------------------------------------
        /// <summary>A list entry with a value behind its caption (null = "any").</summary>
        private sealed class PickItem
        {
            public string Value { get; }
            private readonly string _caption;
            public PickItem(string value, string caption) { Value = value; _caption = caption; }
            public override string ToString() => _caption;
        }

        /// <summary>A row of the suggestions table. The common name, the
        /// group and the number of records arrive a moment after the name.</summary>
        internal sealed class SuggestRow : INotifyPropertyChanged
        {
            private string _common = "", _group = "";
            private long _count = -1;
            public GbifTaxon Taxon { get; }
            public SuggestRow(GbifTaxon t)
            {
                Taxon = t;
                _group = t.Group ?? "";
            }
            /// <summary>The name without its author, which arrives with the common name.</summary>
            public string Canonical
            {
                set { _canonical = string.IsNullOrWhiteSpace(value) ? null : value.Trim(); Raise(nameof(Name)); }
            }
            private string _canonical;
            /// <summary>The scientific name: without its author once GBIF has said which part that is.</summary>
            public string Name
            {
                get
                {
                    if (_canonical != null)
                    {
                        return _canonical;
                    }
                    string s = Taxon.ScientificName ?? Taxon.Key ?? "";
                    string a = Taxon.Authorship;
                    return !string.IsNullOrEmpty(a) && s.EndsWith(a, StringComparison.Ordinal) && s.Length > a.Length
                        ? s.Substring(0, s.Length - a.Length).Trim() : s;
                }
            }
            /// <summary>The name with its author and family: the hover help of the name.</summary>
            public string FullName
            {
                get
                {
                    string s = Taxon.ScientificName ?? Taxon.Key ?? "";
                    if (!string.IsNullOrEmpty(Taxon.Authorship) && !s.Contains(Taxon.Authorship))
                    {
                        s += " " + Taxon.Authorship;
                    }
                    return s + (string.IsNullOrEmpty(Taxon.Context) ? "" : "  (" + Taxon.Context + ")")
                        + ". Click to open it on GBIF.org.";
                }
            }
            public string Rank
            {
                get
                {
                    string r = (Taxon.Rank ?? "").ToLowerInvariant();
                    return string.IsNullOrEmpty(Taxon.Status) || Taxon.Status == "ACCEPTED" ? r
                        : (r + " (" + Taxon.Status.ToLowerInvariant().Replace('_', ' ') + ")").Trim();
                }
            }
            public string Common
            {
                get => _common;
                set { _common = value ?? ""; Raise(nameof(Common)); }
            }
            public string Group
            {
                get => _group;
                set { _group = value ?? ""; Raise(nameof(Group)); }
            }
            public long Count
            {
                get => _count;
                set { _count = value; Raise(nameof(CountText)); }
            }
            public string CountText => _count >= 0 ? _count.ToString("N0", CultureInfo.CurrentCulture) : "";
            public override string ToString() => Name + " (" + Rank + ")";
            public event PropertyChangedEventHandler PropertyChanged;
            private void Raise(string name) => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
        }

        private sealed class TaxonItem
        {
            public GbifTaxon Taxon { get; }
            public TaxonItem(GbifTaxon t) { Taxon = t; }

            public override string ToString()
            {
                string rank = string.IsNullOrEmpty(Taxon.Rank) ? "" : Taxon.Rank.Substring(0, 1) + Taxon.Rank.Substring(1).ToLowerInvariant();
                string s = Taxon.ScientificName ?? Taxon.Key;
                if (!string.IsNullOrEmpty(Taxon.Authorship) && !s.Contains(Taxon.Authorship))
                {
                    s += " " + Taxon.Authorship;
                }
                var extra = new List<string>();
                if (rank.Length > 0) extra.Add(rank.ToLowerInvariant());
                if (!string.IsNullOrEmpty(Taxon.Context)) extra.Add(Taxon.Context);
                if (!string.IsNullOrEmpty(Taxon.Status) && Taxon.Status != "ACCEPTED") extra.Add(Taxon.Status.ToLowerInvariant().Replace('_', ' '));
                return extra.Count == 0 ? s : s + "   (" + string.Join(", ", extra) + ")";
            }
        }

        internal sealed class SpeciesRow : INotifyPropertyChanged
        {
            private string _name;
            private bool _checked;
            private GbifTaxon _taxon;
            public string Key { get; set; }
            public long Count { get; set; }
            public string CountText => Count.ToString("N0", CultureInfo.CurrentCulture);
            public string Name
            {
                get => _name;
                set { _name = value; Raise(nameof(Name)); Raise(nameof(Display)); }
            }
            private string _common;
            /// <summary>GBIF's common name for the species, or null.</summary>
            public string Common
            {
                get => _common;
                set { _common = value; Raise(nameof(Display)); }
            }
            /// <summary>The scientific name, then the common name in brackets.</summary>
            public string Display => string.IsNullOrEmpty(_common) ? _name : _name + " (" + _common + ")";
            public bool Checked
            {
                get => _checked;
                set { _checked = value; Raise(nameof(Checked)); }
            }
            public GbifTaxon Taxon
            {
                get => _taxon;
                set { _taxon = value; Raise(nameof(Group)); }
            }
            public string Group => _taxon == null ? "" : (_taxon.Group ?? "");
            public event PropertyChangedEventHandler PropertyChanged;
            private void Raise(string n) => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(n));
        }

        internal sealed class SourceRow : INotifyPropertyChanged
        {
            private string _name, _license = "";
            private bool _checked = true;
            public string Key { get; set; }
            public long Count { get; set; }
            public string CountText => Count.ToString("N0", CultureInfo.CurrentCulture);
            public string Name
            {
                get => _name;
                set { _name = value; Raise(nameof(Name)); }
            }
            public string License
            {
                get => _license;
                set { _license = value; Raise(nameof(License)); }
            }
            public bool Checked
            {
                get => _checked;
                set
                {
                    if (_checked != value)
                    {
                        _checked = value;
                        Raise(nameof(Checked));
                    }
                }
            }
            public event PropertyChangedEventHandler PropertyChanged;
            private void Raise(string n) => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(n));
        }

        internal sealed class DownloadRow
        {
            public GbifDownload Download { get; }
            public DownloadRow(GbifDownload d) { Download = d; }
            public string CreatedText => Download.Created.HasValue
                ? Download.Created.Value.ToLocalTime().ToString("yyyy-MM-dd HH:mm", CultureInfo.CurrentCulture) : "";
            public string StatusText => StatusWords(Download.Status);
            public string RecordsText => Download.IsFinished ? Download.TotalRecords.ToString("N0", CultureInfo.CurrentCulture) : "";
            public string SizeText => Download.IsFinished && Download.Size > 0 ? Bytes(Download.Size) : "";
            public string FormatText => Download.Format == "SIMPLE_CSV" ? "Simple (fewer fields)"
                : Download.Format == "DWCA" ? "Full (Darwin Core)" : Download.Format == "SPECIES_LIST" ? "Species list" : Download.Format;
            public string Doi => Download.Doi;
            public string EraseText => Download.EraseAfter.HasValue
                ? Download.EraseAfter.Value.ToLocalTime().ToString("yyyy-MM-dd", CultureInfo.CurrentCulture) : "";

            internal static string StatusWords(string status)
            {
                switch (status)
                {
                    case "PREPARING": return "Preparing";
                    case "RUNNING": return "Running";
                    case "SUSPENDED": return "Waiting";
                    case "SUCCEEDED": return "Ready";
                    case "FAILED": return "Failed";
                    case "KILLED": return "Stopped by GBIF";
                    case "CANCELLED": return "Cancelled";
                    case "FILE_ERASED": return "Erased";
                    default: return status ?? "";
                }
            }
        }
    }

    /// <summary>A plain explanation of citing GBIF data, shown from the
    /// "How to cite GBIF data" buttons.</summary>
    internal sealed class GbifCitationWindow : Window
    {
        internal const string ExampleDoi = "https://doi.org/10.15468/dl.8cehyv";

        internal static readonly string Body =
            "WHY CITE\n"
            + "GBIF's data are free to use, but under GBIF's data user agreement anyone who uses them in research, "
            + "a report or a policy document agrees to cite them. GBIF does not own the records: they belong to the "
            + "museums, agencies, survey programs and volunteer projects that published them. One search usually "
            + "draws on many such sources, and a citation is how each of them gets credit.\n\n"
            + "WHAT A DOWNLOAD'S CITATION IS\n"
            + "Every GBIF download is given a DOI, a permanent web address. The citation GBIF writes for it looks "
            + "like this:\n\n"
            + "    GBIF.org (13 November 2026) GBIF Occurrence Download " + ExampleDoi + "\n\n"
            + "That one line stands for the whole request. Opening the DOI leads to the download's page on GBIF.org, "
            + "which records:\n"
            + "  - the exact filter that was asked for (the species or groups, the study area polygon, the dates and "
            + "every other choice);\n"
            + "  - the date of the request and the number of records it returned;\n"
            + "  - every source dataset that contributed, with its own name, citation and record count;\n"
            + "  - the license that applies to the records together;\n"
            + "  - the file itself, for as long as GBIF keeps it (tab 5 shows the date).\n"
            + "So a reader can see exactly what was used and who supplied it, and every contributing publisher is "
            + "credited through that single line.\n\n"
            + "HOW TO USE IT\n"
            + "Put the line in the references of your paper or report, or in the credits of your map, and refer to it "
            + "where you describe your data, for example: \"Occurrence records were obtained from GBIF (GBIF.org "
            + "2026).\" On tab 5, select the download and click Copy the citation. The tool also writes the "
            + "citation into the log on tab 4 when it loads a download.\n\n"
            + "RECORDS THAT WERE FETCHED DIRECTLY\n"
            + "The first option on tab 4, Fetch them now, uses GBIF's search service, and GBIF issues no DOI for "
            + "that. There are two ways to cite such records:\n\n"
            + "1. Request a GBIF download of the same choices. Select the second option on tab 4 and click Get the "
            + "records, with nothing else changed. You then have a DOI for exactly that filter. Use the records "
            + "from the download as well: GBIF's holdings change as publishers add and correct records, so a download made later can differ by a few "
            + "records from what was fetched directly.\n\n"
            + "2. Register a \"derived dataset\" with GBIF. This is GBIF's way of giving a DOI to records that did "
            + "not come from one download, or that you filtered further yourself. GBIF asks for a title, a short "
            + "description, a public web address where your version of the data can be found (a repository such as "
            + "Zenodo), and a table of the source datasets with the number of records taken from each. After "
            + "fetching records, click Save the source list on tab 4 to write that table, then register it at "
            + "gbif.org/derived-dataset/register, signed in to your GBIF account.\n\n"
            + "For a class exercise or a working map that will not be published, GBIF's citation guidelines are the "
            + "place to check what is expected.";

        public GbifCitationWindow()
        {
            Title = "How to cite GBIF data";
            Width = 720;
            Height = 640;
            MinWidth = 480;
            MinHeight = 360;
            WindowStartupLocation = WindowStartupLocation.CenterOwner;
            ShowInTaskbar = false;
            try
            {
                global::WildlifeTools.Corridor.JennessIcon.Apply(this);
            }
            catch (Exception)
            {
            }
            var dock = new DockPanel { Margin = new Thickness(14) };
            var buttons = new WrapPanel { HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 10, 0, 0) };
            DockPanel.SetDock(buttons, Dock.Bottom);
            void Link(string caption, string url)
            {
                var b = new Button { Content = caption, Padding = new Thickness(12, 4, 12, 4), Margin = new Thickness(0, 0, 8, 6), ToolTip = url };
                b.Click += (_, _) =>
                {
                    try
                    {
                        global::WildlifeTools.SafeLink.Open(url);
                    }
                    catch (Exception)
                    {
                    }
                };
                buttons.Children.Add(b);
            }
            Link("Open the example DOI", ExampleDoi);
            Link("GBIF citation guidelines", "https://www.gbif.org/citation-guidelines");
            Link("GBIF data user agreement", "https://www.gbif.org/terms/data-user");
            Link("Register a derived dataset", "https://www.gbif.org/derived-dataset/register");
            var close = new Button { Content = "Close", Width = 84, Padding = new Thickness(0, 4, 0, 4), Margin = new Thickness(16, 0, 0, 6), IsCancel = true };
            close.Click += (_, _) => Close();
            buttons.Children.Add(close);
            dock.Children.Add(buttons);
            dock.Children.Add(new TextBox
            {
                Text = Body,
                IsReadOnly = true,
                TextWrapping = TextWrapping.Wrap,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                BorderThickness = new Thickness(0),
                Background = System.Windows.Media.Brushes.Transparent,
                FontSize = 13.5,
            });
            Content = dock;
        }
    }

    /// <summary>The small sign-in box: GBIF user name and password.</summary>
    internal sealed class GbifSignInWindow : Window
    {
        private readonly TextBox _user = new TextBox { Height = 24, VerticalContentAlignment = VerticalAlignment.Center };
        private readonly PasswordBox _pass = new PasswordBox { Height = 24, VerticalContentAlignment = VerticalAlignment.Center };

        private readonly CheckBox _remember = new CheckBox
        {
            Content = "Remember me on this computer (Windows Credential Manager)",
            IsChecked = true,
            Margin = new Thickness(0, 10, 0, 0),
        };

        public string UserName => _user.Text.Trim();
        public string Password => _pass.Password;
        public bool Remember => _remember.IsChecked == true;

        public GbifSignInWindow(string user)
        {
            Title = "Sign in to GBIF";
            Width = 520;
            SizeToContent = SizeToContent.Height;
            ResizeMode = ResizeMode.NoResize;
            WindowStartupLocation = WindowStartupLocation.CenterOwner;
            ShowInTaskbar = false;
            try
            {
                global::WildlifeTools.Corridor.JennessIcon.Apply(this);
            }
            catch (Exception)
            {
            }
            _user.Text = user ?? "";
            var p = new StackPanel { Margin = new Thickness(14) };
            p.Children.Add(new TextBlock
            {
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 0, 0, 10),
                Text = "Only required for requesting a GBIF download and for listing your downloads. GBIF prepares "
                     + "downloads under your own free GBIF account. Use your GBIF user name, not your email address.",
            });
            p.Children.Add(new TextBlock { Text = "GBIF user name" });
            p.Children.Add(_user);
            p.Children.Add(new TextBlock { Text = "Password", Margin = new Thickness(0, 8, 0, 0) });
            p.Children.Add(_pass);
            p.Children.Add(_remember);
            p.Children.Add(new TextBlock
            {
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 8, 0, 0),
                Opacity = 0.85,
                Text = "What happens to the password: this tool sends it to GBIF, over an encrypted connection, with each "
                     + "request that needs your account; GBIF offers no other way to sign in. It is sent nowhere else, "
                     + "and it is never written to a settings file, a log or the geoprocessing history. If you check the "
                     + "box above, Windows keeps it in Credential Manager, encrypted under your own Windows login; "
                     + "unchecked, it is forgotten when you close ArcGIS Pro.",
            });
            var how = new Expander { Header = "Storing or removing it yourself", Margin = new Thickness(0, 8, 0, 0) };
            how.Content = new TextBox
            {
                IsReadOnly = true,
                TextWrapping = TextWrapping.Wrap,
                BorderThickness = new Thickness(0),
                Background = System.Windows.Media.Brushes.Transparent,
                Margin = new Thickness(0, 4, 0, 0),
                Text = "You can make the entry yourself, so that you never type the password into this window:\n"
                     + "1. Open the Windows Start menu, type Credential Manager and open it.\n"
                     + "2. Click Windows Credentials, then Add a generic credential.\n"
                     + "3. Internet or network address:  " + CredentialStore.TargetName(GbifClient.Service) + "\n"
                     + "4. Type your GBIF user name and password, and click OK.\n"
                     + "Then close and reopen this tool's window; it will show you as signed in.\n\n"
                     + "Or, in a Command Prompt or PowerShell window, type this line with your own user name; "
                     + "Windows then asks for the password without showing it:\n"
                     + "cmdkey /generic:" + CredentialStore.TargetName(GbifClient.Service) + " /user:YOUR_GBIF_USER_NAME /pass\n\n"
                     + "To remove the entry, click Sign out in this window, or find it in the same list in Credential Manager and click Remove.",
            };
            p.Children.Add(how);
            var link = new TextBlock { Margin = new Thickness(0, 10, 0, 0) };
            var h = new System.Windows.Documents.Hyperlink(new System.Windows.Documents.Run("Create a free GBIF account"))
            {
                NavigateUri = new Uri("https://www.gbif.org/user/profile"),
            };
            h.RequestNavigate += (_, e) =>
            {
                try
                {
                    global::WildlifeTools.SafeLink.Open(e.Uri.AbsoluteUri);
                }
                catch (Exception)
                {
                }
                e.Handled = true;
            };
            link.Inlines.Add(h);
            p.Children.Add(link);
            var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 14, 0, 0) };
            var ok = new Button { Content = "Sign in", Width = 84, IsDefault = true };
            var cancel = new Button { Content = "Cancel", Width = 84, Margin = new Thickness(8, 0, 0, 0), IsCancel = true };
            ok.Click += (_, _) =>
            {
                if (UserName.Length == 0 || Password.Length == 0)
                {
                    return;
                }
                DialogResult = true;
            };
            buttons.Children.Add(ok);
            buttons.Children.Add(cancel);
            p.Children.Add(buttons);
            Content = p;
            Loaded += (_, _) => (UserName.Length == 0 ? (UIElement)_user : _pass).Focus();
        }
    }
}
