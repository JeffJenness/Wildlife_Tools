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
using ArcGIS.Desktop.Core;

namespace WildlifeTools
{
    /// <summary>
    /// The folder the suite's windows offer when saving a graph or table
    /// image. Remembered per PROJECT (persisted through Module1's project
    /// settings) from the last image actually saved, so a project whose
    /// figures live in one folder keeps offering that folder; before any
    /// save it is the project's home folder, and outside a project the
    /// user's Documents.
    /// </summary>
    internal static class ImageSaveFolder
    {
        public static string Initial()
        {
            try
            {
                string last = Module1.LastImageFolder;
                if (!string.IsNullOrEmpty(last) && Directory.Exists(last))
                    return last;
                string home = Project.Current?.HomeFolderPath;
                if (!string.IsNullOrEmpty(home) && Directory.Exists(home))
                    return home;
            }
            catch { }
            return Environment.GetFolderPath(
                Environment.SpecialFolder.MyDocuments);
        }

        /// <summary>Remember the folder of a just-saved image and mark the
        /// project dirty so the choice is written with its next save.</summary>
        public static void Remember(string savedFile)
        {
            try
            {
                string dir = Path.GetDirectoryName(savedFile);
                if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir)) return;
                if (string.Equals(dir, Module1.LastImageFolder,
                        StringComparison.OrdinalIgnoreCase)) return;
                Module1.LastImageFolder = dir;
                Project.Current?.SetDirty(true);
            }
            catch { }
        }
    }
}
