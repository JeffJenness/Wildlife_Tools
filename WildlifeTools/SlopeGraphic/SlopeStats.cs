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

namespace WildlifeTools.SlopeGraphic
{
    /// <summary>How the source slope values are expressed.</summary>
    internal enum SlopeUnit
    {
        Degrees = 0,    // 0-90
        Percent = 1,    // 100 * rise/run; 45 degrees = 100%
        Ratio = 2,      // rise/run; 45 degrees = 1.0
        Gradians = 3,   // gons; 100 gon = 90 degrees
    }

    /// <summary>
    /// Descriptive statistics for a weighted set of SLOPE values. The mean,
    /// SD, and confidence interval are ALWAYS computed on the slope ANGLES
    /// (degrees): terrain inclination is an angle, and percent slope is its
    /// tangent -- a convex transform, so averaging raw percent values biases
    /// steepness statistics high (a single near-vertical cell at 5,000%+ can
    /// dominate the mean). Values in percent/ratio/gon units pass through the
    /// arctangent BEFORE the moment sums, and the results convert back to the
    /// source unit only for display (tan of the mean angle, not the mean of
    /// the tangents). The median, percentiles, min and max are order
    /// statistics -- invariant under the monotone conversion -- and stay in
    /// source units throughout. Pure C#, no ArcGIS dependency. The 95% CI of
    /// the mean uses the t distribution (Zar 1999 ch. 7).
    /// </summary>
    internal sealed class SlopeStats
    {
        public SlopeUnit Unit { get; }
        public bool WeightsAreCounts { get; }
        public long Count { get; private set; }      // cells for binned data
        public double TotalWeight { get; private set; }
        public double MeanDeg { get; private set; }  // degree-space mean
        public double SdDeg { get; private set; }    // degree-space sample SD
        public double CiHalfDeg95 { get; private set; }  // NaN if undefined
        public double Min { get; private set; }      // source units (exact)
        public double Max { get; private set; }
        public double Median { get; private set; }   // source units

        /// <summary>The mean ANGLE re-expressed in the source unit
        /// (tan of the mean angle -- NOT the mean of the tangents).</summary>
        public double Mean => double.IsNaN(MeanDeg)
            ? double.NaN : FromDegrees(MeanDeg, Unit);

        // Sorted (value, weight) pairs for quantiles; source units.
        private readonly List<double> _v = new List<double>();
        private readonly List<double> _w = new List<double>();

        private const double D2R = Math.PI / 180.0;
        private const double R2D = 180.0 / Math.PI;
        private static readonly CultureInfo CI = CultureInfo.InvariantCulture;

        // ---- unit conversions ----------------------------------------------
        public static double ToDegrees(double v, SlopeUnit u)
        {
            switch (u)
            {
                case SlopeUnit.Percent: return Math.Atan(v / 100.0) * R2D;
                case SlopeUnit.Ratio: return Math.Atan(v) * R2D;
                case SlopeUnit.Gradians: return v * 0.9;
                default: return v;
            }
        }

        public static double FromDegrees(double deg, SlopeUnit u)
        {
            switch (u)
            {
                case SlopeUnit.Percent: return Math.Tan(Math.Min(deg, 89.999) * D2R) * 100.0;
                case SlopeUnit.Ratio: return Math.Tan(Math.Min(deg, 89.999) * D2R);
                case SlopeUnit.Gradians: return deg / 0.9;
                default: return deg;
            }
        }

        /// <summary>Highest representable value: vertical.</summary>
        public static double MaxValid(SlopeUnit u) =>
            u == SlopeUnit.Degrees ? 90.0
            : u == SlopeUnit.Gradians ? 100.0
            : double.PositiveInfinity;

        public static string UnitLabel(SlopeUnit u) =>
            u == SlopeUnit.Percent ? "percent"
            : u == SlopeUnit.Ratio ? "rise:run"
            : u == SlopeUnit.Gradians ? "gons" : "degrees";

        public static string UnitSuffix(SlopeUnit u) =>
            u == SlopeUnit.Percent ? "%"
            : u == SlopeUnit.Ratio ? ""
            : u == SlopeUnit.Gradians ? " gon" : "°";

        // ---- construction ---------------------------------------------------
        /// <summary>Exact per-record values (vector/table sources). The moment
        /// sums accumulate the values' ANGLES; the pair list keeps the source
        /// values for the (transform-invariant) quantiles.</summary>
        public SlopeStats(IReadOnlyList<double> values, IReadOnlyList<double> weights,
                          bool weightsAreCounts, SlopeUnit unit)
        {
            Unit = unit;
            WeightsAreCounts = weightsAreCounts;
            int n = values?.Count ?? 0;
            double sw = 0, swx = 0, swx2 = 0;
            Min = double.PositiveInfinity; Max = double.NegativeInfinity;
            for (int i = 0; i < n; i++)
            {
                double x = values[i];
                double w = (weights != null && i < weights.Count) ? weights[i] : 1.0;
                if (w <= 0) continue;
                _v.Add(x); _w.Add(w);
                double deg = ToDegrees(x, unit);
                sw += w; swx += w * deg; swx2 += w * deg * deg;
                if (x < Min) Min = x;
                if (x > Max) Max = x;
            }
            Finish(sw, swx, swx2, weightsAreCounts ? (long)Math.Round(sw) : _v.Count);
        }

        /// <summary>Binned raster data: exact streamed sums (of the ANGLES, in
        /// degrees) plus the angle-space histogram for the quantiles.</summary>
        public static SlopeStats FromBinned(double[] angleHist, double binDeg,
            double sumW, double sumWX, double sumWX2, double min, double max,
            SlopeUnit unit)
        {
            var s = new SlopeStats(unit);
            for (int b = 0; b < angleHist.Length; b++)
            {
                if (angleHist[b] <= 0) continue;
                // Bin center angle -> source-unit value; monotone, so
                // quantiles computed on these are exact to the bin width.
                s._v.Add(FromDegrees((b + 0.5) * binDeg, unit));
                s._w.Add(angleHist[b]);
            }
            s.Min = min; s.Max = max;
            if (s._v.Count == 0) { s.Min = double.PositiveInfinity; s.Max = double.NegativeInfinity; }
            s.Finish(sumW, sumWX, sumWX2, (long)Math.Round(sumW));
            return s;
        }

        private SlopeStats(SlopeUnit unit) { Unit = unit; WeightsAreCounts = true; }

        private void Finish(double sw, double swx, double swx2, long count)
        {
            Count = count;
            TotalWeight = sw;
            if (sw <= 0 || _v.Count == 0)
            {
                MeanDeg = SdDeg = Median = CiHalfDeg95 = double.NaN;
                Min = Max = double.NaN;
                return;
            }
            MeanDeg = swx / sw;                    // degree space throughout
            // Weighted SAMPLE variance with counts-style weights (n-1 form).
            double n = WeightsAreCounts ? Math.Max(sw, 1.0) : _v.Count;
            double ss = Math.Max(swx2 - sw * MeanDeg * MeanDeg, 0.0);
            SdDeg = (n > 1.5 || (!WeightsAreCounts && _v.Count > 1))
                ? Math.Sqrt(ss / (WeightsAreCounts ? Math.Max(sw - 1.0, 1.0)
                                                   : sw * (_v.Count - 1.0) / _v.Count))
                : 0.0;
            SortPairs();
            Median = Quantile(0.5);
            long nEff = WeightsAreCounts ? Count : _v.Count;
            CiHalfDeg95 = (nEff > 1 && SdDeg > 0)
                ? T95(nEff - 1) * SdDeg / Math.Sqrt(nEff)
                : double.NaN;
        }

        private void SortPairs()
        {
            var idx = new int[_v.Count];
            for (int i = 0; i < idx.Length; i++) idx[i] = i;
            var vArr = _v.ToArray(); var wArr = _w.ToArray();
            Array.Sort(idx, (a, b) => vArr[a].CompareTo(vArr[b]));
            for (int i = 0; i < idx.Length; i++)
            { _v[i] = vArr[idx[i]]; _w[i] = wArr[idx[i]]; }
        }

        /// <summary>Weighted quantile (0..1) over the sorted pairs.</summary>
        public double Quantile(double p)
        {
            if (_v.Count == 0) return double.NaN;
            if (p <= 0) return _v[0];
            if (p >= 1) return _v[_v.Count - 1];
            double target = p * TotalWeight, cum = 0;
            for (int i = 0; i < _v.Count; i++)
            {
                cum += _w[i];
                if (cum >= target) return _v[i];
            }
            return _v[_v.Count - 1];
        }

        /// <summary>Two-sided 95% t quantile for the given df (Zar 1999,
        /// table B.3); exact table through 30, then coarse steps to z.</summary>
        /// <summary>Two-sided 95% t value for a confidence interval of a mean
        /// (Zar 1999 ch. 7). Zar's table through 30 df (three decimals); above
        /// that the Cornish-Fisher expansion of t in powers of 1/df, which is
        /// within 3e-8 of the exact value for every df from 31 to 100,000
        /// (checked against scipy 2026-09-24). Replaces coarse steps (2.021,
        /// 2.000, 1.980, 1.960) that made the interval up to 1% too narrow.</summary>
        public static double T95(long df)
        {
            double[] t = { 12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365,
                2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120,
                2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060,
                2.056, 2.052, 2.048, 2.045, 2.042 };
            if (df < 1) return double.NaN;
            if (df <= 30) return t[df - 1];
            const double z = 1.959963984540054;       // normal 97.5% point
            double z2 = z * z;
            double g1 = (z2 * z + z) / 4.0;
            double g2 = (5 * z2 * z2 * z + 16 * z2 * z + 3 * z) / 96.0;
            double g3 = (3 * Math.Pow(z, 7) + 19 * Math.Pow(z, 5) + 17 * z2 * z - 15 * z) / 384.0;
            double g4 = (79 * Math.Pow(z, 9) + 776 * Math.Pow(z, 7) + 1482 * Math.Pow(z, 5)
                         - 1920 * z2 * z - 945 * z) / 92160.0;
            double v = df;
            return z + g1 / v + g2 / (v * v) + g3 / (v * v * v) + g4 / (v * v * v * v);
        }

        // ---- formatted values ----------------------------------------------
        public string Fmt(double v)
        {
            if (double.IsNaN(v)) return "undefined";
            string s = Unit == SlopeUnit.Ratio ? v.ToString("F4", CI) : v.ToString("F3", CI);
            return s + UnitSuffix(Unit);
        }

        /// <summary>"34.567% (19.06°)": value in source units with the degree
        /// equivalent in parentheses (omitted when the source IS degrees).</summary>
        public string FmtBoth(double v)
        {
            if (double.IsNaN(v)) return "undefined";
            if (Unit == SlopeUnit.Degrees) return Fmt(v);
            return Fmt(v) + " (" + ToDegrees(v, Unit).ToString("F3", CI) + "°)";
        }

        /// <summary>Degree-space CI, bounds re-expressed in the source unit:
        /// "24.481° ± 0.532°  (23.949° to 25.013°)" -- for non-degree units
        /// the bounds carry both forms via FmtBoth.</summary>
        public string CiText()
        {
            if (double.IsNaN(CiHalfDeg95))
                return "undefined (needs n > 1 and nonzero variation)";
            double loDeg = Math.Max(MeanDeg - CiHalfDeg95, 0.0);
            double hiDeg = Math.Min(MeanDeg + CiHalfDeg95, 90.0);
            return MeanDeg.ToString("F3", CI) + "° ± " +
                   CiHalfDeg95.ToString("F3", CI) + "°  (" +
                   FmtBoth(FromDegrees(loDeg, Unit)) + " to " +
                   FmtBoth(FromDegrees(hiDeg, Unit)) + ")";
        }
    }
}
