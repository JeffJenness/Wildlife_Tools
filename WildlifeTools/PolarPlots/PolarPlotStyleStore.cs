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

namespace WildlifeTools.PolarPlots
{
    /// <summary>
    /// Saves/loads named polar-plot STYLE presets (plot type, bins, diameter,
    /// every color, font, style flag, and reference-line/label/title option --
    /// everything about appearance, nothing about the data source) as JSON files
    /// in the per-user store used by the rest of the add-in:
    /// %LOCALAPPDATA%\JennessEnterprises\WildlifeTools\polar_plot_styles\.
    /// The dialogs default there but the user may browse anywhere.
    /// </summary>
    internal static class PolarPlotStyleStore
    {
        public static string StoreDir()
        {
            string b = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (string.IsNullOrEmpty(b))
                b = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            string dir = Path.Combine(b, "JennessEnterprises", "WildlifeTools",
                                      "polar_plot_styles");
            Directory.CreateDirectory(dir);
            return dir;
        }

        public static void Save(string path, PolarPlotStyleDto dto)
        {
            var opts = new JsonSerializerOptions { WriteIndented = true };
            File.WriteAllText(path, JsonSerializer.Serialize(dto, opts));
        }

        public static PolarPlotStyleDto Load(string path)
        {
            var opts = new JsonSerializerOptions
            { ReadCommentHandling = JsonCommentHandling.Skip, AllowTrailingCommas = true };
            return JsonSerializer.Deserialize<PolarPlotStyleDto>(
                File.ReadAllText(path), opts);
        }
    }

    /// <summary>JSON-friendly snapshot of the appearance settings. Colors are
    /// [r,g,b,a] arrays (0-255). Versioned for future evolution.</summary>
    internal sealed class PolarPlotStyleDto
    {
        [JsonPropertyName("format")] public string Format { get; set; } = "JennessPolarPlotStyle";
        [JsonPropertyName("version")] public int Version { get; set; } = 1;

        public int PlotTypeIndex { get; set; }         // combo index (0..)
        public int BinCount { get; set; } = 72;
        public string Units { get; set; } = "in";      // "in"/"cm" display unit
                                                       // (diameters below stay inches)
        public double PlotDiameterIn { get; set; } = 4.0;
        public double InnerDiameterIn { get; set; } = 1.5;
        public int[] BearingLine { get; set; }
        public double BearingLineWidthPt { get; set; } = 0.5;

        public int WindClasses { get; set; } = 4;
        public int[] WindLow { get; set; }
        public int[] WindHigh { get; set; }
        public double SmoothingDeg { get; set; } = 20.0;
        public double DotDiameterIn { get; set; } = 0.08;   // inches, like the diameters
        public bool EqualArea { get; set; }
        public bool ShowMeanArrow { get; set; }
        public bool ShowMeanCI { get; set; }
        public int[] MeanArrowColor { get; set; }

        public int[] BinFill { get; set; }
        public int[] BinOutline { get; set; }
        public int[] LowDensity { get; set; }
        public int[] HighDensity { get; set; }
        public double BinOutlineWidthPt { get; set; } = 0.5;

        public bool ShowPrimaryRefLines { get; set; } = true;
        public bool ShowSecondaryRefRays { get; set; } = true;
        public int SecondaryRefBearings { get; set; } = 12;
        public bool ShowSecondaryRefCircles { get; set; } = true;
        public int SecondaryRefCircles { get; set; } = 4;

        public bool ShowAzimuthLabels { get; set; } = true;
        public bool AzimuthLabelsRotated { get; set; }
        public bool ShowInnerLabels { get; set; } = true;
        public bool InnerN { get; set; } = true;
        public bool InnerE { get; set; }
        public bool InnerS { get; set; } = true;
        public bool InnerW { get; set; }

        public string LabelFontFamily { get; set; } = "Segoe UI";
        public double LabelFontSizePt { get; set; } = 9;
        public string LabelFontStyle { get; set; } = "Regular";
        public int[] LabelColor { get; set; }

        public bool ShowPrimaryTitle { get; set; }
        public string TitleFontFamily { get; set; } = "Segoe UI";
        public double TitleFontSizePt { get; set; } = 16;
        public string TitleFontStyle { get; set; } = "Bold";
        public int[] TitleColor { get; set; }

        public bool ShowSecondaryTitles { get; set; }
        public string SecondaryTitleFontFamily { get; set; } = "Segoe UI";
        public double SecondaryTitleFontSizePt { get; set; } = 9;
        public string SecondaryTitleFontStyle { get; set; } = "Italic";
        public int[] SecondaryTitleColor { get; set; }

        public static int[] FromColor(RgbaColor c) => new int[] { c.R, c.G, c.B, c.A };

        public static RgbaColor ToColor(int[] a, RgbaColor fallback)
        {
            if (a == null || a.Length < 3) return fallback;
            byte C(int i, byte dflt) => (byte)Math.Min(Math.Max(
                i >= 0 && i <= 255 ? i : dflt, 0), 255);
            byte alpha = a.Length >= 4 ? C(a[3], 255) : (byte)255;
            return new RgbaColor(C(a[0], 0), C(a[1], 0), C(a[2], 0), alpha);
        }
    }
}
