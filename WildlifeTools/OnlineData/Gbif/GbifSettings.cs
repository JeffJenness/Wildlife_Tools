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
using System.Text;
using System.Text.Json;

namespace WildlifeTools.OnlineData.Gbif
{
    /// <summary>
    /// The choices the GBIF Species Occurrences window remembers from one
    /// use to the next, kept per user in
    /// LocalAppData\JennessEnterprises\WildlifeTools\gbif_window.json.
    /// Never the sign-in: that lives in Windows Credential Manager.
    /// Loading and saving never throw; a missing or unreadable file gives
    /// the defaults. No ArcGIS types here.
    /// </summary>
    public sealed class GbifSettings
    {
        /// <summary>Tab 1: search common names instead of scientific names.</summary>
        public bool SearchCommonNames { get; set; }

        /// <summary>The folder a GBIF zip file was last loaded from.</summary>
        public string LastZipFolder { get; set; }

        private static string FilePath => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "JennessEnterprises", "WildlifeTools", "gbif_window.json");

        public static GbifSettings Load()
        {
            try
            {
                string path = FilePath;
                if (File.Exists(path))
                {
                    return JsonSerializer.Deserialize<GbifSettings>(File.ReadAllText(path, Encoding.UTF8))
                           ?? new GbifSettings();
                }
            }
            catch (Exception)
            {
            }
            return new GbifSettings();
        }

        public void Save()
        {
            try
            {
                string path = FilePath;
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                File.WriteAllText(path, JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }),
                    new UTF8Encoding(false));
            }
            catch (Exception)
            {
                // a choice that cannot be remembered is not worth a message
            }
        }
    }
}
