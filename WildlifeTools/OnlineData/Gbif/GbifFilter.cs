// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Award notice, marking of modified versions, and
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
using System.Linq;
using System.Text;
using System.Text.Json;

namespace WildlifeTools.OnlineData.Gbif
{
    /// <summary>
    /// A broad group of organisms for narrowing the species list (mammals,
    /// birds, ...), defined by keys of the GBIF backbone taxonomy. A group is
    /// the taxa in <see cref="Include"/>, plus, when <see cref="Within"/>
    /// is set, everything inside that taxon except the taxa in
    /// <see cref="Except"/>.
    ///
    /// The keys were read from GBIF's backbone on 2026-11-14. The backbone
    /// has no single class for the reptiles (they are four classes) or for
    /// the ray-finned fishes (their orders sit directly under Chordata), so
    /// those two groups are lists.
    /// </summary>
    internal sealed class GbifSpeciesGroup
    {
        public string Name;
        public string[] Include = Array.Empty<string>();
        public string Within;
        public string[] Except = Array.Empty<string>();

        internal static readonly GbifSpeciesGroup[] All =
        {
            new GbifSpeciesGroup { Name = "Mammals", Include = new[] { "359" } },                 // Mammalia
            new GbifSpeciesGroup { Name = "Birds", Include = new[] { "212" } },                   // Aves
            // Crocodylia, Sphenodontia, Squamata, Testudines
            new GbifSpeciesGroup { Name = "Reptiles", Include = new[] { "11493978", "11569602", "11592253", "11418114" } },
            new GbifSpeciesGroup { Name = "Amphibians", Include = new[] { "131" } },              // Amphibia
            new GbifSpeciesGroup
            {
                Name = "Fishes",
                Include = new[]
                {
                    // classes: Elasmobranchii, Holocephali, Myxini, Petromyzonti, Coelacanthi, Dipneusti
                    "121", "120", "119", "11881065", "11733052", "11500725",
                    // the orders of ray-finned fishes, which the backbone places directly under Chordata
                    "1103", "1104", "494", "495", "1105", "496", "497", "1106", "498", "499", "1107", "537", "538",
                    "1153", "547", "1162", "548", "549", "550", "1163", "1164", "1165", "1166", "1167", "1305",
                    "1067", "1306", "1307", "1308", "1068", "1069", "587", "1310", "588", "589", "1311", "1312",
                    "1313", "590", "708", "890", "774", "889", "773", "772", "888",
                },
            },
            new GbifSpeciesGroup { Name = "Insects", Include = new[] { "216" } },                 // Insecta
            new GbifSpeciesGroup
            {
                // every animal that is neither a chordate nor an insect, plus the chordates
                // without a backbone: Ascidiacea, Thaliacea, Leptocardii, Copelata
                Name = "Other invertebrates",
                Within = "1",
                Except = new[] { "44", "216" },
                Include = new[] { "356", "207", "7375758", "1441" },
            },
            new GbifSpeciesGroup { Name = "Plants", Include = new[] { "6" } },                    // Plantae
            new GbifSpeciesGroup { Name = "Fungi", Include = new[] { "5" } },                     // Fungi
        };
    }

    /// <summary>
    /// What the user asked GBIF for: which taxa, where, when, and which
    /// records to leave out. One filter serves every route (count, species
    /// list, paged search, download request), because all of them take the
    /// same JSON "predicate". No ArcGIS types here.
    /// </summary>
    internal sealed class GbifFilter
    {
        /// <summary>GBIF refuses a download whose polygons hold more points
        /// than this in total (HTTP 413, tested 2026-11-13).</summary>
        internal const int MaxDownloadPolygonPoints = 10000;

        /// <summary>Taxon keys from the GBIF backbone, any rank. A key
        /// includes every child taxon and synonym. Empty = all taxa.</summary>
        public List<string> TaxonKeys { get; } = new List<string>();

        /// <summary>Broad groups to narrow the request to (a record must
        /// belong to one of them, as well as to <see cref="TaxonKeys"/> when
        /// those are given). Used for the species list. Empty = no narrowing.</summary>
        public List<GbifSpeciesGroup> Groups { get; } = new List<GbifSpeciesGroup>();

        /// <summary>The study area as polygons in WGS 84 longitude/latitude:
        /// each polygon is a list of rings, the outer ring first and any
        /// holes after it; a ring is a closed or open list of (lon, lat).
        /// Ring direction does not matter here: <see cref="ToWkt"/> writes
        /// outer rings counter-clockwise and holes clockwise, which is what
        /// GBIF requires (a clockwise outer ring means "everywhere else").
        /// Empty = no spatial filter.</summary>
        public List<List<List<(double Lon, double Lat)>>> Polygons { get; } =
            new List<List<List<(double Lon, double Lat)>>>();

        /// <summary>Two-letter country code (US, CA, MX ...) the record
        /// states or lies in. Null = any country.</summary>
        public string Country { get; set; }

        /// <summary>State or province exactly as the records write it
        /// ("Arizona"; GBIF matches this text letter for letter). Null = any.</summary>
        public string StateProvince { get; set; }

        /// <summary>Also return records that have no coordinates. With a
        /// study-area polygon the request becomes "inside the polygon, OR
        /// without coordinates": the polygon limits the records that can be
        /// mapped, and the records without coordinates are limited only by
        /// the other choices (country, state or province, dates ...).</summary>
        public bool IncludeNoCoordinates { get; set; }

        public int? YearFrom { get; set; }
        public int? YearTo { get; set; }

        /// <summary>Months 1-12. Empty or all twelve = no month filter.</summary>
        public List<int> Months { get; } = new List<int>();

        /// <summary>GBIF basis-of-record codes to KEEP (HUMAN_OBSERVATION,
        /// PRESERVED_SPECIMEN, ...). Empty = all.</summary>
        public List<string> BasisOfRecord { get; } = new List<string>();

        /// <summary>GBIF licence codes to KEEP (CC0_1_0, CC_BY_4_0,
        /// CC_BY_NC_4_0). Empty = all.</summary>
        public List<string> Licenses { get; } = new List<string>();

        /// <summary>Leave out records GBIF flags with a geospatial issue.</summary>
        public bool ExcludeGeospatialIssues { get; set; }

        /// <summary>GBIF dataset keys to LEAVE OUT (sources the user does
        /// not trust or does not want). Every other source stays in.</summary>
        public List<string> ExcludedDatasetKeys { get; } = new List<string>();

        /// <summary>Leave out records whose coordinates sit within 5 km of
        /// the center point of a country or region: GBIF marks these because
        /// such a point usually stands for "somewhere in this country", not
        /// for a place the organism was found.</summary>
        public bool ExcludeCentroids { get; set; }

        /// <summary>Keep presence records only (leave out recorded absences).</summary>
        public bool PresentOnly { get; set; }

        /// <summary>Keep only records whose stated coordinate uncertainty is
        /// at most this many meters. Records with NO stated uncertainty are
        /// left out too when this is set: GBIF cannot match a blank.</summary>
        public double? MaxCoordinateUncertaintyMeters { get; set; }

        /// <summary>Keep only records that carry a still image.</summary>
        public bool WithImagesOnly { get; set; }

        // ------------------------------------------------------------------
        /// <summary>Total number of polygon points as GBIF counts them
        /// (every ring closed, so the first point is counted twice).</summary>
        public int PolygonPointCount()
        {
            int n = 0;
            foreach (var poly in Polygons)
            {
                foreach (var ring in poly)
                {
                    var r = CloseRing(ring);
                    n += r.Count;
                }
            }
            return n;
        }

        /// <summary>True when GBIF's Download service will accept the
        /// polygon as it is.</summary>
        public bool PolygonFitsDownload => PolygonPointCount() <= MaxDownloadPolygonPoints;

        /// <summary>The study area as one WKT POLYGON or MULTIPOLYGON, or
        /// null when there is none.</summary>
        public string ToWkt()
        {
            var parts = new List<string>();
            foreach (var poly in Polygons)
            {
                var rings = new List<string>();
                for (int i = 0; i < poly.Count; i++)
                {
                    var ring = CloseRing(poly[i]);
                    if (ring.Count < 4)
                    {
                        continue;                      // not a ring
                    }
                    bool ccw = SignedArea(ring) > 0;
                    bool wantCcw = i == 0;             // outer ccw, holes cw
                    if (ccw != wantCcw)
                    {
                        ring.Reverse();
                    }
                    rings.Add("(" + string.Join(",", ring.Select(p =>
                        Num(p.Lon) + " " + Num(p.Lat))) + ")");
                }
                if (rings.Count > 0)
                {
                    parts.Add("(" + string.Join(",", rings) + ")");
                }
            }
            if (parts.Count == 0)
            {
                return null;
            }
            return parts.Count == 1
                ? "POLYGON" + parts[0]
                : "MULTIPOLYGON(" + string.Join(",", parts) + ")";
        }

        /// <summary>The filter as the JSON predicate GBIF's search and
        /// download services take. Null when nothing is filtered.</summary>
        public string ToPredicateJson()
        {
            using (var ms = new MemoryStream())
            {
                using (var w = new Utf8JsonWriter(ms))
                {
                    if (!WritePredicate(w))
                    {
                        return null;
                    }
                }
                return Encoding.UTF8.GetString(ms.ToArray());
            }
        }

        /// <summary>Writes the predicate object. False (nothing written)
        /// when the filter is empty.</summary>
        internal bool WritePredicate(Utf8JsonWriter w)
        {
            var clauses = new List<Action<Utf8JsonWriter>>();

            if (TaxonKeys.Count == 1)
            {
                clauses.Add(x => WriteEquals(x, "TAXON_KEY", TaxonKeys[0]));
            }
            else if (TaxonKeys.Count > 1)
            {
                clauses.Add(x => WriteIn(x, "TAXON_KEY", TaxonKeys));
            }

            if (Groups.Count > 0)
            {
                List<string> listed = Groups.SelectMany(g => g.Include).Distinct().ToList();
                List<GbifSpeciesGroup> ranged = Groups.Where(g => g.Within != null).ToList();
                bool several = (listed.Count > 0 ? 1 : 0) + ranged.Count > 1;
                clauses.Add(x =>
                {
                    if (several)
                    {
                        x.WriteStartObject();
                        x.WriteString("type", "or");
                        x.WriteStartArray("predicates");
                    }
                    if (listed.Count > 0)
                    {
                        WriteIn(x, "TAXON_KEY", listed);
                    }
                    foreach (GbifSpeciesGroup g in ranged)
                    {
                        x.WriteStartObject();
                        x.WriteString("type", "and");
                        x.WriteStartArray("predicates");
                        WriteEquals(x, "TAXON_KEY", g.Within);
                        if (g.Except.Length > 0)
                        {
                            x.WriteStartObject();
                            x.WriteString("type", "not");
                            x.WritePropertyName("predicate");
                            WriteIn(x, "TAXON_KEY", g.Except);
                            x.WriteEndObject();
                        }
                        x.WriteEndArray();
                        x.WriteEndObject();
                    }
                    if (several)
                    {
                        x.WriteEndArray();
                        x.WriteEndObject();
                    }
                });
            }

            string wkt = ToWkt();
            if (wkt != null)
            {
                bool orUnlocated = IncludeNoCoordinates;
                clauses.Add(x =>
                {
                    if (orUnlocated)
                    {
                        x.WriteStartObject();
                        x.WriteString("type", "or");
                        x.WriteStartArray("predicates");
                    }
                    x.WriteStartObject();
                    x.WriteString("type", "within");
                    x.WriteString("geometry", wkt);
                    x.WriteEndObject();
                    if (orUnlocated)
                    {
                        WriteEquals(x, "HAS_COORDINATE", "false");
                        x.WriteEndArray();
                        x.WriteEndObject();
                    }
                });
            }
            else if (!IncludeNoCoordinates)
            {
                // With no polygon, still ask only for records that can be mapped.
                clauses.Add(x => WriteEquals(x, "HAS_COORDINATE", "true"));
            }
            if (!string.IsNullOrEmpty(Country))
            {
                clauses.Add(x => WriteEquals(x, "COUNTRY", Country));
            }
            if (!string.IsNullOrEmpty(StateProvince))
            {
                clauses.Add(x => WriteEquals(x, "STATE_PROVINCE", StateProvince));
            }

            if (YearFrom.HasValue)
            {
                clauses.Add(x => WriteCompare(x, "greaterThanOrEquals", "YEAR", Inv(YearFrom.Value)));
            }
            if (YearTo.HasValue)
            {
                clauses.Add(x => WriteCompare(x, "lessThanOrEquals", "YEAR", Inv(YearTo.Value)));
            }
            var months = Months.Where(m => m >= 1 && m <= 12).Distinct().OrderBy(m => m).ToList();
            if (months.Count > 0 && months.Count < 12)
            {
                clauses.Add(x => WriteIn(x, "MONTH", months.Select(Inv).ToList()));
            }
            if (BasisOfRecord.Count > 0)
            {
                clauses.Add(x => WriteIn(x, "BASIS_OF_RECORD", BasisOfRecord));
            }
            if (Licenses.Count > 0)
            {
                clauses.Add(x => WriteIn(x, "LICENSE", Licenses));
            }
            if (ExcludeGeospatialIssues)
            {
                clauses.Add(x => WriteEquals(x, "HAS_GEOSPATIAL_ISSUE", "false"));
            }
            if (ExcludedDatasetKeys.Count > 0)
            {
                clauses.Add(x =>
                {
                    x.WriteStartObject();
                    x.WriteString("type", "not");
                    x.WritePropertyName("predicate");
                    WriteIn(x, "DATASET_KEY", ExcludedDatasetKeys);
                    x.WriteEndObject();
                });
            }
            if (ExcludeCentroids)
            {
                clauses.Add(x =>
                {
                    x.WriteStartObject();
                    x.WriteString("type", "isNull");
                    x.WriteString("parameter", "DISTANCE_FROM_CENTROID_IN_METERS");
                    x.WriteEndObject();
                });
            }
            if (PresentOnly)
            {
                clauses.Add(x => WriteEquals(x, "OCCURRENCE_STATUS", "PRESENT"));
            }
            if (MaxCoordinateUncertaintyMeters.HasValue)
            {
                clauses.Add(x => WriteCompare(x, "lessThanOrEquals",
                    "COORDINATE_UNCERTAINTY_IN_METERS", Num(MaxCoordinateUncertaintyMeters.Value)));
            }
            if (WithImagesOnly)
            {
                clauses.Add(x => WriteEquals(x, "MEDIA_TYPE", "StillImage"));
            }

            if (clauses.Count == 0)
            {
                return false;
            }
            if (clauses.Count == 1)
            {
                clauses[0](w);
                return true;
            }
            w.WriteStartObject();
            w.WriteString("type", "and");
            w.WriteStartArray("predicates");
            foreach (var c in clauses)
            {
                c(w);
            }
            w.WriteEndArray();
            w.WriteEndObject();
            return true;
        }

        /// <summary>A copy of this filter (the polygons are shared, not
        /// copied: they are not edited after being set).</summary>
        public GbifFilter Clone()
        {
            var f = new GbifFilter
            {
                YearFrom = YearFrom,
                YearTo = YearTo,
                Country = Country,
                StateProvince = StateProvince,
                IncludeNoCoordinates = IncludeNoCoordinates,
                ExcludeGeospatialIssues = ExcludeGeospatialIssues,
                ExcludeCentroids = ExcludeCentroids,
                PresentOnly = PresentOnly,
                MaxCoordinateUncertaintyMeters = MaxCoordinateUncertaintyMeters,
                WithImagesOnly = WithImagesOnly,
            };
            f.TaxonKeys.AddRange(TaxonKeys);
            f.Groups.AddRange(Groups);
            f.Polygons.AddRange(Polygons);
            f.Months.AddRange(Months);
            f.BasisOfRecord.AddRange(BasisOfRecord);
            f.Licenses.AddRange(Licenses);
            f.ExcludedDatasetKeys.AddRange(ExcludedDatasetKeys);
            return f;
        }

        // ---- geometry helpers -------------------------------------------------
        internal static List<(double Lon, double Lat)> CloseRing(IList<(double Lon, double Lat)> ring)
        {
            var r = new List<(double Lon, double Lat)>(ring.Count + 1);
            foreach (var p in ring)
            {
                // drop exact repeats, which GBIF's parser can reject
                if (r.Count == 0 || r[r.Count - 1].Lon != p.Lon || r[r.Count - 1].Lat != p.Lat)
                {
                    r.Add(p);
                }
            }
            if (r.Count > 0 && (r[0].Lon != r[r.Count - 1].Lon || r[0].Lat != r[r.Count - 1].Lat))
            {
                r.Add(r[0]);
            }
            return r;
        }

        /// <summary>Twice-signed-area test: positive = counter-clockwise.</summary>
        internal static double SignedArea(IList<(double Lon, double Lat)> ring)
        {
            double a = 0;
            for (int i = 0, j = ring.Count - 1; i < ring.Count; j = i++)
            {
                a += (ring[j].Lon * ring[i].Lat) - (ring[i].Lon * ring[j].Lat);
            }
            return a / 2.0;
        }

        // ---- JSON helpers -----------------------------------------------------
        private static void WriteEquals(Utf8JsonWriter w, string key, string value) =>
            WriteCompare(w, "equals", key, value);

        private static void WriteCompare(Utf8JsonWriter w, string type, string key, string value)
        {
            w.WriteStartObject();
            w.WriteString("type", type);
            w.WriteString("key", key);
            w.WriteString("value", value);
            w.WriteEndObject();
        }

        private static void WriteIn(Utf8JsonWriter w, string key, IList<string> values)
        {
            w.WriteStartObject();
            w.WriteString("type", "in");
            w.WriteString("key", key);
            w.WriteStartArray("values");
            foreach (string v in values)
            {
                w.WriteStringValue(v);
            }
            w.WriteEndArray();
            w.WriteEndObject();
        }

        private static string Inv(int v) => v.ToString(CultureInfo.InvariantCulture);

        /// <summary>Six decimals of a degree is about 0.1 m: plenty for a
        /// study area, and it keeps the request small.</summary>
        private static string Num(double v) =>
            Math.Round(v, 6).ToString("0.######", CultureInfo.InvariantCulture);
    }
}
