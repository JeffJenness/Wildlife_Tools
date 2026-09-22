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

using System.IO;
using System.Reflection;

namespace WildlifeTools
{
    /// <summary>
    /// Locates the Jenness Enterprises Python toolbox that is deployed alongside the
    /// add-in assembly (extracted into a "Toolboxes" subfolder of the add-in install
    /// folder at run time).
    /// </summary>
    internal static class ToolboxPaths
    {
        /// <summary>Full path to the deployed JennessEnterprisesTools.pyt.</summary>
        public static string Pyt
        {
            get
            {
                string dir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
                return Path.Combine(dir, "Toolboxes", "JennessEnterprisesTools.pyt");
            }
        }

        /// <summary>
        /// Tool reference for a tool in the toolbox, e.g.
        /// "...\JennessEnterprisesTools.pyt\LoCoHStandard", or null if the toolbox
        /// cannot be found next to the assembly.
        /// </summary>
        public static string ToolPath(string toolName)
        {
            string pyt = Pyt;
            return File.Exists(pyt) ? Path.Combine(pyt, toolName) : null;
        }
    }
}
