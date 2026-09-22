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
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Stamps the land facet LINKAGE RECORD onto a finished facet raster, by
    /// handing it to the bundled <c>corridor_write_metadata.py</c> helper running
    /// as a one-shot subprocess under ArcGIS Pro's own Python.
    ///
    /// The record is what lets the rest of the chain -- Land Facet Density, then
    /// the Mahalanobis cost surface -- work without re-asking the user what the
    /// clustering run did: the analysis identifier, the ORDERED variable list
    /// (mean vector and covariance matrix must be built in that same order, or
    /// the distances are silently wrong), and which cell value is which
    /// category and cluster.
    ///
    /// Two decisions worth not re-litigating. It goes through Python because the
    /// record format has exactly one implementation --
    /// <c>Toolboxes/land_facet_linkage.py</c>, the same code the density tool
    /// reads with -- and a second C# implementation of the same format could
    /// drift from it silently. And it must run AFTER the raster reaches its
    /// final location: the record travels in the Esri metadata, which is the
    /// only carrier that survives Copy Raster into a file geodatabase (tested
    /// 2026-08-07: GDAL dataset metadata, band metadata and band descriptions
    /// are all stripped by Copy Raster, and a geodatabase raster cannot be
    /// re-stamped through GDAL afterwards).
    /// </summary>
    internal static class CorridorMetadataWriter
    {
        /// <summary>One facet as written to the raster.</summary>
        internal readonly record struct FacetEntry(
            int FacetId, double CategoryValue, string CategoryLabel, int Cluster,
            long Count);

        /// <summary>
        /// Write the facet linkage record onto <paramref name="target"/>.
        /// Returns the analysis id on success, or null if stamping failed --
        /// failure is NOT fatal, since an unstamped raster still works and the
        /// downstream tools simply fall back to their generic labelling.
        /// </summary>
        public static Task<string> StampFacetsAsync(
            string target, string analysisId,
            IReadOnlyList<string> orderedVariables,
            IReadOnlyList<string> orderedVariablePaths,
            IReadOnlyList<FacetEntry> facets,
            int width, int height,
            IReadOnlyDictionary<string, object> parameters,
            string sourceCategoricalRaster,
            CancellationToken ct = default)
        {
            return Task.Run(() => Stamp(target, analysisId, orderedVariables,
                                        orderedVariablePaths, facets, width, height,
                                        parameters, sourceCategoricalRaster, ct), ct);
        }

        private static string Stamp(string target, string analysisId,
            IReadOnlyList<string> orderedVariables,
            IReadOnlyList<string> orderedVariablePaths,
            IReadOnlyList<FacetEntry> facets,
            int width, int height,
            IReadOnlyDictionary<string, object> parameters,
            string sourceCategoricalRaster,
            CancellationToken ct)
        {
            if (string.IsNullOrWhiteSpace(target)) return null;

            string scratch = Path.Combine(Path.GetTempPath(), "WildlifeTools_LandFacet");
            Directory.CreateDirectory(scratch);
            string jobPath = Path.Combine(scratch, Guid.NewGuid().ToString("N") + ".meta.json");

            try
            {
                // Shape mirrors land_facet_linkage.facet_record() exactly.
                // The catalog path lets the Mahalanobis tool re-find the SAME raster
                // even if the layer has been renamed since; the name is the fallback
                // when a path could not be resolved (service or in-memory layers).
                var vars = new List<object>();
                for (int i = 0; i < (orderedVariables?.Count ?? 0); i++)
                {
                    string src = (orderedVariablePaths != null
                                  && i < orderedVariablePaths.Count)
                                 ? orderedVariablePaths[i] : null;
                    vars.Add(new Dictionary<string, object>
                    {
                        ["order"] = i,
                        ["name"] = orderedVariables[i] ?? string.Empty,
                        ["source"] = src ?? string.Empty,
                    });
                }

                var facetList = new List<object>();
                foreach (var f in facets ?? Array.Empty<FacetEntry>())
                    facetList.Add(new Dictionary<string, object>
                    {
                        ["facet_id"] = f.FacetId,
                        ["category_value"] = f.CategoryValue,
                        ["category_label"] = f.CategoryLabel ?? string.Empty,
                        ["cluster"] = f.Cluster,
                        ["count"] = f.Count,
                    });

                var record = new Dictionary<string, object>
                {
                    ["schema"] = "jenness.landfacet/1",
                    ["record"] = "facets",
                    ["analysis_id"] = analysisId,
                    // InvariantCulture: DateTime.ToString uses the current
                    // culture's CALENDAR for yyyy -- a Buddhist/Um-al-Qura
                    // default calendar stamped year 2569/1447, which the
                    // invariant-parsing reader then compared as Gregorian,
                    // firing false STALE warnings on every fresh chain.
                    ["created"] = DateTime.UtcNow.ToString(
                        "yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture),
                    ["tool"] = "Land Facet Clustering",
                    ["source"] = new Dictionary<string, object>
                    {
                        ["categorical_raster"] = sourceCategoricalRaster ?? string.Empty,
                    },
                    ["grid"] = new Dictionary<string, object>
                    {
                        ["width"] = width,
                        ["height"] = height,
                    },
                    ["variables"] = vars,
                    ["facets"] = facetList,
                    ["params"] = parameters ?? new Dictionary<string, object>(),
                };

                var job = new Dictionary<string, object>
                {
                    ["target"] = target,
                    ["record"] = record,
                };
                File.WriteAllText(jobPath, JsonSerializer.Serialize(job));

                string python = FindProPython();
                string helper = Path.Combine(
                    Path.GetDirectoryName(typeof(CorridorMetadataWriter).Assembly.Location) ?? ".",
                    "Corridor", "corridor_write_metadata.py");
                if (!File.Exists(helper)) return null;

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
                bool ok = false;
                p.OutputDataReceived += (_, e) =>
                {
                    if (e.Data == null) return;
                    if (e.Data.StartsWith("OK ", StringComparison.Ordinal)) ok = true;
                    else if (e.Data.StartsWith("ERROR", StringComparison.Ordinal))
                        err.AppendLine(e.Data);
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
                        return null;
                    }
                }
                p.WaitForExit();   // let the async output handlers drain

                return (p.ExitCode == 0 && ok) ? analysisId : null;
            }
            catch
            {
                return null;       // never let metadata stamping fail the run
            }
            finally
            {
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
