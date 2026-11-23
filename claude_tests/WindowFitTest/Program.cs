// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Award notice, marking of modified versions, and
// use of names).
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
using System.Threading;
using System.Windows;
using System.Windows.Controls;
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

            // ---- back inside the screen ----------------------------------------------------------------------
            var area = new Rect(0, 0, 1280, 672);
            Point p = WindowFit.Inside(new Rect(600, 200, 1040, 656), area);
            Check("hanging off the right and the bottom: moved left and up until it is inside", p.X == 240 && p.Y == 16, p.ToString());
            p = WindowFit.Inside(new Rect(-80, -30, 400, 300), area);
            Check("hanging off the left and the top: moved right and down", p.X == 0 && p.Y == 0, p.ToString());
            p = WindowFit.Inside(new Rect(100, 100, 400, 300), area);
            Check("inside already: not moved", p.X == 100 && p.Y == 100, p.ToString());
            p = WindowFit.Inside(new Rect(50, 50, 1500, 900), area);
            Check("larger than the screen: its top left corner stays in view", p.X == 0 && p.Y == 0, p.ToString());
            p = WindowFit.Inside(new Rect(2000, 300, 800, 600), new Rect(1920, 0, 1280, 672));
            Check("on a second monitor to the right: kept inside that monitor", p.X == 2000 && p.Y == 72, p.ToString());
            p = WindowFit.Inside(new Rect(double.NaN, double.NaN, 400, 300), area);
            Check("a window with no place yet: left alone", double.IsNaN(p.X) && double.IsNaN(p.Y));

            Console.WriteLine();
            Console.WriteLine(_passed + " passed, " + _failed + " failed");
            return _failed == 0 ? 0 : 1;
        }
    }
}
