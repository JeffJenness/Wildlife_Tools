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

// NOTE: this file uses ArcGIS.Core.CIM (plain data classes) and nothing else
// from ArcGIS, so it is compiled as it stands into the headless test
// claude_tests/ColorWalkerTest. Keep it free of Desktop types.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using ArcGIS.Core.CIM;

namespace WildlifeTools.ColorHelper
{
    /// <summary>What a color is doing where it was found. One row of the
    /// Element Color Helper's table is one color in one role.</summary>
    public enum ColorRole
    {
        Text = 0,
        Lines = 1,
        Fills = 2,
        Markers = 3,
        Halos = 4,
        Frames = 5,
    }

    public sealed class WalkOptions
    {
        /// <summary>Also visit each element's frame: its border, background
        /// and shadow symbols.</summary>
        public bool IncludeFrames { get; set; }

        /// <summary>Also visit text halos, text shadows and callout
        /// backgrounds.</summary>
        public bool IncludeHalos { get; set; }
    }

    /// <summary>One distinct color in one role, across every element scanned.</summary>
    public sealed class ColorUse
    {
        public ColorRole Role { get; set; }
        public byte R { get; set; }
        public byte G { get; set; }
        public byte B { get; set; }

        /// <summary>How many colors in the elements this row stands for.</summary>
        public int Count { get; set; }

        /// <summary>True when at least one of those colors is partly transparent.</summary>
        public bool AnyTransparent { get; set; }

        /// <summary>Names of the elements the color was found in.</summary>
        public SortedSet<string> Elements { get; } =
            new SortedSet<string>(StringComparer.CurrentCultureIgnoreCase);

        public string Key => KeyOf(Role, R, G, B);

        public static string KeyOf(ColorRole role, byte r, byte g, byte b) =>
            ((int)role).ToString(CultureInfo.InvariantCulture) + "|"
            + r.ToString(CultureInfo.InvariantCulture) + ","
            + g.ToString(CultureInfo.InvariantCulture) + ","
            + b.ToString(CultureInfo.InvariantCulture);

        /// <summary>"Black", "White", "Gray 50%" or "#RRGGBB".</summary>
        public string ColorName => ColorWalker.NameOf(R, G, B);

        public string RoleName => ColorWalker.RoleName(Role);
    }

    /// <summary>
    /// Walks the CIM definition of a layout or map element and finds every
    /// color in it, or replaces the ones asked for. It walks the object graph
    /// by reflection (every CIM class is a tree of CIMObject properties and
    /// arrays), so it needs no knowledge of legends, scale bars, north arrows
    /// or graphics in particular: a color is a color wherever it sits.
    ///
    /// What it leaves alone, on purpose:
    /// - Pictures (picture markers, picture fills and strokes, picture
    ///   graphics): the image is what it is. A picture marker's tint color
    ///   and color substitutions are skipped with it.
    /// - Symbol layers that are color-locked (Pro's own flag for "keep this
    ///   color when the symbol is recolored") or disabled.
    /// - Element frames and text halos, shadows and callouts, unless asked
    ///   for: a white halo on white text would swallow the text.
    /// - A legend's patches are never reached: a legend's definition holds
    ///   only its text symbols; the patches come from the map's layers.
    /// - Transparency: a replaced color keeps the old color's alpha.
    /// </summary>
    public static class ColorWalker
    {
        private delegate void ColorHit(ColorRole role, CIMColor color, Action<CIMColor> replace);

        /// <summary>Adds every color found under <paramref name="root"/> to
        /// <paramref name="into"/>, keyed by role and RGB.</summary>
        public static void Scan(CIMObject root, string elementName, WalkOptions options,
            IDictionary<string, ColorUse> into)
        {
            options ??= new WalkOptions();
            Walk(root, null, options, 0, (role, color, _) =>
            {
                if (!TryToRgb(color, out byte r, out byte g, out byte b))
                {
                    return;
                }
                string key = ColorUse.KeyOf(role, r, g, b);
                if (!into.TryGetValue(key, out ColorUse use))
                {
                    use = new ColorUse { Role = role, R = r, G = g, B = b };
                    into[key] = use;
                }
                use.Count++;
                if (color.Alpha < 99.5f)
                {
                    use.AnyTransparent = true;
                }
                if (!string.IsNullOrEmpty(elementName))
                {
                    use.Elements.Add(elementName);
                }
            });
        }

        /// <summary>Replaces, in place, every color whose role-and-RGB key is
        /// in <paramref name="newColors"/>, keeping each color's own alpha.
        /// Returns how many colors were replaced.</summary>
        public static int Replace(CIMObject root, WalkOptions options,
            IReadOnlyDictionary<string, (byte R, byte G, byte B)> newColors)
        {
            options ??= new WalkOptions();
            int n = 0;
            Walk(root, null, options, 0, (role, color, replace) =>
            {
                if (!TryToRgb(color, out byte r, out byte g, out byte b)
                    || !newColors.TryGetValue(ColorUse.KeyOf(role, r, g, b), out var rgb))
                {
                    return;
                }
                replace(new CIMRGBColor { R = rgb.R, G = rgb.G, B = rgb.B, Alpha = color.Alpha });
                n++;
            });
            return n;
        }

        // ------------------------------------------------------------------
        //  The walk
        // ------------------------------------------------------------------

        private const int MaxDepth = 64;

        private static readonly string[] HaloProperties = { "HaloSymbol", "Callout", "ShadowColor" };

        private static void Walk(CIMObject node, ColorRole? context, WalkOptions options, int depth, ColorHit hit)
        {
            if (node == null || depth > MaxDepth)
            {
                return;
            }
            // Pictures are never recolored; nor are locked or switched-off layers.
            if (node is CIMPictureMarker || node is CIMPictureFill || node is CIMPictureStroke
                || node is CIMPictureGraphic)
            {
                return;
            }
            if (node is CIMSymbolLayer layer && (layer.ColorLocked || !layer.Enable))
            {
                return;
            }

            ColorRole? here = context;
            if (node is CIMGraphicFrame)
            {
                if (!options.IncludeFrames)
                {
                    return;
                }
                here = ColorRole.Frames;
            }
            else if (context == null)
            {
                // Text and markers claim everything inside them; a stroke or a
                // fill is itself the role of its colors.
                if (node is CIMTextSymbol) here = ColorRole.Text;
                else if (node is CIMMarker) here = ColorRole.Markers;
                else if (node is CIMStroke) here = ColorRole.Lines;
                else if (node is CIMFill) here = ColorRole.Fills;
            }
            else if (context == ColorRole.Lines || context == ColorRole.Fills)
            {
                // a marker placed along a line, or a text symbol inside a
                // marker, takes its own role
                if (node is CIMTextSymbol) here = ColorRole.Text;
                else if (node is CIMMarker) here = ColorRole.Markers;
            }

            bool isText = node is CIMTextSymbol;
            foreach (Slot slot in SlotsOf(node.GetType()))
            {
                ColorRole? role = here;
                if (isText && Array.IndexOf(HaloProperties, slot.Property.Name) >= 0)
                {
                    if (!options.IncludeHalos)
                    {
                        continue;
                    }
                    role = ColorRole.Halos;
                }

                object value;
                try
                {
                    value = slot.Property.GetValue(node);
                }
                catch
                {
                    continue;
                }
                if (value == null)
                {
                    continue;
                }

                switch (slot.Kind)
                {
                    case SlotKind.Color:
                        {
                            PropertyInfo prop = slot.Property;
                            CIMObject owner = node;
                            hit(role ?? ColorRole.Fills, (CIMColor)value, c => prop.SetValue(owner, c));
                            break;
                        }
                    case SlotKind.ColorArray:
                        {
                            var colors = (CIMColor[])value;
                            for (int i = 0; i < colors.Length; i++)
                            {
                                if (colors[i] == null)
                                {
                                    continue;
                                }
                                int index = i;
                                hit(role ?? ColorRole.Fills, colors[i], c => colors[index] = c);
                            }
                            break;
                        }
                    case SlotKind.Object:
                        Walk((CIMObject)value, role, options, depth + 1, hit);
                        break;
                    case SlotKind.ObjectArray:
                        foreach (object item in (Array)value)
                        {
                            if (item is CIMObject child)
                            {
                                Walk(child, role, options, depth + 1, hit);
                            }
                        }
                        break;
                }
            }
        }

        private enum SlotKind
        {
            Color,
            ColorArray,
            Object,
            ObjectArray,
        }

        private sealed class Slot
        {
            public PropertyInfo Property;
            public SlotKind Kind;
        }

        private static readonly Dictionary<Type, Slot[]> SlotCache = new Dictionary<Type, Slot[]>();

        /// <summary>The properties of a CIM class worth visiting: colors,
        /// arrays of colors, CIM objects and arrays of CIM objects.</summary>
        private static Slot[] SlotsOf(Type type)
        {
            lock (SlotCache)
            {
                if (SlotCache.TryGetValue(type, out Slot[] cached))
                {
                    return cached;
                }
            }
            var slots = new List<Slot>();
            foreach (PropertyInfo p in type.GetProperties(BindingFlags.Instance | BindingFlags.Public))
            {
                if (p.GetIndexParameters().Length != 0 || !p.CanRead)
                {
                    continue;
                }
                Type t = p.PropertyType;
                if (typeof(CIMColor).IsAssignableFrom(t))
                {
                    if (p.CanWrite)
                    {
                        slots.Add(new Slot { Property = p, Kind = SlotKind.Color });
                    }
                }
                else if (t.IsArray)
                {
                    Type e = t.GetElementType();
                    if (e == null)
                    {
                        continue;
                    }
                    if (typeof(CIMColor).IsAssignableFrom(e))
                    {
                        slots.Add(new Slot { Property = p, Kind = SlotKind.ColorArray });
                    }
                    else if (typeof(CIMObject).IsAssignableFrom(e))
                    {
                        slots.Add(new Slot { Property = p, Kind = SlotKind.ObjectArray });
                    }
                }
                else if (typeof(CIMObject).IsAssignableFrom(t))
                {
                    slots.Add(new Slot { Property = p, Kind = SlotKind.Object });
                }
            }
            Slot[] result = slots.ToArray();
            lock (SlotCache)
            {
                SlotCache[type] = result;
            }
            return result;
        }

        // ------------------------------------------------------------------
        //  Colors
        // ------------------------------------------------------------------

        /// <summary>The color as 8-bit RGB, whatever model it is stored in
        /// (RGB, gray, HSV, HSL, CMYK, Lab). False for a model this cannot
        /// read.</summary>
        public static bool TryToRgb(CIMColor color, out byte r, out byte g, out byte b)
        {
            r = g = b = 0;
            if (color == null)
            {
                return false;
            }
            double[] v = color.Values;
            try
            {
                switch (color)
                {
                    case CIMRGBColor rgb:
                        return Set(rgb.R, rgb.G, rgb.B, out r, out g, out b);
                    case CIMGrayColor gray:
                        return Set(gray.Level, gray.Level, gray.Level, out r, out g, out b);
                    case CIMHSVColor _:
                        if (v == null || v.Length < 3) return false;
                        HsvToRgb(v[0], v[1] / 100.0, v[2] / 100.0, out double hr, out double hg, out double hb);
                        return Set(hr * 255.0, hg * 255.0, hb * 255.0, out r, out g, out b);
                    case CIMHSLColor _:
                        if (v == null || v.Length < 3) return false;
                        HslToRgb(v[0], v[1] / 100.0, v[2] / 100.0, out double lr, out double lg, out double lb);
                        return Set(lr * 255.0, lg * 255.0, lb * 255.0, out r, out g, out b);
                    case CIMCMYKColor _:
                        if (v == null || v.Length < 4) return false;
                        return Set(255.0 * (1 - v[0] / 100.0) * (1 - v[3] / 100.0),
                                   255.0 * (1 - v[1] / 100.0) * (1 - v[3] / 100.0),
                                   255.0 * (1 - v[2] / 100.0) * (1 - v[3] / 100.0), out r, out g, out b);
                    case CIMLABColor _:
                        if (v == null || v.Length < 3) return false;
                        LabToRgb(v[0], v[1], v[2], out double xr, out double xg, out double xb);
                        return Set(xr * 255.0, xg * 255.0, xb * 255.0, out r, out g, out b);
                    default:
                        return false;
                }
            }
            catch
            {
                return false;
            }
        }

        private static bool Set(double rr, double gg, double bb, out byte r, out byte g, out byte b)
        {
            r = Clamp(rr);
            g = Clamp(gg);
            b = Clamp(bb);
            return true;
        }

        private static byte Clamp(double v)
        {
            if (double.IsNaN(v)) return 0;
            return (byte)Math.Max(0, Math.Min(255, Math.Round(v)));
        }

        private static void HsvToRgb(double h, double s, double v, out double r, out double g, out double b)
        {
            h = ((h % 360) + 360) % 360;
            double c = v * s, x = c * (1 - Math.Abs(h / 60.0 % 2 - 1)), m = v - c;
            (double r1, double g1, double b1) =
                h < 60 ? (c, x, 0.0) : h < 120 ? (x, c, 0.0) : h < 180 ? (0.0, c, x)
                : h < 240 ? (0.0, x, c) : h < 300 ? (x, 0.0, c) : (c, 0.0, x);
            r = r1 + m;
            g = g1 + m;
            b = b1 + m;
        }

        private static void HslToRgb(double h, double s, double l, out double r, out double g, out double b)
        {
            h = ((h % 360) + 360) % 360;
            double c = (1 - Math.Abs(2 * l - 1)) * s, x = c * (1 - Math.Abs(h / 60.0 % 2 - 1)), m = l - c / 2;
            (double r1, double g1, double b1) =
                h < 60 ? (c, x, 0.0) : h < 120 ? (x, c, 0.0) : h < 180 ? (0.0, c, x)
                : h < 240 ? (0.0, x, c) : h < 300 ? (x, 0.0, c) : (c, 0.0, x);
            r = r1 + m;
            g = g1 + m;
            b = b1 + m;
        }

        /// <summary>CIE Lab (D65) to sRGB, 0..1 per channel.</summary>
        private static void LabToRgb(double l, double a, double bb, out double r, out double g, out double b)
        {
            double fy = (l + 16) / 116.0, fx = a / 500.0 + fy, fz = fy - bb / 200.0;
            double Finv(double t) => t * t * t > 0.008856 ? t * t * t : (t - 16.0 / 116.0) / 7.787;
            double x = 0.95047 * Finv(fx), y = 1.0 * Finv(fy), z = 1.08883 * Finv(fz);
            double rl = 3.2406 * x - 1.5372 * y - 0.4986 * z;
            double gl = -0.9689 * x + 1.8758 * y + 0.0415 * z;
            double bl = 0.0557 * x - 0.2040 * y + 1.0570 * z;
            double Gamma(double c) => c <= 0.0031308 ? 12.92 * c : 1.055 * Math.Pow(Math.Max(c, 0), 1 / 2.4) - 0.055;
            r = Gamma(rl);
            g = Gamma(gl);
            b = Gamma(bl);
        }

        /// <summary>"Black", "White", "Gray 50%" for the grays, else "#RRGGBB".</summary>
        public static string NameOf(byte r, byte g, byte b)
        {
            if (r == g && g == b)
            {
                if (r == 0) return "Black";
                if (r == 255) return "White";
                return "Gray " + Math.Round(100.0 * (255 - r) / 255.0).ToString(CultureInfo.InvariantCulture) + "%";
            }
            return "#" + r.ToString("X2", CultureInfo.InvariantCulture)
                + g.ToString("X2", CultureInfo.InvariantCulture)
                + b.ToString("X2", CultureInfo.InvariantCulture);
        }

        public static string RoleName(ColorRole role) => role switch
        {
            ColorRole.Text => "Text",
            ColorRole.Lines => "Lines",
            ColorRole.Fills => "Fills",
            ColorRole.Markers => "Markers",
            ColorRole.Halos => "Halos, shadows, callouts",
            ColorRole.Frames => "Element frames",
            _ => role.ToString(),
        };
    }
}
