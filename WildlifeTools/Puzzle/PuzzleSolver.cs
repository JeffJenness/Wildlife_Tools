using System;
using System.Collections.Generic;
using System.Text;

namespace WildlifeTools.Puzzle
{
    /// <summary>
    /// Computes a solution for a sliding-tile puzzle of any R x C (>= 2) size by a
    /// layer-by-layer reduction: solve the top rows one at a time (the last two
    /// tiles of each row via the classic corner rotation), then solve the left of
    /// the remaining two rows column-pair by column-pair, then finish the last
    /// 2x2 by rotation. The result is the sequence of cell indices to slide, each
    /// valid in turn from the supplied state, that drives the board to solved.
    ///
    /// This is deliberately simple-and-always-correct rather than optimal: the
    /// reported move count is THIS solver's solution length, not the theoretical
    /// minimum (optimal solving is intractable for 5x5 and 6x6). Returns null only
    /// if the input is unsolvable (which the game never produces).
    /// </summary>
    internal static class PuzzleSolver
    {
        public static List<int> Solve(int rows, int cols, int[] cells)
            => new Worker(rows, cols, cells).Solve();

        private sealed class Worker
        {
            private readonly int R, C, N, Blank;
            private readonly int[] at;    // tile id at each position (Blank = N-1)
            private readonly int[] pos;   // position of each tile
            private readonly bool[] locked;
            private readonly List<int> moves = new List<int>();

            public Worker(int rows, int cols, int[] cells)
            {
                R = rows; C = cols; N = R * C; Blank = N - 1;
                at = new int[N]; pos = new int[N]; locked = new bool[N];
                for (int i = 0; i < N; i++) at[i] = cells[i] < 0 ? Blank : cells[i];
                for (int i = 0; i < N; i++) pos[at[i]] = i;
            }

            private int BlankPos => pos[Blank];

            private void Swap(int q)
            {
                int b = BlankPos;
                int t = at[q];
                at[b] = t; at[q] = Blank;
                pos[t] = b; pos[Blank] = q;
                moves.Add(q);
            }

            private IEnumerable<int> Neighbors(int p)
            {
                int r = p / C, c = p % C;
                if (r > 0) yield return p - C;
                if (r < R - 1) yield return p + C;
                if (c > 0) yield return p - 1;
                if (c < C - 1) yield return p + 1;
            }

            /// <summary>Move a tile to a target cell without disturbing any locked
            /// (already-solved) cell. Searches the joint (tile-position,
            /// blank-position) space over unlocked cells with BFS, so it finds a
            /// valid maneuver whenever one exists -- robust against the narrow-
            /// channel pockets that trip up a greedy mover.</summary>
            private bool PlaceTile(int tile, int target)
            {
                if (pos[tile] == target) return true;

                int start = pos[tile] * N + BlankPos;
                var prevState = new int[N * N];
                var prevMove = new int[N * N];
                for (int i = 0; i < prevState.Length; i++) prevState[i] = -2;

                var queue = new Queue<int>();
                queue.Enqueue(start); prevState[start] = -1;
                int goal = -1;
                while (queue.Count > 0)
                {
                    int s = queue.Dequeue();
                    int t = s / N, b = s % N;
                    if (t == target) { goal = s; break; }
                    foreach (int nb in Neighbors(b))
                    {
                        if (locked[nb]) continue;         // never disturb solved tiles
                        int nt = (nb == t) ? b : t;       // blank moving onto the tile pushes it
                        int ns = nt * N + nb;
                        if (prevState[ns] != -2) continue;
                        prevState[ns] = s; prevMove[ns] = nb;
                        queue.Enqueue(ns);
                    }
                }
                if (goal < 0) return false;

                var seq = new List<int>();
                for (int s = goal; prevState[s] != -1; s = prevState[s]) seq.Add(prevMove[s]);
                seq.Reverse();
                foreach (int mv in seq) Swap(mv);
                return true;
            }

            public List<int> Solve()
            {
                // Top rows, until two rows remain.
                for (int r = 0; r <= R - 3; r++)
                {
                    for (int c = 0; c <= C - 3; c++)
                    {
                        if (!PlaceTile(r * C + c, r * C + c)) return null;
                        locked[r * C + c] = true;
                    }
                    if (!SolveRowLastTwo(r)) return null;
                }
                // Left columns of the final two rows, until a 2x2 remains.
                for (int c = 0; c <= C - 3; c++)
                {
                    if (!SolveColPair(c)) return null;
                }
                return SolveFinalRegion() ? moves : null;
            }

            private bool SolveRowLastTwo(int r)
                => PlaceTwo(r * C + (C - 2), r * C + (C - 1));

            private bool SolveColPair(int c)
                => PlaceTwo((R - 2) * C + c, (R - 1) * C + c);

            /// <summary>Place two tiles into their homes SIMULTANEOUSLY via a BFS
            /// over the joint (tile1, tile2, blank) state through unlocked cells,
            /// then lock both. Placing the last pair of a row or column at once is
            /// what avoids the classic "placing the second disturbs the first"
            /// deadlock -- and the joint BFS also sidesteps every narrow-channel
            /// pocket a staged rotation would fall into. Tile ids equal their home
            /// positions, so g1 = t1 and g2 = t2.</summary>
            private bool PlaceTwo(int t1, int t2)
            {
                if (pos[t1] == t1 && pos[t2] == t2)
                {
                    locked[t1] = true; locked[t2] = true; return true;
                }

                int Encode(int a, int b, int bl) => (a * N + b) * N + bl;
                int start = Encode(pos[t1], pos[t2], BlankPos);
                var prevState = new Dictionary<int, int>();
                var prevMove = new Dictionary<int, int>();
                var queue = new Queue<int>();
                queue.Enqueue(start); prevState[start] = -1;
                int goal = -1;
                while (queue.Count > 0)
                {
                    int s = queue.Dequeue();
                    int bl = s % N, p2 = (s / N) % N, p1 = s / (N * N);
                    if (p1 == t1 && p2 == t2) { goal = s; break; }
                    foreach (int nb in Neighbors(bl))
                    {
                        if (locked[nb]) continue;
                        int np1 = p1, np2 = p2;
                        if (nb == p1) np1 = bl; else if (nb == p2) np2 = bl;
                        int ns = Encode(np1, np2, nb);
                        if (prevState.ContainsKey(ns)) continue;
                        prevState[ns] = s; prevMove[ns] = nb;
                        queue.Enqueue(ns);
                    }
                }
                if (goal < 0) return false;

                var seq = new List<int>();
                for (int s = goal; prevState[s] != -1; s = prevState[s]) seq.Add(prevMove[s]);
                seq.Reverse();
                foreach (int mv in seq) Swap(mv);
                locked[t1] = true; locked[t2] = true;
                return true;
            }

            /// <summary>Solve whatever unlocked cells remain (the final 2x2) by an
            /// exhaustive BFS over the region's arrangements -- tiny and always
            /// correct, with no rotation special-casing.</summary>
            private bool SolveFinalRegion()
            {
                var free = new List<int>();
                for (int i = 0; i < N; i++) if (!locked[i]) free.Add(i);

                bool IsGoal(int[] a)
                {
                    foreach (int c in free) if (a[c] != Blank && a[c] != c) return false;
                    return true;
                }
                string Key(int[] a)
                {
                    var sb = new StringBuilder();
                    foreach (int c in free) sb.Append(a[c]).Append(':');
                    return sb.ToString();
                }

                var startArr = (int[])at.Clone();
                if (IsGoal(startArr)) return true;

                var prev = new Dictionary<string, (string pk, int move)>();
                var q = new Queue<int[]>();
                prev[Key(startArr)] = (null, -1);
                q.Enqueue(startArr);
                int[] goalArr = null;
                while (q.Count > 0)
                {
                    var a = q.Dequeue();
                    if (IsGoal(a)) { goalArr = a; break; }
                    int bp = -1;
                    foreach (int c in free) if (a[c] == Blank) { bp = c; break; }
                    foreach (int nb in Neighbors(bp))
                    {
                        if (locked[nb]) continue;
                        var na = (int[])a.Clone();
                        na[bp] = na[nb]; na[nb] = Blank;
                        string nk = Key(na);
                        if (prev.ContainsKey(nk)) continue;
                        prev[nk] = (Key(a), nb);
                        q.Enqueue(na);
                    }
                }
                if (goalArr == null) return false;

                var seq = new List<int>();
                string k = Key(goalArr);
                while (prev[k].pk != null) { seq.Add(prev[k].move); k = prev[k].pk; }
                seq.Reverse();
                foreach (int mv in seq) Swap(mv);
                return true;
            }
        }
    }
}
