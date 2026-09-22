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
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Geometric
{
    /// <summary>
    /// Geometric Tools ribbon button that opens the Repeating Shapes
    /// window -- the friendly image-gallery front end (only reachable
    /// from this button) that gathers the parameters and runs the real
    /// RepeatingShapes geoprocessing tool through the GP framework, so
    /// History and environments behave exactly as a pane run. The GP
    /// pane and ModelBuilder continue to use the standard tool dialog.
    /// </summary>
    internal class RepeatingShapesButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                RepeatingShapesWindow.ShowSingleton();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the Repeating Shapes window:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
