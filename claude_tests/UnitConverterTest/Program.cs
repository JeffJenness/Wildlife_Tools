// Checks of the Unit Converter engine against the real bundled catalog. See the csproj.
using System;
using System.Globalization;
using System.IO;
using System.Linq;
using WildlifeTools.UnitConverter;

internal static class Program
{
    private static int _fails;

    private static void Check(string name, bool ok, string detail = "")
    {
        Console.WriteLine((ok ? "ok    " : "FAIL  ") + name + (ok ? "" : "  " + detail));
        if (!ok) _fails++;
    }

    private static bool Rel(double a, double b, double tol) =>
        Math.Abs(a - b) <= tol * Math.Max(Math.Abs(a), Math.Abs(b)) || Math.Abs(a - b) < 1e-300;

    private static string RepoRoot()
    {
        var d = new DirectoryInfo(AppContext.BaseDirectory);
        while (d != null && !Directory.Exists(Path.Combine(d.FullName, "WildlifeTools"))) d = d.Parent;
        return d?.FullName;
    }

    private static int Main()
    {
        CultureInfo.CurrentCulture = CultureInfo.GetCultureInfo("en-US");
        string root = RepoRoot();
        string bundled = UnitCatalog.BundledPath(Path.Combine(root, "WildlifeTools"));
        UnitCatalog cat = UnitCatalog.Load(bundled, null);
        Check("catalog: 1,214 units in 23 classes", cat.Units.Count == 1214 && cat.Classes.Count == 23,
            cat.Units.Count + " units, " + cat.Classes.Count + " classes");
        Check("aliases: FeetUS and Foot_US find the US survey foot", cat.Find("FeetUS")?.Name == "feet (US Survey)" && cat.Find("foot_us")?.Name == "feet (US Survey)");
        Check("aliases: the search box finds AcresUS", cat.UnitsFor(null, "AcresUS").Any(u => u.Name == "acres (US survey)"));
        Check("international watt = 1.000165 W", Math.Abs(UnitCatalog.Convert(1, cat.Find("watts (Int)"), cat.Find("watts")) - 1.000165) < 1e-15);
        Check("a knot = 1.852 km/h exactly", UnitCatalog.Convert(1, cat.Find("knots"), cat.Find("kilometers/hr")) == 1.852);
        // ---- the classes added 2026-10-07
        Check("trees: 100 per acre = 247.105 per hectare", Rel(C(100, "count per acre", "count per hectare"), 247.10538146716533, 1e-13));
        Check("wood: 1,000 cu ft per acre = 69.97 cu m per hectare", Rel(C(1000, "cu feet per acre", "cu meters per hectare"), 69.972451790633609, 1e-13));
        Check("fuel: 1 ton per acre = 2.2417 tonnes per hectare", Rel(C(1, "tons per acre", "tonnes per hectare"), 2000 * 0.45359237 / 0.40468564224 / 1000, 1e-13));
        Check("density: 62.428 lb/ft3 = 1 g/cm3", Rel(C(1, "grams per cu centimeter", "pounds per cu foot"), 62.427960576144606, 1e-13));
        Check("fire: 1 BTU/ft/s = 3.4615 kW/m", Rel(C(1, "BTU per foot per second", "kilowatts per meter"), 1055.05585262 / 0.3048 / 1000, 1e-13));
        Check("fire: 1 langley = 41.84 kJ/m2", Rel(C(1, "langleys", "kilojoules per sq meter"), 41.84, 1e-14));
        Check("rate of spread: 1 chain/hr = 20.1168 m/hr", Rel(C(1, "chains/hr", "meters/hr"), 20.1168, 1e-14));
        Check("a cunit is 100 cubic feet", Rel(C(1, "cunits", "cu. feet"), 100, 1e-14));
        Check("MBF is 1,000 board feet", Rel(C(1, "MBF (thousand board feet)", "board feet"), 1000, 1e-14));
        Check("find by class: 'forestry' lists every forestry class",
              new[] { "Stand Basal Area (Forestry)", "Tree Diameter and Basal Area (Forestry)" }.All(c => cat.UnitsFor(null, "forestry").Count(u => u.ClassName == c) == cat.UnitsFor(c, "").Count));
        Check("find by class: 'stand basal' lists the whole Stand Basal Area class",
              cat.UnitsFor(null, "stand basal").Count(u => u.ClassName == "Stand Basal Area (Forestry)") == cat.UnitsFor("Stand Basal Area (Forestry)", "").Count);
        // ---- animal unit months: NDSU Extension R1810's worked examples
        Check("R1810 Ex. 1: 100 1,200-lb cows with calves for 5.5 months = 632.5 AUM",
              Rel(C(100 * 5.5, "cattle months (cow with calf, 1,200 lb; NDSU)", "animal unit months"), 632.5, 1e-13));
        Check("R1810 Ex. 1: 4 mature bulls for 2.5 months = 14.0 AUM at Table 1's 1.40 (the example's 15.0 uses the bison bull's 1.50)",
              Rel(C(4 * 2.5, "cattle months (mature bull; NDSU)", "animal unit months"), 14.0, 1e-13));
        Check("R1810 Ex. 2: 250 ewes with lambs for 5 months = 250 AUM", Rel(C(250 * 5, "sheep months (mature, with lamb; NDSU)", "animal unit months"), 250, 1e-13));
        Check("R1810 Ex. 2: 5 rams for 2 months = 2.5 AUM", Rel(C(5 * 2, "sheep months (ram; NDSU)", "animal unit months"), 2.5, 1e-13));
        Check("R1810 Ex. 5: 474,000 lb of available forage = 519.2 AUM",
              Math.Round(C(474000, "pounds of air-dried forage (913 lb/AUM)", "animal unit months"), 1) == 519.2);
        Check("an elk month is 3 mule deer months", Rel(C(1, "elk months (mature; NDSU)", "mule deer months (mature; NDSU)"), 3, 1e-13));
        // mule deer per cow, within each source (Jeff, 2026-10-07)
        Check("NRCS: one cow-with-calf month = 5 mule deer months", Rel(C(1, "cattle months (cow with calf; NRCS)", "mule deer months (mature; NRCS)"), 5, 1e-13));
        Check("Vallentine: one mature cow month = 1/0.23 mule deer months", Rel(C(1, "cattle months (mature cow, non-lactating; Vallentine)", "mule deer months (mature; Vallentine)"), 1 / 0.23, 1e-13));
        Check("Holechek (NMSU B-829): one 1,000-lb cow month = 6.67 mule deer months of 150 lb", Rel(C(1, "cattle months (mature, 1,000 lb; Holechek)", "mule deer months (150 lb; Holechek)"), 1 / 0.15, 1e-13));
        Check("Arizona Table 1: 4.67 mule deer of 125 lb per animal unit", Rel(C(1, "cattle months (cow, 1,000 lb; Arizona)", "mule deer months (125 lb; Arizona)"), 4.67, 1e-13));
        Check("Holechek: a 1,200-lb moose month = 1.2 AUM", Rel(C(1, "moose months (1,200 lb; Holechek)", "animal unit months"), 1.2, 1e-14));
        Check("find 'mule deer' lists one unit per source (5)", cat.UnitsFor(null, "mule deer").Count == 5);
        Check("Vallentine units cite Rangelands Gateway", cat.Units.Where(u => u.Name.EndsWith("; Vallentine)")).All(u => u.Sources.Contains("vallentine1990") && (u.Sources.Contains("rg_au") || u.Sources.Contains("rg_aue"))));
        Check("Arizona (Ruyle and Ogden 1993): 8,000 lb of air-dried forage = 10 AUM",
              Rel(C(8000, "pounds of air-dried forage (800 lb/AUM)", "animal unit months"), 10, 1e-13));
        Check("Wyoming B-1320: 1 AUM = 750 lb of air-dried forage", Rel(C(1, "animal unit months", "pounds of air-dried forage (750 lb/AUM)"), 750, 1e-13));
        Check("NRCS: 1 AUM = 790 lb of oven-dry forage", Rel(C(1, "animal unit months", "pounds of oven-dry forage (790 lb/AUM)"), 790, 1e-13));
        Check("SRM: 1 AUM = 780 lb of oven-dry forage", Rel(C(1, "animal unit months", "pounds of oven-dry forage (780 lb/AUM)"), 780, 1e-13));
        Check("NRCS: 365 animal unit days = 1 animal unit year", Rel(C(365, "animal unit days", "animal unit years"), 1, 1e-13));
        Check("fees: 100 horses for 2 months = 200 head months = 200 AUM (NDSU: 250)",
              Rel(C(200, "head months (federal grazing fees)", "animal unit months"), 200, 1e-14) && Rel(C(200, "horse months (mature; NDSU)", "animal unit months"), 250, 1e-13));
        Check("fees: 5 sheep for a month = 1 head month", Rel(C(5, "sheep or goat months (federal grazing fees)", "head months (federal grazing fees)"), 1, 1e-14));
        Check("the international nautical league is 5,556 m exactly", C(1, "leagues (Int)", "meters") == 5556);
        Check("the Harvard Bridge: 364.4 smoots = 620.13592 m", Rel(C(364.4, "smoots", "meters"), 620.13592, 1e-13));
        Check("sources: the smoot and the Texas vara cite their own sources, not the SI",
              cat.Find("smoots").Sources.Contains("ahd_smoot") && !cat.Find("smoots").Sources.Contains("bipm_si")
              && cat.Find("varas (Texas)").Sources.Contains("tx_nrc_21077") && !cat.Find("varas (Texas)").Sources.Contains("bipm_si"));
        Check("sources: the international nautical league is not an electrical unit", !cat.Find("leagues (Int)").Sources.Contains("crc62"));
        Check("sources: the U.S. survey units cite the 1959 and 2020 notices",
              cat.Units.Where(u => u.Name.Contains("(US survey)")).All(u => u.Sources.Contains("fr1959") && u.Sources.Contains("fr2020")));
        Check("every unit has a source", cat.Units.All(u => u.Sources.Count > 0));
        Check("NIST: a U.S. bushel is 2150.42 cubic inches", Rel(C(1, "bushels (US level)", "cu. inches"), 2150.42, 1e-13));
        Check("NIST: a slug is one pound-force per foot per second squared", Rel(C(1, "slugs", "kilograms"), 4.4482216152605 / 0.3048, 1e-13));
        Check("NIST: a poundal is one pound-foot per second squared", Rel(C(1, "poundals", "newtons"), 0.45359237 * 0.3048, 1e-13));
        Check("every source is in the citation list", cat.Units.All(u => u.Sources.All(s => cat.Citations.ContainsKey(s))));
        // Jeff, 2026-10-08: the original descriptions are credited to the original program, not to the standard
        Check("acres: value from NIST Handbook 44, description from the OED and the original Unit Converter",
              cat.SourcesText(cat.Find("acres")) == "Value: NIST Handbook 44 (2026), Appendix C. Description: OED, acre, n.; the original Unit Converter (Jenness 2001).",
              cat.SourcesText(cat.Find("acres")));
        Check("barn: description from its coiners (Holloway and Baker 1972), value from NIST",
              cat.SourcesText(cat.Find("barns")) == "Value: NIST SP 811 (2008), Appendix B. Description: Holloway and Baker (1972), How the barn was born; the original Unit Converter (Jenness 2001)."
              && cat.Find("barns").Definition.Contains("as big as a barn"), cat.SourcesText(cat.Find("barns")));
        // Jeff, 2026-10-08: OED citations and corrections (catalog_text_fixes.py)
        Check("OED: 235 units cite an OED entry for their description", cat.Units.Count(u => u.DescriptionSources.Any(s => s.StartsWith("oed_"))) == 235);
        Check("OED: tun is Old English, not from French thunder", cat.Find("tuns").Definition.Contains("Old English (tunne)") && !cat.Units.Any(u => u.Definition.Contains("thunder")));
        Check("Fahrenheit 1686-1736; Celsius is Anders; the triple point is 0.01 C",
              cat.Find("degrees Fahrenheit").Definition.Contains("(1686-1736)") && cat.Find("degrees Celsius").Definition.Contains("Anders Celsius")
              && cat.Find("degrees Celsius").Definition.Contains("0.01 degrees Celsius"));
        Check("Kelvin: William Thomson, Baron Kelvin", cat.Find("Kelvin").Definition.Contains("William Thomson, Baron Kelvin (1824-1907)"));
        // Jeff, 2026-10-08: the circular units spelled out (no aliases: not released yet)
        Check("circular meter (CA): 640 sq cm = 0.0815 circular meters, 0.2855 m across",
              Rel(C(640, "sq cm (CA)", "circular meters (CA)"), 0.064 / (Math.PI / 4), 1e-12) && Rel(C(640, "sq cm (CA)", "meters diameter"), 2 * Math.Sqrt(0.064 / Math.PI), 1e-12));
        Check("no unit is still named cir.", !cat.Units.Any(u => u.Name.StartsWith("cir.") || u.Singular.StartsWith("cir.") || u.Aliases.Any(a => a.StartsWith("cir."))));
        Check("circular inches (Area) and circular inches (CA) are different units", cat.Find("circular inches").ClassName == "Area" && cat.Find("circular inches (CA)").ClassName == "Circle Area and Diameter");
        Check("Ramsden: renamed, and the old name still finds it", cat.Find("links (Ramsden)") != null && cat.Find("links (Ramden)")?.Name == "links (Ramsden)");
        Check("mile: the cut-off sentence is whole again", cat.Find("circular miles (CA)").Definition.Contains("equivalent to 1,000 paces") && cat.Find("leagues (Int)").Definition.Contains("equivalent to 1,000 paces"));
        Check("anomalistic millennium named right", cat.Find("millennia (anom.)").Definition.StartsWith("An anomalistic millennium is"));
        Check("no 2001 misspellings left", !cat.Units.Any(u => System.Text.RegularExpressions.Regex.IsMatch(u.Definition, @"Galeta|tunnerre|twelth|Ramden|mathmet|mathemet|occuring|divisable|calender|Andres|apothecarie's|It's use|\ba an\b")));
        Check("every description source is in the citation list", cat.Units.All(u => u.DescriptionSources.All(s => cat.Citations.ContainsKey(s))));
        Check("every 2001 description is credited to the original program (1,005 units)",
              cat.Units.Count(u => !u.IsUser && (u.DescriptionSources.Contains("jenness_uc") || (u.Sources.Count == 1 && u.Sources[0] == "jenness_uc"))) == 1005);
        Check("rewritten descriptions keep their own sources: joules (Int)",
              cat.Find("joules (Int)").DescriptionSources.Count == 0 && cat.SourcesText(cat.Find("joules (Int)")).StartsWith("Sources: CRC"));
        Check("a user unit cannot take an ArcGIS alias as its name", cat.PutUserUnit(new UnitDef { Name = "FeetUS", RefName = "meters", DefinedFactor = 1 }, null) != null);
        Check("every class's base unit is in the catalog", cat.Classes.All(c => cat.Find(c.BaseUnit) != null));
        Check("every unit has a definition", cat.Units.All(u => !string.IsNullOrWhiteSpace(u.Definition)));
        // Jeff, 2026-10-08: the mile's word-origin sentence appeared twice
        Check("no definition repeats a sentence", cat.Units.All(u =>
        {
            var s = System.Text.RegularExpressions.Regex.Split(u.Definition, @"(?<=[.!?])\s+(?=[""A-Z0-9(])")
                .Select(x => string.Join(" ", x.ToLowerInvariant().Split((char[])null, StringSplitOptions.RemoveEmptyEntries)))
                .Where(x => x.Length > 12).ToList();
            return s.Count == s.Distinct().Count();
        }));
        Check("the 2001 typo Internation is fixed", cat.Units.All(u => !System.Text.RegularExpressions.Regex.IsMatch(u.Definition, @"\bInternation\b")));

        double C(double x, string a, string b) => UnitCatalog.Convert(x, cat.Find(a), cat.Find(b));

        // ---- known values (exact definitions)
        Check("1 inch = 25.4 millimeters", Rel(C(1, "inches", "millimeters"), 25.4, 1e-14));
        Check("1 mile = 1609.344 meters", Rel(C(1, "miles", "meters"), 1609.344, 1e-14));
        Check("640 acres = 1 square mile", Rel(C(640, "acres", "sq miles"), 1, 1e-14));
        Check("1 acre = 0.40468564224 hectares", Rel(C(1, "acres", "hectares"), 0.40468564224, 1e-14));
        Check("1 US gallon = 3.785411784 liters", Rel(C(1, "gallons (US fluid)", "liters"), 3.785411784, 1e-14));
        Check("1 imperial gallon = 4.54609 liters", Rel(C(1, "gallons (Brit)", "liters"), 4.54609, 1e-14));
        Check("1 pound = 0.45359237 kilograms", Rel(C(1, "pounds", "kilograms"), 0.45359237, 1e-14));
        Check("1 pound-force = 4.4482216152605 newtons", Rel(C(1, "pounds-force (avdp)", "newtons"), 4.4482216152605, 1e-13));
        Check("1 US survey foot = 1200/3937 m", Rel(C(1, "feet (US Survey)", "meters"), 1200.0 / 3937.0, 1e-14));
        Check("1 mile/hr = 1.609344 km/hr", Rel(C(1, "miles/hr", "kilometers/hr"), 1.609344, 1e-14));

        // ---- temperature (affine)
        Check("32 F = 0 C", Math.Abs(C(32, "degrees Fahrenheit", "degrees Celsius")) < 1e-12);
        Check("212 F = 100 C", Rel(C(212, "degrees Fahrenheit", "degrees Celsius"), 100, 1e-14));
        Check("-40 F = -40 C", Rel(C(-40, "degrees Fahrenheit", "degrees Celsius"), -40, 1e-14));
        Check("0 C = 273.15 K", Rel(C(0, "degrees Celsius", "Kelvin"), 273.15, 1e-14));
        Check("0 R = 0 K", Math.Abs(C(0, "degrees Rankine", "Kelvin")) < 1e-9);
        Check("100 C = 80 Reaumur", Rel(C(100, "degrees Celsius", "degrees Réaumur"), 80, 1e-14));

        // ---- slope and angles
        Check("100% slope = 45 degrees", Rel(C(100, "% (Percent) Slope", "circ. degrees"), 45, 1e-14));
        Check("the old name % (slope) still finds % (Percent) Slope", cat.Find("% (slope)")?.Name == "% (Percent) Slope");
        Check("a search for 'percent' finds % (Percent) Slope", cat.UnitsFor(null, "percent").Any(u => u.Name == "% (Percent) Slope"));
        Check("45 degrees = 100% slope", Rel(C(45, "circ. degrees", "% (Percent) Slope"), 100, 1e-13));
        Check("pi radians = 180 degrees", Rel(C(Math.PI, "radians", "circ. degrees"), 180, 1e-14));
        Check("10% slope = 5.7106 degrees", Rel(C(10, "% (slope)", "circ. degrees"), Math.Atan(0.1) * 180 / Math.PI, 1e-14));

        // ---- circles: diameter <-> area
        Check("a circle 1 m across has pi/4 sq m", Rel(C(1, "meters diameter", "sq m (CA)"), Math.PI / 4, 1e-14));
        Check("a circle of pi/4 sq m is 1 m across", Rel(C(Math.PI / 4, "sq m (CA)", "meters diameter"), 1, 1e-14));
        Check("a circle 12 in across has pi/4 sq ft", Rel(C(12, "inches diameter", "sq ft (CA)"), Math.PI / 4, 1e-13));
        Check("10 in dbh = 0.5454 sq ft basal area", Rel(C(10, "inches dbh", "sq ft (BA/tree)"), Math.PI * (10.0 / 12 / 2) * (10.0 / 12 / 2), 1e-13));
        Check("diameter in cm vs feet is linear", Rel(C(30.48, "centimeters dbh", "feet dbh"), 1, 1e-13));
        Check("a negative area has no diameter", double.IsNaN(C(-1, "sq m (CA)", "meters diameter")));

        // ---- basal area
        Check("1 sq ft/acre = 0.2296 sq m/ha", Rel(C(1, "BA (sq ft/acre)", "BA (sq m/hectare)"), 0.09290304 / 0.40468564224, 1e-13));
        Check("a tally of 3 with a 10 BAF prism = 30 sq ft/acre", Rel(C(3, "`in` (10BAF-US)", "BA (sq ft/acre)"), 30, 1e-13));

        // ---- round trips through every unit
        int bad = 0;
        string worst = "";
        foreach (UnitDef u in cat.Units)
        {
            UnitDef b = cat.Find(cat.FindClass(u.ClassName).BaseUnit);
            foreach (double x in new[] { 1.0, 37.25, 0.001 })
            {
                double back = UnitCatalog.Convert(UnitCatalog.Convert(x, u, b), b, u);
                // an offset (Kelvin, Rankine) costs absolute, not relative, precision near 0
                if (!Rel(back, x, 1e-11) && Math.Abs(back - x) > 1e-12) { bad++; worst = u.Name + " " + x + " -> " + back; }
            }
        }
        Check("round trip x -> base -> x for every unit", bad == 0, bad + " failures, e.g. " + worst);
        Check("converting between classes is refused", Throws(() => C(1, "feet", "liters")));

        // ---- the user's own units and classes
        string tmp = Path.Combine(Path.GetTempPath(), "uc_test_" + Guid.NewGuid().ToString("N") + ".json");
        Check("new user unit: 1 rod-ish = 16.5 feet", cat.PutUserUnit(new UnitDef { Name = "test rods", Singular = "test rod", RefName = "feet", DefinedFactor = 16.5, Definition = "a test" }, null) == null);
        Check("user unit converts through its reference", Rel(C(2, "test rods", "meters"), 2 * 16.5 * 0.3048, 1e-14));
        Check("user unit defined against a user unit", cat.PutUserUnit(new UnitDef { Name = "test furlongs", Singular = "test furlong", RefName = "test rods", DefinedFactor = 40 }, null) == null
                                                            && Rel(C(1, "test furlongs", "feet"), 660, 1e-14));
        Check("a clashing name is refused", cat.PutUserUnit(new UnitDef { Name = "Feet", RefName = "meters", DefinedFactor = 1 }, null) != null);
        Check("a zero factor is refused", cat.PutUserUnit(new UnitDef { Name = "zeroes", RefName = "meters", DefinedFactor = 0 }, null) != null);
        Check("an offset on a circle unit is refused", cat.PutUserUnit(new UnitDef { Name = "odd circles", RefName = "meters diameter", DefinedFactor = 1, DefinedOffset = 2 }, null) != null);
        Check("user unit on a curve: 1 test-yard diameter = 3 ft diameter", cat.PutUserUnit(new UnitDef { Name = "test yards diameter", RefName = "feet diameter", DefinedFactor = 3 }, null) == null
                                                            && Rel(C(1, "test yards diameter", "sq ft (CA)"), Math.PI / 4 * 9, 1e-13));
        Check("user affine unit: degrees Delisle-ish", cat.PutUserUnit(new UnitDef { Name = "test degrees", RefName = "degrees Celsius", DefinedFactor = 2, DefinedOffset = 10 }, null) == null
                                                            && Rel(C(5, "test degrees", "degrees Celsius"), 20, 1e-14) && Rel(C(20, "degrees Celsius", "test degrees"), 5, 1e-14));
        Check("a unit with dependents cannot be deleted", cat.RemoveUserUnit(cat.Find("test rods")) != null);
        Check("a bundled unit cannot be deleted", cat.RemoveUserUnit(cat.Find("feet")) != null);
        Check("new user class with its base unit", cat.AddUserClass("Test Money", "test dollars", "test dollar", "money") == null);
        Check("a unit in the user class", cat.PutUserUnit(new UnitDef { Name = "test cents", RefName = "test dollars", DefinedFactor = 0.01 }, null) == null
                                         && Rel(C(250, "test cents", "test dollars"), 2.5, 1e-14));
        cat.Saved.Add(new SavedConversion { Name = "acres to hectares", ClassName = "Area", From = "acres", To = "hectares" });
        cat.SaveUser(tmp);
        UnitCatalog again = UnitCatalog.Load(bundled, tmp);
        Check("saved and reloaded: user units and classes come back", again.Find("test furlongs") != null && again.Find("test cents") != null
                                                                     && again.FindClass("Test Money") != null && again.Units.Count == cat.Units.Count, string.Join("; ", again.Warnings));
        Check("reloaded user unit converts the same", Rel(UnitCatalog.Convert(1, again.Find("test furlongs"), again.Find("feet")), 660, 1e-14));
        Check("reloaded saved conversion", again.Saved.Count == 1 && again.Saved[0].To == "hectares");
        // edit a unit others depend on
        UnitDef rods = again.Find("test rods");
        Check("editing a unit keeps the units defined against it", again.PutUserUnit(new UnitDef { Name = "test perches", Singular = "test perch", RefName = "feet", DefinedFactor = 16.5 }, rods) == null
                                                                && Rel(UnitCatalog.Convert(1, again.Find("test furlongs"), again.Find("feet")), 660, 1e-14)
                                                                && again.Find("test rods") == null);
        Check("deleting the leaf, then its reference", again.RemoveUserUnit(again.Find("test furlongs")) == null && again.RemoveUserUnit(again.Find("test perches")) == null);
        Check("a class base with units cannot be deleted", again.RemoveUserUnit(again.Find("test dollars")) != null);
        File.Delete(tmp);

        // ---- reading numbers
        double v;
        Check("parse 1/4", NumberText.TryParse("1/4", out v) && v == 0.25);
        Check("parse 5 1/8", NumberText.TryParse("5 1/8", out v) && v == 5.125);
        Check("parse -5 1/8", NumberText.TryParse("-5 1/8", out v) && v == -5.125);
        Check("parse 1.5e3", NumberText.TryParse("1.5e3", out v) && v == 1500);
        Check("parse 1,234.5", NumberText.TryParse("1,234.5", out v) && v == 1234.5);
        Check("refuse 1/0", !NumberText.TryParse("1/0", out v));
        Check("refuse 5 1/", !NumberText.TryParse("5 1/", out v));
        Check("refuse text", !NumberText.TryParse("abc", out v));

        // ---- writing numbers
        Check("format 258.998811 with 4 places", NumberText.Format(258.99881103, 4, false) == "258.9988", NumberText.Format(258.99881103, 4, false));
        Check("format 1234567.891 with 2 places", NumberText.Format(1234567.891, 2, false) == "1,234,567.89");
        Check("scientific 0.000123 with 3 places", NumberText.Format(0.000123, 3, true) == "1.230E-4", NumberText.Format(0.000123, 3, true));
        Check("scientific 6.02e23", NumberText.Format(6.02e23, 2, true) == "6.02E+23", NumberText.Format(6.02e23, 2, true));
        Check("readable 0.40468564224", NumberText.Readable(0.40468564224) == "0.404 685 642 24", NumberText.Readable(0.40468564224));
        Check("readable 2.47105381467", NumberText.Readable(C(1, "hectares", "acres")) == "2.471 053 814 67", NumberText.Readable(C(1, "hectares", "acres")));
        Check("readable 43560", NumberText.Readable(43560) == "43,560");
        Check("prefer scientific for tiny and huge", NumberText.PreferScientific(1e-7) && NumberText.PreferScientific(3e14) && !NumberText.PreferScientific(258.9));

        // ---- finding
        Check("search 'acre' finds acres and acre-feet", cat.UnitsFor(null, "acre").Any(u => u.Name == "acres") && cat.UnitsFor(null, "acre").Any(u => u.Name.StartsWith("acre-f")));
        Check("class filter: Temperature has 6 units", cat.UnitsFor("Temperature", "").Count(u => !u.IsUser) == 6);
        Check("renamed classes: Length and Distance, Angle and Slope", cat.FindClass("Length and Distance") != null && cat.FindClass("Angle and Slope") != null && cat.FindClass("Length") == null);
        Check("two-word search", cat.UnitsFor(null, "sq feet").All(u => u.Name.Contains("sq") && u.Name.Contains("feet")));

        Console.WriteLine();
        Console.WriteLine("UNIT CONVERTER ENGINE: " + _fails + " failed");
        return _fails == 0 ? 0 : 1;
    }

    private static bool Throws(Action a)
    {
        try { a(); return false; } catch (InvalidOperationException) { return true; }
    }
}
