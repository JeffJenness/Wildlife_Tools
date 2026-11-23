// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Award notice, marking of modified versions, use of names,
// and an additional permission to link with ArcGIS Pro).
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
using System.Linq;
using ArcGIS.Desktop.Catalog;
using ArcGIS.Desktop.Core;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.OnlineData
{
    /// <summary>
    /// The Browse buttons of the Online Data windows: ArcGIS Pro's own
    /// dialogs for choosing where an output goes. Reached only from clicks,
    /// through each window's Actions class, so the panels can still be
    /// built and drawn outside Pro.
    /// </summary>
    internal static class OnlineBrowse
    {
        /// <summary>Asks where to save an output feature class: ArcGIS Pro's
        /// own save dialog, opened at the geodatabase of the path now in
        /// the box when it exists, else at the project's default
        /// geodatabase. Returns the full path of a feature class inside a
        /// file geodatabase, or null when the user cancelled or chose a
        /// place that is not one (the dialog also offers folders, where
        /// the result would be a shapefile, which these tools do not
        /// write: the user is told so). Nothing is created.</summary>
        internal static string FeatureClass(string current, string title, string toolTitle)
        {
            var dlg = new SaveItemDialog
            {
                Title = title,
                Filter = ItemFilters.FeatureClasses_All,
            };
            try
            {
                string start = null;
                string cur = (current ?? "").Trim().Trim('"').Replace('/', '\\').TrimEnd('\\');
                int cut = cur.LastIndexOf('\\');
                if (cut > 0 && Directory.Exists(cur.Substring(0, cut)))
                {
                    start = cur.Substring(0, cut);              // a file geodatabase is a folder on disk
                    dlg.OverwritePrompt = true;
                }
                dlg.InitialLocation = start ?? Project.Current?.DefaultGeodatabasePath;
            }
            catch (Exception)
            {
            }
            if (dlg.ShowDialog() != true || string.IsNullOrWhiteSpace(dlg.FilePath))
            {
                return null;
            }
            // with an empty DefaultExt the dialog returns the name with a
            // dot after it ("name."): the dot is not part of the name
            string path = dlg.FilePath.Trim().Replace('/', '\\').TrimEnd('\\', '.', ' ');
            foreach (string ext in new[] { ".shp", ".dbf" })
            {
                if (path.EndsWith(ext, StringComparison.OrdinalIgnoreCase))
                {
                    path = path.Substring(0, path.Length - ext.Length);
                }
            }
            // inside a file geodatabase: ".gdb" followed by a separator, then the name, directly
            int at = -1;
            string low = path.ToLowerInvariant();
            for (int i = low.IndexOf(".gdb", StringComparison.Ordinal); i >= 0; i = low.IndexOf(".gdb", i + 4, StringComparison.Ordinal))
            {
                if (i + 4 < low.Length && low[i + 4] == '\\')
                {
                    at = i + 4;
                }
            }
            if (at < 0 || path.IndexOf('\\', at + 1) >= 0 || at + 1 >= path.Length)
            {
                MessageBox.Show("Choose a file geodatabase and type a name for the feature class. It must be saved "
                    + "directly inside a file geodatabase: not in a folder (that would be a shapefile), and not inside a "
                    + "feature dataset.", toolTitle);
                return null;
            }
            string name = path.Substring(at + 1);
            var clean = new System.Text.StringBuilder();
            foreach (char c in name)
            {
                clean.Append(char.IsLetterOrDigit(c) || c == '_' ? c : '_');
            }
            if (clean.Length > 0 && char.IsDigit(clean[0]))
            {
                clean.Insert(0, '_');
            }
            return path.Substring(0, at + 1) + clean;
        }

        /// <summary>Asks for a folder or a file geodatabase (where rasters
        /// are written). Returns its path, or null when cancelled.</summary>
        internal static string FolderOrGeodatabase(string current, string title)
        {
            var filter = new BrowseProjectFilter { Name = "Folders and file geodatabases" };
            filter.AddFilter(BrowseProjectFilter.GetFilter(ItemFilters.Folders));
            filter.AddFilter(BrowseProjectFilter.GetFilter(ItemFilters.Geodatabases));
            var dlg = new OpenItemDialog { Title = title, MultiSelect = false, BrowseFilter = filter };
            try
            {
                string cur = (current ?? "").Trim().Trim('"').Replace('/', '\\').TrimEnd('\\');
                int cut = cur.LastIndexOf('\\');
                dlg.InitialLocation = cut > 0 && Directory.Exists(cur.Substring(0, cut))
                    ? cur.Substring(0, cut) : Project.Current?.HomeFolderPath;
            }
            catch (Exception)
            {
            }
            if (dlg.ShowDialog() != true || dlg.Items == null)
            {
                return null;
            }
            return dlg.Items.FirstOrDefault()?.Path;
        }
    }
}
