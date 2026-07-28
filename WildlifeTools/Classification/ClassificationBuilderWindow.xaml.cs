using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Globalization;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Windows;
using System.Windows.Data;
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
            row.Criteria.Add(new CritRowVM
            {
                Input = roles.FirstOrDefault() ?? "tpi",
                Units = fam == "general" ? "standardized" : "standardized",
                Op1 = "<=", Val1 = "0"
            });
        }

        private void OnRemoveCriterion(object s, RoutedEventArgs e)
        {
            if (ClassGrid.SelectedItem is ClassRowVM row && CritGrid.SelectedItem is CritRowVM c)
                row.Criteria.Remove(c);
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
                var fam = FamilyBox.SelectedItem as string ?? "slope_position";
                _current.Name = NameBox.Text.Trim();
                _current.Family = fam;
                _current.Description = string.IsNullOrWhiteSpace(DescBox.Text) ? null : DescBox.Text.Trim();
                var su = SlopeUnitsBox.SelectedItem as string;
                _current.SlopeUnits = (fam == "general" || string.IsNullOrEmpty(su)) ? null : su;
                _current.Inputs = ClassificationStore.DefaultInputs(fam);
                _current.Classes = _classes.Select(c => c.ToClassDef()).ToList();
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
        public int Value { get => _value; set { _value = value; Raise(); } }
        public string Name { get => _name; set { _name = value; Raise(); } }
        public int R { get => _r; set { _r = Clamp(value); Raise(); } }
        public int G { get => _g; set { _g = Clamp(value); Raise(); } }
        public int B { get => _b; set { _b = Clamp(value); Raise(); } }
        public ObservableCollection<CritRowVM> Criteria { get; } = new();

        private static int Clamp(int v) => v < 0 ? 0 : v > 255 ? 255 : v;

        public static ClassRowVM From(ClassDef c)
        {
            var row = new ClassRowVM { Value = c.Value, Name = c.Name };
            if (c.Color != null && c.Color.Length == 3)
            { row.R = c.Color[0]; row.G = c.Color[1]; row.B = c.Color[2]; }
            foreach (var cr in c.Criteria)
                row.Criteria.Add(CritRowVM.From(cr));
            return row;
        }

        public ClassDef ToClassDef() => new()
        {
            Value = Value,
            Name = Name?.Trim() ?? "",
            Color = new[] { R, G, B },
            Criteria = Criteria.Select(c => c.ToCriterion()).ToList()
        };

        public event PropertyChangedEventHandler PropertyChanged;
        private void Raise([CallerMemberName] string n = null)
            => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(n));
    }

    public class CritRowVM
    {
        public string Input { get; set; } = "tpi";
        public string Units { get; set; } = "standardized";
        public string Op1 { get; set; } = "<=";
        public string Val1 { get; set; } = "0";
        public string Join { get; set; } = "";
        public string Op2 { get; set; } = "";
        public string Val2 { get; set; } = "";

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
