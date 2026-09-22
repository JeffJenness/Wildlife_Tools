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
using System.Collections.Generic;

namespace WildlifeTools.Geometric
{
    /// <summary>
    /// The Repeating Shapes window's client-side size math -- the same
    /// measure interlock and unit tables as the Python engine
    /// (repeating_shapes.derive_measures), duplicated here so the dialog
    /// can fill boxes on every keystroke without a validation round trip.
    /// The ENGINE remains the single source of truth at run time: the
    /// window only ever sends the user's typed values through the GP
    /// framework.
    /// </summary>
    internal static class RsMath
    {
        public const string Hexagons = "Hexagons";
        public const string Squares = "Squares";
        public const string Triangles = "Triangles";
        public const string Rectangles = "Rectangles (brick courses)";
        public const string CirclesSq = "Circles - square pattern";
        public const string CirclesTri = "Circles - triangular pattern";
        public const string PointsSq = "Points - square pattern";
        public const string PointsTri = "Points - triangular pattern";

        public static readonly string[] LinearUnits =
            { "Meters", "Kilometers", "Feet", "Miles" };
        public static readonly string[] AreaUnits =
            { "Square Meters", "Hectares", "Square Kilometers",
              "Square Feet", "Acres", "Square Miles" };

        public static readonly Dictionary<string, double> LinM = new()
        {
            ["Meters"] = 1.0, ["Kilometers"] = 1000.0,
            ["Feet"] = 0.3048, ["Miles"] = 1609.344,
        };

        public static readonly Dictionary<string, double> AreaM2 = new()
        {
            ["Square Meters"] = 1.0, ["Hectares"] = 10000.0,
            ["Square Kilometers"] = 1.0e6,
            ["Square Feet"] = 0.3048 * 0.3048,
            ["Acres"] = 4046.8564224,
            ["Square Miles"] = 1609.344 * 1609.344,
        };

        /// <summary>Jeff's coupling: an area unit snaps the linear unit
        /// to its natural partner.</summary>
        public static readonly Dictionary<string, string> AreaToLinear =
            new()
            {
                ["Square Meters"] = "Meters", ["Hectares"] = "Meters",
                ["Square Kilometers"] = "Kilometers",
                ["Square Feet"] = "Feet", ["Acres"] = "Feet",
                ["Square Miles"] = "Miles",
            };

        /// <summary>Measures each shape accepts, in interlock priority
        /// order ("area" values are in linear-unit-squared here).</summary>
        public static string[] MeasuresFor(string shape) => shape switch
        {
            Hexagons => new[] { "area", "edge", "width", "diameter" },
            Squares => new[] { "area", "edge" },
            Triangles => new[] { "area", "edge", "height" },
            Rectangles => new[] { "width", "height" },
            CirclesSq or CirclesTri =>
                new[] { "area", "radius", "diameter", "circumference",
                        "spacing" },
            _ => new[] { "spacing" },
        };

        /// <summary>All of a shape's measures from any one of them (in a
        /// consistent linear unit; areas in that unit squared). Returns
        /// an empty dictionary for independent measures (rectangle
        /// width/height, circle spacing).</summary>
        public static Dictionary<string, double> Derive(
            string shape, string measure, double v)
        {
            var o = new Dictionary<string, double>();
            if (v <= 0) return o;
            double e;
            switch (shape)
            {
                case Hexagons:
                    e = measure switch
                    {
                        "edge" => v,
                        "width" => v / Math.Sqrt(3.0),
                        "diameter" => v / 2.0,
                        "area" => Math.Sqrt(
                            v / (1.5 * Math.Sqrt(3.0))),
                        _ => 0,
                    };
                    if (e <= 0) return o;
                    o["edge"] = e;
                    o["width"] = Math.Sqrt(3.0) * e;
                    o["diameter"] = 2.0 * e;
                    o["area"] = 1.5 * Math.Sqrt(3.0) * e * e;
                    return o;
                case Squares:
                    e = measure == "edge" ? v : Math.Sqrt(v);
                    o["edge"] = e;
                    o["area"] = e * e;
                    return o;
                case Triangles:
                    e = measure switch
                    {
                        "edge" => v,
                        "height" => 2.0 * v / Math.Sqrt(3.0),
                        "area" => Math.Sqrt(4.0 * v / Math.Sqrt(3.0)),
                        _ => 0,
                    };
                    if (e <= 0) return o;
                    o["edge"] = e;
                    o["height"] = Math.Sqrt(3.0) / 2.0 * e;
                    o["area"] = Math.Sqrt(3.0) / 4.0 * e * e;
                    return o;
                case CirclesSq:
                case CirclesTri:
                    if (measure == "spacing") return o;
                    double r = measure switch
                    {
                        "radius" => v,
                        "diameter" => v / 2.0,
                        "circumference" => v / (2.0 * Math.PI),
                        "area" => Math.Sqrt(v / Math.PI),
                        _ => 0,
                    };
                    if (r <= 0) return o;
                    o["radius"] = r;
                    o["diameter"] = 2.0 * r;
                    o["circumference"] = 2.0 * Math.PI * r;
                    o["area"] = Math.PI * r * r;
                    return o;
                default:
                    return o;
            }
        }

        /// <summary>The orientation's symmetry period, for the Randomize
        /// button (hexagons/triangles repeat every 60 degrees, squares
        /// and square arrangements every 90, others every 180).</summary>
        public static double OrientationPeriod(string shape) =>
            shape switch
            {
                Hexagons or Triangles or CirclesTri or PointsTri => 60.0,
                Squares or CirclesSq or PointsSq => 90.0,
                _ => 180.0,
            };
    }
}
