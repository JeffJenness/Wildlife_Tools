using System;
using System.Collections.Generic;
using System.Linq;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// The fuzzy-clustering numerics for the Land Facet tool, ported from the
    /// original Brost &amp; Beier R functions (LF_fxns.R: e1071::cmeans + eight
    /// cluster-validity indices) and validated against that R code to ~1e-15.
    /// Pure math (no ArcGIS dependencies) so it can be unit-tested in isolation.
    ///
    /// Conventions match the R source exactly: data are z-standardized per
    /// variable (sample SD, n-1); fuzzy c-means uses squared-Euclidean distance
    /// in standardized space with fuzziness psi; the confusion index is the ratio
    /// of the second-highest to the highest membership (Burrough et al. 1997).
    /// The Fukuyama-Sugeno, Average-Within-Cluster-Distance, Xie-Beni / Xie-Beni*,
    /// PBM, and Fuzzy-Silhouette indices use the fuzzy centroids; Davies-Bouldin
    /// and Calinski-Harabasz use the crisp cluster means (matching clusterSim).
    /// </summary>
    internal static class LandFacetEngine
    {
        // ---- basic vector helpers -------------------------------------------
        private static double Dist2(double[] a, double[] b)
        {
            double s = 0.0;
            for (int i = 0; i < a.Length; i++) { double d = a[i] - b[i]; s += d * d; }
            return s;
        }

        private static double Dist(double[] a, double[] b) => Math.Sqrt(Dist2(a, b));

        private static double[] ColumnMean(double[][] x)
        {
            int n = x.Length, d = x[0].Length;
            var m = new double[d];
            foreach (var row in x) for (int j = 0; j < d; j++) m[j] += row[j];
            for (int j = 0; j < d; j++) m[j] /= n;
            return m;
        }

        // ---- standardization -------------------------------------------------
        /// <summary>Z-standardize each column (sample SD, n-1), matching R's
        /// scale/sd. Returns the standardized copy; mean and sd are output.</summary>
        public static double[][] Standardize(double[][] x, out double[] mean, out double[] sd)
        {
            int n = x.Length, d = x[0].Length;
            mean = ColumnMean(x);
            sd = new double[d];
            for (int j = 0; j < d; j++)
            {
                double s = 0.0;
                for (int i = 0; i < n; i++) { double t = x[i][j] - mean[j]; s += t * t; }
                sd[j] = Math.Sqrt(s / (n - 1));
            }
            var z = new double[n][];
            for (int i = 0; i < n; i++)
            {
                z[i] = new double[d];
                for (int j = 0; j < d; j++)
                    z[i][j] = sd[j] > 0 ? (x[i][j] - mean[j]) / sd[j] : 0.0;
            }
            return z;
        }

        /// <summary>Standardize a single point with a stored mean/sd.</summary>
        public static double[] StandardizePoint(double[] x, double[] mean, double[] sd)
        {
            var z = new double[x.Length];
            for (int j = 0; j < x.Length; j++)
                z[j] = sd[j] > 0 ? (x[j] - mean[j]) / sd[j] : 0.0;
            return z;
        }

        // ---- fuzzy c-means membership ---------------------------------------
        /// <summary>Fuzzy membership of one standardized point to each centroid:
        /// u_j = d_j^(-2/(psi-1)) / sum_k d_k^(-2/(psi-1)). A point sitting on a
        /// centroid gets membership 1 there.</summary>
        public static double[] MembershipOf(double[] z, double[][] centers, double psi)
        {
            int c = centers.Length;
            var u = new double[c];
            double e = -1.0 / (psi - 1.0);
            int zeroAt = -1;
            var num = new double[c];
            double sum = 0.0;
            for (int j = 0; j < c; j++)
            {
                double d2 = Dist2(z, centers[j]);
                if (d2 <= 0.0) { zeroAt = j; break; }
                num[j] = Math.Pow(d2, e);
                sum += num[j];
            }
            if (zeroAt >= 0) { u[zeroAt] = 1.0; return u; }
            for (int j = 0; j < c; j++) u[j] = num[j] / sum;
            return u;
        }

        /// <summary>Membership matrix (n x c) for all points.</summary>
        public static double[][] Memberships(double[][] z, double[][] centers, double psi)
        {
            var u = new double[z.Length][];
            for (int i = 0; i < z.Length; i++) u[i] = MembershipOf(z[i], centers, psi);
            return u;
        }

        // ---- fuzzy c-means ---------------------------------------------------
        public sealed class FcmResult
        {
            public double[][] Centers;      // c x d
            public double[][] Membership;   // n x c
            public int[] Cluster;           // n, 0-based argmax membership
            public double Objective;        // sum_x sum_i u^m ||x-c_i||^2
        }

        /// <summary>One fuzzy c-means run: alternate centroid and membership
        /// updates from a random membership initialization until the membership
        /// change drops below tol or maxIter is reached.</summary>
        public static FcmResult Fcm(double[][] z, int c, double psi,
                                    int maxIter, double tol, Random rng)
        {
            int n = z.Length, d = z[0].Length;
            // Random initial membership (rows sum to 1).
            var u = new double[n][];
            for (int i = 0; i < n; i++)
            {
                u[i] = new double[c];
                double s = 0.0;
                for (int j = 0; j < c; j++) { u[i][j] = rng.NextDouble() + 1e-9; s += u[i][j]; }
                for (int j = 0; j < c; j++) u[i][j] /= s;
            }
            var centers = new double[c][];
            for (int j = 0; j < c; j++) centers[j] = new double[d];

            for (int iter = 0; iter < maxIter; iter++)
            {
                // Centroids: c_i = sum_x u_xi^m x / sum_x u_xi^m.
                for (int j = 0; j < c; j++)
                {
                    var acc = new double[d];
                    double wsum = 0.0;
                    for (int i = 0; i < n; i++)
                    {
                        double w = Math.Pow(u[i][j], psi);
                        wsum += w;
                        for (int k = 0; k < d; k++) acc[k] += w * z[i][k];
                    }
                    for (int k = 0; k < d; k++) centers[j][k] = wsum > 0 ? acc[k] / wsum : 0.0;
                }
                // Memberships from the new centroids.
                double delta = 0.0;
                for (int i = 0; i < n; i++)
                {
                    var ui = MembershipOf(z[i], centers, psi);
                    for (int j = 0; j < c; j++)
                        delta = Math.Max(delta, Math.Abs(ui[j] - u[i][j]));
                    u[i] = ui;
                }
                if (delta < tol) break;
            }

            var cluster = new int[n];
            double obj = 0.0;
            for (int i = 0; i < n; i++)
            {
                int best = 0;
                for (int j = 1; j < c; j++) if (u[i][j] > u[i][best]) best = j;
                cluster[i] = best;
                for (int j = 0; j < c; j++)
                    obj += Math.Pow(u[i][j], psi) * Dist2(z[i], centers[j]);
            }
            return new FcmResult { Centers = centers, Membership = u, Cluster = cluster, Objective = obj };
        }

        /// <summary>Run FCM <paramref name="restarts"/> times and keep the run with
        /// the lowest objective (the standard multi-start strategy).</summary>
        public static FcmResult FcmBest(double[][] z, int c, double psi,
                                        int restarts, int maxIter, double tol, Random rng)
        {
            FcmResult best = null;
            for (int r = 0; r < restarts; r++)
            {
                var res = Fcm(z, c, psi, maxIter, tol, rng);
                if (best == null || res.Objective < best.Objective) best = res;
            }
            return best;
        }

        // ---- cluster validity indices ---------------------------------------
        public sealed class ValidityIndices
        {
            public double FS, AWCD, XB, XBstar, DB, PBM, CH, FSil;
        }

        /// <summary>All eight validity indices for one partition, matching
        /// LF_fxns.R. <paramref name="cluster"/> is 0-based.</summary>
        public static ValidityIndices Validity(double[][] z, double[][] centers,
                                               double[][] memb, int[] cluster, double psi)
        {
            int n = z.Length, c = centers.Length;
            var xmean = ColumnMean(z);

            // Per-cluster fuzzy-weighted squared-distance sum, and weight sum.
            var wSqSum = new double[c];   // sum_x u^psi ||x-c_i||^2
            var wSum = new double[c];     // sum_x u^psi
            var wDistSum = new double[c]; // sum_x u^psi ||x-c_i||  (PBM, unsquared)
            double fs = 0.0;
            for (int j = 0; j < c; j++)
            {
                double mdist = Dist2(xmean, centers[j]);   // ||mean - c_j||^2
                for (int i = 0; i < n; i++)
                {
                    double w = Math.Pow(memb[i][j], psi);
                    double d2 = Dist2(z[i], centers[j]);
                    wSqSum[j] += w * d2;
                    wSum[j] += w;
                    wDistSum[j] += w * Math.Sqrt(d2);
                    fs += w * (d2 - mdist);
                }
            }

            double awcd = 0.0;
            for (int j = 0; j < c; j++) awcd += wSqSum[j] / wSum[j];
            awcd /= c;

            // Min / max pairwise centroid distances.
            double cmin = double.MaxValue, cmax = 0.0;
            for (int i = 0; i < c; i++)
                for (int j = i + 1; j < c; j++)
                {
                    double dd = Dist(centers[i], centers[j]);
                    if (dd < cmin) cmin = dd;
                    if (dd > cmax) cmax = dd;
                }
            double cmin2 = cmin * cmin;
            double totWSq = wSqSum.Sum();
            double xb = totWSq / (n * cmin2);
            double xbStar = wSqSum.Max() / (n * cmin2);

            // PBM.
            double e1 = 0.0;
            for (int i = 0; i < n; i++) e1 += Dist(z[i], xmean);
            double jm = wDistSum.Sum();
            double pbm = Math.Pow((1.0 / c) * (e1 / jm) * cmax, 2.0);

            // Crisp cluster means (for Davies-Bouldin and Calinski-Harabasz).
            var cmeans = new double[c][];
            var counts = new int[c];
            for (int j = 0; j < c; j++) cmeans[j] = new double[z[0].Length];
            for (int i = 0; i < n; i++)
            {
                int g = cluster[i]; counts[g]++;
                for (int k = 0; k < z[0].Length; k++) cmeans[g][k] += z[i][k];
            }
            for (int j = 0; j < c; j++)
                for (int k = 0; k < z[0].Length; k++)
                    cmeans[j][k] = counts[j] > 0 ? cmeans[j][k] / counts[j] : 0.0;

            var scatter = new double[c];  // mean Euclidean distance to crisp mean (q=1)
            var wgssPart = new double[c];
            for (int i = 0; i < n; i++)
            {
                int g = cluster[i];
                double dcen = Dist(z[i], cmeans[g]);
                scatter[g] += dcen;
                wgssPart[g] += dcen * dcen;
            }
            for (int j = 0; j < c; j++) scatter[j] = counts[j] > 0 ? scatter[j] / counts[j] : 0.0;

            double db = 0.0;
            for (int i = 0; i < c; i++)
            {
                double worst = 0.0;
                for (int j = 0; j < c; j++)
                {
                    if (j == i) continue;
                    double r = (scatter[i] + scatter[j]) / Dist(cmeans[i], cmeans[j]);
                    if (r > worst) worst = r;
                }
                db += worst;
            }
            db /= c;

            double bgss = 0.0, wgss = wgssPart.Sum();
            for (int j = 0; j < c; j++) bgss += counts[j] * Dist2(cmeans[j], xmean);
            double ch = (bgss / (c - 1)) / (wgss / (n - c));

            // Fuzzy silhouette (fuzzy centroids for distance, crisp grouping).
            var dToCenter = new double[n][];
            for (int i = 0; i < n; i++)
            {
                dToCenter[i] = new double[c];
                for (int j = 0; j < c; j++) dToCenter[i][j] = Dist(z[i], centers[j]);
            }
            double sNum = 0.0, sDen = 0.0;
            for (int i = 0; i < n; i++)
            {
                int g = cluster[i];
                double a = dToCenter[i][g];
                double b = double.MaxValue;
                for (int j = 0; j < c; j++) if (j != g && dToCenter[i][j] < b) b = dToCenter[i][j];
                double sil = (b - a) / Math.Max(a, b);
                // weight = (top membership - second membership)^2
                double u1 = 0.0, u2 = 0.0;
                for (int j = 0; j < c; j++)
                {
                    double v = memb[i][j];
                    if (v > u1) { u2 = u1; u1 = v; }
                    else if (v > u2) u2 = v;
                }
                double w = (u1 - u2) * (u1 - u2);
                sNum += w * sil;
                sDen += w;
            }
            double fsil = sNum / sDen;

            return new ValidityIndices
            {
                FS = fs, AWCD = awcd, XB = xb, XBstar = xbStar,
                DB = db, PBM = pbm, CH = ch, FSil = fsil
            };
        }

        // ---- classification (confusion index) -------------------------------
        public struct Classification
        {
            public int TopCluster;      // 0-based, highest membership
            public int SecondCluster;   // 0-based, second highest
            public double ConfusionIndex; // u_second / u_top  (Burrough et al. 1997)
        }

        /// <summary>Top/second cluster and the confusion index for a membership
        /// vector.</summary>
        public static Classification Classify(double[] membership)
        {
            int c = membership.Length;
            int top = 0;
            for (int j = 1; j < c; j++) if (membership[j] > membership[top]) top = j;
            int second = -1;
            for (int j = 0; j < c; j++)
            {
                if (j == top) continue;
                if (second < 0 || membership[j] > membership[second]) second = j;
            }
            double ci = membership[top] > 0 ? membership[second] / membership[top] : 1.0;
            return new Classification { TopCluster = top, SecondCluster = second, ConfusionIndex = ci };
        }

        // ---- outliers --------------------------------------------------------
        /// <summary>Flag the lowest-density fraction of points as outliers using a
        /// Gaussian kernel density estimate with Scott's-rule bandwidth (full data
        /// covariance scaled by n^(-1/(d+4))^2). A portable stand-in for the
        /// original ks::kde that reproduces its outlier flags to ~99%. Direct
        /// O(n^2) evaluation -- callers subsample very large data.</summary>
        public static bool[] KdeOutliers(double[][] x, double fraction)
        {
            int n = x.Length, d = x[0].Length;
            var mean = ColumnMean(x);
            var cov = Covariance(x, mean);
            double factor2 = Math.Pow(n, -2.0 / (d + 4));   // Scott factor squared
            var bw = new double[d][];
            for (int i = 0; i < d; i++)
            {
                bw[i] = new double[d];
                for (int j = 0; j < d; j++) bw[i][j] = cov[i][j] * factor2;
            }
            var inv = Invert(bw, out double det);
            var density = new double[n];
            for (int i = 0; i < n; i++)
            {
                double s = 0.0;
                for (int j = 0; j < n; j++)
                    s += Math.Exp(-0.5 * QuadForm(x[i], x[j], inv));
                density[i] = s;   // constant factors are monotonic; omit for ranking
            }
            return FlagLowest(density, fraction);
        }

        /// <summary>Flag the fraction of points with the largest Mahalanobis
        /// distance from the mean (the sparse tails) as outliers.</summary>
        public static bool[] MahalanobisOutliers(double[][] x, double fraction)
        {
            int n = x.Length;
            var mean = ColumnMean(x);
            var cov = Covariance(x, mean);
            var inv = Invert(cov, out _);
            var negD2 = new double[n];
            for (int i = 0; i < n; i++) negD2[i] = -QuadForm(x[i], mean, inv);
            return FlagLowest(negD2, fraction);   // lowest -D2 == largest D2
        }

        /// <summary>A fitted Gaussian KDE: the kernel-center points and the inverse
        /// bandwidth matrix. Fit once on the analysis sample, then evaluate density
        /// at ANY points (the sample, or every cell of the class) on the same
        /// scale.</summary>
        public sealed class KdeModel
        {
            public double[][] Kernels;   // subsampled kernel-center points
            public double[][] BwInv;     // inverse of the Scott-rule bandwidth
        }

        /// <summary>Fit a Gaussian KDE (Scott's-rule bandwidth from
        /// <paramref name="refData"/>) using at most <paramref name="maxKernel"/>
        /// subsampled kernel centers, so evaluation is O(n*k) rather than
        /// O(n^2).</summary>
        public static KdeModel KdePrepare(double[][] refData, int maxKernel, Random rng)
        {
            int n = refData.Length, d = refData[0].Length;
            var mean = ColumnMean(refData);
            var cov = Covariance(refData, mean);
            double factor2 = Math.Pow(n, -2.0 / (d + 4));   // Scott factor squared
            var bw = new double[d][];
            for (int i = 0; i < d; i++)
            {
                bw[i] = new double[d];
                for (int j = 0; j < d; j++) bw[i][j] = cov[i][j] * factor2;
            }
            var inv = Invert(bw, out _);

            double[][] kernels;
            if (n <= maxKernel) kernels = refData;
            else
            {
                var idx = Enumerable.Range(0, n).ToArray();
                for (int i = 0; i < maxKernel; i++)   // Fisher-Yates partial shuffle
                {
                    int j = i + rng.Next(n - i);
                    (idx[i], idx[j]) = (idx[j], idx[i]);
                }
                kernels = idx.Take(maxKernel).Select(i => refData[i]).ToArray();
            }
            return new KdeModel { Kernels = kernels, BwInv = inv };
        }

        /// <summary>Kernel density at each query point under a fitted
        /// <see cref="KdeModel"/> (unnormalized, ranking-only).</summary>
        public static double[] KdeEval(double[][] query, KdeModel model)
        {
            var kernels = model.Kernels;
            var inv = model.BwInv;
            var density = new double[query.Length];
            // Each query point is independent and its kernel sum keeps the same
            // order, so the result is identical to the serial loop -- only spread
            // across cores. This is the dominant cost when classifying every cell.
            System.Threading.Tasks.Parallel.For(0, query.Length, i =>
            {
                var q = query[i];
                double s = 0.0;
                foreach (var k in kernels)
                    s += Math.Exp(-0.5 * QuadForm(q, k, inv));
                density[i] = s;
            });
            return density;
        }

        /// <summary>Convenience: fit and evaluate on the same data.</summary>
        public static double[] KdeDensity(double[][] x, int maxKernel, Random rng) =>
            KdeEval(x, KdePrepare(x, maxKernel, rng));

        /// <summary>Flag the lowest-density <paramref name="fraction"/> of points as
        /// outliers, given precomputed density scores.</summary>
        public static bool[] Outliers(double[] density, double fraction) =>
            FlagLowest(density, fraction);

        private static bool[] FlagLowest(double[] score, double fraction)
        {
            int n = score.Length;
            int k = (int)Math.Round(fraction * n);
            var order = Enumerable.Range(0, n).OrderBy(i => score[i]).ToArray();
            var outlier = new bool[n];
            for (int i = 0; i < k; i++) outlier[order[i]] = true;
            return outlier;
        }

        // ---- small linear algebra (d is 1..6) -------------------------------
        private static double[][] Covariance(double[][] x, double[] mean)
        {
            int n = x.Length, d = x[0].Length;
            var cov = new double[d][];
            for (int i = 0; i < d; i++) cov[i] = new double[d];
            foreach (var row in x)
                for (int i = 0; i < d; i++)
                    for (int j = 0; j < d; j++)
                        cov[i][j] += (row[i] - mean[i]) * (row[j] - mean[j]);
            for (int i = 0; i < d; i++)
                for (int j = 0; j < d; j++)
                    cov[i][j] /= (n - 1);
            return cov;
        }

        private static double QuadForm(double[] a, double[] b, double[][] inv)
        {
            // (a-b)^T inv (a-b) computed without allocating a difference vector --
            // this is called cells x kernels times in KdeEval, so the per-call
            // allocation dominated GC on large rasters.
            int d = a.Length;
            double s = 0.0;
            for (int i = 0; i < d; i++)
            {
                double[] invi = inv[i];
                double row = 0.0;
                for (int j = 0; j < d; j++) row += invi[j] * (a[j] - b[j]);
                s += (a[i] - b[i]) * row;
            }
            return s;
        }

        /// <summary>Invert a small square matrix by Gauss-Jordan; also returns the
        /// determinant.</summary>
        private static double[][] Invert(double[][] m, out double det)
        {
            int d = m.Length;
            var a = new double[d][];
            var inv = new double[d][];
            for (int i = 0; i < d; i++)
            {
                a[i] = (double[])m[i].Clone();
                inv[i] = new double[d];
                inv[i][i] = 1.0;
            }
            det = 1.0;
            for (int col = 0; col < d; col++)
            {
                int piv = col;
                for (int r = col + 1; r < d; r++)
                    if (Math.Abs(a[r][col]) > Math.Abs(a[piv][col])) piv = r;
                if (Math.Abs(a[piv][col]) < 1e-300)
                    throw new InvalidOperationException("Singular matrix (a variable may be constant).");
                if (piv != col)
                {
                    (a[piv], a[col]) = (a[col], a[piv]);
                    (inv[piv], inv[col]) = (inv[col], inv[piv]);
                    det = -det;
                }
                double p = a[col][col];
                det *= p;
                for (int j = 0; j < d; j++) { a[col][j] /= p; inv[col][j] /= p; }
                for (int r = 0; r < d; r++)
                {
                    if (r == col) continue;
                    double f = a[r][col];
                    if (f == 0.0) continue;
                    for (int j = 0; j < d; j++)
                    {
                        a[r][j] -= f * a[col][j];
                        inv[r][j] -= f * inv[col][j];
                    }
                }
            }
            return inv;
        }
    }
}
