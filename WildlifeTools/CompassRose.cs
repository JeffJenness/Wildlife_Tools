using ArcGIS.Core.CIM;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;
using System;
using System.IO;
using System.Linq;
using System.Reflection;

namespace WildlifeTools
{
    /// <summary>
    /// Helpers for placing the Jennessent Compass Rose. The artwork ships as a single SVG
    /// (Cartography\CompassRose.svg, deployed next to the assembly): the Graphics tool drops
    /// it as one picture graphic element, and the Cartography tool uses it as the marker
    /// symbol of a north arrow. Using the SVG (rather than dozens of individual graphics)
    /// keeps placement fast and the result a single, tidy element.
    /// </summary>
    internal static class CompassRose
    {
        /// <summary>Full path to the deployed compass SVG.</summary>
        public static string SvgPath
        {
            get
            {
                string dir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
                return Path.Combine(dir, "Cartography", "CompassRose.svg");
            }
        }

        /// <summary>
        /// A point symbol whose single layer is the compass SVG as a picture marker,
        /// embedded as a data URI so it travels with the project.
        /// </summary>
        public static CIMPointSymbol CreatePictureMarkerSymbol(double sizePoints)
        {
            string dataUri = "data:image/svg+xml;base64," +
                Convert.ToBase64String(File.ReadAllBytes(SvgPath));
            var marker = new CIMPictureMarker
            {
                Enable = true,
                Size = sizePoints,
                URL = dataUri
            };
            return new CIMPointSymbol { SymbolLayers = new CIMSymbolLayer[] { marker } };
        }

        /// <summary>
        /// Adds a north arrow to the layout whose marker symbol is the compass rose, tied to
        /// the given map frame and sized to <paramref name="sizePoints"/>. Must run on the MCT.
        /// </summary>
        public static NorthArrow CreateNorthArrow(Layout layout, MapFrame mapFrame, MapPoint location, double sizePageUnits)
        {
            // Seed a default north arrow from the ArcGIS 2D style, then swap in the compass marker.
            var style = Project.Current.GetItems<StyleProjectItem>().FirstOrDefault(s => s.Name == "ArcGIS 2D")
                        ?? Project.Current.GetItems<StyleProjectItem>().FirstOrDefault();
            var naStyleItem = style?.SearchNorthArrows(string.Empty).FirstOrDefault();

            var naInfo = new NorthArrowInfo
            {
                MapFrameName = mapFrame.Name,
                NorthArrowStyleItem = naStyleItem
            };
            var northArrow = ElementFactory.Instance.CreateMapSurroundElement(
                layout, location, naInfo, "Jennessent Compass North Arrow") as NorthArrow;

            // The marker Size is in POINTS, but the drawn box height is in page units
            // (usually inches); convert so the arrow fills the box instead of being tiny.
            double sizePoints = sizePageUnits * PointsPerPageUnit(layout);
            if (northArrow?.GetDefinition() is CIMMarkerNorthArrow cim)
            {
                cim.PointSymbol = CreatePictureMarkerSymbol(sizePoints).MakeSymbolReference();
                northArrow.SetDefinition(cim);
            }
            northArrow?.SetHeight(sizePageUnits);
            return northArrow;
        }

        /// <summary>Points (1/72 inch) per layout page unit, for sizing a point symbol to a
        /// page-unit box. Falls back to 72 (inches) if the page units can't be read.</summary>
        private static double PointsPerPageUnit(Layout layout)
        {
            try
            {
                double metersPerUnit = layout.GetPage().Units.ConversionFactor;
                if (metersPerUnit > 0)
                    return metersPerUnit / (0.0254 / 72.0);
            }
            catch
            {
            }
            return 72.0;
        }

        /// <summary>Square envelope centered in <paramref name="env"/> (keeps the compass round).</summary>
        public static Envelope Square(Envelope env)
        {
            double side = Math.Min(env.Width, env.Height);
            double cx = (env.XMin + env.XMax) / 2.0;
            double cy = (env.YMin + env.YMax) / 2.0;
            return EnvelopeBuilderEx.CreateEnvelope(
                cx - side / 2.0, cy - side / 2.0, cx + side / 2.0, cy + side / 2.0, env.SpatialReference);
        }

        /// <summary>
        /// Builds a page-coordinate envelope from two client (pixel) points of a manual drag
        /// on a layout. A click or tiny drag falls back to a sensible default size centered on
        /// the point (like Esri's own element tools). Must run on the MCT.
        /// </summary>
        public static Envelope ClientBoxToPage(LayoutView layoutView, System.Windows.Point p1, System.Windows.Point p2)
        {
            Coordinate2D a = layoutView.ClientToPage(p1);
            Coordinate2D b = layoutView.ClientToPage(p2);
            double xmin = Math.Min(a.X, b.X), xmax = Math.Max(a.X, b.X);
            double ymin = Math.Min(a.Y, b.Y), ymax = Math.Max(a.Y, b.Y);

            CIMPage page = layoutView.Layout.GetPage();
            double longSide = Math.Max(page.Width, page.Height);
            if (xmax - xmin < longSide * 0.02 || ymax - ymin < longSide * 0.02)
            {
                double def = longSide / 15.0;           // default size for a click
                xmin = a.X - def / 2.0; xmax = a.X + def / 2.0;
                ymin = a.Y - def / 2.0; ymax = a.Y + def / 2.0;
            }
            return EnvelopeBuilderEx.CreateEnvelope(xmin, ymin, xmax, ymax);
        }
    }
}
