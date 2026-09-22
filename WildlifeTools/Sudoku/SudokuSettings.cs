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
using System.Text.Json;
using System.Text.Json.Serialization;

namespace WildlifeTools.Sudoku
{
    /// <summary>
    /// Persisted Sudoku preferences (skin + difficulty), stored in the same
    /// stable per-user root as the classification-system store so they
    /// survive add-in reinstalls:
    ///     %LOCALAPPDATA%\JennessEnterprises\WildlifeTools\sudoku_settings.json
    /// Load/Save never throw -- a toy must not be able to break the session.
    /// </summary>
    public sealed class SudokuSettings
    {
        [JsonPropertyName("skin")] public string Skin { get; set; } = "Parchment";
        [JsonPropertyName("difficulty")] public int Difficulty { get; set; } = 1;   // 0/1/2

        private static string PathToFile()
        {
            string b = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (string.IsNullOrEmpty(b))
                b = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            return Path.Combine(b, "JennessEnterprises", "WildlifeTools", "sudoku_settings.json");
        }

        public static SudokuSettings Load()
        {
            try
            {
                string p = PathToFile();
                if (File.Exists(p))
                    return JsonSerializer.Deserialize<SudokuSettings>(File.ReadAllText(p))
                           ?? new SudokuSettings();
            }
            catch { /* fall through to defaults */ }
            return new SudokuSettings();
        }

        public void Save()
        {
            try
            {
                string p = PathToFile();
                Directory.CreateDirectory(Path.GetDirectoryName(p));
                File.WriteAllText(p, JsonSerializer.Serialize(this,
                    new JsonSerializerOptions { WriteIndented = true }));
            }
            catch { /* preferences are best-effort */ }
        }
    }
}
