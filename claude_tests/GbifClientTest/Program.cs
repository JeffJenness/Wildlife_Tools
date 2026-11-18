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

// Headless test of the GBIF client (WildlifeTools/OnlineData). It talks to
// the LIVE GBIF services with a handful of small requests, about a second
// apart, so it needs the internet and is polite to GBIF.
//
//   dotnet run --project claude_tests/GbifClientTest            (no account)
//   dotnet run --project claude_tests/GbifClientTest -- account (also the
//       signed-in calls, with the sign-in in Windows Credential Manager;
//       lists and reads downloads, never requests or cancels one)
//
// Counts drift as GBIF grows, so the checks use floors and relations, not
// exact totals.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using WildlifeTools.OnlineData;
using WildlifeTools.OnlineData.Gbif;

internal static class Program
{
    private static int passed, failed;

    private static void Check(string label, bool ok, string detail = null)
    {
        if (ok) passed++; else failed++;
        Console.WriteLine((ok ? "  ok    " : "  FAIL  ") + label + (detail == null ? "" : "  [" + detail + "]"));
    }

    private static List<(double, double)> Circle(int n, double cx, double cy, double r, bool ccw)
    {
        var pts = new List<(double, double)>();
        for (int i = 0; i < n; i++)
        {
            double a = 2 * Math.PI * i / n;
            pts.Add((cx + r * Math.Cos(a), cy + r * Math.Sin(a)));
        }
        if (!ccw) pts.Reverse();
        return pts;
    }

    private static async Task Pause() => await Task.Delay(1200);

    private static async Task<int> Main(string[] args)
    {
        bool account = args.Any(a => a.Equals("account", StringComparison.OrdinalIgnoreCase));

        // ---- 1. the filter, offline ---------------------------------------
        Console.WriteLine("Filter (offline)");
        var f = new GbifFilter();
        Check("empty filter still asks for mappable records",
            f.ToPredicateJson().Contains("HAS_COORDINATE"));
        f.TaxonKeys.Add("2482598");
        var outerCw = Circle(60, -111.65, 35.25, 0.15, false);      // deliberately clockwise
        var holeCcw = Circle(30, -111.65, 35.25, 0.05, true);       // deliberately counter-clockwise
        f.Polygons.Add(new List<List<(double Lon, double Lat)>> { outerCw });
        string wkt = f.ToWkt();
        Check("one polygon -> POLYGON", wkt.StartsWith("POLYGON(("));
        Check("ring is closed and counted closed", f.PolygonPointCount() == 61, f.PolygonPointCount().ToString());
        // the first two points of the written ring must turn counter-clockwise about the center
        string[] p = wkt.Substring(9).TrimEnd(')').Split(',');
        double x0 = double.Parse(p[0].Split(' ')[0]) + 111.65, y0 = double.Parse(p[0].Split(' ')[1]) - 35.25;
        double x1 = double.Parse(p[1].Split(' ')[0]) + 111.65, y1 = double.Parse(p[1].Split(' ')[1]) - 35.25;
        Check("clockwise outer ring is written counter-clockwise", (x0 * y1) - (x1 * y0) > 0);

        var withHole = f.Clone();
        withHole.Polygons.Clear();
        withHole.Polygons.Add(new List<List<(double Lon, double Lat)>> { outerCw, holeCcw });

        var two = f.Clone();
        two.Polygons.Clear();
        two.Polygons.Add(new List<List<(double Lon, double Lat)>> { Circle(40, -111.75, 35.25, 0.05, true) });
        two.Polygons.Add(new List<List<(double Lon, double Lat)>> { Circle(40, -111.55, 35.25, 0.05, true) });
        Check("two polygons -> MULTIPOLYGON", two.ToWkt().StartsWith("MULTIPOLYGON((("));

        var full = f.Clone();
        full.YearFrom = 2015; full.YearTo = 2024;
        full.Months.AddRange(new[] { 5, 6, 7 });
        full.BasisOfRecord.Add("HUMAN_OBSERVATION");
        full.Licenses.AddRange(new[] { "CC0_1_0", "CC_BY_4_0" });
        full.ExcludeGeospatialIssues = true; full.PresentOnly = true;
        string pj = full.ToPredicateJson();
        using (JsonDocument d = JsonDocument.Parse(pj))
        {
            Check("full filter is valid JSON with an 'and' of 9 clauses",
                d.RootElement.GetProperty("type").GetString() == "and"
                && d.RootElement.GetProperty("predicates").GetArrayLength() == 9,
                d.RootElement.GetProperty("predicates").GetArrayLength().ToString());
        }
        var big = new GbifFilter();
        big.Polygons.Add(new List<List<(double Lon, double Lat)>> { Circle(12000, -111.65, 35.25, 0.15, true) });
        Check("12,000-point polygon is flagged as too big for a download", !big.PolygonFitsDownload);
        Check("60-point polygon fits a download", f.PolygonFitsDownload);

        // ---- 2. names, live -------------------------------------------------
        Console.WriteLine("Names (live)");
        using var gbif = new GbifClient("WildlifeTools-AddIn test (Jenness Enterprises)");
        List<GbifTaxon> sug = await gbif.SuggestAsync("Cyanocitta st");
        Check("suggest 'Cyanocitta st' finds Cyanocitta stelleri (key 2482598)",
            sug.Any(t => t.Key == "2482598" && t.Rank == "SPECIES"), string.Join("; ", sug.Take(3)));
        await Pause();
        sug = await gbif.SuggestAsync("Corv", "FAMILY");
        Check("suggest 'Corv' limited to families finds Corvidae",
            sug.Count > 0 && sug.All(t => t.Rank == "FAMILY") && sug.Any(t => t.Key == "5235"));
        await Pause();
        GbifNameMatch m = await gbif.MatchAsync("Cyanocita steleri");
        Check("misspelled name matches Cyanocitta stelleri",
            m.Taxon != null && m.Taxon.Key == "2482598" && m.MatchType != "EXACT",
            m.MatchType + " " + m.Confidence);
        Check("match carries the classification down from the kingdom",
            m.Classification.Count >= 6 && m.Classification[0].Rank == "KINGDOM");
        await Pause();
        m = await gbif.MatchAsync("Qqqzzx notaname");
        Check("nonsense matches nothing", m.Taxon == null || m.MatchType == "NONE", m.MatchType);
        await Pause();
        List<GbifTaxon> kids = await gbif.ChildrenAsync("5235");
        Check("Corvidae has more than 20 genera, Cyanocitta among them",
            kids.Count > 20 && kids.Any(t => t.ScientificName == "Cyanocitta"), kids.Count.ToString());
        await Pause();
        List<GbifTaxon> kingdoms = await gbif.ChildrenAsync(null);
        Check("the root lists the kingdoms, Animalia and Plantae among them",
            kingdoms.Any(t => t.ScientificName == "Animalia") && kingdoms.Any(t => t.ScientificName == "Plantae"),
            string.Join(", ", kingdoms.Select(t => t.ScientificName)));
        await Pause();
        GbifTaxon one = await gbif.TaxonAsync("2482598");
        Check("taxon by key", one.ScientificName.StartsWith("Cyanocitta stelleri") && one.Rank == "SPECIES");
        await Pause();
        List<GbifTaxon> common = await gbif.SearchCommonNameAsync("steller's jay");
        Check("common name 'steller's jay' leads to a Cyanocitta",
            common.Any(t => (t.ScientificName ?? "").StartsWith("Cyanocitta")),
            string.Join("; ", common.Take(3)));
        await Pause();

        // ---- 3. counts, species list, records, live ---------------------------
        Console.WriteLine("Counts and records (live)");
        long n = await gbif.CountAsync(f);
        Check("Steller's Jay in the Flagstaff circle: more than 20,000 records", n > 20000, n.ToString());
        await Pause();
        long nHole = await gbif.CountAsync(withHole);
        Check("the same circle with a hole has fewer", nHole > 0 && nHole < n, nHole.ToString());
        await Pause();
        long nFull = await gbif.CountAsync(full);
        Check("years + months + basis + licence + quality filters narrow it", nFull > 0 && nFull < n, nFull.ToString());
        await Pause();

        // polar bears: one record sits at latitude 0, longitude 0, and GBIF flags it
        var bears = new GbifFilter();
        bears.TaxonKeys.Add("2433451");
        bears.Polygons.Add(new List<List<(double Lon, double Lat)>> { new List<(double, double)> { (-1, -1), (1, -1), (1, 1), (-1, 1) } });
        long nullIsland = await gbif.CountAsync(bears);
        await Pause();
        bears.ExcludeGeospatialIssues = true;
        long nullIslandFiltered = await gbif.CountAsync(bears);
        Check("polar bears at Null Island exist in GBIF and the location filter removes them",
            nullIsland >= 1 && nullIslandFiltered == 0, nullIsland + " -> " + nullIslandFiltered);
        await Pause();
        var world = new GbifFilter();
        world.TaxonKeys.Add("2433451");
        long allBears = await gbif.CountAsync(world);
        await Pause();
        world.ExcludeCentroids = true;
        long noCentroid = await gbif.CountAsync(world);
        Check("the country-center filter is accepted and removes a few records",
            noCentroid > 0 && noCentroid < allBears && allBears - noCentroid < 200, allBears + " -> " + noCentroid);
        await Pause();

        // sources: leaving one dataset out removes exactly its records
        List<GbifFacetCount> src = await gbif.FacetAsync(f, "DATASET_KEY", 50);
        await Pause();
        var without = f.Clone();
        without.ExcludedDatasetKeys.Add(src[0].Name);
        long nWithout = await gbif.CountAsync(without);
        Check("leaving out the largest source removes exactly its records",
            src.Count >= 3 && nWithout == n - src[0].Count, n + " - " + src[0].Count + " = " + nWithout);
        await Pause();
        var info = await gbif.DatasetInfoAsync(src[0].Name);
        Check("a source has a title and a licence", !string.IsNullOrEmpty(info.Title) && !string.IsNullOrEmpty(info.License), info.Title);
        await Pause();

        // spotted owl in Arizona: most records have no coordinates
        var owl = new GbifFilter { Country = "US", StateProvince = "Arizona" };
        owl.TaxonKeys.Add("2497487");
        long owlMapped = await gbif.CountAsync(owl);
        await Pause();
        owl.IncludeNoCoordinates = true;
        long owlAll = await gbif.CountAsync(owl);
        Check("spotted owl in Arizona: including records without coordinates returns many more",
            owlMapped > 0 && owlAll > owlMapped * 2, owlMapped + " with coordinates, " + owlAll + " in all");
        await Pause();
        // with a study area: mapped records from inside it, plus every record without coordinates
        var owlArea = new GbifFilter { Country = "US", StateProvince = "Arizona" };
        owlArea.TaxonKeys.Add("2497487");
        owlArea.Polygons.Add(new List<List<(double Lon, double Lat)>> { new List<(double, double)>
            { (-112.2, 34.6), (-111.0, 34.6), (-111.0, 35.6), (-112.2, 35.6) } });
        long inArea = await gbif.CountAsync(owlArea);
        await Pause();
        owlArea.IncludeNoCoordinates = true;
        long inAreaPlus = await gbif.CountAsync(owlArea);
        Check("study area + no-coordinate records = mapped records inside it plus all the unmapped ones",
            inArea > 0 && inArea < owlMapped && inAreaPlus == inArea + (owlAll - owlMapped),
            inArea + " inside + " + (owlAll - owlMapped) + " without coordinates = " + inAreaPlus);
        await Pause();

        List<GbifFacetCount> states = await gbif.FacetAsync(new GbifFilter { Country = "US", IncludeNoCoordinates = true, TaxonKeys = { "2497487" } }, "STATE_PROVINCE", 50);
        Check("state names are listed as the records write them, Arizona among them",
            states.Any(s => s.Name == "Arizona") && states.Count >= 5, string.Join(", ", states.Take(5).Select(s => s.Name + "=" + s.Count)));
        await Pause();
        var countries = await gbif.CountriesAsync();
        Check("the country list has the United States as US", countries.Count > 200 && countries.Any(c => c.Code == "US" && c.Name.StartsWith("United States")));
        await Pause();
        GbifSearchPage noCoord = await gbif.SearchPageAsync(new GbifFilter { Country = "US", StateProvince = "Arizona", IncludeNoCoordinates = true, TaxonKeys = { "2497487" } }, 300, 0);
        Check("a page of those records includes some with no coordinates at all",
            noCoord.Results.Any(o => !o.Fields.ContainsKey("decimalLatitude")), noCoord.Results.Count(o => !o.Fields.ContainsKey("decimalLatitude")) + " of " + noCoord.Results.Count);
        await Pause();

        var birds = f.Clone();
        birds.TaxonKeys.Clear(); birds.TaxonKeys.Add("212");          // Aves
        List<GbifFacetCount> spp = await gbif.FacetAsync(birds, "SPECIES_KEY", 20);
        Check("species list for all birds: 20 species, largest first, the jay among them",
            spp.Count == 20 && spp[0].Count >= spp[19].Count && spp.Any(s => s.Name == "2482598"),
            spp.Count + " species, top " + spp[0].Name + "=" + spp[0].Count);
        await Pause();
        List<GbifFacetCount> lic = await gbif.FacetAsync(f, "LICENSE", 10);
        Check("licence mix adds up to the count", lic.Sum(x => x.Count) == n,
            string.Join(", ", lic.Select(x => x.Name + "=" + x.Count)));
        await Pause();

        GbifSearchPage page = await gbif.SearchPageAsync(f, 5, 0);
        Check("a page of 5 records", page.Results.Count == 5 && page.Count == n);
        GbifOccurrence o = page.Results[0];
        Check("a record has coordinates inside the circle and the main fields",
            Math.Sqrt(Math.Pow(o.Lon + 111.65, 2) + Math.Pow(o.Lat - 35.25, 2)) <= 0.1501
            && o.Fields.ContainsKey("scientificName")
            && o.Fields.ContainsKey("datasetKey") && o.Fields.ContainsKey("license")
            && o.Fields.ContainsKey("basisOfRecord"),
            o.Lon + ", " + o.Lat + "; " + o.Fields.Count + " fields");
        await Pause();

        var pics = f.Clone();
        pics.WithImagesOnly = true;
        page = await gbif.SearchPageAsync(pics, 3, 0);
        Check("records with photographs carry the link, licence and credit",
            page.Results.Count == 3 && page.Results.All(r => r.Media.Count > 0
                && r.Media[0].Url.StartsWith("http") && !string.IsNullOrEmpty(r.Media[0].License)),
            page.Count + " records with images");
        await Pause();

        string title = await gbif.DatasetTitleAsync("50c9509d-22c7-4a22-a47d-8c48425ef4a7");
        Check("dataset title", title != null && title.Contains("iNaturalist"), title);
        await Pause();

        // ---- 4. errors ------------------------------------------------------
        Console.WriteLine("Errors");
        try
        {
            await gbif.ListDownloadsAsync();
            Check("listing downloads without an account is refused", false);
        }
        catch (GbifException ex)
        {
            Check("listing downloads without an account is refused", ex.IsNotSignedIn, ex.Message);
        }
        Check("413 is explained in plain words",
            GbifClient.Explain((System.Net.HttpStatusCode)413, "The maximum is 10000.").Contains("too large"));
        Check("an HTML error page is not shown to the user",
            !GbifClient.Explain((System.Net.HttpStatusCode)503, "<html><body>VCL failed</body></html>").Contains("<"));

        // ---- 5. the credential store ------------------------------------------
        Console.WriteLine("Credential store");
        const string svc = "GBIF-selftest";
        CredentialStore.Delete(svc);
        Check("nothing stored at first", !CredentialStore.TryRead(svc, out _, out _));
        CredentialStore.Save(svc, "some_user", "päss wörd ☃ 123");
        Check("saved sign-in reads back exactly",
            CredentialStore.TryRead(svc, out string u, out string pw) && u == "some_user"
            && pw == "päss wörd ☃ 123");
        CredentialStore.Save(svc, "other_user", "x");
        Check("saving again replaces it", CredentialStore.UserName(svc) == "other_user");
        Check("delete removes it", CredentialStore.Delete(svc) && !CredentialStore.TryRead(svc, out _, out _));
        Check("deleting again reports nothing to remove", !CredentialStore.Delete(svc));

        // ---- 6. signed-in calls (optional) --------------------------------------
        if (account)
        {
            Console.WriteLine("Account (live, read-only)");
            if (!gbif.LoadStoredAccount())
            {
                Check("a GBIF sign-in is stored for the add-in", false,
                    "none under '" + CredentialStore.TargetName(GbifClient.Service) + "'");
            }
            else
            {
                Check("GBIF accepts the stored sign-in", await gbif.CheckAccountAsync());
                await Pause();
                List<GbifDownload> dl = await gbif.ListDownloadsAsync();
                Check("the account's downloads are listed", dl.Count > 0, dl.Count.ToString());
                GbifDownload done = dl.FirstOrDefault(x => x.IsFinished && x.Format == "SIMPLE_CSV"
                    && x.TotalRecords > 0 && x.TotalRecords < 200000);
                if (done != null)
                {
                    Check("a finished download has a DOI, a link, a filter and an erase date",
                        !string.IsNullOrEmpty(done.Doi) && !string.IsNullOrEmpty(done.DownloadLink)
                        && done.PredicateJson != null && done.EraseAfter.HasValue,
                        done.Key + " " + done.Doi + " " + done.TotalRecords + " records");
                    await Pause();
                    string cite = await gbif.CitationAsync(done.Key);
                    Check("its citation names the DOI", cite != null && cite.Contains(done.Doi), cite);
                    // (fetching the zip file and reading its table is the geoprocessing tool's work, in
                    //  Toolboxes\gbif_occurrences.py, and is tested there)
                }
                var wrong = new GbifClient("WildlifeTools-AddIn test (Jenness Enterprises)");
                wrong.SetAccount(gbif.UserName, "definitely-not-the-password");
                Check("a wrong password is reported as not signed in, not as a crash",
                    !await wrong.CheckAccountAsync());
            }
        }

        Console.WriteLine();
        Console.WriteLine(passed + " passed, " + failed + " failed");
        return failed == 0 ? 0 : 1;
    }
}
