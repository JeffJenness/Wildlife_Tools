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
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using ArcGIS.Core.CIM;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;

namespace WildlifeTools.CustomGraphics
{
    /// <summary>
    /// Draws a registered VECTOR image (SVG, EMF) to a PNG preview with ArcGIS
    /// Pro's own symbol drawing. WPF cannot draw either format, and Pro is
    /// what will draw the image on the map anyway, so the preview shows it
    /// exactly as Pro sees it.
    ///
    /// The preview must be the image's WHOLE CANVAS, margins included: the
    /// Manage window places the focal-point crosshair as a fraction of the
    /// canvas, and that is also the box of the element placed on the map. A
    /// symbol preview does not say where in the patch the marker landed, so
    /// the canvas is found by drawing twice at the same size: first a solid
    /// rectangle of the canvas's shape in a key color, whose pixel bounds are
    /// the canvas; then the image itself, cropped to those bounds.
    /// </summary>
    internal static class GraphicPreviewRenderer
    {
        /// <summary>Side of the square symbol patch, in pixels.</summary>
        private const int Patch = 440;

        /// <summary>Draws the image at <paramref name="imagePath"/> (canvas
        /// width / height = <paramref name="aspect"/>) into
        /// <paramref name="pngPath"/>. False, with nothing written, if Pro
        /// could not draw it. Any thread; the work runs on the MCT.</summary>
        public static Task<bool> RenderAsync(string imagePath, double aspect, string pngPath)
        {
            return QueuedTask.Run(() =>
            {
                try
                {
                    return Render(imagePath, aspect, pngPath);
                }
                catch
                {
                    return false;
                }
            });
        }

        private static bool Render(string imagePath, double aspect, string pngPath)
        {
            if (!(aspect > 0.0) || double.IsInfinity(aspect))
            {
                aspect = 1.0;
            }

            // Largest first. If the key rectangle reaches the edge of the patch
            // (a preview drawn larger than its nominal size), try smaller.
            foreach (double share in new[] { 0.74, 0.50, 0.30 })
            {
                double longPixels = Patch * share;
                double heightPixels = aspect >= 1.0 ? longPixels / aspect : longPixels;
                double sizePoints = heightPixels * 72.0 / 96.0;

                BitmapSource key = Draw(CanvasMarker(sizePoints, aspect));
                if (key == null || !TryKeyBounds(key, out Int32Rect canvas, out bool atEdge))
                {
                    break;      // no key rectangle to measure: fall back below
                }
                if (atEdge)
                {
                    continue;
                }

                BitmapSource picture = Draw(CompassRose.CreateMarker(imagePath, sizePoints));
                if (picture == null || picture.PixelWidth != key.PixelWidth
                    || picture.PixelHeight != key.PixelHeight)
                {
                    break;
                }
                var cropped = new CroppedBitmap(picture, canvas);
                cropped.Freeze();
                GraphicImage.SavePng(cropped, pngPath);
                return true;
            }

            // Fallback: the image alone, cropped to whatever is not background.
            // The thumbnail is then right; the canvas margins are a guess.
            double fallbackPoints = Patch * 0.5 * 72.0 / 96.0 / Math.Max(1.0, aspect);
            BitmapSource alone = Draw(CompassRose.CreateMarker(imagePath, fallbackPoints));
            if (alone != null && TryContentBounds(alone, out Int32Rect content))
            {
                var cropped = new CroppedBitmap(alone, content);
                cropped.Freeze();
                GraphicImage.SavePng(cropped, pngPath);
                return true;
            }
            return false;
        }

        /// <summary>Pro's preview of a point symbol made of one marker layer.</summary>
        private static BitmapSource Draw(CIMMarker marker)
        {
            var symbol = new CIMPointSymbol { SymbolLayers = new CIMSymbolLayer[] { marker } };
            var item = new SymbolStyleItem
            {
                Symbol = symbol,
                PatchWidth = Patch,
                PatchHeight = Patch,
            };
            return item.PreviewImage as BitmapSource;
        }

        /// <summary>A solid magenta rectangle of the canvas's shape, as tall
        /// as the picture marker will be drawn.</summary>
        private static CIMMarker CanvasMarker(double sizePoints, double aspect)
        {
            double h = 100.0, w = 100.0 * aspect;
            Envelope frame = EnvelopeBuilderEx.CreateEnvelope(-w / 2.0, -h / 2.0, w / 2.0, h / 2.0);
            Polygon box = PolygonBuilderEx.CreatePolygon(frame);
            var fill = new CIMPolygonSymbol
            {
                SymbolLayers = new CIMSymbolLayer[]
                {
                    new CIMSolidFill
                    {
                        Enable = true,
                        Color = new CIMRGBColor { R = 255, G = 0, B = 255, Alpha = 100 },
                    },
                },
            };
            return new CIMVectorMarker
            {
                Enable = true,
                Size = sizePoints,
                ColorLocked = true,
                ScaleSymbolsProportionally = true,
                RespectFrame = true,
                Frame = frame,
                MarkerGraphics = new[] { new CIMMarkerGraphic { Geometry = box, Symbol = fill } },
            };
        }

        private static byte[] Pixels(BitmapSource source, out int width, out int height)
        {
            BitmapSource bgra = source.Format == PixelFormats.Bgra32
                ? source
                : new FormatConvertedBitmap(source, PixelFormats.Bgra32, null, 0);
            width = bgra.PixelWidth;
            height = bgra.PixelHeight;
            var px = new byte[width * height * 4];
            bgra.CopyPixels(px, width * 4, 0);
            return px;
        }

        /// <summary>Bounds of the key-colored pixels; <paramref name="atEdge"/>
        /// when they reach the patch's edge (the rectangle was clipped).</summary>
        private static bool TryKeyBounds(BitmapSource source, out Int32Rect bounds, out bool atEdge)
        {
            byte[] px = Pixels(source, out int w, out int h);
            return TryBounds(px, w, h, out bounds, out atEdge,
                (b, g, r, a) => a >= 128 && r >= 190 && b >= 190 && g <= 100);
        }

        /// <summary>Bounds of the pixels that differ from the corner pixel
        /// (taken as the background).</summary>
        private static bool TryContentBounds(BitmapSource source, out Int32Rect bounds)
        {
            byte[] px = Pixels(source, out int w, out int h);
            byte b0 = px[0], g0 = px[1], r0 = px[2], a0 = px[3];
            return TryBounds(px, w, h, out bounds, out _,
                (b, g, r, a) => Math.Abs(a - a0) > 24
                    || (a > 24 && (Math.Abs(b - b0) > 24 || Math.Abs(g - g0) > 24 || Math.Abs(r - r0) > 24)));
        }

        private static bool TryBounds(byte[] px, int w, int h, out Int32Rect bounds, out bool atEdge,
            Func<byte, byte, byte, byte, bool> wanted)
        {
            int minX = int.MaxValue, minY = int.MaxValue, maxX = -1, maxY = -1;
            for (int y = 0; y < h; y++)
            {
                int row = y * w * 4;
                for (int x = 0; x < w; x++)
                {
                    int i = row + x * 4;
                    if (!wanted(px[i], px[i + 1], px[i + 2], px[i + 3]))
                    {
                        continue;
                    }
                    if (x < minX) minX = x;
                    if (x > maxX) maxX = x;
                    if (y < minY) minY = y;
                    if (y > maxY) maxY = y;
                }
            }
            bounds = Int32Rect.Empty;
            atEdge = false;
            if (maxX < 0 || maxX - minX < 4 || maxY - minY < 4)
            {
                return false;
            }
            atEdge = minX <= 0 || minY <= 0 || maxX >= w - 1 || maxY >= h - 1;
            bounds = new Int32Rect(minX, minY, maxX - minX + 1, maxY - minY + 1);
            return true;
        }
    }
}
