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
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;
using ArcGIS.Core.CIM;

namespace WildlifeTools.ColorHelper
{
    /// <summary>
    /// The content of the Element Color Helper window. Lists every color
    /// found in the selected elements, one row per color and role (black
    /// text, black lines and black fills are three rows), and lets the user
    /// give checked rows a new color and apply it to all the elements at
    /// once. Everything that needs ArcGIS Pro (the selection, the scan, the
    /// edit, the color picker, the events) is in
    /// <see cref="ElementColorActions"/>, reached from clicks and from the
    /// Loaded event, so the panel itself can be built and drawn outside Pro.
    /// </summary>
    public partial class ElementColorHelperPanel : UserControl
    {
        /// <summary>One row of the table.</summary>
        public sealed class ColorRow : INotifyPropertyChanged
        {
            private bool _checked;
            private (byte R, byte G, byte B)? _new;

            public ColorUse Use { get; }

            public ColorRow(ColorUse use)
            {
                Use = use;
                Swatch = new SolidColorBrush(Color.FromRgb(use.R, use.G, use.B));
                Swatch.Freeze();
            }

            public string Key => Use.Key;
            public Brush Swatch { get; }
            public string ColorName => Use.ColorName + (Use.AnyTransparent ? " *" : "");
            public string RoleName => Use.RoleName;
            public int Count => Use.Count;

            /// <summary>The count as text: a number bound as a number is shown by
            /// Pro's theme in a different style from the other columns.</summary>
            public string CountText => Use.Count.ToString(CultureInfo.CurrentCulture);
            public string ElementsText => string.Join(", ", Use.Elements);

            public bool Checked
            {
                get => _checked;
                set
                {
                    if (_checked == value) return;
                    _checked = value;
                    Notify(nameof(Checked));
                }
            }

            public (byte R, byte G, byte B)? New
            {
                get => _new;
                set
                {
                    _new = value;
                    NewSwatch = value == null ? null
                        : new SolidColorBrush(Color.FromRgb(value.Value.R, value.Value.G, value.Value.B));
                    NewSwatch?.Freeze();
                    Notify(nameof(New));
                    Notify(nameof(NewSwatch));
                    Notify(nameof(NewName));
                    Notify(nameof(NewVisibility));
                }
            }

            public Brush NewSwatch { get; private set; }
            public string NewName => _new == null ? "" : ColorWalker.NameOf(_new.Value.R, _new.Value.G, _new.Value.B);
            public Visibility NewVisibility => _new == null ? Visibility.Hidden : Visibility.Visible;

            public event PropertyChangedEventHandler PropertyChanged;

            private void Notify(string name) =>
                PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
        }

        private readonly ObservableCollection<ColorRow> _rows = new ObservableCollection<ColorRow>();
        private readonly DispatcherTimer _refresh;
        private FrameworkElement _picker;
        private Func<CIMColor> _pickedColor;
        private IDisposable _events;
        private bool _busy;
        private bool _again;

        /// <summary>Raised by the Close button.</summary>
        public event EventHandler CloseRequested;

        public ElementColorHelperPanel()
        {
            InitializeComponent();
            lstColors.ItemsSource = _rows;
            _refresh = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(250) };
            _refresh.Tick += (_, _) =>
            {
                _refresh.Stop();
                RefreshNow();
            };
            Loaded += OnLoaded;
            Unloaded += OnUnloaded;
            txtSelection.Text = "Select the elements to recolor on the layout or the map.";
            UpdateButtons();
        }

        // ---- inside Pro ----------------------------------------------------

        private void OnLoaded(object sender, RoutedEventArgs e)
        {
            if (_picker == null)
            {
                try
                {
                    _picker = ElementColorActions.CreatePicker(out _pickedColor);
                    pickerHost.Content = _picker;
                }
                catch (Exception ex)
                {
                    pickerHost.Content = new TextBlock
                    {
                        Text = "(color picker unavailable: " + ex.Message + ")",
                        VerticalAlignment = System.Windows.VerticalAlignment.Center,
                    };
                }
            }
            _events?.Dispose();
            try
            {
                _events = ElementColorActions.Subscribe(QueueRefresh);
            }
            catch
            {
                _events = null;
            }
            btnRestore.IsEnabled = ElementColorActions.HasSnapshots;
            QueueRefresh();
        }

        private void OnUnloaded(object sender, RoutedEventArgs e)
        {
            _refresh.Stop();
            _events?.Dispose();
            _events = null;
        }

        internal WalkOptions Options => new WalkOptions
        {
            IncludeFrames = chkFrames.IsChecked == true,
            IncludeHalos = chkHalos.IsChecked == true,
        };

        /// <summary>Rescans the selection shortly; several notices in a row
        /// (a drag-select fires many) become one scan.</summary>
        internal void QueueRefresh()
        {
            Dispatcher.BeginInvoke(new Action(() =>
            {
                _refresh.Stop();
                _refresh.Start();
            }));
        }

        private async void RefreshNow()
        {
            if (_busy)
            {
                _again = true;
                return;
            }
            _busy = true;
            try
            {
                ElementColorActions.ScanResult scan = await ElementColorActions.ScanAsync(Options);
                ShowScan(scan.Summary, scan.Uses);
            }
            catch (Exception ex)
            {
                txtStatus.Text = "Could not read the selection: " + ex.Message;
            }
            finally
            {
                _busy = false;
            }
            if (_again)
            {
                _again = false;
                QueueRefresh();
            }
        }

        private void OnOptionChanged(object sender, RoutedEventArgs e)
        {
            if (IsLoaded)
            {
                QueueRefresh();
            }
        }

        // ---- the table --------------------------------------------------------

        /// <summary>Fills the table from a scan. Rows that were already there
        /// keep their check mark and their new color. (Also what the headless
        /// harness calls.)</summary>
        internal void ShowScan(string summary, IEnumerable<ColorUse> uses)
        {
            var remembered = _rows.ToDictionary(r => r.Key, r => (r.Checked, r.New));
            _rows.Clear();
            foreach (ColorUse use in uses.OrderBy(u => (int)u.Role).ThenByDescending(u => u.Count)
                         .ThenBy(u => u.ColorName, StringComparer.CurrentCulture))
            {
                var row = new ColorRow(use);
                if (remembered.TryGetValue(use.Key, out var was))
                {
                    row.Checked = was.Checked;
                    row.New = was.New;
                }
                row.PropertyChanged += (_, _) => UpdateButtons();
                _rows.Add(row);
            }
            txtSelection.Text = summary;
            UpdateButtons();
        }

        private void UpdateButtons()
        {
            bool anyChecked = _rows.Any(r => r.Checked);
            btnAssign.IsEnabled = anyChecked && _pickedColor != null;
            btnClear.IsEnabled = anyChecked && _rows.Any(r => r.Checked && r.New != null);
            btnApply.IsEnabled = _rows.Any(r => r.New != null);
        }

        private void OnCheckAll(object sender, RoutedEventArgs e)
        {
            foreach (ColorRow r in _rows) r.Checked = true;
        }

        private void OnUncheckAll(object sender, RoutedEventArgs e)
        {
            foreach (ColorRow r in _rows) r.Checked = false;
        }

        private void OnAssign(object sender, RoutedEventArgs e)
        {
            CIMColor picked = _pickedColor?.Invoke();
            if (picked == null || !ColorWalker.TryToRgb(picked, out byte r, out byte g, out byte b))
            {
                txtStatus.Text = "Pick a color first.";
                return;
            }
            int n = 0;
            foreach (ColorRow row in _rows.Where(x => x.Checked))
            {
                row.New = (r, g, b);
                n++;
            }
            txtStatus.Text = n == 0
                ? "Check the rows that should get the new color."
                : n + (n == 1 ? " row" : " rows") + " will become " + ColorWalker.NameOf(r, g, b)
                  + ". Click Apply to change the elements.";
            UpdateButtons();
        }

        private void OnClearNew(object sender, RoutedEventArgs e)
        {
            foreach (ColorRow row in _rows.Where(x => x.Checked))
            {
                row.New = null;
            }
            UpdateButtons();
        }

        // ---- the elements ---------------------------------------------------------

        private async void OnApply(object sender, RoutedEventArgs e)
        {
            var map = new Dictionary<string, (byte R, byte G, byte B)>();
            foreach (ColorRow row in _rows.Where(x => x.New != null))
            {
                map[row.Key] = row.New.Value;
            }
            if (map.Count == 0)
            {
                return;
            }
            btnApply.IsEnabled = false;
            try
            {
                (int elements, int colors) = await ElementColorActions.ApplyAsync(Options, map);
                txtStatus.Text = colors == 0
                    ? "Nothing changed: the selection no longer holds those colors."
                    : "Changed " + colors + (colors == 1 ? " color in " : " colors in ")
                      + elements + (elements == 1 ? " element." : " elements.");
                btnRestore.IsEnabled = ElementColorActions.HasSnapshots;
            }
            catch (Exception ex)
            {
                txtStatus.Text = "The colors were not changed: " + ex.Message;
            }
            QueueRefresh();
        }

        private async void OnRestore(object sender, RoutedEventArgs e)
        {
            btnRestore.IsEnabled = false;
            try
            {
                int n = await ElementColorActions.RestoreAsync();
                txtStatus.Text = n == 0
                    ? "Nothing to restore."
                    : "Restored the original colors of " + n + (n == 1 ? " element." : " elements.");
            }
            catch (Exception ex)
            {
                txtStatus.Text = "The colors were not restored: " + ex.Message;
            }
            btnRestore.IsEnabled = ElementColorActions.HasSnapshots;
            QueueRefresh();
        }

        private void OnHelp(object sender, RoutedEventArgs e) => ElementColorActions.OpenHelp();

        private void OnClose(object sender, RoutedEventArgs e) =>
            CloseRequested?.Invoke(this, EventArgs.Empty);
    }
}
