// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Award notice, marking of modified versions, and
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
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using WildlifeTools.OnlineData.Gbif;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.OnlineData.INaturalist
{
    /// <summary>
    /// Everything the iNaturalist Observations window needs from ArcGIS Pro:
    /// the study-area choices of the map, a default output path, running
    /// the geoprocessing tool that fetches and writes the observations, and
    /// the help page. Kept apart from the panel so the panel can be built
    /// and drawn outside Pro.
    /// </summary>
    internal static class INatActions
    {
        internal const string HelpPage = "data-tools/inaturalist-observations.html";
        internal const string Title = "iNaturalist Observations";
        internal const string ToolName = "INaturalistObservations";

        internal const string GradeResearch = "Research grade";
        internal const string GradeNeedsId = "Needs ID";
        internal const string GradeCasual = "Casual";

        internal static string ToolboxPath => OnlineToolRunner.ToolboxPath;

        /// <summary>The study-area choices for the active map (the same
        /// list the GBIF window offers).</summary>
        internal static Task<List<GbifActions.AreaChoice>> ListAreasAsync() => GbifActions.ListAreasAsync();

        /// <summary>The rectangle around a study area, in WGS 84, or null
        /// when it has no polygons.</summary>
        internal static async Task<(double West, double South, double East, double North)?> AreaBoxAsync(
            GbifActions.AreaChoice choice)
        {
            return BoxOf(await GbifActions.AreaPolygonsAsync(choice));
        }

        /// <summary>The rectangle around polygons that have already been
        /// read (each a list of rings, outer ring first), in WGS 84, or
        /// null when there are none.</summary>
        internal static (double West, double South, double East, double North)? BoxOf(
            List<List<List<(double Lon, double Lat)>>> polys)
        {
            var pts = (polys ?? new List<List<List<(double Lon, double Lat)>>>())
                .Where(p => p.Count > 0).SelectMany(p => p[0]).ToList();
            if (pts.Count == 0)
            {
                return null;
            }
            return (Math.Max(-180, pts.Min(p => p.Lon)), Math.Max(-90, pts.Min(p => p.Lat)),
                    Math.Min(180, pts.Max(p => p.Lon)), Math.Min(90, pts.Max(p => p.Lat)));
        }

        internal static Task<string> DefaultOutputPathAsync(string baseName) =>
            GbifActions.DefaultOutputPathAsync(baseName);

        /// <summary>What the window hands the geoprocessing tool.</summary>
        internal sealed class ToolRequest
        {
            public List<string> Taxa = new List<string>();      // "Name (id)"
            public GbifActions.AreaChoice Area;                 // null or KindWorld = no study area
            public string Place;                                // "Display name (id)" or null
            public INatQuery Query;
            public string OutputPath;
        }

        private static string Flag(bool b) => b ? "true" : "false";

        /// <summary>Runs the iNaturalist Observations geoprocessing tool.
        /// The argument order is the tool's parameter order.</summary>
        internal static async Task<IGPResult> RunToolAsync(ToolRequest r, Action<string, int> onMessage,
            Action<string, int> onProgress, CancellationToken ct)
        {
            object layerArg = "";
            object extentArg = "";
            if (r.Area != null && r.Area.Kind == GbifActions.AreaChoice.KindLayer && r.Area.Layer is FeatureLayer fl)
            {
                layerArg = fl;
            }
            else if (r.Area != null && r.Area.Kind == GbifActions.AreaChoice.KindExtent)
            {
                extentArg = await QueuedTask.Run(() => (object)(MapView.Active?.Extent
                    ?? throw new InvalidOperationException("There is no active map view.")));
            }
            INatQuery q = r.Query;
            CultureInfo inv = CultureInfo.InvariantCulture;
            var grades = new List<string>();
            if (q.Research) grades.Add("'" + GradeResearch + "'");
            if (q.NeedsId) grades.Add("'" + GradeNeedsId + "'");
            if (q.Casual) grades.Add(GradeCasual);
            IReadOnlyList<string> args = Geoprocessing.MakeValueArray(
                string.Join("; ", r.Taxa),
                layerArg,
                extentArg,
                r.Place ?? "",
                string.Join(";", grades),
                Flag(q.ExcludeCaptive),
                q.DateFrom ?? "",
                q.DateTo ?? "",
                string.Join(";", q.Months.Select(m => m.ToString(inv))),
                Flag(q.OpenLicenseOnly),
                Flag(q.ExcludeNonCommercial),
                Flag(q.PhotosOnly),
                q.MaxUncertaintyMeters.HasValue ? q.MaxUncertaintyMeters.Value.ToString("R", inv) : "",
                Flag(q.ExcludeObscured),
                q.Establishment ?? "Any",
                q.Observer ?? "",
                q.Project ?? "",
                r.OutputPath);
            return await OnlineToolRunner.RunAsync(ToolName, args, onMessage, onProgress, ct);
        }

        internal static int Severity(IGPMessage m) => OnlineToolRunner.Severity(m);

        internal static string BrowseOutput(string current) =>
            OnlineBrowse.FeatureClass(current, "Save the iNaturalist observations as", Title);

        internal static void Info(string message) => MessageBox.Show(message, Title);

        internal static void Help() => global::WildlifeTools.WebHelp.Open(HelpPage);
    }
}
