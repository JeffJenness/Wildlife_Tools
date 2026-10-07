// Headless test of WildlifeTools.ColorHelper.ColorWalker, the Element Color
// Helper's engine: it compiles the add-in's own source file against
// ArcGIS.Core and runs it on hand-built CIM definitions of the element kinds
// the tool is for (a scale bar, a legend, a north arrow, text, graphics).
// ArcGIS.Core's CIM classes are plain data classes, so this needs no Pro.
//   dotnet run --project claude_tests/ColorWalkerTest

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.Loader;
using ArcGIS.Core.CIM;
using WildlifeTools.ColorHelper;

internal static class Program
{
    private static int _pass, _fail;

    private static void Check(string name, bool ok, string detail = "")
    {
        if (ok) _pass++;
        else { _fail++; Console.WriteLine("FAIL  " + name + "  " + detail); }
    }

    private static CIMRGBColor Rgb(double r, double g, double b, float a = 100) =>
        new CIMRGBColor { R = (float)r, G = (float)g, B = (float)b, Alpha = a };

    private static CIMPolygonSymbol Poly(CIMColor fill, CIMColor outline, double width = 1, bool lockFill = false) =>
        new CIMPolygonSymbol
        {
            SymbolLayers = new CIMSymbolLayer[]
            {
                new CIMSolidStroke { Enable = true, Color = outline, Width = width },
                new CIMSolidFill { Enable = true, Color = fill, ColorLocked = lockFill },
            },
        };

    private static CIMLineSymbol Line(CIMColor color, double width = 1) =>
        new CIMLineSymbol { SymbolLayers = new CIMSymbolLayer[] { new CIMSolidStroke { Enable = true, Color = color, Width = width } } };

    private static CIMTextSymbol Text(CIMColor fill, CIMColor halo = null, CIMColor shadow = null) =>
        new CIMTextSymbol
        {
            FontFamilyName = "Arial",
            Height = 10,
            Symbol = new CIMPolygonSymbol { SymbolLayers = new CIMSymbolLayer[] { new CIMSolidFill { Enable = true, Color = fill } } },
            HaloSymbol = halo == null ? null : new CIMPolygonSymbol { SymbolLayers = new CIMSymbolLayer[] { new CIMSolidFill { Enable = true, Color = halo } } },
            HaloSize = halo == null ? 0 : 1,
            ShadowColor = shadow,
        };

    private static CIMSymbolReference Ref(CIMSymbol s) => new CIMSymbolReference { Symbol = s };

    private static Dictionary<string, ColorUse> ScanAll(WalkOptions opt, params (string Name, CIMObject Cim)[] elements)
    {
        var d = new Dictionary<string, ColorUse>();
        foreach (var (name, cim) in elements) ColorWalker.Scan(cim, name, opt, d);
        return d;
    }

    private static ColorUse Use(Dictionary<string, ColorUse> d, ColorRole role, int r, int g, int b)
    {
        d.TryGetValue(ColorUse.KeyOf(role, (byte)r, (byte)g, (byte)b), out ColorUse u);
        return u;
    }

    private static int Main()
    {
        // ArcGIS.Core's dependencies live in Pro's bin
        AssemblyLoadContext.Default.Resolving += (ctx, name) =>
        {
            string hit = Directory.EnumerateFiles(@"C:\Program Files\ArcGIS\Pro\bin", name.Name + ".dll",
                SearchOption.AllDirectories).FirstOrDefault();
            return hit == null ? null : ctx.LoadFromAssemblyPath(hit);
        };
        try
        {
            Run();
        }
        catch (Exception ex)
        {
            Console.WriteLine("THREW: " + ex);
            return 1;
        }
        Console.WriteLine();
        Console.WriteLine("ColorWalker: " + _pass + " passed, " + _fail + " failed");
        return _fail == 0 ? 0 : 1;
    }

    private static void Run()
    {
        CIMRGBColor black = Rgb(0, 0, 0), white = Rgb(255, 255, 255), red = Rgb(200, 30, 30);
        var none = new WalkOptions();
        var all = new WalkOptions { IncludeFrames = true, IncludeHalos = true };

        // ---------------------------------------------------- color models
        Check("rgb", ColorWalker.TryToRgb(Rgb(10, 20, 30), out byte r, out byte g, out byte b) && r == 10 && g == 20 && b == 30);
        Check("gray", ColorWalker.TryToRgb(new CIMGrayColor { Level = 128 }, out r, out g, out b) && r == 128 && g == 128 && b == 128);
        Check("cmyk black", ColorWalker.TryToRgb(new CIMCMYKColor { Values = new double[] { 0, 0, 0, 100 } }, out r, out g, out b) && r == 0 && g == 0 && b == 0);
        Check("cmyk cyan", ColorWalker.TryToRgb(new CIMCMYKColor { Values = new double[] { 100, 0, 0, 0 } }, out r, out g, out b) && r == 0 && g == 255 && b == 255);
        Check("hsv red", ColorWalker.TryToRgb(new CIMHSVColor { Values = new double[] { 0, 100, 100 } }, out r, out g, out b) && r == 255 && g == 0 && b == 0);
        Check("hsv green", ColorWalker.TryToRgb(new CIMHSVColor { Values = new double[] { 120, 100, 50 } }, out r, out g, out b) && r == 0 && g == 128 && b == 0);
        Check("hsl blue", ColorWalker.TryToRgb(new CIMHSLColor { Values = new double[] { 240, 100, 50 } }, out r, out g, out b) && r == 0 && g == 0 && b == 255);
        Check("lab white", ColorWalker.TryToRgb(new CIMLABColor { Values = new double[] { 100, 0, 0 } }, out r, out g, out b) && r >= 254 && g >= 254 && b >= 254);
        Check("lab black", ColorWalker.TryToRgb(new CIMLABColor { Values = new double[] { 0, 0, 0 } }, out r, out g, out b) && r == 0 && g == 0 && b == 0);
        Check("null color", !ColorWalker.TryToRgb(null, out _, out _, out _));
        Check("names", ColorWalker.NameOf(0, 0, 0) == "Black" && ColorWalker.NameOf(255, 255, 255) == "White"
            && ColorWalker.NameOf(128, 128, 128) == "Gray 50%" && ColorWalker.NameOf(200, 30, 30) == "#C81E1E");

        // ---------------------------------------------------- a scale bar
        var scaleBar = new CIMScaleLine
        {
            Name = "Scale Bar",
            LabelSymbol = Ref(Text(black)),
            UnitLabelSymbol = Ref(Text(black)),
            DivisionMarkSymbol = Ref(Line(black)),
            SubdivisionMarkSymbol = Ref(Line(Rgb(0, 0, 0, 60))),      // partly transparent marks
            LineSymbol = Ref(Line(black, 2)),
            GraphicFrame = new CIMGraphicFrame
            {
                BorderSymbol = Ref(Line(black)),
                BackgroundSymbol = Ref(Poly(white, white)),
            },
        };
        // a double-fill scale bar: the alternating black and white fills
        var bars = new CIMDoubleFillScaleBar
        {
            Name = "Bars",
            FillSymbol1 = Ref(Poly(black, black)),
            FillSymbol2 = Ref(Poly(white, black)),
            LabelSymbol = Ref(Text(black)),
        };
        // a legend: only text symbols live in it
        var legend = new CIMLegend
        {
            Name = "Legend",
            TitleSymbol = Ref(Text(black)),
            Items = new CIMLegendItem[]
            {
                new CIMHorizontalLegendItem
                {
                    LayerNameSymbol = Ref(Text(black)),
                    HeadingSymbol = Ref(Text(black)),
                    LabelSymbol = Ref(Text(Rgb(64, 64, 64))),
                },
            },
        };
        // an Esri north arrow: a font glyph with a fill
        var northArrow = new CIMMarkerNorthArrow
        {
            Name = "North Arrow",
            PointSymbol = Ref(new CIMPointSymbol
            {
                SymbolLayers = new CIMSymbolLayer[]
                {
                    new CIMCharacterMarker { Enable = true, Size = 40, FontFamilyName = "ESRI North", CharacterIndex = 65, Symbol = Poly(black, black) },
                },
            }),
        };
        // our own north arrow: a picture, nothing to recolor
        var pictureArrow = new CIMMarkerNorthArrow
        {
            Name = "Picture Arrow",
            PointSymbol = Ref(new CIMPointSymbol
            {
                SymbolLayers = new CIMSymbolLayer[]
                {
                    new CIMPictureMarker { Enable = true, Size = 40, URL = "data:image/png;base64,AAAA", TintColor = red,
                        ColorSubstitutions = new[] { new CIMColorSubstitution { OldColor = black, NewColor = white } } },
                },
            }),
        };
        // text with a white halo and a gray shadow
        var textElement = new CIMGraphicElement
        {
            Name = "Title",
            Graphic = new CIMTextGraphic { Text = "Hello", Symbol = Ref(Text(black, white, Rgb(128, 128, 128))) },
        };
        // a drawn polygon: red fill, black outline, fill color-locked in a second one
        var polygon = new CIMGraphicElement { Name = "Polygon", Graphic = new CIMPolygonGraphic { Symbol = Ref(Poly(red, black, 2)) } };
        var lockedPolygon = new CIMGraphicElement { Name = "Locked", Graphic = new CIMPolygonGraphic { Symbol = Ref(Poly(red, black, 2, lockFill: true)) } };
        // a point graphic: a marker with a text symbol inside a line symbol would be exotic; use a vector marker
        var point = new CIMGraphicElement
        {
            Name = "Point",
            Graphic = new CIMPointGraphic
            {
                Symbol = Ref(new CIMPointSymbol
                {
                    SymbolLayers = new CIMSymbolLayer[]
                    {
                        new CIMVectorMarker
                        {
                            Enable = true, Size = 12,
                            MarkerGraphics = new[] { new CIMMarkerGraphic { Symbol = Poly(black, black) } },
                        },
                    },
                }),
            },
        };
        // a gradient fill: its colors live in a color ramp
        var gradient = new CIMGraphicElement
        {
            Name = "Gradient",
            Graphic = new CIMPolygonGraphic
            {
                Symbol = Ref(new CIMPolygonSymbol
                {
                    SymbolLayers = new CIMSymbolLayer[]
                    {
                        new CIMGradientFill { Enable = true, ColorRamp = new CIMLinearContinuousColorRamp { FromColor = black, ToColor = white } },
                    },
                }),
            },
        };
        // a disabled layer is skipped
        var disabled = new CIMGraphicElement
        {
            Name = "Disabled",
            Graphic = new CIMPolygonGraphic
            {
                Symbol = Ref(new CIMPolygonSymbol { SymbolLayers = new CIMSymbolLayer[] { new CIMSolidFill { Enable = false, Color = red } } }),
            },
        };

        // ---------------------------------------------------- scanning, default options
        var d = ScanAll(none, ("Scale Bar", scaleBar), ("Bars", bars), ("Legend", legend), ("North Arrow", northArrow),
            ("Picture Arrow", pictureArrow), ("Title", textElement), ("Polygon", polygon), ("Locked", lockedPolygon),
            ("Point", point), ("Gradient", gradient), ("Disabled", disabled));

        ColorUse blackText = Use(d, ColorRole.Text, 0, 0, 0);
        Check("black text found", blackText != null);
        Check("black text count: 2 scale bar + 1 bars + 3 legend + 1 title", blackText != null && blackText.Count == 7, blackText?.Count.ToString());
        Check("black text elements", blackText != null && blackText.Elements.SetEquals(new[] { "Scale Bar", "Bars", "Legend", "Title" }),
            blackText == null ? "" : string.Join("/", blackText.Elements));
        ColorUse grayText = Use(d, ColorRole.Text, 64, 64, 64);
        Check("gray legend labels", grayText != null && grayText.Count == 1 && grayText.Elements.Contains("Legend"));
        ColorUse blackLines = Use(d, ColorRole.Lines, 0, 0, 0);
        Check("black lines: 3 scale bar marks/line + 2 bars outlines + 1 polygon + 1 locked outline", blackLines != null && blackLines.Count == 7, blackLines?.Count.ToString());
        Check("transparent marks flagged", blackLines != null && blackLines.AnyTransparent);
        Check("black text not flagged transparent", blackText != null && !blackText.AnyTransparent);
        ColorUse blackFills = Use(d, ColorRole.Fills, 0, 0, 0);
        Check("black fills: bars fill 1 + gradient start", blackFills != null && blackFills.Count == 2, blackFills?.Count.ToString());
        ColorUse whiteFills = Use(d, ColorRole.Fills, 255, 255, 255);
        Check("white fills: bars fill 2 + gradient end", whiteFills != null && whiteFills.Count == 2, whiteFills?.Count.ToString());
        ColorUse redFills = Use(d, ColorRole.Fills, 200, 30, 30);
        Check("red fill: polygon only (locked and disabled skipped)", redFills != null && redFills.Count == 1 && redFills.Elements.SetEquals(new[] { "Polygon" }),
            redFills?.Count.ToString());
        ColorUse blackMarkers = Use(d, ColorRole.Markers, 0, 0, 0);
        Check("black markers: north arrow glyph fill+outline, point marker fill+outline", blackMarkers != null && blackMarkers.Count == 4, blackMarkers?.Count.ToString());
        Check("picture arrow contributes nothing", !d.Values.Any(u => u.Elements.Contains("Picture Arrow")));
        Check("no halos without the option", !d.Values.Any(u => u.Role == ColorRole.Halos));
        Check("no frames without the option", !d.Values.Any(u => u.Role == ColorRole.Frames));
        Check("white appears only as fills", d.Values.Where(u => u.R == 255 && u.G == 255 && u.B == 255).All(u => u.Role == ColorRole.Fills));

        // ---------------------------------------------------- scanning, everything on
        var e = ScanAll(all, ("Scale Bar", scaleBar), ("Title", textElement));
        ColorUse whiteHalo = Use(e, ColorRole.Halos, 255, 255, 255);
        Check("white halo listed", whiteHalo != null && whiteHalo.Count == 1 && whiteHalo.Elements.Contains("Title"));
        Check("gray shadow listed as halo", Use(e, ColorRole.Halos, 128, 128, 128) != null);
        ColorUse frameWhite = Use(e, ColorRole.Frames, 255, 255, 255);
        Check("frame background listed", frameWhite != null && frameWhite.Count == 2);   // fill + outline of the background symbol
        Check("frame border listed", Use(e, ColorRole.Frames, 0, 0, 0) != null);
        Check("halo does not change the text row", Use(e, ColorRole.Text, 0, 0, 0).Count == 3);

        // ---------------------------------------------------- replacing
        var map = new Dictionary<string, (byte R, byte G, byte B)>
        {
            [ColorUse.KeyOf(ColorRole.Text, 0, 0, 0)] = (255, 255, 255),
            [ColorUse.KeyOf(ColorRole.Lines, 0, 0, 0)] = (255, 255, 255),
            [ColorUse.KeyOf(ColorRole.Markers, 0, 0, 0)] = (255, 255, 0),
        };
        int n1 = ColorWalker.Replace(scaleBar, none, map);
        Check("scale bar: 2 text + 3 lines replaced", n1 == 5, n1.ToString());
        var subMark = (CIMSolidStroke)((CIMLineSymbol)scaleBar.SubdivisionMarkSymbol.Symbol).SymbolLayers[0];
        Check("transparency kept", subMark.Color is CIMRGBColor c1 && c1.R == 255 && c1.G == 255 && c1.B == 255 && Math.Abs(c1.Alpha - 60) < 0.01);
        var labelFill = (CIMSolidFill)((CIMTextSymbol)scaleBar.LabelSymbol.Symbol).Symbol.SymbolLayers[0];
        Check("label now white, opaque", labelFill.Color is CIMRGBColor c2 && c2.R == 255 && Math.Abs(c2.Alpha - 100) < 0.01);
        var border = (CIMSolidStroke)((CIMLineSymbol)scaleBar.GraphicFrame.BorderSymbol.Symbol).SymbolLayers[0];
        Check("frame border untouched without the option", border.Color is CIMRGBColor c3 && c3.R == 0);

        int n2 = ColorWalker.Replace(bars, none, map);
        Check("bars: 1 label + 2 outlines, fills untouched", n2 == 3, n2.ToString());
        var fill1 = (CIMSolidFill)((CIMPolygonSymbol)bars.FillSymbol1.Symbol).SymbolLayers[1];
        Check("bar fill 1 still black", fill1.Color is CIMRGBColor c4 && c4.R == 0);

        int n3 = ColorWalker.Replace(northArrow, none, map);
        Check("north arrow glyph: fill + outline -> yellow", n3 == 2, n3.ToString());
        var glyph = (CIMCharacterMarker)((CIMPointSymbol)northArrow.PointSymbol.Symbol).SymbolLayers[0];
        Check("glyph fill yellow", ((CIMSolidFill)glyph.Symbol.SymbolLayers[1]).Color is CIMRGBColor c5 && c5.R == 255 && c5.G == 255 && c5.B == 0);

        int n4 = ColorWalker.Replace(pictureArrow, none, map);
        Check("picture arrow: nothing replaced", n4 == 0);
        var pm = (CIMPictureMarker)((CIMPointSymbol)pictureArrow.PointSymbol.Symbol).SymbolLayers[0];
        Check("tint untouched", pm.TintColor is CIMRGBColor c6 && c6.R == 200);

        int n5 = ColorWalker.Replace(textElement, none, map);
        Check("text: fill only (halo excluded)", n5 == 1, n5.ToString());
        var ts = (CIMTextSymbol)((CIMTextGraphic)textElement.Graphic).Symbol.Symbol;
        Check("halo still white", ((CIMSolidFill)ts.HaloSymbol.SymbolLayers[0]).Color is CIMRGBColor c7 && c7.R == 255);
        var haloMap = new Dictionary<string, (byte R, byte G, byte B)> { [ColorUse.KeyOf(ColorRole.Halos, 255, 255, 255)] = (20, 20, 20) };
        int n6 = ColorWalker.Replace(textElement, all, haloMap);
        Check("halo replaced when asked", n6 == 1 && ((CIMSolidFill)ts.HaloSymbol.SymbolLayers[0]).Color is CIMRGBColor c8 && c8.R == 20);

        int n7 = ColorWalker.Replace(lockedPolygon, none, map);
        Check("locked polygon: outline replaced, locked fill kept", n7 == 1);
        var lockedFill = (CIMSolidFill)((CIMPolygonSymbol)((CIMPolygonGraphic)lockedPolygon.Graphic).Symbol.Symbol).SymbolLayers[1];
        Check("locked fill still red", lockedFill.Color is CIMRGBColor c9 && c9.R == 200);

        var rampMap = new Dictionary<string, (byte R, byte G, byte B)> { [ColorUse.KeyOf(ColorRole.Fills, 0, 0, 0)] = (10, 90, 60) };
        int n8 = ColorWalker.Replace(gradient, none, rampMap);
        var ramp = (CIMLinearContinuousColorRamp)((CIMGradientFill)((CIMPolygonSymbol)((CIMPolygonGraphic)gradient.Graphic).Symbol.Symbol).SymbolLayers[0]).ColorRamp;
        Check("gradient start replaced, end kept", n8 == 1 && ramp.FromColor is CIMRGBColor c10 && c10.G == 90 && ramp.ToColor is CIMRGBColor c11 && c11.R == 255);

        int n9 = ColorWalker.Replace(disabled, none, new Dictionary<string, (byte R, byte G, byte B)> { [ColorUse.KeyOf(ColorRole.Fills, 200, 30, 30)] = (0, 0, 0) });
        Check("disabled layer untouched", n9 == 0);

        // a color array: a fixed color ramp
        var fixedRamp = new CIMGraphicElement
        {
            Name = "Fixed",
            Graphic = new CIMPolygonGraphic
            {
                Symbol = Ref(new CIMPolygonSymbol
                {
                    SymbolLayers = new CIMSymbolLayer[]
                    {
                        new CIMGradientFill { Enable = true, ColorRamp = new CIMFixedColorRamp { Colors = new CIMColor[] { black, red, black } } },
                    },
                }),
            },
        };
        var f = ScanAll(none, ("Fixed", fixedRamp));
        Check("color array scanned", Use(f, ColorRole.Fills, 0, 0, 0)?.Count == 2);
        int n10 = ColorWalker.Replace(fixedRamp, none, rampMap);
        var fr = (CIMFixedColorRamp)((CIMGradientFill)((CIMPolygonSymbol)((CIMPolygonGraphic)fixedRamp.Graphic).Symbol.Symbol).SymbolLayers[0]).ColorRamp;
        Check("color array replaced in place", n10 == 2 && fr.Colors[0] is CIMRGBColor c12 && c12.G == 90 && fr.Colors[2] is CIMRGBColor c13 && c13.G == 90 && fr.Colors[1] is CIMRGBColor c14 && c14.R == 200);

        // a clone keeps the originals apart (what Restore relies on)
        var original = (CIMDoubleFillScaleBar)CIMObject.Clone(bars);
        ColorWalker.Replace(bars, none, new Dictionary<string, (byte R, byte G, byte B)> { [ColorUse.KeyOf(ColorRole.Fills, 0, 0, 0)] = (1, 2, 3) });
        Check("clone untouched by a later replace", ((CIMSolidFill)((CIMPolygonSymbol)original.FillSymbol1.Symbol).SymbolLayers[1]).Color is CIMRGBColor c15 && c15.R == 0);
    }
}
