// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Award notice, marking of modified versions, use of names,
// and an additional permission to link with ArcGIS Pro).
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

namespace WildlifeTools.OnlineData
{
    /// <summary>
    /// Runs one of the Online Data geoprocessing tools for a window: the
    /// run is recorded in the Geoprocessing History, its outputs are added
    /// to the map, and its messages and progress are handed back as they
    /// arrive, so the window can show them.
    /// </summary>
    internal static class OnlineToolRunner
    {
        /// <summary>The full path of the toolbox on this computer, for the
        /// ImportToolbox line of a script.</summary>
        internal static string ToolboxPath => global::WildlifeTools.ToolboxPaths.Pyt;

        /// <summary><paramref name="onMessage"/> gets each message with its
        /// severity (0 information, 1 warning, 2 error);
        /// <paramref name="onProgress"/> the progress label and percent as
        /// they change (either may be null / -1). Both may be called on a
        /// background thread.</summary>
        internal static async Task<IGPResult> RunAsync(string toolName, IReadOnlyList<string> args,
            Action<string, int> onMessage, Action<string, int> onProgress, CancellationToken ct)
        {
            string toolPath = global::WildlifeTools.ToolboxPaths.ToolPath(toolName)
                ?? throw new InvalidOperationException("Could not find JennessEnterprisesTools.pyt next to the add-in.");
            GPToolExecuteEventHandler callback = (eventName, o) =>
            {
                try
                {
                    switch (eventName)
                    {
                        case "OnMessage":
                            if (o is IGPMessage m && onMessage != null)
                            {
                                onMessage(m.Text ?? "", m.Type == GPMessageType.Error ? 2
                                    : m.Type == GPMessageType.Warning ? 1 : 0);
                            }
                            break;
                        case "OnProgressMessage":
                            onProgress?.Invoke(o as string, -1);
                            break;
                        case "OnProgressPos":
                            if (o is int pos)
                            {
                                onProgress?.Invoke(null, pos);
                            }
                            break;
                    }
                }
                catch (Exception)
                {
                    // a display problem must never stop the tool
                }
            };
            // a window has always replaced an existing feature class of the same name
            var env = Geoprocessing.MakeEnvironmentArray(overwriteoutput: true);
            try
            {
                return await Geoprocessing.ExecuteToolAsync(toolPath, args, env, ct, callback,
                    GPExecuteToolFlags.Default | GPExecuteToolFlags.AddToHistory);
            }
            catch (NullReferenceException)
            {
                // the flags overloads have misfired before (see the Land Facet window)
                return await Geoprocessing.ExecuteToolAsync(toolPath, args);
            }
        }

        /// <summary>The severity of a tool message, as RunAsync reports it.</summary>
        internal static int Severity(IGPMessage m) =>
            m.Type == GPMessageType.Error ? 2 : m.Type == GPMessageType.Warning ? 1 : 0;
    }
}
