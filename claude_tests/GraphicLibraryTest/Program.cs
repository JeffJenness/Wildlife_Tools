// Headless test of WildlifeTools.CustomGraphics.GraphicLibrary / GraphicImage
// (the per-user store of registered images behind the Add Graphics and North
// Arrows galleries). It compiles the add-in's own source file, so what passes
// here is what ships. Works in a temporary folder; the real store in the user
// profile is never touched.
//   dotnet run --project claude_tests/GraphicLibraryTest

using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using WildlifeTools.CustomGraphics;

internal static class Program
{
    private static int _pass, _fail;

    private static void Check(string name, bool ok, string detail = "")
    {
        if (ok) _pass++;
        else { _fail++; Console.WriteLine("FAIL  " + name + "  " + detail); }
    }

    private static void Near(string name, double got, double want, double tol = 1e-6) =>
        Check(name, Math.Abs(got - want) <= tol, "got " + got + ", want " + want);

    private static bool Throws<T>(Action a) where T : Exception
    {
        try { a(); return false; }
        catch (T) { return true; }
        catch (Exception) { return false; }
    }

    private static string _work;

    /// <summary>Writes a w x h picture (a colored box with a diagonal) in the given format.</summary>
    private static string MakeRaster(string name, int w, int h, BitmapEncoder enc)
    {
        var dv = new DrawingVisual();
        using (DrawingContext dc = dv.RenderOpen())
        {
            dc.DrawRectangle(Brushes.SteelBlue, null, new Rect(0, 0, w, h));
            dc.DrawLine(new Pen(Brushes.Gold, 6), new Point(0, 0), new Point(w, h));
        }
        var bmp = new RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32);
        bmp.Render(dv);
        BitmapSource src = enc is JpegBitmapEncoder || enc is BmpBitmapEncoder
            ? new FormatConvertedBitmap(bmp, PixelFormats.Bgr24, null, 0)
            : (BitmapSource)bmp;
        enc.Frames.Add(BitmapFrame.Create(src));
        string path = Path.Combine(_work, name);
        using (FileStream fs = File.Create(path)) enc.Save(fs);
        return path;
    }

    private static string MakeText(string name, string text)
    {
        string path = Path.Combine(_work, name);
        File.WriteAllText(path, text, new UTF8Encoding(false));
        return path;
    }

    private static byte[] EmfHeader(int fl, int ft, int fr, int fb, int bl, int bt, int br, int bb)
    {
        var b = new byte[108];
        BitConverter.GetBytes(1u).CopyTo(b, 0);            // EMR_HEADER
        BitConverter.GetBytes(108u).CopyTo(b, 4);
        BitConverter.GetBytes(bl).CopyTo(b, 8);
        BitConverter.GetBytes(bt).CopyTo(b, 12);
        BitConverter.GetBytes(br).CopyTo(b, 16);
        BitConverter.GetBytes(bb).CopyTo(b, 20);
        BitConverter.GetBytes(fl).CopyTo(b, 24);
        BitConverter.GetBytes(ft).CopyTo(b, 28);
        BitConverter.GetBytes(fr).CopyTo(b, 32);
        BitConverter.GetBytes(fb).CopyTo(b, 36);
        BitConverter.GetBytes(0x464D4520u).CopyTo(b, 40);  // " EMF"
        return b;
    }

    private static int PngLongSide(string path)
    {
        using (FileStream fs = File.OpenRead(path))
        {
            BitmapFrame f = BitmapDecoder.Create(fs, BitmapCreateOptions.None, BitmapCacheOption.OnLoad).Frames[0];
            return Math.Max(f.PixelWidth, f.PixelHeight);
        }
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
        _work = Path.Combine(Path.GetTempPath(), "GraphicLibraryTest_" + Guid.NewGuid().ToString("N"));
        string store = Path.Combine(_work, "store");
        Directory.CreateDirectory(_work);
        try
        {
            GraphicLibrary.UseStoreFolder(store);
            int changes = 0;
            GraphicLibrary.Changed += (_, _) => changes++;

            // ---------------------------------------------------- built-ins
            var bg = GraphicLibrary.BuiltIns(GraphicKind.Graphic);
            var ba = GraphicLibrary.BuiltIns(GraphicKind.NorthArrow);
            Check("four built-in graphics", bg.Count == 4);
            Check("four built-in north arrows", ba.Count == 4);
            Check("built-in graphic names", bg[1].Name == "Compass graphic 1 - dot" && bg[3].Name == "Compass graphic 2 - dot");
            Check("built-in arrow names", ba[0].Name == "North arrow 1" && ba[2].Name == "North arrow 2");
            Check("built-ins flagged", bg.All(e => e.IsBuiltIn) && ba.All(e => e.IsBuiltIn));
            Near("dot focal below the middle", bg[1].FocalY, 0.448416);
            Near("dot canvas is taller than wide", bg[1].Aspect, 0.896856);
            Check("same ids in both galleries", bg.Select(e => e.Id).SequenceEqual(ba.Select(e => e.Id)));
            Check("empty store: nothing registered", GraphicLibrary.Custom(GraphicKind.Graphic).Count == 0);
            Check("default choice is the dot rose",
                GraphicLibrary.Current(GraphicKind.Graphic).Id == bg[1].Id
                && GraphicLibrary.Current(GraphicKind.NorthArrow).Name == "North arrow 1 - dot");

            // ----------------------------------------------- SVG / EMF shape
            Check("svg width+height", GraphicImage.TrySvgAspect("<svg xmlns='http://www.w3.org/2000/svg' width='300' height='150'/>", out double a) && Math.Abs(a - 2.0) < 1e-9);
            Check("svg units", GraphicImage.TrySvgAspect("<svg width=\"2in\" height=\"96px\"/>", out a) && Math.Abs(a - 2.0) < 1e-9);
            Check("svg mm", GraphicImage.TrySvgAspect("<svg width=\"50.8mm\" height=\"25.4mm\"/>", out a) && Math.Abs(a - 2.0) < 1e-9);
            Check("svg viewBox only", GraphicImage.TrySvgAspect("<svg viewBox=\"0 0 400 100\"/>", out a) && Math.Abs(a - 4.0) < 1e-9);
            Check("svg viewBox commas", GraphicImage.TrySvgAspect("<svg viewBox=\"0,0,90,120\"/>", out a) && Math.Abs(a - 0.75) < 1e-9);
            Check("svg percent falls back to viewBox", GraphicImage.TrySvgAspect("<svg width=\"100%\" height=\"100%\" viewBox=\"0 0 30 10\"/>", out a) && Math.Abs(a - 3.0) < 1e-9);
            Check("svg prolog, doctype and comment",
                GraphicImage.TrySvgAspect("<?xml version=\"1.0\"?>\n<!DOCTYPE svg PUBLIC \"-//W3C//DTD SVG 1.1//EN\" \"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd\">\n<!-- made by hand -->\n<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"10\" height=\"40\"><rect/></svg>", out a)
                && Math.Abs(a - 0.25) < 1e-9);
            Check("svg with no size is refused", !GraphicImage.TrySvgAspect("<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>", out a));
            Check("not svg is refused", !GraphicImage.TrySvgAspect("<html><svg width='1' height='1'/></html>", out a));
            Check("garbage is refused", !GraphicImage.TrySvgAspect("this is not xml", out a));
            Check("emf frame", GraphicImage.TryEmfAspect(EmfHeader(0, 0, 6000, 2000, 0, 0, 299, 99), out a) && Math.Abs(a - 3.0) < 1e-9);
            Check("emf bounds when frame is empty", GraphicImage.TryEmfAspect(EmfHeader(0, 0, 0, 0, 10, 10, 209, 109), out a) && Math.Abs(a - 2.0) < 1e-9);
            Check("emf bad signature", !GraphicImage.TryEmfAspect(new byte[108], out a));
            Check("emf short header", !GraphicImage.TryEmfAspect(new byte[10], out a));

            // ------------------------------------------------------ register
            string png = MakeRaster("Agency Logo.png", 600, 300, new PngBitmapEncoder());
            string jpg = MakeRaster("photo.jpg", 200, 800, new JpegBitmapEncoder());
            string bmp = MakeRaster("seal.bmp", 120, 120, new BmpBitmapEncoder());
            string gif = MakeRaster("mark.gif", 90, 60, new GifBitmapEncoder());
            string tif = MakeRaster("scan.tif", 1000, 500, new TiffBitmapEncoder());
            string svg = MakeText("arrow.svg", "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"100\" height=\"250\" viewBox=\"0 0 100 250\"><path d=\"M50 0 L100 250 L0 250 Z\"/></svg>");
            string emf = Path.Combine(_work, "drawing.emf");
            File.WriteAllBytes(emf, EmfHeader(0, 0, 3000, 2000, 0, 0, 149, 99));

            GraphicEntry ePng = GraphicLibrary.Register(GraphicKind.Graphic, png);
            Check("png name from file", ePng.Name == "Agency Logo");
            Near("png aspect", ePng.Aspect, 2.0);
            Check("png pixels", ePng.PixelWidth == 600 && ePng.PixelHeight == 300);
            Check("png copied into the store", File.Exists(GraphicLibrary.FilePath(ePng)) && Path.GetDirectoryName(GraphicLibrary.FilePath(ePng)) == store);
            Check("png stored as png", ePng.FileName.EndsWith(".png"));
            Check("png preview written", File.Exists(GraphicLibrary.PreviewPath(ePng)));
            Check("png preview no larger than the cap", PngLongSide(GraphicLibrary.PreviewPath(ePng)) == GraphicImage.PreviewPixels);
            Near("new focal is the center", ePng.FocalX + ePng.FocalY, 1.0);
            Check("not built in", !ePng.IsBuiltIn);
            Check("source remembered", ePng.Source == png);
            Check("register raised Changed", changes == 1);

            GraphicEntry eJpg = GraphicLibrary.Register(GraphicKind.Graphic, jpg);
            Near("jpg aspect", eJpg.Aspect, 0.25);
            Check("jpg kept as jpg", eJpg.FileName.EndsWith(".jpg"));
            GraphicEntry eBmp = GraphicLibrary.Register(GraphicKind.Graphic, bmp);
            Near("bmp aspect", eBmp.Aspect, 1.0);
            Check("small image preview is not enlarged", PngLongSide(GraphicLibrary.PreviewPath(eBmp)) == 120);
            GraphicEntry eGif = GraphicLibrary.Register(GraphicKind.Graphic, gif);
            Near("gif aspect", eGif.Aspect, 1.5);
            GraphicEntry eTif = GraphicLibrary.Register(GraphicKind.Graphic, tif);
            Check("tiff stored as png", eTif.FileName.EndsWith(".png"));
            Check("tiff keeps its pixels", eTif.PixelWidth == 1000 && eTif.PixelHeight == 500);
            Check("tiff copy is a readable png", PngLongSide(GraphicLibrary.FilePath(eTif)) == 1000);
            GraphicEntry eSvg = GraphicLibrary.Register(GraphicKind.NorthArrow, svg);
            Near("svg aspect", eSvg.Aspect, 0.4);
            Check("svg is vector (no pixels)", eSvg.PixelWidth == 0 && eSvg.PixelHeight == 0);
            Check("svg kept as svg", eSvg.FileName.EndsWith(".svg"));
            Check("svg placeholder preview written", File.Exists(GraphicLibrary.PreviewPath(eSvg)));
            GraphicEntry eEmf = GraphicLibrary.Register(GraphicKind.NorthArrow, emf);
            Near("emf aspect", eEmf.Aspect, 1.5);

            Check("five graphics registered", GraphicLibrary.Custom(GraphicKind.Graphic).Count == 5);
            Check("two north arrows registered", GraphicLibrary.Custom(GraphicKind.NorthArrow).Count == 2);
            Check("All = built-ins then mine", GraphicLibrary.All(GraphicKind.Graphic).Count == 9
                && GraphicLibrary.All(GraphicKind.Graphic)[4].Id == ePng.Id);
            Check("galleries are separate", GraphicLibrary.Find(GraphicKind.NorthArrow, ePng.Id) == null
                && GraphicLibrary.Find(GraphicKind.Graphic, ePng.Id) != null);

            // same file twice, in the same gallery and in the other one
            GraphicEntry ePng2 = GraphicLibrary.Register(GraphicKind.Graphic, png);
            Check("second copy gets a numbered name", ePng2.Name == "Agency Logo 2", ePng2.Name);
            Check("second copy is its own file", ePng2.FileName != ePng.FileName);
            GraphicEntry ePngArrow = GraphicLibrary.Register(GraphicKind.NorthArrow, png);
            Check("other gallery keeps the plain name", ePngArrow.Name == "Agency Logo");

            // ------------------------------------------------- refused files
            Check("missing file", Throws<FileNotFoundException>(() => GraphicLibrary.Register(GraphicKind.Graphic, Path.Combine(_work, "nope.png"))));
            string txt = MakeText("notes.txt", "hello");
            Check("unsupported type", Throws<NotSupportedException>(() => GraphicLibrary.Register(GraphicKind.Graphic, txt)));
            string bad = MakeText("broken.png", "this is not a png");
            int before = Directory.GetFiles(store).Length;
            Check("unreadable image", Throws<InvalidOperationException>(() => GraphicLibrary.Register(GraphicKind.Graphic, bad)));
            Check("unreadable image leaves nothing behind", Directory.GetFiles(store).Length == before);
            string badSvg = MakeText("nosize.svg", "<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>");
            Check("svg with no size", Throws<InvalidOperationException>(() => GraphicLibrary.Register(GraphicKind.Graphic, badSvg)));
            Check("refusals do not register anything", GraphicLibrary.Custom(GraphicKind.Graphic).Count == 6);

            // --------------------------------------------------------- rename
            int c0 = changes;
            Check("rename", GraphicLibrary.Rename(GraphicKind.Graphic, ePng, "  Forest Service shield ") == "Forest Service shield" && ePng.Name == "Forest Service shield");
            Check("rename raised Changed", changes == c0 + 1);
            Check("blank name refused", GraphicLibrary.Rename(GraphicKind.Graphic, ePng, "   ") == "Forest Service shield");
            Check("clash with another image gets a number", GraphicLibrary.Rename(GraphicKind.Graphic, eJpg, "forest service SHIELD") == "forest service SHIELD 2");
            Check("clash with a built-in gets a number", GraphicLibrary.Rename(GraphicKind.Graphic, eBmp, "Compass graphic 1") == "Compass graphic 1 2");
            Check("same name again is no change", GraphicLibrary.Rename(GraphicKind.Graphic, ePng, "Forest Service shield") == "Forest Service shield");
            GraphicEntry builtIn = GraphicLibrary.BuiltIns(GraphicKind.Graphic)[0];
            Check("built-in cannot be renamed", GraphicLibrary.Rename(GraphicKind.Graphic, builtIn, "Mine") == "Compass graphic 1");
            Check("unique name helper", GraphicLibrary.UniqueName(GraphicKind.Graphic, "Forest Service shield") == "Forest Service shield 3"
                && GraphicLibrary.UniqueName(GraphicKind.Graphic, "") == "Image");

            // ---------------------------------------------------------- focal
            c0 = changes;
            GraphicLibrary.SetFocal(GraphicKind.Graphic, ePng, 0.25, 0.8);
            Near("focal x", ePng.FocalX, 0.25);
            Near("focal y", ePng.FocalY, 0.8);
            GraphicLibrary.SetFocal(GraphicKind.Graphic, eJpg, -3, 7);
            Check("focal clamped", eJpg.FocalX == 0.0 && eJpg.FocalY == 1.0);
            Check("focal does not raise Changed", changes == c0);
            GraphicLibrary.SetFocal(GraphicKind.Graphic, builtIn, 0.1, 0.1);
            Near("built-in focal untouched", GraphicLibrary.BuiltIns(GraphicKind.Graphic)[0].FocalX, 0.499987);

            // -------------------------------------------------------- preview
            string proName = eSvg.Id + "_preview_pro.png";
            string oldPreview = GraphicLibrary.PreviewPath(eSvg);
            GraphicImage.WritePlaceholderPreview(Path.Combine(store, proName), 0.4, "X");
            c0 = changes;
            GraphicLibrary.SetPreview(GraphicKind.NorthArrow, eSvg, proName);
            Check("preview replaced", eSvg.Preview == proName && File.Exists(GraphicLibrary.PreviewPath(eSvg)));
            Check("old preview deleted", !File.Exists(oldPreview));
            Check("preview raised Changed", changes == c0 + 1);
            Check("preview loads frozen", GraphicLibrary.LoadPreview(eSvg, 64) is BitmapSource bs && bs.IsFrozen && bs.PixelHeight == 64);
            Check("missing preview loads as null", GraphicLibrary.LoadPreview(new GraphicEntry { Id = "x", Preview = "gone.png" }, 64) == null);

            // -------------------------------------------------------- current
            GraphicLibrary.SetCurrent(GraphicKind.Graphic, ePng.Id);
            GraphicLibrary.SetCurrent(GraphicKind.NorthArrow, eSvg.Id);
            Check("current graphic", GraphicLibrary.Current(GraphicKind.Graphic).Id == ePng.Id);

            // ----------------------------------------- reload from the disk
            GraphicLibrary.UseStoreFolder(store);
            var again = GraphicLibrary.Custom(GraphicKind.Graphic);
            Check("reload: six graphics", again.Count == 6);
            GraphicEntry rPng = again.First(e => e.Id == ePng.Id);
            Check("reload: name", rPng.Name == "Forest Service shield");
            Near("reload: focal x", rPng.FocalX, 0.25);
            Near("reload: focal y", rPng.FocalY, 0.8);
            Near("reload: aspect", rPng.Aspect, 2.0);
            Check("reload: pixels", rPng.PixelWidth == 600);
            Check("reload: not built in", !rPng.IsBuiltIn);
            Check("reload: current graphic kept", GraphicLibrary.Current(GraphicKind.Graphic).Id == ePng.Id);
            Check("reload: current arrow kept", GraphicLibrary.Current(GraphicKind.NorthArrow).Id == eSvg.Id);
            Check("reload: svg preview name", GraphicLibrary.Custom(GraphicKind.NorthArrow).First(e => e.Id == eSvg.Id).Preview == proName);

            // --------------------------------------------------------- remove
            string pngFile = GraphicLibrary.FilePath(rPng), pngPrev = GraphicLibrary.PreviewPath(rPng);
            c0 = changes;
            Check("remove", GraphicLibrary.Remove(GraphicKind.Graphic, rPng));
            Check("remove deletes the copy and the preview", !File.Exists(pngFile) && !File.Exists(pngPrev));
            Check("remove raised Changed", changes == c0 + 1);
            Check("removed image is gone", GraphicLibrary.Find(GraphicKind.Graphic, ePng.Id) == null && GraphicLibrary.Custom(GraphicKind.Graphic).Count == 5);
            Check("removing the current choice falls back to the default", GraphicLibrary.Current(GraphicKind.Graphic).Id == bg[1].Id);
            Check("remove twice is refused", !GraphicLibrary.Remove(GraphicKind.Graphic, rPng));
            Check("built-in cannot be removed", !GraphicLibrary.Remove(GraphicKind.Graphic, builtIn));
            Check("original file untouched", File.Exists(png));
            Check("wrong gallery is refused", !GraphicLibrary.Remove(GraphicKind.Graphic, eSvg) && GraphicLibrary.Custom(GraphicKind.NorthArrow).Count == 3);

            // ------------------------------------ a damaged or foreign list
            string json = Path.Combine(store, "custom_graphics.json");
            File.WriteAllText(json, "{ \"format\": \"JennessCustomGraphics\", \"version\": 1, \"graphics\": ["
                + "{\"id\":\"a\",\"name\":\"  ok  \",\"file\":\"a.png\",\"focalX\":9,\"focalY\":-1,\"aspect\":0},"
                + "{\"id\":\"a\",\"name\":\"duplicate id\",\"file\":\"b.png\"},"
                + "{\"id\":\"\",\"name\":\"no id\",\"file\":\"c.png\"},"
                + "{\"id\":\"d\",\"name\":\"path in file\",\"file\":\"..\\\\..\\\\evil.png\"},"
                + "{\"id\":\"builtin:CompassRose\",\"name\":\"imposter\",\"file\":\"e.png\"},"
                + "null ], }");
            GraphicLibrary.UseStoreFolder(store);
            var cleaned = GraphicLibrary.Custom(GraphicKind.Graphic);
            Check("damaged list: one good entry kept", cleaned.Count == 1 && cleaned[0].Id == "a", cleaned.Count.ToString());
            Check("damaged list: values repaired", cleaned.Count == 1 && cleaned[0].Name == "ok" && cleaned[0].FocalX == 1.0 && cleaned[0].FocalY == 0.0 && cleaned[0].Aspect == 1.0);
            Check("damaged list: missing section is empty", GraphicLibrary.Custom(GraphicKind.NorthArrow).Count == 0);
            File.WriteAllText(json, "{ not json at all");
            GraphicLibrary.UseStoreFolder(store);
            Check("unreadable list is treated as empty", GraphicLibrary.Custom(GraphicKind.Graphic).Count == 0 && GraphicLibrary.All(GraphicKind.Graphic).Count == 4);
            File.WriteAllText(json, "{ \"format\": \"SomethingElse\", \"graphics\": [{\"id\":\"z\",\"name\":\"z\",\"file\":\"z.png\"}] }");
            GraphicLibrary.UseStoreFolder(store);
            Check("foreign file is ignored", GraphicLibrary.Custom(GraphicKind.Graphic).Count == 0);

            // ----------------------------------------------------- summaries
            Check("raster summary", GraphicImage.FileSummary(eJpg).Contains("photo.jpg") && GraphicImage.FileSummary(eJpg).Contains("JPG image") && GraphicImage.FileSummary(eJpg).Contains("200"));
            Check("vector summary", GraphicImage.FileSummary(eSvg).Contains("SVG vector drawing"));
            Check("built-in summary", GraphicImage.FileSummary(builtIn).StartsWith("Built in"));
            Check("supported types", GraphicImage.IsSupported(".PNG") && GraphicImage.IsSupported(".tiff") && GraphicImage.IsSupported(".emf") && !GraphicImage.IsSupported(".pdf") && !GraphicImage.IsSupported(""));
            Check("vector types", GraphicImage.IsVector(".SVG") && GraphicImage.IsVector(".emf") && !GraphicImage.IsVector(".png"));
        }
        finally
        {
            try { Directory.Delete(_work, true); } catch { }
        }

        Console.WriteLine();
        Console.WriteLine("GraphicLibrary: " + _pass + " passed, " + _fail + " failed");
        return _fail == 0 ? 0 : 1;
    }
}
