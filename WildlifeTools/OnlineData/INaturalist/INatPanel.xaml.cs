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
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Threading;
using WildlifeTools.OnlineData.Gbif;

namespace WildlifeTools.OnlineData.INaturalist
{
    /// <summary>
    /// The content of the iNaturalist Observations window. It asks
    /// iNaturalist the small questions itself (names, places, counts, the
    /// species of an area), and reaches ArcGIS Pro only through
    /// <see cref="INatActions"/>, so the panel can be built and drawn
    /// outside Pro. The observations are fetched and written by the
    /// geoprocessing tool.
    /// </summary>
    public partial class INatPanel : UserControl
    {
        public event EventHandler CloseRequested;

        internal const long LargeRequest = 100000;      // above this the count warns how long it takes
        internal const long MaxObservations = 2000000;  // above this the tool refuses the run
        internal const double SecondsPerPage = 2.2;     // as in inaturalist_observations.py

        private readonly INatClient _inat = new INatClient();
        private readonly ObservableCollection<TaxonRow> _found = new ObservableCollection<TaxonRow>();
        private readonly ObservableCollection<TaxonRow> _species = new ObservableCollection<TaxonRow>();
        private readonly ObservableCollection<TaxonRow> _chosen = new ObservableCollection<TaxonRow>();
        private readonly List<CheckBox> _months = new List<CheckBox>();
        private CancellationTokenSource _taxonCts, _placeCts, _countCts, _speciesCts, _workCts;
        private (double West, double South, double East, double North)? _areaBox;
        private bool _areaBoxKnown;
        private string _countSig, _speciesSig;
        private int _speciesPage;
        private long _speciesTotal;
        private bool _busy, _loadedOnce, _filling, _overLimit;
        private INatTaxon _photoTaxon;
        private readonly List<CheckBox> _groups = new List<CheckBox>();
        /// <summary>The groups of the species list: iNaturalist's own broad groups (its "iconic taxa").</summary>
        private static readonly (string Label, string Iconic)[] Groups =
        {
            ("Mammals", "Mammalia"), ("Birds", "Aves"), ("Reptiles", "Reptilia"), ("Amphibians", "Amphibia"),
            ("Ray-finned fishes", "Actinopterygii"), ("Insects", "Insecta"), ("Arachnids", "Arachnida"),
            ("Mollusks", "Mollusca"), ("Other animals", "Animalia"), ("Plants", "Plantae"), ("Fungi", "Fungi"),
        };
        private int _photoPage, _photoShown;
        private CancellationTokenSource _photoCts;
        private object _getToolTip;
        private int _toolMessages;
        private bool _toolSaidCancelled;

        private static readonly string[] MonthNames =
            { "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" };

        public INatPanel()
        {
            InitializeComponent();
            for (int m = 0; m < 12; m++)
            {
                var box = new CheckBox { Content = MonthNames[m], Margin = new Thickness(0, 0, 10, 0) };
                box.Click += OnChoiceChanged;
                _months.Add(box);
                MonthPanel.Children.Add(box);
            }
            foreach (var (label, iconic) in Groups)
            {
                var box = new CheckBox { Content = label, Tag = iconic, Margin = new Thickness(0, 0, 12, 2) };
                box.Click += OnGroupToggled;
                _groups.Add(box);
                GroupPanel.Children.Add(box);
            }
            foreach (string s in new[] { "Any", "Native", "Introduced" })
            {
                EstablishmentBox.Items.Add(s);
            }
            _filling = true;
            EstablishmentBox.SelectedIndex = 0;
            PlaceList.Items.Add(NoPlace);
            PlaceList.SelectedIndex = 0;
            AreaBox.Items.Add(GbifActions.AreaChoice.World());
            AreaBox.SelectedIndex = 0;
            _filling = false;
            TaxonGrid.ItemsSource = _found;
            SpeciesGrid.ItemsSource = _species;
            ChosenList.ItemsSource = _chosen;
            Loaded += OnLoaded;
            Unloaded += (_, _) =>
            {
                _taxonCts?.Cancel();
                _placeCts?.Cancel();
                _countCts?.Cancel();
                _speciesCts?.Cancel();
                _photoCts?.Cancel();
                _workCts?.Cancel();
            };
        }

        private const string NoPlace = "No place";

        private async void OnLoaded(object sender, RoutedEventArgs e)
        {
            if (_loadedOnce)
            {
                return;
            }
            _loadedOnce = true;
            try
            {
                await RefreshAreasAsync();
                OutputBox.Text = await INatActions.DefaultOutputPathAsync("iNaturalist_observations");
            }
            catch (Exception)
            {
                // outside Pro, or no project: the user types the path
            }
        }

        // =====================================================================
        //  species
        // =====================================================================
        private void OnTaxonKey(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.Enter)
            {
                e.Handled = true;
                OnTaxonSearch(sender, e);
            }
        }

        private async void OnTaxonSearch(object sender, RoutedEventArgs e)
        {
            string text = TaxonBox.Text.Trim();
            if (text.Length == 0)
            {
                TaxonNote.Text = "Type a scientific or common name first.";
                return;
            }
            _taxonCts?.Cancel();
            var cts = _taxonCts = new CancellationTokenSource();
            TaxonNote.Text = "Asking iNaturalist...";
            try
            {
                List<INatTaxon> list = await _inat.SearchTaxaAsync(text, cts.Token);
                if (cts.IsCancellationRequested)
                {
                    return;
                }
                _found.Clear();
                foreach (INatTaxon t in list)
                {
                    _found.Add(new TaxonRow(t, t.Observations));
                }
                TaxonNote.Text = list.Count == 0
                    ? "iNaturalist has no species or group with a name like that. Try the scientific name, or fewer letters."
                    : "Select the species or groups you want (hold Shift or Ctrl for several) and click Add. The Observations column is the number iNaturalist holds worldwide, of every grade.";
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                TaxonNote.Text = Short(ex);
            }
        }

        // =====================================================================
        //  browsing the taxonomy (tab 1)
        // =====================================================================
        private bool _treeAsked;

        private async void OnNameTabChanged(object sender, SelectionChangedEventArgs e)
        {
            if (ReferenceEquals(e.OriginalSource, NameTabs))
            {
                e.Handled = true;                     // not a change of the window's own tabs
                if (NameTabs.SelectedIndex == 1 && !_treeAsked)
                {
                    await LoadTaxonomyAsync();
                }
            }
        }

        /// <summary>The kingdoms, as the top of the tree.</summary>
        private async Task LoadTaxonomyAsync()
        {
            _treeAsked = true;
            BrowseTree.Items.Clear();
            BrowseTree.Items.Add(new TreeViewItem { Header = "Asking iNaturalist...", IsEnabled = false });
            try
            {
                var (kingdoms, _) = await _inat.ChildrenAsync(INatClient.RootTaxon, CancellationToken.None);
                BrowseTree.Items.Clear();
                foreach (INatTaxon t in kingdoms.OrderBy(k => k.Name, StringComparer.OrdinalIgnoreCase))
                {
                    BrowseTree.Items.Add(MakeNode(t));
                }
            }
            catch (Exception ex)
            {
                _treeAsked = false;                   // asked again the next time the tab is opened
                BrowseTree.Items.Clear();
                BrowseTree.Items.Add(new TreeViewItem { Header = Short(ex) + " Open this tab again to retry.", IsEnabled = false });
            }
        }

        private static bool IsLowestRank(string rank) =>
            rank == "subspecies" || rank == "variety" || rank == "form" || rank == "hybrid" || rank == "infrahybrid";

        private TreeViewItem MakeNode(INatTaxon t)
        {
            var row = new TaxonRow(t, t.Observations);
            var node = new TreeViewItem
            {
                Header = row.Display + (t.Observations >= 0
                    ? "  \u2014  " + N0(t.Observations) + (t.Observations == 1 ? " observation" : " observations") : ""),
                Tag = row,
            };
            if (!IsLowestRank(t.Rank))
            {
                node.Items.Add(new TreeViewItem { Header = "Asking iNaturalist...", IsEnabled = false });
                node.Expanded += OnNodeExpanded;
            }
            return node;
        }

        private async void OnNodeExpanded(object sender, RoutedEventArgs e)
        {
            var node = (TreeViewItem)sender;
            e.Handled = true;
            if (!(node.Tag is TaxonRow row) || node.Items.Count != 1
                || !(node.Items[0] is TreeViewItem first) || first.Tag != null)
            {
                return;                               // already read
            }
            try
            {
                var (kids, total) = await _inat.ChildrenAsync(row.Taxon.Id, CancellationToken.None);
                node.Items.Clear();
                foreach (INatTaxon k in kids.OrderBy(k => k.Name, StringComparer.OrdinalIgnoreCase))
                {
                    node.Items.Add(MakeNode(k));
                }
                if (kids.Count == 0)
                {
                    node.Items.Add(new TreeViewItem { Header = "(nothing inside)", Tag = "empty", IsEnabled = false });
                }
                else if (kids.Count < total)
                {
                    node.Items.Add(new TreeViewItem
                    {
                        Header = "(these are the " + N0(kids.Count) + " most observed of " + N0(total) + "; search by name for the others)",
                        Tag = "more",
                        IsEnabled = false,
                    });
                }
            }
            catch (Exception ex)
            {
                first.Header = Short(ex);
            }
        }

        private void OnTreeDoubleClick(object sender, MouseButtonEventArgs e)
        {
            // only a name with nothing inside it adds on a double-click; on a branch the double-click opens or closes it
            if (BrowseTree.SelectedItem is TreeViewItem tv && tv.Tag is TaxonRow row && !tv.HasItems)
            {
                AddTaxa(new[] { row });
            }
        }

        /// <summary>The Add button of a row (the species list on tab 3).</summary>
        private void OnTaxonAdd(object sender, RoutedEventArgs e)
        {
            if ((sender as FrameworkElement)?.DataContext is TaxonRow row)
            {
                AddTaxa(new[] { row });
            }
        }

        /// <summary>Tab 1's Add button. The tab that is showing decides: every
        /// selected row of the search results, or the tree's selected name.</summary>
        private void OnAddSelectedTaxa(object sender, RoutedEventArgs e)
        {
            if (NameTabs.SelectedIndex == 1)
            {
                if (BrowseTree.SelectedItem is TreeViewItem tv && tv.Tag is TaxonRow picked)
                {
                    AddTaxa(new[] { picked });
                }
                else
                {
                    SetStatus("Select a name in the taxonomy first, then click Add.");
                }
                return;
            }
            List<TaxonRow> rows = TaxonGrid.SelectedItems.OfType<TaxonRow>().ToList();
            if (rows.Count == 0)
            {
                SetStatus(_found.Count == 0 ? "Search for a name first, then select the species or groups you want and click Add."
                                            : "Select one or more rows in the list first, then click Add.");
                return;
            }
            // in the order of the list, not the order they were clicked
            AddTaxa(rows.OrderBy(r => _found.IndexOf(r)).ToList());
        }

        private void OnTaxonGridDoubleClick(object sender, MouseButtonEventArgs e)
        {
            // a double-click on a row adds it; on a header or the empty part, nothing
            var from = e.OriginalSource as DependencyObject;
            while (from != null && !(from is DataGridRow))
            {
                from = from is System.Windows.Media.Visual || from is System.Windows.Media.Media3D.Visual3D
                    ? System.Windows.Media.VisualTreeHelper.GetParent(from) : LogicalTreeHelper.GetParent(from);
            }
            if ((from as DataGridRow)?.Item is TaxonRow row)
            {
                AddTaxa(new[] { row });
            }
        }

        private void AddTaxa(IReadOnlyList<TaxonRow> rows)
        {
            var added = new List<string>();
            foreach (TaxonRow row in rows)
            {
                if (_chosen.All(c => c.Taxon.Id != row.Taxon.Id))
                {
                    _chosen.Add(new TaxonRow(row.Taxon, -1));
                    added.Add(row.Taxon.Name);
                }
            }
            ShowChosen();
            SetStatus(added.Count == 0
                ? (rows.Count == 1 ? rows[0].Taxon.Name + " is already" : "Those are already") + " among the species or groups to get (tab 1)."
                : added.Count == 1 ? added[0] + " is among the species or groups to get (tab 1)."
                : added.Count.ToString(CultureInfo.CurrentCulture) + " species or groups were added to those to get (tab 1): "
                  + string.Join(", ", added) + ".");
            if (added.Count > 0)
            {
                Stale();
            }
        }

        private void OnTaxonRemove(object sender, RoutedEventArgs e)
        {
            if ((sender as FrameworkElement)?.DataContext is TaxonRow row)
            {
                _chosen.Remove(row);
                ShowChosen();
                Stale();
            }
        }

        private void ShowChosen()
        {
            ChosenNote.Text = _chosen.Count == 0
                ? "None chosen: observations of every species will be asked for, so choose a study area or a place."
                : _chosen.Count == 1 ? "Observations of this species or group will be asked for."
                : "Observations of any of these " + _chosen.Count.ToString(CultureInfo.CurrentCulture) + " will be asked for.";
        }

        private void OnTaxonLink(object sender, RoutedEventArgs e)
        {
            if ((sender as FrameworkContentElement)?.DataContext is TaxonRow row)
            {
                OpenUrl(INatClient.Site + "taxa/" + row.Taxon.Id.ToString(CultureInfo.InvariantCulture));
            }
        }

        // =====================================================================
        //  where
        // =====================================================================
        private async void OnRefreshAreas(object sender, RoutedEventArgs e) => await RefreshAreasAsync();

        private async Task RefreshAreasAsync()
        {
            object keep = (AreaBox.SelectedItem as GbifActions.AreaChoice)?.Layer;
            int keepKind = (AreaBox.SelectedItem as GbifActions.AreaChoice)?.Kind ?? GbifActions.AreaChoice.KindWorld;
            List<GbifActions.AreaChoice> list;
            try
            {
                list = await INatActions.ListAreasAsync();
            }
            catch (Exception)
            {
                list = new List<GbifActions.AreaChoice> { GbifActions.AreaChoice.World() };
            }
            _filling = true;
            AreaBox.Items.Clear();
            foreach (GbifActions.AreaChoice a in list)
            {
                AreaBox.Items.Add(a);
            }
            // the study area the user chose is kept; a new window starts with none
            int pick = list.FindIndex(a => a.Kind == keepKind && ReferenceEquals(a.Layer, keep));
            AreaBox.SelectedIndex = pick < 0 ? 0 : pick;
            _filling = false;
            ShowAreaNote();
            _areaBoxKnown = false;
            Stale();
        }

        private DispatcherTimer _mapTimer;

        /// <summary>Called by the window when the map changed (a layer
        /// added or removed, another map, another selection): the study
        /// area list and its note are read again shortly after.</summary>
        internal void MapChanged()
        {
            Dispatcher.BeginInvoke(new Action(() =>
            {
                if (_mapTimer == null)
                {
                    _mapTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(400) };
                    _mapTimer.Tick += async (_, _) =>
                    {
                        _mapTimer.Stop();
                        if (AreaBox.IsDropDownOpen)
                        {
                            _mapTimer.Start();
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

        private void ShowAreaNote()
        {
            var a = AreaBox.SelectedItem as GbifActions.AreaChoice;
            AreaNote.Text = a == null ? "" : a.Kind == GbifActions.AreaChoice.KindWorld
                ? "No study area from the map. Choose a species or group, or a place on iNaturalist."
                : a.Note ?? "";
        }

        private void OnAreaChanged(object sender, SelectionChangedEventArgs e)
        {
            if (_filling)
            {
                return;
            }
            ShowAreaNote();
            _areaBoxKnown = false;
            Stale();
        }

        private void OnPlaceKey(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.Enter)
            {
                e.Handled = true;
                OnPlaceSearch(sender, e);
            }
        }

        private async void OnPlaceSearch(object sender, RoutedEventArgs e)
        {
            string text = PlaceBox.Text.Trim();
            if (text.Length == 0)
            {
                PlaceNote.Text = "Type the start of a place's name first.";
                return;
            }
            _placeCts?.Cancel();
            var cts = _placeCts = new CancellationTokenSource();
            PlaceNote.Text = "Asking iNaturalist...";
            try
            {
                List<INatPlace> list = await _inat.SearchPlacesAsync(text, cts.Token);
                if (cts.IsCancellationRequested)
                {
                    return;
                }
                _filling = true;
                PlaceList.Items.Clear();
                PlaceList.Items.Add(NoPlace);
                foreach (INatPlace p in list)
                {
                    PlaceList.Items.Add(p);
                }
                PlaceList.SelectedIndex = 0;
                _filling = false;
                PlaceNote.Text = list.Count == 0
                    ? "iNaturalist has no place whose name starts like that."
                    : list.Count.ToString(CultureInfo.CurrentCulture) + (list.Count == 1 ? " place matches." : " places match.")
                      + " Choose one in the list above. No place is chosen yet.";
                PlaceList.IsDropDownOpen = list.Count > 0;
                Stale();
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                PlaceNote.Text = Short(ex);
            }
        }

        private void OnPlaceChanged(object sender, SelectionChangedEventArgs e)
        {
            if (_filling)
            {
                return;
            }
            PlaceNote.Text = PlaceList.SelectedItem is INatPlace p
                ? "Observations inside iNaturalist's own boundary of " + p.DisplayName + ". With a study area from the map as well, "
                  + "the observations are those in both."
                : "No place is chosen. A place and a study area can be used together: the observations are then those in both.";
            Stale();
        }

        // =====================================================================
        //  the choices
        // =====================================================================
        private void OnChoiceChanged(object sender, RoutedEventArgs e)
        {
            if (!_filling)
            {
                Stale();
            }
        }

        /// <summary>The count and the species list no longer describe the
        /// choices: they are read again when their tab is next shown.</summary>
        private void Stale()
        {
            _countSig = null;
            SetOverLimit(false);
            if (CountText != null)
            {
                CountText.Text = "";
            }
        }

        private static readonly Regex YearOnly = new Regex(@"^\d{4}$");

        /// <summary>'2024' or '2024-05-01' as yyyy-MM-dd; null when empty;
        /// throws FormatException when it cannot be read.</summary>
        internal static string ReadDate(string text, bool end)
        {
            string s = (text ?? "").Trim();
            if (s.Length == 0)
            {
                return null;
            }
            if (YearOnly.IsMatch(s))
            {
                return s + (end ? "-12-31" : "-01-01");
            }
            if (DateTime.TryParseExact(s, new[] { "yyyy-MM-dd", "yyyy-M-d", "yyyy/MM/dd", "yyyy/M/d" },
                    CultureInfo.InvariantCulture, DateTimeStyles.None, out DateTime d))
            {
                return d.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            }
            throw new FormatException("The date \"" + s + "\" could not be read. Write it as YYYY-MM-DD, such as 2024-05-01, "
                + "or give just a year.");
        }

        /// <summary>Reads the tabs into a request. Returns null (after
        /// telling the user why, unless <paramref name="quiet"/>) when
        /// something typed cannot be used.</summary>
        private async Task<INatQuery> BuildQueryAsync(bool quiet)
        {
            var q = new INatQuery();
            try
            {
                q.TaxonIds = _chosen.Select(c => c.Taxon.Id).ToList();
                q.PlaceId = (PlaceList.SelectedItem as INatPlace)?.Id;
                q.Research = ResearchBox.IsChecked == true;
                q.NeedsId = NeedsIdBox.IsChecked == true;
                q.Casual = CasualBox.IsChecked == true;
                if (!q.Research && !q.NeedsId && !q.Casual)
                {
                    throw new FormatException("Check at least one quality grade on tab 2.");
                }
                q.ExcludeCaptive = CaptiveBox.IsChecked == true;
                q.DateFrom = ReadDate(DateFromBox.Text, false);
                q.DateTo = ReadDate(DateToBox.Text, true);
                if (q.DateFrom != null && q.DateTo != null && string.CompareOrdinal(q.DateFrom, q.DateTo) > 0)
                {
                    throw new FormatException("The first date is later than the last date.");
                }
                q.Months = Enumerable.Range(1, 12).Where(m => _months[m - 1].IsChecked == true).ToList();
                q.OpenLicenseOnly = OpenLicenseBox.IsChecked == true;
                q.ExcludeNonCommercial = CommercialBox.IsChecked == true;
                q.PhotosOnly = PhotosBox.IsChecked == true;
                q.ExcludeObscured = ObscuredBox.IsChecked == true;
                string acc = AccuracyBox.Text.Trim();
                if (acc.Length > 0)
                {
                    if (!double.TryParse(acc, NumberStyles.Float, CultureInfo.CurrentCulture, out double m) || m <= 0)
                    {
                        throw new FormatException("The accuracy must be a number of meters greater than zero, or empty.");
                    }
                    q.MaxUncertaintyMeters = m;
                }
                q.Establishment = EstablishmentBox.SelectedItem as string ?? "Any";
                q.Observer = ObserverBox.Text.Trim();
                q.Project = ProjectBox.Text.Trim();

                var area = AreaBox.SelectedItem as GbifActions.AreaChoice;
                if (area != null && area.Kind != GbifActions.AreaChoice.KindWorld)
                {
                    // the map's extent changes as the user pans, so it is read every time
                    if (!_areaBoxKnown || area.Kind == GbifActions.AreaChoice.KindExtent)
                    {
                        _areaBox = await INatActions.AreaBoxAsync(area);
                        _areaBoxKnown = true;
                    }
                    if (!_areaBox.HasValue)
                    {
                        throw new FormatException("The study area \"" + area.Label + "\" has no polygons to use.");
                    }
                    q.Box = _areaBox;
                }
                if (!q.HasScope)
                {
                    throw new FormatException("Choose at least one species or group, a study area or a place on tab 1 "
                        + "(or an observer or a project on tab 2). With none of them, the request is every observation "
                        + "iNaturalist holds.");
                }
                return q;
            }
            catch (Exception ex)
            {
                if (quiet)
                {
                    SetStatus(ex is FormatException ? ex.Message : Short(ex));
                }
                else
                {
                    Say(ex is FormatException ? ex.Message : Short(ex));
                }
                return null;
            }
        }

        private bool AreaIsLayer =>
            (AreaBox.SelectedItem as GbifActions.AreaChoice)?.Kind == GbifActions.AreaChoice.KindLayer;

        /// <summary>The choices in plain words.</summary>
        private string Describe(INatQuery q)
        {
            var parts = new List<string>();
            parts.Add(_chosen.Count == 0 ? "every species" : string.Join("; ", _chosen.Select(c => c.Taxon.Name)));
            var area = AreaBox.SelectedItem as GbifActions.AreaChoice;
            if (area != null && area.Kind == GbifActions.AreaChoice.KindLayer)
            {
                parts.Add("in the polygons of " + area.Label);
            }
            else if (area != null && area.Kind == GbifActions.AreaChoice.KindExtent)
            {
                parts.Add("in the area the map shows");
            }
            if (PlaceList.SelectedItem is INatPlace p)
            {
                parts.Add("in " + p.DisplayName);
            }
            var grades = new List<string>();
            if (q.Research) grades.Add("research grade");
            if (q.NeedsId) grades.Add("needs ID");
            if (q.Casual) grades.Add(q.ExcludeCaptive ? "casual (not captive or cultivated)" : "casual");
            parts.Add(string.Join(", ", grades));
            if (q.DateFrom != null || q.DateTo != null)
            {
                parts.Add("observed " + (q.DateFrom ?? "the earliest") + " to " + (q.DateTo ?? "the latest"));
            }
            if (q.Months.Count > 0 && q.Months.Count < 12)
            {
                parts.Add("in " + string.Join(", ", q.Months.Select(m => MonthNames[m - 1])));
            }
            if (q.ExcludeObscured) parts.Add("no obscured locations");
            if (q.MaxUncertaintyMeters.HasValue) parts.Add("stated accuracy under " + INatQuery.Num(q.MaxUncertaintyMeters.Value) + " m");
            if (q.ExcludeNonCommercial) parts.Add("licenses that allow commercial use");
            else if (q.OpenLicenseOnly) parts.Add("CC0 or Creative Commons licenses");
            if (q.PhotosOnly) parts.Add("with a photograph");
            if (q.Establishment != "Any") parts.Add(q.Establishment.ToLowerInvariant() + " species");
            if (!string.IsNullOrEmpty(q.Observer)) parts.Add("observer " + q.Observer);
            if (!string.IsNullOrEmpty(q.Project)) parts.Add("project " + q.Project);
            return string.Join("; ", parts) + ".";
        }

        // =====================================================================
        //  tabs 3 and 4: what the choices give
        // =====================================================================
        private async void OnTabChanged(object sender, SelectionChangedEventArgs e)
        {
            if (!ReferenceEquals(e.OriginalSource, Tabs) || !_loadedOnce)
            {
                return;
            }
            await RefreshOpenTabAsync();
        }

        private async Task RefreshOpenTabAsync()
        {
            if (Tabs.SelectedIndex == 2)
            {
                await CheckSpeciesListAsync();
            }
            else if (Tabs.SelectedIndex == 3)
            {
                await CountAsync();
            }
        }

        private async void OnCount(object sender, RoutedEventArgs e)
        {
            _countSig = null;
            await CountAsync();
        }

        private async Task CountAsync()
        {
            SetStatus(null);
            INatQuery q = await BuildQueryAsync(true);
            if (q == null)
            {
                RequestText.Text = "The choices on tabs 1 and 2 are not complete: see the message at the bottom of the window.";
                CountText.Text = "";
                return;
            }
            RequestText.Text = Describe(q);
            string sig = INatQuery.QueryString(q.Params());
            if (sig == _countSig)
            {
                return;
            }
            _countCts?.Cancel();
            var cts = _countCts = new CancellationTokenSource();
            CountText.Text = "Counting...";
            SetOverLimit(false);
            try
            {
                long n = await _inat.CountAsync(q, cts.Token);
                if (cts.IsCancellationRequested)
                {
                    return;
                }
                _countSig = sig;
                SetOverLimit(n > MaxObservations);
                string where = AreaIsLayer ? " in the rectangle around the study area" : "";
                CountText.Text = n == 0 ? "iNaturalist has no observations that match" + where + "."
                    : (n == 1 ? "iNaturalist has 1 observation that matches" : "iNaturalist has " + N0(n) + " observations that match")
                      + where + "."
                      + (n > MaxObservations ? " That is more than the " + N0(MaxObservations) + " this tool fetches in one run, so "
                            + "the Get the observations button is greyed out. The limit respects iNaturalist, which says "
                            + "its service is not meant for copying its data in bulk, and it protects you: iNaturalist asks each user "
                            + "to make fewer than 10,000 requests a day (2,000,000 observations) and may block a computer that goes "
                            + "beyond that. Narrow the species, the area, the dates or the grades, or use the GBIF Species "
                            + "Occurrences window, which can request a download of any size."
                         : n > LargeRequest ? " That is a large request: fetching them will take about " + Span(n / 200.0 * SecondsPerPage)
                            + ". Narrowing the species, the area, the dates or the grades shortens it; for research-grade "
                            + "observations in bulk, the GBIF Species Occurrences window can request a download of any size."
                         : n > 2000 ? " Fetching them will take about " + Span(n / 200.0 * SecondsPerPage) + "." : "");
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                CountText.Text = Short(ex);
            }
        }

        /// <summary>When tab 3 is shown: a list made for other choices is
        /// taken down, so it never describes choices that have changed.</summary>
        private async Task CheckSpeciesListAsync()
        {
            if (_species.Count == 0 && _speciesSig == null)
            {
                return;
            }
            INatQuery q = await BuildQueryAsync(true);
            string sig = q == null ? null : INatQuery.QueryString(q.Params());
            if (sig != _speciesSig)
            {
                _species.Clear();
                ClearPhotos();
                _speciesSig = null;
                SpeciesMoreButton.IsEnabled = false;
                SpeciesNote.Text = "The choices on tabs 1 and 2 have changed since the list was made. Click List the species to make it again.";
            }
        }

        /// <summary>A group checked or unchecked: the list is made again for the groups now checked.</summary>
        private void OnGroupToggled(object sender, RoutedEventArgs e) => OnListSpecies(sender, e);

        private async void OnListSpecies(object sender, RoutedEventArgs e)
        {
            _species.Clear();
            ClearPhotos();
            _speciesPage = 1;
            _speciesSig = null;
            await SpeciesPageAsync();
        }

        private async void OnMoreSpecies(object sender, RoutedEventArgs e)
        {
            _speciesPage++;
            await SpeciesPageAsync();
        }

        private async Task SpeciesPageAsync()
        {
            SetStatus(null);
            INatQuery q = await BuildQueryAsync(false);
            if (q == null)
            {
                return;
            }
            _speciesCts?.Cancel();
            var cts = _speciesCts = new CancellationTokenSource();
            SpeciesNote.Text = "Asking iNaturalist...";
            SpeciesButton.IsEnabled = false;
            SpeciesMoreButton.IsEnabled = false;
            try
            {
                List<CheckBox> only = _groups.Where(g => g.IsChecked == true).ToList();
                var (list, total) = await _inat.SpeciesAsync(q, _speciesPage, cts.Token,
                    only.Select(g => (string)g.Tag).ToList());
                if (cts.IsCancellationRequested)
                {
                    return;
                }
                _speciesTotal = total;
                _speciesSig = INatQuery.QueryString(q.Params());
                foreach (var (taxon, count) in list)
                {
                    _species.Add(new TaxonRow(taxon, count));
                }
                string where = AreaIsLayer ? " in the rectangle around the study area" : "";
                SpeciesNote.Text = _species.Count == 0 ? "No observations match" + where + ", so there are no species to list."
                    : N0(_speciesTotal) + (_speciesTotal == 1 ? " species has" : " species have") + " matching observations" + where
                      + (_species.Count < _speciesTotal ? "; showing the " + N0(_species.Count) + " most observed." : ".");
                if (only.Count > 0)
                {
                    SpeciesNote.Text += " Listing only: " + string.Join(", ", only.Select(g => (string)g.Content)) + ".";
                }
                SpeciesMoreButton.IsEnabled = _species.Count < _speciesTotal && list.Count > 0;
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                SpeciesNote.Text = Short(ex);
            }
            finally
            {
                SpeciesButton.IsEnabled = true;
            }
        }

        private void OnSaveSpecies(object sender, RoutedEventArgs e)
        {
            if (_species.Count == 0)
            {
                Say("There is no species list to save yet. Click List the species first.");
                return;
            }
            var dlg = new Microsoft.Win32.SaveFileDialog
            {
                Title = "Save the species list",
                Filter = "CSV file (*.csv)|*.csv",
                FileName = "iNaturalist_species.csv",
            };
            if (dlg.ShowDialog(Window.GetWindow(this)) != true)
            {
                return;
            }
            try
            {
                var sb = new StringBuilder();
                sb.AppendLine("scientificName,commonName,rank,group,observations,inatTaxonID");
                foreach (TaxonRow r in _species)
                {
                    sb.AppendLine(string.Join(",", Csv(r.Name), Csv(r.Common), Csv(r.Rank), Csv(r.Group),
                        r.Count.ToString(CultureInfo.InvariantCulture), r.Taxon.Id.ToString(CultureInfo.InvariantCulture)));
                }
                File.WriteAllText(dlg.FileName, sb.ToString(), new UTF8Encoding(true));
                SetStatus("Saved " + N0(_species.Count) + " species to " + dlg.FileName + ".");
            }
            catch (Exception ex)
            {
                Say("The list could not be saved: " + ex.Message);
            }
        }

        private static string Csv(string s) =>
            s == null ? "" : s.IndexOfAny(new[] { ',', '"', '\n', '\r' }) >= 0 ? "\"" + s.Replace("\"", "\"\"") + "\"" : s;

        // =====================================================================
        //  getting the observations
        // =====================================================================
        private async void OnGet(object sender, RoutedEventArgs e)
        {
            if (_busy)
            {
                return;
            }
            // Busy from here, BEFORE the first await. Reading a polygon study area takes a moment, and a
            // second click on Get in that moment would otherwise pass the test above too and start a second
            // run, with Cancel reaching only one of the two.
            SetBusy(true);
            var cts = _workCts = new CancellationTokenSource();
            try
            {
                INatQuery q = await BuildQueryAsync(false);
                if (q == null || cts.IsCancellationRequested)
                {
                    return;
                }
                string output = OutputBox.Text.Trim().Trim('"');
                if (output.Length == 0)
                {
                    Say("Give the output feature class, inside a file geodatabase.");
                    return;
                }
                SetStatus(null);
                LogBox.Document.Blocks.Clear();
                Step(1, "Starting the geoprocessing tool...");
                _toolMessages = 0;
                _toolSaidCancelled = false;
                var request = new INatActions.ToolRequest
                {
                    Taxa = _chosen.Select(c => c.Taxon.Entry).ToList(),
                    Area = AreaBox.SelectedItem as GbifActions.AreaChoice,
                    Place = (PlaceList.SelectedItem as INatPlace)?.Entry,
                    Query = q,
                    OutputPath = output,
                };
                var result = await INatActions.RunToolAsync(request,
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
                    })), cts.Token);
                // let the messages still queued for this thread be shown first
                await Dispatcher.InvokeAsync(() => { }, DispatcherPriority.Background);
                if (_toolMessages == 0)
                {
                    foreach (var m in result.Messages)
                    {
                        OnToolMessage(m.Text ?? "", INatActions.Severity(m));
                    }
                }
                if (result.IsCanceled || cts.IsCancellationRequested)
                {
                    if (!_toolSaidCancelled)
                    {
                        Log("Cancelled.");
                    }
                    Step(0, "Cancelled.");
                }
                else if (result.IsFailed)
                {
                    Step(0, "Stopped. The report below says why.");
                }
                else
                {
                    Log("This run is recorded in the Geoprocessing History (Analysis tab, History). Right-click its entry there "
                        + "and choose Copy Python Command to repeat the same request from a script. ArcGIS Pro does not record "
                        + "where the toolbox is for a run started from a window, so the first line of the copied command names "
                        + "the wrong toolbox. Replace that line with this one:");
                    Log("    arcpy.ImportToolbox(r\"" + INatActions.ToolboxPath + "\")");
                    Step(100, "Done.");
                }
            }
            catch (OperationCanceledException)
            {
                Log("Cancelled.");
                Step(0, "Cancelled.");
            }
            catch (Exception ex)
            {
                Log("Stopped: " + Short(ex));
                Step(0, "Stopped: " + Short(ex));
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
            GetButton.IsEnabled = !busy && !_overLimit;
            CancelButton.IsEnabled = busy;
        }

        /// <summary>Above the tool's limit the Get button is greyed out,
        /// and its hover help says why.</summary>
        private void SetOverLimit(bool over)
        {
            if (GetButton == null)
            {
                return;
            }
            if (_getToolTip == null)
            {
                _getToolTip = GetButton.ToolTip;
            }
            _overLimit = over;
            GetButton.IsEnabled = !_busy && !over;
            GetButton.ToolTip = over ? (object)OverLimitToolTip() : _getToolTip;
        }

        private static ToolTip OverLimitToolTip() => new ToolTip
        {
            Content = new TextBlock
            {
                MaxWidth = 420,
                TextWrapping = TextWrapping.Wrap,
                Text = "Greyed out because more than " + N0(MaxObservations) + " observations match, which is the most "
                    + "this tool fetches in one run. The limit respects iNaturalist, which says its service is not meant "
                    + "for copying its data in bulk. It also protects you: iNaturalist asks each user to make fewer than "
                    + "10,000 requests a day, which is 2,000,000 observations, and may block a computer that goes beyond "
                    + "that. Narrow the species, the area, the dates or the grades on tabs 1 and 2, then count again."
            }
        };

        // lines of every geoprocessing run that say nothing about this one
        private static readonly string[] ToolBoilerplate =
        {
            "Start Time:", "Succeeded at", "Failed at", "Failed to execute", "Executing:", "Executing (",
            "View online help files:", "To run this tool from a script", "Running script", "Completed script",
            "Canceled function", "Cancelled function", "import arcpy", "arcpy.ImportToolbox(", "arcpy.jenness.",
        };

        private void OnToolMessage(string text, int severity)
        {
            _toolMessages++;
            if (string.IsNullOrWhiteSpace(text)
                || ToolBoilerplate.Any(b => text.TrimStart().StartsWith(b, StringComparison.Ordinal))
                || text.TrimEnd().EndsWith("aborted by User.", StringComparison.OrdinalIgnoreCase))
            {
                return;
            }
            if (text.TrimStart().StartsWith("Cancelled", StringComparison.Ordinal))
            {
                _toolSaidCancelled = true;
            }
            Log(severity == 2 ? "Stopped: " + text.Trim() : severity == 1 ? "Note: " + text.Trim() : text.TrimEnd());
        }

        // =====================================================================
        //  small things
        // =====================================================================
        private static string N0(long n) => n.ToString("N0", CultureInfo.CurrentCulture);

        internal static string Span(double seconds)
        {
            int s = (int)Math.Round(seconds);
            if (s < 90)
            {
                return s.ToString(CultureInfo.CurrentCulture) + " seconds";
            }
            int m = (int)Math.Round(s / 60.0);
            return m < 90 ? m.ToString(CultureInfo.CurrentCulture) + " minutes"
                : (s / 3600.0).ToString("0.0", CultureInfo.CurrentCulture) + " hours";
        }

        private void Step(double percent, string text)
        {
            Progress.Value = Math.Max(0, Math.Min(100, percent));
            ProgressText.Text = text;
        }

        /// <summary>Adds one item to the report: a bulleted paragraph with a
        /// hanging indent. A line that starts with spaces is a detail of
        /// the item above it: indented, no bullet.</summary>
        private void Log(string line)
        {
            bool detail = line.StartsWith("    ", StringComparison.Ordinal);
            var para = new System.Windows.Documents.Paragraph
            {
                Margin = detail ? new Thickness(34, 0, 0, 1) : new Thickness(16, LogBox.Document.Blocks.Count == 0 ? 0 : 8, 0, 0),
                TextIndent = detail ? 0 : -14,
                LineHeight = double.NaN,
            };
            para.Inlines.Add(new System.Windows.Documents.Run(detail ? line.Trim() : "•  " + line));
            LogBox.Document.Blocks.Add(para);
            LogBox.ScrollToEnd();
        }

        /// <summary>The report as plain text (for tests).</summary>
        internal string LogText() =>
            new System.Windows.Documents.TextRange(LogBox.Document.ContentStart, LogBox.Document.ContentEnd).Text;

        private void SetStatus(string message)
        {
            StatusText.Text = message ?? "";
            StatusText.Visibility = string.IsNullOrEmpty(message) ? Visibility.Collapsed : Visibility.Visible;
        }

        private void Say(string message)
        {
            SetStatus(message);
            try
            {
                INatActions.Info(message);
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
                return "iNaturalist did not answer in time. Try again.";
            }
            if (ex is System.Net.Http.HttpRequestException)
            {
                return "iNaturalist could not be reached. Check the internet connection.";
            }
            return ex.Message;
        }

        // =====================================================================
        //  photographs of one species (tab 3)
        // =====================================================================
        private const int PhotoBatch = 6;

        private void ClearPhotos()
        {
            _photoCts?.Cancel();
            _photoTaxon = null;
            if (PhotoPanel != null)
            {
                PhotoPanel.Children.Clear();
                MorePhotosButton.IsEnabled = false;
                PhotoNote.Text = "Click \"show\" beside a species to see photographs of it here.";
            }
        }

        private async void OnSpeciesPhotosLink(object sender, RoutedEventArgs e)
        {
            // the row the link sits in, whatever row is highlighted
            if ((sender as FrameworkContentElement)?.DataContext is TaxonRow row)
            {
                SpeciesGrid.SelectedItem = row;
                _photoTaxon = row.Taxon;
                _photoPage = 0;
                _photoShown = 0;
                PhotoPanel.Children.Clear();
                await LoadPhotosAsync();
            }
        }

        private async void OnMorePhotos(object sender, RoutedEventArgs e) => await LoadPhotosAsync();

        private async Task LoadPhotosAsync()
        {
            INatTaxon taxon = _photoTaxon;
            if (taxon == null)
            {
                return;
            }
            INatQuery q = await BuildQueryAsync(false);
            if (q == null)
            {
                return;
            }
            _photoCts?.Cancel();
            var cts = _photoCts = new CancellationTokenSource();
            MorePhotosButton.IsEnabled = false;
            PhotoNote.Text = "Asking iNaturalist...";
            try
            {
                var (photos, total) = await _inat.PhotosAsync(q, taxon.Id, _photoPage + 1, PhotoBatch, cts.Token);
                if (cts.IsCancellationRequested)
                {
                    return;
                }
                _photoPage++;
                foreach (INatPhoto p in photos)
                {
                    var img = new Image { Width = 180, Height = 135, Stretch = System.Windows.Media.Stretch.Uniform };
                    string who = string.IsNullOrEmpty(p.Observer) ? "unknown observer" : p.Observer;
                    var credit = new TextBlock
                    {
                        Text = who + "\n" + p.LicenseWords,
                        TextWrapping = TextWrapping.Wrap,
                        Width = 180,
                        FontSize = 11,
                        Opacity = 0.85,
                    };
                    var stack = new StackPanel { Margin = new Thickness(0, 0, 10, 0), Cursor = Cursors.Hand };
                    stack.Children.Add(img);
                    stack.Children.Add(credit);
                    long id = p.ObservationId;
                    stack.ToolTip = (string.IsNullOrEmpty(p.Attribution) ? "Photograph by " + who : p.Attribution)
                        + (string.IsNullOrEmpty(p.ObservedOn) ? "" : ". Observed on " + p.ObservedOn)
                        + ". Click to open the observation on iNaturalist.";
                    stack.MouseLeftButtonUp += (_, _) =>
                        OpenUrl(INatClient.Site + "observations/" + id.ToString(CultureInfo.InvariantCulture));
                    PhotoPanel.Children.Add(stack);
                    _ = LoadThumbAsync(img, p.Sized("small"), cts.Token);
                }
                _photoShown += photos.Count;
                string where = AreaIsLayer ? " in the rectangle around the study area" : "";
                PhotoNote.Text = total == 0
                    ? "No matching observations of " + taxon.Name + where + " have a photograph."
                    : taxon.Name + ": " + N0(total) + (total == 1 ? " matching observation" : " matching observations") + where
                      + (total == 1 ? " has a photograph" : " have photographs") + "; showing the first photograph of "
                      + N0(_photoShown) + ", newest first. Click a picture to open its observation on iNaturalist.";
                MorePhotosButton.IsEnabled = photos.Count > 0 && _photoShown < total;
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                PhotoNote.Text = Short(ex);
            }
        }

        private async Task LoadThumbAsync(Image img, string url, CancellationToken ct)
        {
            try
            {
                byte[] bytes = await _inat.GetBytesAsync(url, ct);
                var bmp = new System.Windows.Media.Imaging.BitmapImage();
                bmp.BeginInit();
                bmp.CacheOption = System.Windows.Media.Imaging.BitmapCacheOption.OnLoad;
                bmp.StreamSource = new MemoryStream(bytes);
                bmp.EndInit();
                bmp.Freeze();
                img.Source = bmp;
            }
            catch (Exception)
            {
                // a missing picture leaves an empty frame; the credit still shows
            }
        }

        private static void OpenUrl(string url)
        {
            if (string.IsNullOrEmpty(url))
            {
                return;
            }
            try
            {
                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(url) { UseShellExecute = true });
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

        private void OnHelp(object sender, RoutedEventArgs e)
        {
            try
            {
                INatActions.Help();
            }
            catch (Exception)
            {
            }
        }

        // Browse: choose where an output goes, with ArcGIS Pro's own dialog
        private void OnBrowseOutput(object sender, RoutedEventArgs e)
        {
            try
            {
                string path = INatActions.BrowseOutput(OutputBox.Text);
                if (!string.IsNullOrEmpty(path))
                {
                    OutputBox.Text = path;
                }
            }
            catch (Exception)
            {
                // outside ArcGIS Pro there is no such dialog: the path is typed
            }
        }

        private void OnClose(object sender, RoutedEventArgs e) => CloseRequested?.Invoke(this, EventArgs.Empty);

        /// <summary>One species or group in a list.</summary>
        internal sealed class TaxonRow
        {
            public INatTaxon Taxon { get; }
            public long Count { get; }
            public TaxonRow(INatTaxon t, long count) { Taxon = t; Count = count; }
            public string Name => Taxon.Name;
            public string Common => Taxon.Common;
            public string Rank => Taxon.Rank;
            public string Group => Taxon.Group;
            public string CountText => Count >= 0 ? Count.ToString("N0", CultureInfo.CurrentCulture) : "";
            public string Display => Taxon.Name + (string.IsNullOrEmpty(Taxon.Common) ? "" : "  (" + Taxon.Common + ")")
                + (string.IsNullOrEmpty(Taxon.Rank) ? "" : "  —  " + Taxon.Rank);
        }
    }
}
