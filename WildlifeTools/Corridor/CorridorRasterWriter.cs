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
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Writes an in-memory classified cluster-ID grid to a georeferenced GeoTIFF
    /// by handing it to the bundled <c>corridor_write_raster.py</c> helper, which
    /// runs as a one-shot subprocess under ArcGIS Pro's own Python (GDAL only, no
    /// arcpy, no license). This keeps the Land Facet tool fully self-contained --
    /// no visible geoprocessing tool -- while reusing the proven GDAL write path.
    /// The array is handed over as raw binary (byte-exact, no formatting risk) plus
    /// a small JSON job describing the grid; the helper prints PROGRESS lines this
    /// class forwards to an <see cref="IProgress{T}"/> so the dialog can show a
    /// determinate meter.
    /// </summary>
    internal static class CorridorRasterWriter
    {
        /// <summary>
        /// Write <paramref name="clusterIds"/> (row-major, length width*height,
        /// with <paramref name="noData"/> where unclassified) as a GeoTIFF at
        /// <paramref name="outPath"/>. Returns the written path. Runs off the UI
        /// thread; reports progress 0..1.
        /// </summary>
        public static Task<string> WriteAsync(
            int[] clusterIds, int width, int height,
            double[] geotransform, string wkt, int noData, string outPath,
            IProgress<double> progress,
            IReadOnlyList<(int value, long count, string category, int cluster)> rat = null,
            CancellationToken ct = default)
        {
            return Task.Run(() => Write(clusterIds, width, height, geotransform,
                                        wkt, noData, outPath, progress, rat, ct), ct);
        }

        private static string Write(int[] clusterIds, int width, int height,
            double[] geotransform, string wkt, int noData, string outPath,
            IProgress<double> progress,
            IReadOnlyList<(int value, long count, string category, int cluster)> rat,
            CancellationToken ct)
        {
            if (clusterIds.Length != (long)width * height)
                throw new ArgumentException("clusterIds length must equal width*height.");

            string scratch = Path.Combine(Path.GetTempPath(), "WildlifeTools_LandFacet");
            Directory.CreateDirectory(scratch);
            string tag = Guid.NewGuid().ToString("N");
            string binPath = Path.Combine(scratch, tag + ".bin");
            string jobPath = Path.Combine(scratch, tag + ".json");

            try
            {
                // Raw binary array (int32, row-major) -- byte-exact handoff,
                // written in strips. A single byte[] of Length*4 overflowed
                // int arithmetic above ~537M cells (negative array size)
                // while the dialog's guard admits arrays up to int.MaxValue
                // cells -- and it doubled peak memory by holding a second
                // full-raster copy.
                using (var fs = new FileStream(binPath, FileMode.Create,
                                               FileAccess.Write, FileShare.None,
                                               1 << 20))
                {
                    const int chunkInts = 1 << 22;   // 16 MB per write
                    for (int off = 0; off < clusterIds.Length; off += chunkInts)
                    {
                        ct.ThrowIfCancellationRequested();
                        int n = Math.Min(chunkInts, clusterIds.Length - off);
                        fs.Write(System.Runtime.InteropServices.MemoryMarshal
                            .AsBytes(clusterIds.AsSpan(off, n)));
                    }
                }

                var job = new
                {
                    data = binPath,
                    @out = outPath,
                    width,
                    height,
                    dtype = "int32",
                    nodata = noData,
                    geotransform,
                    wkt = wkt ?? string.Empty,
                    // Optional raster attribute table: [value, count, category, cluster] rows.
                    rat = rat?.Select(r => new object[]
                        { r.value, r.count, r.category ?? string.Empty, r.cluster }).ToList(),
                };
                File.WriteAllText(jobPath, JsonSerializer.Serialize(job));

                string python = FindProPython();
                string helper = Path.Combine(
                    Path.GetDirectoryName(typeof(CorridorRasterWriter).Assembly.Location) ?? ".",
                    "Corridor", "corridor_write_raster.py");
                if (!File.Exists(helper))
                    throw new FileNotFoundException(
                        "The raster-write helper was not found next to the add-in: " + helper);

                var psi = new ProcessStartInfo(python)
                {
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                };
                psi.ArgumentList.Add(helper);
                psi.ArgumentList.Add(jobPath);

                using var p = new Process { StartInfo = psi };
                var err = new StringBuilder();
                string okPath = null;
                p.OutputDataReceived += (_, e) =>
                {
                    if (e.Data == null) return;
                    if (e.Data.StartsWith("PROGRESS", StringComparison.Ordinal))
                    {
                        if (double.TryParse(e.Data.Substring(8).Trim(), NumberStyles.Float,
                                            CultureInfo.InvariantCulture, out double f))
                            progress?.Report(f);
                    }
                    else if (e.Data.StartsWith("OK ", StringComparison.Ordinal))
                    {
                        okPath = e.Data.Substring(3).Trim();
                    }
                };
                p.ErrorDataReceived += (_, e) => { if (e.Data != null) err.AppendLine(e.Data); };

                p.Start();
                p.BeginOutputReadLine();
                p.BeginErrorReadLine();
                while (!p.WaitForExit(200))
                {
                    if (ct.IsCancellationRequested)
                    {
                        try { p.Kill(entireProcessTree: true); } catch { /* best effort */ }
                        throw new OperationCanceledException(ct);
                    }
                }
                p.WaitForExit();   // let the async output handlers drain

                if (p.ExitCode != 0 || okPath == null)
                    throw new InvalidOperationException(
                        "Writing the land-facet raster failed"
                        + (err.Length > 0 ? ":" + Environment.NewLine + err : " (exit " + p.ExitCode + ")."));
                return okPath;
            }
            finally
            {
                try { File.Delete(binPath); } catch { /* scratch */ }
                try { File.Delete(jobPath); } catch { /* scratch */ }
            }
        }

        /// <summary>Locate ArcGIS Pro's bundled python.exe.</summary>
        private static string FindProPython()
        {
            try
            {
                string bin = Path.GetDirectoryName(
                    Process.GetCurrentProcess().MainModule?.FileName);   // ...\Pro\bin
                if (!string.IsNullOrEmpty(bin))
                {
                    string py = Path.Combine(bin, "Python", "envs", "arcgispro-py3", "python.exe");
                    if (File.Exists(py)) return py;
                }
            }
            catch { /* fall through to well-known locations */ }

            foreach (var root in new[]
                     {
                         Environment.GetEnvironmentVariable("ProgramFiles"),
                         Environment.GetEnvironmentVariable("ProgramW6432"),
                         @"C:\Program Files",
                     })
            {
                if (string.IsNullOrEmpty(root)) continue;
                string py = Path.Combine(root, "ArcGIS", "Pro", "bin", "Python",
                                         "envs", "arcgispro-py3", "python.exe");
                if (File.Exists(py)) return py;
            }
            throw new FileNotFoundException("Could not locate ArcGIS Pro's Python (python.exe).");
        }
    }
}
