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

namespace WildlifeTools.Classification
{
    /// <summary>
    /// Ribbon button that opens the Classification System Builder: create,
    /// edit, duplicate, and delete the reusable classification systems (class
    /// rules, names, and colors) that the Slope Position / Landform / General
    /// Grid Classification geoprocessing tools apply. Systems are stored as
    /// JSON in a per-user location that survives add-in reinstalls.
    /// </summary>
    internal class ClassificationBuilderButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                var win = new ClassificationBuilderWindow
                {
                    Owner = FrameworkApplication.Current.MainWindow
                };
                win.ShowDialog();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the Classification System Builder:" +
                    Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
