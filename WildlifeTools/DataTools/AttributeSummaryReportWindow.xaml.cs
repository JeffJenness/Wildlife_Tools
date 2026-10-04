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
using System.Globalization;
using System.IO;
using System.Text;
using System.Windows;
using System.Windows.Documents;
using System.Windows.Media;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.DataTools
{
    /// <summary>
    /// Displays one Attribute Summary Report: bold section headers over the
    /// format-template lines, with the suite's copy conventions (raw / rich /
    /// TSV table / save to file, each confirmed by a toast).
    /// </summary>
    public partial class AttributeSummaryReportWindow
        : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private readonly string _plain;
        private readonly string _tsv;
        private static readonly CultureInfo CI = CultureInfo.InvariantCulture;

        internal AttributeSummaryReportWindow(AttributeSummary.SummaryResult r,
                                              string format)
        {
            InitializeComponent();
            _result = r;
            _format = format;
            _plain = AttributeSummary.BuildPlainReport(r, format);
            _tsv = AttributeSummary.BuildTsv(r);
            rtb.Document = BuildDocument(r, format);
        }

        private static FlowDocument BuildDocument(
            AttributeSummary.SummaryResult r, string format)
        {
            var doc = new FlowDocument
            {
                FontFamily = new FontFamily("Segoe UI"),
                FontSize = 13,
                PagePadding = new Thickness(2),
            };
            var title = new Paragraph(new Bold(new Run("Attribute Summary Report")))
            { FontSize = 16, TextAlignment = TextAlignment.Center,
              Margin = new Thickness(0, 0, 0, 8) };
            doc.Blocks.Add(title);

            bool commas = (format ?? "").IndexOf("{commas}",
                StringComparison.OrdinalIgnoreCase) >= 0;
            string num(long v) => v.ToString(commas ? "#,##0" : "0", CI);

            var head = new Paragraph
            { Margin = new Thickness(0, 0, 0, 8),
              BorderBrush = new SolidColorBrush(Color.FromArgb(70, 0, 0, 0)),
              BorderThickness = new Thickness(0, 0, 0, 1),
              Padding = new Thickness(0, 0, 0, 6) };
            head.Inlines.Add(new Bold(new Run("Dataset:  ")));
            head.Inlines.Add(new Run(r.DatasetName +
                (r.SelectedOnly ? "   (selected records only)" : "")));
            head.Inlines.Add(new LineBreak());
            head.Inlines.Add(new Bold(new Run("Records analyzed:  ")));
            head.Inlines.Add(new Run(num(r.TotalCount)));
            string totalW = AttributeSummary.TotalWeightText(r, commas);
            if (totalW.Length > 0)
            {
                int colon = totalW.IndexOf(':');
                head.Inlines.Add(new LineBreak());
                head.Inlines.Add(new Bold(new Run(
                    totalW.Substring(0, colon + 1) + "  ")));
                head.Inlines.Add(new Run(
                    totalW.Substring(colon + 1).TrimStart()));
            }
            doc.Blocks.Add(head);

            foreach (var sec in r.Sections)
            {
                var hp = new Paragraph(new Bold(new Run(sec.Title)))
                { Margin = new Thickness(0, 6, 0, 2) };
                doc.Blocks.Add(hp);
                var body = new Paragraph { Margin = new Thickness(0, 0, 0, 4) };
                long inc = 0;
                bool first = true;
                void AddLine(string text)
                {
                    if (!first) body.Inlines.Add(new LineBreak());
                    first = false;
                    body.Inlines.Add(new Run(text));
                }
                if (sec.NullCount > 0)
                {
                    inc++;
                    AddLine(AttributeSummary.NullLine(r, sec, inc, commas));
                }
                foreach (var vc in sec.Classes)
                {
                    inc++;
                    AddLine(AttributeSummary.Render(format, vc.Values, inc,
                        vc.Count, r.TotalCount, vc.Weight, r.TotalWeight,
                        r.Kind, r.CellAreaM2));
                }
                doc.Blocks.Add(body);
            }
            return doc;
        }

        // ---- buttons --------------------------------------------------------
        private void OnCopyRaw(object sender, RoutedEventArgs e)
        {
            if (TryClipboard(() => Clipboard.SetText(_plain)))
                ShowToast(sender, "✓ Report copied as plain text");
        }

        private void OnCopyRtf(object sender, RoutedEventArgs e)
        {
            bool ok = TryClipboard(() =>
            {
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
                data.SetData(DataFormats.UnicodeText, _plain);
                Clipboard.SetDataObject(data, true);
            });
            if (ok) ShowToast(sender, "✓ Report copied as rich text " +
                                      "(paste into Word, ...)");
        }

        private void OnCopyTsv(object sender, RoutedEventArgs e)
        {
            if (TryClipboard(() => Clipboard.SetText(_tsv)))
                ShowToast(sender, "✓ Table copied (paste into Excel)");
        }

        private readonly AttributeSummary.SummaryResult _result;
        private readonly string _format;

        /// <summary>The report with ArcGIS text-formatting tags (BOL headers),
        /// for layout text elements.</summary>
        private string BuildEsriTagged()
        {
            var r = _result;
            bool commas = (_format ?? "").IndexOf("{commas}",
                StringComparison.OrdinalIgnoreCase) >= 0;
            string num(long v) => v.ToString(commas ? "#,##0" : "0", CI);
            string esc(string s) => (s ?? "")
                .Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;");
            var sb = new StringBuilder();
            sb.AppendLine("<FNT size=\"14\"><BOL>Attribute Summary Report</BOL></FNT>");
            sb.AppendLine();
            sb.Append("<BOL>Dataset:</BOL>  ").Append(esc(r.DatasetName));
            if (r.SelectedOnly) sb.Append("   (selected records only)");
            sb.AppendLine();
            sb.Append("<BOL>Records analyzed:</BOL>  ")
              .AppendLine(num(r.TotalCount));
            string totalW = AttributeSummary.TotalWeightText(r, commas);
            if (totalW.Length > 0)
            {
                int colon = totalW.IndexOf(':');
                sb.Append("<BOL>").Append(esc(totalW.Substring(0, colon + 1)))
                  .Append("</BOL>  ")
                  .AppendLine(esc(totalW.Substring(colon + 1).TrimStart()));
            }
            foreach (var sec in r.Sections)
            {
                sb.AppendLine();
                sb.Append("<BOL>").Append(esc(sec.Title)).AppendLine("</BOL>");
                long inc = 0;
                if (sec.NullCount > 0)
                {
                    inc++;
                    sb.AppendLine(esc(AttributeSummary.NullLine(
                        r, sec, inc, commas)));
                }
                foreach (var vc in sec.Classes)
                {
                    inc++;
                    sb.AppendLine(esc(AttributeSummary.Render(_format, vc.Values,
                        inc, vc.Count, r.TotalCount, vc.Weight, r.TotalWeight,
                        r.Kind, r.CellAreaM2)));
                }
            }
            return sb.ToString();
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
                    .AddStatsText(this, tagged, "Attribute Summary Report");
                if (where != null) ShowToast(sender, "✓ " + where);
            }
            catch (Exception ex)
            { MessageBox.Show(ex.Message, "Attribute Summary Reports"); }
        }

        private void OnSaveFile(object sender, RoutedEventArgs e)
        {
            var dlg = new Microsoft.Win32.SaveFileDialog
            {
                Filter = "Text file (*.txt)|*.txt|Rich text (*.rtf)|*.rtf|"
                       + "Tab-separated table (*.tsv)|*.tsv",
                FileName = "AttributeSummary.txt",
                Title = "Save attribute summary report",
            };
            if (dlg.ShowDialog() != true) return;
            try
            {
                string ext = Path.GetExtension(dlg.FileName).ToLowerInvariant();
                if (ext == ".rtf")
                {
                    var range = new TextRange(rtb.Document.ContentStart,
                                              rtb.Document.ContentEnd);
                    using (var fs = new FileStream(dlg.FileName, FileMode.Create))
                        range.Save(fs, DataFormats.Rtf);
                }
                else if (ext == ".tsv")
                    File.WriteAllText(dlg.FileName, _tsv);
                else
                    File.WriteAllText(dlg.FileName, _plain);
                ShowToast(sender, "✓ Saved " + Path.GetFileName(dlg.FileName));
            }
            catch (Exception ex)
            { MessageBox.Show("Could not save: " + ex.Message,
                "Attribute Summary Reports"); }
        }

        private static bool TryClipboard(Action a)
        {
            try { a(); return true; }
            catch (Exception ex)
            {
                MessageBox.Show("Could not copy to the clipboard: " + ex.Message,
                    "Attribute Summary Reports");
                return false;
            }
        }

        private void OnHelp(object sender, RoutedEventArgs e)
            => WebHelp.Open("data-tools/attribute-summary-reports.html");

        private void OnCloseClick(object sender, RoutedEventArgs e) => Close();

        // ---- transient toast ------------------------------------------------
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
                { Interval = TimeSpan.FromSeconds(2.0) };
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
                },
            };
            _toast.IsOpen = true;
            _toastTimer.Start();
        }
    }
}
