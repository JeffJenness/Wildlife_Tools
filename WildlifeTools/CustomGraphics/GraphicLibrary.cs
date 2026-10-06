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

// NOTE: nothing in this file may use an ArcGIS type. It is compiled as it
// stands into the headless test claude_tests/GraphicLibraryTest, so what
// passes there is what ships. Anything that needs ArcGIS Pro (drawing a
// vector image, placing an element) lives in the other files of this folder.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Xml;

namespace WildlifeTools.CustomGraphics
{
    /// <summary>The two galleries an image can be registered in.</summary>
    public enum GraphicKind
    {
        Graphic = 0,
        NorthArrow = 1,
    }

    /// <summary>
    /// One artwork the Add Graphics or North Arrows gallery offers: either one
    /// of the add-in's own compass designs or an image the user registered.
    /// The focal point is the spot that counts as the artwork's center (the
    /// hub of a compass), as fractions of its box: X from the left edge, Y
    /// from the BOTTOM edge. It is stamped on every element placed from the
    /// entry, where the Circle Background tool reads it (the one tool that
    /// uses it: its circle can be centered there).
    /// </summary>
    public sealed class GraphicEntry
    {
        [JsonPropertyName("id")] public string Id { get; set; } = "";
        [JsonPropertyName("name")] public string Name { get; set; } = "";

        /// <summary>The image file: a name inside the store folder for a
        /// registered image, the deployed SVG's name for a built-in one.</summary>
        [JsonPropertyName("file")] public string FileName { get; set; } = "";

        /// <summary>The PNG picture of the whole image canvas, used for the
        /// gallery thumbnail and the Manage window's preview: a name inside
        /// the store folder (registered) or the built-in's key.</summary>
        [JsonPropertyName("preview")] public string Preview { get; set; } = "";

        [JsonPropertyName("focalX")] public double FocalX { get; set; } = 0.5;
        [JsonPropertyName("focalY")] public double FocalY { get; set; } = 0.5;

        /// <summary>Width / height of the image canvas.</summary>
        [JsonPropertyName("aspect")] public double Aspect { get; set; } = 1.0;

        /// <summary>Pixel size of a raster image; 0 for a vector image.</summary>
        [JsonPropertyName("pixelWidth")] public int PixelWidth { get; set; }
        [JsonPropertyName("pixelHeight")] public int PixelHeight { get; set; }

        /// <summary>Where the image was registered from. Information only:
        /// the store keeps its own copy.</summary>
        [JsonPropertyName("source")] public string Source { get; set; } = "";

        [JsonIgnore] public bool IsBuiltIn { get; set; }

        /// <summary>One line describing a built-in design.</summary>
        [JsonIgnore] public string Description { get; set; } = "";
    }

    /// <summary>The JSON file of registered images.</summary>
    public sealed class GraphicStoreFile
    {
        [JsonPropertyName("format")] public string Format { get; set; } = GraphicLibrary.FormatTag;
        [JsonPropertyName("version")] public int Version { get; set; } = 1;
        [JsonPropertyName("graphics")] public List<GraphicEntry> Graphics { get; set; } = new List<GraphicEntry>();
        [JsonPropertyName("northArrows")] public List<GraphicEntry> NorthArrows { get; set; } = new List<GraphicEntry>();

        /// <summary>The entry each gallery placed last, so the ribbon button
        /// (the top half of the split button) repeats it in the next session.</summary>
        [JsonPropertyName("lastGraphic")] public string LastGraphic { get; set; } = "";
        [JsonPropertyName("lastNorthArrow")] public string LastNorthArrow { get; set; } = "";
    }

    /// <summary>
    /// The artwork behind the Add Graphics and North Arrows galleries: the
    /// add-in's four compass designs plus the images the user has registered.
    /// Registered images are COPIED into a per-user folder
    /// (LocalAppData\JennessEnterprises\WildlifeTools\CustomGraphics), with a
    /// JSON list beside them, so they are there in every project and survive
    /// the original file being moved or deleted.
    /// </summary>
    public static class GraphicLibrary
    {
        internal const string FormatTag = "JennessCustomGraphics";

        /// <summary>Gallery section holding the built-in designs.</summary>
        public const string BuiltInGroup = "Jenness Enterprises";

        /// <summary>Gallery section holding the user's registered images.</summary>
        public static string CustomGroup(GraphicKind kind) =>
            kind == GraphicKind.NorthArrow ? "My North Arrows" : "My Graphics";

        private const string BuiltInPrefix = "builtin:";
        private const string DefaultBuiltIn = BuiltInPrefix + "CompassRoseDot";

        private static readonly object Gate = new object();
        private static GraphicStoreFile _store;
        private static string _folderOverride;
        private static long _stamp;

        /// <summary>Raised after an image is registered, removed or renamed, or
        /// its preview is redrawn: whatever shows the list should rebuild it.
        /// May be raised on any thread.</summary>
        public static event EventHandler Changed;

        /// <summary>Counts the changes that raise <see cref="Changed"/>, for a
        /// listener that would rather compare than subscribe.</summary>
        public static long Stamp => Interlocked.Read(ref _stamp);

        // ------------------------------------------------------------------
        //  The built-in designs. The focal and aspect numbers are printed by
        //  DevScripts\fc_to_svg.py and compass2d_to_svg.py whenever they
        //  regenerate the SVGs: KEEP THEM IN SYNC with that output. (Rerun
        //  DevScripts\make_compass_previews.ps1 as well.)
        // ------------------------------------------------------------------
        private sealed class BuiltIn
        {
            public string Key, File, GraphicName, ArrowName, Description;
            public double Fx, Fy, Aspect;
        }

        private static readonly BuiltIn[] BuiltInArt =
        {
            new BuiltIn
            {
                Key = "CompassRose", File = "CompassRose.svg",
                GraphicName = "Compass graphic 1", ArrowName = "North arrow 1",
                Fx = 0.499987, Fy = 0.499987, Aspect = 1.0,
                Description = "The Jennessent Compass Rose.",
            },
            new BuiltIn
            {
                // taller canvas: the hub sits below the middle
                Key = "CompassRoseDot", File = "CompassRoseDot.svg",
                GraphicName = "Compass graphic 1 - dot", ArrowName = "North arrow 1 - dot",
                Fx = 0.499987, Fy = 0.448416, Aspect = 0.896856,
                Description = "The Jennessent Compass Rose with the north dot.",
            },
            new BuiltIn
            {
                Key = "Compass2", File = "Compass2.svg",
                GraphicName = "Compass graphic 2", ArrowName = "North arrow 2",
                Fx = 0.5, Fy = 0.5, Aspect = 1.0,
                Description = "The Compass_2d design: black cardinal star, gray "
                    + "intercardinal star, twin rings.",
            },
            new BuiltIn
            {
                Key = "Compass2Dot", File = "Compass2Dot.svg",
                GraphicName = "Compass graphic 2 - dot", ArrowName = "North arrow 2 - dot",
                Fx = 0.5, Fy = 0.431026, Aspect = 0.862053,
                Description = "The Compass_2d design with the gradient spearhead "
                    + "above the north point.",
            },
        };

        /// <summary>The add-in's own designs, as they are named in the given
        /// gallery. Fresh objects each call; changing one changes nothing.</summary>
        public static List<GraphicEntry> BuiltIns(GraphicKind kind)
        {
            var list = new List<GraphicEntry>();
            foreach (BuiltIn b in BuiltInArt)
            {
                list.Add(new GraphicEntry
                {
                    Id = BuiltInPrefix + b.Key,
                    Name = kind == GraphicKind.NorthArrow ? b.ArrowName : b.GraphicName,
                    FileName = b.File,
                    Preview = b.Key,
                    FocalX = b.Fx,
                    FocalY = b.Fy,
                    Aspect = b.Aspect,
                    IsBuiltIn = true,
                    Description = b.Description,
                });
            }
            return list;
        }

        /// <summary>The user's registered images of one kind, in the order
        /// they were registered. These are the live entries.</summary>
        public static List<GraphicEntry> Custom(GraphicKind kind)
        {
            lock (Gate)
            {
                return new List<GraphicEntry>(ListFor(Store(), kind));
            }
        }

        /// <summary>Built-in designs first, then the user's images.</summary>
        public static List<GraphicEntry> All(GraphicKind kind)
        {
            List<GraphicEntry> all = BuiltIns(kind);
            all.AddRange(Custom(kind));
            return all;
        }

        public static GraphicEntry Find(GraphicKind kind, string id)
        {
            if (string.IsNullOrEmpty(id))
            {
                return null;
            }
            return All(kind).FirstOrDefault(e => e.Id == id);
        }

        /// <summary>The entry the gallery placed last (kept between sessions),
        /// or the default built-in design if there is none or it has since
        /// been removed. Never null.</summary>
        public static GraphicEntry Current(GraphicKind kind)
        {
            string id;
            lock (Gate)
            {
                GraphicStoreFile s = Store();
                id = kind == GraphicKind.NorthArrow ? s.LastNorthArrow : s.LastGraphic;
            }
            return Find(kind, id) ?? Find(kind, DefaultBuiltIn) ?? BuiltIns(kind)[0];
        }

        /// <summary>Remembers which entry a gallery placed last.</summary>
        public static void SetCurrent(GraphicKind kind, string id)
        {
            lock (Gate)
            {
                GraphicStoreFile s = Store();
                string was = kind == GraphicKind.NorthArrow ? s.LastNorthArrow : s.LastGraphic;
                if (was == id)
                {
                    return;
                }
                if (kind == GraphicKind.NorthArrow)
                {
                    s.LastNorthArrow = id ?? "";
                }
                else
                {
                    s.LastGraphic = id ?? "";
                }
                TrySave();
            }
        }

        // ------------------------------------------------------------------
        //  The store on disk
        // ------------------------------------------------------------------

        /// <summary>Points the library at another folder and forgets what was
        /// loaded. For the headless test only.</summary>
        internal static void UseStoreFolder(string folder)
        {
            lock (Gate)
            {
                _folderOverride = folder;
                _store = null;
            }
        }

        /// <summary>The per-user folder holding the registered images, their
        /// previews and the JSON list. Created on first use.</summary>
        public static string StoreFolder
        {
            get
            {
                string dir = _folderOverride;
                if (string.IsNullOrEmpty(dir))
                {
                    string b = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                    if (string.IsNullOrEmpty(b))
                    {
                        b = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
                    }
                    dir = Path.Combine(b, "JennessEnterprises", "WildlifeTools", "CustomGraphics");
                }
                Directory.CreateDirectory(dir);
                return dir;
            }
        }

        private static string JsonPath => Path.Combine(StoreFolder, "custom_graphics.json");

        /// <summary>Full path of a registered image's file. (A built-in
        /// design's file is deployed with the add-in instead; the placement
        /// code resolves that one.)</summary>
        public static string FilePath(GraphicEntry entry) =>
            Path.Combine(StoreFolder, entry.FileName ?? "");

        /// <summary>Full path of a registered image's preview PNG.</summary>
        public static string PreviewPath(GraphicEntry entry) =>
            Path.Combine(StoreFolder, entry.Preview ?? "");

        // callers hold Gate
        private static GraphicStoreFile Store()
        {
            if (_store != null)
            {
                return _store;
            }
            GraphicStoreFile loaded = null;
            try
            {
                string p = JsonPath;
                if (File.Exists(p))
                {
                    loaded = JsonSerializer.Deserialize<GraphicStoreFile>(
                        File.ReadAllText(p), new JsonSerializerOptions
                        {
                            ReadCommentHandling = JsonCommentHandling.Skip,
                            AllowTrailingCommas = true,
                        });
                    if (loaded != null && loaded.Format != FormatTag)
                    {
                        loaded = null;
                    }
                }
            }
            catch
            {
                loaded = null;      // an unreadable list is treated as empty
            }
            loaded ??= new GraphicStoreFile();
            loaded.Graphics = Clean(loaded.Graphics);
            loaded.NorthArrows = Clean(loaded.NorthArrows);
            _store = loaded;
            return _store;
        }

        /// <summary>Drops entries a hand-edited or damaged file could carry
        /// that would break the lists: no id, no file, a clashing id.</summary>
        private static List<GraphicEntry> Clean(List<GraphicEntry> list)
        {
            var good = new List<GraphicEntry>();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (GraphicEntry e in list ?? new List<GraphicEntry>())
            {
                if (e == null || string.IsNullOrWhiteSpace(e.Id)
                    || e.Id.StartsWith(BuiltInPrefix, StringComparison.Ordinal)
                    || string.IsNullOrWhiteSpace(e.FileName)
                    || e.FileName != Path.GetFileName(e.FileName)
                    || !seen.Add(e.Id))
                {
                    continue;
                }
                e.IsBuiltIn = false;
                e.Name = string.IsNullOrWhiteSpace(e.Name) ? "Image" : e.Name.Trim();
                e.FocalX = Clamp01(e.FocalX);
                e.FocalY = Clamp01(e.FocalY);
                if (!(e.Aspect > 0) || double.IsInfinity(e.Aspect))
                {
                    e.Aspect = 1.0;
                }
                good.Add(e);
            }
            return good;
        }

        private static List<GraphicEntry> ListFor(GraphicStoreFile s, GraphicKind kind) =>
            kind == GraphicKind.NorthArrow ? s.NorthArrows : s.Graphics;

        // callers hold Gate; throws if the file cannot be written
        private static void Save()
        {
            string json = JsonSerializer.Serialize(Store(),
                new JsonSerializerOptions { WriteIndented = true });
            string path = JsonPath;
            string tmp = path + ".tmp";
            File.WriteAllText(tmp, json);
            File.Move(tmp, path, true);
        }

        private static bool TrySave()
        {
            try
            {
                Save();
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static void RaiseChanged()
        {
            Interlocked.Increment(ref _stamp);
            try
            {
                Changed?.Invoke(null, EventArgs.Empty);
            }
            catch
            {
                // a listener's trouble is not the library's
            }
        }

        // ------------------------------------------------------------------
        //  Registering, renaming, removing
        // ------------------------------------------------------------------

        /// <summary>
        /// Registers an image file in one gallery: copies it into the store
        /// (a TIFF is converted to PNG, which every placement path reads),
        /// measures it, draws its preview and adds it to the list, named after
        /// the file. A vector image (SVG, EMF) gets a placeholder preview
        /// here, because only ArcGIS Pro can draw it; the caller replaces it
        /// through <see cref="SetPreview"/>. Call on the UI thread. Throws
        /// with a message fit to show if the file cannot be used.
        /// </summary>
        public static GraphicEntry Register(GraphicKind kind, string sourcePath)
        {
            if (string.IsNullOrWhiteSpace(sourcePath) || !File.Exists(sourcePath))
            {
                throw new FileNotFoundException("The image file was not found: " + sourcePath);
            }
            string ext = Path.GetExtension(sourcePath).ToLowerInvariant();
            if (!GraphicImage.IsSupported(ext))
            {
                throw new NotSupportedException(
                    Path.GetFileName(sourcePath) + " is not an image type these tools can use. "
                    + "Use one of: " + GraphicImage.SupportedList + ".");
            }

            string folder = StoreFolder;
            string id = Guid.NewGuid().ToString("N");
            string storedName = id + (GraphicImage.NeedsPngConversion(ext) ? ".png" : ext);
            string storedPath = Path.Combine(folder, storedName);
            string previewName = id + "_preview.png";
            string previewPath = Path.Combine(folder, previewName);
            try
            {
                if (GraphicImage.NeedsPngConversion(ext))
                {
                    GraphicImage.ConvertToPng(sourcePath, storedPath);
                }
                else
                {
                    File.Copy(sourcePath, storedPath, true);
                    // a read-only original must not leave a copy that cannot be removed
                    File.SetAttributes(storedPath, FileAttributes.Normal);
                }

                GraphicImage.Info info = GraphicImage.Describe(storedPath);
                if (info.IsVector)
                {
                    GraphicImage.WritePlaceholderPreview(previewPath, info.Aspect,
                        ext.TrimStart('.').ToUpperInvariant());
                }
                else
                {
                    GraphicImage.WriteRasterPreview(storedPath, previewPath, GraphicImage.PreviewPixels);
                }

                var entry = new GraphicEntry
                {
                    Id = id,
                    FileName = storedName,
                    Preview = previewName,
                    FocalX = 0.5,
                    FocalY = 0.5,
                    Aspect = info.Aspect,
                    PixelWidth = info.PixelWidth,
                    PixelHeight = info.PixelHeight,
                    Source = sourcePath,
                };
                lock (Gate)
                {
                    entry.Name = UniqueNameLocked(kind, Path.GetFileNameWithoutExtension(sourcePath), null);
                    ListFor(Store(), kind).Add(entry);
                    try
                    {
                        Save();
                    }
                    catch
                    {
                        ListFor(Store(), kind).Remove(entry);
                        throw;
                    }
                }
                RaiseChanged();
                return entry;
            }
            catch (Exception ex)
            {
                TryDelete(storedPath);
                TryDelete(previewPath);
                throw new InvalidOperationException(
                    Path.GetFileName(sourcePath) + " could not be registered: " + ex.Message, ex);
            }
        }

        /// <summary>Takes a registered image out of its gallery and deletes
        /// the store's copy of it. Elements already placed from it are not
        /// touched: each carries its own copy of the picture.</summary>
        public static bool Remove(GraphicKind kind, GraphicEntry entry)
        {
            if (entry == null || entry.IsBuiltIn)
            {
                return false;
            }
            lock (Gate)
            {
                GraphicStoreFile s = Store();
                List<GraphicEntry> list = ListFor(s, kind);
                GraphicEntry live = list.FirstOrDefault(e => e.Id == entry.Id);
                if (live == null)
                {
                    return false;
                }
                list.Remove(live);
                if (s.LastGraphic == live.Id)
                {
                    s.LastGraphic = "";
                }
                if (s.LastNorthArrow == live.Id)
                {
                    s.LastNorthArrow = "";
                }
                TrySave();
                TryDelete(Path.Combine(StoreFolder, live.FileName ?? ""));
                TryDelete(Path.Combine(StoreFolder, live.Preview ?? ""));
            }
            RaiseChanged();
            return true;
        }

        /// <summary>Renames a registered image. The name is what its gallery
        /// thumbnail says and what elements placed from it are called.
        /// Returns the name actually given: a blank name is refused (the old
        /// name is returned) and a name already in that gallery gets a number
        /// added.</summary>
        public static string Rename(GraphicKind kind, GraphicEntry entry, string newName)
        {
            if (entry == null || entry.IsBuiltIn)
            {
                return entry?.Name;
            }
            string wanted = (newName ?? "").Trim();
            if (wanted.Length == 0 || wanted == entry.Name)
            {
                return entry.Name;
            }
            lock (Gate)
            {
                GraphicEntry live = ListFor(Store(), kind).FirstOrDefault(e => e.Id == entry.Id);
                if (live == null)
                {
                    return entry.Name;
                }
                live.Name = UniqueNameLocked(kind, wanted, live.Id);
                entry.Name = live.Name;
                TrySave();
            }
            RaiseChanged();
            return entry.Name;
        }

        /// <summary>Sets a registered image's focal point (fractions of its
        /// box, X from the left, Y from the bottom; clamped to 0..1). The
        /// galleries do not show focal points, so this does not raise
        /// <see cref="Changed"/>.</summary>
        public static void SetFocal(GraphicKind kind, GraphicEntry entry, double fx, double fy)
        {
            if (entry == null || entry.IsBuiltIn)
            {
                return;
            }
            fx = Clamp01(fx);
            fy = Clamp01(fy);
            lock (Gate)
            {
                GraphicEntry live = ListFor(Store(), kind).FirstOrDefault(e => e.Id == entry.Id);
                if (live == null)
                {
                    return;
                }
                live.FocalX = fx;
                live.FocalY = fy;
                entry.FocalX = fx;
                entry.FocalY = fy;
                TrySave();
            }
        }

        /// <summary>Records that a registered image's preview has been
        /// redrawn into the named PNG in the store folder (ArcGIS Pro's
        /// drawing of a vector image replacing the placeholder).</summary>
        public static void SetPreview(GraphicKind kind, GraphicEntry entry, string previewFileName)
        {
            if (entry == null || entry.IsBuiltIn || string.IsNullOrEmpty(previewFileName))
            {
                return;
            }
            lock (Gate)
            {
                GraphicEntry live = ListFor(Store(), kind).FirstOrDefault(e => e.Id == entry.Id);
                if (live == null)
                {
                    return;
                }
                string old = live.Preview;
                live.Preview = previewFileName;
                entry.Preview = previewFileName;
                TrySave();
                if (!string.Equals(old, previewFileName, StringComparison.OrdinalIgnoreCase))
                {
                    TryDelete(Path.Combine(StoreFolder, old ?? ""));
                }
            }
            RaiseChanged();
        }

        /// <summary>A name not yet used in the gallery: the wanted name, or
        /// that name with " 2", " 3", ... added.</summary>
        public static string UniqueName(GraphicKind kind, string wanted)
        {
            lock (Gate)
            {
                return UniqueNameLocked(kind, wanted, null);
            }
        }

        private static string UniqueNameLocked(GraphicKind kind, string wanted, string exceptId)
        {
            string baseName = string.IsNullOrWhiteSpace(wanted) ? "Image" : wanted.Trim();
            var taken = new HashSet<string>(StringComparer.CurrentCultureIgnoreCase);
            foreach (GraphicEntry e in BuiltIns(kind))
            {
                taken.Add(e.Name);
            }
            foreach (GraphicEntry e in ListFor(Store(), kind))
            {
                if (e.Id != exceptId)
                {
                    taken.Add(e.Name);
                }
            }
            if (!taken.Contains(baseName))
            {
                return baseName;
            }
            for (int n = 2; ; n++)
            {
                string candidate = baseName + " " + n.ToString(CultureInfo.InvariantCulture);
                if (!taken.Contains(candidate))
                {
                    return candidate;
                }
            }
        }

        // ------------------------------------------------------------------
        //  Pictures of the entries
        // ------------------------------------------------------------------

        /// <summary>The entry's preview picture, decoded no taller than
        /// <paramref name="decodeHeight"/> pixels (0 = full size), frozen so
        /// any thread may use it; null if it cannot be read. Call on the UI
        /// thread.</summary>
        public static ImageSource LoadPreview(GraphicEntry entry, int decodeHeight)
        {
            if (entry == null)
            {
                return null;
            }
            try
            {
                if (entry.IsBuiltIn)
                {
                    var bi = new BitmapImage();
                    bi.BeginInit();
                    bi.CacheOption = BitmapCacheOption.OnLoad;
                    if (decodeHeight > 0)
                    {
                        bi.DecodePixelHeight = decodeHeight;
                    }
                    bi.UriSource = new Uri(
                        "pack://application:,,,/WildlifeTools;component/Cartography/Previews/"
                        + entry.Preview + ".png");
                    bi.EndInit();
                    bi.Freeze();
                    return bi;
                }
                string path = PreviewPath(entry);
                return File.Exists(path) ? GraphicImage.Load(path, decodeHeight) : null;
            }
            catch
            {
                return null;
            }
        }

        internal static double Clamp01(double v)
        {
            if (double.IsNaN(v))
            {
                return 0.5;
            }
            return v < 0.0 ? 0.0 : v > 1.0 ? 1.0 : v;
        }

        private static void TryDelete(string path)
        {
            try
            {
                if (!string.IsNullOrEmpty(path) && File.Exists(path))
                {
                    File.Delete(path);
                }
            }
            catch
            {
                // an orphaned file in the store folder harms nothing
            }
        }
    }

    /// <summary>
    /// What the library needs to know about an image file without ArcGIS Pro:
    /// which types can be registered, the shape of the canvas, and a preview
    /// picture of a raster image.
    /// </summary>
    public static class GraphicImage
    {
        /// <summary>Longest side of a stored preview, in pixels.</summary>
        public const int PreviewPixels = 384;

        private static readonly string[] Raster = { ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff" };
        private static readonly string[] Vector = { ".svg", ".emf" };

        public const string SupportedList = "PNG, JPEG, BMP, GIF, TIFF, SVG or EMF";

        /// <summary>Filter for the file dialog that picks images to register.</summary>
        public const string DialogFilter =
            "Images (*.png;*.jpg;*.jpeg;*.bmp;*.gif;*.tif;*.tiff;*.svg;*.emf)"
            + "|*.png;*.jpg;*.jpeg;*.bmp;*.gif;*.tif;*.tiff;*.svg;*.emf"
            + "|All files (*.*)|*.*";

        public static bool IsVector(string ext) =>
            Array.IndexOf(Vector, (ext ?? "").ToLowerInvariant()) >= 0;

        public static bool IsSupported(string ext)
        {
            string e = (ext ?? "").ToLowerInvariant();
            return Array.IndexOf(Raster, e) >= 0 || Array.IndexOf(Vector, e) >= 0;
        }

        /// <summary>ArcGIS Pro builds a picture marker (the heart of a north
        /// arrow) from JPG, BMP, PNG, GIF, EMF and SVG, but not from TIFF, so
        /// a TIFF is stored as a PNG.</summary>
        public static bool NeedsPngConversion(string ext)
        {
            string e = (ext ?? "").ToLowerInvariant();
            return e == ".tif" || e == ".tiff";
        }

        public struct Info
        {
            public double Aspect;
            public int PixelWidth;
            public int PixelHeight;
            public bool IsVector;
        }

        /// <summary>Measures an image file. Throws if it is not a readable
        /// image of a supported type.</summary>
        public static Info Describe(string path)
        {
            string ext = Path.GetExtension(path).ToLowerInvariant();
            if (ext == ".svg")
            {
                if (!TrySvgAspect(File.ReadAllText(path), out double a))
                {
                    throw new InvalidDataException(
                        "the SVG file does not say how large its drawing is (no usable width and height, and no viewBox)");
                }
                return new Info { Aspect = a, IsVector = true };
            }
            if (ext == ".emf")
            {
                byte[] head = new byte[44];
                int n;
                using (FileStream fs = File.OpenRead(path))
                {
                    n = fs.Read(head, 0, head.Length);
                }
                if (n < head.Length || !TryEmfAspect(head, out double a))
                {
                    throw new InvalidDataException("the file is not a readable EMF picture");
                }
                return new Info { Aspect = a, IsVector = true };
            }

            int w, h;
            try
            {
                using (FileStream fs = File.OpenRead(path))
                {
                    BitmapDecoder dec = BitmapDecoder.Create(fs,
                        BitmapCreateOptions.DelayCreation | BitmapCreateOptions.IgnoreColorProfile,
                        BitmapCacheOption.None);
                    BitmapFrame frame = dec.Frames[0];
                    w = frame.PixelWidth;
                    h = frame.PixelHeight;
                }
            }
            catch (Exception ex) when (!(ex is IOException) && !(ex is UnauthorizedAccessException))
            {
                // WPF's own words here ("No imaging component suitable...") help nobody.
                throw new InvalidDataException(
                    "the file is not a readable " + ext.TrimStart('.').ToUpperInvariant() + " image");
            }
            if (w <= 0 || h <= 0)
            {
                throw new InvalidDataException("the image has no pixels");
            }
            return new Info { Aspect = (double)w / h, PixelWidth = w, PixelHeight = h };
        }

        /// <summary>
        /// Width / height of an SVG drawing's canvas: its width and height
        /// when both are absolute lengths (that is the size the picture has),
        /// otherwise the shape of its viewBox.
        /// </summary>
        public static bool TrySvgAspect(string svgText, out double aspect)
        {
            aspect = 1.0;
            if (string.IsNullOrWhiteSpace(svgText))
            {
                return false;
            }
            string width = null, height = null, viewBox = null;
            try
            {
                var settings = new XmlReaderSettings
                {
                    DtdProcessing = DtdProcessing.Ignore,
                    XmlResolver = null,
                    IgnoreComments = true,
                    IgnoreProcessingInstructions = true,
                    IgnoreWhitespace = true,
                };
                using (var sr = new StringReader(svgText))
                using (XmlReader xr = XmlReader.Create(sr, settings))
                {
                    while (xr.Read())
                    {
                        if (xr.NodeType != XmlNodeType.Element)
                        {
                            continue;
                        }
                        if (!string.Equals(xr.LocalName, "svg", StringComparison.OrdinalIgnoreCase))
                        {
                            return false;       // the root element is not <svg>
                        }
                        width = xr.GetAttribute("width");
                        height = xr.GetAttribute("height");
                        viewBox = xr.GetAttribute("viewBox");
                        break;
                    }
                }
            }
            catch
            {
                return false;
            }

            if (TryLength(width, out double w) && TryLength(height, out double h) && w > 0 && h > 0)
            {
                aspect = w / h;
                return true;
            }
            if (!string.IsNullOrWhiteSpace(viewBox))
            {
                string[] parts = viewBox.Replace(',', ' ')
                    .Split((char[])null, StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length == 4
                    && double.TryParse(parts[2], NumberStyles.Float, CultureInfo.InvariantCulture, out double vw)
                    && double.TryParse(parts[3], NumberStyles.Float, CultureInfo.InvariantCulture, out double vh)
                    && vw > 0 && vh > 0)
                {
                    aspect = vw / vh;
                    return true;
                }
            }
            return false;
        }

        /// <summary>An SVG length in CSS pixels; false for a missing, relative
        /// (%, em, ex) or unreadable one.</summary>
        private static bool TryLength(string text, out double px)
        {
            px = 0.0;
            if (string.IsNullOrWhiteSpace(text))
            {
                return false;
            }
            Match m = Regex.Match(text.Trim(), @"^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-zA-Z%]*)$");
            if (!m.Success
                || !double.TryParse(m.Groups[1].Value, NumberStyles.Float, CultureInfo.InvariantCulture, out double v))
            {
                return false;
            }
            double perUnit;
            switch (m.Groups[2].Value.ToLowerInvariant())
            {
                case "":
                case "px": perUnit = 1.0; break;
                case "pt": perUnit = 96.0 / 72.0; break;
                case "pc": perUnit = 16.0; break;
                case "in": perUnit = 96.0; break;
                case "cm": perUnit = 96.0 / 2.54; break;
                case "mm": perUnit = 96.0 / 25.4; break;
                default: return false;
            }
            px = v * perUnit;
            return true;
        }

        /// <summary>
        /// Width / height of an EMF picture from the first 44 bytes of its
        /// header: the frame rectangle (hundredths of a millimeter), or the
        /// bounds rectangle (device units) if the frame is empty.
        /// </summary>
        public static bool TryEmfAspect(byte[] header, out double aspect)
        {
            aspect = 1.0;
            if (header == null || header.Length < 44
                || BitConverter.ToUInt32(header, 0) != 1u               // EMR_HEADER
                || BitConverter.ToUInt32(header, 40) != 0x464D4520u)    // " EMF"
            {
                return false;
            }
            double fw = BitConverter.ToInt32(header, 32) - (double)BitConverter.ToInt32(header, 24);
            double fh = BitConverter.ToInt32(header, 36) - (double)BitConverter.ToInt32(header, 28);
            if (fw > 0 && fh > 0)
            {
                aspect = fw / fh;
                return true;
            }
            double bw = BitConverter.ToInt32(header, 16) - (double)BitConverter.ToInt32(header, 8) + 1;
            double bh = BitConverter.ToInt32(header, 20) - (double)BitConverter.ToInt32(header, 12) + 1;
            if (bw > 0 && bh > 0)
            {
                aspect = bw / bh;
                return true;
            }
            return false;
        }

        /// <summary>Rewrites a raster image (a TIFF) as a PNG, first frame,
        /// full size.</summary>
        public static void ConvertToPng(string sourcePath, string pngPath)
        {
            BitmapSource bmp;
            using (FileStream fs = File.OpenRead(sourcePath))
            {
                BitmapDecoder dec = BitmapDecoder.Create(fs,
                    BitmapCreateOptions.PreservePixelFormat, BitmapCacheOption.OnLoad);
                bmp = dec.Frames[0];
                bmp.Freeze();
            }
            SavePng(bmp, pngPath);
        }

        /// <summary>Writes a raster image's preview: the whole image, scaled
        /// so its longest side is at most <paramref name="maxPixels"/>.</summary>
        public static void WriteRasterPreview(string imagePath, string pngPath, int maxPixels)
        {
            BitmapSource bmp;
            using (FileStream fs = File.OpenRead(imagePath))
            {
                BitmapDecoder dec = BitmapDecoder.Create(fs,
                    BitmapCreateOptions.IgnoreColorProfile, BitmapCacheOption.OnLoad);
                bmp = dec.Frames[0];
                bmp.Freeze();
            }
            int longest = Math.Max(bmp.PixelWidth, bmp.PixelHeight);
            if (longest > maxPixels)
            {
                double k = (double)maxPixels / longest;
                var scaled = new TransformedBitmap(bmp, new ScaleTransform(k, k));
                scaled.Freeze();
                bmp = scaled;
            }
            SavePng(bmp, pngPath);
        }

        /// <summary>
        /// Writes a stand-in preview for a vector image, which only ArcGIS Pro
        /// can draw: a pale box of the image's shape with its type lettered
        /// in the middle. Shown until (or unless) Pro's drawing replaces it.
        /// </summary>
        public static void WritePlaceholderPreview(string pngPath, double aspect, string label)
        {
            if (!(aspect > 0) || double.IsInfinity(aspect))
            {
                aspect = 1.0;
            }
            const double Long = 256.0;
            double w = aspect >= 1.0 ? Long : Math.Max(24.0, Long * aspect);
            double h = aspect >= 1.0 ? Math.Max(24.0, Long / aspect) : Long;

            var visual = new DrawingVisual();
            using (DrawingContext dc = visual.RenderOpen())
            {
                var edge = new Pen(new SolidColorBrush(Color.FromRgb(0x9A, 0x9A, 0x9A)), 2.0)
                {
                    DashStyle = new DashStyle(new double[] { 4, 3 }, 0),
                };
                dc.DrawRectangle(new SolidColorBrush(Color.FromRgb(0xF3, 0xF3, 0xF3)), edge,
                    new Rect(1, 1, w - 2, h - 2));
                var text = new FormattedText(label ?? "", CultureInfo.InvariantCulture,
                    FlowDirection.LeftToRight, new Typeface("Segoe UI"),
                    Math.Max(12.0, Math.Min(w, h) * 0.28),
                    new SolidColorBrush(Color.FromRgb(0x77, 0x77, 0x77)), 1.0);
                dc.DrawText(text, new Point((w - text.Width) / 2.0, (h - text.Height) / 2.0));
            }
            var bmp = new RenderTargetBitmap((int)Math.Ceiling(w), (int)Math.Ceiling(h),
                96, 96, PixelFormats.Pbgra32);
            bmp.Render(visual);
            bmp.Freeze();
            SavePng(bmp, pngPath);
        }

        /// <summary>Writes a bitmap as a PNG, replacing any file of that name.</summary>
        public static void SavePng(BitmapSource bmp, string pngPath)
        {
            var enc = new PngBitmapEncoder();
            enc.Frames.Add(BitmapFrame.Create(bmp));
            string tmp = pngPath + ".tmp";
            using (FileStream fs = File.Create(tmp))
            {
                enc.Save(fs);
            }
            File.Move(tmp, pngPath, true);
        }

        /// <summary>
        /// Loads a picture into memory, decoded no taller than
        /// <paramref name="decodeHeight"/> pixels (0 = full size), and frozen.
        /// The file is read whole and closed: it is never left locked (the
        /// store deletes files) and never served stale from WPF's image cache.
        /// </summary>
        public static ImageSource Load(string path, int decodeHeight)
        {
            byte[] bytes = File.ReadAllBytes(path);
            var bi = new BitmapImage();
            bi.BeginInit();
            bi.CacheOption = BitmapCacheOption.OnLoad;
            if (decodeHeight > 0)
            {
                bi.DecodePixelHeight = decodeHeight;
            }
            bi.StreamSource = new MemoryStream(bytes);
            bi.EndInit();
            bi.Freeze();
            return bi;
        }

        /// <summary>One line describing a registered image's file for the
        /// Manage window: its type and, for a raster, its pixel size.</summary>
        public static string FileSummary(GraphicEntry entry)
        {
            if (entry == null)
            {
                return "";
            }
            if (entry.IsBuiltIn)
            {
                return "Built in (vector drawing)";
            }
            string type = Path.GetExtension(entry.FileName ?? "").TrimStart('.').ToUpperInvariant();
            string from = string.IsNullOrEmpty(entry.Source) ? "" : Path.GetFileName(entry.Source);
            string what = entry.PixelWidth > 0 && entry.PixelHeight > 0
                ? string.Format(CultureInfo.CurrentCulture, "{0} image, {1:N0} x {2:N0} pixels",
                    type, entry.PixelWidth, entry.PixelHeight)
                : type + " vector drawing";
            return from.Length > 0 ? from + "  (" + what + ")" : what;
        }
    }
}
