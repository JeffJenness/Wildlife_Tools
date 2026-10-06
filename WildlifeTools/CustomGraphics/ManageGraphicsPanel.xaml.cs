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
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;

namespace WildlifeTools.CustomGraphics
{
    /// <summary>
    /// The content of the Manage Custom Graphics and North Arrows window: two
    /// tabs (Graphics, North Arrows) over one body. The left side lists what
    /// the matching ribbon gallery holds (the add-in's own designs, read-only,
    /// then the user's registered images) with Add Image and Remove; the
    /// right side shows the selected image with its name and its focal point,
    /// set by clicking the picture, nudging it with the arrow keys, or typing
    /// the two fractions.
    ///
    /// Everything that needs ArcGIS Pro (drawing a vector image's preview,
    /// stamping a focal point on a selected element, Pro's message boxes) is
    /// in <see cref="ManageGraphicsActions"/> and is reached only from a
    /// click, so the panel itself can be built and drawn outside Pro.
    /// </summary>
    public partial class ManageGraphicsPanel : UserControl
    {
        /// <summary>One line of the list.</summary>
        public sealed class Row
        {
            public GraphicEntry Entry { get; set; }
            public ImageSource Thumb { get; set; }
            public string Name { get; set; }
            public string Sub { get; set; }
            public string Group { get; set; }
        }

        private static string _lastFolder;

        private GraphicKind _kind = GraphicKind.Graphic;
        private GraphicEntry _sel;
        private bool _loading;
        private bool _dragging;

        // A focal point nudged with the arrow keys is written to the library a
        // moment after the last key, not on every repeat of a held key.
        private readonly DispatcherTimer _saveTimer;
        private GraphicEntry _unsaved;
        private GraphicKind _unsavedKind;

        private static readonly Brush EdgeIdle = new SolidColorBrush(Color.FromRgb(0xB8, 0xB8, 0xB8));
        private static readonly Brush EdgeActive = new SolidColorBrush(Color.FromRgb(0xD1, 0x1A, 0x1A));

        /// <summary>Raised by the Close button.</summary>
        public event EventHandler CloseRequested;

        public ManageGraphicsPanel()
        {
            InitializeComponent();
            _saveTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(400) };
            _saveTimer.Tick += (_, _) => SaveNudgedFocal();
            Loaded += (_, _) =>
            {
                GraphicLibrary.Changed -= OnLibraryChanged;
                GraphicLibrary.Changed += OnLibraryChanged;
            };
            Unloaded += (_, _) =>
            {
                SaveNudgedFocal();
                GraphicLibrary.Changed -= OnLibraryChanged;
            };
            ShowKind(GraphicKind.Graphic, null);
        }

        private bool Editable => _sel != null && !_sel.IsBuiltIn;

        // ---- which gallery -------------------------------------------------

        /// <summary>Shows one gallery's list, selecting the entry with the
        /// given id (or the first registered image, or the first entry).</summary>
        internal void ShowKind(GraphicKind kind, string selectId)
        {
            SaveNudgedFocal();
            _loading = true;
            try
            {
                _kind = kind;
                int tab = kind == GraphicKind.NorthArrow ? 1 : 0;
                MoveBodyToTab(tab);
                if (tabs.SelectedIndex != tab)
                {
                    tabs.SelectedIndex = tab;
                }
                txtKind.Text = kind == GraphicKind.NorthArrow
                    ? "These are the north arrows in the North Arrows gallery. A north arrow goes on "
                      + "a layout and turns with its map frame, so register an image drawn with "
                      + "north pointing straight up."
                    : "These are the graphics in the Add Graphics gallery. A graphic goes on a map, "
                      + "in its graphics layer, or on a layout.";
                FillList(selectId);
            }
            finally
            {
                _loading = false;
            }
            ShowSelected();
        }

        /// <summary>The one body belongs to whichever tab is showing.</summary>
        private void MoveBodyToTab(int tab)
        {
            var target = (TabItem)tabs.Items[tab];
            if (ReferenceEquals(target.Content, body))
            {
                return;
            }
            foreach (TabItem t in tabs.Items)
            {
                if (ReferenceEquals(t.Content, body))
                {
                    t.Content = null;
                }
            }
            target.Content = body;
        }

        private void OnTabChanged(object sender, SelectionChangedEventArgs e)
        {
            // The list's own SelectionChanged bubbles up through the TabControl.
            if (_loading || !ReferenceEquals(e.OriginalSource, tabs))
            {
                return;
            }
            ShowKind(tabs.SelectedIndex == 1 ? GraphicKind.NorthArrow : GraphicKind.Graphic, null);
        }

        // ---- the list ------------------------------------------------------

        private void FillList(string selectId)
        {
            var rows = new List<Row>();
            foreach (GraphicEntry e in GraphicLibrary.All(_kind))
            {
                rows.Add(new Row
                {
                    Entry = e,
                    Thumb = GraphicLibrary.LoadPreview(e, 88),
                    Name = e.Name,
                    Sub = e.IsBuiltIn ? "Built in" : GraphicImage.FileSummary(e),
                    Group = e.IsBuiltIn ? GraphicLibrary.BuiltInGroup : GraphicLibrary.CustomGroup(_kind),
                });
            }
            var view = new ListCollectionView(rows);
            view.GroupDescriptions.Add(new PropertyGroupDescription(nameof(Row.Group)));
            lstItems.ItemsSource = view;

            Row pick = rows.FirstOrDefault(r => r.Entry.Id == selectId)
                ?? rows.FirstOrDefault(r => !r.Entry.IsBuiltIn)
                ?? rows.FirstOrDefault();
            lstItems.SelectedItem = pick;
            if (pick != null)
            {
                lstItems.ScrollIntoView(pick);
            }
        }

        private void OnSelect(object sender, SelectionChangedEventArgs e)
        {
            if (!_loading)
            {
                ShowSelected();
            }
        }

        /// <summary>The library changed (here or elsewhere: a vector image's
        /// preview has just been drawn). Refresh the pictures; leave the text
        /// boxes alone, since the user may be typing in one.</summary>
        private void OnLibraryChanged(object sender, EventArgs e)
        {
            Dispatcher.BeginInvoke(new Action(() =>
            {
                string id = _sel?.Id;
                _loading = true;
                try
                {
                    FillList(id);
                }
                finally
                {
                    _loading = false;
                }
                GraphicEntry now = (lstItems.SelectedItem as Row)?.Entry;
                if (now?.Id == id && id != null)
                {
                    _sel = now;
                    imgPreview.Source = GraphicLibrary.LoadPreview(_sel, 0);
                }
                else
                {
                    ShowSelected();
                }
            }));
        }

        // ---- the selected image --------------------------------------------

        private void ShowSelected()
        {
            SaveNudgedFocal();
            _sel = (lstItems.SelectedItem as Row)?.Entry;
            bool has = _sel != null;
            bool mine = Editable;
            _loading = true;
            try
            {
                txtName.Text = has ? _sel.Name : "";
                txtName.IsReadOnly = !mine;
                txtFile.Text = has ? GraphicImage.FileSummary(_sel) : "";
                imgPreview.Source = has ? GraphicLibrary.LoadPreview(_sel, 0) : null;
                txtFx.IsReadOnly = !mine;
                txtFy.IsReadOnly = !mine;
                btnCenter.IsEnabled = mine;
                btnRemove.IsEnabled = mine;
                btnApply.IsEnabled = has;
                previewFrame.Cursor = mine ? Cursors.Cross : Cursors.Arrow;
                frameEdge.Stroke = mine && previewFrame.IsKeyboardFocusWithin ? EdgeActive : EdgeIdle;
                txtApply.Text = "";
                if (!has)
                {
                    txtNote.Text = "";
                }
                else if (mine)
                {
                    txtNote.Text = "Click the picture to put the crosshair on the image's focal point, "
                        + "then use the arrow keys to move it a pixel at a time. One tool uses the "
                        + "focal point: Circle Background can center its circle on this point "
                        + "instead of on the middle of the image.";
                }
                else
                {
                    txtNote.Text = "This is one of the add-in's own designs, so it cannot be renamed, "
                        + "removed or changed. Its focal point shows where one belongs: on the hub "
                        + "of the compass.";
                }
            }
            finally
            {
                _loading = false;
            }
            ShowFocal();
            LayoutPreview();
        }

        private static string Fmt(double v) => v.ToString("0.###", CultureInfo.InvariantCulture);

        private void ShowFocal()
        {
            _loading = true;
            try
            {
                txtFx.Text = _sel != null ? Fmt(_sel.FocalX) : "";
                txtFy.Text = _sel != null ? Fmt(_sel.FocalY) : "";
            }
            finally
            {
                _loading = false;
            }
            UpdateCrosshair();
        }

        private void OnPreviewSize(object sender, SizeChangedEventArgs e) => LayoutPreview();

        /// <summary>Sizes the picture frame to the largest box of the image's
        /// own shape that fits the space.</summary>
        private void LayoutPreview()
        {
            double availW = previewHost.ActualWidth, availH = previewHost.ActualHeight;
            if (_sel == null || availW < 4 || availH < 4)
            {
                previewFrame.Width = 0;
                previewFrame.Height = 0;
                cvFocal.Visibility = Visibility.Collapsed;
                return;
            }
            double aspect = _sel.Aspect > 0 && !double.IsInfinity(_sel.Aspect) ? _sel.Aspect : 1.0;
            double w = availW, h = w / aspect;
            if (h > availH)
            {
                h = availH;
                w = h * aspect;
            }
            previewFrame.Width = Math.Max(1.0, w);
            previewFrame.Height = Math.Max(1.0, h);
            cvFocal.Visibility = Visibility.Visible;
            UpdateCrosshair();
        }

        private void UpdateCrosshair()
        {
            double w = previewFrame.Width, h = previewFrame.Height;
            if (_sel == null || double.IsNaN(w) || double.IsNaN(h) || w < 1 || h < 1)
            {
                return;
            }
            double x = _sel.FocalX * w;
            double y = (1.0 - _sel.FocalY) * h;       // the fraction is measured from the BOTTOM
            foreach (var line in new[] { hUnder, hOver })
            {
                line.X1 = 0;
                line.X2 = w;
                line.Y1 = y;
                line.Y2 = y;
            }
            foreach (var line in new[] { vUnder, vOver })
            {
                line.X1 = x;
                line.X2 = x;
                line.Y1 = 0;
                line.Y2 = h;
            }
            Canvas.SetLeft(ringUnder, x - ringUnder.Width / 2.0);
            Canvas.SetTop(ringUnder, y - ringUnder.Height / 2.0);
            Canvas.SetLeft(ringOver, x - ringOver.Width / 2.0);
            Canvas.SetTop(ringOver, y - ringOver.Height / 2.0);
        }

        // ---- focal point: the picture ----------------------------------------

        private void OnPreviewDown(object sender, MouseButtonEventArgs e)
        {
            if (!Editable)
            {
                return;
            }
            previewFrame.Focus();       // the arrow keys now nudge the crosshair
            _dragging = true;
            previewFrame.CaptureMouse();
            MoveFocal(e.GetPosition(previewFrame), false);
        }

        private void OnPreviewMove(object sender, MouseEventArgs e)
        {
            if (_dragging)
            {
                MoveFocal(e.GetPosition(previewFrame), false);
            }
        }

        private void OnPreviewUp(object sender, MouseButtonEventArgs e)
        {
            if (!_dragging)
            {
                return;
            }
            _dragging = false;
            previewFrame.ReleaseMouseCapture();
            MoveFocal(e.GetPosition(previewFrame), true);
        }

        private void MoveFocal(Point p, bool save)
        {
            double w = previewFrame.ActualWidth, h = previewFrame.ActualHeight;
            if (!Editable || w < 1 || h < 1)
            {
                return;
            }
            SetFocal(p.X / w, 1.0 - p.Y / h, save);
        }

        // ---- focal point: the arrow keys ---------------------------------------

        /// <summary>With the keyboard on the picture (it was clicked), the
        /// arrow keys move the crosshair one pixel of the preview at a time,
        /// ten with Shift held.</summary>
        private void OnPreviewKey(object sender, KeyEventArgs e)
        {
            int dx = 0, dy = 0;
            switch (e.Key)
            {
                case Key.Left: dx = -1; break;
                case Key.Right: dx = 1; break;
                case Key.Up: dy = 1; break;         // the fraction is measured from the BOTTOM
                case Key.Down: dy = -1; break;
                default: return;
            }
            if (!Editable)
            {
                return;
            }
            int step = (Keyboard.Modifiers & ModifierKeys.Shift) != 0 ? 10 : 1;
            NudgeFocal(dx * step, dy * step);
            e.Handled = true;
        }

        /// <summary>Moves the focal point by whole pixels of the preview as it
        /// is drawn now: right and UP are positive. The move shows at once and
        /// is written to the library shortly after the last one.</summary>
        internal void NudgeFocal(int rightPixels, int upPixels)
        {
            double w = previewFrame.ActualWidth, h = previewFrame.ActualHeight;
            if (!Editable || w < 1 || h < 1)
            {
                return;
            }
            SetFocal(_sel.FocalX + rightPixels / w, _sel.FocalY + upPixels / h, false);
            _unsaved = _sel;
            _unsavedKind = _kind;
            _saveTimer.Stop();
            _saveTimer.Start();
        }

        /// <summary>Writes a nudged focal point that is still waiting to be
        /// saved. Safe to call at any time.</summary>
        private void SaveNudgedFocal()
        {
            _saveTimer.Stop();
            GraphicEntry entry = _unsaved;
            if (entry == null)
            {
                return;
            }
            _unsaved = null;
            GraphicLibrary.SetFocal(_unsavedKind, entry, entry.FocalX, entry.FocalY);
        }

        private void OnPreviewFocusChanged(object sender, KeyboardFocusChangedEventArgs e)
        {
            bool active = previewFrame.IsKeyboardFocusWithin && Editable;
            frameEdge.Stroke = active ? EdgeActive : EdgeIdle;
            if (!previewFrame.IsKeyboardFocusWithin)
            {
                SaveNudgedFocal();
            }
        }

        /// <summary>Moves the selected image's focal point; writes it to the
        /// library when <paramref name="save"/> (not on every mouse move).</summary>
        private void SetFocal(double fx, double fy, bool save)
        {
            if (!Editable)
            {
                return;
            }
            fx = Math.Round(GraphicLibrary.Clamp01(fx), 4);
            fy = Math.Round(GraphicLibrary.Clamp01(fy), 4);
            _sel.FocalX = fx;
            _sel.FocalY = fy;
            if (save)
            {
                _saveTimer.Stop();
                _unsaved = null;
                GraphicLibrary.SetFocal(_kind, _sel, fx, fy);
            }
            ShowFocal();
        }

        // ---- focal point: the number boxes -------------------------------------

        private void OnFocalKey(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.Enter)
            {
                CommitFocalBoxes();
                e.Handled = true;
            }
        }

        private void OnFocalCommit(object sender, RoutedEventArgs e) => CommitFocalBoxes();

        private void CommitFocalBoxes()
        {
            if (_loading || !Editable)
            {
                return;
            }
            if (TryFraction(txtFx.Text, out double fx) && TryFraction(txtFy.Text, out double fy))
            {
                if (Math.Abs(fx - _sel.FocalX) > 1e-9 || Math.Abs(fy - _sel.FocalY) > 1e-9)
                {
                    SetFocal(fx, fy, true);
                    return;
                }
            }
            ShowFocal();        // unreadable or unchanged: show what is stored
        }

        /// <summary>A number from 0 to 1, typed with a period or a comma.</summary>
        internal static bool TryFraction(string text, out double value)
        {
            return double.TryParse((text ?? "").Trim().Replace(',', '.'),
                    NumberStyles.Float, CultureInfo.InvariantCulture, out value)
                && value >= 0.0 && value <= 1.0;
        }

        private void OnCenter(object sender, RoutedEventArgs e)
        {
            SetFocal(0.5, 0.5, true);
            previewFrame.Focus();       // ready to be nudged from the middle
        }

        // ---- name ----------------------------------------------------------------

        private void OnNameKey(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.Enter)
            {
                CommitName();
                e.Handled = true;
            }
        }

        private void OnNameCommit(object sender, RoutedEventArgs e) => CommitName();

        private void CommitName()
        {
            if (_loading || !Editable)
            {
                return;
            }
            string typed = (txtName.Text ?? "").Trim();
            if (typed.Length == 0 || typed == _sel.Name)
            {
                txtName.Text = _sel.Name;
                return;
            }
            string given = GraphicLibrary.Rename(_kind, _sel, typed);
            ShowKind(_kind, _sel.Id);
            txtStatus.Text = given == typed
                ? "Renamed to \"" + given + "\"."
                : "That name is already in the gallery, so the image is now \"" + given + "\".";
        }

        // ---- add, remove ------------------------------------------------------------

        private void OnAdd(object sender, RoutedEventArgs e)
        {
            var dlg = new Microsoft.Win32.OpenFileDialog
            {
                Title = _kind == GraphicKind.NorthArrow
                    ? "Register Images as North Arrows"
                    : "Register Images as Graphics",
                Filter = GraphicImage.DialogFilter,
                Multiselect = true,
                CheckFileExists = true,
            };
            if (!string.IsNullOrEmpty(_lastFolder) && Directory.Exists(_lastFolder))
            {
                dlg.InitialDirectory = _lastFolder;
            }
            if (dlg.ShowDialog(Window.GetWindow(this)) != true)
            {
                return;
            }

            GraphicKind kind = _kind;
            var problems = new List<string>();
            var added = new List<GraphicEntry>();
            foreach (string file in dlg.FileNames)
            {
                try
                {
                    _lastFolder = Path.GetDirectoryName(file);
                    added.Add(GraphicLibrary.Register(kind, file));
                }
                catch (Exception ex)
                {
                    problems.Add(ex.Message);
                }
            }

            if (added.Count > 0)
            {
                ShowKind(kind, added[added.Count - 1].Id);
                Dispatcher.BeginInvoke(new Action(() => previewFrame.Focus()), DispatcherPriority.Input);
                string where = kind == GraphicKind.NorthArrow ? "North Arrows" : "Add Graphics";
                txtStatus.Text = added.Count == 1
                    ? "Registered \"" + added[0].Name + "\". It is now in the " + where + " gallery."
                    : "Registered " + added.Count + " images. They are now in the " + where + " gallery.";
                // A vector image has only a placeholder picture so far.
                foreach (GraphicEntry entry in added)
                {
                    if (GraphicImage.IsVector(Path.GetExtension(entry.FileName)))
                    {
                        ManageGraphicsActions.DrawVectorPreview(kind, entry);
                    }
                }
            }
            if (problems.Count > 0)
            {
                ManageGraphicsActions.Tell(
                    (problems.Count == 1 ? "One image was not registered:" : "Some images were not registered:")
                    + Environment.NewLine + Environment.NewLine
                    + string.Join(Environment.NewLine + Environment.NewLine, problems));
            }
        }

        private void OnListKey(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.Delete && Editable)
            {
                OnRemove(sender, e);
                e.Handled = true;
            }
        }

        private void OnRemove(object sender, RoutedEventArgs e)
        {
            if (!Editable)
            {
                return;
            }
            GraphicEntry gone = _sel;
            string where = _kind == GraphicKind.NorthArrow ? "North Arrows" : "Add Graphics";
            string placed = _kind == GraphicKind.NorthArrow ? "North arrows" : "Graphics";
            if (!ManageGraphicsActions.Ask(
                    "Remove \"" + gone.Name + "\" from the " + where + " gallery?"
                    + Environment.NewLine + Environment.NewLine
                    + placed + " already placed from it stay as they are."))
            {
                return;
            }
            GraphicLibrary.Remove(_kind, gone);
            ShowKind(_kind, null);
            txtStatus.Text = "Removed \"" + gone.Name + "\".";
        }

        // ---- the rest -------------------------------------------------------------------

        private async void OnApply(object sender, RoutedEventArgs e)
        {
            if (_sel == null)
            {
                return;
            }
            CommitFocalBoxes();
            double fx = _sel.FocalX, fy = _sel.FocalY;
            btnApply.IsEnabled = false;
            try
            {
                txtApply.Text = await ManageGraphicsActions.StampSelectedAsync(fx, fy);
            }
            catch (Exception ex)
            {
                txtApply.Text = "The focal point was not applied: " + ex.Message;
            }
            finally
            {
                btnApply.IsEnabled = _sel != null;
            }
        }

        private void OnHelp(object sender, RoutedEventArgs e) =>
            ManageGraphicsActions.OpenHelp();

        private void OnClose(object sender, RoutedEventArgs e) =>
            CloseRequested?.Invoke(this, EventArgs.Empty);
    }
}
