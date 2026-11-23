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
using System.Linq;

namespace WildlifeTools.OnlineData
{
    /// <summary>
    /// What the Online Data windows do with a message of the geoprocessing
    /// tool they run, before it goes into the window's report. One copy for
    /// all the windows, so that they cannot drift apart: which lines say
    /// nothing about the run, when the tool has said it was cancelled, and
    /// how an error or a warning is marked. Text only, no ArcGIS types.
    /// </summary>
    internal static class OnlineReport
    {
        // lines of every geoprocessing run that say nothing about this one
        private static readonly string[] Boilerplate =
        {
            "Start Time:", "Succeeded at", "Failed at", "Failed to execute", "Executing:", "Executing (",
            "View online help files:", "To run this tool from a script", "Running script", "Completed script",
            "Canceled function", "Cancelled function", "import arcpy", "arcpy.ImportToolbox(", "arcpy.jenness.",
        };

        /// <summary>True for a message that does not belong in a window's
        /// report: an empty one, one of the lines every geoprocessing run
        /// writes, or ArcGIS Pro's own "aborted by User" line.</summary>
        internal static bool IsBoilerplate(string text) =>
            string.IsNullOrWhiteSpace(text)
            || Boilerplate.Any(b => text.TrimStart().StartsWith(b, StringComparison.Ordinal))
            || text.TrimEnd().EndsWith("aborted by User.", StringComparison.OrdinalIgnoreCase);

        /// <summary>True when the tool itself has reported that it was
        /// cancelled, so the window need not say it a second time.</summary>
        internal static bool SaysCancelled(string text) =>
            (text ?? "").TrimStart().StartsWith("Cancelled", StringComparison.Ordinal);

        /// <summary>A message as a line of the report, marked the same way in
        /// every window: an error (severity 2) with "Stopped: " and a warning
        /// (severity 1) with "Note: ". The tool's own "Cancelled." arrives as
        /// a warning too, and is left as it is.</summary>
        internal static string Line(string text, int severity) =>
            severity == 2 ? "Stopped: " + text.Trim()
            : severity == 1 && !SaysCancelled(text) ? "Note: " + text.Trim()
            : text.TrimEnd();
    }
}
