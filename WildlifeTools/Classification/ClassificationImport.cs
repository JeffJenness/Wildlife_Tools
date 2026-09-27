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
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;

namespace WildlifeTools.Classification
{
    /// <summary>
    /// Sharing classification systems between users. A colleague's file may
    /// be one system (a file copied out of their store, or one written by
    /// Export), a JSON array of systems, or an object with a "systems"
    /// array; Scan finds every candidate in it and reports which can be
    /// imported and which cannot, and why.
    /// </summary>
    internal static class ClassificationImport
    {
        internal enum Conflict { KeepBoth, Replace, Skip }

        internal sealed class Candidate : INotifyPropertyChanged
        {
            private bool _selected;
            public bool Selected
            {
                get => _selected;
                set { _selected = value; PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Selected))); }
            }
            public ClassificationSystem System { get; set; }
            public string Name => System?.Name ?? "(unnamed)";
            public string Family => System?.Family ?? "";
            public int ClassCount => System?.Classes?.Count ?? 0;
            public string Error { get; set; }          // null when importable
            public bool Exists { get; set; }           // a store system has this name
            public bool Importable => Error == null;
            public string Status => Error != null ? "Cannot import: " + Error
                : Exists ? "A system with this name already exists"
                : "Ready";
            public event PropertyChangedEventHandler PropertyChanged;
        }

        /// <summary>Every classification system found in a JSON file.</summary>
        internal static List<Candidate> Scan(string path, IEnumerable<string> existingNames)
        {
            var names = new HashSet<string>(existingNames ?? Array.Empty<string>(),
                                            StringComparer.OrdinalIgnoreCase);
            var result = new List<Candidate>();
            string text = File.ReadAllText(path);
            using var doc = JsonDocument.Parse(text);
            var root = doc.RootElement;
            IEnumerable<JsonElement> elements;
            if (root.ValueKind == JsonValueKind.Array)
                elements = root.EnumerateArray().ToList();
            else if (root.ValueKind == JsonValueKind.Object
                     && root.TryGetProperty("systems", out var arr)
                     && arr.ValueKind == JsonValueKind.Array)
                elements = arr.EnumerateArray().ToList();
            else if (root.ValueKind == JsonValueKind.Object)
                elements = new[] { root };
            else
                return result;

            foreach (var el in elements)
            {
                var c = new Candidate();
                try
                {
                    if (el.ValueKind != JsonValueKind.Object || !el.TryGetProperty("classes", out _))
                    {
                        c.System = new ClassificationSystem { Name = "(not a classification system)" };
                        c.Error = "no classes";
                        result.Add(c);
                        continue;
                    }
                    var sys = ClassificationStore.Deserialize(el.GetRawText());
                    ClassificationStore.NormalizeRoles(sys);
                    sys.FilePath = null;
                    sys.Builtin = null;         // an import is the user's own copy
                    c.System = sys;
                    ClassificationStore.Validate(sys);
                    c.Exists = names.Contains(sys.Name);
                    c.Selected = true;
                }
                catch (Exception ex)
                {
                    c.System ??= new ClassificationSystem { Name = "(unreadable)" };
                    c.Error = ex.Message;
                }
                result.Add(c);
            }
            return result;
        }

        /// <summary>
        /// Write the chosen candidates into the store. Returns the names
        /// imported, in order. Name conflicts follow <paramref name="mode"/>.
        /// </summary>
        internal static List<string> Import(IEnumerable<Candidate> chosen, Conflict mode,
                                            IList<ClassificationSystem> store)
        {
            var done = new List<string>();
            var names = new HashSet<string>(store.Select(s => s.Name), StringComparer.OrdinalIgnoreCase);
            foreach (var c in chosen.Where(x => x.Selected && x.Importable))
            {
                var sys = c.System;
                var existing = store.FirstOrDefault(
                    s => string.Equals(s.Name, sys.Name, StringComparison.OrdinalIgnoreCase));
                if (existing != null)
                {
                    if (mode == Conflict.Skip) continue;
                    if (mode == Conflict.Replace)
                        sys.FilePath = existing.FilePath;      // overwrite that file
                    else
                    {
                        string b = sys.Name + " (imported)";
                        string n = b;
                        for (int i = 2; names.Contains(n); i++) n = $"{b} {i}";
                        sys.Name = n;
                    }
                }
                if (string.IsNullOrEmpty(sys.FilePath))
                    sys.FilePath = ClassificationStore.UniqueNewPath(sys.Name);
                ClassificationStore.Save(sys);
                names.Add(sys.Name);
                done.Add(sys.Name);
            }
            return done;
        }
    }

    /// <summary>
    /// Modal picker: the systems found in a file, a checkbox per system,
    /// and the rule for name conflicts. DialogResult true = import.
    /// </summary>
    internal sealed class ImportPickerWindow : Window
    {
        public ClassificationImport.Conflict Mode { get; private set; }
            = ClassificationImport.Conflict.KeepBoth;
        private readonly RadioButton _keep, _replace, _skip;

        public ImportPickerWindow(Window owner, string file, List<ClassificationImport.Candidate> found)
        {
            Owner = owner;
            Title = "Import classification systems";
            Width = 720; Height = 420; MinWidth = 560; MinHeight = 320;
            WindowStartupLocation = WindowStartupLocation.CenterOwner;
            ShowInTaskbar = false;

            var root = new Grid { Margin = new Thickness(12) };
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

            int ok = found.Count(c => c.Importable);
            var head = new TextBlock
            {
                Text = $"{Path.GetFileName(file)} holds {found.Count} classification "
                       + (found.Count == 1 ? "system" : "systems")
                       + (ok == found.Count ? "." : $", {ok} of which can be imported.")
                       + " Tick the ones to add to your store.",
                TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 8),
            };
            Grid.SetRow(head, 0); root.Children.Add(head);

            var grid = new DataGrid
            {
                AutoGenerateColumns = false, CanUserAddRows = false,
                HeadersVisibility = DataGridHeadersVisibility.Column,
                SelectionMode = DataGridSelectionMode.Single, IsReadOnly = false,
                ItemsSource = found,
            };
            var chk = new DataGridCheckBoxColumn
            {
                Header = "Import", Width = 60,
                Binding = new Binding("Selected") { UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged },
            };
            grid.Columns.Add(chk);
            // Name gets the spare width: system names run long, statuses do not
            grid.Columns.Add(new DataGridTextColumn { Header = "Name", Binding = new Binding("Name"), Width = new DataGridLength(1, DataGridLengthUnitType.Star), MinWidth = 200, IsReadOnly = true });
            grid.Columns.Add(new DataGridTextColumn { Header = "Family", Binding = new Binding("Family"), Width = 110, IsReadOnly = true });
            grid.Columns.Add(new DataGridTextColumn { Header = "Classes", Binding = new Binding("ClassCount"), Width = 60, IsReadOnly = true });
            grid.Columns.Add(new DataGridTextColumn { Header = "Status", Binding = new Binding("Status"), Width = DataGridLength.Auto, MinWidth = 90, IsReadOnly = true });
            Grid.SetRow(grid, 1); root.Children.Add(grid);

            var conflict = new StackPanel { Margin = new Thickness(0, 10, 0, 0) };
            conflict.Children.Add(new TextBlock
            {
                Text = "If a system with the same name is already in your store:",
                FontWeight = FontWeights.Bold, Margin = new Thickness(0, 0, 0, 4),
            });
            _keep = new RadioButton { Content = "Keep both: import the new one as \"<name> (imported)\"", IsChecked = true, Margin = new Thickness(0, 0, 0, 2) };
            _replace = new RadioButton { Content = "Replace mine with the imported one", Margin = new Thickness(0, 0, 0, 2) };
            _skip = new RadioButton { Content = "Skip it and keep mine", Margin = new Thickness(0, 0, 0, 2) };
            conflict.Children.Add(_keep); conflict.Children.Add(_replace); conflict.Children.Add(_skip);
            // the choice only matters when a listed name is already in the
            // store; showing it unconditionally reads as a claim of a clash
            conflict.Visibility = found.Any(c => c.Importable && c.Exists)
                ? Visibility.Visible : Visibility.Collapsed;
            Grid.SetRow(conflict, 2); root.Children.Add(conflict);

            var buttons = new StackPanel
            {
                Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right,
                Margin = new Thickness(0, 12, 0, 0),
            };
            var importBtn = new Button { Content = "Import", Width = 90, Margin = new Thickness(0, 0, 6, 0), IsDefault = true };
            importBtn.Click += (s, e) =>
            {
                grid.CommitEdit(DataGridEditingUnit.Row, true);
                if (!found.Any(c => c.Selected && c.Importable))
                {
                    MessageBox.Show("Tick at least one system that can be imported.",
                        "Import classification systems", MessageBoxButton.OK, MessageBoxImage.Information);
                    return;
                }
                Mode = _replace.IsChecked == true ? ClassificationImport.Conflict.Replace
                     : _skip.IsChecked == true ? ClassificationImport.Conflict.Skip
                     : ClassificationImport.Conflict.KeepBoth;
                DialogResult = true;
            };
            var cancelBtn = new Button { Content = "Cancel", Width = 90, IsCancel = true };
            buttons.Children.Add(importBtn); buttons.Children.Add(cancelBtn);
            Grid.SetRow(buttons, 3); root.Children.Add(buttons);
            Content = root;
        }
    }
}
