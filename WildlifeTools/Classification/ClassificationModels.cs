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
        // Warning KIND per unit: raw / sd (dev+demstd+standardized) / pctl.
        public static readonly string[] UnitsList = { "raw", "standardized", "sd", "pctl" };
        public static readonly string[] AllRoles = { "tpi", "tpi_small", "tpi_large", "slope", "grid" };
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
            return d;
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
                        sys.FilePath = f;
                        result.Add(sys);
                    }
                }
                catch { /* skip unreadable files */ }
            }
            return result.OrderBy(s => s.Name, StringComparer.OrdinalIgnoreCase).ToList();
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
                if (c.Criteria == null || c.Criteria.Count == 0)
                    throw new InvalidDataException($"Class '{c.Name}' has no criteria.");
                foreach (var cr in c.Criteria)
                {
                    if (!roles.Contains(cr.Input))
                        throw new InvalidDataException(
                            $"Class '{c.Name}' references unknown input '{cr.Input}'.");
                    if (Array.IndexOf(UnitsList, cr.Units) < 0)
                        throw new InvalidDataException($"Unknown units '{cr.Units}'.");
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
                        new() { Role = "grid", Kind = "external", Label = "Input grid" },
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
}
