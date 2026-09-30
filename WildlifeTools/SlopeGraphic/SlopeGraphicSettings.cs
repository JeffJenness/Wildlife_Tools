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
using WildlifeTools.PolarPlots;

namespace WildlifeTools.SlopeGraphic
{
    /// <summary>
    /// User-adjustable appearance settings for the Slope Graphic. Radii follow
    /// the original ArcView tool's proportions (axes and mean line the same
    /// length R; range wedge 0.70R; SD wedge 0.875R) with the new elements
    /// slotted between and outside them. Colors reuse the shared RgbaColor.
    /// </summary>
    internal sealed class SlopeGraphicSettings
    {
        public double SizeIn = 3.0;                // mean-line length R, inches
        public SlopeUnit Unit = SlopeUnit.Degrees;

        public bool ShowMeanLine = true;
        public RgbaColor MeanColor = new RgbaColor(0, 0, 0, 255);
        public double MeanWidthPt = 2.0;

        public bool ShowSdWedge = true;            // mean +/- 1 SD
        public RgbaColor SdFill = new RgbaColor(0, 255, 255, 255);      // cyan
        public RgbaColor SdOutline = new RgbaColor(0, 90, 90, 255);

        public bool ShowRangeWedge = true;         // min..max
        public RgbaColor RangeFill = new RgbaColor(255, 200, 200, 255); // pink
        public RgbaColor RangeOutline = new RgbaColor(150, 70, 70, 255);

        public bool ShowPercentileWedge = false;   // robust alternative to range
        public double PercentileLo = 5.0;
        public double PercentileHi = 95.0;
        public RgbaColor PercentileFill = new RgbaColor(255, 235, 170, 255);
        public RgbaColor PercentileOutline = new RgbaColor(160, 120, 40, 255);

        public bool ShowMedianLine = true;         // dashed
        public RgbaColor MedianColor = new RgbaColor(80, 80, 80, 255);

        public bool ShowCiArc = true;              // 95% CI of the mean
        public RgbaColor CiColor = new RgbaColor(200, 30, 30, 255);

        public bool ShowProtractor = true;         // scale arc + ticks
        public bool ShowProtractorLabels = true;   // degree labels on the arc
        public RgbaColor ProtractorColor = new RgbaColor(120, 120, 120, 255);

        public bool ShowBoundaryLabels = true;     // values at mean/wedge edges
        public bool ShowLegend = true;             // element key, upper right
        public string LabelFontFamily = "Segoe UI";
        public double LabelFontSizePt = 9.0;
        public bool LabelBold = false;
        public bool LabelItalic = false;
        public RgbaColor LabelColor = new RgbaColor(60, 60, 60, 255);
        public RgbaColor LabelHalo = new RgbaColor(255, 255, 255, 220);
        public double LabelHaloWidthPt = 1.2;

        public RgbaColor AxisColor = new RgbaColor(0, 0, 0, 255);
        public double AxisWidthPt = 2.0;

        public bool ShowPrimaryTitle = false;
        public string PrimaryTitle = "";
        public string TitleFontFamily = "Segoe UI";
        public double TitleFontSizePt = 16.0;
        public bool TitleBold = true;
        public bool TitleItalic = false;
        public RgbaColor TitleColor = new RgbaColor(0, 0, 0, 255);
        public bool ShowSecondaryTitles = false;
        public string SecondaryTitleFontFamily = "Segoe UI";
        public double SecondaryTitleFontSizePt = 9.0;
        public bool SecondaryTitleBold = false;
        public bool SecondaryTitleItalic = true;
        public RgbaColor SecondaryTitleColor = new RgbaColor(80, 80, 80, 255);

        public string DatasetName = "";            // provenance for titles
    }

    /// <summary>JSON style presets in the per-user store, mirroring the Polar
    /// Plots preset pattern (colors as [r,g,b,a]; format-validated on load).</summary>
    internal static class SlopeStyleStore
    {
        public static string StoreDir()
        {
            string b = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (string.IsNullOrEmpty(b))
                b = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            string dir = Path.Combine(b, "JennessEnterprises", "WildlifeTools",
                                      "slope_graphic_styles");
            Directory.CreateDirectory(dir);
            return dir;
        }

        public static void Save(string path, SlopeStyleDto dto) =>
            File.WriteAllText(path, JsonSerializer.Serialize(dto,
                new JsonSerializerOptions { WriteIndented = true }));

        public static SlopeStyleDto Load(string path) =>
            JsonSerializer.Deserialize<SlopeStyleDto>(File.ReadAllText(path),
                new JsonSerializerOptions
                { ReadCommentHandling = JsonCommentHandling.Skip, AllowTrailingCommas = true });
    }

    internal sealed class SlopeStyleDto
    {
        [JsonPropertyName("format")] public string Format { get; set; } = "JennessSlopeGraphicStyle";
        [JsonPropertyName("version")] public int Version { get; set; } = 1;

        public double SizeIn { get; set; } = 3.0;      // inches, like polar
        public string Units { get; set; } = "in";      // in/cm DISPLAY unit

        public bool ShowMeanLine { get; set; } = true;
        public int[] MeanColor { get; set; }
        public double MeanWidthPt { get; set; } = 2.0;
        public bool ShowSdWedge { get; set; } = true;
        public int[] SdFill { get; set; }
        public int[] SdOutline { get; set; }
        public bool ShowRangeWedge { get; set; } = true;
        public int[] RangeFill { get; set; }
        public int[] RangeOutline { get; set; }
        public bool ShowPercentileWedge { get; set; }
        public double PercentileLo { get; set; } = 5.0;
        public double PercentileHi { get; set; } = 95.0;
        public int[] PercentileFill { get; set; }
        public int[] PercentileOutline { get; set; }
        public bool ShowMedianLine { get; set; } = true;
        public int[] MedianColor { get; set; }
        public bool ShowCiArc { get; set; } = true;
        public int[] CiColor { get; set; }
        public bool ShowProtractor { get; set; } = true;
        public bool ShowProtractorLabels { get; set; } = true;
        public int[] ProtractorColor { get; set; }
        public bool ShowBoundaryLabels { get; set; } = true;
        public bool ShowLegend { get; set; } = true;
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
            byte C(int i) => (byte)Math.Min(Math.Max(i, 0), 255);
            byte alpha = a.Length >= 4 ? C(a[3]) : (byte)255;
            return new RgbaColor(C(a[0]), C(a[1]), C(a[2]), alpha);
        }
    }
}
