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
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;

namespace WildlifeTools.About
{
    /// <summary>How the installed version stands against the newest release
    /// on GitHub.</summary>
    internal enum UpdateStatus
    {
        /// <summary>The installed version is the newest release.</summary>
        Current,
        /// <summary>A newer release exists.</summary>
        NewerAvailable,
        /// <summary>The installed build is newer than any release (a
        /// development build).</summary>
        Ahead,
        /// <summary>No answer from GitHub, or a version that would not
        /// parse.</summary>
        Unknown,
    }

    /// <summary>
    /// What the About card says and where it links, kept free of ArcGIS so it
    /// can be compiled into a plain console test (claude_tests/AboutInfoTest):
    /// the add-in's names and addresses, the text of the card, the installed
    /// version read from Config.daml, the citation, and the comparison with
    /// the newest GitHub release.
    /// </summary>
    internal static class AboutInfo
    {
        public const string ProductName = "Wildlife and Forestry Tools";
        public const string ProductNameFull = ProductName + " for ArcGIS Pro";

        public const string RepoUrl = "https://github.com/JeffJenness/Wildlife_Tools";
        public const string ReleasesUrl = RepoUrl + "/releases";
        public const string LatestReleasePageUrl = ReleasesUrl + "/latest";
        public const string LatestReleaseApi =
            "https://api.github.com/repos/JeffJenness/Wildlife_Tools/releases/latest";

        /// <summary>Pages on the Jenness Enterprises site, relative to its
        /// root (opened through <see cref="WebHelp"/>).</summary>
        public const string ToolReferenceRelative = "tool-reference.html";
        public const string SourcesCitedRelative = "sources-cited.html";
        public const string HelpPageRelative = "take-a-break/about-the-add-in.html";

        public const string ContactEmail = "jeffj@jennessent.com";

        public const string LicenseLine =
            "Free software under the GNU General Public License, version 3 or "
            + "later, with the additional terms in LICENSE-ADDITIONAL-TERMS.md.";

        // ---- the three paragraphs, in Jeff's words (as on the Tool Reference
        //      page of the web site; keep the two in step) ---------------------
        public const string Para1 =
            "Wildlife and Forestry Tools is a free add-in for ArcGIS Pro offering "
            + "a large collection of analytical, cartographic and data-management "
            + "tools for wildlife research, landscape analysis and general GIS "
            + "work, ported and modernized from over three decades of Jenness "
            + "Enterprises extensions for ArcView 3.x and ArcMap, and expanded "
            + "with the methods that today's journals expect. Every tool runs at "
            + "every ArcGIS Pro license level, with no extension licenses required.";

        public const string Para2Lead = "Free does not mean lightweight or of little value!";
        public const string Para2Body =
            "Many of these tools do things that no standard tool in ArcGIS Pro "
            + "does, each carries its help and its references with it, and the "
            + "geoprocessing tools run from the Geoprocessing pane, ModelBuilder "
            + "and Python alike. Building the add-in took me roughly 800 hours, "
            + "and I began with the research already read and most of the "
            + "algorithms already written for the earlier ArcView and ArcMap "
            + "versions of these tools; an organization setting out to build the "
            + "same set in-house should expect to spend at least that long. What "
            + "you have is that work, finished, tested and ready to use.";

        public const string Para3Lead = "Why give it away?";
        public const string Para3Body =
            "Because of Lois. My wife, best friend and partner, Lois Engelman, "
            + "and I ran Jenness Enterprises together from 2000 until she died in "
            + "July 2026, and the Lois Engelman Memorial Scholarship Fund at "
            + "Northern Arizona University was created in her name to support the "
            + "next generation of forestry, wildlife and ecology students. The "
            + "add-in is free so that it reaches as many people as it can, and I "
            + "hope it will raise money for that fund: my belief is that people "
            + "who try these tools will see their value, and will consider a "
            + "donation toward something that will help develop the next "
            + "generation of foresters, wildlife biologists, ecologists and land "
            + "managers. All donations made toward this fund are fully "
            + "tax-deductible and donated directly to Northern Arizona University.";

        // ---- the installed version ------------------------------------------

        /// <summary>The version attribute of the AddInInfo element of a
        /// Config.daml, or null.</summary>
        public static string ParseDamlVersion(string daml)
        {
            if (string.IsNullOrEmpty(daml))
            {
                return null;
            }
            Match m = Regex.Match(daml, @"<AddInInfo\b[^>]*\bversion=""([^""]+)""");
            return m.Success ? m.Groups[1].Value.Trim() : null;
        }

        /// <summary>The installed add-in's version, read from the Config.daml
        /// that ships beside the DLL: in an installed add-in the DLL sits in
        /// Install\ one level below Config.daml; in a build output folder the
        /// two sit together. Null when neither is found.</summary>
        public static string ReadInstalledVersion(string dllPath)
        {
            try
            {
                string dir = Path.GetDirectoryName(dllPath);
                if (string.IsNullOrEmpty(dir))
                {
                    return null;
                }
                string parent = Path.GetDirectoryName(dir) ?? dir;
                foreach (string candidate in new[]
                         {
                             Path.Combine(dir, "Config.daml"),
                             Path.Combine(parent, "Config.daml"),
                         })
                {
                    if (File.Exists(candidate))
                    {
                        string v = ParseDamlVersion(File.ReadAllText(candidate));
                        if (v != null)
                        {
                            return v;
                        }
                    }
                }
            }
            catch (Exception)
            {
                // An unreadable file leaves the version unknown; the card still opens.
            }
            return null;
        }

        /// <summary>The add-in's version as stamped into the assembly at
        /// build time (the csproj copies AddInInfo/@version into the
        /// assembly version), or null for an unstamped build (0.0.0.0 or
        /// the SDK default 1.0.0.0). Available wherever the DLL is, with or
        /// without a Config.daml beside it.</summary>
        public static string AssemblyVersionString(Version assemblyVersion)
        {
            if (assemblyVersion == null)
            {
                return null;
            }
            string s = assemblyVersion.ToString();
            return s == "0.0.0.0" || s == "1.0.0.0" ? null : s;
        }

        /// <summary>"3.7.1.59326" -> "3.7.1": the first three numeric parts
        /// of ArcGIS Pro's product version, or null. Anything after the
        /// numbers (a "+commit" suffix, a label) is dropped.</summary>
        public static string ShortProVersion(string productVersion)
        {
            if (string.IsNullOrWhiteSpace(productVersion))
            {
                return null;
            }
            Match m = Regex.Match(productVersion.Trim(), @"^\d+(\.\d+)*");
            if (!m.Success)
            {
                return null;
            }
            string[] parts = m.Value.Split('.');
            return string.Join(".", parts.Take(Math.Min(3, parts.Length)));
        }

        /// <summary>The line under the title: version, build date, Pro
        /// version -- whichever of them are known.</summary>
        public static string VersionLine(string version, DateTime? built, string proVersion)
        {
            string line = "Version " + (string.IsNullOrWhiteSpace(version) ? "unknown" : version);
            if (built.HasValue)
            {
                line += ", built " + built.Value.ToString("MMMM d, yyyy", CultureInfo.InvariantCulture);
            }
            if (!string.IsNullOrWhiteSpace(proVersion))
            {
                line += ", running in ArcGIS Pro " + proVersion;
            }
            return line;
        }

        /// <summary>The recommended citation for the add-in as a whole, in the
        /// form the help pages use for each tool.</summary>
        public static string Citation(string version, DateTime built)
        {
            return string.Format(CultureInfo.InvariantCulture,
                "Jenness, J. {0:yyyy}. Wildlife and Forestry Tools add-in for ArcGIS Pro, "
                + "v. {1} ({0:MMMM yyyy}). Jenness Enterprises, Flagstaff, Arizona. "
                + "Available at: {2}.",
                built, string.IsNullOrWhiteSpace(version) ? "unknown" : version, RepoUrl);
        }

        // ---- the newest release -----------------------------------------------

        /// <summary>"v1.112.60.0" -> "1.112.60.0"; null stays null.</summary>
        public static string TagVersion(string tag)
        {
            if (string.IsNullOrWhiteSpace(tag))
            {
                return null;
            }
            string s = tag.Trim();
            return s.StartsWith("v", StringComparison.OrdinalIgnoreCase) ? s.Substring(1) : s;
        }

        private static bool TryParseVersion(string s, out Version v)
        {
            v = null;
            string t = TagVersion(s);
            if (t == null || !Version.TryParse(t, out Version parsed))
            {
                return false;
            }
            // Pad to four parts so 1.112.60 and 1.112.60.0 compare equal.
            v = new Version(parsed.Major, parsed.Minor,
                Math.Max(parsed.Build, 0), Math.Max(parsed.Revision, 0));
            return true;
        }

        /// <summary>Compares the installed version with a release tag.</summary>
        public static UpdateStatus Compare(string installed, string tag)
        {
            if (!TryParseVersion(installed, out Version mine) || !TryParseVersion(tag, out Version theirs))
            {
                return UpdateStatus.Unknown;
            }
            int c = theirs.CompareTo(mine);
            return c > 0 ? UpdateStatus.NewerAvailable
                 : c < 0 ? UpdateStatus.Ahead
                 : UpdateStatus.Current;
        }

        /// <summary>The sentence shown under the version line once the
        /// check has run.</summary>
        public static string UpdateMessage(UpdateStatus status, string tagVersion)
        {
            switch (status)
            {
                case UpdateStatus.Current:
                    return "This is the newest version.";
                case UpdateStatus.NewerAvailable:
                    return "Version " + tagVersion + " is available.";
                case UpdateStatus.Ahead:
                    return "This build is newer than the current release (" + tagVersion + ").";
                default:
                    return "Could not check for a newer version.";
            }
        }
    }
}
