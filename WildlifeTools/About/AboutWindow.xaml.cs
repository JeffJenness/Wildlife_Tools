// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Scholarship Fund notice, marking of modified versions, and
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
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Documents;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using System.Windows.Navigation;
using System.Windows.Threading;
using WildlifeTools.Scholarship;

namespace WildlifeTools.About
{
    /// <summary>
    /// The About card (tool #112): the logo and the name, the installed
    /// version with its build date and a check against the newest GitHub
    /// release, the three paragraphs from the Tool Reference page (what the
    /// add-in is, what it is worth, and why it is free), a citation to copy,
    /// the license and the source, the dedication, and buttons to the Tool
    /// Reference, the scholarship page and the university's giving page.
    /// Non-modal and draggable like the Scholarship card, in the colors of
    /// the web site. One shared instance is reused.
    ///
    /// A plain WPF Window on purpose (not a ProWindow): it needs nothing from
    /// ArcGIS Pro to draw itself, so it can be rendered and checked outside
    /// Pro (see claude_tests/AboutCardRender). The version check runs only
    /// once the window has loaded, so a render never touches the network.
    /// </summary>
    public partial class AboutWindow : Window
    {
        private static AboutWindow _instance;
        private string _version;
        private string _citation;
        private bool _checked;

        public AboutWindow()
        {
            InitializeComponent();
            // Never taller than the screen's work area: past that the card
            // scrolls (SizeToContent stops at MaxHeight, the ScrollViewer
            // takes over), which matters on laptops at 125% scaling.
            try
            {
                MaxHeight = Math.Max(480, SystemParameters.WorkArea.Height - 24);
            }
            catch (Exception)
            {
                // keep the XAML value
            }
            LoadLogo();
            FillVersion();
            NoticeRun.Text = ScholarshipInfo.Notice + " ";
            NoticeLinkRun.Text = ScholarshipInfo.NoticeUrl
                .Replace("https://", "").TrimEnd('/');
            NoticeLink.NavigateUri = new Uri(ScholarshipInfo.NoticeUrl);
            UpdateRun.Text = "Checking for a newer version…";
            UpdateLinkRun.Text = string.Empty;
        }

        /// <summary>Shows the card non-modally; a card that is already open
        /// is brought forward. Must be called on the UI thread.</summary>
        internal static void ShowCard()
        {
            if (_instance == null)
            {
                _instance = new AboutWindow
                {
                    Owner = ArcGIS.Desktop.Framework.FrameworkApplication
                        .Current.MainWindow,
                };
                _instance.Closed += (_, _) => _instance = null;
            }
            _instance.Show();
            _instance.Activate();
        }

        private void LoadLogo()
        {
            try
            {
                var bmp = new BitmapImage();
                bmp.BeginInit();
                bmp.CacheOption = BitmapCacheOption.OnLoad;
                bmp.UriSource = new Uri(
                    "pack://application:,,,/WildlifeTools;component/About/jenness-logo.png");
                bmp.EndInit();
                bmp.Freeze();
                LogoImage.Source = bmp;
            }
            catch (Exception)
            {
                // No logo is better than no card.
            }
        }

        /// <summary>The version line and the citation, from the Config.daml
        /// beside the DLL, the DLL's own date, and the running process.</summary>
        private void FillVersion()
        {
            string dll = null;
            DateTime? built = null;
            string pro = null;
            try
            {
                dll = typeof(AboutWindow).Assembly.Location;
                if (!string.IsNullOrEmpty(dll) && File.Exists(dll))
                {
                    built = File.GetLastWriteTime(dll);
                }
            }
            catch (Exception)
            {
                // leave the date unknown
            }
            try
            {
                string exe = Environment.ProcessPath;
                if (!string.IsNullOrEmpty(exe))
                {
                    pro = AboutInfo.ShortProVersion(
                        FileVersionInfo.GetVersionInfo(exe).ProductVersion);
                }
            }
            catch (Exception)
            {
                // leave the Pro version unknown
            }
            // The Config.daml beside the DLL when there is one (an installed
            // .esriAddinX), else the version stamped into the assembly at
            // build time (a build-output deployment has no Config.daml).
            string version = AboutInfo.ReadInstalledVersion(dll);
            if (version == null)
            {
                try
                {
                    version = AboutInfo.AssemblyVersionString(
                        typeof(AboutWindow).Assembly.GetName().Version);
                }
                catch (Exception)
                {
                    // leave it unknown
                }
            }
            SetVersion(version, built, pro);
        }

        /// <summary>Writes the version line and the citation. Internal so the
        /// render harness, which runs outside an installed add-in, can show
        /// the card as it looks in Pro.</summary>
        internal void SetVersion(string version, DateTime? built, string proVersion)
        {
            _version = version;
            VersionText.Text = AboutInfo.VersionLine(version, built, proVersion);
            _citation = AboutInfo.Citation(version, built ?? DateTime.Today);
            CitationText.Text = _citation;
        }

        private void OnLoaded(object sender, RoutedEventArgs e)
        {
            // Gentle fade-in, like the other cards.
            BeginAnimation(OpacityProperty,
                new DoubleAnimation(0, 1, new Duration(TimeSpan.FromMilliseconds(350))));
            if (!_checked)
            {
                _checked = true;
                _ = CheckForNewerVersionAsync();
            }
        }

        /// <summary>Asks GitHub for the newest release and writes the answer
        /// under the version line. Awaiting on the UI thread brings the
        /// continuation back to it.</summary>
        private async Task CheckForNewerVersionAsync()
        {
            string tag = await UpdateCheck.LatestTagAsync(TimeSpan.FromSeconds(8));
            ApplyUpdateResult(tag);
        }

        /// <summary>Writes the outcome of a version check for release
        /// <paramref name="tag"/> (null = no answer). Internal so the render
        /// harness can show each state without the network.</summary>
        internal void ApplyUpdateResult(string tag)
        {
            UpdateStatus status = AboutInfo.Compare(_version, tag);
            UpdateRun.Text = AboutInfo.UpdateMessage(status, AboutInfo.TagVersion(tag)) + " ";
            if (status == UpdateStatus.NewerAvailable)
            {
                UpdateLinkRun.Text = "Download it from GitHub";
                UpdateLink.NavigateUri = new Uri(AboutInfo.LatestReleasePageUrl);
            }
            else
            {
                UpdateLinkRun.Text = string.Empty;
            }
        }

        // ---- the copy buttons -------------------------------------------------
        private void OnCopyVersion(object sender, RoutedEventArgs e) =>
            CopyToClipboard(VersionText.Text, CopyVersionButton);

        private void OnCopyCitation(object sender, RoutedEventArgs e) =>
            CopyToClipboard(_citation, CopyCitationButton);

        private static void CopyToClipboard(string text, Button button)
        {
            try
            {
                Clipboard.SetText(text ?? string.Empty);
            }
            catch (Exception)
            {
                return;   // the clipboard can be held by another program; try again later
            }
            // Say so on the button for a moment.
            object was = button.Content;
            button.Content = "Copied";
            var timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1.6) };
            timer.Tick += (_, _) =>
            {
                timer.Stop();
                button.Content = was;
            };
            timer.Start();
        }

        // ---- the links ------------------------------------------------------
        private void OnToolReference(object sender, RoutedEventArgs e) =>
            WebHelp.Open(AboutInfo.ToolReferenceRelative);

        private void OnSourcesCited(object sender, RoutedEventArgs e) =>
            WebHelp.Open(AboutInfo.SourcesCitedRelative);

        private void OnScholarship(object sender, RoutedEventArgs e) =>
            ScholarshipLinks.OpenPage();

        private void OnDonate(object sender, RoutedEventArgs e) =>
            ScholarshipLinks.Donate();

        private void OnNavigate(object sender, RequestNavigateEventArgs e)
        {
            ScholarshipLinks.OpenUrl(e.Uri.AbsoluteUri);
            e.Handled = true;
        }

        // ---- the card itself -------------------------------------------------
        private void OnDrag(object sender, System.Windows.Input.MouseButtonEventArgs e)
        {
            // Don't start a window drag when the press lands on a link or a
            // button -- DragMove would capture the mouse and swallow the click.
            if (IsInteractive(e.OriginalSource))
            {
                return;
            }
            try
            {
                DragMove();
            }
            catch (InvalidOperationException)
            {
                // DragMove throws if the button is no longer pressed; ignore.
            }
        }

        /// <summary>True if the clicked element is (or sits inside) a
        /// Hyperlink or a button. Walks the visual tree for UI elements and
        /// the logical tree for text elements (Run/Hyperlink are not
        /// Visuals).</summary>
        private static bool IsInteractive(object source)
        {
            DependencyObject d = source as DependencyObject;
            while (d != null)
            {
                if (d is Hyperlink || d is ButtonBase)
                {
                    return true;
                }
                DependencyObject parent = null;
                if (d is Visual || d is System.Windows.Media.Media3D.Visual3D)
                {
                    parent = VisualTreeHelper.GetParent(d);
                }
                parent ??= LogicalTreeHelper.GetParent(d);
                d = parent;
            }
            return false;
        }

        private void OnClose(object sender, RoutedEventArgs e)
        {
            Close();
        }
    }
}
