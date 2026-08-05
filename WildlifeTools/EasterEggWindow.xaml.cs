using System;
using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Controls.Primitives;
using System.Windows.Documents;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using System.Windows.Navigation;
using ArcGIS.Desktop.Framework;

namespace WildlifeTools
{
    /// <summary>
    /// A small non-modal card that celebrates the classic ArcMap "jerry" easter
    /// egg. It mirrors the Thought-for-the-Day card's look (rounded gradient
    /// border, drop shadow, fade-in, draggable) but has no background image.
    /// The easter-egg artwork (extracted from the ArcMap 8.3 GarciaUI.dll, a
    /// software artifact shown here for historical commentary) is displayed on a
    /// white plaque. The original Adrian Boot photograph is deliberately NOT
    /// bundled: we credit the author and link to the source instead. If a
    /// licensed copy is placed at Images\jerry_boot_photo.jpg it is shown too.
    /// One shared instance is reused.
    /// </summary>
    public partial class EasterEggWindow : Window
    {
        private static EasterEggWindow _instance;

        private EasterEggWindow()
        {
            InitializeComponent();
        }

        /// <summary>
        /// Shows the card non-modally, reusing the single instance if it is
        /// already open. Must be called on the UI thread.
        /// </summary>
        internal static void ShowEgg()
        {
            if (_instance == null)
            {
                _instance = new EasterEggWindow { Owner = FrameworkApplication.Current.MainWindow };
                _instance.Closed += (_, _) => _instance = null;
            }

            _instance.Show();
            _instance.Activate();
        }

        private void OnLoaded(object sender, RoutedEventArgs e)
        {
            LoadEggImage();
            LoadOptionalSourcePhoto();

            // Gentle fade-in.
            BeginAnimation(OpacityProperty,
                new DoubleAnimation(0, 1, new Duration(TimeSpan.FromMilliseconds(350))));
        }

        private static string InstallImagePath(string fileName)
        {
            string installDir = Path.GetDirectoryName(
                System.Reflection.Assembly.GetExecutingAssembly().Location);
            return Path.Combine(installDir ?? string.Empty, "Images", fileName);
        }

        /// <summary>Loads the deployed easter-egg artwork (black-on-white PNG).</summary>
        private void LoadEggImage()
        {
            try
            {
                string path = InstallImagePath("jerry_easteregg.png");
                if (!File.Exists(path))
                {
                    return;
                }

                var bmp = new BitmapImage();
                bmp.BeginInit();
                bmp.CacheOption = BitmapCacheOption.OnLoad;
                bmp.CreateOptions = BitmapCreateOptions.IgnoreImageCache;
                bmp.UriSource = new Uri(path, UriKind.Absolute);
                bmp.EndInit();
                bmp.Freeze();
                EggImage.Source = bmp;
            }
            catch (Exception)
            {
                // Leave the image empty on any failure; the rest of the card stands.
            }
        }

        /// <summary>
        /// If the user has placed a licensed/permitted copy of the source
        /// photograph at Images\jerry_boot_photo.jpg (or .png), show it above the
        /// credit line and hide the "not distributed" note. Nothing is shipped by
        /// default, so by default this does nothing.
        /// </summary>
        private void LoadOptionalSourcePhoto()
        {
            try
            {
                string path = InstallImagePath("jerry_boot_photo.jpg");
                if (!File.Exists(path))
                {
                    path = InstallImagePath("jerry_boot_photo.png");
                }
                if (!File.Exists(path))
                {
                    return;
                }

                var bmp = new BitmapImage();
                bmp.BeginInit();
                bmp.CacheOption = BitmapCacheOption.OnLoad;
                bmp.CreateOptions = BitmapCreateOptions.IgnoreImageCache;
                bmp.DecodePixelWidth = 880; // cap decode size; XAML MaxWidth handles layout
                bmp.UriSource = new Uri(path, UriKind.Absolute);
                bmp.EndInit();
                bmp.Freeze();

                SourceImage.Source = bmp;
                SourcePlaque.Visibility = Visibility.Visible;
                // A licensed/permitted or low-res fair-use copy is present: keep
                // the note visible but state the rationale and credit rather than
                // the "not distributed" default.
                CreditNote.Text =
                    "Shown as a low-resolution thumbnail for historical "
                    + "illustration. © Adrian Boot — all rights reserved. "
                    + "Please license the original at the source above.";
            }
            catch (Exception)
            {
                // Fall back to the link-only credit on any failure.
            }
        }

        private void OnNavigate(object sender, RequestNavigateEventArgs e)
        {
            try
            {
                Process.Start(new ProcessStartInfo(e.Uri.AbsoluteUri) { UseShellExecute = true });
            }
            catch (Exception)
            {
                // Ignore: a missing/blocked browser should not crash the toy.
            }
            e.Handled = true;
        }

        private void OnDrag(object sender, System.Windows.Input.MouseButtonEventArgs e)
        {
            // Don't start a window drag when the press lands on an interactive
            // element (a hyperlink or the close button) -- DragMove would capture
            // the mouse and swallow the click before it can navigate/activate.
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

        /// <summary>
        /// True if the clicked element is (or sits inside) a Hyperlink or a
        /// button. Walks the visual tree for UI elements and the logical tree for
        /// text elements (Run/Hyperlink are not Visuals).
        /// </summary>
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
