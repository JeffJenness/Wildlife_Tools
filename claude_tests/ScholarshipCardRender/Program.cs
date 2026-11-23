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

// Draws the Lois Engelman Memorial Award card (the add-in's
// WildlifeTools.Scholarship.ScholarshipWindow) to PNG files, once for each
// photograph, WITHOUT ArcGIS Pro: the card is a plain WPF Window that needs
// nothing from Pro to draw itself. It reads the built add-in DLL, so build
// the add-in (Release) first.
//   dotnet run --project claude_tests/ScholarshipCardRender -- <output folder>

using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.Loader;
using System.Threading;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;

internal static class Program
{
    private const string ProBin = @"C:\Program Files\ArcGIS\Pro\bin";

    private static int Main(string[] args)
    {
        string outDir = args.Length > 0 ? args[0] : Directory.GetCurrentDirectory();
        Directory.CreateDirectory(outDir);
        int rc = 1;
        var t = new Thread(() => rc = Run(outDir));
        t.SetApartmentState(ApartmentState.STA);
        t.Start();
        t.Join();
        return rc;
    }

    private static string FindDll()
    {
        // The repo root is the first folder upward that holds WildlifeTools\.
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir != null && !Directory.Exists(Path.Combine(dir.FullName, "WildlifeTools")))
        {
            dir = dir.Parent;
        }
        return dir == null ? null : Path.Combine(dir.FullName, "WildlifeTools",
            "bin", "Release", "net10.0-windows", "WildlifeTools.dll");
    }

    private static int Run(string outDir)
    {
        string dll = FindDll();
        if (dll == null || !File.Exists(dll))
        {
            Console.WriteLine("Built add-in DLL not found: " + dll);
            return 1;
        }
        // ArcGIS assemblies the add-in names are found in Pro's bin if asked for.
        AssemblyLoadContext.Default.Resolving += (ctx, name) =>
        {
            string hit = Directory.EnumerateFiles(ProBin, name.Name + ".dll",
                SearchOption.AllDirectories).FirstOrDefault();
            return hit == null ? null : ctx.LoadFromAssemblyPath(hit);
        };
        // An Application object registers the pack:// scheme the card's
        // photographs and font are loaded through.
        _ = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };

        Assembly asm = Assembly.LoadFrom(dll);
        Type winType = asm.GetType("WildlifeTools.Scholarship.ScholarshipWindow", true);
        const BindingFlags Any = BindingFlags.Instance | BindingFlags.Static
            | BindingFlags.Public | BindingFlags.NonPublic;
        int count = (int)winType.GetProperty("PhotoCount", Any).GetValue(null);

        for (int i = 0; i < count; i++)
        {
            var win = (Window)Activator.CreateInstance(winType);
            winType.GetMethod("ShowPhoto", Any).Invoke(win, new object[] { i });

            // Lay the card out at the window's own width, as tall as it
            // wants to be, and draw it on white at 144 DPI.
            var card = (FrameworkElement)win.Content;
            win.Content = null;
            var host = new System.Windows.Controls.Border
            {
                Background = Brushes.White,
                Child = card,
                Width = win.Width,
            };
            foreach (object key in win.Resources.Keys)
            {
                host.Resources[key] = win.Resources[key];
            }
            host.Measure(new Size(win.Width, double.PositiveInfinity));
            host.Arrange(new Rect(host.DesiredSize));
            host.UpdateLayout();

            const double Scale = 1.5;
            var bmp = new RenderTargetBitmap(
                (int)Math.Ceiling(host.ActualWidth * Scale),
                (int)Math.Ceiling(host.ActualHeight * Scale),
                96 * Scale, 96 * Scale, PixelFormats.Pbgra32);
            bmp.Render(host);
            var enc = new PngBitmapEncoder();
            enc.Frames.Add(BitmapFrame.Create(bmp));
            string path = Path.Combine(outDir, "scholarship_card_" + (i + 1) + ".png");
            using (var fs = File.Create(path))
            {
                enc.Save(fs);
            }
            Console.WriteLine(path + "  " + host.ActualWidth + " x "
                + host.ActualHeight + " (window max height " + win.MaxHeight + ")");
        }
        return 0;
    }
}
