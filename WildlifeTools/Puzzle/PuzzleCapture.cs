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
using System.IO;
using System.Threading.Tasks;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;

namespace WildlifeTools.Puzzle
{
    /// <summary>The result of snapshotting the active view for the puzzle.</summary>
    internal sealed class PuzzleSnapshot
    {
        public string ImagePath;
        /// <summary>True if the snapshot is of a whole layout page (rectangular,
        /// used as-is); false if it is a map view (square-cropped by the caller).</summary>
        public bool IsLayout;
    }

    /// <summary>
    /// Captures the active map or layout view to a temporary PNG, for use as the
    /// puzzle image. A layout view (if one is active) wins over a map view, and
    /// its whole page is exported; otherwise the active map view is exported at
    /// its current camera. All export work runs on the MCT via QueuedTask.
    /// </summary>
    internal static class PuzzleCapture
    {
        public static Task<PuzzleSnapshot> CaptureActiveViewAsync()
        {
            return QueuedTask.Run(() =>
            {
                string path = Path.Combine(
                    Path.GetTempPath(),
                    "WildlifeTools_puzzle_" + Guid.NewGuid().ToString("N") + ".png");

                // A layout view takes precedence: export the entire page.
                var layoutView = LayoutView.Active;
                if (layoutView != null)
                {
                    Layout layout = layoutView.Layout;
                    if (layout == null)
                    {
                        throw new InvalidOperationException(
                            "The active layout could not be read.");
                    }
                    var fmt = new PNGFormat
                    {
                        OutputFileName = path,
                        Resolution = 100,
                        HasTransparentBackground = false,
                    };
                    layout.Export(fmt);
                    return new PuzzleSnapshot { ImagePath = path, IsLayout = true };
                }

                // Otherwise export the active map view at its current extent.
                var mapView = MapView.Active;
                if (mapView == null)
                {
                    throw new InvalidOperationException(
                        "Open a map or layout view first, then try again.");
                }
                var mfmt = new PNGFormat
                {
                    OutputFileName = path,
                    Resolution = 96,
                    HasTransparentBackground = false,
                };
                mapView.Export(mfmt);
                return new PuzzleSnapshot { ImagePath = path, IsLayout = false };
            });
        }
    }
}
