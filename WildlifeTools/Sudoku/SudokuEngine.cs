using System;
using System.Collections.Generic;

namespace WildlifeTools.Sudoku
{
    /// <summary>A generated Sudoku puzzle: the full solution plus which cells
    /// are shown as givens.</summary>
    public sealed class SudokuPuzzle
    {
        public int[,] Solution { get; }     // 9x9, digits 1-9
        public bool[,] IsGiven { get; }     // true where the cell starts filled
        public int GivenCount { get; }

        public SudokuPuzzle(int[,] solution, bool[,] isGiven, int givenCount)
        { Solution = solution; IsGiven = isGiven; GivenCount = givenCount; }
    }

    /// <summary>
    /// Sudoku generator/solver. Generation: (1) fill a complete valid grid by
    /// backtracking with a randomized candidate order; (2) remove cells in
    /// 180-degree-symmetric pairs, in random order, keeping a removal only if
    /// the puzzle still has EXACTLY ONE solution (counting solver with an
    /// early exit at 2); stop at the difficulty's target number of givens.
    /// Every puzzle produced is therefore uniquely solvable. The solver uses
    /// bitmask candidate sets and a most-constrained-cell heuristic, so even
    /// hard-level generation completes in well under a second.
    /// </summary>
    public static class SudokuEngine
    {
        /// <summary>Target given counts per difficulty (0=Easy, 1=Medium,
        /// 2=Hard). Symmetric unique-solution removal treats these as
        /// best-effort floors -- a few extra givens may survive.</summary>
        public static readonly int[] TargetGivens = { 40, 32, 26 };

        public static SudokuPuzzle Generate(int targetGivens, Random rng)
        {
            var grid = new int[9, 9];
            if (!Fill(grid, 0, rng))
                throw new InvalidOperationException("Sudoku fill failed (unreachable).");
            var solution = (int[,])grid.Clone();

            // removal order: shuffled cell indices; process symmetric pairs
            var order = new int[81];
            for (int i = 0; i < 81; i++) order[i] = i;
            Shuffle(order, rng);

            var given = new bool[9, 9];
            for (int r = 0; r < 9; r++)
                for (int c = 0; c < 9; c++)
                    given[r, c] = true;
            int count = 81;
            int floor = Math.Max(targetGivens, 17);   // 17 = theoretical minimum

            foreach (int p in order)
            {
                if (count <= floor) break;
                int q = 80 - p;                        // 180-degree twin
                int r1 = p / 9, c1 = p % 9, r2 = q / 9, c2 = q % 9;
                if (!given[r1, c1]) continue;          // already removed (as a twin)
                int removing = (p == q) ? 1 : 2;
                if (count - removing < floor) continue;

                int v1 = grid[r1, c1], v2 = grid[r2, c2];
                grid[r1, c1] = 0; grid[r2, c2] = 0;
                if (CountSolutions(grid, 2) == 1)
                {
                    given[r1, c1] = false; given[r2, c2] = false;
                    count -= removing;
                }
                else
                {
                    grid[r1, c1] = v1; grid[r2, c2] = v2;   // not unique: restore
                }
            }
            return new SudokuPuzzle(solution, given, count);
        }

        // ---- complete-grid fill (randomized backtracking) -------------------
        private static bool Fill(int[,] g, int idx, Random rng)
        {
            if (idx == 81) return true;
            int r = idx / 9, c = idx % 9;
            Span<int> digits = stackalloc int[9];
            for (int i = 0; i < 9; i++) digits[i] = i + 1;
            for (int i = 8; i > 0; i--)                 // Fisher-Yates
            {
                int j = rng.Next(i + 1);
                (digits[i], digits[j]) = (digits[j], digits[i]);
            }
            foreach (int d in digits)
            {
                if (IsLegal(g, r, c, d))
                {
                    g[r, c] = d;
                    if (Fill(g, idx + 1, rng)) return true;
                    g[r, c] = 0;
                }
            }
            return false;
        }

        private static bool IsLegal(int[,] g, int r, int c, int d)
        {
            for (int i = 0; i < 9; i++)
                if (g[r, i] == d || g[i, c] == d) return false;
            int br = r / 3 * 3, bc = c / 3 * 3;
            for (int i = 0; i < 3; i++)
                for (int j = 0; j < 3; j++)
                    if (g[br + i, bc + j] == d) return false;
            return true;
        }

        /// <summary>Number of solutions of ``g``, stopping as soon as
        /// ``limit`` are found (limit=2 = uniqueness test). ``g`` is restored
        /// before returning.</summary>
        public static int CountSolutions(int[,] g, int limit)
        {
            var rows = new int[9]; var cols = new int[9]; var boxes = new int[9];
            var empties = new List<int>(64);
            for (int r = 0; r < 9; r++)
                for (int c = 0; c < 9; c++)
                {
                    int d = g[r, c];
                    if (d == 0) { empties.Add(r * 9 + c); continue; }
                    int bit = 1 << d;
                    rows[r] |= bit; cols[c] |= bit; boxes[r / 3 * 3 + c / 3] |= bit;
                }
            int found = 0;
            Search(g, rows, cols, boxes, empties, limit, ref found);
            return found;
        }

        private static void Search(int[,] g, int[] rows, int[] cols, int[] boxes,
                                   List<int> empties, int limit, ref int found)
        {
            if (found >= limit) return;
            // most-constrained empty cell (fewest candidates)
            int best = -1, bestMask = 0, bestCount = 10;
            for (int i = 0; i < empties.Count; i++)
            {
                int cell = empties[i];
                if (cell < 0) continue;
                int r = cell / 9, c = cell % 9;
                int used = rows[r] | cols[c] | boxes[r / 3 * 3 + c / 3];
                int mask = ~used & 0x3FE;               // candidate bits 1..9
                int n = System.Numerics.BitOperations.PopCount((uint)mask);
                if (n == 0) return;                     // dead end
                if (n < bestCount) { bestCount = n; best = i; bestMask = mask; if (n == 1) break; }
            }
            if (best == -1) { found++; return; }        // no empties: solved

            int cell2 = empties[best];
            int rr = cell2 / 9, cc = cell2 % 9, bb = rr / 3 * 3 + cc / 3;
            empties[best] = -1;
            for (int d = 1; d <= 9 && found < limit; d++)
            {
                int bit = 1 << d;
                if ((bestMask & bit) == 0) continue;
                rows[rr] |= bit; cols[cc] |= bit; boxes[bb] |= bit; g[rr, cc] = d;
                Search(g, rows, cols, boxes, empties, limit, ref found);
                rows[rr] &= ~bit; cols[cc] &= ~bit; boxes[bb] &= ~bit; g[rr, cc] = 0;
            }
            empties[best] = cell2;
        }

        private static void Shuffle(int[] a, Random rng)
        {
            for (int i = a.Length - 1; i > 0; i--)
            {
                int j = rng.Next(i + 1);
                (a[i], a[j]) = (a[j], a[i]);
            }
        }
    }
}
