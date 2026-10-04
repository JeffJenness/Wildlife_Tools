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

        /// <summary>True when each weight is a COUNT of identical observations
        /// (a binned raster read, or a Value/Count attribute table): the
        /// effective sample size is then the total weight, and the extended
        /// statistics (Rayleigh, median, CI) are computed weighted rather than
        /// on the record list, which would treat every occupied bin equally.</summary>
        public bool WeightsAreCounts { get; }

        /// <summary>True when the counts come from a raster read binned to
        /// 0.1-degree classes (as opposed to a table's count field).</summary>
        public bool Binned { get; }

        /// <summary>Axial data: the azimuths given are the DOUBLED angles of
        /// lines that run both ways (Batschelet 1981, sec. 1.6). The mean
        /// direction, median, circular range and confidence half-angle are
        /// halved back to the original scale (the mean axis, which also runs
        /// the opposite way); AxialAngularDeviationDeg is the halved angular
        /// deviation (Batschelet eq. 2.3.5). The mean resultant length, the
        /// variances, circular SD, kappa and the Rayleigh test describe the
        /// doubled angles.</summary>
        public bool Axial { get; }
        public double AxialAngularDeviationDeg { get; private set; } = double.NaN;

        // Results.
        public long Count { get; private set; }
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
        /// <summary>The circular median (compass degrees; an axis, halved,
        /// for axial data). Computed on first use: it compares every
        /// direction with every other, which is far too slow to repeat on
        /// each preview redraw of a large dataset, and only the statistics
        /// report shows it.</summary>
        public double MedianDirectionDeg
        {
            get
            {
                if (!_medianDone)
                {
                    _median = ComputeMedian();
                    _medianDone = true;
                }
                return _median;
            }
        }
        private bool _medianDone;
        private double _median = double.NaN;

        /// <summary>The n behind kappa's small-sample correction and the
        /// confidence interval: the count of observations, or, for weights
        /// that are not counts, Kish's (1992, eq. 4.3) effective sample size
        /// (sum w)^2 / (sum w^2) -- the same rule as the Slope Graphic and
        /// Aspect Focal Statistics.</summary>
        public double EffectiveN { get; private set; }

        /// <summary>True when EffectiveN is a Kish effective sample size that
        /// differs from the record count.</summary>
        public bool KishApplied { get; private set; }
        private double _sumW2;
        public double CircularRangeDeg { get; private set; }       // smallest arc
        public double RayleighZ { get; private set; }              // n * rbar_uw^2
        public double RayleighP { get; private set; }              // uniformity p
        public double VonMisesKappa { get; private set; }          // concentration
        public double MeanCiHalfDeg95 { get; private set; }        // NaN if undefined

        /// <summary>True when kappa carries Fisher's small-sample correction
        /// (n &lt;= 15; Fisher 1993 eq. 4.41).</summary>
        public bool KappaAdjusted { get; private set; }

        /// <summary>True when a confidence interval is reported although the
        /// sample lies outside the range Fisher (1993, Table 4.3) recommends
        /// for the standard-error interval (he advises a bootstrap there).</summary>
        public bool CiOutsideFisherRange { get; private set; }

        /// <summary>Resultants shorter than this are rounding residue of
        /// directions that cancel exactly (reverse azimuths, opposed pairs):
        /// the mean direction is then undefined, not whatever angle the
        /// residue happens to point in.</summary>
        internal const double ZeroRbar = 1e-9;

        private const double DegToRad = Math.PI / 180.0;
        private const double RadToDeg = 180.0 / Math.PI;

        public CircularStats(IReadOnlyList<double> azimuths,
                             IReadOnlyList<double> weights, bool weighted,
                             bool weightsAreCounts = false, bool? binned = null,
                             bool axial = false)
        {
            Azimuths = azimuths ?? Array.Empty<double>();
            Weights = weights;
            Weighted = weighted;
            WeightsAreCounts = weightsAreCounts;
            Binned = binned ?? weightsAreCounts;
            Axial = axial;
            Compute();
            if (Axial)
            {
                // Back to the scale of the lines: halve every angle-valued
                // result. Each is an axis, so it also holds 180 degrees on.
                if (!double.IsNaN(MeanDirectionDeg)) MeanDirectionDeg /= 2.0;
                if (!double.IsNaN(CircularRangeDeg)) CircularRangeDeg /= 2.0;
                if (!double.IsNaN(MeanCiHalfDeg95)) MeanCiHalfDeg95 /= 2.0;
                AxialAngularDeviationDeg = AngularDeviationDeg / 2.0;
            }
        }

        /// <summary>"16.80000 / 196.80000 Degrees" for an axis, or the plain
        /// direction text.</summary>
        public string DirectionText(double deg, CultureInfo ci)
        {
            if (double.IsNaN(deg)) return "undefined";
            if (!Axial) return deg.ToString("F5", ci) + " Degrees";
            return deg.ToString("F5", ci) + " / " +
                   Mod360(deg + 180.0).ToString("F5", ci) + " Degrees";
        }

        /// <summary>Report label for the mean, median and interval lines:
        /// "Mean Direction" or, for axial data, "Mean Axis".</summary>
        public string DirWord => Axial ? "Axis" : "Direction";

        private void Compute()
        {
            double s = 0.0, c = 0.0, w = 0.0, w2 = 0.0;
            int n = Azimuths.Count;
            for (int i = 0; i < n; i++)
            {
                double wi = (Weights != null && i < Weights.Count) ? Weights[i] : 1.0;
                double r = Azimuths[i] * DegToRad;
                s += wi * Math.Sin(r);
                c += wi * Math.Cos(r);
                w += wi;
                w2 += wi * wi;
            }
            _sumW2 = w2;
            // For count-weighted data the sample size is the number of
            // underlying observations (cells), not the number of bins.
            Count = WeightsAreCounts ? (long)Math.Round(w) : n;
            TotalWeight = w;
            SumSin = s;
            SumCos = c;
            ResultantLength = Math.Sqrt(s * s + c * c);
            double rbar = (w > 0.0) ? ResultantLength / w : 0.0;
            if (rbar > 1.0) rbar = 1.0;               // guard FP just over 1
            if (rbar < ZeroRbar)                      // cancelled: FP residue only
            {
                rbar = 0.0;
                ResultantLength = 0.0;
            }
            MeanResultantLength = rbar;

            MeanDirectionDeg = (rbar > 0.0)
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

            // Rayleigh test of uniformity. Z = n r-bar^2 (Zar 1999 eq. 27.2);
            // p from Zar 1999 eq. 27.4 (derived from Greenwood and Durand
            // 1955), with Rayleigh's R = n r-bar:
            //   p = exp[ sqrt(1 + 4n + 4(n^2 - R^2)) - (1 + 2n) ]
            // the same formula as Topography along Lines and the aspect tools
            // (unified 2026-09-24; previously a series approximation within
            // 0.0002 of it). For count-weighted data (raster bins /
            // Value+Count tables) the sample is the underlying observations,
            // so use the weighted r-bar and the total count as n.
            double nEff = WeightsAreCounts ? TotalWeight : n;
            double rbarEff = WeightsAreCounts ? MeanResultantLength : rbarU;
            double z = nEff * rbarEff * rbarEff;
            RayleighZ = z;
            double rEff = nEff * rbarEff;
            RayleighP = (nEff > 0)
                ? Clamp01(Math.Exp(Math.Sqrt(Math.Max(
                      1.0 + 4.0 * nEff + 4.0 * (nEff * nEff - rEff * rEff), 0.0))
                    - (1.0 + 2.0 * nEff)))
                : double.NaN;

            // von Mises concentration kappa: Best and Fisher's (1981 p. 495)
            // piecewise approximation to the maximum-likelihood estimate
            // (Fisher 1993 eq. 4.40), with their small-sample correction
            // (p. 498; Fisher 1993 eq. 4.41, applied for n <= 15 -- the
            // ArcMap Polar Plots tool applied the same one).
            // n for kappa's correction and the interval: the observations
            // themselves when the weights are counts (or absent); otherwise
            // Kish's effective sample size, so a few heavy records do not
            // pass for many.
            double nCi = Count;
            KishApplied = false;
            if (Weighted && !WeightsAreCounts && _sumW2 > 0.0)
            {
                double kish = TotalWeight * TotalWeight / _sumW2;
                if (kish < nCi - 1e-9)
                {
                    nCi = kish;
                    KishApplied = true;
                }
            }
            EffectiveN = nCi;
            VonMisesKappa = KappaSmallSample(
                KappaFromRbar(MeanResultantLength), nCi);
            KappaAdjusted = nCi > 0 && nCi <= 15;

            // 95% confidence interval of the mean direction (Fisher 1993
            // sec. 4.5.5): circular standard error 1/sqrt(n * r-bar * kappa)
            // (eq. 4.42), half-angle arcsin(1.96 * s.e.) (eq. 4.43).
            // Undefined (NaN) when 1.96 * s.e. reaches 1 (the directions are
            // too dispersed for the interval to exist), or when n < 2. Drawn
            // as the confidence arc.
            MeanCiHalfDeg95 = double.NaN;
            CiOutsideFisherRange = false;
            if (nCi > 1 && MeanResultantLength > 0 && VonMisesKappa > 0 &&
                !double.IsNaN(MeanDirectionDeg))
            {
                double x = 1.96 / Math.Sqrt(
                    nCi * MeanResultantLength * VonMisesKappa);
                if (x > 0 && x < 1.0)
                {
                    MeanCiHalfDeg95 = Math.Asin(x) * RadToDeg;
                    CiOutsideFisherRange =
                        !FisherIntervalSuitable(VonMisesKappa, nCi);
                }
            }

            // Circular median + range (order statistics; Fisher 1993 sec. 2.3.2;
            // Mardia & Jupp 2000 sec. 2.3). Computed on the unweighted azimuths.
            if (n == 0) { CircularRangeDeg = double.NaN; return; }
            vals.Sort();
            // Circular range = 360 - largest gap between adjacent sorted angles.
            double maxGap = 360.0 - (vals[n - 1] - vals[0]);   // the wrap gap
            for (int i = 1; i < n; i++)
                maxGap = Math.Max(maxGap, vals[i] - vals[i - 1]);
            CircularRangeDeg = 360.0 - maxGap;
            // Range is about which directions are OCCUPIED, so the sorted
            // distinct list serves both modes. The median (which must respect
            // counts) is computed on demand: see MedianDirectionDeg.
        }

        private double ComputeMedian()
        {
            int n = Azimuths.Count;
            if (n == 0) return double.NaN;
            double m;
            if (WeightsAreCounts) m = WeightedCircularMedian();
            else
            {
                var vals = new List<double>(n);
                for (int i = 0; i < n; i++) vals.Add(Mod360(Azimuths[i]));
                vals.Sort();
                m = CircularMedian(vals);
            }
            // Axial data: back to the scale of the lines, like the mean.
            if (Axial && !double.IsNaN(m)) m /= 2.0;
            return m;
        }

        /// <summary>Circular median for count-weighted data: the observed
        /// direction minimizing the count-weighted summed circular distance
        /// (Fisher 1993, generalized). O(k^2) over the k occupied bins.</summary>
        private double WeightedCircularMedian()
        {
            int k = Azimuths.Count;
            if (k == 0) return double.NaN;
            double best = Azimuths[0], bestCost = double.PositiveInfinity;
            for (int i = 0; i < k; i++)
            {
                double c = Azimuths[i], cost = 0.0;
                for (int j = 0; j < k; j++)
                {
                    double d = Math.Abs(Mod360(Azimuths[j] - c));
                    if (d > 180.0) d = 360.0 - d;
                    double wj = (Weights != null && j < Weights.Count) ? Weights[j] : 1.0;
                    cost += wj * d;
                    if (cost >= bestCost) { cost = double.PositiveInfinity; break; }
                }
                if (cost < bestCost) { bestCost = cost; best = c; }
            }
            return Mod360(best);
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

        /// <summary>Best and Fisher's (1981, p. 498) correction of the
        /// maximum-likelihood kappa for small samples (Fisher 1993 eq. 4.41,
        /// applied for n &lt;= 15), where the uncorrected estimate is
        /// biased upward.</summary>
        internal static double KappaSmallSample(double kml, double n)
        {
            if (n < 1 || n > 15 || kml <= 0.0) return kml;
            double dn = n;
            if (kml < 2.0)
                return Math.Max(kml - 2.0 / (dn * kml), 0.0);
            return (dn - 1) * (dn - 1) * (dn - 1) * kml / (dn * dn * dn + dn);
        }

        /// <summary>Fisher's (1993) Table 4.3: whether the standard-error
        /// interval (rather than a bootstrap) is the recommended method for
        /// this kappa and sample size.</summary>
        internal static bool FisherIntervalSuitable(double kappa, double n)
        {
            if (kappa >= 2.0) return true;
            if (kappa >= 1.5) return n >= 10;
            if (kappa >= 1.0) return n >= 15;
            if (kappa >= 0.4) return n >= 25;
            return false;
        }

        /// <summary>The cautions that follow the extended statistics, shared
        /// by every report format.</summary>
        public List<string> Notes()
        {
            var ci = CultureInfo.InvariantCulture;
            var notes = new List<string>();
            if (Axial)
                notes.Add("(Axial data: the statistics are computed on the " +
                          "doubled angles, Batschelet 1981 sec. 1.6. The mean " +
                          "axis, median axis, circular range, confidence " +
                          "interval and the angular deviation of the axes are " +
                          "halved back to the scale of the lines; the mean " +
                          "resultant length, variances, circular standard " +
                          "deviation, kappa and the Rayleigh test describe the " +
                          "doubled angles.)");
            if (WeightsAreCounts && Binned)
                notes.Add("(Values counted into 0.1-degree classes, each " +
                          "represented by the mean of its values; statistics " +
                          "computed on the per-class cell counts.)");
            else if (WeightsAreCounts)
                notes.Add("(Weights are counts of observations; every statistic " +
                          "is computed on the full distribution, with n the " +
                          "total count.)");
            else if (Weighted)
                notes.Add("(Median, Range and the Rayleigh test are computed " +
                          "on the unweighted azimuths.)");
            string nTxt = KishApplied
                ? EffectiveN.ToString("F1", ci) : Count.ToString("N0", ci);
            if (KishApplied)
                notes.Add("(The weights are not counts, so kappa and the " +
                          "confidence interval take n as the effective sample " +
                          "size of the weighted records, " + nTxt + " of " +
                          Count.ToString("N0", ci) + "; Kish 1992 eq. 4.3.)");
            if (KappaAdjusted)
                notes.Add("(Kappa adjusted for small sample size, n = " + nTxt +
                          "; Best and Fisher 1981, Fisher 1993 eq. 4.41.)");
            if (CiOutsideFisherRange)
                notes.Add("(Caution: with kappa = " +
                          VonMisesKappa.ToString("F3", ci) + " and n = " +
                          nTxt + ", the confidence interval " +
                          "is outside the range Fisher (1993, Table 4.3) " +
                          "recommends for this method; he advises a bootstrap " +
                          "interval instead.)");
            return notes;
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
            if (Weighted && !WeightsAreCounts)   // for counts, Count IS the total
                sb.AppendLine("Total weight = " + TotalWeight.ToString("N4", ci));
            string meanTxt = double.IsNaN(MeanDirectionDeg)
                ? "undefined (resultant length is zero)"
                : DirectionText(MeanDirectionDeg, ci);
            sb.AppendLine("Mean " + DirWord + " = " + meanTxt);
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
            if (Axial)
                sb.AppendLine("Angular Deviation of the Axes = " +
                              AxialAngularDeviationDeg.ToString("F5", ci) +
                              " Degrees   [C]");
            sb.AppendLine("-------------------------------------------------------");
            // Extended descriptors (computed on the unweighted azimuths).
            string medTxt = double.IsNaN(MedianDirectionDeg)
                ? "undefined" : DirectionText(MedianDirectionDeg, ci);
            sb.AppendLine("Median " + DirWord + " = " + medTxt + "   [A,B]");
            sb.AppendLine("Circular Range = " + CircularRangeDeg.ToString("F5", ci) +
                          " Degrees   [A,B]");
            sb.AppendLine("von Mises Concentration (kappa) = " +
                          VonMisesKappa.ToString("F5", ci) + "   [A,D]");
            sb.AppendLine("Mean " + DirWord + " 95% CI = " + MeanCiText(ci) + "   [A,D]");
            sb.AppendLine("Rayleigh Test of Uniformity: Z = " +
                          RayleighZ.ToString("F5", ci) + ", p = " +
                          RayleighP.ToString("F5", ci) + "   [A,E]");
            foreach (string note in Notes())
                sb.AppendLine("   " + note);
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
            if (KishApplied)
                sb.AppendLine("[F] Kish, L. 1992. Weighting for unequal Pi. Journal of " +
                              "Official Statistics 8:183-200.");
            sb.AppendLine("Circular statistics after Jenness Enterprises Polar Plots.");
            return sb.ToString();
        }
    }
}
