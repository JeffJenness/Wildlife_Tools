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
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;

namespace WildlifeTools.Puzzle
{
    /// <summary>
    /// A sliding-tile puzzle built from a snapshot of the active map or layout
    /// view. Maps use a centered-square crop switchable 4x4 / 5x5 / 6x6; layouts
    /// use the whole page with the chosen count along the short edge and a derived
    /// near-square count along the long edge. Parchment skin; one shared,
    /// non-modal, resizable window.
    /// </summary>
    public partial class SlidingPuzzleWindow
        : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private const double Tile = 120;   // logical cell size (scaled by the Viewbox)
        private const double Inset = 3;    // gap between tiles

        private readonly Random _rng = new Random();
        private BitmapSource _source;      // square (map) or full (layout)
        private bool _isLayout;
        private int _n = 4;                // tiles along the short edge (or square dim)
        private int _rows, _cols;
        private PuzzleBoard _board;
        private Border[] _tiles;
        private TextBlock[] _numbers;
        private Image _goalOverlay;
        private bool _peek;
        private bool _busy;
        private List<int> _plan;   // committed solver plan (cells to slide), or null

        public SlidingPuzzleWindow()
        {
            InitializeComponent();
            Loaded += async (_, __) => await CaptureAndBuildAsync();
        }

        private void SetStatus(string t) => txtStatus.Text = t;

        // ---- capture ---------------------------------------------------------
        private async System.Threading.Tasks.Task CaptureAndBuildAsync()
        {
            if (_busy) return;
            _busy = true;
            SetStatus("Capturing the active view...");
            try
            {
                PuzzleSnapshot snap = await PuzzleCapture.CaptureActiveViewAsync();
                LoadSource(snap);
                BuildPuzzle();
            }
            catch (Exception ex)
            {
                SetStatus("Could not capture the view: " + ex.Message);
            }
            finally { _busy = false; }
        }

        private void LoadSource(PuzzleSnapshot snap)
        {
            var bi = new BitmapImage();
            bi.BeginInit();
            bi.CacheOption = BitmapCacheOption.OnLoad;
            bi.CreateOptions = BitmapCreateOptions.IgnoreImageCache;
            bi.UriSource = new Uri(snap.ImagePath, UriKind.Absolute);
            bi.EndInit();
            bi.Freeze();
            try { File.Delete(snap.ImagePath); } catch { /* temp file; ignore */ }

            _isLayout = snap.IsLayout;
            if (_isLayout)
            {
                _source = bi;
                txtSizeLabel.Text = "Tiles (short edge):";
            }
            else
            {
                int sq = Math.Min(bi.PixelWidth, bi.PixelHeight);
                int ox = (bi.PixelWidth - sq) / 2;
                int oy = (bi.PixelHeight - sq) / 2;
                var crop = new CroppedBitmap(bi, new Int32Rect(ox, oy, sq, sq));
                crop.Freeze();
                _source = crop;
                txtSizeLabel.Text = "Tiles:";
            }
        }

        // ---- build -----------------------------------------------------------
        private void BuildPuzzle()
        {
            if (_source == null) return;
            int pixW = _source.PixelWidth, pixH = _source.PixelHeight;
            (_rows, _cols) = PuzzleBoard.ChooseDims(pixW, pixH, _n, _isLayout);

            int tilePx = Math.Max(1, Math.Min(pixW / _cols, pixH / _rows));
            int usableW = tilePx * _cols, usableH = tilePx * _rows;
            int offX = (pixW - usableW) / 2, offY = (pixH - usableH) / 2;

            _board = new PuzzleBoard(_rows, _cols, _rng);
            _board.Scramble();

            BoardCanvas.Children.Clear();
            BoardCanvas.Width = _cols * Tile;
            BoardCanvas.Height = _rows * Tile;

            int n = _rows * _cols;
            _tiles = new Border[n - 1];
            _numbers = new TextBlock[n - 1];

            for (int t = 0; t < n - 1; t++)
            {
                int homeRow = t / _cols, homeCol = t % _cols;
                var rect = new Int32Rect(offX + homeCol * tilePx, offY + homeRow * tilePx,
                                         tilePx, tilePx);
                var cb = new CroppedBitmap(_source, rect);
                cb.Freeze();

                var img = new Image { Source = cb, Stretch = Stretch.Fill };
                var num = new TextBlock
                {
                    Text = (t + 1).ToString(),
                    FontSize = 22,
                    FontWeight = FontWeights.Bold,
                    Foreground = new SolidColorBrush(Color.FromArgb(230, 255, 255, 255)),
                    HorizontalAlignment = HorizontalAlignment.Right,
                    VerticalAlignment = VerticalAlignment.Bottom,
                    Margin = new Thickness(0, 0, 5, 3),
                    Visibility = chkEasy.IsChecked == true ? Visibility.Visible : Visibility.Collapsed,
                    Effect = new System.Windows.Media.Effects.DropShadowEffect
                    { BlurRadius = 4, ShadowDepth = 1, Color = Colors.Black, Opacity = 0.9 },
                };
                _numbers[t] = num;

                var cell = new Grid();
                cell.Children.Add(img);
                cell.Children.Add(num);

                var border = new Border
                {
                    Width = Tile - 2 * Inset,
                    Height = Tile - 2 * Inset,
                    CornerRadius = new CornerRadius(4),
                    BorderBrush = new SolidColorBrush(Color.FromRgb(0x2E, 0x1F, 0x0E)),
                    BorderThickness = new Thickness(1),
                    ClipToBounds = true,
                    Cursor = System.Windows.Input.Cursors.Hand,
                    Child = cell,
                    Tag = t,
                };
                border.MouseLeftButtonUp += OnTileClick;
                _tiles[t] = border;
                BoardCanvas.Children.Add(border);
                PositionTile(t, animate: false);
            }

            // Goal overlay (the assembled picture) for Peek and the solved state.
            var goalRect = new Int32Rect(offX, offY, usableW, usableH);
            var goalCb = new CroppedBitmap(_source, goalRect);
            goalCb.Freeze();
            _goalOverlay = new Image
            {
                Source = goalCb,
                Stretch = Stretch.Fill,
                Width = BoardCanvas.Width,
                Height = BoardCanvas.Height,
                Visibility = Visibility.Collapsed,
                IsHitTestVisible = false,
            };
            Canvas.SetLeft(_goalOverlay, 0);
            Canvas.SetTop(_goalOverlay, 0);
            Panel.SetZIndex(_goalOverlay, 100);
            BoardCanvas.Children.Add(_goalOverlay);

            _peek = false;
            UpdateSizeButtons();
            RefreshPlan();
            SetStatus(DescribeGrid() + "  —  Moves: 0");
        }

        private string DescribeGrid() =>
            _isLayout
                ? $"{_cols} x {_rows} tiles (layout)"
                : $"{_cols} x {_rows} tiles";

        private int IndexOfTile(int t)
        {
            for (int i = 0; i < _board.Cells.Length; i++)
            {
                if (_board.Cells[i] == t) return i;
            }
            return -1;
        }

        private void PositionTile(int t, bool animate)
        {
            var border = _tiles[t];
            int cell = IndexOfTile(t);
            double left = (cell % _cols) * Tile + Inset;
            double top = (cell / _cols) * Tile + Inset;
            if (animate)
            {
                var ease = new QuadraticEase { EasingMode = EasingMode.EaseOut };
                border.BeginAnimation(Canvas.LeftProperty,
                    new DoubleAnimation(left, TimeSpan.FromMilliseconds(110)) { EasingFunction = ease });
                border.BeginAnimation(Canvas.TopProperty,
                    new DoubleAnimation(top, TimeSpan.FromMilliseconds(110)) { EasingFunction = ease });
            }
            else
            {
                border.BeginAnimation(Canvas.LeftProperty, null);
                border.BeginAnimation(Canvas.TopProperty, null);
                Canvas.SetLeft(border, left);
                Canvas.SetTop(border, top);
            }
        }

        private void PositionAll(bool animate)
        {
            if (_tiles == null) return;
            for (int t = 0; t < _tiles.Length; t++) PositionTile(t, animate);
        }

        // ---- play ------------------------------------------------------------
        private void OnTileClick(object sender, System.Windows.Input.MouseButtonEventArgs e)
        {
            if (_board == null) return;
            if (_board.IsSolved) return;
            int t = (int)((Border)sender).Tag;
            int cell = IndexOfTile(t);
            if (_board.TryMove(cell))
            {
                PositionTile(t, animate: true);
                if (_board.IsSolved) { OnSolved(); UpdateSolveButton(); }
                else
                {
                    RefreshPlan();   // the player diverged; recompute the plan/count
                    SetStatus(DescribeGrid() + "  —  Moves: " + _board.MoveCount);
                }
            }
        }

        // ---- solver -----------------------------------------------------------
        private void RefreshPlan()
        {
            _plan = _board == null ? null : PuzzleSolver.Solve(_rows, _cols, _board.Cells);
            UpdateSolveButton();
        }

        private void UpdateSolveButton()
        {
            if (_board != null && _board.IsSolved)
            {
                btnSolve.Content = "Solved";
                btnSolve.IsEnabled = false;
                return;
            }
            int n = _plan?.Count ?? 0;
            btnSolve.Content = n > 0 ? $"Solve ({n})" : "Solve";
            btnSolve.IsEnabled = n > 0;
        }

        private void OnSolveStep(object sender, RoutedEventArgs e)
        {
            if (_board == null || _board.IsSolved) return;
            if (_plan == null || _plan.Count == 0) RefreshPlan();
            if (_plan == null || _plan.Count == 0) return;

            int cell = _plan[0];
            int oldBlank = _board.BlankIndex;
            if (!_board.TryMove(cell)) { RefreshPlan(); return; }

            _plan.RemoveAt(0);                       // keep the committed plan; count is monotonic
            int movedTile = _board.Cells[oldBlank];  // the tile now filling the old blank
            if (movedTile >= 0) PositionTile(movedTile, animate: true);

            if (_board.IsSolved) { OnSolved(); UpdateSolveButton(); }
            else
            {
                UpdateSolveButton();
                SetStatus(DescribeGrid() + "  —  Moves: " + _board.MoveCount);
            }
        }

        private void OnSolved()
        {
            _goalOverlay.Opacity = 0;
            _goalOverlay.Visibility = Visibility.Visible;
            _goalOverlay.BeginAnimation(OpacityProperty,
                new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(400)));
            SetStatus("Solved in " + _board.MoveCount + " moves!  Nicely done.");
            ShowToast("✓ Solved in " + _board.MoveCount + " moves!");
        }

        // ---- toolbar ---------------------------------------------------------
        private async void OnNewSnapshot(object sender, RoutedEventArgs e) =>
            await CaptureAndBuildAsync();

        private void OnSize4(object sender, RoutedEventArgs e) => SetSize(4);
        private void OnSize5(object sender, RoutedEventArgs e) => SetSize(5);
        private void OnSize6(object sender, RoutedEventArgs e) => SetSize(6);

        private void SetSize(int n)
        {
            if (_source == null || n == _n) return;
            _n = n;
            BuildPuzzle();
        }

        private void UpdateSizeButtons()
        {
            bool ready = _source != null;
            btnSize4.IsEnabled = ready && _n != 4;
            btnSize5.IsEnabled = ready && _n != 5;
            btnSize6.IsEnabled = ready && _n != 6;
        }

        private void OnShuffle(object sender, RoutedEventArgs e)
        {
            if (_board == null) return;
            _peek = false;
            _goalOverlay.Visibility = Visibility.Collapsed;
            _board.Scramble();
            PositionAll(animate: false);
            RefreshPlan();
            SetStatus(DescribeGrid() + "  —  Moves: 0");
        }

        private void OnPeek(object sender, RoutedEventArgs e)
        {
            if (_goalOverlay == null) return;
            if (_board != null && _board.IsSolved) return;
            _peek = !_peek;
            _goalOverlay.Opacity = 1;
            _goalOverlay.Visibility = _peek ? Visibility.Visible : Visibility.Collapsed;
            btnPeek.Content = _peek ? "Hide" : "Peek";
        }

        private void OnEasyToggle(object sender, RoutedEventArgs e)
        {
            if (_numbers == null) return;
            var vis = chkEasy.IsChecked == true ? Visibility.Visible : Visibility.Collapsed;
            foreach (var num in _numbers) num.Visibility = vis;
        }

        // ---- transient toast -------------------------------------------------
        private System.Windows.Controls.Primitives.Popup _toast;
        private System.Windows.Threading.DispatcherTimer _toastTimer;

        private void ShowToast(string message)
        {
            if (_toast == null)
            {
                _toast = new System.Windows.Controls.Primitives.Popup
                {
                    AllowsTransparency = true,
                    Placement = System.Windows.Controls.Primitives.PlacementMode.Center,
                    PlacementTarget = this,
                    StaysOpen = true,
                    PopupAnimation = System.Windows.Controls.Primitives.PopupAnimation.Fade,
                };
                _toastTimer = new System.Windows.Threading.DispatcherTimer
                { Interval = TimeSpan.FromSeconds(2.2) };
                _toastTimer.Tick += (s2, e2) => { _toastTimer.Stop(); _toast.IsOpen = false; };
                Closed += (s2, e2) => { _toastTimer.Stop(); _toast.IsOpen = false; };
            }
            _toastTimer.Stop();
            _toast.IsOpen = false;
            _toast.Child = new Border
            {
                Background = new SolidColorBrush(Color.FromArgb(235, 45, 45, 45)),
                CornerRadius = new CornerRadius(6),
                Padding = new Thickness(16, 10, 16, 10),
                Child = new TextBlock
                {
                    Text = message,
                    Foreground = Brushes.White,
                    FontSize = 15,
                },
            };
            _toast.IsOpen = true;
            _toastTimer.Start();
        }
    }
}
