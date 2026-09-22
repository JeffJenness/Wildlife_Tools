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
    /// Corridor Designer Tools-group ribbon button that opens the "Land Facet
    /// Data Report" window (a single shared non-modal instance):
    /// pick a dataset and get a human-readable report of its sources, how they
    /// were analyzed, and where it sits in the Land Facet Corridor analysis
    /// sequence, with staleness warnings when a recorded source has since been
    /// regenerated.
    /// </summary>
    internal sealed class LandFacetDescribeButton : Button
    {
        private static LandFacetDescribeWindow _window;

        protected override void OnClick()
        {
            try
            {
                if (_window != null) { _window.Activate(); return; }
                _window = new LandFacetDescribeWindow
                { Owner = FrameworkApplication.Current.MainWindow };
                _window.Closed += (_, __) => _window = null;
                _window.Show();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not open the Corridor Analysis Data Report window:"
                    + Environment.NewLine + Environment.NewLine + ex.Message,
                    "Corridor Analysis Data Report");
            }
        }
    }
}
