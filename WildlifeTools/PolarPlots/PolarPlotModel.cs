using System;
using System.Collections.Generic;

namespace WildlifeTools.PolarPlots
{
    // ------------------------------------------------------------------
    //  Resolution-independent plot geometry: a list of primitives in a
    //  plot coordinate space measured in INCHES, origin at the plot
    //  CENTER, +x right, +y UP (math convention). Each renderer (WPF,
    //  SVG, CIM) maps this one model to its own device space, so the
    //  live preview, the exported SVG and the map/layout graphic are
    //  guaranteed identical. Compass azimuth: 0 = north (+y), increasing
    //  clockwise; a point at (radius, azimuth) is at
    //  x = r*sin(az), y = r*cos(az).
    // ------------------------------------------------------------------

    internal enum PolarElementKind { Polygon, Polyline, Circle, Line, Text }
    internal enum TextAnchor { Start, Middle, End }

    internal sealed class PolarElement
    {
        public PolarElementKind Kind;
        public List<PtIn> Points;          // Polygon / Polyline / Line (2 pts)
        public PtIn Center;                // Circle / Text position
        public double Radius;              // Circle
        public RgbaColor Fill = new RgbaColor(0, 0, 0, 0);
        public RgbaColor Stroke = new RgbaColor(0, 0, 0, 0);
        public double StrokeWidthPt;       // points (1/72 in)
        public PolarDash Dash = PolarDash.Solid;
        // Text:
        public string Text;
        public string FontFamily;
        public double FontSizePt;
        public bool Bold;
        public bool Italic;
        public TextAnchor Anchor = TextAnchor.Middle;
        public double RotationDeg;         // CW positive, about Center
        public RgbaColor Halo = new RgbaColor(0, 0, 0, 0);  // text halo (readability)
        public double HaloWidthPt;
    }

    internal readonly struct PtIn
    {
        public readonly double X, Y;       // inches, +y up, origin = center
        public PtIn(double x, double y) { X = x; Y = y; }
    }

    /// <summary>
    /// The assembled plot: ordered primitives plus the overall bounds (inches,
    /// center-origin) so renderers can size their canvas. Built by
    /// <see cref="PolarPlotBuilder"/> from settings + binned data.
    /// </summary>
    internal sealed class PolarPlotModel
    {
        public readonly List<PolarElement> Elements = new List<PolarElement>();
        public double HalfWidthIn = 3.0;   // canvas extends +/- this in x
        public double HalfHeightIn = 3.0;  // and +/- this in y

        public static PtIn Polar(double radiusIn, double azimuthDeg)
        {
            double a = azimuthDeg * Math.PI / 180.0;
            return new PtIn(radiusIn * Math.Sin(a), radiusIn * Math.Cos(a));
        }
    }
}
