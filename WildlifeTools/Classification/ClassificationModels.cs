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
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace WildlifeTools.Classification
{
    // JSON model for a classification system. This MUST stay in lock-step with
    // the Python side (Toolboxes/classification_store.py + tpi_tools.py) and the
    // schema in docs/classification_system_schema.md. schemaVersion 1.

    public class ClassificationSystem
    {
        [JsonPropertyName("schemaVersion")] public int SchemaVersion { get; set; } = 1;
        [JsonPropertyName("name")] public string Name { get; set; } = "";
        [JsonPropertyName("family")] public string Family { get; set; } = "slope_position";
        [JsonPropertyName("description")] public string Description { get; set; }
        [JsonPropertyName("source")] public string Source { get; set; }
        [JsonPropertyName("builtin")] public bool? Builtin { get; set; }
        // Units the system's slope thresholds assume ("Degrees" or "Percent");
        // used only to warn about incompatible slope rasters. Null = unspecified.
        [JsonPropertyName("slopeUnits")] public string SlopeUnits { get; set; }
        [JsonPropertyName("inputs")] public List<ClassInput> Inputs { get; set; } = new();
        [JsonPropertyName("classes")] public List<ClassDef> Classes { get; set; } = new();

        [JsonIgnore] public string FilePath { get; set; }
    }

    public class ClassInput
    {
        [JsonPropertyName("role")] public string Role { get; set; }
        [JsonPropertyName("kind")] public string Kind { get; set; } = "external";
        [JsonPropertyName("label")] public string Label { get; set; }
        [JsonPropertyName("optional")] public bool? Optional { get; set; }
    }

    public class ClassDef
    {
        [JsonPropertyName("value")] public int Value { get; set; }
        [JsonPropertyName("name")] public string Name { get; set; } = "";
        [JsonPropertyName("color")] public int[] Color { get; set; }
        // Percent opacity 0-100 (matches CIM / Pro's transparency UI).
        // Null = fully opaque; the builder omits it at 100 so opaque files
        // stay unchanged.
        [JsonPropertyName("alpha")] public double? Alpha { get; set; }
        [JsonPropertyName("criteria")] public List<Criterion> Criteria { get; set; } = new();
    }

    public class Criterion
    {
        [JsonPropertyName("input")] public string Input { get; set; }
        [JsonPropertyName("units")] public string Units { get; set; } = "raw";
        [JsonPropertyName("conditions")] public List<Condition> Conditions { get; set; } = new();
    }

    public class Condition
    {
        // "join" is written only on the second condition (and / or).
        [JsonPropertyName("join")] public string Join { get; set; }
        [JsonPropertyName("op")] public string Op { get; set; }
        [JsonPropertyName("value")] public double Value { get; set; }
    }

    /// <summary>
    /// Reads and writes classification systems in the same per-user store the
    /// Python apply tools use:
    /// %LOCALAPPDATA%\JennessEnterprises\WildlifeTools\classification_systems\.
    /// </summary>
    public static class ClassificationStore
    {
        public static readonly string[] Families = { "slope_position", "landform", "general" };
        public static readonly string[] Operators = { "<", "<=", ">", ">=", "=", "!=" };
        public static readonly string[] OperatorsBlank = { "", "<", "<=", ">", ">=", "=", "!=" };
        public static readonly string[] JoinOptions = { "", "and", "or" };
        // Criterion units are per input KIND (mirrors classification_store.py):
        // TPI roles threshold a TPI representation; slope roles threshold slope
        // in degrees/percent ("raw" = legacy alias in pre-2026-07-29 files);
        // external (general raster) roles threshold cell values or a
        // whole-raster z-score.
        public static readonly string[] TpiUnits = { "raw", "standardized", "sd", "pctl" };
        public static readonly string[] SlopeCriterionUnits = { "degrees", "percent", "raw" };
        public static readonly string[] ExternalUnits = { "raw", "standardized" };
        public static string KindOfRole(string role) =>
            role == "slope" ? "slope"
            : (role != null && role.StartsWith("tpi")) ? "tpi" : "external";
        public static string[] UnitsForKind(string kind) =>
            kind == "slope" ? SlopeCriterionUnits
            : kind == "tpi" ? TpiUnits : ExternalUnits;
        public static readonly string[] AllRoles = { "tpi", "tpi_small", "tpi_large", "slope", "raster" };
        public static readonly string[] SlopeUnitsList = { "Degrees", "Percent" };
        // Bindable variant that allows "unspecified".
        public static readonly string[] SlopeUnitsBlank = { "", "Degrees", "Percent" };

        private static readonly JsonSerializerOptions Opts = new()
        {
            WriteIndented = true,
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        };

        public static string StoreDir()
        {
            string b = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (string.IsNullOrEmpty(b))
                b = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            return Path.Combine(b, "JennessEnterprises", "WildlifeTools", "classification_systems");
        }

        public static string EnsureStore()
        {
            string d = StoreDir();
            Directory.CreateDirectory(d);
            SeedDefaults(d);
            return d;
        }

        /// <summary>
        /// Copy each bundled default system (deployed with the add-in under
        /// Toolboxes\classification_seeds) into the store IF a file of that
        /// name is not already there -- never overwrites, so user edits win.
        /// Mirrors classification_store.seed_defaults on the Python side; BOTH
        /// sides must seed, because either the Builder or a geoprocessing tool
        /// can be the first thing a fresh user opens.
        /// </summary>
        private static void SeedDefaults(string dest)
        {
            try
            {
                string seeds = Path.Combine(
                    Path.GetDirectoryName(WildlifeTools.ToolboxPaths.Pyt) ?? "",
                    "classification_seeds");
                if (!Directory.Exists(seeds)) return;
                foreach (var f in Directory.EnumerateFiles(seeds, "*.json"))
                {
                    string target = Path.Combine(dest, Path.GetFileName(f));
                    if (!File.Exists(target))
                    {
                        try { File.Copy(f, target); }
                        catch { /* another process may have won the race */ }
                    }
                }
            }
            catch { /* seeding must never block the builder from opening */ }
        }

        // Legacy role identifiers -> modern names ("grid" was the raster format
        // of the ArcView/ArcInfo era). Mirrors classification_store.py:
        // old files migrate in memory on read and rewrite on the next save.
        private static readonly Dictionary<string, string> LegacyRoles =
            new() { ["grid"] = "raster" };

        internal static void NormalizeRoles(ClassificationSystem s)
        {
            if (s?.Inputs == null) return;
            var existing = new HashSet<string>(s.Inputs.Select(i => i.Role));
            foreach (var kv in LegacyRoles)
            {
                if (!existing.Contains(kv.Key) || existing.Contains(kv.Value))
                    continue;
                foreach (var i in s.Inputs.Where(i => i.Role == kv.Key))
                    i.Role = kv.Value;
                foreach (var c in s.Classes ?? new List<ClassDef>())
                    foreach (var cr in c.Criteria ?? new List<Criterion>())
                        if (cr.Input == kv.Key)
                            cr.Input = kv.Value;
            }
        }

        public static List<ClassificationSystem> List()
        {
            var result = new List<ClassificationSystem>();
            string d = EnsureStore();
            foreach (var f in Directory.EnumerateFiles(d, "*.json").OrderBy(x => x))
            {
                try
                {
                    var sys = JsonSerializer.Deserialize<ClassificationSystem>(
                        File.ReadAllText(f), Opts);
                    if (sys != null && !string.IsNullOrEmpty(sys.Name))
                    {
                        NormalizeRoles(sys);
                        sys.FilePath = f;
                        result.Add(sys);
                    }
                }
                catch { /* skip unreadable files */ }
            }
            return result.OrderBy(s => s.Name, StringComparer.OrdinalIgnoreCase).ToList();
        }

        /// <summary>One system from its JSON text (a store file or an
        /// element of a shared bundle). Throws on malformed JSON.</summary>
        internal static ClassificationSystem Deserialize(string json)
            => JsonSerializer.Deserialize<ClassificationSystem>(json, Opts)
               ?? throw new InvalidOperationException("empty document");

        /// <summary>A store path for a NEW file named after the system that
        /// does not collide with any existing file (names that differ only
        /// in punctuation share a slug).</summary>
        internal static string UniqueNewPath(string name)
        {
            string d = EnsureStore();
            string slug = Slug(name);
            string path = Path.Combine(d, slug + ".json");
            for (int i = 2; File.Exists(path); i++)
                path = Path.Combine(d, $"{slug}_{i}.json");
            return path;
        }

        public static string Slug(string name)
        {
            var sb = new StringBuilder();
            foreach (char c in name ?? "")
                sb.Append(char.IsLetterOrDigit(c) || c == '.' || c == '-' ? c : '_');
            string s = sb.ToString().Trim('_');
            return string.IsNullOrEmpty(s) ? "system" : s;
        }

        /// <summary>Validate, then write. Returns the file path written.</summary>
        public static string Save(ClassificationSystem sys)
        {
            Validate(sys);
            string d = EnsureStore();
            string path = sys.FilePath;
            if (string.IsNullOrEmpty(path))
                path = Path.Combine(d, Slug(sys.Name) + ".json");
            File.WriteAllText(path, JsonSerializer.Serialize(sys, Opts));
            sys.FilePath = path;
            return path;
        }

        public static void Delete(ClassificationSystem sys)
        {
            if (!string.IsNullOrEmpty(sys.FilePath) && File.Exists(sys.FilePath))
                File.Delete(sys.FilePath);
        }

        public static void Validate(ClassificationSystem s)
        {
            if (s == null) throw new InvalidDataException("No system.");
            if (string.IsNullOrWhiteSpace(s.Name))
                throw new InvalidDataException("The system needs a name.");
            if (Array.IndexOf(Families, s.Family) < 0)
                throw new InvalidDataException($"Unknown family '{s.Family}'.");
            if (!string.IsNullOrEmpty(s.SlopeUnits)
                && Array.IndexOf(SlopeUnitsList, s.SlopeUnits) < 0)
                throw new InvalidDataException($"Unknown slopeUnits '{s.SlopeUnits}'.");
            if (s.Inputs == null || s.Inputs.Count == 0)
                throw new InvalidDataException("The system declares no inputs.");
            if (s.Classes == null || s.Classes.Count == 0)
                throw new InvalidDataException("The system has no classes.");
            var roles = new HashSet<string>(s.Inputs.Select(i => i.Role));
            var seen = new HashSet<int>();
            foreach (var c in s.Classes)
            {
                if (c.Value == 0)
                    throw new InvalidDataException(
                        $"Class value 0 is reserved for unclassified/NoData cells; use values 1 and up (class '{c.Name}').");
                if (!seen.Add(c.Value))
                    throw new InvalidDataException($"Duplicate class value {c.Value}.");
                if (string.IsNullOrWhiteSpace(c.Name))
                    throw new InvalidDataException($"Class value {c.Value} needs a name.");
                if (c.Color != null && (c.Color.Length != 3 || c.Color.Any(x => x < 0 || x > 255)))
                    throw new InvalidDataException($"Class '{c.Name}' color must be R,G,B in 0-255.");
                if (c.Alpha is double al && (al < 0 || al > 100))
                    throw new InvalidDataException(
                        $"Class '{c.Name}' alpha must be 0-100 (percent opacity).");
                if (c.Criteria == null || c.Criteria.Count == 0)
                    throw new InvalidDataException($"Class '{c.Name}' has no criteria.");
                foreach (var cr in c.Criteria)
                {
                    if (!roles.Contains(cr.Input))
                        throw new InvalidDataException(
                            $"Class '{c.Name}' references unknown input '{cr.Input}'.");
                    var allowed = UnitsForKind(KindOfRole(cr.Input));
                    if (Array.IndexOf(allowed, cr.Units) < 0)
                        throw new InvalidDataException(
                            $"Units '{cr.Units}' are not valid for input '{cr.Input}' "
                            + $"in class '{c.Name}' (valid: {string.Join(", ", allowed)}).");
                    if (cr.Conditions == null || cr.Conditions.Count < 1 || cr.Conditions.Count > 2)
                        throw new InvalidDataException(
                            $"A criterion in class '{c.Name}' needs 1 or 2 conditions.");
                    for (int i = 0; i < cr.Conditions.Count; i++)
                    {
                        var cond = cr.Conditions[i];
                        if (Array.IndexOf(Operators, cond.Op) < 0)
                            throw new InvalidDataException(
                                $"Bad operator '{cond.Op}' in class '{c.Name}'.");
                        // anything but and/or would silently act as OR in the classifier
                        if (i == 1 && !string.IsNullOrEmpty(cond.Join)
                            && cond.Join != "and" && cond.Join != "or")
                            throw new InvalidDataException(
                                $"Condition join must be 'and' or 'or' in class '{c.Name}' (got '{cond.Join}').");
                    }
                }
            }
        }

        /// <summary>The fixed input roles for each family (kind + label).</summary>
        public static List<ClassInput> DefaultInputs(string family)
        {
            switch (family)
            {
                case "landform":
                    return new List<ClassInput>
                    {
                        new() { Role = "tpi_small", Kind = "tpi", Label = "Small-neighborhood TPI" },
                        new() { Role = "tpi_large", Kind = "tpi", Label = "Large-neighborhood TPI" },
                        new() { Role = "slope", Kind = "slope", Label = "Slope (degrees)", Optional = true },
                    };
                case "general":
                    return new List<ClassInput>
                    {
                        // Old files using the legacy role "grid" migrate to
                        // "raster" on read (NormalizeRoles / _normalize_roles).
                        new() { Role = "raster", Kind = "external", Label = "Input raster" },
                    };
                default: // slope_position
                    return new List<ClassInput>
                    {
                        new() { Role = "tpi", Kind = "tpi", Label = "TPI" },
                        new() { Role = "slope", Kind = "slope", Label = "Slope (degrees)" },
                    };
            }
        }

        public static string[] RolesFor(string family) =>
            DefaultInputs(family).Select(i => i.Role).ToArray();
    }

    /// <summary>
    /// Save-time sanity analysis of a system's class ranges: reports value
    /// ranges no class covers (cells there become Unclassified), pairs of
    /// classes whose ranges overlap (lowest class value wins), and inputs
    /// whose criteria mix units. Exact for the schema's criterion shape
    /// (1-2 threshold conditions joined by and/or = unions of intervals).
    /// Gap detection uses the necessary condition per input: a cell whose
    /// value on SOME input falls in no class's interval for that input can
    /// never be classified, regardless of the other inputs.
    /// </summary>
    internal static class CoverageAnalysis
    {
        private const double NegInf = double.NegativeInfinity;
        private const double PosInf = double.PositiveInfinity;

        private readonly struct Iv
        {
            public readonly double Lo, Hi; public readonly bool LoIn, HiIn;
            public Iv(double lo, bool loIn, double hi, bool hiIn)
            { Lo = lo; LoIn = loIn; Hi = hi; HiIn = hiIn; }
            public bool IsEmpty => Lo > Hi || (Lo == Hi && !(LoIn && HiIn));
        }

        private static readonly List<Iv> Full = new() { new Iv(NegInf, false, PosInf, false) };

        private static List<Iv> FromCondition(string op, double v) => op switch
        {
            "<" => new List<Iv> { new(NegInf, false, v, false) },
            "<=" => new List<Iv> { new(NegInf, false, v, true) },
            ">" => new List<Iv> { new(v, false, PosInf, false) },
            ">=" => new List<Iv> { new(v, true, PosInf, false) },
            "=" => new List<Iv> { new(v, true, v, true) },
            "!=" => new List<Iv> { new(NegInf, false, v, false), new(v, false, PosInf, false) },
            _ => new List<Iv>(Full),
        };

        private static List<Iv> Intersect(List<Iv> a, List<Iv> b)
        {
            var outp = new List<Iv>();
            foreach (var x in a)
                foreach (var y in b)
                {
                    double lo; bool loIn;
                    if (x.Lo > y.Lo) { lo = x.Lo; loIn = x.LoIn; }
                    else if (y.Lo > x.Lo) { lo = y.Lo; loIn = y.LoIn; }
                    else { lo = x.Lo; loIn = x.LoIn && y.LoIn; }
                    double hi; bool hiIn;
                    if (x.Hi < y.Hi) { hi = x.Hi; hiIn = x.HiIn; }
                    else if (y.Hi < x.Hi) { hi = y.Hi; hiIn = y.HiIn; }
                    else { hi = x.Hi; hiIn = x.HiIn && y.HiIn; }
                    var iv = new Iv(lo, loIn, hi, hiIn);
                    if (!iv.IsEmpty) outp.Add(iv);
                }
            return Normalize(outp);
        }

        private static List<Iv> Normalize(List<Iv> ivs)
        {
            var s = ivs.Where(i => !i.IsEmpty)
                       .OrderBy(i => i.Lo).ThenByDescending(i => i.LoIn).ToList();
            var outp = new List<Iv>();
            foreach (var iv in s)
            {
                if (outp.Count > 0)
                {
                    var last = outp[^1];
                    bool touches = iv.Lo < last.Hi
                        || (iv.Lo == last.Hi && (last.HiIn || iv.LoIn));
                    if (touches)
                    {
                        if (iv.Hi > last.Hi || (iv.Hi == last.Hi && iv.HiIn && !last.HiIn))
                            outp[^1] = new Iv(last.Lo, last.LoIn, iv.Hi, iv.HiIn);
                        continue;
                    }
                }
                outp.Add(iv);
            }
            return outp;
        }

        private static List<Iv> Complement(List<Iv> covered)
        {
            var c = Normalize(covered);
            var gaps = new List<Iv>();
            double lo = NegInf; bool loIn = false;
            foreach (var iv in c)
            {
                var g = new Iv(lo, loIn, iv.Lo, !iv.LoIn && iv.Lo != NegInf);
                if (!g.IsEmpty && !(g.Lo == NegInf && g.Hi == NegInf)) gaps.Add(g);
                lo = iv.Hi; loIn = !iv.HiIn && iv.Hi != PosInf;
            }
            var tail = new Iv(lo, loIn, PosInf, false);
            if (!tail.IsEmpty && lo != PosInf) gaps.Add(tail);
            return gaps;
        }

        /// <summary>Interval set for one criterion (1-2 conditions, and/or).</summary>
        private static List<Iv> CriterionSet(Criterion cr)
        {
            var conds = cr.Conditions ?? new List<Condition>();
            if (conds.Count == 0) return new List<Iv>(Full);
            var s1 = FromCondition(conds[0].Op, conds[0].Value);
            if (conds.Count == 1) return s1;
            var s2 = FromCondition(conds[1].Op, conds[1].Value);
            return (conds[1].Join ?? "and") == "or"
                ? Normalize(s1.Concat(s2).ToList())
                : Intersect(s1, s2);
        }

        /// <summary>role -> interval set for one class (criteria on the same
        /// role intersect; roles the class does not constrain are absent).</summary>
        private static Dictionary<string, List<Iv>> ClassSets(ClassDef c)
        {
            var d = new Dictionary<string, List<Iv>>();
            foreach (var cr in c.Criteria ?? new List<Criterion>())
            {
                var s = CriterionSet(cr);
                d[cr.Input] = d.TryGetValue(cr.Input, out var prev) ? Intersect(prev, s) : s;
            }
            return d;
        }

        private static string Num(double v) => v.ToString("0.######",
            System.Globalization.CultureInfo.InvariantCulture);

        private static string FmtGap(Iv g)
        {
            if (g.Lo == g.Hi) return "the exact value " + Num(g.Lo);
            if (g.Lo == NegInf) return "values " + (g.HiIn ? "≤ " : "< ") + Num(g.Hi);
            if (g.Hi == PosInf) return "values " + (g.LoIn ? "≥ " : "> ") + Num(g.Lo);
            return "values from " + Num(g.Lo) + (g.LoIn ? " (inclusive)" : " (exclusive)")
                 + " to " + Num(g.Hi) + (g.HiIn ? " (inclusive)" : " (exclusive)");
        }

        public static List<string> Analyze(ClassificationSystem s)
        {
            var warnings = new List<string>();
            var classes = (s.Classes ?? new List<ClassDef>())
                          .OrderBy(c => c.Value).ToList();
            if (classes.Count == 0) return warnings;
            var sets = classes.Select(ClassSets).ToList();

            // ---- mixed units per input --------------------------------------
            var unitsByRole = new Dictionary<string, HashSet<string>>();
            foreach (var c in classes)
                foreach (var cr in c.Criteria ?? new List<Criterion>())
                    (unitsByRole.TryGetValue(cr.Input, out var set)
                        ? set : unitsByRole[cr.Input] = new HashSet<string>()).Add(cr.Units);
            foreach (var kv in unitsByRole.Where(kv => kv.Value.Count > 1))
                warnings.Add($"Input '{kv.Key}': criteria MIX units ({string.Join(", ", kv.Value)}) "
                    + "-- the tool classifies one representation per input, so mixed "
                    + "units cannot all be honored.");

            // ---- gaps (necessary condition per input) -----------------------
            foreach (var role in unitsByRole.Keys)
            {
                // a class that does not constrain this role covers it entirely
                if (sets.Any(d => !d.ContainsKey(role))) continue;
                var union = new List<Iv>();
                foreach (var d in sets) union.AddRange(d[role]);
                var gaps = Complement(union);
                // physically bounded units: clip gaps to the meaningful range
                var u = unitsByRole[role];
                if (u.Contains("pctl"))
                    gaps = Intersect(gaps, new List<Iv> { new(0, true, 100, true) });
                else if (u.Contains("degrees"))
                    gaps = Intersect(gaps, new List<Iv> { new(0, true, 90, true) });
                else if (u.Contains("percent"))
                    gaps = Intersect(gaps, new List<Iv> { new(0, true, PosInf, false) });
                foreach (var g in gaps.Take(4))
                    warnings.Add($"Input '{role}': {FmtGap(g)} covered by NO class -- "
                        + "those cells will be left Unclassified in the output.");
                if (gaps.Count > 4)
                    warnings.Add($"Input '{role}': ...and {gaps.Count - 4} more uncovered range(s).");
            }

            // ---- overlapping classes (lowest value wins) --------------------
            var overlaps = new List<string>();
            for (int i = 0; i < classes.Count; i++)
                for (int j = i + 1; j < classes.Count; j++)
                {
                    bool all = true;
                    foreach (var role in sets[i].Keys.Union(sets[j].Keys))
                    {
                        var a = sets[i].TryGetValue(role, out var sa) ? sa : Full;
                        var b = sets[j].TryGetValue(role, out var sb) ? sb : Full;
                        if (Intersect(a, b).Count == 0) { all = false; break; }
                    }
                    if (all)
                        overlaps.Add($"Classes '{classes[i].Name}' ({classes[i].Value}) and "
                            + $"'{classes[j].Name}' ({classes[j].Value}) OVERLAP -- cells in "
                            + $"the overlap get the lower value ({classes[i].Value}).");
                }
            warnings.AddRange(overlaps.Take(6));
            if (overlaps.Count > 6)
                warnings.Add($"...and {overlaps.Count - 6} more overlapping class pair(s).");
            return warnings;
        }
    }
}
