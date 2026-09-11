using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Windows;
using System.Windows.Documents;
using System.Windows.Media;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.SlopeGraphic
{
    /// <summary>
    /// Resizable Slope Statistics report window: formatted document, three
    /// clipboard formats, one-click layout text element, toast feedback --
    /// the same conventions as the Polar Plots statistics window. All values
    /// are reported in the SOURCE unit with degree equivalents in parentheses.
    /// </summary>
    public partial class SlopeStatsWindow : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private readonly SlopeStats _st;
        private readonly IReadOnlyList<string> _headers;
        private readonly string _plainText;
        private static readonly CultureInfo CI = CultureInfo.InvariantCulture;

        internal SlopeStatsWindow(SlopeStats st, IReadOnlyList<string> headers)
        {
            InitializeComponent();
            _st = st;
            _headers = headers;
            _plainText = BuildPlain();
            rtb.Document = BuildDocument();
        }

        // ---- report content -------------------------------------------------
        private IEnumerable<(string name, string value, string refs)> StatLines()
        {
            var st = _st;
            yield return ("Count of values analyzed", st.Count.ToString("N0", CI), null);
            if (!st.WeightsAreCounts && st.TotalWeight > 0 &&
                Math.Abs(st.TotalWeight - st.Count) > 1e-9)
                yield return ("Total weight", st.TotalWeight.ToString("N4", CI), null);
            yield return ("Mean Slope", st.FmtBoth(st.Mean), null);
            yield return ("Median Slope", st.FmtBoth(st.Median), null);
            yield return ("Standard Deviation",
                st.SdDeg.ToString("F3", CI) + "° (of the slope angles)", null);
            yield return ("Minimum Slope", st.FmtBoth(st.Min), null);
            yield return ("Maximum Slope", st.FmtBoth(st.Max), null);
            yield return ("5th / 95th Percentiles",
                st.FmtBoth(st.Quantile(0.05)) + "  /  " + st.FmtBoth(st.Quantile(0.95)), null);
            yield return ("Mean Slope 95% CI", st.CiText(), "[A]");
            if (st.Unit != SlopeUnit.Degrees)
                yield return ("Note", "The mean, SD and CI are computed on the " +
                    "slope ANGLES and converted for display; computing them on raw " +
                    SlopeStats.UnitLabel(st.Unit) + " values would bias them high " +
                    "(the tangent is convex).", null);
        }

        private string BuildPlain()
        {
            var sb = new StringBuilder();
            sb.AppendLine("Slope Statistics");
            sb.AppendLine("-------------------------------------------------------");
            if (_headers != null && _headers.Count > 0)
            {
                sb.AppendLine("Input Parameters:");
                foreach (var h in _headers) sb.AppendLine("   " + h);
                sb.AppendLine("-------------------------------------------------------");
            }
            foreach (var (name, value, refs) in StatLines())
                sb.AppendLine(name + " = " + value + (refs != null ? "   " + refs : ""));
            if (_st.WeightsAreCounts)
                sb.AppendLine("   (Cell values binned to 0.01-degree classes for the " +
                              "median/percentiles; mean, SD, min and max are exact.)");
            sb.AppendLine("-------------------------------------------------------");
            foreach (var r in References()) sb.AppendLine(r);
            return sb.ToString();
        }

        private static string[] References() => new[]
        {
            "[A] Zar, J. H. 1999. Biostatistical Analysis, 4th ed. Prentice " +
            "Hall. ch. 7 (t-based confidence interval for a mean).",
            "Slope graphic after the Jenness Enterprises Grid Tools extension " +
            "for ArcView 3.x.",
        };

        private FlowDocument BuildDocument()
        {
            var doc = new FlowDocument
            {
                FontFamily = new FontFamily("Segoe UI"),
                FontSize = 13,
                PagePadding = new Thickness(2),
            };
            var title = new Paragraph(new Bold(new Run("Slope Statistics")))
            { FontSize = 16, TextAlignment = TextAlignment.Center, Margin = new Thickness(0, 0, 0, 10) };
            doc.Blocks.Add(title);

            if (_headers != null && _headers.Count > 0)
            {
                var hp = RulePara();
                hp.Inlines.Add(new Bold(new Run("Input Parameters")));
                foreach (var h in _headers)
                {
                    hp.Inlines.Add(new LineBreak());
                    hp.Inlines.Add(new Run("   " + h));
                }
                doc.Blocks.Add(hp);
            }

            var sp = RulePara();
            foreach (var (name, value, refs) in StatLines())
            {
                sp.Inlines.Add(new Bold(new Run(name + " = ")));
                sp.Inlines.Add(new Run(value));
                if (refs != null)
                    sp.Inlines.Add(new Run("   " + refs)
                    { Foreground = Brushes.Gray, FontSize = 11 });
                sp.Inlines.Add(new LineBreak());
            }
            if (_st.WeightsAreCounts)
                sp.Inlines.Add(new Italic(new Run(
                    "(Cell values binned to 0.01-degree classes for the " +
                    "median/percentiles; mean, SD, min and max are exact.)"))
                { FontSize = 11 });
            doc.Blocks.Add(sp);

            var rp = new Paragraph { FontSize = 11, Foreground = Brushes.Gray };
            foreach (string r in References())
            { rp.Inlines.Add(new Run(r)); rp.Inlines.Add(new LineBreak()); }
            doc.Blocks.Add(rp);
            return doc;
        }

        private static Paragraph RulePara() => new Paragraph
        {
            BorderBrush = new SolidColorBrush(Color.FromArgb(60, 0, 0, 0)),
            BorderThickness = new Thickness(0, 0, 0, 1),
            Padding = new Thickness(0, 0, 0, 8),
            Margin = new Thickness(0, 0, 0, 8),
        };

        private string BuildEsriTagged()
        {
            var sb = new StringBuilder();
            sb.AppendLine("<FNT size=\"14\"><BOL>Slope Statistics</BOL></FNT>");
            sb.AppendLine();
            if (_headers != null && _headers.Count > 0)
            {
                sb.AppendLine("<BOL>Input Parameters</BOL>");
                foreach (var h in _headers) sb.AppendLine("   " + Esc(h));
                sb.AppendLine();
            }
            foreach (var (name, value, refs) in StatLines())
            {
                sb.Append("<BOL>").Append(Esc(name)).Append(" = </BOL>").Append(Esc(value));
                if (refs != null)
                    sb.Append("  <CLR red=\"128\" green=\"128\" blue=\"128\">")
                      .Append(refs).Append("</CLR>");
                sb.AppendLine();
            }
            if (_st.WeightsAreCounts)
                sb.AppendLine("<ITA>(Cell values binned to 0.01-degree classes for " +
                              "the median/percentiles; mean, SD, min and max are exact.)</ITA>");
            sb.AppendLine();
            foreach (string r in References())
                sb.Append("<FNT size=\"7\"><CLR red=\"110\" green=\"110\" blue=\"110\">")
                  .Append(Esc(r)).AppendLine("</CLR></FNT>");
            return sb.ToString();
        }

        private static string Esc(string s) => (s ?? "")
            .Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;");

        // ---- buttons --------------------------------------------------------
        private void OnCopyRaw(object sender, RoutedEventArgs e)
        {
            if (TryClipboard(() => Clipboard.SetText(_plainText)))
                ShowToast(sender, "✓ Report copied as plain text");
        }

        private void OnCopyRtf(object sender, RoutedEventArgs e)
        {
            bool ok = TryClipboard(() =>
            {
                var range = new TextRange(rtb.Document.ContentStart, rtb.Document.ContentEnd);
                string rtf;
                using (var ms = new MemoryStream())
                {
                    range.Save(ms, DataFormats.Rtf);
                    rtf = Encoding.UTF8.GetString(ms.ToArray());
                }
                var data = new DataObject();
                data.SetData(DataFormats.Rtf, rtf);
                data.SetData(DataFormats.UnicodeText, _plainText);
                Clipboard.SetDataObject(data, true);
            });
            if (ok) ShowToast(sender, "✓ Report copied as rich text " +
                                      "(paste into Word, PowerPoint, ...)");
        }

        private void OnCopyEsri(object sender, RoutedEventArgs e)
        {
            if (TryClipboard(() => Clipboard.SetText(BuildEsriTagged())))
                ShowToast(sender, "✓ Esri-formatted text copied " +
                                  "(paste into a layout text element)");
        }

        private async void OnAddToLayout(object sender, RoutedEventArgs e)
        {
            string tagged = BuildEsriTagged();
            try
            {
                string where = await PolarPlots.LayoutPlacement
                    .AddStatsText(this, tagged, "Slope Statistics");
                if (where != null) ShowToast(sender, "✓ " + where);
            }
            catch (Exception ex) { MessageBox.Show(ex.Message, "Slope Graphic"); }
        }

        private static bool TryClipboard(Action a)
        {
            try { a(); return true; }
            catch (Exception ex)
            {
                MessageBox.Show("Could not copy to the clipboard: " + ex.Message,
                    "Slope Graphic");
                return false;
            }
        }

        // ---- transient toast (same idiom as the polar stats window) ---------
        private System.Windows.Controls.Primitives.Popup _toast;
        private System.Windows.Threading.DispatcherTimer _toastTimer;

        private void ShowToast(object nearButton, string message)
        {
            if (_toast == null)
            {
                _toast = new System.Windows.Controls.Primitives.Popup
                {
                    AllowsTransparency = true,
                    Placement = System.Windows.Controls.Primitives.PlacementMode.Top,
                    StaysOpen = true,
                    PopupAnimation = System.Windows.Controls.Primitives.PopupAnimation.Fade,
                };
                _toastTimer = new System.Windows.Threading.DispatcherTimer
                { Interval = TimeSpan.FromSeconds(2.2) };
                _toastTimer.Tick += (s2, e2) => { _toastTimer.Stop(); _toast.IsOpen = false; };
                Closed += (s2, e2) => { _toastTimer.Stop(); _toast.IsOpen = false; };
            }
            _toastTimer.Stop();
            _toast.IsOpen = false;
            _toast.PlacementTarget = nearButton as UIElement ?? this;
            _toast.Child = new System.Windows.Controls.Border
            {
                Background = new SolidColorBrush(Color.FromArgb(235, 45, 45, 45)),
                CornerRadius = new CornerRadius(4),
                Padding = new Thickness(10, 6, 10, 6),
                Margin = new Thickness(0, 0, 0, 6),
                Child = new System.Windows.Controls.TextBlock
                {
                    Text = message,
                    Foreground = Brushes.White,
                    FontSize = 12,
                    MaxWidth = 340,
                    TextWrapping = TextWrapping.Wrap,
                },
            };
            _toast.IsOpen = true;
            _toastTimer.Start();
        }

        private void OnCloseClick(object sender, RoutedEventArgs e) => Close();
    }
}
