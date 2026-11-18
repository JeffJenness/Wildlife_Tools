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
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using WildlifeTools.OnlineData.Gbif;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.OnlineData.BurnSeverity
{
    /// <summary>
    /// Everything the Burn Severity windows need from ArcGIS Pro: the
    /// study-area choices of the map, which fires touch a polygon study
    /// area, default output paths, running the geoprocessing tool, and the
    /// help page. Kept apart from the panel so the panel can be built and
    /// drawn outside Pro.
    /// </summary>
    internal static class BurnActions
    {
        internal static string ToolName(string program) => program + "BurnSeverity";

        internal static string HelpPage(string program) => "data-tools/" + program.ToLowerInvariant() + "-burn-severity.html";

        internal static string Title(string program) => program + " Burn Severity";

        internal static string ToolboxPath => OnlineToolRunner.ToolboxPath;

        internal static Task<List<GbifActions.AreaChoice>> ListAreasAsync() => GbifActions.ListAreasAsync();

        /// <summary>The study area as polygons in WGS 84. A search reads it
        /// ONCE and uses the result twice: for the rectangle the catalog is
        /// asked for (<see cref="BoxOf"/>) and for the test of each fire
        /// against the polygons themselves (<see cref="TouchingAsync"/>).
        /// Reading a layer means straightening, projecting and dissolving
        /// all its polygons, which is not something to do twice.</summary>
        internal static Task<List<List<List<(double Lon, double Lat)>>>> AreaPolygonsAsync(
            GbifActions.AreaChoice choice) => GbifActions.AreaPolygonsAsync(choice);

        /// <summary>The rectangle around a study area's polygons, in WGS 84, or null.</summary>
        internal static (double West, double South, double East, double North)? BoxOf(
            List<List<List<(double Lon, double Lat)>>> area) => INaturalist.INatActions.BoxOf(area);

        /// <summary>The fires whose perimeter touches the study area's
        /// polygons (the catalog itself searches by rectangle only).</summary>
        internal static async Task<List<BurnFire>> TouchingAsync(
            List<List<List<(double Lon, double Lat)>>> area, List<BurnFire> fires)
        {
            return await QueuedTask.Run(() =>
            {
                SpatialReference wgs = SpatialReferences.WGS84;
                Polygon Build(List<List<List<(double Lon, double Lat)>>> polys)
                {
                    var b = new PolygonBuilderEx(wgs);
                    foreach (var poly in polys)
                    {
                        foreach (var ring in poly)
                        {
                            b.AddPart(ring.Select(p => new Coordinate2D(p.Lon, p.Lat)));
                        }
                    }
                    return b.ToGeometry();
                }
                Polygon study = Build(area);
                var kept = new List<BurnFire>();
                foreach (BurnFire f in fires)
                {
                    bool touches = true;                // when in doubt, keep the fire
                    try
                    {
                        if (f.Polygons != null && f.Polygons.Count > 0)
                        {
                            touches = GeometryEngine.Instance.Intersects(study, Build(f.Polygons));
                        }
                    }
                    catch (Exception)
                    {
                        touches = true;
                    }
                    if (touches)
                    {
                        kept.Add(f);
                    }
                }
                return kept;
            });
        }

        internal static Task<string> DefaultOutputPathAsync(string baseName) =>
            GbifActions.DefaultOutputPathAsync(baseName);

        /// <summary>What the window hands the geoprocessing tool.</summary>
        internal sealed class ToolRequest
        {
            public string Program;
            /// <summary>A fire's event identifier, or the identifier, a
            /// colon and a map ID for one assessment of the fire.</summary>
            public List<string> FireIds = new List<string>();
            public int? YearFrom, YearTo;                   // the span of the checked fires: narrows the catalog search
            public string Perimeters;
            public List<string> Products = new List<string>();
            public string RasterWorkspace;                  // empty: the geodatabase of the perimeters
            public string Email;                            // empty: no bundle request
            public bool Images;
            public string Projection = "UTM";
        }

        /// <summary>Runs the program's Burn Severity geoprocessing tool for
        /// exactly the fires checked in the window. The argument order is
        /// the tool's parameter order.</summary>
        internal static Task<IGPResult> RunToolAsync(ToolRequest r, Action<string, int> onMessage,
            Action<string, int> onProgress, CancellationToken ct)
        {
            IReadOnlyList<string> args = Geoprocessing.MakeValueArray(
                "",                                                     // fire name: the IDs say which fires
                r.YearFrom.HasValue ? (object)r.YearFrom.Value : "",
                r.YearTo.HasValue ? (object)r.YearTo.Value : "",
                "",                                                     // study area: already applied to the list
                "",
                string.Join("; ", r.FireIds),
                r.Perimeters,
                string.Join(";", r.Products.Select(p => "'" + p + "'")),
                r.RasterWorkspace ?? "",
                r.Email ?? "",
                r.Images ? "true" : "false",
                r.Projection ?? "UTM");
            return OnlineToolRunner.RunAsync(ToolName(r.Program), args, onMessage, onProgress, ct);
        }

        internal static int Severity(IGPMessage m) => OnlineToolRunner.Severity(m);

        internal static string BrowsePerimeters(string program, string current) =>
            OnlineBrowse.FeatureClass(current, "Save the fire perimeters as", Title(program));

        internal static string BrowseRasters(string current) =>
            OnlineBrowse.FolderOrGeodatabase(current, "Choose the folder or file geodatabase for the rasters");

        internal static void Info(string program, string message) => MessageBox.Show(message, Title(program));

        internal static void Help(string program) => global::WildlifeTools.WebHelp.Open(HelpPage(program));
    }
}
