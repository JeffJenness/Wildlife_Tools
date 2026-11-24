// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Award notice, marking of modified versions, use of names,
// and an additional permission to link with ArcGIS Pro).
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

// Headless test of WindowFit: every window the add-in makes must fit a
// laptop screen (Jeff Jenness, 23 November 2026). Plain WPF windows are
// shaped for a work area that is GIVEN (a laptop's, a large monitor's) and
// never shown, so the test needs no screen of any particular size and no
// ArcGIS Pro.
//
//   dotnet run --project claude_tests/WindowFitTest
//
// The laptop: a 1366 by 768 screen, or 1920 by 1080 at 150 percent, leaves a
// work area of about 1280 by 672 once the taskbar is taken off. With the
// margin of 16 a window may be 1264 by 656 there.
//
// (That every window CALLS WindowFit is checked by claude_tests/window_fit_test.py.)

using System;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Threading;
using WildlifeTools;

namespace WindowFitTest
{
    internal static class Program
    {
        private static int _passed, _failed;

        private static void Check(string name, bool ok, string detail = "")
        {
            if (ok) _passed++; else _failed++;
            Console.WriteLine((ok ? "ok    " : "FAIL  ") + name + (ok || detail.Length == 0 ? "" : "  " + detail));
        }

        private static readonly Rect Laptop = new Rect(0, 0, 1280, 672);
        private static readonly Rect Large = new Rect(0, 0, 2560, 1400);

        private static Grid Form(int rows, bool oneRowTakesTheRest, double rowHeight = 60)
        {
            var g = new Grid { Margin = new Thickness(12) };
            for (int i = 0; i < rows; i++)
            {
                g.RowDefinitions.Add(new RowDefinition
                {
                    Height = oneRowTakesTheRest && i == rows - 2 ? new GridLength(1, GridUnitType.Star) : GridLength.Auto,
                });
                var b = new Button { Content = "row " + i, Height = rowHeight };
                Grid.SetRow(b, i);
                g.Children.Add(b);
            }
            return g;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct NativeRect { public int Left, Top, Right, Bottom; }

        [DllImport("user32.dll")]
        private static extern bool GetWindowRect(IntPtr hwnd, out NativeRect rect);

        [DllImport("user32.dll")]
        private static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int cx, int cy, uint flags);

        /// <summary>A window that behaves as ArcGIS Pro's ProWindow does: at
        /// some moment of its opening, after its constructor, the size and
        /// place it had when it was last closed are put back, straight onto
        /// the window. The moments tried are every one a window offers,
        /// before and after the listeners of each.</summary>
        private sealed class Remembering : Window
        {
            public const int Never = 0, WhenItGetsItsHandle = 1, AfterThat = 2, WhenLoadedFirst = 3, WhenLoadedLast = 4,
                WhenFirstDrawn = 5, AfterFirstDrawn = 6;
            public int When;
            public Int32Rect Remembered = new Int32Rect(1490, 28, 1120, 788);    // Jeff's TPI Neighborhood Sampler

            public Remembering()
            {
                // (ProWindow's own constructor runs before the window's: what it listens for, it hears first)
                Loaded += (s, e) => { if (When == WhenLoadedFirst) PutBack(); };
            }

            public void ListenLast()
            {
                Loaded += (s, e) => { if (When == WhenLoadedLast) PutBack(); };
            }

            protected override void OnSourceInitialized(EventArgs e)
            {
                if (When == WhenItGetsItsHandle) PutBack();
                base.OnSourceInitialized(e);                    // the listeners hear it here
                if (When == AfterThat) PutBack();
            }

            protected override void OnContentRendered(EventArgs e)
            {
                if (When == WhenFirstDrawn) PutBack();
                base.OnContentRendered(e);
                if (When == AfterFirstDrawn) PutBack();
            }

            private void PutBack()
            {
                SetWindowPos(new WindowInteropHelper(this).Handle, IntPtr.Zero, Remembered.X, Remembered.Y, Remembered.Width,
                    Remembered.Height, 0x0004 | 0x0010);
            }
        }

        /// <summary>Opens a window for a moment, lets everything that was
        /// waiting run, and says where the window ended up, in pixels.</summary>
        private static Int32Rect OpenAndMeasure(Window w)
        {
            w.ShowActivated = w.WindowState == WindowState.Maximized;     // (a maximized window may not open unactivated)
            w.ShowInTaskbar = false;
            w.Show();
            var frame = new DispatcherFrame();
            var timer = new DispatcherTimer(DispatcherPriority.ApplicationIdle) { Interval = TimeSpan.FromMilliseconds(350) };
            timer.Tick += (s, e) => { timer.Stop(); frame.Continue = false; };
            timer.Start();
            Dispatcher.PushFrame(frame);
            GetWindowRect(new WindowInteropHelper(w).Handle, out NativeRect r);
            w.Close();
            return new Int32Rect(r.Left, r.Top, r.Right - r.Left, r.Bottom - r.Top);
        }

        private static bool Fits(Int32Rect at, Int32Rect work, int margin)
        {
            return at.Width <= work.Width - margin && at.Height <= work.Height - margin && at.X >= work.X && at.Y >= work.Y
                && at.X + at.Width <= work.X + work.Width && at.Y + at.Height <= work.Y + work.Height;
        }

        private static string Px(Int32Rect r)
        {
            return r.Width + " x " + r.Height + " at " + r.X + ", " + r.Y;
        }

        private static string Size(Window w)
        {
            return w.Width + " x " + w.Height + ", least " + w.MinWidth + " x " + w.MinHeight + ", most " + w.MaxWidth + " x "
                + w.MaxHeight + ", content " + (w.Content == null ? "none" : w.Content.GetType().Name);
        }

        private static int Main()
        {
            int rc = 1;
            var t = new Thread(() => rc = Run());
            t.SetApartmentState(ApartmentState.STA);
            t.Start();
            t.Join();
            return rc;
        }

        private static int Run()
        {
            // ---- the limit -----------------------------------------------------------------------------------
            Size limit = WindowFit.Limit(Laptop);
            Check("the laptop: a window may be 1264 by 656", limit.Width == 1264 && limit.Height == 656, limit.ToString());
            Size f = WindowFit.Fitted(1040, 780, Laptop);
            Check("a size of 1040 by 780 opens at 1040 by 656 there", f.Width == 1040 && f.Height == 656, f.ToString());
            f = WindowFit.Fitted(double.NaN, 300, Laptop);
            Check("a width that is not set stays unset", double.IsNaN(f.Width) && f.Height == 300, f.ToString());
            limit = WindowFit.Limit(new Rect(0, 0, 300, 200));
            Check("a work area too small to be true: the limit never falls below 320 by 240",
                limit.Width == 320 && limit.Height == 240, limit.ToString());

            // ---- a window with a size of its own ---------------------------------------------------------------
            var w = new Window { Width = 1040, Height = 780, MinWidth = 860, MinHeight = 620, Content = Form(4, true) };
            WindowFit.Shape(w, Laptop);
            Check("own size, taller than the laptop: opens at the laptop's height, width and least size as they were",
                w.Width == 1040 && w.Height == 656 && w.MinWidth == 860 && w.MinHeight == 620, Size(w));
            Check("own size: no greatest size is set (the window can still be enlarged on a larger screen), no scroll box",
                double.IsPositiveInfinity(w.MaxHeight) && double.IsPositiveInfinity(w.MaxWidth) && w.Content is Grid, Size(w));

            w = new Window { Width = 1120, Height = 740, MinWidth = 860, MinHeight = 660, Content = Form(4, true) };
            WindowFit.Shape(w, Laptop);
            Check("own size, with a least height above the laptop's: the least height is lowered with it",
                w.Height == 656 && w.MinHeight == 656 && w.Width == 1120 && w.MinWidth == 860, Size(w));

            w = new Window { Width = 1400, Height = 500, MinWidth = 1300, MinHeight = 400 };
            WindowFit.Shape(w, Laptop);
            Check("own size, wider than the laptop: width and least width come down to 1264",
                w.Width == 1264 && w.MinWidth == 1264 && w.Height == 500 && w.MinHeight == 400, Size(w));

            w = new Window { Width = 380, Height = 300, MinWidth = 200, MinHeight = 100, Content = Form(3, false) };
            WindowFit.Shape(w, Laptop);
            Check("own size, small: untouched",
                w.Width == 380 && w.Height == 300 && w.MinWidth == 200 && w.MinHeight == 100 && w.Content is Grid, Size(w));

            w = new Window { Width = 1040, Height = 780, MinWidth = 860, MinHeight = 620 };
            WindowFit.Shape(w, Large);
            Check("own size, on a large monitor: untouched", w.Width == 1040 && w.Height == 780 && w.MinHeight == 620, Size(w));

            // ---- a window that sizes itself to its content ------------------------------------------------------
            Grid form = Form(8, false);
            w = new Window { Width = 380, SizeToContent = SizeToContent.Height, ResizeMode = ResizeMode.NoResize, Content = form };
            WindowFit.Shape(w, Laptop);
            var box = w.Content as ScrollViewer;
            Check("sized to its content, a form of rows that cannot give way: its greatest height is the laptop's",
                w.MaxHeight == 656 && w.Width == 380 && double.IsPositiveInfinity(w.MaxWidth), Size(w));
            Check("sized to its content, such a form: put in a scroll box that scrolls up and down only, and takes no focus",
                box != null && ReferenceEquals(box.Content, form) && box.VerticalScrollBarVisibility == ScrollBarVisibility.Auto
                && box.HorizontalScrollBarVisibility == ScrollBarVisibility.Disabled && !box.Focusable, Size(w));
            WindowFit.Shape(w, Laptop);
            Check("shaped a second time: not put in a second scroll box",
                ReferenceEquals(w.Content, box) && ReferenceEquals(box.Content, form), Size(w));

            form = Form(8, true);
            w = new Window { Width = 470, SizeToContent = SizeToContent.Height, Content = form };
            WindowFit.Shape(w, Laptop);
            Check("sized to its content, with a row that takes the rest: limited to the laptop, and no scroll box (the row gives way)",
                w.MaxHeight == 656 && ReferenceEquals(w.Content, form), Size(w));

            w = new Window { Width = 520, SizeToContent = SizeToContent.Height, MaxHeight = 500, Content = new StackPanel() };
            WindowFit.Shape(w, Laptop, scrollContent: false);
            Check("a greatest height already below the laptop's is kept", w.MaxHeight == 500, Size(w));
            var card = new StackPanel();
            w = new Window { Width = 900, SizeToContent = SizeToContent.Height, MaxHeight = 880, Content = card };
            WindowFit.Shape(w, Laptop, scrollContent: false);
            Check("a card (it scrolls its own text): its greatest height comes down from 880 to 656, and it gets no scroll box",
                w.MaxHeight == 656 && ReferenceEquals(w.Content, card), Size(w));
            w = new Window { Width = 900, SizeToContent = SizeToContent.Height, MaxHeight = 880, Content = new StackPanel() };
            WindowFit.Shape(w, Large, scrollContent: false);
            Check("the card on a large monitor: its own greatest height stands", w.MaxHeight == 880, Size(w));

            var panel = new StackPanel();
            w = new Window { SizeToContent = SizeToContent.WidthAndHeight, Content = panel };
            WindowFit.Shape(w, Laptop);
            box = w.Content as ScrollViewer;
            Check("sized to its content both ways, a stack of things: limited both ways, scroll box both ways",
                w.MaxWidth == 1264 && w.MaxHeight == 656 && box != null && ReferenceEquals(box.Content, panel)
                && box.VerticalScrollBarVisibility == ScrollBarVisibility.Auto
                && box.HorizontalScrollBarVisibility == ScrollBarVisibility.Auto, Size(w));

            var own = new ScrollViewer { Content = new StackPanel() };
            w = new Window { Width = 400, SizeToContent = SizeToContent.Height, Content = own };
            WindowFit.Shape(w, Laptop);
            Check("content that is a scroll box already: left as it is", ReferenceEquals(w.Content, own) && w.MaxHeight == 656, Size(w));

            var dock = new DockPanel { LastChildFill = true };
            w = new Window { Width = 400, SizeToContent = SizeToContent.Height, Content = dock };
            WindowFit.Shape(w, Laptop);
            Check("a dock panel whose last child fills: no scroll box (that child gives way)", ReferenceEquals(w.Content, dock), Size(w));

            var bare = new Grid();
            w = new Window { Width = 400, SizeToContent = SizeToContent.Height, Content = bare };
            WindowFit.Shape(w, Laptop);
            Check("a grid with no rows named (one row that takes everything): no scroll box", ReferenceEquals(w.Content, bare), Size(w));

            var text = new TextBlock { Text = "a window with nothing but text" };
            w = new Window { Content = text };
            WindowFit.Shape(w, Laptop);
            Check("no size given and not sized to its content (Windows chooses): limited to the laptop, content left to fill it",
                w.MaxWidth == 1264 && w.MaxHeight == 656 && ReferenceEquals(w.Content, text), Size(w));

            w = new Window { Width = 400, SizeToContent = SizeToContent.Height, Content = "only words" };
            WindowFit.Shape(w, Laptop);
            Check("content that is not an element: limited, and nothing else done", w.MaxHeight == 656 && (w.Content as string) == "only words");

            Check("the choice of a scroll box: a form of fixed rows yes, by height; not when only the width is from the content and it has a column that gives way",
                WindowFit.NeedsScrollBox(Form(3, false), false, true) && !WindowFit.NeedsScrollBox(Form(3, false), true, false));

            // ---- the scroll box really scrolls: a form taller than the laptop, laid out at the laptop's height ----
            form = Form(14, false, 60);                          // 14 rows of 60 and a margin: 864 high
            w = new Window { Width = 380, SizeToContent = SizeToContent.Height, Content = form };
            WindowFit.Shape(w, Laptop);
            box = (ScrollViewer)w.Content;
            box.Measure(new Size(380, 656));
            box.Arrange(new Rect(0, 0, 380, 656));
            box.UpdateLayout();
            Check("a form 864 high in a window 656 high: all of it can be scrolled to (the last row, with its buttons, too)",
                Math.Abs(box.ExtentHeight - 864) < 1 && box.ScrollableHeight > 200 && box.ViewportHeight <= 656,
                "extent " + box.ExtentHeight + ", viewport " + box.ViewportHeight + ", can scroll " + box.ScrollableHeight);
            form = Form(4, false, 60);
            w = new Window { Width = 380, SizeToContent = SizeToContent.Height, Content = form };
            WindowFit.Shape(w, Laptop);
            box = (ScrollViewer)w.Content;
            box.Measure(new Size(380, 656));
            Check("a form that fits: the scroll box asks for the form's own height and no more, so the window looks as before",
                Math.Abs(box.DesiredSize.Height - 264) < 1, "asks for " + box.DesiredSize.Height);

            // ---- a window on the screen, fitted to its monitor (in pixels) ---------------------------------------
            var small = new Int32Rect(0, 0, 1366, 728);          // a 1366 by 768 monitor less its taskbar
            Int32Rect r = WindowFit.FitInside(new Int32Rect(1490, 28, 1120, 788), small, 16, 16);
            Check("the TPI Neighborhood Sampler as ArcGIS Pro remembered it (1120 by 788 at 1490, 28), on a 1366 by 768 monitor: "
                + "712 high and wholly on the screen", r.Width == 1120 && r.Height == 712 && r.X == 246 && r.Y == 16, Px(r));
            r = WindowFit.FitInside(new Int32Rect(100, 28, 577, 843), small, 16, 16);
            Check("Repeating Shapes as remembered (577 by 843): 712 high, moved up to show its last row",
                r.Width == 577 && r.Height == 712 && r.X == 100 && r.Y == 16, Px(r));
            r = WindowFit.FitInside(new Int32Rect(-80, -30, 400, 300), small, 16, 16);
            Check("hanging off the left and the top: moved right and down, its size kept",
                r.X == 0 && r.Y == 0 && r.Width == 400 && r.Height == 300, Px(r));
            r = WindowFit.FitInside(new Int32Rect(100, 100, 400, 300), small, 16, 16);
            Check("inside already: not touched", r.X == 100 && r.Y == 100 && r.Width == 400 && r.Height == 300, Px(r));
            r = WindowFit.FitInside(new Int32Rect(50, 50, 3000, 2000), small, 16, 16);
            Check("far larger than the screen: brought down to the screen less the margin, and moved the least that puts it inside",
                r.X == 16 && r.Y == 16 && r.Width == 1350 && r.Height == 712, Px(r));
            r = WindowFit.FitInside(new Int32Rect(1700, 300, 900, 700), new Int32Rect(1366, 0, 1366, 728), 16, 16);
            Check("on a second monitor to the right: fitted to THAT monitor",
                r.X == 1700 && r.Y == 28 && r.Width == 900 && r.Height == 700, Px(r));
            r = WindowFit.FitInside(new Int32Rect(10, 10, 900, 900), small, 24, 24, keepWidth: false, keepHeight: true);
            Check("a side the window sizes to its content is not resized here (its greatest size holds it), only moved",
                r.Height == 900 && r.Y == 0 && r.Width == 900 && r.X == 10, Px(r));
            r = WindowFit.FitInside(new Int32Rect(0, 0, 500, 400), new Int32Rect(0, 0, 300, 200), 16, 16);
            Check("a work area too small to be true: never asked to be smaller than 320 by 240",
                r.Width == 320 && r.Height == 240, Px(r));

            // ---- windows that REALLY open, with a remembered size put back after the constructor -------------------
            // What Jeff met on 23 November 2026. Each window below is made as a XAML window is (a size, a least size,
            // WindowFit.Apply in the constructor's place), opened on this screen for a third of a second, and measured.
            // The work area is given, so the test does not need a small monitor.
            WindowFit.TestWorkArea = small;
            var moments = new[]
            {
                (Remembering.Never, "nothing remembered (a first use): the size its XAML asks for is cut to the screen"),
                (Remembering.WhenItGetsItsHandle, "remembered size put back as the window gets its handle"),
                (Remembering.AfterThat, "put back just after that, when the listeners have already heard"),
                (Remembering.WhenLoadedFirst, "put back when the window is loaded, by a listener that came first"),
                (Remembering.WhenLoadedLast, "put back when the window is loaded, by a listener that came last"),
                (Remembering.WhenFirstDrawn, "put back as the window is first drawn"),
                (Remembering.AfterFirstDrawn, "put back after the window is first drawn, when the listeners have already heard"),
            };
            foreach (var (when, words) in moments)
            {
                var win = new Remembering { When = when, Width = 1120, Height = 800, MinWidth = 880, MinHeight = 600, Content = Form(4, true) };
                WindowFit.Apply(win);
                win.ListenLast();
                Int32Rect at = OpenAndMeasure(win);
                Check("opened for real, " + words + ": it ends no larger than the screen and wholly on it",
                    Fits(at, small, 16) && at.Height == 712 && at.Width == 1120, "it ended " + Px(at));
            }

            var tallForm = Form(20, false, 60);                   // 1,224 high: a form that cannot give way
            var content = new Remembering { When = Remembering.Never, Width = 380, SizeToContent = SizeToContent.Height, Content = tallForm };
            WindowFit.Apply(content);
            Int32Rect got = OpenAndMeasure(content);
            Check("opened for real, a window sized to content taller than the screen: held to the screen, with a scroll box",
                Fits(got, small, 16) && content.Content is ScrollViewer, "it ended " + Px(got));

            var big = new Remembering { When = Remembering.Never, Width = 600, Height = 400, WindowState = WindowState.Maximized };
            WindowFit.Apply(big);
            Int32Rect full = OpenAndMeasure(big);
            Check("opened for real, maximized: left alone (it is larger than the small work area given, and stays so)",
                full.Width > small.Width || full.Height > small.Height, "it ended " + Px(full));
            WindowFit.TestWorkArea = null;

            Console.WriteLine();
            Console.WriteLine(_passed + " passed, " + _failed + " failed");
            return _failed == 0 ? 0 : 1;
        }
    }
}
