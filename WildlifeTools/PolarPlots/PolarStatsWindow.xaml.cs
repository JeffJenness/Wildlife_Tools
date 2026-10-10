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
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Windows;
using System.Windows.Documents;
using System.Windows.Media;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.PolarPlots
{
    /// <summary>
    /// Resizable window presenting the Circular Descriptive Statistics as a
    /// formatted document (RichTextBox). Nothing touches the clipboard until
    /// the user clicks one of the copy buttons:
    ///   Copy Raw Text  -- plain text;
    ///   Copy Rich Text -- RTF serialized from the displayed document itself,
    ///                     so Word/PowerPoint paste exactly what is shown;
    ///   Copy Esri-formatted Text -- text carrying ArcGIS text-formatting tags
    ///                     (BOL / CLR / FNT), for pasting into a layout TEXT
    ///                     element where ArcGIS renders the formatting.
    /// </summary>
    public partial class PolarStatsWindow : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private readonly string _plainText;
        private readonly CircularStats _stats;
        private readonly IReadOnlyList<string> _headers;
        private static readonly CultureInfo CI = CultureInfo.InvariantCulture;

        internal PolarStatsWindow(CircularStats st, IReadOnlyList<string> headerLines,
                                  string plainText)
        {
            InitializeComponent();
            global::WildlifeTools.Corridor.JennessIcon.Apply(this);
            _plainText = plainText ?? "";
            _stats = st;
            _headers = headerLines;
            rtb.Document = BuildDocument(st, headerLines);
        }

        private static FlowDocument BuildDocument(CircularStats st,
                                                  IReadOnlyList<string> headers)
        {
            var doc = new FlowDocument
            {
                FontFamily = new FontFamily("Segoe UI"),
                FontSize = 13,
                PagePadding = new Thickness(2),
            };

            var title = new Paragraph(new Bold(new Run("Circular Descriptive Statistics")))
            { FontSize = 16, TextAlignment = TextAlignment.Center, Margin = new Thickness(0, 0, 0, 10) };
            doc.Blocks.Add(title);

            if (headers != null && headers.Count > 0)
            {
                var hp = RulePara();
                hp.Inlines.Add(new Bold(new Run("Input Parameters")));
                foreach (var h in headers)
                {
                    hp.Inlines.Add(new LineBreak());
                    hp.Inlines.Add(new Run("   " + h));
                }
                doc.Blocks.Add(hp);
            }

            void AddStat(Paragraph p, string name, string value, string refs = null)
            {
                p.Inlines.Add(new Bold(new Run(name + " = ")));
                p.Inlines.Add(new Run(value));
                if (!string.IsNullOrEmpty(refs))
                    p.Inlines.Add(new Run("   " + refs)
                    { Foreground = Brushes.Gray, FontSize = 11 });
                p.Inlines.Add(new LineBreak());
            }

            var sp = RulePara();
            AddStat(sp, "Count of values analyzed", st.Count.ToString("N0", CI));
            if (st.Weighted && !st.WeightsAreCounts)
                AddStat(sp, "Total weight", st.TotalWeight.ToString("N4", CI));
            AddStat(sp, "Mean " + st.DirWord, double.IsNaN(st.MeanDirectionDeg)
                ? "undefined (resultant length is zero)"
                : st.DirectionText(st.MeanDirectionDeg, CI));
            AddStat(sp, "Resultant Length", st.ResultantLength.ToString("F5", CI));
            AddStat(sp, "Mean Resultant Length", st.MeanResultantLength.ToString("F4", CI));
            AddStat(sp, "Circular Variance", st.CircularVariance.ToString("F4", CI), "[A,B]");
            AddStat(sp, "Angular Variance", st.AngularVariance.ToString("F5", CI), "[C]");
            AddStat(sp, "Circular Standard Deviation",
                st.CircularSdRad.ToString("F5", CI) + " Radians = " +
                st.CircularSdDeg.ToString("F5", CI) + " Degrees", "[A,B]");
            AddStat(sp, "Angular Deviation",
                st.AngularDeviationRad.ToString("F5", CI) + " Radians = " +
                st.AngularDeviationDeg.ToString("F5", CI) + " Degrees", "[C]");
            if (st.Axial)
                AddStat(sp, "Angular Deviation of the Axes",
                    st.AxialAngularDeviationDeg.ToString("F5", CI) + " Degrees", "[C]");
            doc.Blocks.Add(sp);

            var xp = RulePara();
            AddStat(xp, "Median " + st.DirWord, double.IsNaN(st.MedianDirectionDeg)
                ? "undefined" : st.DirectionText(st.MedianDirectionDeg, CI), "[A,B]");
            AddStat(xp, "Circular Range", st.CircularRangeDeg.ToString("F5", CI) + " Degrees", "[A,B]");
            AddStat(xp, "von Mises Concentration (kappa)", st.VonMisesKappa.ToString("F5", CI), "[A,D]");
            AddStat(xp, "Mean " + st.DirWord + " 95% CI", st.MeanCiText(CI), "[A,D]");
            AddStat(xp, "Rayleigh Test of Uniformity",
                "Z = " + st.RayleighZ.ToString("F5", CI) + ", p = " + st.RayleighP.ToString("F5", CI), "[A,E]");
            var notes = st.Notes();
            for (int i = 0; i < notes.Count; i++)
            {
                if (i > 0) xp.Inlines.Add(new LineBreak());
                xp.Inlines.Add(new Italic(new Run(notes[i])) { FontSize = 11 });
            }
            doc.Blocks.Add(xp);

            var rp = new Paragraph { FontSize = 11, Foreground = Brushes.Gray };
            foreach (string r in References(st.KishApplied))
            {
                rp.Inlines.Add(new Run(r));
                rp.Inlines.Add(new LineBreak());
            }
            doc.Blocks.Add(rp);
            return doc;
        }

        private static string[] References(bool kish)
        {
            var refs = new List<string>
            {
                "[A] Fisher, N. I. 1993. Statistical Analysis of Circular Data. Cambridge University Press.",
                "[B] Mardia, K. V., and P. E. Jupp. 2000. Directional Statistics. Wiley.",
                "[C] Batschelet, E. 1981. Circular Statistics in Biology. Academic Press.",
                "[D] Best, D. J., and N. I. Fisher. 1981. Communications in Statistics - Simulation and Computation 10(5):493-502.",
                "[E] Zar, J. H. 1999. Biostatistical Analysis, 4th ed. Prentice Hall, ch. 27 (Rayleigh p: eq. 27.4).",
            };
            if (kish)
                refs.Add("[F] Kish, L. 1992. Weighting for unequal Pi. Journal of Official " +
                         "Statistics 8:183-200.");
            return refs.ToArray();
        }

        /// <summary>A paragraph with a bottom hairline border -- unlike a
        /// BlockUIContainer, a paragraph border survives RTF serialization, so
        /// the section rules paste into Word.</summary>
        private static Paragraph RulePara() => new Paragraph
        {
            Margin = new Thickness(0, 4, 0, 8),
            Padding = new Thickness(0, 0, 0, 6),
            BorderBrush = new SolidColorBrush(Color.FromArgb(70, 0, 0, 0)),
            BorderThickness = new Thickness(0, 0, 0, 1),
        };

        // ---- copy buttons ---------------------------------------------------
        private void OnCopyRaw(object sender, RoutedEventArgs e)
        {
            if (TryClipboard(() => Clipboard.SetText(_plainText)))
                ShowToast(sender, "✓ Report copied as plain text");
        }

        private void OnCopyRtf(object sender, RoutedEventArgs e)
        {
            bool ok = TryClipboard(() =>
            {
                // Serialize the displayed document itself, so what pastes into
                // Word is exactly what this window shows.
                var range = new TextRange(rtb.Document.ContentStart,
                                          rtb.Document.ContentEnd);
                string rtf;
                using (var ms = new MemoryStream())
                {
                    range.Save(ms, DataFormats.Rtf);
                    rtf = Encoding.UTF8.GetString(ms.ToArray());
                }
                var data = new DataObject();
                data.SetData(DataFormats.Rtf, rtf);
                data.SetData(DataFormats.UnicodeText, _plainText); // plain fallback
                Clipboard.SetDataObject(data, true);
            });
            if (ok)
                ShowToast(sender, "✓ Report copied as rich text " +
                                  "(paste into Word, PowerPoint, ...)");
        }

        private void OnCopyEsri(object sender, RoutedEventArgs e)
        {
            if (TryClipboard(() => Clipboard.SetText(BuildEsriTagged())))
                ShowToast(sender, "✓ Esri-formatted text copied " +
                                  "(paste into a layout text element)");
        }

        /// <summary>One-click: place the report as a formatted text element on
        /// the active layout (the same Esri-tagged text as Copy Esri-formatted
        /// Text, no clipboard round-trip).</summary>
        private async void OnAddToLayout(object sender, RoutedEventArgs e)
        {
            string tagged = BuildEsriTagged();
            try
            {
                string where = await LayoutPlacement.AddStatsText(this,
                    tagged, "Circular Descriptive Statistics");
                if (where != null) ShowToast(sender, "✓ " + where);
            }
            catch (Exception ex)
            {
                MessageBox.Show(ex.Message, "Polar Plots");
            }
        }

        // ---- transient toast ------------------------------------------------
        // Small self-dismissing confirmation bubble above the clicked button,
        // so every Copy / Add click gives visible feedback without a modal
        // dialog to dismiss.
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
                _toastTimer.Tick += (s2, e2) =>
                { _toastTimer.Stop(); _toast.IsOpen = false; };
                Closed += (s2, e2) =>
                { _toastTimer.Stop(); _toast.IsOpen = false; };
            }
            _toastTimer.Stop();
            _toast.IsOpen = false;                 // restart if already showing
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

        /// <summary>
        /// The report as text with ArcGIS text-formatting tags (see Esri's
        /// "Text formatting tags" help page): BOL for the stat names, CLR for
        /// the gray citation markers, FNT size for the heading. Paste into a
        /// layout TEXT element's text and ArcGIS renders the formatting.
        /// </summary>
        private string BuildEsriTagged()
        {
            var sb = new StringBuilder();
            var st = _stats;
            sb.AppendLine("<FNT size=\"14\"><BOL>Circular Descriptive Statistics</BOL></FNT>");
            sb.AppendLine();
            if (_headers != null && _headers.Count > 0)
            {
                sb.AppendLine("<BOL>Input Parameters</BOL>");
                foreach (var h in _headers) sb.AppendLine("   " + EscapeEsri(h));
                sb.AppendLine();
            }
            void Line(string name, string value, string refs = null)
            {
                sb.Append("<BOL>").Append(EscapeEsri(name)).Append(" = </BOL>")
                  .Append(EscapeEsri(value));
                if (!string.IsNullOrEmpty(refs))
                    sb.Append("  <CLR red=\"128\" green=\"128\" blue=\"128\">")
                      .Append(refs).Append("</CLR>");
                sb.AppendLine();
            }
            Line("Count of values analyzed", st.Count.ToString("N0", CI));
            if (st.Weighted && !st.WeightsAreCounts)
                Line("Total weight", st.TotalWeight.ToString("N4", CI));
            Line("Mean " + st.DirWord, double.IsNaN(st.MeanDirectionDeg)
                ? "undefined (resultant length is zero)"
                : st.DirectionText(st.MeanDirectionDeg, CI));
            Line("Resultant Length", st.ResultantLength.ToString("F5", CI));
            Line("Mean Resultant Length", st.MeanResultantLength.ToString("F4", CI));
            Line("Circular Variance", st.CircularVariance.ToString("F4", CI), "[A,B]");
            Line("Angular Variance", st.AngularVariance.ToString("F5", CI), "[C]");
            Line("Circular Standard Deviation",
                st.CircularSdRad.ToString("F5", CI) + " Radians = " +
                st.CircularSdDeg.ToString("F5", CI) + " Degrees", "[A,B]");
            Line("Angular Deviation",
                st.AngularDeviationRad.ToString("F5", CI) + " Radians = " +
                st.AngularDeviationDeg.ToString("F5", CI) + " Degrees", "[C]");
            if (st.Axial)
                Line("Angular Deviation of the Axes",
                    st.AxialAngularDeviationDeg.ToString("F5", CI) + " Degrees", "[C]");
            sb.AppendLine();
            Line("Median " + st.DirWord, double.IsNaN(st.MedianDirectionDeg)
                ? "undefined" : st.DirectionText(st.MedianDirectionDeg, CI), "[A,B]");
            Line("Circular Range", st.CircularRangeDeg.ToString("F5", CI) + " Degrees", "[A,B]");
            Line("von Mises Concentration (kappa)", st.VonMisesKappa.ToString("F5", CI), "[A,D]");
            Line("Mean " + st.DirWord + " 95% CI", st.MeanCiText(CI), "[A,D]");
            Line("Rayleigh Test of Uniformity",
                "Z = " + st.RayleighZ.ToString("F5", CI) + ", p = " +
                st.RayleighP.ToString("F5", CI), "[A,E]");
            foreach (string note in st.Notes())
                sb.Append("<ITA>").Append(EscapeEsri(note)).AppendLine("</ITA>");
            sb.AppendLine();
            foreach (string r in References(st.KishApplied))
                sb.Append("<FNT size=\"7\"><CLR red=\"110\" green=\"110\" blue=\"110\">")
                  .Append(EscapeEsri(r)).AppendLine("</CLR></FNT>");
            return sb.ToString();
        }

        /// <summary>ArcGIS parses formatting tags as XML, so literal &amp;, &lt;
        /// and &gt; in the visible text must be escaped.</summary>
        private static string EscapeEsri(string s) => (s ?? "")
            .Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;");

        private static bool TryClipboard(Action a)
        {
            try { a(); return true; }
            catch (Exception ex)
            {
                MessageBox.Show("Could not copy to the clipboard: " + ex.Message,
                    "Polar Plots");
                return false;
            }
        }

        private void OnCloseClick(object sender, RoutedEventArgs e) => Close();
    }
}
