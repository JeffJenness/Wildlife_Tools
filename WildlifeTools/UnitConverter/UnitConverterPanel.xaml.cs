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
using System.Reflection;
using System.Windows;
using System.Windows.Controls;

namespace WildlifeTools.UnitConverter
{
    /// <summary>
    /// The content of the Unit Converter window: three tabs. Convert Numbers
    /// converts one value at a time, as Jeff's Unit Converter of 2000 did;
    /// Convert a Field runs the Convert Field Units tool on a table of the
    /// map; My Units adds the user's own units and measurement classes.
    /// Everything that needs ArcGIS Pro is in <see cref="UnitConverterActions"/>,
    /// reached from clicks and from the Tables tab being opened, so the panel
    /// can be built and drawn outside Pro.
    /// </summary>
    public partial class UnitConverterPanel : UserControl
    {
        /// <summary>A unit as a list shows it: with its class when every
        /// class is listed.</summary>
        private sealed class UnitItem
        {
            public UnitItem(UnitDef u, bool showClass)
            {
                U = u;
                Text = showClass ? u.Name + "   · " + u.ClassName : u.Name;
            }

            public UnitDef U { get; }
            public string Text { get; }
            public override string ToString() => Text;
        }

        /// <summary>A row of the My Units list (strings only: Pro's theme
        /// styles bound numbers differently).</summary>
        public sealed class MineRow
        {
            internal UnitDef U { get; set; }
            public string Name { get; set; }
            public string ClassName { get; set; }
            public string DefinedText { get; set; }
        }

        /// <summary>A class list, FROM and TO lists, a find box above each
        /// (Jeff, 2026-10-08: a separate Find for each list) and their
        /// definitions: one on each conversion tab.</summary>
        private sealed class Picker
        {
            public ComboBox Cls;
            public TextBox FindFrom;
            public TextBox FindTo;
            public ListBox From;
            public ListBox To;
            public TextBox FromDef;
            public TextBox ToDef;

            public UnitDef FromUnit => (From.SelectedItem as UnitItem)?.U;
            public UnitDef ToUnit => (To.SelectedItem as UnitItem)?.U;
            public string ClassName => Cls.SelectedItem as string;
        }

        private UnitCatalog _cat;
        private readonly Picker _num;
        private readonly Picker _tab;
        private bool _syncing;
        private int _places = 4;
        private bool? _scientific;            // null: follow the size of the result
        private bool _outFieldTyped;
        private UnitDef _editing;
        private List<UnitConverterActions.TableChoice> _tables;

        /// <summary>Raised by the Close button.</summary>
        public event EventHandler CloseRequested;

        public UnitConverterPanel()
        {
            InitializeComponent();
            _num = new Picker { Cls = cmbClass, FindFrom = txtFindFrom, FindTo = txtFindTo, From = lstFrom, To = lstTo,
                                FromDef = txtFromDef, ToDef = txtToDef };
            _tab = new Picker { Cls = cmbClassT, FindFrom = txtFindFromT, FindTo = txtFindToT, From = lstFromT, To = lstToT,
                                FromDef = txtFromDefT, ToDef = txtToDefT };
            tabs.SelectionChanged += OnTabChanged;
            string dir = Path.GetDirectoryName(typeof(UnitConverterPanel).Assembly.Location);
            try
            {
                SetCatalog(UnitCatalog.Load(UnitCatalog.BundledPath(dir), UnitCatalog.UserPath));
            }
            catch (Exception ex)
            {
                txtStatus.Text = "The unit catalog could not be read: " + ex.Message;
            }
        }

        /// <summary>Puts a catalog in the window (also what the headless
        /// harness calls, with a temporary user file).</summary>
        internal void SetCatalog(UnitCatalog cat)
        {
            _cat = cat;
            FillClasses(_num);
            FillClasses(_tab);
            RefillUnits(_num);
            RefillUnits(_tab);
            FillSaved();
            FillMine();
            FillNewClasses();
            if (cat.Warnings.Count > 0)
            {
                txtStatus.Text = string.Join(" ", cat.Warnings);
            }
            else
            {
                txtStatus.Text = cat.Units.Count.ToString("N0", CultureInfo.CurrentCulture) + " units in "
                                 + cat.Classes.Count + " measurement classes.";
            }
            UpdateNumbers();
        }

        internal UnitCatalog Catalog => _cat;

        private void SaveUserFile()
        {
            try
            {
                _cat.SaveUser(UnitCatalog.UserPath);
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Your units could not be saved: " + ex.Message;
            }
        }

        // ---- the pickers ----------------------------------------------------------

        private void FillClasses(Picker p)
        {
            string was = p.ClassName;
            _syncing = true;
            var names = new List<string> { UnitCatalog.AllClasses };
            names.AddRange(_cat.Classes.Select(c => c.Name).OrderBy(n => n, StringComparer.CurrentCultureIgnoreCase));
            p.Cls.ItemsSource = names;
            p.Cls.SelectedItem = names.Contains(was) ? was : UnitCatalog.AllClasses;
            _syncing = false;
        }

        private void RefillUnits(Picker p)
        {
            if (_cat == null)
            {
                return;
            }
            UnitDef from = p.FromUnit, to = p.ToUnit;
            string cls = p.ClassName;
            bool all = string.IsNullOrEmpty(cls) || cls == UnitCatalog.AllClasses;
            _syncing = true;
            p.From.ItemsSource = _cat.UnitsFor(cls, p.FindFrom.Text).Select(u => new UnitItem(u, all)).ToList();
            p.To.ItemsSource = _cat.UnitsFor(cls, p.FindTo.Text).Select(u => new UnitItem(u, all)).ToList();
            Reselect(p.From, from);
            Reselect(p.To, to);
            _syncing = false;
            ShowDefinitions(p);
        }

        private static void Reselect(ListBox box, UnitDef u)
        {
            if (u == null)
            {
                return;
            }
            foreach (object o in box.Items)
            {
                if (o is UnitItem i && ReferenceEquals(i.U, u))
                {
                    box.SelectedItem = o;
                    box.ScrollIntoView(o);
                    // again once the list has been laid out, or a long list
                    // stays scrolled to its top
                    box.Dispatcher.BeginInvoke(new Action(() => box.ScrollIntoView(o)),
                        System.Windows.Threading.DispatcherPriority.Loaded);
                    return;
                }
            }
        }

        private void ShowDefinitions(Picker p)
        {
            p.FromDef.Text = DefinitionText(p.FromUnit);
            p.ToDef.Text = DefinitionText(p.ToUnit);
        }

        private string DefinitionText(UnitDef u)
        {
            if (u == null)
            {
                return "";
            }
            string head = u.Name + " (" + u.ClassName + (u.IsUser ? ", one of your units" : "") + ")";
            string def = string.IsNullOrWhiteSpace(u.Definition) ? "No definition." : u.Definition;
            if (u.Ref != null)
            {
                def = "1 " + u.LabelFor(1) + " = " + NumberText.Readable(u.DefinedFactor) + " " + u.Ref.LabelFor(u.DefinedFactor)
                      + (u.DefinedOffset != 0 ? " + " + NumberText.Readable(u.DefinedOffset) : "") + ". " + def;
            }
            string src = _cat?.SourcesText(u) ?? "";
            return head + Environment.NewLine + def + (src.Length > 0 ? Environment.NewLine + src : "");
        }

        /// <summary>A unit chosen while every class is listed picks the class,
        /// so the other list offers only the units it can convert to. The
        /// find boxes keep what was typed in them: each narrows its own list
        /// within the class.</summary>
        private void PickClassFrom(Picker p, UnitDef u)
        {
            if (u == null || (p.ClassName != null && p.ClassName != UnitCatalog.AllClasses))
            {
                return;
            }
            _syncing = true;
            p.Cls.SelectedItem = u.ClassName;
            _syncing = false;
            RefillUnits(p);
        }

        /// <summary>Sets both units of a picker and empties both find boxes
        /// (the harness and saved conversions use it).</summary>
        internal void SelectUnits(bool tablesTab, string from, string to)
        {
            SelectUnitsAndFinds(tablesTab, from, to, "", "");
        }

        /// <summary>Sets both units of a picker with these texts in the find
        /// boxes (Reverse swaps the texts with the units). A box whose text
        /// would hide its unit is emptied.</summary>
        private void SelectUnitsAndFinds(bool tablesTab, string from, string to, string findFrom, string findTo)
        {
            Picker p = tablesTab ? _tab : _num;
            UnitDef a = _cat.Find(from), b = _cat.Find(to);
            if (a == null || b == null)
            {
                return;
            }
            _syncing = true;
            p.Cls.SelectedItem = a.ClassName;
            p.FindFrom.Text = findFrom ?? "";
            p.FindTo.Text = findTo ?? "";
            _syncing = false;
            RefillUnits(p);
            _syncing = true;
            Reselect(p.From, a);
            Reselect(p.To, b);
            _syncing = false;
            if (!ReferenceEquals(p.FromUnit, a) || !ReferenceEquals(p.ToUnit, b))
            {
                _syncing = true;
                if (!ReferenceEquals(p.FromUnit, a)) p.FindFrom.Text = "";
                if (!ReferenceEquals(p.ToUnit, b)) p.FindTo.Text = "";
                _syncing = false;
                RefillUnits(p);
                _syncing = true;
                Reselect(p.From, a);
                Reselect(p.To, b);
                _syncing = false;
            }
            ShowDefinitions(p);
            if (tablesTab)
            {
                UpdateTables();
            }
            else
            {
                UpdateNumbers();
            }
        }

        // ---- Convert Numbers --------------------------------------------------------

        private void OnClassChanged(object sender, SelectionChangedEventArgs e)
        {
            if (_syncing || _cat == null) return;
            RefillUnits(_num);
            UpdateNumbers();
        }

        private void OnFindChanged(object sender, TextChangedEventArgs e)
        {
            if (_syncing || _cat == null) return;
            RefillUnits(_num);
            UpdateNumbers();
        }

        private void OnFromChanged(object sender, SelectionChangedEventArgs e)
        {
            if (_syncing) return;
            PickClassFrom(_num, _num.FromUnit);
            ShowDefinitions(_num);
            _scientific = null;
            UpdateNumbers();
        }

        private void OnToChanged(object sender, SelectionChangedEventArgs e)
        {
            if (_syncing) return;
            PickClassFrom(_num, _num.ToUnit);
            ShowDefinitions(_num);
            _scientific = null;
            UpdateNumbers();
        }

        private void OnReverse(object sender, RoutedEventArgs e)
        {
            UnitDef a = _num.FromUnit, b = _num.ToUnit;
            if (a == null || b == null) return;
            SelectUnitsAndFinds(false, b.Name, a.Name, txtFindTo.Text, txtFindFrom.Text);
        }

        private void OnInputChanged(object sender, TextChangedEventArgs e)
        {
            if (_syncing) return;
            UpdateNumbers();
        }

        private void OnPlacesMinus(object sender, RoutedEventArgs e)
        {
            _places = Math.Max(0, _places - 1);
            UpdateNumbers();
        }

        private void OnPlacesPlus(object sender, RoutedEventArgs e)
        {
            _places = Math.Min(14, _places + 1);
            UpdateNumbers();
        }

        private void OnFormatChanged(object sender, RoutedEventArgs e)
        {
            if (_syncing || rbScientific == null) return;
            _scientific = rbScientific.IsChecked == true;
            UpdateNumbers();
        }

        /// <summary>Recomputes the result and the one-to-one lines.</summary>
        internal void UpdateNumbers()
        {
            if (txtPlaces == null || _cat == null) return;
            txtPlaces.Text = _places.ToString(CultureInfo.CurrentCulture);
            UnitDef a = _num.FromUnit, b = _num.ToUnit;
            bool both = a != null && b != null;
            btnReverse.IsEnabled = both;
            btnSave.IsEnabled = both;
            btnUnsave.IsEnabled = cmbSaved.SelectedItem is SavedConversion;
            txtOneA.Text = "";
            txtOneB.Text = "";
            txtOutput.Text = "";
            btnCopy.IsEnabled = false;
            txtInUnit.Text = a?.Name ?? "";
            txtOutUnit.Text = b?.Name ?? "";
            if (!both)
            {
                return;
            }
            try
            {
                double ab = UnitCatalog.Convert(1, a, b), ba = UnitCatalog.Convert(1, b, a);
                txtOneA.Text = "1 " + a.LabelFor(1) + " = " + NumberText.Readable(ab) + " " + b.LabelFor(ab);
                txtOneB.Text = "1 " + b.LabelFor(1) + " = " + NumberText.Readable(ba) + " " + a.LabelFor(ba);
                if (a.IsNonlinear || b.IsNonlinear || HasOffset(a) || HasOffset(b))
                {
                    txtOneB.Text += Environment.NewLine + "(These units are not in simple proportion, so these lines hold only for 1; the result above holds for any value.)";
                }
            }
            catch (Exception ex)
            {
                txtOneA.Text = ex.Message;
                return;
            }
            string s = txtInput.Text;
            if (string.IsNullOrWhiteSpace(s))
            {
                return;
            }
            if (!NumberText.TryParse(s, out double x))
            {
                txtOutput.Text = "";
                txtOutUnit.Text = "(not a number yet)";
                return;
            }
            double y = UnitCatalog.Convert(x, a, b);
            bool sci = _scientific ?? NumberText.PreferScientific(y);
            _syncing = true;
            rbScientific.IsChecked = sci;
            rbDecimal.IsChecked = !sci;
            _syncing = false;
            txtOutput.Text = NumberText.Format(y, _places, sci);
            txtInUnit.Text = a.LabelFor(x);
            txtOutUnit.Text = b.LabelFor(y);
            btnCopy.IsEnabled = !double.IsNaN(y);
            if (double.IsNaN(y))
            {
                txtOutUnit.Text = b.Name + " (no value: a negative area has no diameter)";
            }
        }

        private static bool HasOffset(UnitDef u)
        {
            for (UnitDef r = u; r != null; r = r.Ref)
            {
                if (r.Offset != 0 || r.DefinedOffset != 0) return true;
            }
            return false;
        }

        private void OnCopyResult(object sender, RoutedEventArgs e)
        {
            try
            {
                Clipboard.SetText(txtOutput.Text);
                txtStatus.Text = "Copied " + txtOutput.Text + ".";
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Could not copy: " + ex.Message;
            }
        }

        // ---- saved conversions --------------------------------------------------------

        private void FillSaved()
        {
            _syncing = true;
            cmbSaved.ItemsSource = null;
            cmbSaved.ItemsSource = _cat.Saved.ToList();
            cmbSaved.SelectedIndex = -1;
            _syncing = false;
            if (btnUnsave != null) btnUnsave.IsEnabled = false;
        }

        private void OnSavedChosen(object sender, SelectionChangedEventArgs e)
        {
            if (_syncing) return;
            if (cmbSaved.SelectedItem is SavedConversion s)
            {
                SelectUnits(false, s.From, s.To);
                btnUnsave.IsEnabled = true;
                txtStatus.Text = "";
            }
        }

        private void OnSaveConversion(object sender, RoutedEventArgs e)
        {
            UnitDef a = _num.FromUnit, b = _num.ToUnit;
            if (a == null || b == null) return;
            if (_cat.Saved.Any(s => string.Equals(s.From, a.Name, StringComparison.OrdinalIgnoreCase)
                                    && string.Equals(s.To, b.Name, StringComparison.OrdinalIgnoreCase)))
            {
                txtStatus.Text = "That conversion is already saved.";
                return;
            }
            var sc = new SavedConversion { Name = a.Name + " to " + b.Name, ClassName = a.ClassName, From = a.Name, To = b.Name };
            _cat.Saved.Add(sc);
            SaveUserFile();
            FillSaved();
            _syncing = true;
            cmbSaved.SelectedItem = cmbSaved.Items.Cast<SavedConversion>().FirstOrDefault(s => s.Name == sc.Name);
            _syncing = false;
            btnUnsave.IsEnabled = true;
            txtStatus.Text = "Saved '" + sc.Name + "'. Choose it from Saved conversions to set both units in one step.";
        }

        private void OnRemoveConversion(object sender, RoutedEventArgs e)
        {
            if (!(cmbSaved.SelectedItem is SavedConversion s)) return;
            _cat.Saved.Remove(s);
            SaveUserFile();
            FillSaved();
            txtStatus.Text = "Removed '" + s.Name + "' from the saved conversions.";
        }

        // ---- Convert a Field ----------------------------------------------------------------

        private void OnTabChanged(object sender, SelectionChangedEventArgs e)
        {
            if (!ReferenceEquals(e.OriginalSource, tabs)) return;
            if (tabs.SelectedIndex == 1 && _tables == null)
            {
                RefreshTables();
            }
        }

        private void OnRefreshTables(object sender, RoutedEventArgs e) => RefreshTables();

        private async void RefreshTables()
        {
            try
            {
                string was = (cmbTable.SelectedItem as UnitConverterActions.TableChoice)?.Name;
                _tables = await UnitConverterActions.ListTablesAsync();
                cmbTable.ItemsSource = _tables;
                cmbTable.SelectedItem = _tables.FirstOrDefault(t => t.Name == was) ?? _tables.FirstOrDefault(t => t.IsSelectedInContents)
                                        ?? _tables.FirstOrDefault();
                if (_tables.Count == 0)
                {
                    txtRecords.Text = "The active map has no layers or tables with numeric fields.";
                }
            }
            catch (Exception ex)
            {
                txtRecords.Text = "Could not read the map's tables: " + ex.Message;
            }
            UpdateTables();
        }

        private void OnTableChanged(object sender, SelectionChangedEventArgs e)
        {
            var t = cmbTable.SelectedItem as UnitConverterActions.TableChoice;
            cmbField.ItemsSource = t?.Fields;
            cmbField.SelectedIndex = t != null && t.Fields.Count > 0 ? 0 : -1;
            if (t == null)
            {
                txtRecords.Text = "";
            }
            else
            {
                txtRecords.Text = t.RecordNote;
            }
            UpdateTables();
        }

        private void OnFieldChanged(object sender, SelectionChangedEventArgs e) => UpdateTables();

        private void OnClassChangedT(object sender, SelectionChangedEventArgs e)
        {
            if (_syncing || _cat == null) return;
            RefillUnits(_tab);
            UpdateTables();
        }

        private void OnFindChangedT(object sender, TextChangedEventArgs e)
        {
            if (_syncing || _cat == null) return;
            RefillUnits(_tab);
            UpdateTables();
        }

        private void OnUnitsChangedT(object sender, SelectionChangedEventArgs e)
        {
            if (_syncing) return;
            PickClassFrom(_tab, ReferenceEquals(sender, lstFromT) ? _tab.FromUnit : _tab.ToUnit);
            ShowDefinitions(_tab);
            UpdateTables();
        }

        private void OnReverseT(object sender, RoutedEventArgs e)
        {
            UnitDef a = _tab.FromUnit, b = _tab.ToUnit;
            if (a == null || b == null) return;
            SelectUnitsAndFinds(true, b.Name, a.Name, txtFindToT.Text, txtFindFromT.Text);
        }

        private void OnOutFieldTyped(object sender, TextChangedEventArgs e)
        {
            if (_syncing) return;
            _outFieldTyped = !string.IsNullOrWhiteSpace(txtOutField.Text);
            UpdateTables();
        }

        private void OnInPlaceChanged(object sender, RoutedEventArgs e) => UpdateTables();

        private void UpdateTables()
        {
            if (btnConvertField == null) return;
            var t = cmbTable.SelectedItem as UnitConverterActions.TableChoice;
            string field = cmbField.SelectedItem as string;
            UnitDef a = _tab.FromUnit, b = _tab.ToUnit;
            bool inPlace = chkInPlace.IsChecked == true;
            txtOutField.IsEnabled = !inPlace;
            _syncing = true;
            if (inPlace)
            {
                txtOutField.Text = field ?? "";
            }
            else if (!_outFieldTyped && field != null && b != null)
            {
                txtOutField.Text = SuggestField(field, b.Name, t?.IsDbf == true);
            }
            _syncing = false;
            btnConvertField.IsEnabled = t != null && field != null && a != null && b != null && !ReferenceEquals(a, b)
                                        && !string.IsNullOrWhiteSpace(txtOutField.Text);
        }

        /// <summary>The same suggestion as the Convert Field Units tool's.</summary>
        internal static string SuggestField(string field, string toUnit, bool dbf)
        {
            string tag = new string((toUnit ?? "").Where(char.IsLetterOrDigit).Take(6).ToArray());
            string name = field + "_" + (tag.Length > 0 ? tag : "conv");
            return dbf && name.Length > 10 ? name.Substring(0, 10) : name;
        }

        private async void OnConvertField(object sender, RoutedEventArgs e)
        {
            var t = cmbTable.SelectedItem as UnitConverterActions.TableChoice;
            string field = cmbField.SelectedItem as string;
            UnitDef a = _tab.FromUnit, b = _tab.ToUnit;
            if (t == null || field == null || a == null || b == null) return;
            string outField = txtOutField.Text.Trim();
            btnConvertField.IsEnabled = false;
            txtStatus.Text = "Converting " + field + " from " + a.Name + " to " + b.Name + "...";
            try
            {
                string result = await UnitConverterActions.RunConvertAsync(t, field, a.Name, b.Name, outField);
                txtStatus.Text = result;
                _outFieldTyped = false;
                RefreshTables();
            }
            catch (Exception ex)
            {
                txtStatus.Text = "The field was not converted: " + ex.Message;
            }
            finally
            {
                UpdateTables();
            }
        }

        private void OnOpenTool(object sender, RoutedEventArgs e)
        {
            var t = cmbTable.SelectedItem as UnitConverterActions.TableChoice;
            try
            {
                UnitConverterActions.OpenTool(t, cmbField.SelectedItem as string, _tab.ClassName,
                    _tab.FromUnit?.Name, _tab.ToUnit?.Name, txtOutField.Text.Trim());
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Could not open the tool: " + ex.Message;
            }
        }

        // ---- My Units ---------------------------------------------------------------------------

        private void FillMine()
        {
            var rows = new List<MineRow>();
            foreach (UnitClassDef c in _cat.Classes.Where(c => c.IsUser))
            {
                UnitDef b = _cat.Find(c.BaseUnit);
                if (b != null)
                {
                    rows.Add(new MineRow { U = b, Name = b.Name, ClassName = c.Name, DefinedText = "the first unit of your class" });
                }
            }
            foreach (UnitDef u in _cat.Units.Where(u => u.IsUser && u.Ref != null))
            {
                rows.Add(new MineRow
                {
                    U = u,
                    Name = u.Name,
                    ClassName = u.ClassName,
                    DefinedText = "1 = " + NumberText.Readable(u.DefinedFactor) + " " + u.Ref.Name
                                  + (u.DefinedOffset != 0 ? " + " + NumberText.Readable(u.DefinedOffset) : ""),
                });
            }
            lstMine.ItemsSource = rows;
        }

        private void FillNewClasses()
        {
            string was = cmbNewClass.SelectedItem as string;
            List<string> names = _cat.Classes.Select(c => c.Name).OrderBy(n => n, StringComparer.CurrentCultureIgnoreCase).ToList();
            cmbNewClass.ItemsSource = names;
            cmbNewClass.SelectedItem = names.Contains(was) ? was : names.FirstOrDefault(n => n == "Length and Distance") ?? names.FirstOrDefault();
            FillNewRefs();
        }

        private void FillNewRefs()
        {
            string was = (cmbNewRef.SelectedItem as UnitDef)?.Name;
            string cls = cmbNewClass.SelectedItem as string;
            List<UnitDef> us = _cat.UnitsFor(cls, "").Where(u => !ReferenceEquals(u, _editing)).ToList();
            cmbNewRef.ItemsSource = us;
            cmbNewRef.SelectedItem = us.FirstOrDefault(u => u.Name == was)
                                     ?? us.FirstOrDefault(u => u.Name == _cat.FindClass(cls)?.BaseUnit) ?? us.FirstOrDefault();
        }

        private void OnNewClassChanged(object sender, SelectionChangedEventArgs e) => FillNewRefs();

        private void OnNewNameChanged(object sender, TextChangedEventArgs e)
        {
            if (txtOneNew == null || txtNewSingular == null) return;
            string n = txtNewSingular.Text.Trim();
            if (n.Length == 0) n = txtNewName.Text.Trim();
            txtOneNew.Text = "1 " + (n.Length > 0 ? n : "(unit)") + " =";
        }

        private void OnMineChosen(object sender, SelectionChangedEventArgs e)
        {
            if (!(lstMine.SelectedItem is MineRow r)) return;
            UnitDef u = r.U;
            _editing = u;
            txtEditHead.Text = "Edit '" + u.Name + "'";
            txtNewName.Text = u.Name;
            txtNewSingular.Text = u.Singular;
            cmbNewClass.SelectedItem = u.ClassName;
            FillNewRefs();
            bool isBase = u.Ref == null;
            txtNewFactor.Text = isBase ? "" : u.DefinedFactor.ToString("R", CultureInfo.CurrentCulture);
            txtNewOffset.Text = isBase ? "0" : u.DefinedOffset.ToString("R", CultureInfo.CurrentCulture);
            if (!isBase) cmbNewRef.SelectedItem = u.Ref;
            txtNewDef.Text = u.Definition;
            txtNewFactor.IsEnabled = cmbNewRef.IsEnabled = txtNewOffset.IsEnabled = cmbNewClass.IsEnabled = btnSaveUnit.IsEnabled = !isBase;
            btnDeleteUnit.IsEnabled = true;
            txtStatus.Text = isBase ? "'" + u.Name + "' is the first unit of your class '" + u.ClassName
                                      + "'. Every other unit of the class is defined from it, directly or through others." : "";
        }

        private void OnNewUnit(object sender, RoutedEventArgs e)
        {
            _editing = null;
            lstMine.SelectedItem = null;
            txtEditHead.Text = "Add a unit";
            txtNewName.Text = txtNewSingular.Text = txtNewFactor.Text = txtNewDef.Text = "";
            txtNewOffset.Text = "0";
            txtNewFactor.IsEnabled = cmbNewRef.IsEnabled = txtNewOffset.IsEnabled = cmbNewClass.IsEnabled = btnSaveUnit.IsEnabled = true;
            btnDeleteUnit.IsEnabled = false;
            FillNewRefs();
        }

        private void OnSaveUnit(object sender, RoutedEventArgs e)
        {
            if (!NumberText.TryParse(txtNewFactor.Text, out double k))
            {
                txtStatus.Text = "Type how many of '" + ((cmbNewRef.SelectedItem as UnitDef)?.Name ?? "the unit") + "' make one of yours.";
                return;
            }
            double o = 0;
            if (!string.IsNullOrWhiteSpace(txtNewOffset.Text) && !NumberText.TryParse(txtNewOffset.Text, out o))
            {
                txtStatus.Text = "The offset must be a number (0 for nearly every unit).";
                return;
            }
            var u = new UnitDef
            {
                Name = txtNewName.Text.Trim(),
                Singular = string.IsNullOrWhiteSpace(txtNewSingular.Text) ? txtNewName.Text.Trim() : txtNewSingular.Text.Trim(),
                RefName = (cmbNewRef.SelectedItem as UnitDef)?.Name,
                DefinedFactor = k,
                DefinedOffset = o,
                Definition = txtNewDef.Text.Trim(),
            };
            string problem = _cat.PutUserUnit(u, _editing);
            if (problem != null)
            {
                txtStatus.Text = "Not saved: " + problem;
                return;
            }
            SaveUserFile();
            string verb = _editing == null ? "Added" : "Saved";
            _editing = u;
            AfterCatalogChange();
            txtStatus.Text = verb + " '" + u.Name + "': 1 " + u.LabelFor(1) + " = " + NumberText.Readable(k) + " " + u.Ref.LabelFor(k)
                             + ". It now converts to every unit of " + u.ClassName + ".";
        }

        private void OnDeleteUnit(object sender, RoutedEventArgs e)
        {
            if (!(lstMine.SelectedItem is MineRow r)) return;
            string problem = _cat.RemoveUserUnit(r.U);
            if (problem != null)
            {
                txtStatus.Text = "Not deleted: " + problem;
                return;
            }
            SaveUserFile();
            AfterCatalogChange();
            OnNewUnit(null, null);
            txtStatus.Text = "Deleted '" + r.Name + "'.";
        }

        private void OnAddClass(object sender, RoutedEventArgs e)
        {
            string problem = _cat.AddUserClass(txtClassName.Text, txtClassBase.Text, txtClassBaseSingular.Text, "");
            if (problem != null)
            {
                txtStatus.Text = "Not added: " + problem;
                return;
            }
            string cls = txtClassName.Text.Trim();
            SaveUserFile();
            AfterCatalogChange();
            cmbNewClass.SelectedItem = cls;
            txtClassName.Text = txtClassBase.Text = txtClassBaseSingular.Text = "";
            txtStatus.Text = "Added the class '" + cls + "'. Add its other units above, defined against its first unit.";
        }

        private void AfterCatalogChange()
        {
            FillClasses(_num);
            FillClasses(_tab);
            RefillUnits(_num);
            RefillUnits(_tab);
            FillSaved();
            FillMine();
            FillNewClasses();
            UpdateNumbers();
            UpdateTables();
        }

        // ---- the bottom bar -----------------------------------------------------------------------

        private void OnHelp(object sender, RoutedEventArgs e) => UnitConverterActions.OpenHelp();

        private void OnClose(object sender, RoutedEventArgs e) =>
            CloseRequested?.Invoke(this, EventArgs.Empty);
    }
}
