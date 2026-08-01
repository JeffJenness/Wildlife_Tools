using System;

namespace WildlifeTools.PolarPlots
{
    internal enum PolarPlotType
    {
        PeaksAndValleys = 1,   // filled radial silhouette
        CircularBins = 2,      // rose diagram (wedge bars)
        ShadeByDensity = 3,    // wedges shaded low->high by bin value
        // Phase 2:
        ExternalPeaksAndValleys = 4,
        ExternalBins = 5,
        Bearings = 6,
    }

    internal enum PolarWeightOption { None = 1, ByLength = 2, ByAttribute = 3 }

    internal enum PolarAzimuthOption { ByPolyline = 1, ByAttribute = 2 }

    /// <summary>
    /// All user-adjustable settings for a polar plot. Plain data (no ArcGIS
    /// types) so the renderer/SVG stay portable; colors are simple RGBA. The
    /// window binds to this and re-renders on any change.
    /// </summary>
    internal sealed class PolarPlotSettings
    {
        // Plot style + geometry (inches; 1 inch = 96 device-independent units).
        public PolarPlotType PlotType = PolarPlotType.CircularBins;
        public int BinCount = 72;                 // 72 bins = 5 degrees each
        public double PlotDiameterIn = 4.0;       // data circle diameter
        public double InnerDiameterIn = 1.5;      // external plots only
        public double OuterDiameterIn = 4.0;      // external plots only

        // Bin symbology.
        public RgbaColor BinFill = new RgbaColor(150, 170, 210, 255);
        public RgbaColor BinOutline = new RgbaColor(40, 70, 120, 255);
        public double BinOutlineWidthPt = 0.5;
        public RgbaColor LowDensity = new RgbaColor(230, 240, 255, 255);
        public RgbaColor HighDensity = new RgbaColor(30, 70, 140, 255);

        // Reference lines.
        public bool ShowPrimaryRefLines = true;   // N/E/S/W rays + outer circle
        public RgbaColor PrimaryRefColor = new RgbaColor(90, 90, 90, 255);
        public double PrimaryRefWidthPt = 1.0;
        public bool ShowSecondaryRefRadius = true; // secondary azimuth rays
        public int SecondaryRefBearings = 12;      // >=3; 12 -> every 30 degrees
        public bool ShowSecondaryRefCircles = true;
        public int SecondaryRefCircles = 4;        // rings between 0 and max
        public RgbaColor SecondaryRefColor = new RgbaColor(160, 160, 160, 255);
        public double SecondaryRefWidthPt = 0.5;
        public PolarDash SecondaryRefDash = PolarDash.Dash;

        // Labels.
        public bool ShowAzimuthLabels = true;      // degree labels around the rim
        public bool AzimuthLabelsRotated = false;  // perpendicular to the bearing
        public bool ShowInnerLabels = true;        // ring values on the axes
        public RgbaColor LabelHalo = new RgbaColor(255, 255, 255, 220);
        public double LabelHaloWidthPt = 1.2;
        public bool InnerLabelNorth = true, InnerLabelEast = false,
                    InnerLabelSouth = true, InnerLabelWest = false;
        public string LabelFontFamily = "Segoe UI";
        public double LabelFontSizePt = 9.0;
        public bool LabelBold = false;
        public bool LabelItalic = false;
        public RgbaColor LabelColor = new RgbaColor(60, 60, 60, 255);

        // Titles.
        public bool ShowPrimaryTitle = false;
        public string PrimaryTitle = "";
        public string TitleFontFamily = "Segoe UI";
        public double TitleFontSizePt = 16.0;
        public bool TitleBold = true;
        public bool TitleItalic = false;
        public RgbaColor TitleColor = new RgbaColor(0, 0, 0, 255);
        public bool ShowSecondaryTitles = false;   // source/count/weight/max lines
        public string SecondaryTitleFontFamily = "Segoe UI";
        public double SecondaryTitleFontSizePt = 9.0;
        public bool SecondaryTitleBold = false;
        public bool SecondaryTitleItalic = true;
        public RgbaColor SecondaryTitleColor = new RgbaColor(80, 80, 80, 255);

        // Data source (set from the window; used for headers/labels only here).
        public string DatasetName = "";
        public PolarAzimuthOption AzimuthOption = PolarAzimuthOption.ByAttribute;
        public string AzimuthField = "";
        public PolarWeightOption WeightOption = PolarWeightOption.None;
        public string WeightField = "";
        public bool UseSelectedOnly = false;
        public bool AddReverse = false;
        public bool ExcludeNegative = true;
        public bool ExcludeMinusOneOnly = false;

        public bool IsExternal =>
            PlotType == PolarPlotType.ExternalPeaksAndValleys ||
            PlotType == PolarPlotType.ExternalBins;

        public PolarPlotSettings Clone() => (PolarPlotSettings)MemberwiseClone();
    }

    internal enum PolarDash { Solid, Dot, Dash, DashDot }

    /// <summary>Simple RGBA color (0-255) independent of WPF/ArcGIS.</summary>
    internal readonly struct RgbaColor
    {
        public readonly byte R, G, B, A;
        public RgbaColor(byte r, byte g, byte b, byte a) { R = r; G = g; B = b; A = a; }
        public bool IsTransparent => A == 0;

        public string ToSvg() => $"#{R:X2}{G:X2}{B:X2}";
        public double SvgOpacity => A / 255.0;

        /// <summary>Linear blend from this color to <paramref name="other"/> at t in 0..1.</summary>
        public RgbaColor Lerp(RgbaColor other, double t)
        {
            if (t < 0) t = 0; if (t > 1) t = 1;
            return new RgbaColor(
                (byte)Math.Round(R + (other.R - R) * t),
                (byte)Math.Round(G + (other.G - G) * t),
                (byte)Math.Round(B + (other.B - B) * t),
                (byte)Math.Round(A + (other.A - A) * t));
        }
    }
}
