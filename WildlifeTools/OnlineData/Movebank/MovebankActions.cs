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
using System.Threading;
using System.Threading.Tasks;
using ArcGIS.Desktop.Core.Geoprocessing;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.OnlineData.Movebank
{
    /// <summary>
    /// Everything the Movebank Animal Tracks window needs from ArcGIS Pro:
    /// default output paths, running the geoprocessing tool that fetches
    /// and writes the data, and the help page. Kept apart from the panel
    /// so the panel can be built and drawn outside Pro.
    /// </summary>
    internal static class MovebankActions
    {
        internal const string HelpPage = "data-tools/movebank-animal-tracks.html";
        internal const string Title = "Movebank Animal Tracks";
        internal const string ToolName = "MovebankAnimalTracks";

        // the tool's "What to create" choices
        internal const string OutBoth = "Points and track lines";
        internal const string OutPoints = "Points";
        internal const string OutLines = "Track lines";

        internal static string ToolboxPath => OnlineToolRunner.ToolboxPath;

        /// <summary>A feature class path in the project's default
        /// geodatabase, with a name not yet in use.</summary>
        internal static Task<string> DefaultOutputPathAsync(string baseName) =>
            Gbif.GbifActions.DefaultOutputPathAsync(baseName);

        /// <summary>Runs the Movebank Animal Tracks geoprocessing tool. The
        /// argument order is the tool's parameter order.</summary>
        internal static Task<IGPResult> RunToolAsync(string dataset, string output, string outPoints, string outLines,
            string animals, bool includeFlagged, bool acceptTerms, Action<string, int> onMessage,
            Action<string, int> onProgress, CancellationToken ct)
        {
            IReadOnlyList<string> args = Geoprocessing.MakeValueArray(
                dataset,
                output,
                outPoints ?? "",
                outLines ?? "",
                animals ?? "",
                "",                             // first date
                "",                             // last date
                includeFlagged ? "true" : "false",
                acceptTerms ? "true" : "false");
            return OnlineToolRunner.RunAsync(ToolName, args, onMessage, onProgress, ct);
        }

        /// <summary>The study areas the map offers: its extent and its polygon layers.</summary>
        internal static Task<List<Gbif.GbifActions.AreaChoice>> ListAreasAsync() => Gbif.GbifActions.ListAreasAsync();

        /// <summary>The rectangle around a study area, in WGS 84, or null.</summary>
        internal static Task<(double West, double South, double East, double North)?> AreaBoxAsync(
            Gbif.GbifActions.AreaChoice choice) => INaturalist.INatActions.AreaBoxAsync(choice);

        internal static int Severity(IGPMessage m) => OnlineToolRunner.Severity(m);

        internal static string BrowseOutput(string current, string title) =>
            OnlineBrowse.FeatureClass(current, title, Title);

        internal static void Info(string message) => MessageBox.Show(message, Title);

        internal static void Help() => global::WildlifeTools.WebHelp.Open(HelpPage);
    }
}
