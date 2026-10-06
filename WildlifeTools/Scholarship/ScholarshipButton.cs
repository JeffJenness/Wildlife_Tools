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
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.Scholarship
{
    /// <summary>
    /// "Take a Break"-group ribbon button for the Lois Engelman Memorial
    /// Scholarship Fund: the 100th tool, reserved for it. Opens the
    /// <see cref="ScholarshipWindow"/> card, which says what the fund is and
    /// links to the scholarship page and to the university's giving page.
    /// Runs entirely on the UI thread; one shared non-modal window.
    /// </summary>
    internal sealed class ScholarshipButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                ScholarshipWindow.ShowCard();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    $"Could not open the scholarship window:{Environment.NewLine}{Environment.NewLine}{ex.Message}",
                    ScholarshipInfo.FundName);
            }
        }
    }
}
