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
using System.Windows;
using System.Windows.Controls.Primitives;
using System.Windows.Documents;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using System.Windows.Navigation;

namespace WildlifeTools.Scholarship
{
    /// <summary>
    /// The card behind the Lois Engelman Memorial Scholarship Fund button: a
    /// photograph of Lois and Jeff (one of two, chosen at random each time
    /// the card opens), a few sentences on what the fund is, the dedication
    /// notice the license requires, and two links -- the scholarship page on
    /// the Jenness Enterprises site and the university's giving page.
    /// Non-modal and draggable like the other Take a Break cards, in the
    /// colors of the web site. One shared instance is reused.
    ///
    /// A plain WPF Window on purpose (not a ProWindow): it needs nothing from
    /// ArcGIS Pro to draw itself, so it can be rendered and checked outside
    /// Pro (see claude_tests/ScholarshipCardRender).
    /// </summary>
    public partial class ScholarshipWindow : Window
    {
        private static ScholarshipWindow _instance;
        private static readonly Random Rng = new Random();

        /// <summary>The photographs (embedded resources in this folder) and
        /// their captions, as on the home page of the web site.</summary>
        private static readonly (string File, string Caption)[] Photos =
        {
            ("lois_and_jeff_early.jpg", "Jeff and Lois, in their early days."),
            ("lois_and_jeff_recent.jpg", "Jeff and Lois, in Flagstaff."),
        };

        public ScholarshipWindow()
        {
            InitializeComponent();
            NoticeRun.Text = ScholarshipInfo.Notice + " ";
            NoticeLinkRun.Text = ScholarshipInfo.NoticeUrl
                .Replace("https://", "").TrimEnd('/');
            NoticeLink.NavigateUri = new Uri(ScholarshipInfo.NoticeUrl);
            ShowPhoto(Rng.Next(Photos.Length));
        }

        /// <summary>Shows the card non-modally. A card that is already open
        /// is brought forward as it is; a new one picks its photograph at
        /// random. Must be called on the UI thread.</summary>
        internal static void ShowCard()
        {
            if (_instance == null)
            {
                _instance = new ScholarshipWindow
                {
                    Owner = ArcGIS.Desktop.Framework.FrameworkApplication
                        .Current.MainWindow,
                };
                _instance.Closed += (_, _) => _instance = null;
            }
            _instance.Show();
            _instance.Activate();
        }

        /// <summary>Puts photograph <paramref name="index"/> and its caption
        /// on the card. A photograph that cannot be loaded leaves the frame
        /// empty; the rest of the card stands.</summary>
        internal void ShowPhoto(int index)
        {
            try
            {
                var (file, caption) = Photos[
                    Math.Min(Math.Max(index, 0), Photos.Length - 1)];
                var bmp = new BitmapImage();
                bmp.BeginInit();
                bmp.CacheOption = BitmapCacheOption.OnLoad;
                bmp.UriSource = new Uri(
                    "pack://application:,,,/WildlifeTools;component/Scholarship/"
                    + file);
                bmp.EndInit();
                bmp.Freeze();
                PhotoImage.Source = bmp;
                PhotoCaption.Text = caption;
            }
            catch (Exception)
            {
                PhotoCaption.Text = string.Empty;
            }
        }

        internal static int PhotoCount => Photos.Length;

        private void OnLoaded(object sender, RoutedEventArgs e)
        {
            // Gentle fade-in, like the other cards.
            BeginAnimation(OpacityProperty,
                new DoubleAnimation(0, 1, new Duration(TimeSpan.FromMilliseconds(350))));
        }

        // ---- the links ------------------------------------------------------
        private void OnAbout(object sender, RoutedEventArgs e) =>
            WebHelp.Open(ScholarshipInfo.PageRelative);

        private void OnDonate(object sender, RoutedEventArgs e)
        {
            if (string.IsNullOrWhiteSpace(ScholarshipInfo.NauDonateUrl))
            {
                ArcGIS.Desktop.Framework.Dialogs.MessageBox.Show(
                    "Northern Arizona University's giving page for the "
                    + ScholarshipInfo.FundName + " is being set up. This "
                    + "button will open it as soon as it is ready."
                    + Environment.NewLine + Environment.NewLine
                    + "Until then, the scholarship page has the latest: "
                    + "click About the scholarship.",
                    ScholarshipInfo.FundName);
                return;
            }
            OpenUrl(ScholarshipInfo.NauDonateUrl);
        }

        private void OnNavigate(object sender, RequestNavigateEventArgs e)
        {
            OpenUrl(e.Uri.AbsoluteUri);
            e.Handled = true;
        }

        private static void OpenUrl(string url)
        {
            try
            {
                Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
            }
            catch (Exception)
            {
                // A missing or blocked browser should not crash the card.
            }
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
