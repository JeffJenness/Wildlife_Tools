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

namespace WildlifeTools.Puzzle
{
    /// <summary>
    /// The logical state of a sliding-tile puzzle: an R x C grid of tiles with
    /// one empty slot. Cell values are the tile's HOME index (0 .. N-2) in
    /// row-major order, or <see cref="Blank"/> for the empty slot, whose home is
    /// the bottom-right cell. Scrambling uses inversion-parity correction so the
    /// board is always solvable, which is both instant and maximally shuffled
    /// (unlike a random walk).
    /// </summary>
    internal sealed class PuzzleBoard
    {
        public const int Blank = -1;

        public int Rows { get; }
        public int Cols { get; }
        public int Count => Rows * Cols;
        public int[] Cells { get; }          // length Count; value = home index or Blank
        public int MoveCount { get; private set; }

        private readonly Random _rng;

        public PuzzleBoard(int rows, int cols, Random rng)
        {
            Rows = rows;
            Cols = cols;
            _rng = rng ?? new Random();
            Cells = new int[Count];
            Reset();
        }

        /// <summary>Puts every tile home and the blank in the bottom-right cell.</summary>
        public void Reset()
        {
            for (int i = 0; i < Count; i++) Cells[i] = i;
            Cells[Count - 1] = Blank;
            MoveCount = 0;
        }

        public bool IsSolved
        {
            get
            {
                for (int i = 0; i < Count - 1; i++)
                {
                    if (Cells[i] != i) return false;
                }
                return Cells[Count - 1] == Blank;
            }
        }

        public int BlankIndex
        {
            get
            {
                for (int i = 0; i < Count; i++)
                {
                    if (Cells[i] == Blank) return i;
                }
                return Count - 1;
            }
        }

        /// <summary>
        /// If the tile at <paramref name="cellIndex"/> is orthogonally adjacent to
        /// the blank, slides it into the blank and returns true (counting a move).
        /// </summary>
        public bool TryMove(int cellIndex)
        {
            if (cellIndex < 0 || cellIndex >= Count) return false;
            if (Cells[cellIndex] == Blank) return false;

            int blank = BlankIndex;
            int br = blank / Cols, bc = blank % Cols;
            int cr = cellIndex / Cols, cc = cellIndex % Cols;
            bool adjacent = (Math.Abs(br - cr) == 1 && bc == cc)
                         || (Math.Abs(bc - cc) == 1 && br == cr);
            if (!adjacent) return false;

            Cells[blank] = Cells[cellIndex];
            Cells[cellIndex] = Blank;
            MoveCount++;
            return true;
        }

        /// <summary>
        /// Scrambles into a random, always-solvable, not-already-solved state.
        /// Fisher-Yates shuffle, then a single swap to correct inversion parity
        /// if needed.
        /// </summary>
        public void Scramble()
        {
            do
            {
                for (int i = Count - 1; i > 0; i--)
                {
                    int j = _rng.Next(i + 1);
                    (Cells[i], Cells[j]) = (Cells[j], Cells[i]);
                }
                if (!IsSolvable())
                {
                    FixParity();
                }
            }
            while (IsSolved);   // reshuffle on the rare fully-solved draw
            MoveCount = 0;
        }

        /// <summary>
        /// Solvability test for an R x C sliding puzzle: with an odd number of
        /// columns, the inversion count must be even; with an even number of
        /// columns, (inversions + blank's row counted from the bottom) must be
        /// even.
        /// </summary>
        private bool IsSolvable()
        {
            int inversions = 0;
            // Row-major sequence of tile values, skipping the blank.
            for (int a = 0; a < Count; a++)
            {
                if (Cells[a] == Blank) continue;
                for (int b = a + 1; b < Count; b++)
                {
                    if (Cells[b] == Blank) continue;
                    if (Cells[a] > Cells[b]) inversions++;
                }
            }

            if (Cols % 2 == 1)
            {
                return inversions % 2 == 0;
            }
            // Even width: solvable iff (inversions + blank's row from the bottom,
            // counting the bottom row as 1) is odd.
            int blankRowFromBottom = Rows - (BlankIndex / Cols);
            return (inversions + blankRowFromBottom) % 2 == 1;
        }

        /// <summary>Swaps the first two non-blank tiles to flip inversion parity.</summary>
        private void FixParity()
        {
            int first = -1;
            for (int i = 0; i < Count; i++)
            {
                if (Cells[i] == Blank) continue;
                if (first < 0) { first = i; continue; }
                (Cells[first], Cells[i]) = (Cells[i], Cells[first]);
                return;
            }
        }

        /// <summary>
        /// Chooses the tile grid for a snapshot of the given pixel size. A square
        /// image (map) uses n x n. A rectangular image (layout) uses n tiles along
        /// the SHORT edge and round(long/short * n) along the long edge, so the
        /// tiles come out as close to square as possible.
        /// </summary>
        public static (int rows, int cols) ChooseDims(int pixelWidth, int pixelHeight,
            int n, bool isLayout)
        {
            if (!isLayout)
            {
                return (n, n);
            }

            int shortPx = Math.Min(pixelWidth, pixelHeight);
            int longPx = Math.Max(pixelWidth, pixelHeight);
            int longCount = Math.Max(2, (int)Math.Round((double)longPx / shortPx * n));

            // Orient: the short edge gets n tiles.
            if (pixelWidth <= pixelHeight)   // portrait: width is short
            {
                return (longCount, n);       // rows = longCount, cols = n
            }
            return (n, longCount);           // landscape: rows = n, cols = longCount
        }
    }
}
