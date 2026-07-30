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
