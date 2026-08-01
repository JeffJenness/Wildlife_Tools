using System;
using System.Collections.Generic;
using System.Globalization;
using System.Windows;
using System.Windows.Documents;
using System.Windows.Media;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.PolarPlots
{
    /// <summary>
    /// Resizable window presenting the Circular Descriptive Statistics as a
    /// formatted document (RichTextBox). The plain-text version is copied to
    /// the clipboard ONLY when the user clicks "Copy to clipboard".
    /// </summary>
    public partial class PolarStatsWindow : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private readonly string _plainText;
        private static readonly CultureInfo CI = CultureInfo.InvariantCulture;

        internal PolarStatsWindow(CircularStats st, IReadOnlyList<string> headerLines,
                                  string plainText)
        {
            InitializeComponent();
            _plainText = plainText ?? "";
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
                var hp = new Paragraph { Margin = new Thickness(0, 0, 0, 8) };
                hp.Inlines.Add(new Bold(new Run("Input Parameters")));
                foreach (var h in headers)
                {
                    hp.Inlines.Add(new LineBreak());
                    hp.Inlines.Add(new Run("   " + h));
                }
                doc.Blocks.Add(hp);
                doc.Blocks.Add(Rule());
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

            var sp = new Paragraph { Margin = new Thickness(0, 6, 0, 6) };
            AddStat(sp, "Count of values analyzed", st.Count.ToString("N0", CI));
            if (st.Weighted)
                AddStat(sp, "Total weight", st.TotalWeight.ToString("N4", CI));
            AddStat(sp, "Mean Direction", double.IsNaN(st.MeanDirectionDeg)
                ? "undefined (resultant length is zero)"
                : st.MeanDirectionDeg.ToString("F5", CI) + " Degrees");
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
            doc.Blocks.Add(sp);
            doc.Blocks.Add(Rule());

            var xp = new Paragraph { Margin = new Thickness(0, 6, 0, 6) };
            AddStat(xp, "Median Direction", double.IsNaN(st.MedianDirectionDeg)
                ? "undefined" : st.MedianDirectionDeg.ToString("F5", CI) + " Degrees", "[A,B]");
            AddStat(xp, "Circular Range", st.CircularRangeDeg.ToString("F5", CI) + " Degrees", "[A,B]");
            AddStat(xp, "von Mises Concentration (kappa)", st.VonMisesKappa.ToString("F5", CI), "[A,D]");
            AddStat(xp, "Rayleigh Test of Uniformity",
                "Z = " + st.RayleighZ.ToString("F5", CI) + ", p = " + st.RayleighP.ToString("F5", CI), "[A,E]");
            if (st.Weighted)
                xp.Inlines.Add(new Italic(new Run(
                    "(Median, Range and the Rayleigh test are computed on the " +
                    "unweighted azimuths.)")) { FontSize = 11 });
            doc.Blocks.Add(xp);
            doc.Blocks.Add(Rule());

            var rp = new Paragraph { FontSize = 11, Foreground = Brushes.Gray };
            rp.Inlines.Add(new Run(
                "[A] Fisher, N. I. 1993. Statistical Analysis of Circular Data. Cambridge University Press."));
            rp.Inlines.Add(new LineBreak());
            rp.Inlines.Add(new Run(
                "[B] Mardia, K. V., and P. E. Jupp. 2000. Directional Statistics. Wiley."));
            rp.Inlines.Add(new LineBreak());
            rp.Inlines.Add(new Run(
                "[C] Batschelet, E. 1981. Circular Statistics in Biology. Academic Press."));
            rp.Inlines.Add(new LineBreak());
            rp.Inlines.Add(new Run(
                "[D] Best, D. J., and N. I. Fisher. 1981. Communications in Statistics - " +
                "Simulation and Computation 10(5):493-502."));
            rp.Inlines.Add(new LineBreak());
            rp.Inlines.Add(new Run(
                "[E] Zar, J. H. 1999. Biostatistical Analysis, 4th ed. Prentice Hall, ch. 27."));
            doc.Blocks.Add(rp);
            return doc;
        }

        private static BlockUIContainer Rule() => new BlockUIContainer(
            new System.Windows.Controls.Border
            {
                Height = 1,
                Background = new SolidColorBrush(Color.FromArgb(60, 0, 0, 0)),
                Margin = new Thickness(0, 2, 0, 2),
            });

        private void OnCopy(object sender, RoutedEventArgs e)
        {
            try
            {
                Clipboard.SetText(_plainText);
            }
            catch (Exception ex)
            {
                MessageBox.Show("Could not copy to the clipboard: " + ex.Message,
                    "Polar Plots");
            }
        }

        private void OnCloseClick(object sender, RoutedEventArgs e) => Close();
    }
}
