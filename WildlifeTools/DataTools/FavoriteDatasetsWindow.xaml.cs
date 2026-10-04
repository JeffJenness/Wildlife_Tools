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
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using ArcGIS.Desktop.Catalog;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.DataTools
{
    /// <summary>
    /// Load Favorite Datasets: the Pro recreation of the ArcMap tool
    /// (frmLoadRefDatasets) -- four user-named groups of favorite datasets,
    /// each a checkbox list with Add / Remove / Edit Description, a single
    /// "Add Selected to Map" that loads every checked item from every group
    /// (local data and web services alike), JSON persistence in the user
    /// profile, and RED names for datasets that appear unavailable.
    /// </summary>
    public partial class FavoriteDatasetsWindow : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        // ---- view models ----------------------------------------------------
        public sealed class ItemVM : INotifyPropertyChanged
        {
            private bool _checked;
            private bool _unavailable;
            public string Description { get; set; } = "";
            public string Path { get; set; } = "";
            public string TypeId { get; set; } = "";
            public bool IsTable { get; set; }
            public string Added { get; set; } = "";

            public bool IsChecked
            {
                get => _checked;
                set { _checked = value; Notify(nameof(IsChecked)); }
            }
            public bool IsUnavailable
            {
                get => _unavailable;
                set { _unavailable = value; Notify(nameof(IsUnavailable)); }
            }
            public string InfoTip =>
                Path + "\n" + (string.IsNullOrEmpty(TypeId) ? "dataset" : TypeId) +
                (IsTable ? "  (standalone table)" : "") +
                (string.IsNullOrEmpty(Added) ? "" : "\nAdded " + Added);

            public event PropertyChangedEventHandler PropertyChanged;
            private void Notify(string n) =>
                PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(n));
        }

        public sealed class GroupVM : INotifyPropertyChanged
        {
            private string _name = "";
            private readonly Action _dirty;
            public GroupVM(Action markDirty) { _dirty = markDirty; }
            public string Name
            {
                get => _name;
                set
                {
                    if (_name == value) return;
                    _name = value;
                    _dirty?.Invoke();
                    PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Name)));
                }
            }
            public ObservableCollection<ItemVM> Items { get; } =
                new ObservableCollection<ItemVM>();
            public event PropertyChangedEventHandler PropertyChanged;
        }

        private readonly List<GroupVM> _groups = new List<GroupVM>();
        private bool _dirty;
        private bool _loadingVm;

        public FavoriteDatasetsWindow()
        {
            InitializeComponent();
            var store = FavoritesStore.Load();
            var slots = new[] { grp1, grp2, grp3, grp4 };
            for (int i = 0; i < 4; i++)
            {
                var g = new GroupVM(MarkDirty) ;
                _loadingVm = true;
                g.Name = store.Groups[i].Name;
                _loadingVm = false;
                foreach (var it in store.Groups[i].Items)
                    g.Items.Add(new ItemVM
                    {
                        Description = it.Description,
                        Path = it.Path,
                        TypeId = it.TypeId,
                        IsTable = it.IsTable,
                        Added = it.Added,
                    });
                _groups.Add(g);
                slots[i].Content = g;
            }
            _dirty = false;
            SetStatus("Check the datasets to load, then click Add Selected to Map.");
            Loaded += async (_, __) => await RefreshAvailabilityAsync();
        }

        private void MarkDirty()
        {
            if (!_loadingVm) _dirty = true;
        }

        private void SetStatus(string t) => txtStatus.Text = t;

        // ---- availability ---------------------------------------------------
        private async System.Threading.Tasks.Task RefreshAvailabilityAsync()
        {
            var all = _groups.SelectMany(g => g.Items).ToList();
            if (all.Count == 0) return;
            SetStatus("Checking dataset availability...");
            int bad = 0;
            var tasks = all.Select(async vm =>
            {
                bool? ok = await FavoriteAvailability.CheckAsync(vm.Path);
                // null = undeterminable (e.g. .sde): treat as available.
                vm.IsUnavailable = ok == false;
                if (ok == false) System.Threading.Interlocked.Increment(ref bad);
            });
            await System.Threading.Tasks.Task.WhenAll(tasks);
            SetStatus(bad == 0
                ? "All listed datasets appear to be available."
                : bad + " dataset(s) appear to be UNAVAILABLE (shown in red).");
        }

        private async void OnRecheckClick(object sender, RoutedEventArgs e) =>
            await RefreshAvailabilityAsync();

        // ---- add / remove / edit -------------------------------------------
        private GroupVM GroupOf(object sender) =>
            (sender as FrameworkElement)?.Tag as GroupVM;

        private async void OnAddClick(object sender, RoutedEventArgs e)
        {
            var g = GroupOf(sender);
            if (g == null) return;
            var dlg = new OpenItemDialog
            {
                Title = "Find dataset(s) to add to '" + g.Name + "'",
                MultiSelect = true,
                BrowseFilter = new BrowseProjectFilter("esri_browseDialogFilters_all"),
            };
            if (dlg.ShowDialog() != true || dlg.Items == null) return;
            int added = 0;
            foreach (Item item in dlg.Items)
            {
                if (item == null || string.IsNullOrWhiteSpace(item.Path))
                    continue;
                string desc = Prompt("Description for this dataset:",
                    "Add Favorite", item.Title ?? item.Name ?? item.Path);
                if (desc == null) continue;            // canceled: skip item
                if (string.IsNullOrWhiteSpace(desc))
                    desc = item.Title ?? item.Path;
                string tid = item.TypeID ?? "";
                var vm = new ItemVM
                {
                    Description = desc.Trim(),
                    Path = item.Path,
                    TypeId = tid,
                    IsTable = tid.IndexOf("table", StringComparison.OrdinalIgnoreCase) >= 0
                              && tid.IndexOf("feature", StringComparison.OrdinalIgnoreCase) < 0,
                    Added = DateTime.Now.ToString("yyyy-MM-dd"),
                };
                g.Items.Add(vm);
                added++;
            }
            if (added > 0)
            {
                _dirty = true;
                SetStatus(added + " dataset(s) added to '" + g.Name +
                          "' (remember to Save Changes).");
                await RefreshAvailabilityAsync();
            }
        }

        private void OnRemoveClick(object sender, RoutedEventArgs e)
        {
            var g = GroupOf(sender);
            if (g == null) return;
            var doomed = g.Items.Where(i => i.IsChecked).ToList();
            if (doomed.Count == 0)
            {
                MessageBox.Show("Check the item(s) to remove first.",
                    "Load Favorite Datasets");
                return;
            }
            foreach (var d in doomed) g.Items.Remove(d);
            _dirty = true;
            SetStatus(doomed.Count + " item(s) removed from '" + g.Name +
                      "' (remember to Save Changes).");
        }

        private void OnEditClick(object sender, RoutedEventArgs e)
        {
            var g = GroupOf(sender);
            if (g == null) return;
            var chosen = g.Items.Where(i => i.IsChecked).ToList();
            if (chosen.Count == 0)
            {
                MessageBox.Show("Check the item(s) whose description you want "
                    + "to edit first.", "Load Favorite Datasets");
                return;
            }
            int edited = 0;
            foreach (var vm in chosen)
            {
                string d = Prompt("Description:", "Edit Description", vm.Description);
                if (d == null || string.IsNullOrWhiteSpace(d) ||
                    d.Trim() == vm.Description)
                    continue;
                vm.Description = d.Trim();
                edited++;
            }
            if (edited > 0)
            {
                _dirty = true;
                // Rebuild the bindings for edited rows (Description has no
                // change notification; a remove/insert refresh is simplest).
                for (int i = 0; i < g.Items.Count; i++)
                {
                    var vm = g.Items[i];
                    g.Items.RemoveAt(i);
                    g.Items.Insert(i, vm);
                }
                SetStatus(edited + " description(s) updated (remember to Save "
                          + "Changes).");
            }
        }

        private void OnItemDoubleClick(object sender, System.Windows.Input.MouseButtonEventArgs e)
        {
            var vm = ((e.OriginalSource as FrameworkElement)?.DataContext) as ItemVM;
            if (vm == null) return;
            MessageBox.Show(
                vm.Description + Environment.NewLine + Environment.NewLine +
                "Path:  " + vm.Path + Environment.NewLine +
                "Type:  " + (string.IsNullOrEmpty(vm.TypeId) ? "dataset" : vm.TypeId) +
                (vm.IsTable ? "  (standalone table)" : "") + Environment.NewLine +
                (string.IsNullOrEmpty(vm.Added) ? "" : "Added: " + vm.Added + Environment.NewLine) +
                "Status: " + (vm.IsUnavailable
                    ? "UNAVAILABLE (path or server not found)" : "available"),
                "Favorite Dataset");
        }

        // ---- load to map ----------------------------------------------------
        private async void OnLoadClick(object sender, RoutedEventArgs e)
        {
            var chosen = _groups.SelectMany(g => g.Items)
                                .Where(i => i.IsChecked).ToList();
            if (chosen.Count == 0)
            {
                MessageBox.Show("Check at least one dataset first.",
                    "Load Favorite Datasets");
                return;
            }
            var map = MapView.Active?.Map;
            if (map == null)
            {
                MessageBox.Show("Open a map view first.", "Load Favorite Datasets");
                return;
            }
            btnLoad.IsEnabled = false;
            SetStatus("Adding " + chosen.Count + " dataset(s) to the map...");
            var failures = new List<string>();
            try
            {
                await QueuedTask.Run(() =>
                {
                    // Tables first, in selection order.
                    foreach (var vm in chosen.Where(v => v.IsTable))
                    {
                        try
                        {
                            StandaloneTableFactory.Instance.CreateStandaloneTable(
                                new Uri(vm.Path, UriKind.Absolute), map);
                        }
                        catch (Exception ex)
                        {
                            failures.Add(vm.Description + "  (" + ex.Message + ")");
                        }
                    }
                    // Layers are created directly in their cartographic place
                    // (points above lines above polygons above rasters) by Pro's
                    // AutoArrange, rather than added at the top and then moved:
                    // one change to the Contents pane per layer instead of two
                    // (an add immediately followed by a move could race Pro's
                    // Contents tree while it was still building the new row).
                    // AutoArrange puts each new layer at the top of its type
                    // group, so adding in REVERSE selection order leaves several
                    // layers of one type in the order they were chosen.
                    foreach (var vm in chosen.Where(v => !v.IsTable).Reverse())
                    {
                        try
                        {
                            var lcp = new LayerCreationParams(new Uri(vm.Path, UriKind.Absolute))
                            {
                                MapMemberPosition = MapMemberPosition.AutoArrange,
                            };
                            LayerFactory.Instance.CreateLayer<Layer>(lcp, map);
                        }
                        catch (Exception ex)
                        {
                            failures.Add(vm.Description + "  (" + ex.Message + ")");
                        }
                    }
                });
            }
            finally { btnLoad.IsEnabled = true; }
            int okCount = chosen.Count - failures.Count;
            SetStatus(okCount + " dataset(s) added to the map" +
                      (failures.Count > 0 ? "; " + failures.Count + " failed." : "."));
            if (failures.Count > 0)
                MessageBox.Show("These dataset(s) could not be loaded:" +
                    Environment.NewLine + Environment.NewLine +
                    string.Join(Environment.NewLine, failures),
                    "Load Favorite Datasets");
        }


        // ---- save / close ---------------------------------------------------
        private FavoritesStore ToStore()
        {
            var s = new FavoritesStore();
            foreach (var g in _groups)
                s.Groups.Add(new FavoriteGroup
                {
                    Name = string.IsNullOrWhiteSpace(g.Name)
                        ? "Group " + (s.Groups.Count + 1) : g.Name.Trim(),
                    Items = g.Items.Select(i => new FavoriteItem
                    {
                        Description = i.Description,
                        Path = i.Path,
                        TypeId = i.TypeId,
                        IsTable = i.IsTable,
                        Added = i.Added,
                    }).ToList(),
                });
            return s;
        }

        private bool SaveNow()
        {
            try
            {
                ToStore().Save();
                _dirty = false;
                SetStatus("Favorites saved.");
                return true;
            }
            catch (Exception ex)
            {
                MessageBox.Show("Could not save the favorites: " + ex.Message,
                    "Load Favorite Datasets");
                return false;
            }
        }

        private void OnSaveClick(object sender, RoutedEventArgs e)
        {
            if (SaveNow())
                ShowToast(sender, "✓ Changes saved");
        }

        private void OnHelp(object sender, RoutedEventArgs e)
            => WebHelp.Open("data-tools/load-favorite-datasets.html");

        private void OnCloseClick(object sender, RoutedEventArgs e) => Close();

        // ---- transient toast (same idiom as the stats windows) --------------
        private System.Windows.Controls.Primitives.Popup _toast;
        private System.Windows.Threading.DispatcherTimer _toastTimer;

        private void ShowToast(object nearButton, string message)
        {
            if (_toast == null)
            {
                _toast = new System.Windows.Controls.Primitives.Popup
                {
                    AllowsTransparency = true,
                    Placement = System.Windows.Controls.Primitives.PlacementMode.Top,
                    StaysOpen = true,
                    PopupAnimation = System.Windows.Controls.Primitives.PopupAnimation.Fade,
                };
                _toastTimer = new System.Windows.Threading.DispatcherTimer
                { Interval = TimeSpan.FromSeconds(2.0) };
                _toastTimer.Tick += (s2, e2) => { _toastTimer.Stop(); _toast.IsOpen = false; };
                Closed += (s2, e2) => { _toastTimer.Stop(); _toast.IsOpen = false; };
            }
            _toastTimer.Stop();
            _toast.IsOpen = false;
            _toast.PlacementTarget = nearButton as UIElement ?? this;
            _toast.Child = new System.Windows.Controls.Border
            {
                Background = new System.Windows.Media.SolidColorBrush(
                    System.Windows.Media.Color.FromArgb(235, 45, 45, 45)),
                CornerRadius = new CornerRadius(4),
                Padding = new Thickness(10, 6, 10, 6),
                Margin = new Thickness(0, 0, 0, 6),
                Child = new TextBlock
                {
                    Text = message,
                    Foreground = System.Windows.Media.Brushes.White,
                    FontSize = 12,
                },
            };
            _toast.IsOpen = true;
            _toastTimer.Start();
        }

        protected override void OnClosing(CancelEventArgs e)
        {
            if (_dirty)
            {
                var r = MessageBox.Show(
                    "You have unsaved changes to your favorites lists. Click "
                    + "Yes to save them, No to close without saving, or Cancel "
                    + "to return to the dialog.",
                    "Unsaved Changes", MessageBoxButton.YesNoCancel);
                if (r == MessageBoxResult.Yes)
                {
                    if (!SaveNow()) e.Cancel = true;
                }
                else if (r == MessageBoxResult.Cancel)
                    e.Cancel = true;
            }
            base.OnClosing(e);
        }

        // ---- small input prompt --------------------------------------------
        private string Prompt(string label, string title, string initial)
        {
            var box = new TextBox
            {
                Text = initial ?? "",
                Margin = new Thickness(0, 6, 0, 10),
                MinWidth = 380,
            };
            string result = null;
            var win = new Window
            {
                Title = title,
                Owner = this,
                SizeToContent = SizeToContent.WidthAndHeight,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                ResizeMode = ResizeMode.NoResize,
                ShowInTaskbar = false,
                Background = new System.Windows.Media.SolidColorBrush(
                    System.Windows.Media.Color.FromRgb(0xF3, 0xEA, 0xD2)),
                FontFamily = (System.Windows.Media.FontFamily)
                    FindResource("BodyFontFam"),
                FontSize = 14,
            };
            var ok = new Button { Content = "OK", Width = 80, Margin = new Thickness(4, 0, 4, 0), IsDefault = true };
            var cancel = new Button { Content = "Cancel", Width = 80, Margin = new Thickness(4, 0, 4, 0), IsCancel = true };
            ok.Click += (_, __) => { result = box.Text; win.DialogResult = true; };
            var panel = new StackPanel { Margin = new Thickness(14) };
            panel.Children.Add(new TextBlock { Text = label });
            panel.Children.Add(box);
            var btns = new StackPanel
            { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
            btns.Children.Add(ok);
            btns.Children.Add(cancel);
            panel.Children.Add(btns);
            win.Content = panel;
            box.SelectAll();
            box.Focus();
            win.ShowDialog();
            return result;
        }
    }
}
