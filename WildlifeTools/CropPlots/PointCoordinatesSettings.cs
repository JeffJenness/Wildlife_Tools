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
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace WildlifeTools.CropPlots
{
    /// <summary>The look of one kind of line in a Point Coordinates callout.</summary>
    public sealed class CalloutTextStyle
    {
        public string Family { get; set; } = "Arial";
        public double Size { get; set; } = 10;
        public bool Bold { get; set; } = true;
        public bool Italic { get; set; } = false;
        /// <summary>#RRGGBB</summary>
        public string Color { get; set; } = "#000000";

        public CalloutTextStyle Clone() => (CalloutTextStyle)MemberwiseClone();
    }

    /// <summary>
    /// What the Point Coordinates tool writes into its callout and how it
    /// looks, chosen in the Point Coordinates Settings window and kept per
    /// user in LocalAppData\JennessEnterprises\WildlifeTools\point_coordinates.json.
    /// Also builds the callout's text (with ArcGIS Pro text formatting tags,
    /// so the coordinate lines and the coordinate-system names can differ in
    /// size) -- pure code, testable outside Pro.
    /// </summary>
    public sealed class PointCoordinatesSettings
    {
        public const string CalloutNone = "None";
        public const string CalloutSimpleLine = "Simple line";
        public const string CalloutBackground = "Background";
        public const string CalloutBalloon = "Balloon";
        public static readonly string[] CalloutStyles = { CalloutNone, CalloutSimpleLine, CalloutBackground, CalloutBalloon };

        public bool ShowGcsName { get; set; } = true;
        public bool ShowLatDD { get; set; } = true;
        public bool ShowLonDD { get; set; } = true;
        public bool ShowLatDMS { get; set; } = false;
        public bool ShowLonDMS { get; set; } = false;
        public bool ShowX { get; set; } = true;
        public bool ShowY { get; set; } = true;
        public bool ShowPcsName { get; set; } = true;
        public int DdDecimals { get; set; } = 6;
        public int DmsDecimals { get; set; } = 2;
        public int XyDecimals { get; set; } = 2;
        public bool LabelLines { get; set; } = true;
        public CalloutTextStyle Coordinates { get; set; } = new CalloutTextStyle();
        public CalloutTextStyle Names { get; set; } = new CalloutTextStyle
        {
            Family = "Arial", Size = 7, Bold = false, Italic = true, Color = "#505050",
        };
        public string Callout { get; set; } = CalloutBalloon;
        public string BackgroundColor { get; set; } = "#FFFFBE";
        public string OutlineColor { get; set; } = "#C8C8C8";
        public string LeaderColor { get; set; } = "#808080";
        public bool Marker { get; set; } = true;
        /// <summary>Color and size of the DEFAULT marker (a circle with a
        /// gray outline), used while <see cref="MarkerSymbol"/> is empty.</summary>
        public string MarkerColor { get; set; } = "#55FF00";
        public double MarkerSize { get; set; } = 7;
        /// <summary>The user's own marker: any ArcGIS Pro point symbol, as
        /// the CIM symbol reference's JSON (from Format Point Symbol); null
        /// or empty = the default circle.</summary>
        public string MarkerSymbol { get; set; }

        [JsonIgnore]
        public bool AnyLine => ShowGcsName || ShowLatDD || ShowLonDD || ShowLatDMS || ShowLonDMS || ShowX || ShowY || ShowPcsName;

        public PointCoordinatesSettings Clone()
        {
            var c = (PointCoordinatesSettings)MemberwiseClone();
            c.Coordinates = Coordinates.Clone();
            c.Names = Names.Clone();
            return c;
        }

        // =====================================================================
        // the store
        // =====================================================================
        internal static string FolderOverride;

        private static string JsonPath
        {
            get
            {
                string dir = FolderOverride;
                if (string.IsNullOrEmpty(dir))
                {
                    string b = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                    if (string.IsNullOrEmpty(b))
                    {
                        b = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
                    }
                    dir = Path.Combine(b, "JennessEnterprises", "WildlifeTools");
                }
                Directory.CreateDirectory(dir);
                return Path.Combine(dir, "point_coordinates.json");
            }
        }

        private static readonly JsonSerializerOptions JsonOpts = new JsonSerializerOptions { WriteIndented = true };

        public static PointCoordinatesSettings Load()
        {
            try
            {
                string p = JsonPath;
                if (File.Exists(p))
                {
                    var s = JsonSerializer.Deserialize<PointCoordinatesSettings>(File.ReadAllText(p), JsonOpts);
                    if (s != null)
                    {
                        s.Coordinates ??= new CalloutTextStyle();
                        s.Names ??= new CalloutTextStyle { Size = 7, Bold = false, Italic = true, Color = "#505050" };
                        if (Array.IndexOf(CalloutStyles, s.Callout) < 0)
                        {
                            s.Callout = CalloutBalloon;
                        }
                        return s;
                    }
                }
            }
            catch
            {
            }
            return new PointCoordinatesSettings();
        }

        public void Save()
        {
            try
            {
                File.WriteAllText(JsonPath, JsonSerializer.Serialize(this, JsonOpts));
            }
            catch
            {
            }
        }

        // =====================================================================
        // the text
        // =====================================================================
        /// <summary>One line of the callout: its text and which style it uses.</summary>
        public readonly struct Line
        {
            public Line(string text, bool isName)
            {
                Text = text;
                IsName = isName;
            }

            public string Text { get; }
            public bool IsName { get; }
        }

        /// <summary>The callout's lines for a point: lon/lat (decimal
        /// degrees), the geographic coordinate system's name, projected X/Y
        /// (when the point's CS is projected) and the projected CS's name,
        /// each only when switched on. Names have their underscores turned
        /// into spaces. <paramref name="unitAbbrev"/> is the projected unit
        /// ("m", "ft US"), null for a geographic point.</summary>
        public List<Line> Lines(double lon, double lat, string gcsName, double? x, double? y, string pcsName,
            string unitAbbrev)
        {
            var lines = new List<Line>();
            string lbl(string s) => LabelLines ? s + " " : "";
            if (ShowLatDD)
            {
                lines.Add(new Line(lbl("Lat") + lat.ToString("F" + DdDecimals, CultureInfo.InvariantCulture) + "°", false));
            }
            if (ShowLonDD)
            {
                lines.Add(new Line(lbl("Lon") + lon.ToString("F" + DdDecimals, CultureInfo.InvariantCulture) + "°", false));
            }
            if (ShowLatDMS)
            {
                lines.Add(new Line(lbl("Lat") + Dms(lat, true), false));
            }
            if (ShowLonDMS)
            {
                lines.Add(new Line(lbl("Lon") + Dms(lon, false), false));
            }
            if (ShowGcsName && !string.IsNullOrEmpty(gcsName))
            {
                lines.Add(new Line(Pretty(gcsName), true));
            }
            if (x.HasValue && y.HasValue && !string.IsNullOrEmpty(pcsName))
            {
                string u = string.IsNullOrEmpty(unitAbbrev) ? "" : " " + unitAbbrev;
                if (ShowX)
                {
                    lines.Add(new Line(lbl("X") + x.Value.ToString("N" + XyDecimals, CultureInfo.InvariantCulture) + u, false));
                }
                if (ShowY)
                {
                    lines.Add(new Line(lbl("Y") + y.Value.ToString("N" + XyDecimals, CultureInfo.InvariantCulture) + u, false));
                }
                if (ShowPcsName)
                {
                    lines.Add(new Line(Pretty(pcsName), true));
                }
            }
            return lines;
        }

        /// <summary>Degrees, minutes and seconds with a hemisphere letter,
        /// e.g. 35°07'24.40"N.</summary>
        public string Dms(double deg, bool isLatitude)
        {
            string hemi = isLatitude ? (deg < 0 ? "S" : "N") : (deg < 0 ? "W" : "E");
            double a = Math.Abs(deg);
            int d = (int)Math.Floor(a);
            double mFull = (a - d) * 60.0;
            int m = (int)Math.Floor(mFull);
            double s = (mFull - m) * 60.0;
            // carry a rounded 60.00" up into the minutes/degrees
            double sRound = Math.Round(s, DmsDecimals);
            if (sRound >= 60.0)
            {
                sRound = 0;
                m += 1;
                if (m >= 60)
                {
                    m = 0;
                    d += 1;
                }
            }
            return d + "°" + m.ToString("00", CultureInfo.InvariantCulture) + "'"
                   + sRound.ToString("00." + new string('0', DmsDecimals), CultureInfo.InvariantCulture).TrimEnd('.')
                   + "\"" + hemi;
        }

        public static string Pretty(string csName) => (csName ?? "").Replace('_', ' ').Trim();

        private static string Tag(CalloutTextStyle st, string text)
        {
            string hex = (st.Color ?? "#000000").TrimStart('#');
            int r = 0, g = 0, b = 0;
            if (hex.Length == 6)
            {
                r = Convert.ToInt32(hex.Substring(0, 2), 16);
                g = Convert.ToInt32(hex.Substring(2, 2), 16);
                b = Convert.ToInt32(hex.Substring(4, 2), 16);
            }
            var sb = new StringBuilder();
            sb.Append("<FNT name='").Append(st.Family ?? "Arial").Append("' size='")
              .Append(st.Size.ToString("0.#", CultureInfo.InvariantCulture)).Append("'>");
            sb.Append("<CLR red='").Append(r).Append("' green='").Append(g).Append("' blue='").Append(b).Append("'>");
            if (st.Bold)
            {
                sb.Append("<BOL>");
            }
            if (st.Italic)
            {
                sb.Append("<ITA>");
            }
            sb.Append(text);
            if (st.Italic)
            {
                sb.Append("</ITA>");
            }
            if (st.Bold)
            {
                sb.Append("</BOL>");
            }
            sb.Append("</CLR></FNT>");
            return sb.ToString();
        }

        /// <summary>The callout text with ArcGIS Pro formatting tags, one
        /// line per entry of <see cref="Lines"/>.</summary>
        public string FormattedText(IEnumerable<Line> lines)
        {
            var parts = new List<string>();
            foreach (Line l in lines)
            {
                parts.Add(Tag(l.IsName ? Names : Coordinates, l.Text));
            }
            return string.Join("\n", parts);
        }

        /// <summary>The same lines without tags (for the settings preview).</summary>
        public static string PlainText(IEnumerable<Line> lines)
        {
            var parts = new List<string>();
            foreach (Line l in lines)
            {
                parts.Add(l.Text);
            }
            return string.Join("\n", parts);
        }
    }
}
