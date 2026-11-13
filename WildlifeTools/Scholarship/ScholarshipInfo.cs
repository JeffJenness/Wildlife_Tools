// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Award Fund notice, marking of modified versions, and
// use of names).
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

namespace WildlifeTools.Scholarship
{
    /// <summary>
    /// The names, the notice and the addresses of the Lois Engelman Memorial
    /// Award Fund, kept in ONE place so that the window, the ribbon
    /// and anything added later (an About dialog) say the same thing.
    /// </summary>
    internal static class ScholarshipInfo
    {
        public const string FundName = "Lois Engelman Memorial Award Fund";

        /// <summary>The tool's full name, as LICENSE-ADDITIONAL-TERMS.md and
        /// the README give it.</summary>
        public const string ToolName = "Donate to the " + FundName;

        /// <summary>The notice that LICENSE-ADDITIONAL-TERMS.md, section 1,
        /// requires to be preserved "in the ribbon element that opens the
        /// award fund page". Keep it VERBATIM with that file; the
        /// address that follows it is <see cref="NoticeUrl"/>.</summary>
        public const string Notice =
            "This software is dedicated to the memory of Lois Engelman (wife, "
            + "best friend and partner of the author for 36 years). If these "
            + "tools are helpful and valuable to you, please consider donating "
            + "to the Lois Engelman Memorial Award Fund, which helps "
            + "educate and train the next generation of land managers and "
            + "ecologists:";

        /// <summary>The address published with the notice. The license lets
        /// it be updated to a successor address published by Jenness
        /// Enterprises.</summary>
        public const string NoticeUrl = "https://www.jennessent.com/Lois/";

        /// <summary>The award page on the Jenness Enterprises site,
        /// relative to the site root (opened through <see cref="WebHelp"/>,
        /// so it follows the site wherever it is hosted).</summary>
        public const string PageRelative = "lois-engelman-memorial-scholarship.html";

        /// <summary>Northern Arizona University's giving page for the fund.
        /// Opened 2026-11-13; the form opens with the designation "Lois
        /// Engelman Memorial Award" already selected. If this is ever
        /// EMPTY, the Donate button says the page is being set up instead
        /// of opening a dead link.</summary>
        public const string NauDonateUrl =
            "https://securelb.imodules.com/s/1898/giving19/form.aspx?sid=1898&gid=2&pgid=418&bledit=1&dids=4296";
    }
}
