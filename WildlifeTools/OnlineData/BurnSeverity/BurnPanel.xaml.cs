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

using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Threading;
using WildlifeTools.OnlineData.Gbif;

namespace WildlifeTools.OnlineData.BurnSeverity
{
    /// <summary>
    /// The content of a Burn Severity window, for one program (MTBS, RAVG
    /// or BAER). Tab 1 lists the fires that match a name, years and a
    /// study area, straight from the Burn Severity Portal's catalog; the
    /// user checks, unchecks and adds to that list. Tab 2 runs the
    /// program's geoprocessing tool for exactly the checked fires.
    /// </summary>
    public partial class BurnPanel : UserControl
    {
        /// <summary>What differs between the three programs.</summary>
        private sealed class ProgramInfo
        {
            public string About;
            public string[] Products;           // exactly the tool's product names
            public string ProductNote;
        }

        private static readonly Dictionary<string, ProgramInfo> Programs = new Dictionary<string, ProgramInfo>
        {
            ["MTBS"] = new ProgramInfo
            {
                About = "Monitoring Trends in Burn Severity (MTBS), of the U.S. Geological Survey and the USDA Forest Service, "
                    + "maps every fire of at least 1,000 acres in the western United States and 500 acres in the east, on all "
                    + "ownerships, since 1984. Its burn severity classes are set for each fire by an analyst from the change in "
                    + "the Normalized Burn Ratio (dNBR), so a class does not mean the same amount of change in every fire.",
                Products = new[] { "Thematic burn severity (6 classes)" },
                ProductNote = "The image services carry MTBS's yearly national mosaics. A fire mapped after its year's mosaic was "
                    + "made has its raster only in its data bundle; the report says which fires those are.",
            },
            ["RAVG"] = new ProgramInfo
            {
                About = "Rapid Assessment of Vegetation Condition after Wildfire (RAVG), of the USDA Forest Service, maps fires "
                    + "that burn at least 1,000 acres of forested National Forest System land. It estimates the percent of basal "
                    + "area lost, the percent of canopy cover lost and the composite burn index, with regression models built from "
                    + "field plots. The models are calibrated for forest and do not hold in other vegetation.",
                Products = new[] { "Basal area loss (7 classes)", "Canopy cover loss (5 classes)", "Composite burn index (4 classes)" },
                ProductNote = "The image service carries RAVG's mosaics for 2012 to 2023 only. Fires of other years have their "
                    + "rasters only in their data bundles.",
            },
            ["BAER"] = new ProgramInfo
            {
                About = "The Burned Area Emergency Response (BAER) imagery support program, of the USDA Forest Service and the "
                    + "U.S. Geological Survey, maps fires for BAER teams within days of containment. The team checks the first map "
                    + "in the field and produces the soil burn severity map, which describes the fire's effect on the soil and "
                    + "ground cover, not on the vegetation canopy.",
                Products = new[] { "Soil burn severity (4 classes)" },
                ProductNote = "The image service carries the soil burn severity the Forest Service has published, from 2017, and not "
                    + "for every fire. A fire's data bundle may have it when the service does not, but for some fires none "
                    + "was published.",
            },
        };

        private readonly BurnClient _client = new BurnClient();
        private readonly ObservableCollection<FireRow> _fires = new ObservableCollection<FireRow>();
        private readonly List<CheckBox> _products = new List<CheckBox>();
        private CancellationTokenSource _searchCts, _workCts;
        private bool _busy, _loadedOnce, _filling;
        private int _toolMessages;
        private bool _toolSaidCancelled;
        private DispatcherTimer _mapTimer;

        /// <summary>MTBS, RAVG or BAER. Set by the window before it is shown.</summary>
        public string Program { get; private set; } = "MTBS";

        public event EventHandler CloseRequested;

        public BurnPanel()
        {
            InitializeComponent();
            FireGrid.ItemsSource = _fires;
            _filling = true;
            AreaBox.Items.Add(GbifActions.AreaChoice.World());
            AreaBox.SelectedIndex = 0;
            _filling = false;
            SetProgram("MTBS");
            Loaded += OnLoaded;
            Unloaded += (_, _) =>
            {
                _searchCts?.Cancel();
                _workCts?.Cancel();
            };
        }

        /// <summary>Makes this the panel of one program.</summary>
        public void SetProgram(string program)
        {
            Program = Programs.ContainsKey(program) ? program : "MTBS";
            ProgramInfo info = Programs[Program];
            AboutText.Text = info.About;
            ProductNote.Text = info.ProductNote;
            ProductPanel.Children.Clear();
            _products.Clear();
            foreach (string p in info.Products)
            {
                var box = new CheckBox { Content = p, IsChecked = true, Margin = new Thickness(0, 0, 0, 3) };
                _products.Add(box);
                ProductPanel.Children.Add(box);
            }
            ShowChecked();
        }

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
                PerimetersBox.Text = await BurnActions.DefaultOutputPathAsync(Program + "_fires");
            }
            catch (Exception)
            {
                // outside Pro, or no project: the user types the path
            }
        }

        // =====================================================================
        //  the study area
        // =====================================================================
        private async void OnRefreshAreas(object sender, RoutedEventArgs e) => await RefreshAreasAsync();

        private async Task RefreshAreasAsync()
        {
            object keep = (AreaBox.SelectedItem as GbifActions.AreaChoice)?.Layer;
            int keepKind = (AreaBox.SelectedItem as GbifActions.AreaChoice)?.Kind ?? GbifActions.AreaChoice.KindWorld;
            List<GbifActions.AreaChoice> list;
            try
            {
                list = await BurnActions.ListAreasAsync();
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
            int pick = list.FindIndex(a => a.Kind == keepKind && ReferenceEquals(a.Layer, keep));
            AreaBox.SelectedIndex = pick < 0 ? 0 : pick;
            _filling = false;
            ShowAreaNote();
        }

        /// <summary>Called by the window when the map changed: the study
        /// area list is read again shortly after. The list of fires is not
        /// touched: it changes only when the user asks.</summary>
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
                ? "No study area: fires anywhere. Give a fire name or years."
                : a.Note ?? "";
        }

        private void OnAreaChanged(object sender, SelectionChangedEventArgs e)
        {
            if (!_filling)
            {
                ShowAreaNote();
            }
        }

        // =====================================================================
        //  listing the fires
        // =====================================================================
        private void OnSearchKey(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.Enter)
            {
                e.Handled = true;
                OnList(sender, e);
            }
        }

        private async void OnList(object sender, RoutedEventArgs e) => await SearchAsync(false);

        private async void OnAddToList(object sender, RoutedEventArgs e) => await SearchAsync(true);

        private static int? Year(TextBox box, string what, out string problem)
        {
            problem = null;
            string s = box.Text.Trim();
            if (s.Length == 0)
            {
                return null;
            }
            // A LAST year later than this one is accepted (typing 9999 is the easy way to say "through the
            // latest fires"): the search clamps it and says which years were not available.
            bool last = what == "last";
            if (!Regex.IsMatch(s, @"^\d{4}$") || int.Parse(s, CultureInfo.InvariantCulture) < 1984
                || (!last && int.Parse(s, CultureInfo.InvariantCulture) > DateTime.Now.Year + 1))
            {
                problem = last
                    ? "The last year must be 1984 or later."
                    : "The first year must be a year from 1984 to " + DateTime.Now.Year.ToString(CultureInfo.InvariantCulture) + ".";
                return null;
            }
            return int.Parse(s, CultureInfo.InvariantCulture);
        }

        /// <summary>The sentence that says which years asked for are later than this year, or "".</summary>
        private string FutureYears(int asked, int thisYear)
        {
            if (asked <= thisYear)
            {
                return "";
            }
            string a = (thisYear + 1).ToString(CultureInfo.InvariantCulture), b = asked.ToString(CultureInfo.InvariantCulture);
            return " Fires were asked for through " + b + ", but " + (asked == thisYear + 1 ? "the year " + a + " is" : "the years " + a + " to " + b + " are")
                + " not in the " + Program + " catalog yet, so the list is through "
                + thisYear.ToString(CultureInfo.InvariantCulture) + ".";
        }

        private async Task SearchAsync(bool add)
        {
            SetStatus(null);
            string name = NameBox.Text.Trim();
            int? y0 = Year(YearFromBox, "first", out string p0);
            int? y1 = Year(YearToBox, "last", out string p1);
            if (p0 != null || p1 != null)
            {
                Say(p0 ?? p1);
                return;
            }
            if (y0.HasValue && y1.HasValue && y0 > y1)
            {
                Say("The first year is later than the last year.");
                return;
            }
            int thisYear = DateTime.Now.Year;
            string futureNote = y1.HasValue ? FutureYears(y1.Value, thisYear) : "";
            if (y1.HasValue && y1.Value > thisYear)
            {
                y1 = thisYear;
            }
            var area = AreaBox.SelectedItem as GbifActions.AreaChoice;
            bool hasArea = area != null && area.Kind != GbifActions.AreaChoice.KindWorld;
            if (name.Length == 0 && !y0.HasValue && !y1.HasValue && !hasArea)
            {
                Say("Give at least a fire name, a year or a study area. With none of them the list would be every fire "
                    + Program + " has mapped.");
                return;
            }
            _searchCts?.Cancel();
            var cts = _searchCts = new CancellationTokenSource();
            ListButton.IsEnabled = false;
            AddButton.IsEnabled = false;
            ListNote.Text = "Asking the fire catalog...";
            try
            {
                (double West, double South, double East, double North)? box = null;
                List<List<List<(double Lon, double Lat)>>> areaPolygons = null;
                if (hasArea)
                {
                    // read ONCE: the rectangle around it is what the catalog is asked for, and the polygons
                    // themselves are what each fire is tested against below
                    areaPolygons = await BurnActions.AreaPolygonsAsync(area);
                    box = BurnActions.BoxOf(areaPolygons);
                    if (box == null)
                    {
                        ListNote.Text = "The study area has no polygons to use.";
                        return;
                    }
                }
                bool polygons = hasArea && area.Kind == GbifActions.AreaChoice.KindLayer;
                List<BurnFire> found = await _client.SearchAsync(Program, name, y0, y1, box, polygons, cts.Token);
                if (cts.IsCancellationRequested)
                {
                    return;
                }
                int inRectangle = found.Count;
                if (polygons && found.Count > 0)
                {
                    // the catalog searches by rectangle: keep the fires that touch the polygons themselves
                    found = await BurnActions.TouchingAsync(areaPolygons, found);
                }
                if (!add)
                {
                    _fires.Clear();
                }
                var have = new HashSet<string>(_fires.Select(f => f.Key));
                int added = 0;
                foreach (BurnFire f in found)
                {
                    var row = new FireRow(f);
                    if (have.Add(row.Key))
                    {
                        _fires.Add(row);
                        added++;
                    }
                }
                string what = string.Join("; ", new[]
                {
                    name.Length > 0 ? "name contains \"" + name + "\"" : null,
                    y0.HasValue || y1.HasValue ? (y0 == y1 ? y0.ToString() : (y0?.ToString() ?? "the start") + " to " + (y1?.ToString() ?? "now")) : null,
                    hasArea ? "touching " + area.Label : null,
                }.Where(s => s != null));
                ListNote.Text = found.Count == 0
                    ? "No " + Program + " fire matches (" + what + ")." + (add ? " The list is unchanged." : "")
                    : (found.Count == 1 ? "1 " + Program + " fire matches" : N0(found.Count) + " " + Program + " fires match")
                      + " (" + what + ")"
                      + (polygons && inRectangle > found.Count ? "; " + N0(inRectangle - found.Count)
                            + " more in the rectangle around the study area do not touch it and are not listed" : "")
                      + (add ? "; " + N0(added) + " added to the list." : ".")
                      + (found.Count >= BurnClient.Most ? " Only the first " + N0(BurnClient.Most) + " are listed: narrow the search." : "");
                ListNote.Text += futureNote;
                ShowChecked();
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                ListNote.Text = Short(ex);
            }
            finally
            {
                ListButton.IsEnabled = true;
                AddButton.IsEnabled = true;
            }
        }

        private void OnClearList(object sender, RoutedEventArgs e)
        {
            _searchCts?.Cancel();
            _fires.Clear();
            ListNote.Text = "The list is empty. Give a fire name, years or a study area, then click List the fires.";
            SetStatus(null);
            ShowChecked();
        }

        private void OnCheckAll(object sender, RoutedEventArgs e)
        {
            foreach (FireRow r in _fires)
            {
                r.Checked = true;
            }
            ShowChecked();
        }

        private void OnUncheckAll(object sender, RoutedEventArgs e)
        {
            foreach (FireRow r in _fires)
            {
                r.Checked = false;
            }
            ShowChecked();
        }

        private void OnRemoveUnchecked(object sender, RoutedEventArgs e)
        {
            foreach (FireRow r in _fires.Where(f => !f.Checked).ToList())
            {
                _fires.Remove(r);
            }
            ShowChecked();
        }

        private void OnFireChecked(object sender, RoutedEventArgs e) => ShowChecked();

        private List<FireRow> CheckedFires() => _fires.Where(f => f.Checked).ToList();

        /// <summary>The count under the list and the sentence at the top of
        /// tab 2 always describe the list as it stands.</summary>
        private void ShowChecked()
        {
            List<FireRow> on = CheckedFires();
            double acres = on.Where(f => f.Fire.Acres > 0).Sum(f => f.Fire.Acres);
            CheckedNote.Text = _fires.Count == 0 ? ""
                : N0(on.Count) + " of " + N0(_fires.Count) + (_fires.Count == 1 ? " fire" : " fires")
                  + (on.Count == 1 ? " is checked" : " are checked")
                  + (on.Count > 0 ? " (" + N0((long)Math.Round(acres)) + " acres as " + Program + " reports them)" : "")
                  + ". The checked fires are the ones tab 2 gets.";
            ChosenText.Text = on.Count == 0
                ? "No fire is checked. List the fires on tab 1 and check the ones you want."
                : (on.Count == 1 ? "1 " + Program + " fire: " : N0(on.Count) + " " + Program + " fires: ")
                  + string.Join("; ", on.Take(12).Select(f => f.Name + " " + f.Year))
                  + (on.Count > 12 ? "; and " + N0(on.Count - 12) + " more" : "") + ".";
        }

        private void OnTabChanged(object sender, SelectionChangedEventArgs e)
        {
            if (ReferenceEquals(e.OriginalSource, Tabs))
            {
                ShowChecked();
            }
        }

        // =====================================================================
        //  getting the data
        // =====================================================================
        private static bool LooksLikeEmail(string s) => Regex.IsMatch(s ?? "", @"^[^@\s]+@[^@\s]+\.[^@\s]+$");

        private void OnEmailChanged(object sender, TextChangedEventArgs e)
        {
            string email = EmailBox.Text.Trim();
            bool on = email.Length > 0;
            ImagesBox.IsEnabled = on;
            ProjectionPanel.IsEnabled = on;
            EmailNote.Text = !on ? "Empty: no bundle is requested."
                : LooksLikeEmail(email) ? "The portal will email a download link to this address."
                : "That does not look like an email address yet.";
        }

        private async void OnGet(object sender, RoutedEventArgs e)
        {
            if (_busy)
            {
                return;
            }
            List<FireRow> on = CheckedFires();
            if (on.Count == 0)
            {
                Say("No fire is checked. List the fires on tab 1 and check the ones you want.");
                return;
            }
            string perimeters = PerimetersBox.Text.Trim().Trim('"');
            if (perimeters.Length == 0)
            {
                Say("Give the output feature class for the perimeters, inside a file geodatabase.");
                return;
            }
            string email = EmailBox.Text.Trim();
            if (email.Length > 0 && !LooksLikeEmail(email))
            {
                Say("That does not look like an email address. Correct it, or empty the box to ask for no bundle.");
                return;
            }
            if (email.Length > 0 && on.Count > 500)
            {
                Say("The portal takes at most 500 fires in one bundle request, and " + N0(on.Count)
                    + " are checked. Uncheck some, or empty the email box.");
                return;
            }
            SetStatus(null);
            SetBusy(true);
            var cts = _workCts = new CancellationTokenSource();
            LogBox.Document.Blocks.Clear();
            try
            {
                Step(1, "Starting the geoprocessing tool...");
                _toolMessages = 0;
                _toolSaidCancelled = false;
                var request = new BurnActions.ToolRequest
                {
                    Program = Program,
                    // One entry for each checked ROW. A fire can have several assessments, each a row of its
                    // own, and the fire's identifier alone would bring them all: the map ID after the colon
                    // names the one that is checked. (The catalog gives some older fires no map ID.)
                    FireIds = on.Where(f => f.Id.Length > 0)
                        .Select(f => f.Fire.MapId > 0 ? f.Id + ":" + f.Fire.MapId.ToString(CultureInfo.InvariantCulture) : f.Id)
                        .Distinct().ToList(),
                    YearFrom = on.Where(f => f.Year > 0).Select(f => (int?)f.Year).Min(),
                    YearTo = on.Where(f => f.Year > 0).Select(f => (int?)f.Year).Max(),
                    Perimeters = perimeters,
                    Products = _products.Where(b => b.IsChecked == true).Select(b => (string)b.Content).ToList(),
                    RasterWorkspace = RasterBox.Text.Trim().Trim('"'),
                    Email = email,
                    Images = ImagesBox.IsChecked == true,
                    Projection = ProjAlbers.IsChecked == true ? "Albers" : "UTM",
                };
                var result = await BurnActions.RunToolAsync(request,
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
                await Dispatcher.InvokeAsync(() => { }, DispatcherPriority.Background);
                if (_toolMessages == 0)
                {
                    foreach (var m in result.Messages)
                    {
                        OnToolMessage(m.Text ?? "", BurnActions.Severity(m));
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
                    Log("    arcpy.ImportToolbox(r\"" + BurnActions.ToolboxPath + "\")");
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
            GetButton.IsEnabled = !busy;
            CancelButton.IsEnabled = busy;
        }

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

        // =====================================================================
        //  small things
        // =====================================================================
        private static string N0(long n) => n.ToString("N0", CultureInfo.CurrentCulture);

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
                BurnActions.Info(Program, message);
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
                return "The fire catalog of the Burn Severity Portal did not answer in time. Try again in a few minutes.";
            }
            if (ex is System.Net.Http.HttpRequestException)
            {
                return "The fire catalog of the Burn Severity Portal could not be reached. Check the internet connection.";
            }
            return ex.Message;
        }

        private void OnLink(object sender, System.Windows.Navigation.RequestNavigateEventArgs e)
        {
            e.Handled = true;
            try
            {
                global::WildlifeTools.SafeLink.Open(e.Uri.AbsoluteUri);
            }
            catch (Exception)
            {
            }
        }

        private void OnHelp(object sender, RoutedEventArgs e)
        {
            try
            {
                BurnActions.Help(Program);
            }
            catch (Exception)
            {
                SetStatus("The help page could not be opened.");
            }
        }

        // Browse: choose where an output goes, with ArcGIS Pro's own dialog
        private void OnBrowsePerimeters(object sender, RoutedEventArgs e)
        {
            try
            {
                string path = BurnActions.BrowsePerimeters(Program, PerimetersBox.Text);
                if (!string.IsNullOrEmpty(path))
                {
                    PerimetersBox.Text = path;
                }
            }
            catch (Exception)
            {
                // outside ArcGIS Pro there is no such dialog: the path is typed
            }
        }

        private void OnBrowseRasters(object sender, RoutedEventArgs e)
        {
            try
            {
                string path = BurnActions.BrowseRasters(RasterBox.Text);
                if (!string.IsNullOrEmpty(path))
                {
                    RasterBox.Text = path;
                }
            }
            catch (Exception)
            {
                // outside ArcGIS Pro there is no such dialog: the path is typed
            }
        }

        private void OnClose(object sender, RoutedEventArgs e) => CloseRequested?.Invoke(this, EventArgs.Empty);

        /// <summary>A fire in the list.</summary>
        internal sealed class FireRow : INotifyPropertyChanged
        {
            private bool _checked = true;
            public BurnFire Fire { get; }
            public FireRow(BurnFire f) { Fire = f; }
            /// <summary>One row per assessment: a fire can have an initial and an extended one.</summary>
            public string Key => Fire.Id + "|" + Fire.MapId.ToString(CultureInfo.InvariantCulture) + "|" + Fire.Assessment
                + "|" + Fire.IgnitionDate;
            public string Name => Fire.Name;
            public string Id => Fire.Id;
            public int Year => Fire.Year;
            public string IgnitionDate => Fire.IgnitionDate;
            public string FireType => Fire.FireType;
            public string Assessment => Fire.Assessment;
            public string AcresText => Fire.Acres >= 0 ? Fire.Acres.ToString("N0", CultureInfo.CurrentCulture) : "";
            public bool Checked
            {
                get => _checked;
                set { _checked = value; PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Checked))); }
            }
            public event PropertyChangedEventHandler PropertyChanged;
        }
    }
}
