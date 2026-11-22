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

// Headless test of PrismLayerFacts: what the add-in reads from a layer's
// definition and from a netCDF file before it closes the map's time slider
// to a single date. The guard under test: only layers of a file that the
// PRISM Climate Data tool wrote, and only in a map with no other layer
// that uses time.
//
//   dotnet run --project claude_tests/PrismLayerFactsTest
//
// With arguments it reports on real files instead, for a look at a layer
// file (.lyrx) or a layer definition (.json) saved from ArcGIS Pro:
//
//   dotnet run --project claude_tests/PrismLayerFactsTest -- <layer.lyrx or layer.json> [...]

using System;
using System.IO;
using System.Text;
using System.Text.Json;
using WildlifeTools.OnlineData.Prism;

namespace PrismLayerFactsTest
{
    internal static class Program
    {
        private static int _passed, _failed;

        private static void Check(string name, bool ok, string detail = "")
        {
            if (ok) _passed++;
            else
            {
                _failed++;
                Console.WriteLine("FAIL  " + name + "  " + detail);
            }
        }

        // The XML by which a multidimensional layer names its file, as ArcGIS Pro 3.7 writes it (shortened).
        private static string DatasetXml(string path, string variable = "tmean")
        {
            return "<XmlRasterDataset xsi:type='typens:FunctionRasterDatasetName'>"
                + "<Function xsi:type='typens:MultidimensionalFilterFunction' id='ID1'><Name>Multidimensional Filter Function</Name></Function>"
                + "<Arguments><Values><AnyType xsi:type='typens:ArrayOfDimensionalDefinition'><DimensionalDefinition>"
                + "<Values><AnyType xsi:type='xs:string'>" + variable + "</AnyType><AnyType xsi:type='xs:string'></AnyType></Values>"
                + "</DimensionalDefinition></AnyType><AnyType xsi:type='typens:Raster'><DatasetName xsi:type='typens:FunctionRasterDatasetName'>"
                + "<Function xsi:type='typens:MultidimensionalRasterFunction'><Name>Multidimensional Raster Function</Name></Function>"
                + "<Arguments><Values><AnyType xsi:type='xs:string'>" + path + "</AnyType>"
                + "<AnyType xsi:type='typens:ArrayOfString'><String>" + variable + "</String></AnyType></Values></Arguments>"
                + "<BrowseName>x967cf055_9279_43f0_b622_1182b751f2e8y0.afr</BrowseName></DatasetName></AnyType></Values></Arguments>"
                + "</XmlRasterDataset>";
        }

        private static string RasterLayer(string datasetXml, long start, long? end, bool useTime = true)
        {
            using var ms = new MemoryStream();
            using (var w = new Utf8JsonWriter(ms))
            {
                w.WriteStartObject();
                w.WriteString("type", "CIMRasterLayer");
                w.WriteString("name", "PRISM_climate_tmean");
                w.WriteStartObject("dataConnection");
                w.WriteString("type", "CIMStandardDataConnection");
                w.WriteString("workspaceFactory", "SDE");
                w.WriteString("dataset", datasetXml);
                w.WriteEndObject();
                w.WriteStartObject("timeDefinition");
                w.WriteString("type", "CIMTimeDataDefinition");
                w.WriteBoolean("useTime", useTime);
                w.WriteEndObject();
                w.WriteStartObject("activeSlice");
                w.WriteString("type", "CIMRasterMultidimensionalDisplayDefinition");
                w.WriteString("variableName", "tmean");
                w.WriteStartObject("timeValue");
                w.WriteString("type", "TimeExtent");
                w.WriteNumber("start", start);
                if (end.HasValue) w.WriteNumber("end", end.Value);
                else w.WriteNull("end");
                w.WriteBoolean("empty", false);
                w.WriteEndObject();
                w.WriteEndObject();
                w.WriteEndObject();
            }
            return Encoding.UTF8.GetString(ms.ToArray());
        }

        private const long Jan2020 = 1577836800000L;     // 1 January 2020, as a layer definition writes it
        private const long Feb2020 = 1580515200000L;

        private static int Main(string[] args)
        {
            if (args.Length > 0) return Report(args);

            // ---- a layer of a netCDF file, as the tool's layers are ---------------------------------------
            string one = RasterLayer(DatasetXml(@"D:\Climate\PRISM_climate.nc"), Jan2020, Jan2020);
            bool ok = PrismLayerFacts.TryReadNetCdfLayer(one, out string path, out DateTime shown, out bool single);
            Check("netCDF layer: read", ok);
            Check("netCDF layer: its file", path == @"D:\Climate\PRISM_climate.nc", path);
            Check("netCDF layer: the date it shows", shown == new DateTime(2020, 1, 1), shown.ToString("s"));
            Check("netCDF layer: a single date", single);
            Check("netCDF layer: uses time", PrismLayerFacts.IsTimeAware(one));

            string span = RasterLayer(DatasetXml(@"D:\Climate\old.nc"), Jan2020, Feb2020);
            ok = PrismLayerFacts.TryReadNetCdfLayer(span, out path, out shown, out single);
            Check("a span of days is read, and is not a single date", ok && !single && shown == new DateTime(2020, 1, 1));

            string open = RasterLayer(DatasetXml(@"D:\Climate\open.nc"), Jan2020, null);
            ok = PrismLayerFacts.TryReadNetCdfLayer(open, out path, out shown, out single);
            Check("a time with no end is not a single date", ok && !single);

            string amp = RasterLayer(DatasetXml(@"D:\Rain &amp; Snow\PRISM &apos;20.NC"), Jan2020, Jan2020);
            ok = PrismLayerFacts.TryReadNetCdfLayer(amp, out path, out shown, out single);
            Check("a path with an ampersand and an apostrophe, and .NC in capitals",
                ok && path == @"D:\Rain & Snow\PRISM '20.NC", path);

            string two = RasterLayer(DatasetXml(@"D:\a.nc").Replace("<BrowseName>", "<AnyType>D:\\b.nc</AnyType><BrowseName>"),
                Jan2020, Jan2020);
            Check("two different netCDF files in one layer: not read",
                !PrismLayerFacts.TryReadNetCdfLayer(two, out path, out shown, out single) && path == null, path);

            string relative = RasterLayer(DatasetXml(@"PRISM_climate.nc"), Jan2020, Jan2020);
            Check("a file name with no folder: not read",
                !PrismLayerFacts.TryReadNetCdfLayer(relative, out path, out shown, out single));

            string tif = RasterLayer(DatasetXml(@"D:\Climate\elevation.tif"), Jan2020, Jan2020);
            Check("a multidimensional layer of another kind of file: not read",
                !PrismLayerFacts.TryReadNetCdfLayer(tif, out path, out shown, out single));

            string folderForm = "{\"type\":\"CIMRasterLayer\",\"dataConnection\":{\"type\":\"CIMStandardDataConnection\","
                + "\"workspaceConnectionString\":\"DATABASE=D:\\\\Climate\",\"workspaceFactory\":\"Raster\",\"dataset\":\"p.nc\"},"
                + "\"activeSlice\":{\"timeValue\":{\"type\":\"TimeExtent\",\"start\":" + Jan2020 + ",\"end\":" + Jan2020 + "}}}";
            ok = PrismLayerFacts.TryReadNetCdfLayer(folderForm, out path, out shown, out single);
            Check("a connection that gives a folder and a file name", ok && path == @"D:\Climate\p.nc" && single, path);

            string written = "{\"dataConnection\":{\"dataset\":\"" + DatasetXml("D:/x/p.nc").Replace("'", "\\u0027") + "\"},"
                + "\"activeSlice\":{\"timeValue\":{\"start\":\"2020-03-01T00:00:00Z\",\"end\":\"2020-03-01T00:00:00Z\"}}}";
            ok = PrismLayerFacts.TryReadNetCdfLayer(written, out path, out shown, out single);
            Check("dates written out, and a path with forward slashes",
                ok && single && shown == new DateTime(2020, 3, 1) && path == "D:/x/p.nc", shown.ToString("s") + " " + path);

            // ---- layers that are not ---------------------------------------------------------------------
            string plainRaster = "{\"type\":\"CIMRasterLayer\",\"name\":\"dem\",\"dataConnection\":{\"type\":"
                + "\"CIMStandardDataConnection\",\"workspaceConnectionString\":\"DATABASE=D:\\\\data\",\"dataset\":\"dem.tif\"},"
                + "\"timeDefinition\":{\"type\":\"CIMTimeDataDefinition\",\"useTime\":false}}";
            Check("an ordinary raster: not a netCDF layer",
                !PrismLayerFacts.TryReadNetCdfLayer(plainRaster, out path, out shown, out single));
            Check("an ordinary raster: does not use time", !PrismLayerFacts.IsTimeAware(plainRaster));

            string tracks = "{\"type\":\"CIMFeatureLayer\",\"name\":\"tracks\",\"featureTable\":{\"type\":\"CIMFeatureTable\","
                + "\"timeFields\":{\"type\":\"CIMTimeTableDefinition\",\"startTimeField\":\"timestamp\"},"
                + "\"timeDefinition\":{\"type\":\"CIMTimeDataDefinition\"}}}";
            Check("a feature layer with a time field: uses time", PrismLayerFacts.IsTimeAware(tracks));
            Check("a feature layer with a time field: not a netCDF layer",
                !PrismLayerFacts.TryReadNetCdfLayer(tracks, out path, out shown, out single));

            string deepTime = "{\"type\":\"CIMGroupLayer\",\"x\":[{\"y\":{\"timeDefinition\":{\"useTime\":true}}}]}";
            Check("useTime found wherever it sits", PrismLayerFacts.IsTimeAware(deepTime));

            string noTime = "{\"type\":\"CIMFeatureLayer\",\"featureTable\":{\"timeFields\":{\"type\":\"CIMTimeTableDefinition\"},"
                + "\"timeDefinition\":{\"useTime\":false}},\"note\":\"useTime: true\"}";
            Check("no time: empty time fields, useTime false, and the words in a text do not count",
                !PrismLayerFacts.IsTimeAware(noTime));

            Check("a definition that cannot be read counts as one that uses time",
                PrismLayerFacts.IsTimeAware("{not json") && PrismLayerFacts.IsTimeAware(null)
                && PrismLayerFacts.IsTimeAware(""));
            Check("a definition that cannot be read is not a netCDF layer",
                !PrismLayerFacts.TryReadNetCdfLayer("{not json", out path, out shown, out single)
                && !PrismLayerFacts.TryReadNetCdfLayer(null, out path, out shown, out single)
                && !PrismLayerFacts.TryReadNetCdfLayer("[1,2]", out path, out shown, out single));

            // ---- the file: written by the tool, or not ------------------------------------------------------
            string work = Path.Combine(Path.GetTempPath(), "prism_facts_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(work);
            try
            {
                byte[] marker = Encoding.ASCII.GetBytes(PrismLayerFacts.Marker);
                Check("the marker is plain ASCII", Encoding.UTF8.GetBytes(PrismLayerFacts.Marker).Length == marker.Length);

                string ours = Path.Combine(work, "ours.nc");
                WriteWithMarker(ours, 300000, 17400, marker);
                Check("a file with the marker where the tool's files hold it", PrismLayerFacts.WrittenByTool(ours));

                string other = Path.Combine(work, "other.nc");
                WriteWithMarker(other, 300000, -1, marker);
                Check("a netCDF file made some other way: no marker", !PrismLayerFacts.WrittenByTool(other));

                string history = Path.Combine(work, "history.nc");
                WriteWithMarker(history, 300000, 17400, Encoding.ASCII.GetBytes(
                    "written by the PRISM Climate Data tool of Wildlife and Forestry Tools for ArcGIS Pro"));
                Check("the wording of an older file's history is not the marker", !PrismLayerFacts.WrittenByTool(history));

                string late = Path.Combine(work, "late.nc");
                WriteWithMarker(late, PrismLayerFacts.MarkerSearchBytes + 200000, PrismLayerFacts.MarkerSearchBytes + 1000, marker);
                Check("a marker beyond the part that is read does not count", !PrismLayerFacts.WrittenByTool(late));

                string edge = Path.Combine(work, "edge.nc");
                WriteWithMarker(edge, PrismLayerFacts.MarkerSearchBytes + 1000, PrismLayerFacts.MarkerSearchBytes - marker.Length, marker);
                Check("a marker that ends exactly where the reading ends counts", PrismLayerFacts.WrittenByTool(edge));

                string cut = Path.Combine(work, "cut.nc");
                WriteWithMarker(cut, PrismLayerFacts.MarkerSearchBytes + 1000, PrismLayerFacts.MarkerSearchBytes - marker.Length + 1, marker);
                Check("a marker cut off by the end of the reading does not", !PrismLayerFacts.WrittenByTool(cut));

                string tiny = Path.Combine(work, "tiny.nc");
                File.WriteAllBytes(tiny, new byte[] { 1, 2, 3 });
                Check("a file shorter than the marker", !PrismLayerFacts.WrittenByTool(tiny));
                File.WriteAllBytes(tiny, marker);
                Check("the same file rewritten with the marker is read again", PrismLayerFacts.WrittenByTool(tiny));
                Check("and asked again gives the same answer", PrismLayerFacts.WrittenByTool(tiny));

                Check("a file that is not there", !PrismLayerFacts.WrittenByTool(Path.Combine(work, "gone.nc")));
                Check("no path", !PrismLayerFacts.WrittenByTool(null) && !PrismLayerFacts.WrittenByTool("  "));
                Check("a path that cannot be one", !PrismLayerFacts.WrittenByTool("D:\\a\0b.nc"));

                // open elsewhere for writing, as ArcGIS Pro may hold it: still read
                using (var held = new FileStream(ours, FileMode.Open, FileAccess.ReadWrite, FileShare.ReadWrite))
                {
                    File.SetLastWriteTimeUtc(other, DateTime.UtcNow.AddMinutes(-5));
                    string again = Path.Combine(work, "held.nc");
                    File.Copy(ours, again);
                    using (var h2 = new FileStream(again, FileMode.Open, FileAccess.ReadWrite, FileShare.ReadWrite))
                        Check("a file another program has open is still read", PrismLayerFacts.WrittenByTool(again));
                }
            }
            finally
            {
                try { Directory.Delete(work, true); } catch (IOException) { }
            }

            Console.WriteLine();
            Console.WriteLine(_passed + " passed, " + _failed + " failed");
            return _failed == 0 ? 0 : 1;
        }

        private static void WriteWithMarker(string path, int length, int at, byte[] text)
        {
            var bytes = new byte[length];
            var rnd = new Random(7);
            rnd.NextBytes(bytes);
            // random bytes cannot spell the marker by chance in any way that matters, but keep them out of the
            // letters anyway so a failure is never a puzzle
            for (int i = 0; i < bytes.Length; i++) if (bytes[i] >= 32 && bytes[i] < 127) bytes[i] = 0;
            if (at >= 0) Array.Copy(text, 0, bytes, at, text.Length);
            File.WriteAllBytes(path, bytes);
        }

        /// <summary>Reports what the add-in would read from saved layer
        /// files or layer definitions.</summary>
        private static int Report(string[] paths)
        {
            foreach (string p in paths)
            {
                string text = File.ReadAllText(p);
                using JsonDocument doc = JsonDocument.Parse(text);
                JsonElement root = doc.RootElement;
                if (root.TryGetProperty("layerDefinitions", out JsonElement defs) && defs.ValueKind == JsonValueKind.Array)
                {
                    foreach (JsonElement d in defs.EnumerateArray()) Describe(Path.GetFileName(p), d.GetRawText());
                }
                else
                {
                    Describe(Path.GetFileName(p), text);
                }
            }
            return 0;
        }

        private static void Describe(string name, string layerJson)
        {
            bool nc = PrismLayerFacts.TryReadNetCdfLayer(layerJson, out string path, out DateTime shown, out bool single);
            Console.WriteLine(name + ": uses time " + PrismLayerFacts.IsTimeAware(layerJson)
                + " | netCDF layer " + nc
                + (nc ? " | file " + path + " | shows " + shown.ToString("yyyy-MM-dd HH:mm") + " | single date " + single
                    + " | written by the tool " + PrismLayerFacts.WrittenByTool(path) : ""));
        }
    }
}
