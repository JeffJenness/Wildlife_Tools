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
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace WildlifeTools.UnitConverter
{
    // The arithmetic and the data behind the Unit Converter window, with no
    // ArcGIS or WPF types in it, so it compiles into a plain console test
    // (claude_tests/UnitConverterTest). The Python side of the same catalog
    // is Toolboxes/unit_converter.py, used by the Convert Field Units tool.
    //
    // Every unit converts to its measurement class's BASE unit and back:
    //   linear and affine units   base = x * factor + offset
    //   circle diameters          base = pi / 4 * (x * factor)^2   (an area)
    //   percent slope             base = atan(x * factor / 100), in degrees
    // so any unit converts to any other unit of its class through the base.
    // This is the 2000 program's three steps (FROM unit to a primary unit,
    // primary to primary, primary to TO unit) folded into two.
    //
    // A unit the user adds is defined against any unit already in its class,
    // "1 rod = 16.5 feet", and converts through that unit.

    internal enum UnitTransform { None, CircleDiameter, SlopePercent }

    /// <summary>A measurement class: Length, Area, ..., or one the user made.</summary>
    internal sealed class UnitClassDef
    {
        public string Name { get; set; }
        public string BaseUnit { get; set; }
        public bool IsUser { get; set; }

        public override string ToString() => Name;
    }

    /// <summary>One measurement unit.</summary>
    internal sealed class UnitDef
    {
        public string Name { get; set; }
        public string Singular { get; set; }
        public string ClassName { get; set; }
        public string Definition { get; set; } = "";
        public double Factor { get; set; } = 1.0;
        public double Offset { get; set; }
        public UnitTransform Transform { get; set; }
        public bool IsUser { get; set; }

        /// <summary>Other names for the unit: the names ArcGIS Pro and the
        /// EPSG registry use (FeetUS, Foot_US, US survey foot), found by Find.</summary>
        public List<string> Aliases { get; set; } = new List<string>();

        /// <summary>Keys of the sources that state or confirm the unit's
        /// value, in <see cref="UnitCatalog.Citations"/>.</summary>
        public List<string> Sources { get; set; } = new List<string>();

        /// <summary>Keys of the sources of the unit's description when they
        /// differ from those of its value: the descriptions of the original
        /// program (word origins, history) are credited to it, so they do not
        /// read as the claims of the standard that gives the value (Jeff,
        /// 2026-10-08).</summary>
        public List<string> DescriptionSources { get; set; } = new List<string>();

        // a user unit: 1 of this unit = DefinedFactor RefUnit + DefinedOffset
        public string RefName { get; set; }
        public double DefinedFactor { get; set; } = 1.0;
        public double DefinedOffset { get; set; }
        internal UnitDef Ref { get; set; }

        public double ToBase(double x)
        {
            if (Ref != null)
            {
                return Ref.ToBase(x * DefinedFactor + DefinedOffset);
            }
            switch (Transform)
            {
                case UnitTransform.CircleDiameter:
                    double d = x * Factor;
                    return Math.PI / 4.0 * d * d;
                case UnitTransform.SlopePercent:
                    return Math.Atan(x * Factor / 100.0) * 180.0 / Math.PI;
                default:
                    return x * Factor + Offset;
            }
        }

        public double FromBase(double b)
        {
            if (Ref != null)
            {
                return (Ref.FromBase(b) - DefinedOffset) / DefinedFactor;
            }
            switch (Transform)
            {
                case UnitTransform.CircleDiameter:
                    return b < 0 ? double.NaN : Math.Sqrt(4.0 * b / Math.PI) / Factor;
                case UnitTransform.SlopePercent:
                    return 100.0 * Math.Tan(b * Math.PI / 180.0) / Factor;
                default:
                    return (b - Offset) / Factor;
            }
        }

        /// <summary>True when this unit converts through a curve (a circle's
        /// diameter, a slope's percent) rather than by a factor.</summary>
        public bool IsNonlinear => Ref != null ? Ref.IsNonlinear : Transform != UnitTransform.None;

        /// <summary>The name to show beside a value: singular for exactly 1.</summary>
        public string LabelFor(double value) =>
            Math.Abs(value) == 1.0 && !string.IsNullOrEmpty(Singular) ? Singular : Name;

        public override string ToString() => Name;
    }

    /// <summary>A source the catalog cites for unit values.</summary>
    internal sealed class Citation
    {
        public string Key { get; set; }
        public string Short { get; set; }
        public string Full { get; set; }
        public string Url { get; set; }
    }

    /// <summary>A conversion the user saved for quick reach.</summary>
    internal sealed class SavedConversion
    {
        public string Name { get; set; }
        public string ClassName { get; set; }
        public string From { get; set; }
        public string To { get; set; }

        public override string ToString() => Name;
    }

    /// <summary>
    /// The bundled catalog plus the user's own classes, units and saved
    /// conversions. The bundled file ships with the add-in; the user's file
    /// lives in the per-user folder the classification systems use, so it
    /// survives reinstalling the add-in, and a new bundled catalog never
    /// overwrites it.
    /// </summary>
    internal sealed class UnitCatalog
    {
        public const string AllClasses = "(all classes)";

        private readonly Dictionary<string, UnitDef> _byName =
            new Dictionary<string, UnitDef>(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, UnitDef> _byAlias =
            new Dictionary<string, UnitDef>(StringComparer.OrdinalIgnoreCase);

        public List<UnitClassDef> Classes { get; } = new List<UnitClassDef>();
        public List<UnitDef> Units { get; } = new List<UnitDef>();
        public List<SavedConversion> Saved { get; } = new List<SavedConversion>();
        public List<string> Warnings { get; } = new List<string>();
        public Dictionary<string, Citation> Citations { get; } = new Dictionary<string, Citation>(StringComparer.OrdinalIgnoreCase);
        public string Source { get; private set; } = "";

        // ---- where the files are ------------------------------------------------

        /// <summary>units.json beside the add-in's toolbox.</summary>
        public static string BundledPath(string addinDir) =>
            Path.Combine(addinDir, "Toolboxes", "unit_converter", "units.json");

        /// <summary>%LOCALAPPDATA%\JennessEnterprises\WildlifeTools\unit_converter\my_units.json</summary>
        public static string UserPath
        {
            get
            {
                string b = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                if (string.IsNullOrEmpty(b))
                {
                    b = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
                }
                return Path.Combine(b, "JennessEnterprises", "WildlifeTools", "unit_converter", "my_units.json");
            }
        }

        // ---- loading ---------------------------------------------------------------

        public static UnitCatalog Load(string bundledPath, string userPath)
        {
            var cat = new UnitCatalog();
            cat.LoadBundled(File.ReadAllText(bundledPath, Encoding.UTF8));
            if (!string.IsNullOrEmpty(userPath) && File.Exists(userPath))
            {
                try
                {
                    cat.LoadUser(File.ReadAllText(userPath, Encoding.UTF8));
                }
                catch (Exception ex)
                {
                    cat.Warnings.Add("Your own units could not be read (" + ex.Message + "): " + userPath);
                }
            }
            return cat;
        }

        internal void LoadBundled(string json)
        {
            JsonNode root = JsonNode.Parse(json);
            Source = (string)root["source"] ?? "";
            if (root["citations"] is JsonObject cits)
            {
                foreach (KeyValuePair<string, JsonNode> kv in cits)
                {
                    Citations[kv.Key] = new Citation
                    {
                        Key = kv.Key,
                        Short = (string)kv.Value?["short"] ?? kv.Key,
                        Full = (string)kv.Value?["full"] ?? "",
                        Url = (string)kv.Value?["url"] ?? "",
                    };
                }
            }
            foreach (JsonNode c in root["classes"].AsArray())
            {
                Classes.Add(new UnitClassDef { Name = (string)c["name"], BaseUnit = (string)c["base"] });
            }
            foreach (JsonNode u in root["units"].AsArray())
            {
                var def = new UnitDef
                {
                    Name = (string)u["name"],
                    Singular = (string)u["singular"] ?? (string)u["name"],
                    ClassName = (string)u["class"],
                    Factor = (double)u["factor"],
                    Offset = u["offset"] != null ? (double)u["offset"] : 0.0,
                    Definition = (string)u["definition"] ?? "",
                };
                if (u["sources"] is JsonArray src)
                {
                    def.Sources = src.Select(a => (string)a).Where(a => !string.IsNullOrWhiteSpace(a)).ToList();
                }
                if (u["descriptionSources"] is JsonArray dsrc)
                {
                    def.DescriptionSources = dsrc.Select(a => (string)a).Where(a => !string.IsNullOrWhiteSpace(a)).ToList();
                }
                if (u["aliases"] is JsonArray al)
                {
                    def.Aliases = al.Select(a => (string)a).Where(a => !string.IsNullOrWhiteSpace(a)).ToList();
                }
                string t = (string)u["transform"];
                def.Transform = t == "circle_diameter" ? UnitTransform.CircleDiameter
                    : t == "slope_percent" ? UnitTransform.SlopePercent : UnitTransform.None;
                AddUnit(def);
            }
        }

        internal void LoadUser(string json)
        {
            JsonNode root = JsonNode.Parse(json);
            if (root?["classes"] is JsonArray classes)
            {
                foreach (JsonNode c in classes)
                {
                    string name = (string)c["name"];
                    string baseName = (string)c["base"];
                    if (string.IsNullOrWhiteSpace(name) || string.IsNullOrWhiteSpace(baseName))
                    {
                        continue;
                    }
                    if (FindClass(name) != null || Find(baseName) != null)
                    {
                        Warnings.Add("Skipped your class '" + name + "': the class or its base unit name is already in use.");
                        continue;
                    }
                    Classes.Add(new UnitClassDef { Name = name, BaseUnit = baseName, IsUser = true });
                    AddUnit(new UnitDef
                    {
                        Name = baseName,
                        Singular = (string)c["baseSingular"] ?? baseName,
                        ClassName = name,
                        Definition = (string)c["definition"] ?? "",
                        IsUser = true,
                    });
                }
            }
            var pending = new List<UnitDef>();
            if (root?["units"] is JsonArray units)
            {
                foreach (JsonNode u in units)
                {
                    JsonNode d = u["definedAs"];
                    if (d == null)
                    {
                        continue;
                    }
                    pending.Add(new UnitDef
                    {
                        Name = (string)u["name"],
                        Singular = (string)u["singular"] ?? (string)u["name"],
                        ClassName = (string)u["class"],
                        Definition = (string)u["definition"] ?? "",
                        RefName = (string)d["unit"],
                        DefinedFactor = d["factor"] != null ? (double)d["factor"] : 1.0,
                        DefinedOffset = d["offset"] != null ? (double)d["offset"] : 0.0,
                        IsUser = true,
                    });
                }
            }
            // a user unit may be defined against another user unit: resolve
            // in rounds until nothing more can be resolved
            bool progress = true;
            while (pending.Count > 0 && progress)
            {
                progress = false;
                foreach (UnitDef u in pending.ToList())
                {
                    UnitDef r = Find(u.RefName);
                    if (r == null)
                    {
                        continue;
                    }
                    string problem = CheckUserUnit(u, r, null);
                    pending.Remove(u);
                    progress = true;
                    if (problem != null)
                    {
                        Warnings.Add("Skipped your unit '" + u.Name + "': " + problem);
                        continue;
                    }
                    u.Ref = r;
                    u.ClassName = r.ClassName;
                    AddUnit(u);
                }
            }
            foreach (UnitDef u in pending)
            {
                Warnings.Add("Skipped your unit '" + u.Name + "': the unit it is defined against, '" + u.RefName + "', is not in the catalog.");
            }
            if (root?["saved"] is JsonArray saved)
            {
                foreach (JsonNode s in saved)
                {
                    var sc = new SavedConversion
                    {
                        Name = (string)s["name"],
                        ClassName = (string)s["class"],
                        From = (string)s["from"],
                        To = (string)s["to"],
                    };
                    if (Find(sc.From) != null && Find(sc.To) != null)
                    {
                        Saved.Add(sc);
                    }
                }
            }
        }

        private void AddUnit(UnitDef u)
        {
            Units.Add(u);
            _byName[u.Name] = u;
            foreach (string a in u.Aliases)
            {
                if (!_byAlias.ContainsKey(a.Trim()))
                {
                    _byAlias[a.Trim()] = u;
                }
            }
        }

        // ---- finding --------------------------------------------------------------------

        /// <summary>A unit by its name, or by one of its aliases.</summary>
        public UnitDef Find(string name)
        {
            if (name == null)
            {
                return null;
            }
            string k = name.Trim();
            return _byName.TryGetValue(k, out UnitDef u) ? u : _byAlias.TryGetValue(k, out UnitDef a) ? a : null;
        }

        /// <summary>"Source: NIST SP 811 (2008), Appendix B." for a unit, or,
        /// when its description has sources of its own, "Value: NIST Handbook
        /// 44 (2026), Appendix C. Description: the original Unit Converter
        /// (Jenness 2001)."; "" for a unit with no sources.</summary>
        public string SourcesText(UnitDef u)
        {
            if (u == null || u.Sources.Count == 0)
            {
                return "";
            }
            string Names(IEnumerable<string> keys) =>
                string.Join("; ", keys.Select(k => Citations.TryGetValue(k, out Citation c) ? c.Short : k));
            if (u.DescriptionSources.Count > 0)
            {
                return "Value: " + Names(u.Sources) + ". Description: " + Names(u.DescriptionSources) + ".";
            }
            return (u.Sources.Count == 1 ? "Source: " : "Sources: ") + Names(u.Sources) + ".";
        }

        public UnitClassDef FindClass(string name) =>
            Classes.FirstOrDefault(c => string.Equals(c.Name, name, StringComparison.OrdinalIgnoreCase));

        /// <summary>The units of a class (or of every class), whose name,
        /// singular, aliases or class contains every word of the filter, sorted
        /// alphabetically as the old program listed them.</summary>
        public List<UnitDef> UnitsFor(string className, string filter)
        {
            IEnumerable<UnitDef> q = Units;
            if (!string.IsNullOrEmpty(className) && className != AllClasses)
            {
                q = q.Where(u => string.Equals(u.ClassName, className, StringComparison.OrdinalIgnoreCase));
            }
            string[] words = (filter ?? "").Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
            if (words.Length > 0)
            {
                // a word may match the unit's name, singular or aliases, or its
                // class's name: "forestry" or "basal area" lists whole classes
                q = q.Where(u => words.All(w =>
                    u.Name.IndexOf(w, StringComparison.CurrentCultureIgnoreCase) >= 0
                    || (u.Singular ?? "").IndexOf(w, StringComparison.CurrentCultureIgnoreCase) >= 0
                    || u.Aliases.Any(a => a.IndexOf(w, StringComparison.CurrentCultureIgnoreCase) >= 0)
                    || (u.ClassName ?? "").IndexOf(w, StringComparison.CurrentCultureIgnoreCase) >= 0));
            }
            return q.OrderBy(u => u.Name, StringComparer.CurrentCultureIgnoreCase).ToList();
        }

        // ---- converting ------------------------------------------------------------------

        /// <summary>Converts x from one unit to another of the same class.</summary>
        public static double Convert(double x, UnitDef from, UnitDef to)
        {
            if (from == null || to == null)
            {
                throw new ArgumentNullException(from == null ? nameof(from) : nameof(to));
            }
            if (!string.Equals(from.ClassName, to.ClassName, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "'" + from.Name + "' (" + from.ClassName + ") and '" + to.Name + "' (" + to.ClassName
                    + ") are in different measurement classes.");
            }
            if (ReferenceEquals(from, to))
            {
                return x;
            }
            return to.FromBase(from.ToBase(x));
        }

        // ---- the user's own units ------------------------------------------------------

        /// <summary>Why a user unit cannot be added, or null. <paramref name="replacing"/>
        /// is the unit being edited, so its own name is not a clash.</summary>
        public string CheckUserUnit(UnitDef u, UnitDef reference, UnitDef replacing)
        {
            if (string.IsNullOrWhiteSpace(u.Name))
            {
                return "it needs a name.";
            }
            UnitDef clash = Find(u.Name);
            if (clash != null && !ReferenceEquals(clash, replacing))
            {
                return "the name '" + u.Name + "' is already used by a unit in " + clash.ClassName + ".";
            }
            if (reference == null)
            {
                return "choose the unit it is defined against.";
            }
            if (!(Math.Abs(u.DefinedFactor) > 0) || double.IsNaN(u.DefinedFactor) || double.IsInfinity(u.DefinedFactor))
            {
                return "the number of '" + reference.Name + "' in one '" + u.Name + "' must be a number other than 0.";
            }
            if (u.DefinedOffset != 0 && reference.IsNonlinear)
            {
                return "an offset cannot be used with '" + reference.Name + "', which converts through a curve.";
            }
            for (UnitDef r = reference; r != null; r = r.Ref)
            {
                if (ReferenceEquals(r, replacing))
                {
                    return "it cannot be defined against itself.";
                }
            }
            return null;
        }

        /// <summary>Adds or replaces a user unit. Returns null, or why not.</summary>
        public string PutUserUnit(UnitDef u, UnitDef replacing)
        {
            UnitDef reference = Find(u.RefName);
            string problem = CheckUserUnit(u, reference, replacing);
            if (problem != null)
            {
                return problem;
            }
            u.Ref = reference;
            u.ClassName = reference.ClassName;
            u.IsUser = true;
            if (replacing != null)
            {
                int i = Units.IndexOf(replacing);
                Units.RemoveAt(i);
                _byName.Remove(replacing.Name);
                // units defined against the old one now point at the new one
                foreach (UnitDef other in Units.Where(x => ReferenceEquals(x.Ref, replacing)))
                {
                    other.Ref = u;
                    other.RefName = u.Name;
                }
                foreach (SavedConversion s in Saved)
                {
                    if (string.Equals(s.From, replacing.Name, StringComparison.OrdinalIgnoreCase)) s.From = u.Name;
                    if (string.Equals(s.To, replacing.Name, StringComparison.OrdinalIgnoreCase)) s.To = u.Name;
                }
                Units.Insert(i, u);
                _byName[u.Name] = u;
            }
            else
            {
                AddUnit(u);
            }
            return null;
        }

        /// <summary>The user units that are defined, directly or through
        /// others, against this one.</summary>
        public List<UnitDef> Dependents(UnitDef u) =>
            Units.Where(x => x != u && Chain(x).Contains(u)).ToList();

        private static IEnumerable<UnitDef> Chain(UnitDef u)
        {
            for (UnitDef r = u.Ref; r != null; r = r.Ref)
            {
                yield return r;
            }
        }

        /// <summary>Removes a user unit, and a user class's base unit removes
        /// the whole class. Returns null, or why not.</summary>
        public string RemoveUserUnit(UnitDef u)
        {
            if (u == null || !u.IsUser)
            {
                return "only your own units can be deleted.";
            }
            List<UnitDef> deps = Dependents(u);
            if (deps.Count > 0)
            {
                return "'" + string.Join("', '", deps.Select(d => d.Name)) + "' "
                       + (deps.Count == 1 ? "is" : "are") + " defined against it; delete or redefine "
                       + (deps.Count == 1 ? "that unit" : "those units") + " first.";
            }
            UnitClassDef cls = Classes.FirstOrDefault(c => c.IsUser && string.Equals(c.BaseUnit, u.Name, StringComparison.OrdinalIgnoreCase));
            if (cls != null)
            {
                if (Units.Any(x => x != u && string.Equals(x.ClassName, cls.Name, StringComparison.OrdinalIgnoreCase)))
                {
                    return "it is the base unit of your class '" + cls.Name + "', which still has other units.";
                }
                Classes.Remove(cls);
            }
            Units.Remove(u);
            _byName.Remove(u.Name);
            Saved.RemoveAll(s => string.Equals(s.From, u.Name, StringComparison.OrdinalIgnoreCase)
                                 || string.Equals(s.To, u.Name, StringComparison.OrdinalIgnoreCase));
            return null;
        }

        /// <summary>Adds a new measurement class with its base unit.</summary>
        public string AddUserClass(string name, string baseName, string baseSingular, string definition)
        {
            if (string.IsNullOrWhiteSpace(name) || string.IsNullOrWhiteSpace(baseName))
            {
                return "a new class needs a name and the name of its first unit.";
            }
            if (FindClass(name) != null || name.Trim() == AllClasses)
            {
                return "there is already a class named '" + name.Trim() + "'.";
            }
            if (Find(baseName) != null)
            {
                return "the name '" + baseName.Trim() + "' is already used by a unit in " + Find(baseName).ClassName + ".";
            }
            Classes.Add(new UnitClassDef { Name = name.Trim(), BaseUnit = baseName.Trim(), IsUser = true });
            AddUnit(new UnitDef
            {
                Name = baseName.Trim(),
                Singular = string.IsNullOrWhiteSpace(baseSingular) ? baseName.Trim() : baseSingular.Trim(),
                ClassName = name.Trim(),
                Definition = definition ?? "",
                IsUser = true,
            });
            return null;
        }

        public void SaveUser(string path)
        {
            var root = new JsonObject
            {
                ["schemaVersion"] = 1,
                ["note"] = "Your own measurement classes, units and saved conversions for the Unit Converter "
                           + "(Wildlife and Forestry Tools). Shared by the Unit Converter window and the "
                           + "Convert Field Units tool.",
            };
            var classes = new JsonArray();
            foreach (UnitClassDef c in Classes.Where(c => c.IsUser))
            {
                UnitDef b = Find(c.BaseUnit);
                classes.Add(new JsonObject
                {
                    ["name"] = c.Name,
                    ["base"] = c.BaseUnit,
                    ["baseSingular"] = b?.Singular ?? c.BaseUnit,
                    ["definition"] = b?.Definition ?? "",
                });
            }
            root["classes"] = classes;
            var units = new JsonArray();
            foreach (UnitDef u in Units.Where(u => u.IsUser && u.Ref != null))
            {
                var d = new JsonObject { ["unit"] = u.RefName, ["factor"] = u.DefinedFactor };
                if (u.DefinedOffset != 0)
                {
                    d["offset"] = u.DefinedOffset;
                }
                units.Add(new JsonObject
                {
                    ["name"] = u.Name,
                    ["singular"] = u.Singular,
                    ["class"] = u.ClassName,
                    ["definedAs"] = d,
                    ["definition"] = u.Definition ?? "",
                });
            }
            root["units"] = units;
            var saved = new JsonArray();
            foreach (SavedConversion s in Saved)
            {
                saved.Add(new JsonObject { ["name"] = s.Name, ["class"] = s.ClassName, ["from"] = s.From, ["to"] = s.To });
            }
            root["saved"] = saved;
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            string tmp = path + ".tmp";
            File.WriteAllText(tmp, root.ToJsonString(new JsonSerializerOptions { WriteIndented = true }), new UTF8Encoding(false));
            File.Copy(tmp, path, true);
            File.Delete(tmp);
        }
    }

    /// <summary>Reading and writing numbers the way the old program did.</summary>
    internal static class NumberText
    {
        /// <summary>
        /// Parses a decimal number, scientific notation ("1.5e3"), a fraction
        /// ("1/4") or a whole number and a fraction separated by a space
        /// ("5 1/8" = 5.125, "-5 1/8" = -5.125). Group separators of the
        /// current culture are allowed.
        /// </summary>
        public static bool TryParse(string text, out double value)
        {
            value = double.NaN;
            if (string.IsNullOrWhiteSpace(text))
            {
                return false;
            }
            string s = text.Trim();
            CultureInfo cul = CultureInfo.CurrentCulture;
            int slash = s.IndexOf('/');
            if (slash < 0)
            {
                return TryPlain(s, cul, out value);
            }
            string left = s.Substring(0, slash).Trim();
            string right = s.Substring(slash + 1).Trim();
            if (!TryPlain(right, cul, out double den) || den == 0)
            {
                return false;
            }
            double whole = 0;
            string num = left;
            int space = left.LastIndexOf(' ');
            if (space > 0)
            {
                if (!TryPlain(left.Substring(0, space), cul, out whole))
                {
                    return false;
                }
                num = left.Substring(space + 1);
            }
            if (!TryPlain(num, cul, out double n))
            {
                return false;
            }
            bool negative = whole < 0 || (space > 0 && left.TrimStart().StartsWith("-", StringComparison.Ordinal));
            double frac = Math.Abs(n / den);
            value = space > 0 ? (negative ? -(Math.Abs(whole) + frac) : whole + frac) : n / den;
            return !double.IsNaN(value) && !double.IsInfinity(value);
        }

        private static bool TryPlain(string s, CultureInfo cul, out double v)
        {
            return double.TryParse(s, NumberStyles.Float | NumberStyles.AllowThousands, cul, out v)
                   || double.TryParse(s, NumberStyles.Float | NumberStyles.AllowThousands, CultureInfo.InvariantCulture, out v);
        }

        /// <summary>The old program's guess at a sensible format: scientific
        /// for very large or very small values.</summary>
        public static bool PreferScientific(double v)
        {
            double a = Math.Abs(v);
            return a != 0 && (a >= 1e12 || a < 1e-4);
        }

        /// <summary>The value with the given number of decimal places (0 to
        /// 14), in decimal or scientific notation.</summary>
        public static string Format(double v, int places, bool scientific)
        {
            if (double.IsNaN(v))
            {
                return "undefined";
            }
            if (double.IsInfinity(v))
            {
                return v > 0 ? "infinity" : "-infinity";
            }
            places = Math.Max(0, Math.Min(14, places));
            if (scientific)
            {
                return v.ToString("E" + places.ToString(CultureInfo.InvariantCulture), CultureInfo.CurrentCulture)
                    .Replace("E+0", "E+").Replace("E-0", "E-").Replace("E+0", "E+").Replace("E-0", "E-");
            }
            return v.ToString("N" + places.ToString(CultureInfo.InvariantCulture), CultureInfo.CurrentCulture);
        }

        /// <summary>
        /// The one-to-one line of the old program: twelve significant
        /// digits, commas in front of the decimal point and a space every
        /// three digits after it, so a long factor is easy to read:
        /// 0.404 685 642 24.
        /// </summary>
        public static string Readable(double v)
        {
            if (double.IsNaN(v) || double.IsInfinity(v))
            {
                return Format(v, 0, false);
            }
            if (v != 0 && (Math.Abs(v) >= 1e15 || Math.Abs(v) < 1e-9))
            {
                return Format(v, 10, true);
            }
            string s = v.ToString("G12", CultureInfo.InvariantCulture);
            if (s.Contains("E"))
            {
                s = ((decimal)v).ToString(CultureInfo.InvariantCulture);
            }
            string sign = s.StartsWith("-", StringComparison.Ordinal) ? "-" : "";
            s = s.TrimStart('-');
            int dot = s.IndexOf('.');
            string ip = dot < 0 ? s : s.Substring(0, dot);
            string fp = dot < 0 ? "" : s.Substring(dot + 1);
            NumberFormatInfo nf = CultureInfo.CurrentCulture.NumberFormat;
            string ig = long.TryParse(ip, NumberStyles.None, CultureInfo.InvariantCulture, out long iv)
                ? iv.ToString("N0", CultureInfo.CurrentCulture) : ip;
            var sb = new StringBuilder();
            for (int i = 0; i < fp.Length; i++)
            {
                if (i > 0 && i % 3 == 0)
                {
                    sb.Append(' ');
                }
                sb.Append(fp[i]);
            }
            return sign + ig + (fp.Length > 0 ? nf.NumberDecimalSeparator + sb : "");
        }
    }
}
