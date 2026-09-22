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
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.Sudoku
{
    /// <summary>A complete color identity for the Sudoku window. Skins swap
    /// palettes only; the game mechanics never change. (An image-based skin
    /// later just means FrameBrush/BackdropBrush become ImageBrushes.)</summary>
    public sealed class SudokuSkin
    {
        // window chrome
        public Color FrameTop, FrameMid, FrameBottom;       // outer "wood" frame
        public Color BackTop, BackMid, BackBottom;          // inner backdrop
        public Color BoardFrame, BoardBg, Ink;              // board edge, board bg, label ink
        public Color BtnFace, BtnText, BtnEdge, BtnHover, BtnPressed;
        // cells
        public Color CellBase, CellGiven, CellCross, CellSelected, GridLine;
        // digits
        public Color InkGiven, InkEntry, InkCorrect, InkWrong;
        public Color InkNote;              // pencil-mark candidates
        // typography (embedded OFL fonts; Georgia fallback baked into each)
        public FontFamily DigitFont, LabelFont, DisplayFont;
        public double DigitSize = 26;
    }

    /// <summary>
    /// Non-modal Sudoku window for the "Take a Break" ribbon group. All input
    /// is handled at the window level (the cells are passive Borders):
    ///   * click a cell to select; type 1-9 to enter, 0/Delete/Backspace to clear
    ///   * arrow keys move the selection, WRAPPING to the opposite edge
    ///   * Tab snakes right/down through all 81 cells (Shift+Tab reverses),
    ///     bottom-right wrapping to top-left
    /// Check colors entries blue/red; Reveal Cell asks first; a correct finish
    /// fires a gold diagonal wave plus a SOLVED! plaque. The skin choice and
    /// difficulty persist in %LOCALAPPDATA%\JennessEnterprises\WildlifeTools\.
    /// </summary>
    public partial class SudokuWindow : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private const double CellSize = 44.0;

        private static readonly Random Rng = new();

        // ---- embedded fonts (Resource items under Fonts\; all SIL-OFL) ----
        // Pack-URI base + "./#Family, Fallback": renders identically on every
        // machine, no font installation involved; Georgia is the safety net.
        private static readonly Uri FontBase =
            new("pack://application:,,,/WildlifeTools;component/Fonts/");
        private static readonly FontFamily FontGaramond =
            new(FontBase, "./#EB Garamond, Georgia");
        private static readonly FontFamily FontCinzel =
            new(FontBase, "./#Cinzel, Georgia");
        private static readonly FontFamily FontOrbitron =
            new(FontBase, "./#Orbitron, Georgia");
        private static readonly FontFamily FontBebas =
            new(FontBase, "./#Bebas Neue, Georgia");
        private static readonly FontFamily FontArchitect =
            new(FontBase, "./#Architects Daughter, Georgia");

        // ---- built-in skins (ordered) -----------------------------------
        private static readonly (string Name, SudokuSkin Skin)[] Skins =
        {
            ("Parchment", new SudokuSkin
            {
                FrameTop = C("#6B4A26"), FrameMid = C("#4E351B"), FrameBottom = C("#3A2712"),
                BackTop = C("#F8F1DE"), BackMid = C("#EFE3C4"), BackBottom = C("#E7D7B0"),
                BoardFrame = C("#2E1F0E"), BoardBg = C("#F9F3E2"), Ink = C("#332612"),
                BtnFace = C("#5C3F20"), BtnText = C("#F5EBD3"), BtnEdge = C("#2E1F0E"),
                BtnHover = C("#7A5730"), BtnPressed = C("#412C14"),
                CellBase = C("#F9F3E2"), CellGiven = C("#EFE4C6"), CellCross = C("#F2E9CC"),
                CellSelected = C("#FFD88A"), GridLine = C("#4A381E"),
                InkGiven = C("#2E2313"), InkEntry = C("#3A4A5C"),
                InkCorrect = C("#0F62C2"), InkWrong = C("#C22718"),
                InkNote = C("#8E2318"),    // Ganado red on parchment
                DigitFont = FontGaramond, LabelFont = FontGaramond,
                DisplayFont = FontCinzel, DigitSize = 27,
            }),
            ("Midnight Brass", new SudokuSkin
            {
                FrameTop = C("#1C1710"), FrameMid = C("#120E08"), FrameBottom = C("#080604"),
                BackTop = C("#37312A"), BackMid = C("#2B2620"), BackBottom = C("#211D17"),
                BoardFrame = C("#B08D52"), BoardBg = C("#262019"), Ink = C("#D8CCB0"),
                BtnFace = C("#A8895A"), BtnText = C("#1C1208"), BtnEdge = C("#0E0A06"),
                BtnHover = C("#C4A46E"), BtnPressed = C("#8A6F45"),
                CellBase = C("#2A241C"), CellGiven = C("#352E23"), CellCross = C("#2F2921"),
                CellSelected = C("#8A6420"), GridLine = C("#A8895A"),
                InkGiven = C("#EFE3C8"), InkEntry = C("#9CC3EE"),
                InkCorrect = C("#5FB0FF"), InkWrong = C("#FF7A5C"),
                InkNote = C("#FF9C8A"),    // light red on the dark brass
                DigitFont = FontOrbitron, LabelFont = FontBebas,
                DisplayFont = FontBebas, DigitSize = 21,
            }),
            ("Blueprint", new SudokuSkin
            {
                FrameTop = C("#16283F"), FrameMid = C("#0F1D30"), FrameBottom = C("#0A1523"),
                BackTop = C("#2A517F"), BackMid = C("#24476F"), BackBottom = C("#1D3A61"),
                BoardFrame = C("#0C1B2C"), BoardBg = C("#1E3C66"), Ink = C("#CFE2F7"),
                BtnFace = C("#9FC3E8"), BtnText = C("#122238"), BtnEdge = C("#0A1523"),
                BtnHover = C("#BCD8F4"), BtnPressed = C("#7FA8D2"),
                CellBase = C("#24466F"), CellGiven = C("#2C5280"), CellCross = C("#284A73"),
                CellSelected = C("#4E7FB5"), GridLine = C("#9FC3E8"),
                InkGiven = C("#FFFFFF"), InkEntry = C("#FFD166"),
                InkCorrect = C("#7BE495"), InkWrong = C("#FF8585"),
                InkNote = C("#FF9E9E"),    // light red on blueprint blue
                DigitFont = FontArchitect, LabelFont = FontArchitect,
                DisplayFont = FontArchitect, DigitSize = 24,
            }),
        };

        private static Color C(string hex) => (Color)ColorConverter.ConvertFromString(hex);

        // ---- state --------------------------------------------------------
        private SudokuSkin _skin = Skins[0].Skin;
        private readonly SudokuSettings _settings;
        private bool _initializing = true;

        private int[,] _solution = new int[9, 9];
        private bool[,] _given = new bool[9, 9];
        private readonly int[,] _entry = new int[9, 9];   // 0 = empty
        private int _selR = 0, _selC = 0;
        private bool _solved;
        private bool _wasFull;
        private string _difficultyName = "Medium";

        private readonly Border[,] _cellB = new Border[9, 9];
        private readonly TextBlock[,] _cellT = new TextBlock[9, 9];

        // pencil-mark notes: _notes[r, c, d-1] = "d is a candidate";
        // each digit renders in its home slot of a 3x3 mini-grid (1 top
        // left ... 9 bottom right), in red. The note size is fixed at a
        // quarter of the CELL size (the slots are a third of the cell,
        // so this fills them without crowding) rather than a fraction
        // of the skin's digit size -- the slots never change between
        // skins, so neither should the notes.
        private const double NoteFontSize = CellSize / 4.0;   // 11 px
        private readonly bool[,,] _notes = new bool[9, 9, 9];
        private readonly TextBlock[,,] _noteT = new TextBlock[9, 9, 9];
        private readonly SolidColorBrush _inkNote = new(Colors.Red);

        // shared, non-frozen brushes: swapping their Color re-inks every cell
        private readonly SolidColorBrush _inkGiven = new(Colors.Black);
        private readonly SolidColorBrush _inkEntry = new(Colors.DarkSlateBlue);
        private readonly SolidColorBrush _inkCorrect = new(Colors.Blue);
        private readonly SolidColorBrush _inkWrong = new(Colors.Red);
        private readonly SolidColorBrush _lineBrush = new(Colors.SaddleBrown);

        public SudokuWindow()
        {
            InitializeComponent();

            foreach (var (name, _) in Skins)
                SkinBox.Items.Add(name);

            _settings = SudokuSettings.Load();
            int skinIdx = Math.Max(0, Array.FindIndex(Skins, s => s.Name == _settings.Skin));
            _skin = Skins[skinIdx].Skin;
            SkinBox.SelectedIndex = skinIdx;
            DifficultyBox.SelectedIndex = Math.Min(2, Math.Max(0, _settings.Difficulty));

            BuildBoard();
            ApplySkin(_skin);
            NewPuzzle(confirm: false);
            _initializing = false;

            Loaded += (_, __) => Keyboard.Focus(this);
            PreviewKeyDown += OnBoardKeyDown;
        }

        // ------------------------------------------------------------------
        //  skinning
        // ------------------------------------------------------------------
        private void ApplySkin(SudokuSkin s)
        {
            _skin = s;

            var frame = new LinearGradientBrush { StartPoint = new Point(0, 0), EndPoint = new Point(0, 1) };
            frame.GradientStops.Add(new GradientStop(s.FrameTop, 0));
            frame.GradientStops.Add(new GradientStop(s.FrameMid, 0.5));
            frame.GradientStops.Add(new GradientStop(s.FrameBottom, 1));
            var back = new LinearGradientBrush { StartPoint = new Point(0, 0), EndPoint = new Point(1, 1) };
            back.GradientStops.Add(new GradientStop(s.BackTop, 0));
            back.GradientStops.Add(new GradientStop(s.BackMid, 0.55));
            back.GradientStops.Add(new GradientStop(s.BackBottom, 1));

            Resources["FrameBrush"] = frame;
            Resources["BackdropBrush"] = back;
            Resources["InkBrush"] = new SolidColorBrush(s.Ink);
            Resources["BoardFrameBrush"] = new SolidColorBrush(s.BoardFrame);
            Resources["BoardBgBrush"] = new SolidColorBrush(s.BoardBg);
            Resources["ButtonFaceBrush"] = new SolidColorBrush(s.BtnFace);
            Resources["ButtonTextBrush"] = new SolidColorBrush(s.BtnText);
            Resources["ButtonEdgeBrush"] = new SolidColorBrush(s.BtnEdge);
            Resources["ButtonHoverBrush"] = new SolidColorBrush(s.BtnHover);
            Resources["ButtonPressedBrush"] = new SolidColorBrush(s.BtnPressed);

            Resources["LabelFontFam"] = s.LabelFont;
            Resources["DisplayFontFam"] = s.DisplayFont;
            for (int r = 0; r < 9; r++)
                for (int c = 0; c < 9; c++)
                    if (_cellT[r, c] != null)
                    {
                        _cellT[r, c].FontFamily = s.DigitFont;
                        _cellT[r, c].FontSize = s.DigitSize;
                        for (int d = 0; d < 9; d++)
                        {
                            _noteT[r, c, d].FontFamily = s.DigitFont;
                            _noteT[r, c, d].FontSize = NoteFontSize;
                        }
                    }

            _inkGiven.Color = s.InkGiven;
            _inkEntry.Color = s.InkEntry;
            _inkCorrect.Color = s.InkCorrect;
            _inkWrong.Color = s.InkWrong;
            _inkNote.Color = s.InkNote;
            _lineBrush.Color = s.GridLine;

            // repaint every cell background -- but only once the board exists
            // (setting the combo's initial selection in the ctor fires this
            // handler chain BEFORE BuildBoard has created the cells)
            if (!_solved && _cellB[0, 0] != null)
                Select(_selR, _selC);
        }

        private void OnSkinChanged(object sender, SelectionChangedEventArgs e)
        {
            // during construction the ctor applies the skin itself, at the
            // right point in the startup sequence
            if (_initializing || SkinBox.SelectedIndex < 0) return;
            ApplySkin(Skins[SkinBox.SelectedIndex].Skin);
            _settings.Skin = Skins[SkinBox.SelectedIndex].Name;
            _settings.Save();
            Keyboard.Focus(this);
        }

        // ------------------------------------------------------------------
        //  board construction
        // ------------------------------------------------------------------
        private void BuildBoard()
        {
            for (int i = 0; i < 9; i++)
            {
                BoardHost.RowDefinitions.Add(new RowDefinition { Height = new GridLength(CellSize) });
                BoardHost.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(CellSize) });
            }
            for (int r = 0; r < 9; r++)
                for (int c = 0; c < 9; c++)
                {
                    var text = new TextBlock
                    {
                        HorizontalAlignment = HorizontalAlignment.Center,
                        VerticalAlignment = VerticalAlignment.Center,
                        FontFamily = _skin.DigitFont,
                        FontSize = _skin.DigitSize,
                    };
                    // notes layer: a fixed 3x3 mini-grid behind the main
                    // digit; unchecked slots are simply empty strings, so
                    // no visibility juggling is ever needed
                    var noteGrid = new Grid();
                    for (int i = 0; i < 3; i++)
                    {
                        noteGrid.RowDefinitions.Add(new RowDefinition());
                        noteGrid.ColumnDefinitions.Add(new ColumnDefinition());
                    }
                    for (int d = 0; d < 9; d++)
                    {
                        var nt = new TextBlock
                        {
                            HorizontalAlignment = HorizontalAlignment.Center,
                            VerticalAlignment = VerticalAlignment.Center,
                            FontFamily = _skin.DigitFont,
                            FontSize = NoteFontSize,
                            Foreground = _inkNote,
                        };
                        Grid.SetRow(nt, d / 3);
                        Grid.SetColumn(nt, d % 3);
                        noteGrid.Children.Add(nt);
                        _noteT[r, c, d] = nt;
                    }
                    var host = new Grid();
                    host.Children.Add(noteGrid);
                    host.Children.Add(text);
                    var cell = new Border
                    {
                        Background = new SolidColorBrush(_skin.CellBase),   // animatable
                        BorderBrush = _lineBrush,
                        BorderThickness = new Thickness(
                            c % 3 == 0 ? 2.5 : 0.75, r % 3 == 0 ? 2.5 : 0.75,
                            c == 8 ? 2.5 : 0, r == 8 ? 2.5 : 0),
                        Child = host,
                        SnapsToDevicePixels = true,
                        Tag = r * 9 + c,
                    };
                    cell.MouseLeftButtonDown += OnCellClick;
                    cell.MouseRightButtonUp += OnCellRightClick;
                    Grid.SetRow(cell, r);
                    Grid.SetColumn(cell, c);
                    BoardHost.Children.Add(cell);
                    _cellB[r, c] = cell;
                    _cellT[r, c] = text;
                }
        }

        // ------------------------------------------------------------------
        //  puzzle lifecycle
        // ------------------------------------------------------------------
        private void NewPuzzle(bool confirm)
        {
            if (confirm && !_solved && AnyUserEntries())
            {
                var r = MessageBox.Show(
                    "Abandon the current puzzle and start a new one?",
                    "Sudoku", MessageBoxButton.YesNo, MessageBoxImage.Question);
                if (r != MessageBoxResult.Yes) return;
            }

            int level = Math.Max(0, DifficultyBox.SelectedIndex);
            _difficultyName = ((ComboBoxItem)DifficultyBox.Items[level]).Content.ToString();
            SudokuPuzzle p = SudokuEngine.Generate(SudokuEngine.TargetGivens[level], Rng);
            _solution = p.Solution;
            _given = p.IsGiven;
            _solved = false;
            _wasFull = false;
            WinOverlay.Visibility = Visibility.Collapsed;

            for (int r = 0; r < 9; r++)
                for (int c = 0; c < 9; c++)
                {
                    _entry[r, c] = _given[r, c] ? _solution[r, c] : 0;
                    ClearNotes(r, c);
                    var t = _cellT[r, c];
                    t.Text = _given[r, c] ? _solution[r, c].ToString() : "";
                    t.FontWeight = _given[r, c] ? FontWeights.Bold : FontWeights.Normal;
                    t.Foreground = _given[r, c] ? _inkGiven : _inkEntry;
                    var b = (SolidColorBrush)_cellB[r, c].Background;
                    b.BeginAnimation(SolidColorBrush.ColorProperty, null);   // clear old win wave
                    b.Color = _skin.CellBase;
                }
            Select(0, 0);
            UpdateStatus();
        }

        private bool AnyUserEntries()
        {
            for (int r = 0; r < 9; r++)
                for (int c = 0; c < 9; c++)
                    if (!_given[r, c] && _entry[r, c] != 0) return true;
            return false;
        }

        // ------------------------------------------------------------------
        //  selection + navigation
        // ------------------------------------------------------------------
        private Color BaseColor(int r, int c)
        {
            if (r == _selR && c == _selC) return _skin.CellSelected;
            if (r == _selR || c == _selC) return _skin.CellCross;    // row/col hint
            return _given[r, c] ? _skin.CellGiven : _skin.CellBase;
        }

        private void Select(int r, int c)
        {
            _selR = r; _selC = c;
            if (_solved) return;
            for (int i = 0; i < 9; i++)
                for (int j = 0; j < 9; j++)
                    ((SolidColorBrush)_cellB[i, j].Background).Color = BaseColor(i, j);
        }

        private void OnCellClick(object sender, MouseButtonEventArgs e)
        {
            int idx = (int)((Border)sender).Tag;
            Select(idx / 9, idx % 9);
            Keyboard.Focus(this);
        }

        // ------------------------------------------------------------------
        //  pencil-mark notes (right-click)
        // ------------------------------------------------------------------
        private void OnCellRightClick(object sender, MouseButtonEventArgs e)
        {
            var cell = (Border)sender;
            int idx = (int)cell.Tag;
            int r = idx / 9, c = idx % 9;
            Select(r, c);
            e.Handled = true;
            if (_solved || _given[r, c] || _entry[r, c] != 0)
            {
                Keyboard.Focus(this);
                return;      // notes only make sense in cells still open
            }

            var menu = new ContextMenu { PlacementTarget = cell };
            for (int d = 1; d <= 9; d++)
            {
                var item = new MenuItem
                {
                    Header = "Note " + d,
                    IsCheckable = true,
                    IsChecked = _notes[r, c, d - 1],
                    StaysOpenOnClick = true,    // mark several candidates
                                                // in one visit
                    Tag = d,
                };
                item.Click += (_, __) =>
                {
                    int dd = (int)item.Tag;
                    _notes[r, c, dd - 1] = item.IsChecked;
                    _noteT[r, c, dd - 1].Text =
                        item.IsChecked ? dd.ToString() : "";
                };
                menu.Items.Add(item);
            }
            menu.Items.Add(new Separator());
            var clear = new MenuItem { Header = "Clear this cell's notes" };
            clear.Click += (_, __) => ClearNotes(r, c);
            menu.Items.Add(clear);
            menu.Closed += (_, __) => Keyboard.Focus(this);
            menu.IsOpen = true;
        }

        private void ClearNotes(int r, int c)
        {
            for (int d = 0; d < 9; d++)
            {
                _notes[r, c, d] = false;
                if (_noteT[r, c, d] != null) _noteT[r, c, d].Text = "";
            }
        }

        private void OnBoardKeyDown(object sender, KeyEventArgs e)
        {
            if (_solved && e.Key != Key.Tab) return;   // board locked after a win
            switch (e.Key)
            {
                // arrows wrap to the opposite edge of the same row/column
                case Key.Up: Select((_selR + 8) % 9, _selC); e.Handled = true; return;
                case Key.Down: Select((_selR + 1) % 9, _selC); e.Handled = true; return;
                case Key.Left: Select(_selR, (_selC + 8) % 9); e.Handled = true; return;
                case Key.Right: Select(_selR, (_selC + 1) % 9); e.Handled = true; return;

                // Tab snakes right, wrapping to the next row's far left, and
                // from the bottom-right cell back to the top-left
                case Key.Tab:
                {
                    if (_solved) { e.Handled = true; return; }
                    int idx = _selR * 9 + _selC;
                    bool back = (Keyboard.Modifiers & ModifierKeys.Shift) != 0;
                    idx = (idx + (back ? 80 : 1)) % 81;
                    Select(idx / 9, idx % 9);
                    e.Handled = true;
                    return;
                }

                case Key.Delete:
                case Key.Back:
                case Key.D0:
                case Key.NumPad0:
                    EnterDigit(0);
                    e.Handled = true;
                    return;
            }
            if (e.Key >= Key.D1 && e.Key <= Key.D9)
            { EnterDigit(e.Key - Key.D0); e.Handled = true; }
            else if (e.Key >= Key.NumPad1 && e.Key <= Key.NumPad9)
            { EnterDigit(e.Key - Key.NumPad0); e.Handled = true; }
        }

        // ------------------------------------------------------------------
        //  entering values
        // ------------------------------------------------------------------
        private void EnterDigit(int d)
        {
            if (_solved || _given[_selR, _selC]) return;
            _entry[_selR, _selC] = d;
            ClearNotes(_selR, _selC);   // a real entry (or Delete, the
                                        // note eraser) retires the notes
            var t = _cellT[_selR, _selC];
            t.Text = d == 0 ? "" : d.ToString();
            t.Foreground = _inkEntry;                // editing resets Check coloring
            UpdateStatus();
            CheckCompletion();
        }

        private void CheckCompletion()
        {
            bool full = true;
            bool correct = true;
            for (int r = 0; r < 9 && full; r++)
                for (int c = 0; c < 9; c++)
                {
                    if (_entry[r, c] == 0) { full = false; break; }
                    if (_entry[r, c] != _solution[r, c]) correct = false;
                }
            if (!full) { _wasFull = false; return; }
            if (correct) { Celebrate(); return; }
            // announce only on the transition into a full (but wrong) board
            if (!_wasFull)
            {
                _wasFull = true;
                MessageBox.Show(
                    "The puzzle is complete, but at least one entry is incorrect.\n\n"
                    + "Use the Check button to see which entries are wrong "
                    + "(blue = correct, red = incorrect).",
                    "Sudoku", MessageBoxButton.OK, MessageBoxImage.Information);
            }
        }

        // ------------------------------------------------------------------
        //  buttons
        // ------------------------------------------------------------------
        private void OnNewPuzzle(object sender, RoutedEventArgs e)
        {
            NewPuzzle(confirm: true);
            Keyboard.Focus(this);
        }

        private void OnDifficultyChanged(object sender, SelectionChangedEventArgs e)
        {
            if (_initializing) return;               // applies to the NEXT New Puzzle
            _settings.Difficulty = DifficultyBox.SelectedIndex;
            _settings.Save();
            Keyboard.Focus(this);
        }

        private void OnCheck(object sender, RoutedEventArgs e)
        {
            if (_solved) return;
            for (int r = 0; r < 9; r++)
                for (int c = 0; c < 9; c++)
                {
                    if (_given[r, c] || _entry[r, c] == 0) continue;
                    _cellT[r, c].Foreground =
                        _entry[r, c] == _solution[r, c] ? _inkCorrect : _inkWrong;
                }
            Keyboard.Focus(this);
        }

        private void OnRevealCell(object sender, RoutedEventArgs e)
        {
            if (_solved) return;
            if (_given[_selR, _selC])
            {
                MessageBox.Show("That cell is one of the puzzle's givens.", "Sudoku");
                Keyboard.Focus(this);
                return;
            }
            var r = MessageBox.Show(
                "Reveal the correct value for the selected cell?",
                "Sudoku", MessageBoxButton.YesNo, MessageBoxImage.Question);
            if (r == MessageBoxResult.Yes)
            {
                _entry[_selR, _selC] = _solution[_selR, _selC];
                ClearNotes(_selR, _selC);
                var t = _cellT[_selR, _selC];
                t.Text = _solution[_selR, _selC].ToString();
                t.Foreground = _inkCorrect;          // known correct: blue
                UpdateStatus();
                CheckCompletion();
            }
            Keyboard.Focus(this);
        }

        private void UpdateStatus()
        {
            int remaining = 0;
            for (int r = 0; r < 9; r++)
                for (int c = 0; c < 9; c++)
                    if (_entry[r, c] == 0) remaining++;
            StatusText.Text = remaining == 0
                ? $"{_difficultyName} — board full."
                : $"{_difficultyName} — {remaining} cell{(remaining == 1 ? "" : "s")} to go.  "
                  + "Click a cell, type 1-9; right-click for candidate notes.";
        }

        // ------------------------------------------------------------------
        //  the payoff
        // ------------------------------------------------------------------
        private void Celebrate()
        {
            _solved = true;
            StatusText.Text = $"{_difficultyName} — solved. Back to work?";

            // diagonal gold wave sweeping the board...
            for (int r = 0; r < 9; r++)
                for (int c = 0; c < 9; c++)
                {
                    var brush = (SolidColorBrush)_cellB[r, c].Background;
                    var anim = new ColorAnimationUsingKeyFrames
                    {
                        BeginTime = TimeSpan.FromMilliseconds((r + c) * 70),
                    };
                    anim.KeyFrames.Add(new EasingColorKeyFrame(Color.FromRgb(0xFF, 0xD2, 0x4D),
                        KeyTime.FromTimeSpan(TimeSpan.FromMilliseconds(220))));
                    anim.KeyFrames.Add(new EasingColorKeyFrame(Color.FromRgb(0xBF, 0xE6, 0xB2),
                        KeyTime.FromTimeSpan(TimeSpan.FromMilliseconds(480))));
                    anim.KeyFrames.Add(new EasingColorKeyFrame(_skin.CellGiven,
                        KeyTime.FromTimeSpan(TimeSpan.FromMilliseconds(950))));
                    brush.BeginAnimation(SolidColorBrush.ColorProperty, anim);
                }

            // ...then the SOLVED! plaque (zoom past full size, settle, glow)
            WinSubText.Text = $"{_difficultyName} puzzle complete";
            WinOverlay.Visibility = Visibility.Visible;
            WinOverlay.Opacity = 0;
            var fade = new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(350))
            { BeginTime = TimeSpan.FromMilliseconds(500) };
            WinOverlay.BeginAnimation(OpacityProperty, fade);

            var zoom = new DoubleAnimationUsingKeyFrames { BeginTime = TimeSpan.FromMilliseconds(500) };
            zoom.KeyFrames.Add(new EasingDoubleKeyFrame(1.25,
                KeyTime.FromTimeSpan(TimeSpan.FromMilliseconds(420)),
                new BackEase { EasingMode = EasingMode.EaseOut, Amplitude = 0.6 }));
            zoom.KeyFrames.Add(new EasingDoubleKeyFrame(1.0,
                KeyTime.FromTimeSpan(TimeSpan.FromMilliseconds(700))));
            WinScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleXProperty, zoom);
            WinScale.BeginAnimation(System.Windows.Media.ScaleTransform.ScaleYProperty, zoom);

            // gentle endless shimmer on the SOLVED! glow
            var glow = (System.Windows.Media.Effects.DropShadowEffect)WinText.Effect;
            var pulse = new DoubleAnimation(8, 26, TimeSpan.FromMilliseconds(900))
            { AutoReverse = true, RepeatBehavior = RepeatBehavior.Forever,
              BeginTime = TimeSpan.FromMilliseconds(1200) };
            glow.BeginAnimation(System.Windows.Media.Effects.DropShadowEffect.BlurRadiusProperty, pulse);
        }
    }
}
