// Checks of WildlifeTools.About.AboutInfo and UpdateCheck, compiled from
// source (no ArcGIS): the DAML version parse, the release comparison, the
// version line, the citation, the GitHub JSON parse, and the Config.daml
// lookup beside a DLL. Ends with one live question to GitHub (not a check:
// it prints what it gets, or that it got nothing).
//   dotnet run --project claude_tests/AboutInfoTest

using System;
using System.IO;
using WildlifeTools.About;

internal static class Program
{
    private static int _pass, _fail;

    private static void Check(string name, bool ok, string detail = null)
    {
        if (ok) { _pass++; }
        else { _fail++; Console.WriteLine("FAIL: " + name + (detail == null ? "" : "  [" + detail + "]")); }
    }

    private static void Same(string name, string got, string want) =>
        Check(name, got == want, "got <" + got + "> want <" + want + ">");

    private static int Main()
    {
        // ---- Config.daml version ------------------------------------------
        const string Daml = "<?xml version=\"1.0\"?>\n<ArcGIS>\n  <AddInInfo id=\"{x}\" version=\"1.112.60.0\" desktopVersion=\"3.7\">\n";
        Same("daml version", AboutInfo.ParseDamlVersion(Daml), "1.112.60.0");
        Same("daml version: xml decl not taken", AboutInfo.ParseDamlVersion("<?xml version=\"1.0\"?><ArcGIS/>"), null);
        Same("daml version: empty", AboutInfo.ParseDamlVersion(""), null);

        string tmp = Path.Combine(Path.GetTempPath(), "about_test_" + Guid.NewGuid().ToString("N"));
        try
        {
            // installed layout: <addin>\Config.daml + <addin>\Install\WildlifeTools.dll
            Directory.CreateDirectory(Path.Combine(tmp, "Install"));
            File.WriteAllText(Path.Combine(tmp, "Config.daml"), Daml);
            string dll = Path.Combine(tmp, "Install", "WildlifeTools.dll");
            File.WriteAllText(dll, "x");
            Same("installed layout", AboutInfo.ReadInstalledVersion(dll), "1.112.60.0");
            // build-output layout: both in one folder
            string flat = Path.Combine(tmp, "flat");
            Directory.CreateDirectory(flat);
            File.WriteAllText(Path.Combine(flat, "Config.daml"), Daml.Replace("1.112.60.0", "9.9.9.9"));
            Same("flat layout", AboutInfo.ReadInstalledVersion(Path.Combine(flat, "WildlifeTools.dll")), "9.9.9.9");
            // two levels down: neither the folder nor its parent holds a Config.daml
            Same("no daml", AboutInfo.ReadInstalledVersion(Path.Combine(tmp, "nowhere", "deeper", "x.dll")), null);
            Same("null path", AboutInfo.ReadInstalledVersion(null), null);
        }
        finally
        {
            try { Directory.Delete(tmp, true); } catch (Exception) { }
        }

        // ---- release comparison -------------------------------------------
        Same("tag strip", AboutInfo.TagVersion("v1.2.3.4"), "1.2.3.4");
        Same("tag strip: none", AboutInfo.TagVersion("1.2.3.4"), "1.2.3.4");
        Same("tag strip: null", AboutInfo.TagVersion(null), null);
        Check("same", AboutInfo.Compare("1.112.60.0", "v1.112.60.0") == UpdateStatus.Current);
        Check("newer", AboutInfo.Compare("1.112.60.0", "v1.113.61.0") == UpdateStatus.NewerAvailable);
        Check("newer by 4th part", AboutInfo.Compare("1.112.60.0", "v1.112.61.1") == UpdateStatus.NewerAvailable);
        Check("ahead", AboutInfo.Compare("1.112.60.0", "v1.111.58.2") == UpdateStatus.Ahead);
        Check("3 vs 4 parts equal", AboutInfo.Compare("1.112.60", "v1.112.60.0") == UpdateStatus.Current);
        Check("no tag", AboutInfo.Compare("1.112.60.0", null) == UpdateStatus.Unknown);
        Check("no version", AboutInfo.Compare(null, "v1.0.0.0") == UpdateStatus.Unknown);
        Check("garbage", AboutInfo.Compare("1.112.60.0", "latest") == UpdateStatus.Unknown);
        Same("msg current", AboutInfo.UpdateMessage(UpdateStatus.Current, "1.112.60.0"), "This is the newest version.");
        Same("msg newer", AboutInfo.UpdateMessage(UpdateStatus.NewerAvailable, "1.113.61.0"), "Version 1.113.61.0 is available.");
        Same("msg ahead", AboutInfo.UpdateMessage(UpdateStatus.Ahead, "1.111.58.2"), "This build is newer than the current release (1.111.58.2).");
        Same("msg unknown", AboutInfo.UpdateMessage(UpdateStatus.Unknown, null), "Could not check for a newer version.");

        // ---- version line, citation, Pro version -----------------------------
        var built = new DateTime(2026, 10, 13, 15, 0, 0);
        Same("version line", AboutInfo.VersionLine("1.112.60.0", built, "3.7.1"),
            "Version 1.112.60.0, built October 13, 2026, running in ArcGIS Pro 3.7.1");
        Same("version line: unknowns", AboutInfo.VersionLine(null, null, null), "Version unknown");
        Same("citation", AboutInfo.Citation("1.112.60.0", built),
            "Jenness, J. 2026. Wildlife and Forestry Tools add-in for ArcGIS Pro, v. 1.112.60.0 (October 2026). "
            + "Jenness Enterprises, Flagstaff, Arizona. Available at: https://github.com/JeffJenness/Wildlife_Tools.");
        Same("pro version 4 parts", AboutInfo.ShortProVersion("3.7.1.59326"), "3.7.1");
        Same("pro version 2 parts", AboutInfo.ShortProVersion("3.7"), "3.7");
        Same("pro version null", AboutInfo.ShortProVersion(null), null);
        Same("pro version commit suffix", AboutInfo.ShortProVersion("1.0.0+cdcd42db0e4b"), "1.0.0");
        Same("pro version label", AboutInfo.ShortProVersion("3.7.1.59326 (Release)"), "3.7.1");
        Same("pro version no digits", AboutInfo.ShortProVersion("beta"), null);
        Same("assembly version stamped", AboutInfo.AssemblyVersionString(new Version(1, 112, 60, 0)), "1.112.60.0");
        Same("assembly version default", AboutInfo.AssemblyVersionString(new Version(1, 0, 0, 0)), null);
        Same("assembly version zero", AboutInfo.AssemblyVersionString(new Version(0, 0, 0, 0)), null);
        Same("assembly version null", AboutInfo.AssemblyVersionString(null), null);

        // ---- GitHub JSON -----------------------------------------------------
        Same("json tag", UpdateCheck.ParseTag("{\"url\":\"x\",\"tag_name\":\"v1.111.58.2\",\"assets\":[]}"), "v1.111.58.2");
        Same("json no tag", UpdateCheck.ParseTag("{\"message\":\"Not Found\"}"), null);
        Same("json broken", UpdateCheck.ParseTag("{not json"), null);
        Same("json empty", UpdateCheck.ParseTag(""), null);

        Console.WriteLine(_pass + " passed, " + _fail + " failed");

        // ---- one live question (informational) --------------------------------
        string live = UpdateCheck.LatestTagAsync(TimeSpan.FromSeconds(10)).GetAwaiter().GetResult();
        Console.WriteLine("GitHub latest release tag: " + (live ?? "(no answer)")
            + "  -> " + AboutInfo.Compare("1.112.60.0", live) + " for an installed 1.112.60.0");
        return _fail == 0 ? 0 : 1;
    }
}
