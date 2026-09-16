using System;
using System.Collections.Generic;

namespace WildlifeTools
{
    /// <summary>
    /// The size-unit choices shared by the Circle and Rectangle Background dialogs
    /// (2026-09-16, Jeff): index 0 is "% of the selected extent" (the original
    /// behavior); the remaining entries are linear units — on a layout the page
    /// units millimeters / centimeters / inches / points, on a map the map
    /// coordinate system's own unit — in which the value is a MARGIN added beyond
    /// the selected extent, the same idea as the Polygon and Minimum Convex Polygon
    /// tools' buffer size.
    /// </summary>
    internal static class BackgroundUnits
    {
        public const string PercentLabel = "% of extent";

        /// <summary>(label, meters per unit) for the layout page units.</summary>
        public static readonly (string Label, double MetersPerUnit)[] LayoutUnits =
        {
            ("Millimeters", 0.001),
            ("Centimeters", 0.01),
            ("Inches", 0.0254),
            ("Points", 0.0254 / 72.0),
        };

        /// <summary>The combo entries: percent first, then the linear units.</summary>
        public static List<string> Choices(bool isLayout, string mapUnitName)
        {
            var list = new List<string> { PercentLabel };
            if (isLayout)
                foreach (var (label, _) in LayoutUnits)
                    list.Add(label);
            else
                list.Add(string.IsNullOrEmpty(mapUnitName) ? "map units" : mapUnitName);
            return list;
        }

        /// <summary>True when the chosen index means a linear margin, not a percent.</summary>
        public static bool IsLinear(int unitIndex) => unitIndex > 0;

        /// <summary>
        /// Converts a margin typed in the chosen unit to the container's own units:
        /// page units on a layout (via meters per page unit), unchanged on a map.
        /// </summary>
        public static double ToContainerUnits(double value, int unitIndex, bool isLayout,
            double metersPerPageUnit)
        {
            if (!IsLinear(unitIndex))
                return value;
            if (!isLayout)
                return value;
            int i = Math.Clamp(unitIndex - 1, 0, LayoutUnits.Length - 1);
            double mpp = metersPerPageUnit > 0 ? metersPerPageUnit : 0.0254;
            return value * LayoutUnits[i].MetersPerUnit / mpp;
        }
    }
}
