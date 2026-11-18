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
using ArcGIS.Core.Data;
using ArcGIS.Core.Data.DDL;
using ArcGIS.Core.Geometry;
using FieldDescription = ArcGIS.Core.Data.DDL.FieldDescription;

namespace WildlifeTools.OnlineData.Gbif
{
    /// <summary>One attribute field of the output points.</summary>
    internal sealed class GbifField
    {
        public string Name;         // field name in the feature class
        public string Alias;
        public char Kind;           // 'T' text, 'I' whole number, 'D' decimal number
        public int Length;          // text length
        public string SearchKey;    // the name in a search-service record
        public string CsvColumn;    // the column in a download table

        public GbifField(string name, string alias, char kind, int length, string searchKey, string csvColumn)
        {
            Name = name; Alias = alias; Kind = kind; Length = length;
            SearchKey = searchKey; CsvColumn = csvColumn;
        }
    }

    /// <summary>
    /// Writes GBIF records to a point feature class (WGS 84) in a file
    /// geodatabase, with one fixed, typed set of fields whichever route
    /// the records came by (the search service or a download file). Field
    /// types are stated, never guessed from the values, so a catalog
    /// number such as "00123" keeps its zeros and a column that is numeric
    /// in the first rows and text later loses nothing.
    ///
    /// Uses ArcGIS.Core only (geodatabase and geometry), so it also runs
    /// under CoreHost in the headless test. Call it on the MCT in Pro.
    /// </summary>
    internal static class GbifFeatureWriter
    {
        /// <summary>The output fields. "order" and "class" get a trailing
        /// underscore because ORDER is a reserved SQL word.</summary>
        internal static readonly GbifField[] Fields =
        {
            new GbifField("gbifID", "GBIF record ID", 'T', 20, "key", "gbifID"),
            new GbifField("scientificName", "Scientific name", 'T', 255, "scientificName", "scientificName"),
            new GbifField("species", "Species", 'T', 150, "species", "species"),
            new GbifField("taxonRank", "Rank of the identification", 'T', 30, "taxonRank", "taxonRank"),
            new GbifField("kingdom", "Kingdom", 'T', 60, "kingdom", "kingdom"),
            new GbifField("phylum", "Phylum", 'T', 60, "phylum", "phylum"),
            new GbifField("class_", "Class", 'T', 60, "class", "class"),
            new GbifField("order_", "Order", 'T', 60, "order", "order"),
            new GbifField("family", "Family", 'T', 60, "family", "family"),
            new GbifField("genus", "Genus", 'T', 60, "genus", "genus"),
            new GbifField("taxonKey", "GBIF taxon key", 'T', 20, "taxonKey", "taxonKey"),
            new GbifField("speciesKey", "GBIF species key", 'T', 20, "speciesKey", "speciesKey"),
            new GbifField("eventDate", "Date of the record", 'T', 60, "eventDate", "eventDate"),
            new GbifField("year", "Year", 'I', 0, "year", "year"),
            new GbifField("month", "Month", 'I', 0, "month", "month"),
            new GbifField("day", "Day", 'I', 0, "day", "day"),
            new GbifField("decimalLatitude", "Latitude (WGS 84)", 'D', 0, "decimalLatitude", "decimalLatitude"),
            new GbifField("decimalLongitude", "Longitude (WGS 84)", 'D', 0, "decimalLongitude", "decimalLongitude"),
            new GbifField("coordUncert_m", "Coordinate uncertainty (m)", 'D', 0, "coordinateUncertaintyInMeters", "coordinateUncertaintyInMeters"),
            // why a location is vague or hidden, in the publisher's words (e.g. "Coordinate uncertainty
            // increased to 28734m at the request of the observer"). The search service returns these;
            // GBIF's simple download table does not carry them.
            new GbifField("infoWithheld", "Information withheld", 'T', 500, "informationWithheld", "informationWithheld"),
            new GbifField("dataGeneralized", "Data generalizations", 'T', 500, "dataGeneralizations", "dataGeneralizations"),
            new GbifField("elevation_m", "Elevation (m)", 'D', 0, "elevation", "elevation"),
            new GbifField("individualCount", "Individual count", 'I', 0, "individualCount", "individualCount"),
            // biology of the organism, where the publisher recorded it (most records do not)
            new GbifField("sex", "Sex", 'T', 60, "sex", "sex"),
            new GbifField("lifeStage", "Life stage", 'T', 60, "lifeStage", "lifeStage"),
            new GbifField("reproCondition", "Reproductive condition", 'T', 120, "reproductiveCondition", "reproductiveCondition"),
            new GbifField("behavior", "Behavior", 'T', 255, "behavior", "behavior"),
            new GbifField("occurrenceStatus", "Present or absent", 'T', 20, "occurrenceStatus", "occurrenceStatus"),
            new GbifField("basisOfRecord", "Basis of record", 'T', 40, "basisOfRecord", "basisOfRecord"),
            new GbifField("establishmentMeans", "Establishment means", 'T', 60, "establishmentMeans", "establishmentMeans"),
            new GbifField("countryCode", "Country code", 'T', 4, "countryCode", "countryCode"),
            new GbifField("stateProvince", "State or province", 'T', 120, "stateProvince", "stateProvince"),
            new GbifField("county", "County", 'T', 120, "county", "county"),
            new GbifField("locality", "Locality", 'T', 500, "locality", "locality"),
            new GbifField("recordedBy", "Recorded by", 'T', 255, "recordedBy", "recordedBy"),
            new GbifField("identifiedBy", "Identified by", 'T', 255, "identifiedBy", "identifiedBy"),
            new GbifField("institutionCode", "Institution code", 'T', 100, "institutionCode", "institutionCode"),
            new GbifField("collectionCode", "Collection code", 'T', 100, "collectionCode", "collectionCode"),
            new GbifField("catalogNumber", "Catalog number", 'T', 100, "catalogNumber", "catalogNumber"),
            new GbifField("datasetKey", "GBIF dataset key", 'T', 40, "datasetKey", "datasetKey"),
            new GbifField("datasetName", "Source dataset", 'T', 255, "datasetName", "datasetName"),
            new GbifField("iucnRedList", "IUCN Red List category", 'T', 10, "iucnRedListCategory", "iucnRedListCategory"),
            new GbifField("license", "License of the record", 'T', 80, "license", "license"),
            new GbifField("rightsHolder", "Rights holder", 'T', 255, "rightsHolder", "rightsHolder"),
            new GbifField("issues", "GBIF issue flags", 'T', 500, "issue", "issue"),
            new GbifField("mediaType", "Media attached", 'T', 60, "mediaType", "mediaType"),
            new GbifField("photoURL", "First photograph", 'T', 500, "photoURL", null),
            new GbifField("recordURL", "Record on GBIF.org", 'T', 60, null, null),
        };

        /// <summary>What a write did.</summary>
        internal sealed class Result
        {
            public long Written;
            public long SkippedNoCoordinates;
            public long SkippedZeroZero;    // "Null Island": latitude 0, longitude 0
            public long WrittenWithoutLocation;     // rows with an empty shape (counted in Written too)
            public long SkippedOutsideArea;
            public long Truncated;      // text values cut to fit their field
            public Dictionary<string, long> ByDataset = new Dictionary<string, long>(StringComparer.Ordinal);
            /// <summary>The name written for each dataset key (GBIF's title for the dataset,
            /// or the name the records themselves carry when no title was supplied).</summary>
            public Dictionary<string, string> DatasetNames = new Dictionary<string, string>(StringComparer.Ordinal);
            public Dictionary<string, long> ByLicense = new Dictionary<string, long>(StringComparer.Ordinal);
        }

        /// <summary>One record as the writer takes it: coordinates and a
        /// lookup from a source field name to its text.</summary>
        internal readonly struct Record
        {
            public readonly double Lon, Lat;
            public readonly bool HasCoordinates;
            public readonly Func<GbifField, string> Value;
            public Record(double lon, double lat, bool has, Func<GbifField, string> value)
            {
                Lon = lon; Lat = lat; HasCoordinates = has; Value = value;
            }
        }

        /// <summary>Records from search-service pages.</summary>
        internal static IEnumerable<Record> FromSearch(IEnumerable<GbifOccurrence> records)
        {
            foreach (GbifOccurrence o in records)
            {
                GbifOccurrence occ = o;
                bool has = occ.Fields.ContainsKey("decimalLongitude") && occ.Fields.ContainsKey("decimalLatitude");
                yield return new Record(occ.Lon, occ.Lat, has, f =>
                {
                    if (f.SearchKey == null)
                    {
                        return null;
                    }
                    if (f.SearchKey == "mediaType")
                    {
                        return occ.Media.Count > 0 ? "StillImage" : null;
                    }
                    if (f.SearchKey == "photoURL")
                    {
                        string url = occ.Media.Count > 0 ? occ.Media[0].Url : null;
                        return url != null && url.Length <= 500 ? url : null;
                    }
                    return occ.Fields.TryGetValue(f.SearchKey, out string v) ? v : null;
                });
            }
        }

        /// <summary>Records from a download table.</summary>
        internal static IEnumerable<Record> FromDownload(GbifDownloadFile table)
        {
            var index = new Dictionary<string, int>(StringComparer.Ordinal);
            for (int i = 0; i < table.Columns.Length; i++)
            {
                index[table.Columns[i]] = i;
            }
            foreach (string[] row in table.Rows())
            {
                string[] r = row;
                bool has = table.HasCoordinates
                    & double.TryParse(r[table.LonIndex], NumberStyles.Float, CultureInfo.InvariantCulture, out double lon)
                    & double.TryParse(table.HasCoordinates ? r[table.LatIndex] : "", NumberStyles.Float,
                        CultureInfo.InvariantCulture, out double lat);
                yield return new Record(lon, lat, has, f =>
                    f.CsvColumn != null && index.TryGetValue(f.CsvColumn, out int i) ? r[i] : null);
            }
        }

        /// <summary>
        /// Creates the point feature class <paramref name="name"/> in the
        /// file geodatabase and writes the records. An existing feature
        /// class of that name is replaced. <paramref name="keep"/>, when
        /// given, is the final study-area test (true = keep the record).
        /// <paramref name="progress"/> is told the running count.
        /// </summary>
        internal static Result Write(string gdbPath, string name, IEnumerable<Record> records,
            Func<double, double, bool> keep = null, Action<long> progress = null,
            System.Threading.CancellationToken ct = default, bool keepUnlocated = false,
            IDictionary<string, string> datasetTitles = null)
        {
            var result = new Result();
            SpatialReference wgs84 = SpatialReferences.WGS84;
            using (var gdb = new Geodatabase(new FileGeodatabaseConnectionPath(new Uri(gdbPath))))
            {
                var builder = new SchemaBuilder(gdb);
                bool exists;
                try
                {
                    using (FeatureClassDefinition old = gdb.GetDefinition<FeatureClassDefinition>(name))
                    {
                        exists = true;
                        builder.Delete(new FeatureClassDescription(old));
                    }
                }
                catch (ArcGIS.Core.Data.Exceptions.GeodatabaseException)
                {
                    exists = false;
                }
                if (exists && !builder.Build())
                {
                    throw new IOException("Could not replace the existing feature class " + name + ": "
                        + string.Join(" ", builder.ErrorMessages));
                }

                var fields = new List<FieldDescription>();
                foreach (GbifField f in Fields)
                {
                    FieldDescription fd;
                    switch (f.Kind)
                    {
                        case 'I': fd = new FieldDescription(f.Name, FieldType.Integer); break;
                        case 'D': fd = new FieldDescription(f.Name, FieldType.Double); break;
                        default: fd = new FieldDescription(f.Name, FieldType.String) { Length = f.Length }; break;
                    }
                    fd.AliasName = f.Alias;
                    fields.Add(fd);
                }
                var shape = new ShapeDescription(GeometryType.Point, wgs84);
                builder.Create(new FeatureClassDescription(name, fields, shape));
                if (!builder.Build())
                {
                    throw new IOException("Could not create the feature class " + name + ": "
                        + string.Join(" ", builder.ErrorMessages));
                }

                using (FeatureClass fc = gdb.OpenDataset<FeatureClass>(name))
                using (FeatureClassDefinition def = fc.GetDefinition())
                {
                    int shapeIndex = def.FindField(def.GetShapeField());
                    int[] idx = Fields.Select(f => def.FindField(f.Name)).ToArray();
                    int datasetField = Array.FindIndex(Fields, f => f.Name == "datasetKey");
                    int licenseField = Array.FindIndex(Fields, f => f.Name == "license");
                    int nameField = Array.FindIndex(Fields, f => f.Name == "datasetName");
                    int idField = Array.FindIndex(Fields, f => f.Name == "gbifID");
                    int urlField = Array.FindIndex(Fields, f => f.Name == "recordURL");

                    gdb.ApplyEdits(() =>
                    {
                        using (InsertCursor cursor = fc.CreateInsertCursor())
                        using (RowBuffer buf = fc.CreateRowBuffer())
                        {
                            foreach (Record rec in records)
                            {
                                ct.ThrowIfCancellationRequested();
                                // keepUnlocated: a record with no usable coordinates becomes a row with
                                // an EMPTY shape, never a point, so no spatial analysis can pick it up.
                                bool located = true;
                                if (!rec.HasCoordinates || double.IsNaN(rec.Lon) || double.IsNaN(rec.Lat)
                                    || rec.Lat < -90 || rec.Lat > 90 || rec.Lon < -180 || rec.Lon > 180)
                                {
                                    if (!keepUnlocated)
                                    {
                                        result.SkippedNoCoordinates++;
                                        continue;
                                    }
                                    located = false;
                                }
                                if (located && rec.Lon == 0 && rec.Lat == 0)
                                {
                                    // "Null Island": a record whose coordinates were
                                    // missing at the source and became 0, 0. Never mapped,
                                    // whatever the filters were.
                                    if (!keepUnlocated)
                                    {
                                        result.SkippedZeroZero++;
                                        continue;
                                    }
                                    located = false;
                                }
                                if (located && keep != null && !keep(rec.Lon, rec.Lat))
                                {
                                    result.SkippedOutsideArea++;
                                    continue;
                                }
                                string id = null;
                                string dsKey = null;
                                for (int i = 0; i < Fields.Length; i++)
                                {
                                    GbifField f = Fields[i];
                                    string text = i == urlField
                                        ? (id == null ? null : "https://www.gbif.org/occurrence/" + id)
                                        : Normalize(f, rec.Value(f));
                                    if (i == idField)
                                    {
                                        id = string.IsNullOrEmpty(text) ? null : text;
                                    }
                                    if (i == datasetField)
                                    {
                                        dsKey = string.IsNullOrEmpty(text) ? null : text;
                                    }
                                    else if (i == nameField && dsKey != null)
                                    {
                                        // Every row gets its source's name: GBIF's own title for the dataset
                                        // when it was supplied (the same name the Sources tab shows), otherwise
                                        // whatever name the record carries, which many records leave empty.
                                        if (datasetTitles != null && datasetTitles.TryGetValue(dsKey, out string title)
                                            && !string.IsNullOrEmpty(title))
                                        {
                                            text = title;
                                        }
                                        if (!string.IsNullOrEmpty(text))
                                        {
                                            result.DatasetNames[dsKey] = text;
                                        }
                                    }
                                    buf[idx[i]] = Convert(f, text, result);
                                    if (i == datasetField && !string.IsNullOrEmpty(text))
                                    {
                                        result.ByDataset[text] = result.ByDataset.TryGetValue(text, out long c) ? c + 1 : 1;
                                    }
                                    else if (i == licenseField)
                                    {
                                        string lic = string.IsNullOrEmpty(text) ? "(not stated)" : text;
                                        result.ByLicense[lic] = result.ByLicense.TryGetValue(lic, out long c) ? c + 1 : 1;
                                    }
                                }
                                buf[shapeIndex] = located ? MapPointBuilderEx.CreateMapPoint(rec.Lon, rec.Lat, wgs84) : null;
                                cursor.Insert(buf);
                                result.Written++;
                                if (!located)
                                {
                                    result.WrittenWithoutLocation++;
                                }
                                if (result.Written % 1000 == 0)
                                {
                                    cursor.Flush();
                                    progress?.Invoke(result.Written);
                                }
                            }
                            cursor.Flush();
                        }
                    });
                }
            }
            progress?.Invoke(result.Written);
            return result;
        }

        /// <summary>GBIF's search service gives a record's license as the
        /// address of its legal text; its download files give a short code.
        /// The output always carries the address, which a reader can open.</summary>
        internal static string LicenseUrl(string license)
        {
            switch ((license ?? "").Trim().ToUpperInvariant())
            {
                case "CC0_1_0": return "http://creativecommons.org/publicdomain/zero/1.0/legalcode";
                case "CC_BY_4_0": return "http://creativecommons.org/licenses/by/4.0/legalcode";
                case "CC_BY_NC_4_0": return "http://creativecommons.org/licenses/by-nc/4.0/legalcode";
                default: return license;
            }
        }

        /// <summary>Lists of people arrive as "A|B" from the search service
        /// and "A;B" in download files: both become "A; B".</summary>
        internal static string PeopleList(string text) =>
            text.IndexOf('|') < 0 && text.IndexOf(';') < 0 ? text
            : string.Join("; ", text.Split(new[] { '|', ';' }, StringSplitOptions.RemoveEmptyEntries)
                .Select(s => s.Trim()).Where(s => s.Length > 0));

        /// <summary>Makes a value the same whichever route it came by.</summary>
        internal static string Normalize(GbifField f, string text)
        {
            if (string.IsNullOrEmpty(text))
            {
                return text;
            }
            switch (f.Name)
            {
                case "license": return LicenseUrl(text);
                case "recordedBy":
                case "identifiedBy": return PeopleList(text);
                default: return text;
            }
        }

        private static object Convert(GbifField f, string text, Result result)
        {
            if (string.IsNullOrEmpty(text))
            {
                return null;
            }
            switch (f.Kind)
            {
                case 'I':
                    if (int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out int i))
                    {
                        return i;
                    }
                    return double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out double di)
                        && di >= int.MinValue && di <= int.MaxValue ? (object)(int)Math.Round(di) : null;
                case 'D':
                    return double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out double d)
                        && !double.IsNaN(d) && !double.IsInfinity(d) ? (object)d : null;
                default:
                    if (text.Length > f.Length)
                    {
                        result.Truncated++;
                        return text.Substring(0, f.Length);
                    }
                    return text;
            }
        }
    }
}
