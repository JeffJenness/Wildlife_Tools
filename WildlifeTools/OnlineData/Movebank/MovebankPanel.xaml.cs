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
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace WildlifeTools.OnlineData.Movebank
{
    /// <summary>
    /// The content of the Movebank Animal Tracks window. It searches the
    /// Movebank Data Repository itself, and reaches ArcGIS Pro only through
    /// <see cref="MovebankActions"/>, from clicks, so the panel can be built
    /// and drawn outside Pro.
    /// </summary>
    public partial class MovebankPanel : UserControl
    {
        public event EventHandler CloseRequested;

        private const int PageSize = 25;
        private readonly MovebankClient _mb = new MovebankClient();
        private readonly MovebankAccount _account = new MovebankAccount();
        private readonly ObservableCollection<StudyRow> _studyRows = new ObservableCollection<StudyRow>();
        private MovebankStudy _study;           // the study selected on tab 1 (in place of _chosen)
        private bool _studyMode;                // tab 1 lists the account's studies, not the repository
        private int _terms;                     // of _study: 0 not yet asked, 1 shown and to be accepted, 2 already accepted
        private string _noteA, _noteB;          // the repository's notes, kept while the studies' notes are shown
        private readonly ObservableCollection<PackageRow> _rows = new ObservableCollection<PackageRow>();
        private CancellationTokenSource _searchCts, _filesCts, _workCts;
        private string _searchText, _searchSensor;
        private int _page;
        private long _total;
        private MovebankPackage _chosen;        // the dataset selected on tab 1
        private string _folder;                 // a folder on disk, used in place of _chosen
        private bool _busy, _loadedOnce;
        private int _toolMessages;
        private bool _toolSaidCancelled;

        // sensor names as the repository writes them
        private static readonly string[] Sensors =
        {
            "Any sensor", "GPS", "Argos Doppler Shift", "Radio Transmitter", "Solar Geolocator", "Bird Ring",
        };

        public MovebankPanel()
        {
            InitializeComponent();
            foreach (string s in Sensors)
            {
                SensorBox.Items.Add(s);
            }
            SensorBox.SelectedIndex = 0;
            ResultsGrid.ItemsSource = _rows;
            _noteA = NoteA.Text;
            _noteB = NoteB.Text;
            var anywhere = Gbif.GbifActions.AreaChoice.World();
            anywhere.Label = "Anywhere";
            AreaBox.Items.Add(anywhere);
            AreaBox.SelectedIndex = 0;
            ShowAccount();
            Loaded += OnLoaded;
            Unloaded += (_, _) =>
            {
                _searchCts?.Cancel();
                _filesCts?.Cancel();
                _workCts?.Cancel();
            };
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
                PointsBox.Text = await MovebankActions.DefaultOutputPathAsync("Movebank_points");
                LinesBox.Text = await MovebankActions.DefaultOutputPathAsync("Movebank_tracks");
            }
            catch (Exception)
            {
                // outside Pro, or no project: the user types the paths
            }
        }

        // =====================================================================
        //  finding a dataset
        // =====================================================================
        private async void OnSearch(object sender, RoutedEventArgs e)
        {
            if (_studyMode)
            {
                await SearchStudiesAsync();
                return;
            }
            _searchText = SearchBox.Text.Trim();
            _searchSensor = SensorBox.SelectedIndex > 0 ? (string)SensorBox.SelectedItem : null;
            _page = 0;
            _rows.Clear();
            await SearchPageAsync();
        }

        private async void OnMore(object sender, RoutedEventArgs e)
        {
            _page++;
            await SearchPageAsync();
        }

        private async Task SearchPageAsync()
        {
            _searchCts?.Cancel();
            var cts = _searchCts = new CancellationTokenSource();
            SearchNote.Text = "Asking the Movebank Data Repository...";
            MoreButton.IsEnabled = false;
            try
            {
                var (list, total) = await _mb.SearchAsync(_searchText, _searchSensor, _page, PageSize, cts.Token);
                if (cts.IsCancellationRequested)
                {
                    return;
                }
                _total = total;
                foreach (MovebankPackage p in list)
                {
                    _rows.Add(new PackageRow(p));
                }
                SearchNote.Text = _rows.Count == 0
                    ? "No published dataset matches. Try a scientific name, a common name, or fewer words."
                    : _total.ToString("N0", CultureInfo.CurrentCulture) + (_total == 1 ? " published dataset matches" : " published datasets match")
                      + (_rows.Count < _total ? "; showing the newest " + _rows.Count.ToString("N0", CultureInfo.CurrentCulture) + "." : ", newest first.");
                MoreButton.IsEnabled = _rows.Count < _total;
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                SearchNote.Text = Short(ex);
            }
        }

        private async void OnResultSelected(object sender, SelectionChangedEventArgs e)
        {
            if (ResultsGrid.SelectedItem is StudyRow srow)
            {
                ShowStudy(srow.Study);
                return;
            }
            if (!(ResultsGrid.SelectedItem is PackageRow row))
            {
                // nothing is selected (the list was cleared or searched again): the details go with it
                NoDataset();
                return;
            }
            MovebankPackage p = row.Package;
            _chosen = p;
            _study = null;
            ChooseButton.IsEnabled = true;
            DetailTitle.Text = p.Title;
            DetailFacts.Text = string.Join("   ", new[]
            {
                p.Taxa.Count > 0 ? string.Join(", ", p.Taxa) : null,
                p.Animals >= 0 ? p.Animals.ToString("N0", CultureInfo.CurrentCulture) + " animals" : null,
                p.Locations >= 0 ? p.Locations.ToString("N0", CultureInfo.CurrentCulture) + " locations" : null,
                p.Sensors.Count > 0 ? string.Join(", ", p.Sensors) : null,
                string.IsNullOrEmpty(p.License) ? null : "License: " + p.License,
                string.IsNullOrEmpty(p.Doi) ? null : "DOI " + p.Doi,
            }.Where(s => s != null));
            DetailCitation.Text = string.IsNullOrEmpty(p.Citation) ? "" : "Cite as: " + p.Citation;
            DetailMore.Text = string.Join(Environment.NewLine, new[]
            {
                p.Authors.Count > 0 ? "Authors: " + string.Join("; ", p.Authors) : null,
                string.IsNullOrEmpty(p.Issued) ? null : "Published: " + p.Issued,
                p.Keywords.Count > 0 ? "Keywords: " + string.Join("; ", p.Keywords) : null,
                string.IsNullOrEmpty(p.Paper) ? null : "Related publication: " + p.Paper
                    + (string.IsNullOrEmpty(p.Journal) ? "" : " (" + p.Journal + ")"),
                "Where: the record holds no coordinates. Look for the place in the title, the keywords and the summary below.",
            }.Where(s => s != null));
            DetailAbstract.Text = p.Abstract ?? "";
            DetailFiles.Text = "Looking up its files...";
            ShowChosen();
            _filesCts?.Cancel();
            var cts = _filesCts = new CancellationTokenSource();
            try
            {
                List<MovebankFile> files = await _mb.FilesAsync(p, cts.Token);
                if (cts.IsCancellationRequested || !ReferenceEquals(_chosen, p))
                {
                    return;
                }
                row.Files = files;
                DetailFiles.Text = FilesSentence(files);
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                DetailFiles.Text = Short(ex);
            }
        }

        /// <summary>No dataset is selected: the details box and tab 2 say so.</summary>
        private void NoDataset()
        {
            _filesCts?.Cancel();
            _chosen = null;
            _study = null;
            ChooseButton.IsEnabled = false;
            DetailTitle.Text = _studyMode
                ? "Click a study in the list to see its details here. Then click Choose this study to go on to tab 2."
                : "Click a dataset in the list to see its details here. Then click Choose this dataset to go on to tab 2.";
            DetailFacts.Text = "";
            DetailFiles.Text = "";
            DetailCitation.Text = "";
            DetailMore.Text = "";
            DetailAbstract.Text = "";
            ShowChosen();
        }

        /// <summary>Choose this dataset: on to tab 2 with the dataset whose details are shown.</summary>
        private void OnChoose(object sender, RoutedEventArgs e)
        {
            if (_chosen == null && _study == null)
            {
                SetStatus(_studyMode ? "Click a study in the list first." : "Click a dataset in the list first.");
                return;
            }
            if (_folder != null)
            {
                _folder = null;                      // the dataset just chosen replaces a folder on disk
            }
            ShowChosen();
            SetStatus(null);
            Tabs.SelectedIndex = 1;
        }

        private void OnResultDoubleClick(object sender, System.Windows.Input.MouseButtonEventArgs e)
        {
            // a double-click on a row chooses it; on a header or the empty part, nothing
            var from = e.OriginalSource as DependencyObject;
            while (from != null && !(from is DataGridRow))
            {
                from = from is System.Windows.Media.Visual || from is System.Windows.Media.Media3D.Visual3D
                    ? System.Windows.Media.VisualTreeHelper.GetParent(from) : LogicalTreeHelper.GetParent(from);
            }
            if (((from as DataGridRow)?.Item is PackageRow && _chosen != null)
                || ((from as DataGridRow)?.Item is StudyRow && _study != null))
            {
                OnChoose(sender, e);
            }
        }

        /// <summary>Clear: the search box, the list and the chosen dataset.</summary>
        private void OnClear(object sender, RoutedEventArgs e)
        {
            _searchCts?.Cancel();
            SearchBox.Text = "";
            SensorBox.SelectedIndex = 0;
            _page = 0;
            _total = 0;
            _rows.Clear();
            _studyRows.Clear();
            MoreButton.IsEnabled = false;
            SearchNote.Text = _studyMode
                ? "The list is empty. Type a species, a place or any other word and click Search, or click Search with the "
                  + "box empty to list every study your account may download."
                : "The list is empty. Type a species, a place or any other word and click Search, or click Search "
                  + "with the box empty to list every published dataset.";
            NoDataset();
            SetStatus(null);
            SearchBox.Focus();
        }

        /// <summary>What a dataset's files are and how much will be fetched.</summary>
        internal static string FilesSentence(List<MovebankFile> files)
        {
            List<MovebankFile> data = files.Where(f => f.Kind == "data").ToList();
            if (data.Count == 0)
            {
                return "This dataset has no data file in Movebank's CSV format, so it cannot be loaded here. Its files: "
                    + (files.Count == 0 ? "none" : string.Join("; ", files.Select(f => f.Name))) + ".";
            }
            long fetch = files.Where(f => f.Kind != "other").Sum(f => f.Size);
            return "Files to fetch: " + (data.Count == 1 ? "1 data file" : data.Count + " data files")
                + (files.Any(f => f.Kind == "reference") ? ", the reference data about the animals and tags" : "")
                + (files.Any(f => f.Kind == "readme") ? ", the README" : "")
                + " (" + Bytes(fetch) + " in all)."
                + (fetch > 500L * 1024 * 1024 ? " This is a large dataset: fetching and reading it will take a while." : "");
        }

        private void OnTitleLink(object sender, RoutedEventArgs e)
        {
            if ((sender as FrameworkContentElement)?.DataContext is PackageRow row)
            {
                OpenUrl(row.Package.DoiUrl);
            }
            else if ((sender as FrameworkContentElement)?.DataContext is StudyRow srow)
            {
                OpenUrl(srow.Study.Page);
            }
        }

        // =====================================================================
        //  getting the data
        // =====================================================================
        private void ShowChosen()
        {
            if (_folder != null)
            {
                ChosenText.Text = "The Movebank data files in the folder " + _folder + " (nothing will be fetched).";
            }
            else if (_study != null)
            {
                ChosenText.Text = _study.Name + "  (Movebank study " + _study.Id + ", read with the Movebank account "
                    + (MovebankAccount.SignedInAs() ?? "?") + ")";
            }
            else if (_chosen != null)
            {
                ChosenText.Text = _chosen.Title + (string.IsNullOrEmpty(_chosen.Doi) ? "" : "  (DOI " + _chosen.Doi + ")");
            }
            else
            {
                ChosenText.Text = "No dataset is chosen. Click one in the list on tab 1 and then Choose this dataset, or load a dataset that is already on disk.";
            }
            // the license terms belong to a study in use: not to a published dataset, not to files on disk
            TermsPanel.Visibility = _folder == null && _study != null ? Visibility.Visible : Visibility.Collapsed;
            // shown only while a folder on disk is in use: it is the way back to the dataset of tab 1
            ClearFolderButton.Visibility = _folder != null ? Visibility.Visible : Visibility.Collapsed;
        }

        private void OnUseFolder(object sender, RoutedEventArgs e)
        {
            var dlg = new Microsoft.Win32.OpenFolderDialog
            {
                Title = "Choose the folder that holds a Movebank dataset's files",
            };
            if (dlg.ShowDialog(Window.GetWindow(this)) == true)
            {
                _folder = dlg.FolderName;
                ShowChosen();
            }
        }

        private void OnClearFolder(object sender, RoutedEventArgs e)
        {
            _folder = null;
            ShowChosen();
        }

        private void OnOutputChanged(object sender, RoutedEventArgs e)
        {
            if (PointsBox == null || LinesBox == null)
            {
                return;                         // still being built
            }
            PointsBox.IsEnabled = OutLines.IsChecked != true;
            LinesBox.IsEnabled = OutPoints.IsChecked != true;
        }

        private async void OnGet(object sender, RoutedEventArgs e)
        {
            if (_busy)
            {
                return;
            }
            bool study = _folder == null && _study != null;
            string dataset = _folder ?? (_study != null ? "study " + _study.Id
                : _chosen == null ? null : (string.IsNullOrEmpty(_chosen.Doi) ? _chosen.Uuid : _chosen.Doi));
            if (string.IsNullOrEmpty(dataset))
            {
                Say("Choose a dataset first: click one in the list on tab 1 and then Choose this dataset.");
                return;
            }
            if (study && _terms != 2 && AcceptBox.IsChecked != true)
            {
                Say(_terms == 0
                    ? "Movebank hands over a study only after your account has accepted its license terms. Click Show the "
                      + "license terms, read them, and check the box if you accept them."
                    : "Check \"I have read and accept this study's license terms\" if you accept them. Without that, "
                      + "Movebank does not hand over the study.");
                return;
            }
            bool points = OutLines.IsChecked != true, lines = OutPoints.IsChecked != true;
            string outPoints = points ? PointsBox.Text.Trim().Trim('"') : "";
            string outLines = lines ? LinesBox.Text.Trim().Trim('"') : "";
            if ((points && outPoints.Length == 0) || (lines && outLines.Length == 0))
            {
                Say("Give the output feature class for the " + (points && outPoints.Length == 0 ? "points" : "track lines") + ".");
                return;
            }
            if (points && lines && string.Equals(outPoints, outLines, StringComparison.OrdinalIgnoreCase))
            {
                Say("The points and the track lines need different output names.");
                return;
            }
            if (_folder == null && ResultsGrid.SelectedItem is PackageRow row && row.Files != null
                && row.Files.All(f => f.Kind != "data"))
            {
                Say("This dataset has no data file in Movebank's CSV format, so it cannot be loaded here.");
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
                var result = await MovebankActions.RunToolAsync(dataset,
                    points && lines ? MovebankActions.OutBoth : points ? MovebankActions.OutPoints : MovebankActions.OutLines,
                    outPoints, outLines, AnimalsBox.Text.Trim(), FlaggedBox.IsChecked == true,
                    study && AcceptBox.IsChecked == true,
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
                        OnToolMessage(m.Text ?? "", MovebankActions.Severity(m));
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
                    if (study)
                    {
                        _terms = 2;                  // Movebank has them accepted now
                        ShowTermsState();
                    }
                    Log("This run is recorded in the Geoprocessing History (Analysis tab, History). Right-click its entry there "
                        + "and choose Copy Python Command to repeat the same request from a script. ArcGIS Pro does not record "
                        + "where the toolbox is for a run started from a window, so the first line of the copied command names "
                        + "the wrong toolbox. Replace that line with this one:");
                    Log("    arcpy.ImportToolbox(r\"" + MovebankActions.ToolboxPath + "\")");
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

        // lines of every geoprocessing run that say nothing about this one
        private static readonly string[] ToolBoilerplate =
        {
            "Start Time:", "Succeeded at", "Failed at", "Failed to execute", "Executing:", "Executing (",
            "View online help files:", "To run this tool from a script", "Running script", "Completed script",
            "Canceled function", "Cancelled function",
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
            Log(severity == 2 ? "Stopped: " + text.Trim() : text.TrimEnd());
        }

        // =====================================================================
        //  small things
        // =====================================================================
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
                MovebankActions.Info(message);
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
                return "The Movebank Data Repository did not answer in time. Try again.";
            }
            if (ex is System.Net.Http.HttpRequestException)
            {
                return "The Movebank Data Repository could not be reached. Check the internet connection.";
            }
            return ex.Message;
        }

        internal static string Bytes(long n) =>
            n < 1024 ? n + " bytes"
            : n < 1024 * 1024 ? (n / 1024.0).ToString("0", CultureInfo.CurrentCulture) + " KB"
            : n < 1024L * 1024 * 1024 ? (n / 1048576.0).ToString("0.0", CultureInfo.CurrentCulture) + " MB"
            : (n / 1073741824.0).ToString("0.00", CultureInfo.CurrentCulture) + " GB";

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
                MovebankActions.Help();
            }
            catch (Exception)
            {
            }
        }

        // Browse: choose where an output goes, with ArcGIS Pro's own dialog
        private void OnBrowsePoints(object sender, RoutedEventArgs e)
        {
            try
            {
                string path = MovebankActions.BrowseOutput(PointsBox.Text, "Save the points as");
                if (!string.IsNullOrEmpty(path))
                {
                    PointsBox.Text = path;
                }
            }
            catch (Exception)
            {
                // outside ArcGIS Pro there is no such dialog: the path is typed
            }
        }

        private void OnBrowseLines(object sender, RoutedEventArgs e)
        {
            try
            {
                string path = MovebankActions.BrowseOutput(LinesBox.Text, "Save the track lines as");
                if (!string.IsNullOrEmpty(path))
                {
                    LinesBox.Text = path;
                }
            }
            catch (Exception)
            {
                // outside ArcGIS Pro there is no such dialog: the path is typed
            }
        }

        private void OnClose(object sender, RoutedEventArgs e) => CloseRequested?.Invoke(this, EventArgs.Empty);

        // =====================================================================
        //  Movebank itself, with the user's own account
        // =====================================================================
        private void ShowAccount()
        {
            string user = MovebankAccount.SignedInAs();
            AccountText.Text = user == null
                ? "Not signed in to Movebank. The published datasets need no sign-in."
                : "Signed in to Movebank as " + user + ". The sign-in is kept in Windows Credential Manager on this computer.";
            SignInButton.Content = user == null ? "Sign in to Movebank..." : "Switch account...";
            SignOutButton.Visibility = user == null ? Visibility.Collapsed : Visibility.Visible;
        }

        private async void OnSignIn(object sender, RoutedEventArgs e)
        {
            var dlg = new MovebankSignInWindow(MovebankAccount.SignedInAs()) { Owner = Window.GetWindow(this) };
            if (dlg.ShowDialog() != true)
            {
                return;
            }
            SetStatus("Checking the sign-in with Movebank...");
            try
            {
                if (!await _account.CheckAsync(dlg.UserName, dlg.Password, CancellationToken.None))
                {
                    Say("Movebank did not accept that user name and password. Use your Movebank user name, not your email "
                        + "address, and check both by signing in at www.movebank.org. A new account works here only after "
                        + "Movebank has activated it.");
                    return;
                }
                CredentialStore.Save(MovebankAccount.Service, dlg.UserName, dlg.Password);
                _account.Forget();
                SetStatus(null);
                ShowAccount();
                if (_studyMode)
                {
                    await SearchStudiesAsync();
                }
            }
            catch (Exception ex)
            {
                Say("The sign-in could not be checked: " + Short(ex));
            }
        }

        private void OnSignOut(object sender, RoutedEventArgs e)
        {
            try
            {
                CredentialStore.Delete(MovebankAccount.Service);
            }
            catch (Exception)
            {
            }
            _account.Forget();
            ShowAccount();
            if (_studyMode)
            {
                _studyRows.Clear();
                NoDataset();
                SearchNote.Text = "Signed out. Sign in to Movebank to list the studies your account may download.";
            }
        }

        /// <summary>Tab 1 lists either the repository's published datasets
        /// or the studies the account may download.</summary>
        private async void OnSourceChanged(object sender, RoutedEventArgs e)
        {
            if (NoteA == null || SourceStudies == null || ResultsGrid == null)
            {
                return;                         // still being built
            }
            bool studies = SourceStudies.IsChecked == true;
            if (studies == _studyMode)
            {
                return;
            }
            _studyMode = studies;
            _searchCts?.Cancel();
            ResultsGrid.ItemsSource = studies ? (System.Collections.IEnumerable)_studyRows : _rows;
            TitleColumn.Header = studies ? "Study" : "Dataset";
            DateColumn.Header = studies ? "Fixes from" : "Published";
            ChooseButton.Content = studies ? "Choose this study" : "Choose this dataset";
            AreaRow.Visibility = studies ? Visibility.Visible : Visibility.Collapsed;
            NoteC.Visibility = studies ? Visibility.Collapsed : Visibility.Visible;
            MoreButton.Visibility = studies ? Visibility.Collapsed : Visibility.Visible;
            NoteA.Text = studies
                ? "These are the studies in Movebank whose owners let your account download their data. Search by species "
                  + "(scientific name), place, owner or any other word, and limit the list to the studies whose main "
                  + "location is in a study area."
                : _noteA;
            NoteB.Text = studies
                ? "Caution: A study is not a published dataset. Its owner can add to it and correct it, so the same request "
                  + "made later may return different data, and it has no DOI. Before a study's data are fetched you must "
                  + "read and accept its license terms, on tab 2. Movebank asks that you contact a study's owner about "
                  + "what you intend to do with the data, and credit them."
                : _noteB;
            NoDataset();
            if (studies)
            {
                if (MovebankAccount.SignedInAs() == null)
                {
                    _studyRows.Clear();
                    SearchNote.Text = "Sign in to Movebank to list the studies your account may download.";
                }
                else if (_studyRows.Count == 0)
                {
                    await SearchStudiesAsync();
                }
            }
            else
            {
                SearchNote.Text = _rows.Count == 0 ? "" : _rows.Count.ToString("N0", CultureInfo.CurrentCulture) + " published datasets are listed.";
            }
        }

        private async void OnAreaDropDown(object sender, EventArgs e)
        {
            // read from the map each time the box is opened, so the list is never out of date
            object keep = (AreaBox.SelectedItem as Gbif.GbifActions.AreaChoice)?.Layer;
            int keepKind = (AreaBox.SelectedItem as Gbif.GbifActions.AreaChoice)?.Kind ?? Gbif.GbifActions.AreaChoice.KindWorld;
            List<Gbif.GbifActions.AreaChoice> list;
            try
            {
                list = await MovebankActions.ListAreasAsync();
            }
            catch (Exception)
            {
                return;                         // outside Pro, or no map: "anywhere" is all there is
            }
            var fresh = new List<Gbif.GbifActions.AreaChoice> { Gbif.GbifActions.AreaChoice.World() };
            fresh.AddRange(list.Where(a => a.Kind != Gbif.GbifActions.AreaChoice.KindWorld));
            fresh[0].Label = "Anywhere";
            int pick = fresh.FindIndex(a => a.Kind == keepKind && ReferenceEquals(a.Layer, keep));
            _fillingAreas = true;
            AreaBox.Items.Clear();
            foreach (Gbif.GbifActions.AreaChoice a in fresh)
            {
                AreaBox.Items.Add(a);
            }
            AreaBox.SelectedIndex = pick < 0 ? 0 : pick;
            _fillingAreas = false;
        }

        private bool _fillingAreas;

        private async void OnAreaChanged(object sender, SelectionChangedEventArgs e)
        {
            if (_fillingAreas || !_studyMode || !IsLoaded || MovebankAccount.SignedInAs() == null)
            {
                return;
            }
            await SearchStudiesAsync();
        }

        /// <summary>Lists the account's studies that match the words, the
        /// sensor and the study area. The whole list is read from Movebank
        /// once for a sign-in, and searched here.</summary>
        private async Task SearchStudiesAsync()
        {
            if (MovebankAccount.SignedInAs() == null)
            {
                _studyRows.Clear();
                SearchNote.Text = "Sign in to Movebank to list the studies your account may download.";
                Say("Sign in to Movebank first: click Sign in to Movebank at the top of this tab.");
                return;
            }
            _searchCts?.Cancel();
            var cts = _searchCts = new CancellationTokenSource();
            SearchNote.Text = "Asking Movebank for the studies your account may download...";
            try
            {
                List<MovebankStudy> all = await _account.StudiesAsync(cts.Token);
                if (cts.IsCancellationRequested)
                {
                    return;
                }
                string[] words = SearchBox.Text.Split(new[] { ' ', ',', ';' }, StringSplitOptions.RemoveEmptyEntries);
                string sensor = SensorBox.SelectedIndex > 0 ? (string)SensorBox.SelectedItem : null;
                var area = AreaBox.SelectedItem as Gbif.GbifActions.AreaChoice;
                (double West, double South, double East, double North)? box = null;
                string areaWords = "";
                if (area != null && area.Kind != Gbif.GbifActions.AreaChoice.KindWorld)
                {
                    try
                    {
                        box = await MovebankActions.AreaBoxAsync(area);
                    }
                    catch (Exception)
                    {
                        box = null;
                    }
                    if (box == null)
                    {
                        SearchNote.Text = "The study area could not be read from the map. Choose another, or Anywhere.";
                        return;
                    }
                    areaWords = " with a main location in " + area.Label;
                }
                IEnumerable<MovebankStudy> hits = all;
                if (words.Length > 0)
                {
                    hits = hits.Where(s =>
                    {
                        string hay = string.Join(" ", s.Name, s.Taxa, s.Investigator, s.Contact, s.Objective, s.Citation, s.Id);
                        return words.All(w => hay.IndexOf(w, StringComparison.OrdinalIgnoreCase) >= 0);
                    });
                }
                if (sensor != null)
                {
                    hits = hits.Where(s => (s.Sensors ?? "").IndexOf(sensor, StringComparison.OrdinalIgnoreCase) >= 0);
                }
                if (box != null)
                {
                    var b = box.Value;
                    hits = hits.Where(s => !double.IsNaN(s.Lat) && s.Lat >= b.South && s.Lat <= b.North
                        && (b.West <= b.East ? s.Lon >= b.West && s.Lon <= b.East : s.Lon >= b.West || s.Lon <= b.East));
                }
                List<MovebankStudy> list = hits.OrderByDescending(s => s.Last ?? "", StringComparer.Ordinal).ToList();
                _studyRows.Clear();
                foreach (MovebankStudy s in list)
                {
                    _studyRows.Add(new StudyRow(s));
                }
                SearchNote.Text = list.Count == 0
                    ? "None of the " + all.Count.ToString("N0", CultureInfo.CurrentCulture) + " studies your account may download matches"
                      + areaWords + ". Try a scientific name, fewer words, or a larger area."
                    : list.Count.ToString("N0", CultureInfo.CurrentCulture) + " of the " + all.Count.ToString("N0", CultureInfo.CurrentCulture)
                      + " studies your account may download " + (list.Count == 1 ? "matches" : "match") + areaWords
                      + ", the most recent fixes first.";
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                SearchNote.Text = ShortAccount(ex);
            }
        }

        private void ShowStudy(MovebankStudy s)
        {
            _filesCts?.Cancel();
            bool changed = !ReferenceEquals(_study, s);
            _study = s;
            _chosen = null;
            ChooseButton.IsEnabled = true;
            DetailTitle.Text = s.Name;
            DetailFacts.Text = string.Join("   ", new[]
            {
                string.IsNullOrEmpty(s.Taxa) ? null : s.Taxa,
                s.Animals >= 0 ? s.Animals.ToString("N0", CultureInfo.CurrentCulture) + " animals" : null,
                s.Locations >= 0 ? s.Locations.ToString("N0", CultureInfo.CurrentCulture) + " locations" : null,
                string.IsNullOrEmpty(s.Sensors) ? null : s.Sensors,
                "License: " + s.LicenseName,
                "Movebank study " + s.Id,
            }.Where(x => x != null));
            DetailFiles.Text = s.Locations < 0 ? "" : "The data file to fetch will be about " + Bytes((long)(s.Locations * 190.0)) + "."
                + (s.Locations > 2000000 ? " This is a large study: fetching and reading it will take a while." : "");
            DetailCitation.Text = string.IsNullOrEmpty(s.Citation)
                ? "The study gives no citation. Ask its owner how to credit it."
                : "Cite as: " + s.Citation;
            DetailMore.Text = string.Join(Environment.NewLine, new[]
            {
                string.IsNullOrEmpty(s.Investigator) ? null : "Principal investigator: " + s.Investigator,
                string.IsNullOrEmpty(s.Contact) ? null : "Contact person: " + s.Contact,
                string.IsNullOrEmpty(s.First) ? null : "Fixes from " + Day(s.First) + " to " + Day(s.Last) + " (UTC)",
                double.IsNaN(s.Lat) ? null : "Main location: latitude " + s.Lat.ToString("0.###", CultureInfo.CurrentCulture)
                    + ", longitude " + s.Lon.ToString("0.###", CultureInfo.CurrentCulture)
                    + " (one point for the whole study, set by its owner; the animals may travel far from it)",
            }.Where(x => x != null));
            DetailAbstract.Text = s.Objective ?? "";
            if (changed)
            {
                _terms = 0;
                AcceptBox.IsChecked = false;
            }
            ShowTermsState();
            ShowChosen();
        }

        private void ShowTermsState()
        {
            AcceptBox.IsEnabled = _terms == 1;
            AcceptBox.Visibility = _terms == 2 ? Visibility.Collapsed : Visibility.Visible;   // nothing to accept
            if (_terms == 2)
            {
                AcceptBox.IsChecked = false;
            }
            TermsNote.Text = _terms == 2
                ? "Your Movebank account has already accepted this study's license terms, so there is nothing to accept. "
                  + "The terms still apply: they are in the study's page at Movebank, and the tool records the study's license with the output."
                : _terms == 1
                    ? "The terms have been shown. If you accept them, check the box: they are then accepted in the name of your "
                      + "Movebank account when you click Get the data."
                    : "Movebank hands over a study only after your account has accepted the study's license terms. Click Show "
                      + "the license terms and read them. If you accept them, check the box.";
        }

        private async void OnShowTerms(object sender, RoutedEventArgs e)
        {
            MovebankStudy s = _study;
            if (s == null)
            {
                return;
            }
            TermsButton.IsEnabled = false;
            SetStatus("Asking Movebank for the study's license terms...");
            try
            {
                string terms = await _account.TermsAsync(s.Id, CancellationToken.None);
                if (!ReferenceEquals(_study, s))
                {
                    return;
                }
                SetStatus(null);
                if (terms == null)
                {
                    _terms = 2;
                    ShowTermsState();
                    return;
                }
                _terms = 1;
                ShowTermsState();
                new MovebankTermsWindow(s.Name, terms) { Owner = Window.GetWindow(this) }.ShowDialog();
            }
            catch (Exception ex)
            {
                Say("The license terms could not be read: " + ShortAccount(ex));
            }
            finally
            {
                TermsButton.IsEnabled = true;
            }
        }

        private static string Day(string stamp) => string.IsNullOrEmpty(stamp) ? "?" : stamp.Length >= 10 ? stamp.Substring(0, 10) : stamp;

        private static string ShortAccount(Exception ex)
        {
            if (ex is TaskCanceledException || ex is OperationCanceledException)
            {
                return "Movebank did not answer in time. Try again.";
            }
            if (ex is System.Net.Http.HttpRequestException)
            {
                return "Movebank could not be reached. Check the internet connection.";
            }
            return ex.Message;
        }

        /// <summary>One row of the list of studies.</summary>
        internal sealed class StudyRow
        {
            public MovebankStudy Study { get; }
            public StudyRow(MovebankStudy s) { Study = s; }
            public string Title => Study.Name;
            public string Species => Study.Taxa;
            public string AnimalsText => Study.Animals >= 0 ? Study.Animals.ToString("N0", CultureInfo.CurrentCulture) : "";
            public string LocationsText => Study.Locations >= 0 ? Study.Locations.ToString("N0", CultureInfo.CurrentCulture) : "";
            public string Issued => string.IsNullOrEmpty(Study.First) ? ""
                : Study.First.Substring(0, Math.Min(4, Study.First.Length))
                  + (string.IsNullOrEmpty(Study.Last) || Study.Last.Substring(0, Math.Min(4, Study.Last.Length)) == Study.First.Substring(0, Math.Min(4, Study.First.Length))
                      ? "" : "–" + Study.Last.Substring(0, Math.Min(4, Study.Last.Length)));
            public string LicenseShort => Study.LicenseName;
        }

        /// <summary>One row of the results list.</summary>
        internal sealed class PackageRow
        {
            public MovebankPackage Package { get; }
            public List<MovebankFile> Files { get; set; }
            public PackageRow(MovebankPackage p) { Package = p; }
            public string Title => Package.Title.StartsWith("Data from: ", StringComparison.Ordinal)
                ? Package.Title.Substring(11) : Package.Title;
            public string Species => string.Join(", ", Package.Taxa);
            public string AnimalsText => Package.Animals >= 0 ? Package.Animals.ToString("N0", CultureInfo.CurrentCulture) : "";
            public string LocationsText => Package.Locations >= 0 ? Package.Locations.ToString("N0", CultureInfo.CurrentCulture) : "";
            public string Issued => Package.Issued;
            public string LicenseShort
            {
                get
                {
                    string l = Package.License ?? "";
                    return l.StartsWith("CC0", StringComparison.Ordinal) ? "CC0"
                        : l.IndexOf("NonCommercial", StringComparison.OrdinalIgnoreCase) >= 0 ? "CC BY-NC"
                        : l.StartsWith("Attribution", StringComparison.Ordinal) ? "CC BY" : l;
                }
            }
        }
    }
}
