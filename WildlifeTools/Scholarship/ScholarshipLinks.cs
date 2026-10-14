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
using System.Diagnostics;

namespace WildlifeTools.Scholarship
{
    /// <summary>
    /// The scholarship's two destinations, shared by the Scholarship card and
    /// the About card so both behave the same: the scholarship page on the
    /// Jenness Enterprises site, and the university's giving page (which,
    /// until it exists, is explained rather than opened).
    /// </summary>
    internal static class ScholarshipLinks
    {
        internal static void OpenPage() => WebHelp.Open(ScholarshipInfo.PageRelative);

        internal static void Donate()
        {
            if (string.IsNullOrWhiteSpace(ScholarshipInfo.NauDonateUrl))
            {
                ArcGIS.Desktop.Framework.Dialogs.MessageBox.Show(
                    "Northern Arizona University's giving page for the "
                    + ScholarshipInfo.FundName + " is being set up. This "
                    + "button will open it as soon as it is ready."
                    + Environment.NewLine + Environment.NewLine
                    + "Until then, the scholarship page has the latest: "
                    + "click About the scholarship.",
                    ScholarshipInfo.FundName);
                return;
            }
            OpenUrl(ScholarshipInfo.NauDonateUrl);
        }

        /// <summary>Opens a web or mailto address in the default browser or
        /// mail program; a missing or blocked program is ignored.</summary>
        internal static void OpenUrl(string url)
        {
            try
            {
                Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
            }
            catch (Exception)
            {
                // A missing or blocked browser should not crash the card.
            }
        }
    }
}
