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

namespace WildlifeTools
{
    /// <summary>
    /// Opens the Jenness Enterprises online help pages. Every custom
    /// window's Help button routes through here, so pointing the whole
    /// suite at the live site is a one-line change (BaseUrl).
    /// </summary>
    internal static class WebHelp
    {
        // Root of the online help site. Leave empty until the site is
        // hosted; the Help buttons then explain where the pages will
        // live instead of opening a dead link.
        internal const string BaseUrl = "https://www.jennessent.com";

        internal static void Open(string relativePage)
        {
            if (string.IsNullOrWhiteSpace(BaseUrl))
            {
                ArcGIS.Desktop.Framework.Dialogs.MessageBox.Show(
                    "The online help pages are being prepared and will "
                    + "be available at www.jennessent.com.",
                    "Wildlife and Forestry Tools help");
                return;
            }
            try
            {
                System.Diagnostics.Process.Start(
                    new System.Diagnostics.ProcessStartInfo(
                        BaseUrl.TrimEnd('/') + "/" + relativePage)
                    { UseShellExecute = true });
            }
            catch (Exception)
            {
                // A browser hiccup should never crash the window.
            }
        }
    }
}
