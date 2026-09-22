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
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Globalization;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Windows;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Media;

namespace WildlifeTools.Classification
{
    /// <summary>
    /// Editor for classification systems (the class rules, names, and colors)
    /// used by the Slope Position / Landform / General Grid Classification
    /// geoprocessing tools. Reads and writes JSON in the shared per-user store.
    /// </summary>
    public partial class ClassificationBuilderWindow
        : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private readonly ObservableCollection<ClassificationSystem> _systems = new();
        private ClassificationSystem _current;                 // system being edited
        private string _loadedName;         // name as loaded (rename detection)
        private readonly ObservableCollection<ClassRowVM> _classes = new();

        public ClassificationBuilderWindow()
        {
            InitializeComponent();
            FamilyBox.ItemsSource = ClassificationStore.Families;
            SystemList.ItemsSource = _systems;
            ClassGrid.ItemsSource = _classes;
            Reload(null);
        }

        private void Reload(string selectName)
        {
            _systems.Clear();
            foreach (var s in ClassificationStore.List())
                _systems.Add(s);
            if (selectName != null)
                SystemList.SelectedItem = _systems.FirstOrDefault(
                    s => string.Equals(s.Name, selectName, StringComparison.OrdinalIgnoreCase));
            else if (_systems.Count > 0)
                SystemList.SelectedIndex = 0;
            else
                LoadIntoEditor(null);
        }

        // ---- load a system into the editor -------------------------------
        private void OnSystemSelected(object s, System.Windows.Controls.SelectionChangedEventArgs e)
            => LoadIntoEditor(SystemList.SelectedItem as ClassificationSystem);

        private void LoadIntoEditor(ClassificationSystem sys)
        {
            _current = sys;
            _loadedName = sys?.Name;
            _classes.Clear();
            CritGrid.ItemsSource = null;
            CritHeader.Text = "Criteria (select a class above)";
            if (sys == null)
            {
                NameBox.Text = "";
                DescBox.Text = "";
                FamilyBox.SelectedItem = "slope_position";
                SlopeUnitsBox.SelectedItem = "";
                Editor.IsEnabled = false;
                UpdateRolesHint();
                return;
            }
            Editor.IsEnabled = true;
            NameBox.Text = sys.Name;
            DescBox.Text = sys.Description ?? "";
            FamilyBox.SelectedItem = sys.Family;
            SlopeUnitsBox.SelectedItem = string.IsNullOrEmpty(sys.SlopeUnits) ? "" : sys.SlopeUnits;
            foreach (var c in sys.Classes.OrderBy(c => c.Value))
                _classes.Add(ClassRowVM.From(c));
            UpdateRolesHint();
        }

        private void OnFamilyChanged(object s, System.Windows.Controls.SelectionChangedEventArgs e)
            => UpdateRolesHint();

        private void UpdateRolesHint()
        {
            var fam = FamilyBox.SelectedItem as string ?? "slope_position";
            RolesHint.Text = "Valid inputs: " + string.Join(", ", ClassificationStore.RolesFor(fam));
            // slope units are meaningless for the general (single-grid) family
            if (SlopeUnitsBox != null)
                SlopeUnitsBox.IsEnabled = fam != "general";
        }

        // ---- system list buttons -----------------------------------------
        private void OnNew(object s, RoutedEventArgs e)
        {
            var sys = new ClassificationSystem
            {
                Name = UniqueName("New Classification System"),
                Family = "slope_position",
                Classes = new List<ClassDef>
                {
                    new() { Value = 1, Name = "Class 1", Color = new[] { 120, 120, 120 },
                        Criteria = new List<Criterion> { new() { Input = "tpi",
                            Units = "standardized",
                            Conditions = new List<Condition> { new() { Op = "<=", Value = 0 } } } } }
                }
            };
            sys.Inputs = ClassificationStore.DefaultInputs(sys.Family);
            _systems.Add(sys);
            SystemList.SelectedItem = sys;
            NameBox.Focus();
            NameBox.SelectAll();
        }

        private void OnDuplicate(object s, RoutedEventArgs e)
        {
            if (!(SystemList.SelectedItem is ClassificationSystem src)) return;
            CommitEdits();
            var copy = new ClassificationSystem
            {
                Name = UniqueName(src.Name + " (copy)"),
                Family = src.Family,
                Description = src.Description,
                SlopeUnits = src.SlopeUnits,
                Inputs = ClassificationStore.DefaultInputs(src.Family),
                Classes = src.Classes.Select(CloneClass).ToList(),
            };
            _systems.Add(copy);
            SystemList.SelectedItem = copy;
        }

        private void OnDelete(object s, RoutedEventArgs e)
        {
            if (!(SystemList.SelectedItem is ClassificationSystem sys)) return;
            var r = MessageBox.Show($"Delete the classification system '{sys.Name}'?",
                "Classification System Builder", MessageBoxButton.YesNo, MessageBoxImage.Warning);
            if (r != MessageBoxResult.Yes) return;
            try { ClassificationStore.Delete(sys); }
            catch (Exception ex) { MessageBox.Show("Could not delete: " + ex.Message); }
            Reload(null);
        }

        // ---- class + criteria buttons ------------------------------------
        private void OnClassSelected(object s, System.Windows.Controls.SelectionChangedEventArgs e)
        {
            if (ClassGrid.SelectedItem is ClassRowVM row)
            {
                CritGrid.ItemsSource = row.Criteria;
                CritHeader.Text = $"Criteria for class '{row.Name}' (AND together; each is 1-2 conditions)";
            }
            else
            {
                CritGrid.ItemsSource = null;
                CritHeader.Text = "Criteria (select a class above)";
            }
        }

        private void OnAddClass(object s, RoutedEventArgs e)
        {
            int nextVal = _classes.Count == 0 ? 1 : _classes.Max(c => c.Value) + 1;
            var row = new ClassRowVM { Value = nextVal, Name = "Class " + nextVal,
                R = 150, G = 150, B = 150 };
            _classes.Add(row);
            ClassGrid.SelectedItem = row;
        }

        private void OnRemoveClass(object s, RoutedEventArgs e)
        {
            if (ClassGrid.SelectedItem is ClassRowVM row) _classes.Remove(row);
        }

        private void OnAddCriterion(object s, RoutedEventArgs e)
        {
            if (!(ClassGrid.SelectedItem is ClassRowVM row)) { OnAddClass(s, e); return; }
            var fam = FamilyBox.SelectedItem as string ?? "slope_position";
            var roles = ClassificationStore.RolesFor(fam);
            // Units defaults to "raw" (valid for every kind; the Input setter
            // re-coerces if the user switches to a slope input)
            row.Criteria.Add(new CritRowVM
            {
                Input = roles.FirstOrDefault() ?? "tpi",
                Op1 = "<=", Val1 = "0"
            });
        }

        private void OnRemoveCriterion(object s, RoutedEventArgs e)
        {
            if (ClassGrid.SelectedItem is ClassRowVM row && CritGrid.SelectedItem is CritRowVM c)
                row.Criteria.Remove(c);
        }

        // ---- color picker + numeric-only input plumbing -------------------
        /// <summary>Picker pick -> row, immediately (see the XAML comment: a
        /// TwoWay template-column binding defers to ROW commit, which left the
        /// R/G/B cells stale and could overwrite freshly typed values). The
        /// VM's equality guard breaks the resulting feedback loop.</summary>
        private void OnClassColorChanged(object sender,
            ArcGIS.Desktop.Mapping.Controls.ColorChangedEventArgs e)
        {
            if ((sender as FrameworkElement)?.DataContext is ClassRowVM row
                && e?.Color != null)
                row.CimColor = e.Color;
        }

        private static readonly HashSet<string> ClassIntColumns =
            new() { "Value", "R", "G", "B", "Opacity %" };
        private static readonly HashSet<string> CritNumberColumns =
            new() { "Value", "Value 2" };

        private static void FilterNumericInput(string header,
            HashSet<string> numericCols, bool allowDecimal, TextCompositionEventArgs e)
        {
            if (header == null || !numericCols.Contains(header))
                return;                          // free-text column: leave alone
            bool signed = header.StartsWith("Value");   // class/criterion values
            foreach (char ch in e.Text)
            {
                bool ok = char.IsDigit(ch) || (signed && ch == '-')
                          || (allowDecimal && ch == '.');
                if (!ok) { e.Handled = true; return; }   // swallow the keystroke
            }
        }

        private void OnClassGridPreviewTextInput(object s, TextCompositionEventArgs e)
            => FilterNumericInput(ClassGrid.CurrentColumn?.Header as string,
                                  ClassIntColumns, allowDecimal: false, e);

        private void OnCritGridPreviewTextInput(object s, TextCompositionEventArgs e)
            => FilterNumericInput(CritGrid.CurrentColumn?.Header as string,
                                  CritNumberColumns, allowDecimal: true, e);

        // space bar does not go through PreviewTextInput -- block it separately
        private void OnClassGridPreviewKeyDown(object s, KeyEventArgs e)
        {
            if (e.Key == Key.Space && ClassIntColumns.Contains(
                    ClassGrid.CurrentColumn?.Header as string ?? ""))
                e.Handled = true;
        }

        private void OnCritGridPreviewKeyDown(object s, KeyEventArgs e)
        {
            if (e.Key == Key.Space && CritNumberColumns.Contains(
                    CritGrid.CurrentColumn?.Header as string ?? ""))
                e.Handled = true;
        }

        // ---- save --------------------------------------------------------
        private void CommitEdits()
        {
            // force the DataGrids to push any in-progress cell edit
            ClassGrid.CommitEdit(System.Windows.Controls.DataGridEditingUnit.Row, true);
            CritGrid.CommitEdit(System.Windows.Controls.DataGridEditingUnit.Row, true);
        }

        private void OnSave(object s, RoutedEventArgs e)
        {
            if (_current == null) return;
            CommitEdits();
            try
            {
                // Renamed an existing system? Offer save-as-new so an edited
                // copy does not silently overwrite the original file.
                string newName = NameBox.Text.Trim();
                if (!string.IsNullOrEmpty(_current.FilePath)
                    && !string.IsNullOrWhiteSpace(_loadedName)
                    && !newName.Equals(_loadedName, StringComparison.OrdinalIgnoreCase))
                {
                    var r = MessageBox.Show(
                        $"You renamed '{_loadedName}' to '{newName}'.\n\n"
                        + $"Yes  =  save as a NEW classification system (keep '{_loadedName}' unchanged)\n"
                        + "No   =  overwrite the original with the new name\n"
                        + "Cancel  =  do not save",
                        "Classification System Builder",
                        MessageBoxButton.YesNoCancel, MessageBoxImage.Question);
                    if (r == MessageBoxResult.Cancel) return;
                    if (r == MessageBoxResult.Yes)
                        _current.FilePath = null;   // Save() creates a new file
                }

                var fam = FamilyBox.SelectedItem as string ?? "slope_position";
                _current.Name = newName;
                _current.Family = fam;
                _current.Description = string.IsNullOrWhiteSpace(DescBox.Text) ? null : DescBox.Text.Trim();
                var su = SlopeUnitsBox.SelectedItem as string;
                _current.SlopeUnits = (fam == "general" || string.IsNullOrEmpty(su)) ? null : su;
                _current.Inputs = ClassificationStore.DefaultInputs(fam);
                _current.Classes = _classes.Select(c => c.ToClassDef()).ToList();

                // slope criteria carry their own units now: keep the system's
                // slopeUnits declaration in lock-step with them
                var suCrit = _current.Classes.SelectMany(c => c.Criteria)
                    .Where(cr => cr.Input == "slope"
                                 && (cr.Units == "degrees" || cr.Units == "percent"))
                    .Select(cr => cr.Units).FirstOrDefault();
                if (suCrit != null)
                {
                    var su2 = suCrit == "percent" ? "Percent" : "Degrees";
                    _current.SlopeUnits = su2;
                    SlopeUnitsBox.SelectedItem = su2;
                }

                // structural validation first (throws with a clear message)...
                ClassificationStore.Validate(_current);
                // ...then the range sanity check: gaps, overlaps, mixed units
                var issues = CoverageAnalysis.Analyze(_current);
                if (issues.Count > 0)
                {
                    var msg = "Checking the class ranges found:\n\n  •  "
                        + string.Join("\n  •  ", issues)
                        + "\n\nSave anyway?";
                    if (MessageBox.Show(msg, "Classification System Builder",
                            MessageBoxButton.YesNo, MessageBoxImage.Warning)
                        != MessageBoxResult.Yes)
                        return;
                }

                ClassificationStore.Save(_current);
                Reload(_current.Name);
                MessageBox.Show($"Saved '{_current.Name}'.", "Classification System Builder",
                    MessageBoxButton.OK, MessageBoxImage.Information);
            }
            catch (Exception ex)
            {
                MessageBox.Show("Could not save this system:\n\n" + ex.Message,
                    "Classification System Builder", MessageBoxButton.OK, MessageBoxImage.Warning);
            }
        }

        private void OnClose(object s, RoutedEventArgs e) => Close();

        private string UniqueName(string baseName)
        {
            var names = new HashSet<string>(_systems.Select(x => x.Name), StringComparer.OrdinalIgnoreCase);
            if (!names.Contains(baseName)) return baseName;
            for (int i = 2; ; i++)
                if (!names.Contains($"{baseName} {i}")) return $"{baseName} {i}";
        }

        private static ClassDef CloneClass(ClassDef c) => new()
        {
            Value = c.Value, Name = c.Name,
            Color = c.Color?.ToArray(),
            Alpha = c.Alpha,
            Criteria = c.Criteria.Select(cr => new Criterion
            {
                Input = cr.Input, Units = cr.Units,
                Conditions = cr.Conditions.Select(cd => new Condition
                { Join = cd.Join, Op = cd.Op, Value = cd.Value }).ToList()
            }).ToList()
        };
    }

    // ---------------------------------------------------------------------
    //  Editing view-models
    // ---------------------------------------------------------------------
    public class ClassRowVM : INotifyPropertyChanged
    {
        private int _value; private string _name = ""; private int _r = 150, _g = 150, _b = 150;
        private int _a = 100;                        // percent opacity, 0-100
        public int Value { get => _value; set { _value = value; Raise(); } }
        public string Name { get => _name; set { _name = value; Raise(); } }
        public int R { get => _r; set { _r = Clamp(value); Raise(); Raise(nameof(CimColor)); } }
        public int G { get => _g; set { _g = Clamp(value); Raise(); Raise(nameof(CimColor)); } }
        public int B { get => _b; set { _b = Clamp(value); Raise(); Raise(nameof(CimColor)); } }
        public int A { get => _a; set { _a = Clamp100(value); Raise(); Raise(nameof(CimColor)); } }
        public ObservableCollection<CritRowVM> Criteria { get; } = new();

        /// <summary>The class color as a CIM color, two-way bound to Esri's
        /// ColorPickerControl in the Color column. Reading builds a CIMRGBColor
        /// from R/G/B; assigning converts the picked color (RGB, HSV, gray, or
        /// CMYK -- whatever model the picker's dialog was left in) back to
        /// R/G/B. Unknown models leave the color unchanged.</summary>
        public ArcGIS.Core.CIM.CIMColor CimColor
        {
            get => new ArcGIS.Core.CIM.CIMRGBColor { R = _r, G = _g, B = _b, Alpha = _a };
            set
            {
                if (value == null)
                {
                    // The picker nulls its SelectedColor on some focus
                    // transitions (e.g. tabbed-through). Never accept null:
                    // push the row's true color straight back so the swatch
                    // heals instead of going blank.
                    Raise(nameof(CimColor));
                    return;
                }
                if (!TryToRgb(value, out int r, out int g, out int b))
                    return;
                r = Clamp(r); g = Clamp(g); b = Clamp(b);
                // every CIM color model carries Alpha (0-100) on the base class
                int a = Clamp100((int)System.Math.Round(value.Alpha));
                if (r == _r && g == _g && b == _b && a == _a)
                    return;      // no-op: breaks picker<->binding feedback loops
                _r = r; _g = g; _b = b; _a = a;
                Raise(nameof(R)); Raise(nameof(G)); Raise(nameof(B));
                Raise(nameof(A)); Raise(nameof(CimColor));
            }
        }

        private static bool TryToRgb(ArcGIS.Core.CIM.CIMColor c, out int r, out int g, out int b)
        {
            r = g = b = 0;
            switch (c)
            {
                case ArcGIS.Core.CIM.CIMRGBColor rgb:
                    r = (int)System.Math.Round(rgb.R);
                    g = (int)System.Math.Round(rgb.G);
                    b = (int)System.Math.Round(rgb.B);
                    return true;
                case ArcGIS.Core.CIM.CIMHSVColor hsv:
                {
                    // H 0-360, S/V 0-100 -> RGB 0-255
                    double h = hsv.H, s = hsv.S / 100.0, v = hsv.V / 100.0;
                    double cc = v * s, x = cc * (1 - System.Math.Abs(h / 60.0 % 2 - 1)), m = v - cc;
                    (double rr, double gg, double bb) =
                        h < 60 ? (cc, x, 0.0) : h < 120 ? (x, cc, 0.0) : h < 180 ? (0.0, cc, x)
                        : h < 240 ? (0.0, x, cc) : h < 300 ? (x, 0.0, cc) : (cc, 0.0, x);
                    r = (int)System.Math.Round((rr + m) * 255);
                    g = (int)System.Math.Round((gg + m) * 255);
                    b = (int)System.Math.Round((bb + m) * 255);
                    return true;
                }
                case ArcGIS.Core.CIM.CIMGrayColor gray:
                    r = g = b = (int)System.Math.Round(gray.Level);
                    return true;
                case ArcGIS.Core.CIM.CIMCMYKColor cmyk:
                    // 0-100 each; standard naive conversion
                    r = (int)System.Math.Round(255 * (1 - cmyk.C / 100.0) * (1 - cmyk.K / 100.0));
                    g = (int)System.Math.Round(255 * (1 - cmyk.M / 100.0) * (1 - cmyk.K / 100.0));
                    b = (int)System.Math.Round(255 * (1 - cmyk.Y / 100.0) * (1 - cmyk.K / 100.0));
                    return true;
                default:
                    return false;                 // null / Lab / spot: keep current
            }
        }

        private static int Clamp(int v) => v < 0 ? 0 : v > 255 ? 255 : v;
        private static int Clamp100(int v) => v < 0 ? 0 : v > 100 ? 100 : v;

        public static ClassRowVM From(ClassDef c)
        {
            var row = new ClassRowVM { Value = c.Value, Name = c.Name };
            if (c.Color != null && c.Color.Length == 3)
            { row.R = c.Color[0]; row.G = c.Color[1]; row.B = c.Color[2]; }
            row.A = (int)System.Math.Round(c.Alpha ?? 100);
            foreach (var cr in c.Criteria)
                row.Criteria.Add(CritRowVM.From(cr));
            return row;
        }

        public ClassDef ToClassDef() => new()
        {
            Value = Value,
            Name = Name?.Trim() ?? "",
            Color = new[] { R, G, B },
            Alpha = A >= 100 ? null : A,       // omit when fully opaque
            Criteria = Criteria.Select(c => c.ToCriterion()).ToList()
        };

        public event PropertyChangedEventHandler PropertyChanged;
        private void Raise([CallerMemberName] string n = null)
            => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(n));
    }

    public class CritRowVM : INotifyPropertyChanged
    {
        private string _input = "tpi";
        private string _units = "raw";          // STORED form (schema value)

        public string Input
        {
            get => _input;
            set
            {
                if (string.IsNullOrEmpty(value) || value == _input) return;
                _input = value;
                // keep Units legal for the new input's kind
                var allowed = ClassificationStore.UnitsForKind(
                    ClassificationStore.KindOfRole(_input));
                if (System.Array.IndexOf(allowed, _units) < 0)
                    _units = ClassificationStore.KindOfRole(_input) == "slope"
                             ? "degrees" : "raw";
                Raise(); Raise(nameof(Units));
                Raise(nameof(UnitsChoices)); Raise(nameof(UnitsDisplay));
            }
        }

        public string Units
        {
            get => _units;
            set
            {
                if (string.IsNullOrEmpty(value) || value == _units) return;
                _units = value;
                Raise(); Raise(nameof(UnitsDisplay));
            }
        }

        /// <summary>Units choices shown for the CURRENT input's kind
        /// (display labels; mapped to stored schema values below).</summary>
        public System.Collections.Generic.IEnumerable<string> UnitsChoices =>
            ClassificationStore.KindOfRole(_input) switch
            {
                "slope" => new[] { "Degrees", "Percent" },
                "external" => new[] { "Cell Values", "Standardized (z-score)" },
                _ => ClassificationStore.TpiUnits,   // raw/standardized/sd/pctl
            };

        /// <summary>Display label bound to the Units combo; converts to and
        /// from the stored schema value ("raw" on a slope role is the legacy
        /// alias and displays as Degrees).</summary>
        public string UnitsDisplay
        {
            get => ClassificationStore.KindOfRole(_input) switch
            {
                "slope" => _units == "percent" ? "Percent" : "Degrees",
                "external" => _units == "standardized"
                              ? "Standardized (z-score)" : "Cell Values",
                _ => _units,
            };
            set
            {
                if (string.IsNullOrEmpty(value)) return;
                string stored = ClassificationStore.KindOfRole(_input) switch
                {
                    "slope" => value == "Percent" ? "percent" : "degrees",
                    "external" => value == "Standardized (z-score)"
                                  ? "standardized" : "raw",
                    _ => value,
                };
                if (stored == _units) return;
                _units = stored;
                Raise(); Raise(nameof(Units));
            }
        }

        public string Op1 { get; set; } = "<=";
        public string Val1 { get; set; } = "0";
        public string Join { get; set; } = "";
        public string Op2 { get; set; } = "";
        public string Val2 { get; set; } = "";

        public event PropertyChangedEventHandler PropertyChanged;
        private void Raise([CallerMemberName] string n = null)
            => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(n));

        public static CritRowVM From(Criterion c)
        {
            var vm = new CritRowVM { Input = c.Input, Units = c.Units };
            if (c.Conditions.Count > 0)
            { vm.Op1 = c.Conditions[0].Op; vm.Val1 = Fmt(c.Conditions[0].Value); }
            if (c.Conditions.Count > 1)
            { vm.Join = c.Conditions[1].Join ?? "and"; vm.Op2 = c.Conditions[1].Op;
              vm.Val2 = Fmt(c.Conditions[1].Value); }
            return vm;
        }

        public Criterion ToCriterion()
        {
            var crit = new Criterion { Input = Input, Units = Units, Conditions = new List<Condition>() };
            crit.Conditions.Add(new Condition { Op = Op1, Value = Parse(Val1) });
            if (!string.IsNullOrWhiteSpace(Join) && !string.IsNullOrWhiteSpace(Op2)
                && !string.IsNullOrWhiteSpace(Val2))
                crit.Conditions.Add(new Condition { Join = Join, Op = Op2, Value = Parse(Val2) });
            return crit;
        }

        private static double Parse(string s)
        {
            if (double.TryParse(s, NumberStyles.Any, CultureInfo.InvariantCulture, out var v))
                return v;
            throw new System.IO.InvalidDataException($"'{s}' is not a number.");
        }
        private static string Fmt(double v) => v.ToString(CultureInfo.InvariantCulture);
    }

    /// <summary>R,G,B ints -> a brush, for the class color swatch.</summary>
    public class RgbToBrushConverter : IMultiValueConverter
    {
        public object Convert(object[] v, Type t, object p, CultureInfo c)
        {
            try
            {
                byte R = System.Convert.ToByte(v[0]);
                byte G = System.Convert.ToByte(v[1]);
                byte B = System.Convert.ToByte(v[2]);
                return new SolidColorBrush(Color.FromRgb(R, G, B));
            }
            catch { return Brushes.Transparent; }
        }
        public object[] ConvertBack(object v, Type[] t, object p, CultureInfo c)
            => throw new NotSupportedException();
    }
}
