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
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using ArcGIS.Desktop.Framework;

namespace WildlifeTools
{
    /// <summary>
    /// A small, attractive, non-modal "Thought for the Day" card: a rounded panel
    /// with a gradient border, drop shadow, a background image (loaded downscaled
    /// from the deployed Images folder, with a gradient fallback), and a fade-in.
    /// One shared instance is reused; <see cref="ShowQuote"/> manages it.
    /// </summary>
    public partial class QuoteWindow : Window
    {
        private static QuoteWindow _instance;

        private QuoteWindow()
        {
            InitializeComponent();
        }

        /// <summary>
        /// Shows the card non-modally with the given quote, reusing the single
        /// instance if it is already open. Must be called on the UI thread.
        /// </summary>
        internal static void ShowQuote(string quote, string author)
        {
            if (_instance == null)
            {
                _instance = new QuoteWindow { Owner = FrameworkApplication.Current.MainWindow };
                _instance.Closed += (_, _) => _instance = null;
            }

            _instance.SetQuote(quote, author);
            _instance.Show();
            _instance.Activate();
        }

        // Within-paragraph line spacing; the inter-paragraph gap adds 40% on top
        // of it (two successive +20% bumps).
        private const double ParagraphLineHeight = 30;
        private const double InterParagraphGap = ParagraphLineHeight * 0.4;

        private void SetQuote(string quote, string author)
        {
            // Render each paragraph as its own TextBlock so the gap between
            // paragraphs can be larger than the line spacing within a paragraph.
            QuoteParagraphs.Children.Clear();
            string[] paragraphs = (quote ?? string.Empty).Split('\n');
            for (int i = 0; i < paragraphs.Length; i++)
            {
                var block = new TextBlock
                {
                    Text = paragraphs[i],
                    Foreground = Brushes.White,
                    FontSize = 21,
                    FontStyle = FontStyles.Italic,
                    TextWrapping = TextWrapping.Wrap,
                    LineHeight = ParagraphLineHeight,
                    TextAlignment = TextAlignment.Left,
                };
                if (i > 0)
                {
                    block.Margin = new Thickness(0, InterParagraphGap, 0, 0);
                }
                QuoteParagraphs.Children.Add(block);
            }

            AuthorTextBlock.Text = string.IsNullOrWhiteSpace(author) ? string.Empty : "— " + author;
        }

        private void OnLoaded(object sender, RoutedEventArgs e)
        {
            LoadBackgroundImage();

            // Gentle fade-in.
            BeginAnimation(OpacityProperty,
                new DoubleAnimation(0, 1, new Duration(TimeSpan.FromMilliseconds(350))));
        }

        /// <summary>
        /// Sets the card background to the deployed quote_bg.png, decoded at a
        /// modest width to keep memory reasonable. If the file is missing or fails
        /// to load, the XAML gradient fallback remains.
        /// </summary>
        private void LoadBackgroundImage()
        {
            try
            {
                string installDir = Path.GetDirectoryName(
                    System.Reflection.Assembly.GetExecutingAssembly().Location);
                string imagePath = Path.Combine(installDir ?? string.Empty, "Images", "quote_bg.png");
                if (!File.Exists(imagePath))
                {
                    return;
                }

                var bitmap = new BitmapImage();
                bitmap.BeginInit();
                bitmap.CacheOption = BitmapCacheOption.OnLoad;
                bitmap.CreateOptions = BitmapCreateOptions.IgnoreImageCache;
                bitmap.DecodePixelWidth = 1040; // downscale the large source
                bitmap.UriSource = new Uri(imagePath, UriKind.Absolute);
                bitmap.EndInit();
                bitmap.Freeze();

                CardBorder.Background = new ImageBrush(bitmap) { Stretch = Stretch.UniformToFill };
            }
            catch (Exception)
            {
                // Keep the gradient fallback on any failure.
            }
        }

        private void OnDrag(object sender, System.Windows.Input.MouseButtonEventArgs e)
        {
            try
            {
                DragMove();
            }
            catch (InvalidOperationException)
            {
                // DragMove throws if the button is no longer pressed; ignore.
            }
        }

        private void OnClose(object sender, RoutedEventArgs e)
        {
            Close();
        }
    }
}
