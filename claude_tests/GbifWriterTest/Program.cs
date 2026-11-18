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

// Headless test of GbifFeatureWriter under ArcGIS CoreHost: writes GBIF
// records to a point feature class in a scratch file geodatabase and reads
// them back. Needs ArcGIS Pro installed and licensed (it may be closed).
//
//   dotnet run --project claude_tests/GbifWriterTest
//   dotnet run --project claude_tests/GbifWriterTest -- <GBIF SIMPLE_CSV zip>
//
// Part 1 uses hand-made records (edge cases); part 2, with a zip, loads a
// real download and checks the count; part 3 fetches one live search page.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.Loader;
using ArcGIS.Core.Data;
using ArcGIS.Core.Data.DDL;
using ArcGIS.Core.Geometry;
using WildlifeTools.OnlineData.Gbif;

internal static class Program
{
    private const string ProBin = @"C:\Program Files\ArcGIS\Pro\bin";
    private static int passed, failed;

    private static void Check(string label, bool ok, string detail = null)
    {
        if (ok) passed++; else failed++;
        Console.WriteLine((ok ? "  ok    " : "  FAIL  ") + label + (detail == null ? "" : "  [" + detail + "]"));
    }

    [STAThread]
    private static int Main(string[] args)
    {
        AssemblyLoadContext.Default.Resolving += (ctx, name) =>
        {
            string hit = Directory.EnumerateFiles(ProBin, name.Name + ".dll", SearchOption.AllDirectories).FirstOrDefault();
            return hit == null ? null : ctx.LoadFromAssemblyPath(hit);
        };
        return Run(args);
    }

    private static int Run(string[] args)
    {
        try
        {
            ArcGIS.Core.Hosting.Host.Initialize();
        }
        catch (Exception ex)
        {
            Console.WriteLine("CoreHost could not start (is ArcGIS Pro installed and licensed?): " + ex.Message);
            return 2;
        }

        string work = Path.Combine(Path.GetTempPath(), "gbif_writer_test_" + Guid.NewGuid().ToString("N").Substring(0, 8));
        Directory.CreateDirectory(work);
        string gdbPath = Path.Combine(work, "t.gdb");
        using (Geodatabase g = SchemaBuilder.CreateGeodatabase(new FileGeodatabaseConnectionPath(new Uri(gdbPath))))
        {
        }

        // ---- 1. hand-made records -------------------------------------------
        Console.WriteLine("Hand-made records");
        GbifOccurrence Occ(double lon, double lat, bool coords, params (string, string)[] kv)
        {
            var o = new GbifOccurrence { Lon = lon, Lat = lat };
            if (coords)
            {
                o.Fields["decimalLongitude"] = lon.ToString(System.Globalization.CultureInfo.InvariantCulture);
                o.Fields["decimalLatitude"] = lat.ToString(System.Globalization.CultureInfo.InvariantCulture);
            }
            foreach (var (k, v) in kv) o.Fields[k] = v;
            return o;
        }
        var recs = new List<GbifOccurrence>
        {
            Occ(-111.6, 35.2, true, ("key", "5938421997"), ("species", "Cyanocitta stelleri"), ("order", "Passeriformes"),
                ("class", "Aves"), ("catalogNumber", "00123"), ("year", "2026"), ("month", "1"),
                ("coordinateUncertaintyInMeters", "12.5"), ("datasetKey", "ds-A"),
                ("license", "http://creativecommons.org/licenses/by/4.0/legalcode"),
                ("locality", "San Francisco Peaks, \"north\" side; caf\u00e9 \u2603"), ("individualCount", "3.0"),
                ("sex", "FEMALE"), ("lifeStage", "ADULT"), ("county", "Coconino")),
            Occ(-111.7, 35.3, true, ("key", "42"), ("species", "Pinus ponderosa"), ("catalogNumber", "A-7"),
                ("year", "not a year"), ("datasetKey", "ds-A"), ("locality", new string('x', 700))),
            Occ(0, 0, false, ("key", "43"), ("species", "No coordinates")),
            Occ(-200, 35, true, ("key", "44"), ("species", "Bad longitude")),
            Occ(-100.0, 40.0, true, ("key", "45"), ("species", "Outside the area"), ("datasetKey", "ds-B")),
            Occ(0.0, 0.0, true, ("key", "46"), ("species", "Null Island"), ("datasetKey", "ds-C")),
        };
        recs[0].Media.Add(new GbifMedia { Url = "http://example.org/a.jpg" });

        var r = GbifFeatureWriter.Write(gdbPath, "hand", GbifFeatureWriter.FromSearch(recs),
            (lon, lat) => lon < -105);
        Check("2 written, 2 without usable coordinates, 1 outside the area",
            r.Written == 2 && r.SkippedNoCoordinates == 2 && r.SkippedOutsideArea == 1,
            r.Written + " / " + r.SkippedNoCoordinates + " / " + r.SkippedOutsideArea);
        Check("a record at latitude 0, longitude 0 is never written", r.SkippedZeroZero == 1 && !r.ByDataset.ContainsKey("ds-C"),
            r.SkippedZeroZero.ToString());
        Check("one long text was cut and counted", r.Truncated == 1, r.Truncated.ToString());
        Check("records per dataset and per licence are tallied",
            r.ByDataset.TryGetValue("ds-A", out long da) && da == 2 && !r.ByDataset.ContainsKey("ds-B")
            && r.ByLicense.Values.Sum() == 2);

        using (var gdb = new Geodatabase(new FileGeodatabaseConnectionPath(new Uri(gdbPath))))
        using (FeatureClass fc = gdb.OpenDataset<FeatureClass>("hand"))
        using (FeatureClassDefinition def = fc.GetDefinition())
        {
            Check("point feature class in WGS 84",
                def.GetShapeType() == GeometryType.Point && def.GetSpatialReference().Wkid == 4326);
            var byName = def.GetFields().ToDictionary(f => f.Name, f => f);
            Check("every schema field exists with its type",
                GbifFeatureWriter.Fields.All(f => byName.ContainsKey(f.Name)
                    && byName[f.Name].FieldType == (f.Kind == 'I' ? FieldType.Integer : f.Kind == 'D' ? FieldType.Double : FieldType.String)));
            Check("class_ and order_ carry their aliases",
                byName["class_"].AliasName == "Class" && byName["order_"].AliasName == "Order");
            using (RowCursor cur = fc.Search(new QueryFilter { PostfixClause = "ORDER BY gbifID" }, false))
            {
                var rows = new List<Dictionary<string, object>>();
                while (cur.MoveNext())
                {
                    using (var f = (Feature)cur.Current)
                    {
                        var d = new Dictionary<string, object>();
                        foreach (GbifField gf in GbifFeatureWriter.Fields) d[gf.Name] = f[gf.Name];
                        var pt = (MapPoint)f.GetShape();
                        d["X"] = pt.X; d["Y"] = pt.Y;
                        rows.Add(d);
                    }
                }
                Check("two rows read back", rows.Count == 2);
                var a = rows.First(x => (string)x["gbifID"] == "5938421997");
                var b = rows.First(x => (string)x["gbifID"] == "42");
                Check("ten-digit ID kept as text", (string)a["gbifID"] == "5938421997");
                Check("catalog number keeps its leading zeros", (string)a["catalogNumber"] == "00123");
                Check("numbers typed: year, uncertainty, count '3.0' -> 3",
                    (int)a["year"] == 2026 && (double)a["coordUncert_m"] == 12.5 && (int)a["individualCount"] == 3);
                Check("accents, quotes and symbols survive",
                    (string)a["locality"] == "San Francisco Peaks, \"north\" side; caf\u00e9 \u2603");
                Check("class and order land in class_ and order_",
                    (string)a["class_"] == "Aves" && (string)a["order_"] == "Passeriformes");
                Check("license stays the address of its legal text",
                    (string)a["license"] == "http://creativecommons.org/licenses/by/4.0/legalcode");
                Check("record URL built from the ID",
                    (string)a["recordURL"] == "https://www.gbif.org/occurrence/5938421997");
                Check("media flag set from the photo list", (string)a["mediaType"] == "StillImage" && b["mediaType"] == null);
                Check("a year that is not a number becomes empty, not zero", b["year"] == null || b["year"] is DBNull);
                Check("missing values are empty", b["coordUncert_m"] == null || b["coordUncert_m"] is DBNull);
                Check("700-character text cut to the field length", ((string)b["locality"]).Length == 500);
                Check("geometry matches the coordinates",
                    Math.Abs((double)a["X"] + 111.6) < 1e-9 && Math.Abs((double)a["Y"] - 35.2) < 1e-9);
            }
        }
        r = GbifFeatureWriter.Write(gdbPath, "hand", GbifFeatureWriter.FromSearch(recs.Take(1)));
        using (var gdb = new Geodatabase(new FileGeodatabaseConnectionPath(new Uri(gdbPath))))
        using (FeatureClass fc = gdb.OpenDataset<FeatureClass>("hand"))
        {
            Check("writing again replaces the feature class", r.Written == 1 && fc.GetCount() == 1);
        }

        // the two routes write the same values
        Check("a download's license code becomes the same address the direct fetch gives",
            GbifFeatureWriter.LicenseUrl("CC_BY_NC_4_0") == "http://creativecommons.org/licenses/by-nc/4.0/legalcode"
            && GbifFeatureWriter.LicenseUrl("CC0_1_0") == "http://creativecommons.org/publicdomain/zero/1.0/legalcode"
            && GbifFeatureWriter.LicenseUrl("CC_BY_4_0") == "http://creativecommons.org/licenses/by/4.0/legalcode"
            && GbifFeatureWriter.LicenseUrl("http://creativecommons.org/licenses/by/4.0/legalcode") == "http://creativecommons.org/licenses/by/4.0/legalcode"
            && GbifFeatureWriter.LicenseUrl("something else") == "something else");
        Check("lists of people read the same from both routes",
            GbifFeatureWriter.PeopleList("Vorhies|103") == "Vorhies; 103" && GbifFeatureWriter.PeopleList("Vorhies;103") == "Vorhies; 103"
            && GbifFeatureWriter.PeopleList("Jane Doe") == "Jane Doe");

        // every row gets its source's name; the key is kept beside it
        var titles = new Dictionary<string, string> { ["ds-A"] = "Title of Dataset A from GBIF" };
        r = GbifFeatureWriter.Write(gdbPath, "named", GbifFeatureWriter.FromSearch(recs), null, null, default, false, titles);
        using (var gdb = new Geodatabase(new FileGeodatabaseConnectionPath(new Uri(gdbPath))))
        using (FeatureClass fc = gdb.OpenDataset<FeatureClass>("named"))
        using (RowCursor cur = fc.Search(null, false))
        {
            int named = 0, rows = 0, keyed = 0;
            while (cur.MoveNext())
            {
                using (var f = (Feature)cur.Current)
                {
                    rows++;
                    if ((string)f["datasetKey"] == "ds-A") { keyed++; if ((string)f["datasetName"] == "Title of Dataset A from GBIF") named++; }
                }
            }
            Check("rows of a dataset with a GBIF title carry that title, and keep the key", keyed == 2 && named == 2, named + " of " + keyed);
        }
        Check("the result remembers each source's name for the report",
            r.DatasetNames.TryGetValue("ds-A", out string nmA) && nmA == "Title of Dataset A from GBIF" && !r.DatasetNames.ContainsKey("ds-B"));

        // records without coordinates, kept as rows with an empty shape
        r = GbifFeatureWriter.Write(gdbPath, "unlocated", GbifFeatureWriter.FromSearch(recs), null, null, default, true);
        Check("keeping unlocated records: all 6 written, 3 of them without a location (no coordinates, bad longitude, 0,0)",
            r.Written == 6 && r.WrittenWithoutLocation == 3 && r.SkippedNoCoordinates == 0 && r.SkippedZeroZero == 0,
            r.Written + " / " + r.WrittenWithoutLocation);
        using (var gdb = new Geodatabase(new FileGeodatabaseConnectionPath(new Uri(gdbPath))))
        using (FeatureClass fc = gdb.OpenDataset<FeatureClass>("unlocated"))
        using (RowCursor cur = fc.Search(null, false))
        {
            int empty = 0, points = 0;
            bool nullIslandIsEmpty = false, biology = false;
            while (cur.MoveNext())
            {
                using (var f = (Feature)cur.Current)
                {
                    Geometry g = f.GetShape();
                    bool isEmpty = g == null || g.IsEmpty;
                    if (isEmpty) empty++; else points++;
                    if ((string)f["gbifID"] == "46") nullIslandIsEmpty = isEmpty;
                    if ((string)f["gbifID"] == "5938421997") biology = (string)f["sex"] == "FEMALE" && (string)f["lifeStage"] == "ADULT" && (string)f["county"] == "Coconino";
                }
            }
            Check("those 3 rows have an empty shape and the other 3 are points", empty == 3 && points == 3, empty + " empty, " + points + " points");
            Check("the Null Island record is a row without a point, never a point at 0, 0", nullIslandIsEmpty);
            Check("sex, life stage and county are written", biology);
        }

        // ---- 2. a real download ------------------------------------------------
        string zip = args.FirstOrDefault(a => a.EndsWith(".zip", StringComparison.OrdinalIgnoreCase));
        if (zip != null && File.Exists(zip))
        {
            Console.WriteLine("A real download: " + Path.GetFileName(zip));
            long expected;
            using (var t = new GbifDownloadFile(zip)) expected = t.Rows().LongCount();
            var sw = System.Diagnostics.Stopwatch.StartNew();
            using (var t = new GbifDownloadFile(zip))
            {
                // the real titles of this download's datasets, from GBIF
                Dictionary<string, string> dlTitles;
                using (var gb = new GbifClient("WildlifeTools-AddIn test (Jenness Enterprises)"))
                {
                    dlTitles = gb.DownloadDatasetsAsync(Path.GetFileNameWithoutExtension(t.TableName)).GetAwaiter().GetResult();
                }
                r = GbifFeatureWriter.Write(gdbPath, "download", GbifFeatureWriter.FromDownload(t), null, null, default, false, dlTitles);
                Check("GBIF lists the download's datasets with titles, and every row gets one",
                    dlTitles.Count >= 10 && r.DatasetNames.Count == r.ByDataset.Count && r.DatasetNames.Values.Any(v => v.Contains("eBird")),
                    dlTitles.Count + " titles; " + r.DatasetNames.Count + " of " + r.ByDataset.Count + " sources named");
            }
            sw.Stop();
            using (var gdb = new Geodatabase(new FileGeodatabaseConnectionPath(new Uri(gdbPath))))
            using (FeatureClass fc = gdb.OpenDataset<FeatureClass>("download"))
            {
                Check("every record of the download becomes a point",
                    r.Written == expected && fc.GetCount() == expected && r.SkippedNoCoordinates == 0,
                    expected + " rows in " + sw.Elapsed.TotalSeconds.ToString("0.0") + " s; "
                    + r.ByDataset.Count + " datasets; " + r.Truncated + " values cut");
                using (RowCursor cur = fc.Search(new QueryFilter { WhereClause = "species IS NOT NULL AND class_ IS NOT NULL AND license IS NOT NULL AND year IS NOT NULL" }, false))
                {
                    long n = 0;
                    while (cur.MoveNext()) n++;
                    Check("species, class, licence and year are filled on nearly every row", n > expected * 0.95, n.ToString());
                }
            }
            Check("a download's licenses are tallied and written as addresses, not codes",
                r.ByLicense.Keys.All(k => k.StartsWith("http://creativecommons.org/")), string.Join(", ", r.ByLicense.Keys));
            Check("licence tally adds up", r.ByLicense.Values.Sum() == expected,
                string.Join(", ", r.ByLicense.Select(kv => kv.Key.Replace("http://creativecommons.org/", "") + "=" + kv.Value)));
        }

        // ---- 3. one live search page ---------------------------------------------
        Console.WriteLine("A live search page");
        var filter = new GbifFilter();
        filter.TaxonKeys.Add("2482598");
        filter.Polygons.Add(new List<List<(double Lon, double Lat)>> { new List<(double, double)>
            { (-111.80, 35.10), (-111.50, 35.10), (-111.50, 35.40), (-111.80, 35.40) } });
        using (var gbif = new GbifClient("WildlifeTools-AddIn test (Jenness Enterprises)"))
        {
            GbifSearchPage page = gbif.SearchPageAsync(filter, 50, 0).GetAwaiter().GetResult();
            r = GbifFeatureWriter.Write(gdbPath, "search", GbifFeatureWriter.FromSearch(page.Results), filter.Contains);
            Check("50 search records become 50 points inside the box",
                r.Written == 50 && r.SkippedOutsideArea == 0 && r.SkippedNoCoordinates == 0, r.Written.ToString());
            using (var gdb = new Geodatabase(new FileGeodatabaseConnectionPath(new Uri(gdbPath))))
            using (FeatureClass fc = gdb.OpenDataset<FeatureClass>("search"))
            using (RowCursor cur = fc.Search(new QueryFilter { WhereClause = "species = 'Cyanocitta stelleri' AND class_ = 'Aves' AND datasetKey IS NOT NULL AND basisOfRecord IS NOT NULL" }, false))
            {
                long n = 0;
                while (cur.MoveNext()) n++;
                Check("search records fill species, class, dataset and basis of record", n == 50, n.ToString());
            }
        }

        try { Directory.Delete(work, true); } catch { }
        Console.WriteLine();
        Console.WriteLine(passed + " passed, " + failed + " failed");
        return failed == 0 ? 0 : 1;
    }
}
