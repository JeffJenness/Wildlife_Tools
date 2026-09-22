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
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Opens the non-modal "Create Reclass Tables for the Create Habitat
    /// Suitability Model Tool" window: an interactive remap builder showing
    /// the chosen raster's histogram and statistics, translucent coverage
    /// washes as remap rows are entered, and a save step that writes an
    /// ASCII remap file next to a file raster or a geodatabase remap table
    /// next to a geodatabase raster -- both accepted directly by the Create
    /// Habitat Suitability Model geoprocessing tool.
    /// </summary>
    internal class HsmRemapButton : Button
    {
        private HsmRemapWindow _window;

        protected override void OnClick()
        {
            try
            {
                if (_window != null)
                {
                    _window.Activate();
                    return;
                }
                _window = new HsmRemapWindow
                {
                    Owner = FrameworkApplication.Current.MainWindow,
                };
                _window.Closed += (s, e) => _window = null;
                _window.Show();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the remap table builder:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
