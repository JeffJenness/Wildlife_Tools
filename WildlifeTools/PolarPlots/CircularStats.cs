using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace WildlifeTools.PolarPlots
{
    /// <summary>
    /// Circular (directional) statistics for a weighted set of azimuths, and
    /// azimuth binning for the polar plots. Pure C# with no ArcGIS dependency so
    /// it can be reasoned about and unit-tested directly. This is the port of the
    /// original Polar Plots CircularFunctions.bas (CalcPolarStats) and produces
    /// the same values as the tool's Circular Descriptive Statistics report.
    ///
    /// Azimuths are COMPASS degrees (0 = north, increasing clockwise). Every
    /// statistic is rotation-invariant, so the compass convention carries through
    /// and the mean direction comes back in compass degrees. References:
    /// Mardia and Jupp (2000); Fisher (1993); Batschelet (1981); Zar (1999).
    /// </summary>
    internal sealed class CircularStats
    {
        // Inputs (already filtered/weighted/reversed by the caller).
        public IReadOnlyList<double> Azimuths { get; }   // compass degrees, 0-360
        public IReadOnlyList<double> Weights { get; }    // parallel; 1.0 if unweighted
        public bool Weighted { get; }

        // Results.
        public int Count { get; private set; }
        public double TotalWeight { get; private set; }
        public double SumSin { get; private set; }
        public double SumCos { get; private set; }
        public double ResultantLength { get; private set; }        // R
        public double MeanResultantLength { get; private set; }    // R-bar (0..1)
        public double MeanDirectionDeg { get; private set; }       // compass, 0-360, NaN if R=0
        public double CircularVariance { get; private set; }       // 1 - R-bar
        public double AngularVariance { get; private set; }        // 2(1 - R-bar)
        public double CircularSdRad { get; private set; }
        public double CircularSdDeg { get; private set; }
        public double AngularDeviationRad { get; private set; }
        public double AngularDeviationDeg { get; private set; }

        // Extended descriptors (computed on the UNWEIGHTED azimuths, since the
        // median/range are order statistics and the Rayleigh test is defined for
        // an unweighted sample). Cited in the report.
        public double MedianDirectionDeg { get; private set; }     // compass
        public double CircularRangeDeg { get; private set; }       // smallest arc
        public double RayleighZ { get; private set; }              // n * rbar_uw^2
        public double RayleighP { get; private set; }              // uniformity p
        public double VonMisesKappa { get; private set; }          // concentration
        public double MeanCiHalfDeg95 { get; private set; }        // NaN if undefined

        private const double DegToRad = Math.PI / 180.0;
        private const double RadToDeg = 180.0 / Math.PI;

        public CircularStats(IReadOnlyList<double> azimuths,
                             IReadOnlyList<double> weights, bool weighted)
        {
            Azimuths = azimuths ?? Array.Empty<double>();
            Weights = weights;
            Weighted = weighted;
            Compute();
        }

        private void Compute()
        {
            double s = 0.0, c = 0.0, w = 0.0;
            int n = Azimuths.Count;
            for (int i = 0; i < n; i++)
            {
                double wi = (Weights != null && i < Weights.Count) ? Weights[i] : 1.0;
                double r = Azimuths[i] * DegToRad;
                s += wi * Math.Sin(r);
                c += wi * Math.Cos(r);
                w += wi;
            }
            Count = n;
            TotalWeight = w;
            SumSin = s;
            SumCos = c;
            ResultantLength = Math.Sqrt(s * s + c * c);
            double rbar = (w > 0.0) ? ResultantLength / w : 0.0;
            if (rbar > 1.0) rbar = 1.0;               // guard FP just over 1
            MeanResultantLength = rbar;

            MeanDirectionDeg = (ResultantLength > 0.0)
                ? Mod360(Math.Atan2(s, c) * RadToDeg)
                : double.NaN;

            CircularVariance = 1.0 - rbar;
            AngularVariance = 2.0 * (1.0 - rbar);
            // Circular SD = sqrt(-2 ln R-bar); Angular deviation = sqrt(2(1 - R-bar)).
            CircularSdRad = Math.Sqrt(Math.Max(-2.0 * Math.Log(Math.Max(rbar, 1e-12)), 0.0));
            CircularSdDeg = CircularSdRad * RadToDeg;
            AngularDeviationRad = Math.Sqrt(Math.Max(2.0 * (1.0 - rbar), 0.0));
            AngularDeviationDeg = AngularDeviationRad * RadToDeg;

            ComputeExtended();
        }

        private void ComputeExtended()
        {
            int n = Azimuths.Count;
            // Unweighted resultant for the Rayleigh test + von Mises kappa.
            double su = 0.0, cu = 0.0;
            var vals = new List<double>(n);
            for (int i = 0; i < n; i++)
            {
                double a = Mod360(Azimuths[i]);
                vals.Add(a);
                double r = a * DegToRad;
                su += Math.Sin(r);
                cu += Math.Cos(r);
            }
            double rbarU = (n > 0) ? Math.Sqrt(su * su + cu * cu) / n : 0.0;
            if (rbarU > 1.0) rbarU = 1.0;

            // Rayleigh test of uniformity (Fisher 1993 eq. 4.17; Zar 1999 eq. 27.2-4).
            double z = n * rbarU * rbarU;
            RayleighZ = z;
            RayleighP = (n > 0)
                ? Clamp01(Math.Exp(-z) * (1.0
                    + (2.0 * z - z * z) / (4.0 * n)
                    - (24.0 * z - 132.0 * z * z + 76.0 * z * z * z - 9.0 * z * z * z * z)
                        / (288.0 * n * n)))
                : double.NaN;

            // von Mises concentration kappa MLE approximation (Fisher 1993
            // eq. 4.42 / Best & Fisher 1981), from the (reported) mean resultant.
            VonMisesKappa = KappaFromRbar(MeanResultantLength);

            // 95% confidence half-angle of the mean direction: von Mises
            // large-sample standard error 1/sqrt(n * r-bar * kappa) (Fisher
            // 1993 sec. 4.4.5). Undefined (NaN) when the directions are too
            // dispersed for the interval to mean anything (half-angle >= 90
            // degrees, kappa ~ 0), or when n < 2. Drawn as the confidence arc.
            MeanCiHalfDeg95 = double.NaN;
            if (Count > 1 && MeanResultantLength > 0 && VonMisesKappa > 0 &&
                !double.IsNaN(MeanDirectionDeg))
            {
                double se = 1.0 / Math.Sqrt(
                    Count * MeanResultantLength * VonMisesKappa);
                double half = 1.96 * se / DegToRad;
                if (half > 0 && half < 90.0) MeanCiHalfDeg95 = half;
            }

            // Circular median + range (order statistics; Fisher 1993 sec. 2.3.2;
            // Mardia & Jupp 2000 sec. 2.3). Computed on the unweighted azimuths.
            if (n == 0) { MedianDirectionDeg = double.NaN; CircularRangeDeg = double.NaN; return; }
            vals.Sort();
            // Circular range = 360 - largest gap between adjacent sorted angles.
            double maxGap = 360.0 - (vals[n - 1] - vals[0]);   // the wrap gap
            for (int i = 1; i < n; i++)
                maxGap = Math.Max(maxGap, vals[i] - vals[i - 1]);
            CircularRangeDeg = 360.0 - maxGap;
            MedianDirectionDeg = CircularMedian(vals);
        }

        private static double KappaFromRbar(double r)
        {
            if (r <= 0.0) return 0.0;
            if (r > 0.999999) r = 0.999999;
            double k;
            if (r < 0.53) k = 2.0 * r + r * r * r + 5.0 * r * r * r * r * r / 6.0;
            else if (r < 0.85) k = -0.4 + 1.39 * r + 0.43 / (1.0 - r);
            else k = 1.0 / (r * r * r - 4.0 * r * r + 3.0 * r);
            return Math.Min(Math.Max(k, 0.0), 1.0e4);
        }

        private static double CircularMedian(List<double> sorted)
        {
            // The observed direction minimizing the summed circular distance to
            // all others (Fisher 1993). O(n^2); fine for a stats report.
            int n = sorted.Count;
            double best = sorted[0], bestCost = double.PositiveInfinity;
            for (int i = 0; i < n; i++)
            {
                double cost = 0.0;
                for (int j = 0; j < n; j++)
                {
                    double d = Math.Abs(sorted[i] - sorted[j]);
                    if (d > 180.0) d = 360.0 - d;
                    cost += d;
                }
                if (cost < bestCost) { bestCost = cost; best = sorted[i]; }
            }
            return best;
        }

        private static double Clamp01(double x) => x < 0 ? 0 : (x > 1 ? 1 : x);

        public static double Mod360(double d)
        {
            d %= 360.0;
            if (d < 0.0) d += 360.0;
            return d;
        }

        /// <summary>
        /// Bin the (weighted) azimuths into <paramref name="binCount"/> equal
        /// bins around the circle, each centered on its azimuth. Bin 0 is
        /// centered on 0 deg (north); a value's bin is the nearest center. Returns
        /// the summed weight (or count, if unweighted) in each bin.
        /// </summary>
        public double[] Bin(int binCount)
        {
            if (binCount < 1) binCount = 1;
            double[] bins = new double[binCount];
            double width = 360.0 / binCount;
            int n = Azimuths.Count;
            for (int i = 0; i < n; i++)
            {
                double wi = (Weights != null && i < Weights.Count) ? Weights[i] : 1.0;
                // Center bin b on b*width; a value falls in the nearest center.
                double a = Mod360(Azimuths[i]);
                int b = (int)Math.Floor((a + width / 2.0) / width);
                if (b >= binCount) b -= binCount;      // wrap the top half-bin
                if (b < 0) b += binCount;
                bins[b] += wi;
            }
            return bins;
        }

        /// <summary>The center azimuth (compass degrees) of bin <c>b</c>.</summary>
        public static double BinCenterDeg(int b, int binCount) => b * (360.0 / binCount);

        /// <summary>
        /// The report text shown by the original tool's statistics window
        /// (Circular Descriptive Statistics), suitable for a layout text box or
        /// the clipboard. <paramref name="header"/> lines describe the inputs.
        /// </summary>
        /// <summary>"141.20000 ± 8.40000 Degrees (132.8° to 149.6°)", or the
        /// undefined explanation. Shared by every report format so the text
        /// always matches the plotted confidence arc.</summary>
        public string MeanCiText(CultureInfo ci)
        {
            if (double.IsNaN(MeanCiHalfDeg95))
                return "undefined (directions too dispersed)";
            double lo = Mod360(MeanDirectionDeg - MeanCiHalfDeg95);
            double hi = Mod360(MeanDirectionDeg + MeanCiHalfDeg95);
            return MeanDirectionDeg.ToString("F5", ci) + " ± " +
                   MeanCiHalfDeg95.ToString("F5", ci) + " Degrees (" +
                   lo.ToString("F1", ci) + "° to " + hi.ToString("F1", ci) + "°)";
        }

        public string BuildReport(IEnumerable<string> headerLines)
        {
            var sb = new StringBuilder();
            var ci = CultureInfo.InvariantCulture;
            sb.AppendLine("Circular Descriptive Statistics");
            sb.AppendLine("-------------------------------------------------------");
            if (headerLines != null)
            {
                sb.AppendLine("Input Parameters:");
                foreach (var h in headerLines) sb.AppendLine("   " + h);
                sb.AppendLine("-------------------------------------------------------");
            }
            sb.AppendLine("Count of values analyzed = " + Count.ToString("N0", ci));
            if (Weighted)
                sb.AppendLine("Total weight = " + TotalWeight.ToString("N4", ci));
            string meanTxt = double.IsNaN(MeanDirectionDeg)
                ? "undefined (resultant length is zero)"
                : MeanDirectionDeg.ToString("F5", ci) + " Degrees";
            sb.AppendLine("Mean Direction = " + meanTxt);
            sb.AppendLine("Resultant Length = " + ResultantLength.ToString("F5", ci));
            sb.AppendLine("Mean Resultant Length = " + MeanResultantLength.ToString("F4", ci));
            sb.AppendLine("Circular Variance = " + CircularVariance.ToString("F4", ci) + "   [A,B]");
            sb.AppendLine("Angular Variance = " + AngularVariance.ToString("F5", ci) + "   [C]");
            sb.AppendLine("Circular Standard Deviation (Radians) = " +
                          CircularSdRad.ToString("F5", ci) + " Radians   [A,B]");
            sb.AppendLine("Circular Standard Deviation (Degrees) = " +
                          CircularSdDeg.ToString("F5", ci) + " Degrees   [A,B]");
            sb.AppendLine("Angular Deviation (Radians) = " +
                          AngularDeviationRad.ToString("F5", ci) + " Radians   [C]");
            sb.AppendLine("Angular Deviation (Degrees) = " +
                          AngularDeviationDeg.ToString("F5", ci) + " Degrees   [C]");
            sb.AppendLine("-------------------------------------------------------");
            // Extended descriptors (computed on the unweighted azimuths).
            string medTxt = double.IsNaN(MedianDirectionDeg)
                ? "undefined" : MedianDirectionDeg.ToString("F5", ci) + " Degrees";
            sb.AppendLine("Median Direction = " + medTxt + "   [A,B]");
            sb.AppendLine("Circular Range = " + CircularRangeDeg.ToString("F5", ci) +
                          " Degrees   [A,B]");
            sb.AppendLine("von Mises Concentration (kappa) = " +
                          VonMisesKappa.ToString("F5", ci) + "   [A,D]");
            sb.AppendLine("Mean Direction 95% CI = " + MeanCiText(ci) + "   [A,D]");
            sb.AppendLine("Rayleigh Test of Uniformity: Z = " +
                          RayleighZ.ToString("F5", ci) + ", p = " +
                          RayleighP.ToString("F5", ci) + "   [A,E]");
            if (Weighted)
                sb.AppendLine("   (Median, Range and the Rayleigh test are computed " +
                              "on the unweighted azimuths.)");
            sb.AppendLine("-------------------------------------------------------");
            sb.AppendLine("[A] Fisher, N. I. 1993. Statistical Analysis of Circular Data. " +
                          "Cambridge University Press.");
            sb.AppendLine("[B] Mardia, K. V., and P. E. Jupp. 2000. Directional Statistics. " +
                          "Wiley.");
            sb.AppendLine("[C] Batschelet, E. 1981. Circular Statistics in Biology. " +
                          "Academic Press.");
            sb.AppendLine("[D] Best, D. J., and N. I. Fisher. 1981. The bias of the maximum " +
                          "likelihood estimators of the von Mises-Fisher concentration " +
                          "parameters. Communications in Statistics - Simulation and " +
                          "Computation 10(5):493-502.");
            sb.AppendLine("[E] Zar, J. H. 1999. Biostatistical Analysis, 4th ed. " +
                          "Prentice Hall. ch. 27.");
            sb.AppendLine("Circular statistics after Jenness Enterprises Polar Plots.");
            return sb.ToString();
        }
    }
}
