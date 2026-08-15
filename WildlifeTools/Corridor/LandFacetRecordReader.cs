using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Xml;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Reads the machine-readable Land Facet linkage records out of dataset
    /// metadata and turns them into the human-readable "Land Facet Data
    /// Report".
    ///
    /// This file deliberately has NO ArcGIS references: everything here works
    /// on metadata XML strings handed in by a fetch delegate, so the chain
    /// walking, staleness checks and narrative building are testable outside
    /// Pro. The ArcGIS side (getting XML for a layer or a path on the MCT)
    /// lives in <see cref="LandFacetDescribeWindow"/>.
    ///
    /// SYNC NOTE: the marker string and the JSON field names must match
    /// Toolboxes\land_facet_linkage.py, the single WRITING definition of the
    /// record format. Reading is tolerant -- unknown fields are ignored and
    /// missing ones degrade to prose that says so -- so schema additions on
    /// the Python side do not break this reader.
    /// </summary>
    internal static class LandFacetRecordReader
    {
        private const string Marker = "JENNESS-LANDFACET-LINKAGE:";
        private const string Schema = "jenness.landfacet/1";

        // ---- report model ---------------------------------------------------
        internal enum BlockKind
        {
            Title, Heading, HeadingTarget, SubHeading, Body, Bullet, KeyValue,
            Warning, Note, ChainStep, ChainStepCurrent, Separator,
            // A bullet that needs the reader's eye: rendered in the same
            // alert red as Warning (e.g. a termini category with NO termini
            // in one or more Wildland Blocks).
            BulletAlert,
        }

        internal sealed class Block
        {
            public BlockKind Kind;
            public string Text = "";
            public string Value = "";   // KeyValue only
            public Block(BlockKind k, string text, string value = "")
            { Kind = k; Text = text; Value = value; }
        }

        // ---- metadata XML helpers ------------------------------------------
        /// <summary>The linkage-record JSON on a metadata XML, or null.</summary>
        internal static JsonElement? ExtractRecord(string metadataXml)
        {
            string blob = ExtractSuppInfo(metadataXml);
            if (blob == null || blob.IndexOf(Marker, StringComparison.Ordinal) < 0)
                return null;
            string json = blob.Substring(
                blob.IndexOf(Marker, StringComparison.Ordinal) + Marker.Length).Trim();
            try
            {
                var doc = JsonDocument.Parse(TrimToJson(json));
                var root = doc.RootElement.Clone();
                if (S(root, "schema") != Schema) return null;
                return root;
            }
            catch { return null; }
        }

        private static string TrimToJson(string text)
        {
            // Tolerate prose appended after the JSON object by a later editor:
            // balance braces from the first '{'.
            int start = text.IndexOf('{');
            if (start < 0) return text;
            int depth = 0;
            bool inStr = false;
            for (int i = start; i < text.Length; i++)
            {
                char ch = text[i];
                if (inStr)
                {
                    if (ch == '\\') i++;
                    else if (ch == '"') inStr = false;
                }
                else if (ch == '"') inStr = true;
                else if (ch == '{') depth++;
                else if (ch == '}' && --depth == 0)
                    return text.Substring(start, i - start + 1);
            }
            return text.Substring(start);
        }

        private static string ExtractSuppInfo(string metadataXml)
        {
            if (string.IsNullOrWhiteSpace(metadataXml)) return null;
            try
            {
                var doc = new XmlDocument();
                doc.LoadXml(metadataXml);
                return doc.SelectSingleNode("//dataIdInfo/suppInfo")?.InnerText;
            }
            catch { return null; }
        }

        /// <summary>The Esri Summary (purpose) text, for the tier-2 fallback.</summary>
        internal static string ExtractSummary(string metadataXml)
        {
            if (string.IsNullOrWhiteSpace(metadataXml)) return null;
            try
            {
                var doc = new XmlDocument();
                doc.LoadXml(metadataXml);
                return doc.SelectSingleNode("//dataIdInfo/idPurp")?.InnerText?.Trim();
            }
            catch { return null; }
        }

        internal static bool LooksLikeJennessProduct(string metadataXml)
        {
            if (string.IsNullOrWhiteSpace(metadataXml)) return false;
            try
            {
                var doc = new XmlDocument();
                doc.LoadXml(metadataXml);
                string credit = doc.SelectSingleNode("//idCredit")?.InnerText ?? "";
                return credit.IndexOf("Jenness Enterprises",
                                      StringComparison.OrdinalIgnoreCase) >= 0;
            }
            catch { return false; }
        }

        // ---- tolerant JSON access ------------------------------------------
        private static string S(JsonElement e, string name)
        {
            return e.ValueKind == JsonValueKind.Object
                   && e.TryGetProperty(name, out var v)
                   && v.ValueKind == JsonValueKind.String
                ? v.GetString() : null;
        }

        private static double? D(JsonElement e, string name)
        {
            if (e.ValueKind == JsonValueKind.Object
                && e.TryGetProperty(name, out var v)
                && v.ValueKind == JsonValueKind.Number
                && v.TryGetDouble(out double d)) return d;
            return null;
        }

        private static bool? B(JsonElement e, string name)
        {
            if (e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out var v))
            {
                if (v.ValueKind == JsonValueKind.True) return true;
                if (v.ValueKind == JsonValueKind.False) return false;
            }
            return null;
        }

        private static JsonElement? O(JsonElement e, string name)
        {
            return e.ValueKind == JsonValueKind.Object
                   && e.TryGetProperty(name, out var v)
                   && (v.ValueKind == JsonValueKind.Object
                       || v.ValueKind == JsonValueKind.Array)
                ? v : (JsonElement?)null;
        }

        private static IEnumerable<JsonElement> Arr(JsonElement e, string name)
        {
            var o = O(e, name);
            if (o.HasValue && o.Value.ValueKind == JsonValueKind.Array)
                foreach (var item in o.Value.EnumerateArray()) yield return item;
        }

        private static DateTime? Created(JsonElement e)
        {
            string t = S(e, "created");
            if (t != null && DateTime.TryParse(t, CultureInfo.InvariantCulture,
                    DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal,
                    out var dt))
                return dt;
            return null;
        }

        private static string FacetLabel(JsonElement? facet)
        {
            if (!facet.HasValue || facet.Value.ValueKind != JsonValueKind.Object)
                return null;
            var f = facet.Value;
            string cat = S(f, "category_label");
            double? clu = D(f, "cluster");
            if (!string.IsNullOrEmpty(cat) && clu.HasValue)
                return string.Format(CultureInfo.InvariantCulture,
                                     "{0}, Cluster {1:0}", cat, clu.Value);
            if (!string.IsNullOrEmpty(cat)) return cat;
            double? fid = D(f, "facet_id");
            return fid.HasValue
                ? "Facet " + fid.Value.ToString("0", CultureInfo.InvariantCulture)
                : null;
        }

        // ---- the report -----------------------------------------------------
        /// <summary>
        /// Build the full report for a dataset. ``fetchXml`` returns the
        /// metadata XML for a dataset path (null when the dataset does not
        /// exist or has no readable metadata); it is also used to follow the
        /// recorded source paths, so all ArcGIS work stays in the caller.
        /// </summary>
        internal static List<Block> BuildReport(string datasetLabel,
                                                string datasetPath,
                                                string metadataXml,
                                                Func<string, string> fetchXml)
        {
            var blocks = new List<Block>
            {
                new Block(BlockKind.Title, "Corridor Analysis Data Report"),
                new Block(BlockKind.KeyValue, "Dataset", datasetLabel),
            };
            if (!string.IsNullOrEmpty(datasetPath) && datasetPath != datasetLabel)
                blocks.Add(new Block(BlockKind.KeyValue, "Location", datasetPath));
            blocks.Add(new Block(BlockKind.Separator, ""));

            var rec = ExtractRecord(metadataXml);
            if (!rec.HasValue)
            {
                // Tier 2: one of ours, but not part of the Land Facet chain.
                if (LooksLikeJennessProduct(metadataXml))
                {
                    blocks.Add(new Block(BlockKind.Body,
                        "This dataset is not part of a Land Facet Corridor "
                        + "analysis chain, but it was created by a Jenness "
                        + "Enterprises tool. Its own provenance summary reads:"));
                    string sum = ExtractSummary(metadataXml);
                    blocks.Add(new Block(BlockKind.Note,
                        string.IsNullOrWhiteSpace(sum)
                            ? "(no summary text was recorded)" : sum));
                }
                else
                {
                    // Tier 3: nothing known.
                    blocks.Add(new Block(BlockKind.Body,
                        "This dataset carries no Land Facet analysis record and "
                        + "does not appear to have been created by the Jenness "
                        + "Enterprises tools. Nothing is known about its role in "
                        + "a Land Facet Corridor analysis."));
                }
                return blocks;
            }

            // ---- collect the ancestry ------------------------------------
            var nodes = CollectChain(datasetPath, rec.Value, fetchXml);

            // ---- the "you are here" map -----------------------------------
            // A union has MULTIPLE parallel sources, not one linear chain
            // position -- the fixed step-by-step map doesn't apply; skip it
            // and let DescribeUnion's own "Sources used" section stand in.
            string kind = S(rec.Value, "record") ?? "";
            if (kind == "hsm")
            {
                // A species HSM has its own build/modify/invert sequence --
                // the land facet map would highlight nothing for it.
                blocks.Add(new Block(BlockKind.Heading,
                    "Where this dataset sits in the analysis"));
                AddHsmChainMap(blocks, rec.Value);
                blocks.Add(new Block(BlockKind.Separator, ""));
            }
            else if (kind != "union" && kind != "patch_map")
            {
                // A union fans in from parallel sources and a patch map is
                // a terminal habitat product -- neither sits on the linear
                // step maps, so neither gets one.
                blocks.Add(new Block(BlockKind.Heading,
                    "Where this dataset sits in the analysis"));
                AddChainMap(blocks, kind);
                blocks.Add(new Block(BlockKind.Separator, ""));
            }

            // ---- one section per ancestor, root first ---------------------
            foreach (var node in nodes)
            {
                DescribeNode(blocks, node, fetchXml);
                AddPostProcessingSteps(blocks, node.Record);
            }

            // ---- a union fans IN: append each source's own background -----
            // A union's sources are parallel, possibly unrelated analyses, so
            // CollectChain deliberately never walks them as a single ancestor
            // chain. Instead each record-carrying source gets its full
            // analysis history appended here, exactly as that source's own
            // report would read (Jeff, 2026-08-10).
            if (kind == "union")
            {
                int si = 0;
                foreach (var s in Arr(rec.Value, "sources"))
                {
                    si++;
                    string sPath = S(s, "path");
                    if (string.IsNullOrWhiteSpace(sPath)) continue;
                    var sRec = ExtractRecord(fetchXml?.Invoke(sPath));
                    if (!sRec.HasValue) continue;

                    string leaf = sPath.Substring(
                        sPath.LastIndexOfAny(new[] { '\\', '/' }) + 1);
                    blocks.Add(new Block(BlockKind.Separator, ""));
                    blocks.Add(new Block(BlockKind.Heading,
                        "Background of Source " + si + " — " + leaf));
                    blocks.Add(new Block(BlockKind.Note,
                        "The full analysis history of this union input, as "
                        + "its own report would read."));
                    foreach (var n in CollectChain(sPath, sRec.Value, fetchXml))
                    {
                        // The Ganado-red highlight marks the DESCRIBED
                        // dataset only, never a source's sub-chain.
                        n.IsTarget = false;
                        DescribeNode(blocks, n, fetchXml);
                        AddPostProcessingSteps(blocks, n.Record);
                    }
                }
            }

            string created = Created(rec.Value)?.ToString(
                "yyyy-MM-dd HH:mm 'UTC'", CultureInfo.InvariantCulture);
            string aid = S(rec.Value, "analysis_id");
            blocks.Add(new Block(BlockKind.Separator, ""));
            if (created != null)
                blocks.Add(new Block(BlockKind.KeyValue, "This dataset created",
                                     created));
            if (aid != null)
                blocks.Add(new Block(BlockKind.KeyValue, "Analysis identifier",
                                     aid));
            return blocks;
        }

        /// <summary>
        /// Generic "this dataset was also modified afterward" list -- works
        /// for ANY record kind, not just corridors. A record carries this
        /// when a later tool (e.g. Fill Corridor Holes) edited it in place
        /// rather than deriving a new kind of analysis product; the
        /// record's own kind/analysis id are left untouched, only a step is
        /// appended. Silently does nothing if the record has none.
        /// </summary>
        private static void AddPostProcessingSteps(List<Block> blocks, JsonElement rec)
        {
            var steps = Arr(rec, "post_processing").ToList();
            if (steps.Count == 0) return;
            blocks.Add(new Block(BlockKind.SubHeading, "Also processed afterward:"));
            foreach (var step in steps)
            {
                string tool = S(step, "tool") ?? "(unknown tool)";
                string when = null;
                string appliedRaw = S(step, "applied");
                if (appliedRaw != null && DateTime.TryParse(appliedRaw,
                        CultureInfo.InvariantCulture,
                        DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal,
                        out var dt))
                    when = dt.ToString("yyyy-MM-dd HH:mm 'UTC'", CultureInfo.InvariantCulture);
                string detail = S(step, "detail");
                blocks.Add(new Block(BlockKind.Bullet, tool
                    + (when == null ? "" : " (" + when + ")")
                    + (string.IsNullOrEmpty(detail) ? "" : ": " + detail)));
            }
        }

        private static void AddChainMap(List<Block> blocks, string kind)
        {
            (string[] keys, string text)[] steps =
            {
                (new[] { "facets" },
                 "1.  Land Facet Clustering — group the landscape into facets"),
                (new[] { "density" },
                 "2.  Land Facet Density — proportion of each facet in a "
                 + "neighborhood"),
                (new[] { "mahalanobis", "diversity" },
                 "3.  Land Facet Mahalanobis — cost surface per facet (or "
                 + "Diversity Indices for the interspersion corridor)"),
                (new[] { "inverted" },
                 "4.  Invert Raster — high-is-good surface into a least-cost "
                 + "surface (from a land facet Mahalanobis/Diversity "
                 + "surface, or a species habitat-suitability model built "
                 + "independently)"),
                (new[] { "termini" },
                 "5.  Identify Termini Polygons — the corridor endpoints "
                 + "inside the Wildland Blocks (from Density, Diversity, "
                 + "or a species habitat-suitability surface)"),
                (new[] { "corridor" },
                 "6.  Least-Cost Corridor — the corridor polygons between "
                 + "each pair of Wildland Blocks"),
            };
            foreach (var (stepKeys, text) in steps)
                blocks.Add(new Block(
                    stepKeys.Contains(kind)
                        ? BlockKind.ChainStepCurrent : BlockKind.ChainStep,
                    text));
        }

        /// <summary>
        /// The "you are here" map for a habitat-suitability-model dataset.
        /// A species HSM is the alternative to the land facet steps 1-3, with
        /// its own build/modify sequence before the shared invert/termini/
        /// corridor steps -- the facet map would highlight nothing for it.
        /// Step 2 lights up as well when the record carries post_processing
        /// steps, because the Modify HSM tools append those rather than
        /// minting a new record kind.
        /// </summary>
        private static void AddHsmChainMap(List<Block> blocks, JsonElement rec)
        {
            bool modified = Arr(rec, "post_processing").Any();
            blocks.Add(new Block(BlockKind.ChainStepCurrent,
                "1.  Build the habitat suitability model — score habitat "
                + "factors 0–100 and combine them, or rescale an existing "
                + "model onto the 0–100 scale"));
            blocks.Add(new Block(
                modified ? BlockKind.ChainStepCurrent : BlockKind.ChainStep,
                "2.  Modify the model (optional) — Reclassify Features in "
                + "HSM, Critical Habitat Feature"));
            blocks.Add(new Block(BlockKind.ChainStep,
                "3.  Invert Raster — the high-is-good model into a "
                + "least-cost surface"));
            blocks.Add(new Block(BlockKind.ChainStep,
                "4.  Identify Termini Polygons — the corridor endpoints "
                + "inside the Wildland Blocks (cut from the HSM itself, "
                + "not the inverted surface)"));
            blocks.Add(new Block(BlockKind.ChainStep,
                "5.  Least-Cost Corridor — the corridor polygons between "
                + "each pair of Wildland Blocks"));
        }

        // ---- the chain ------------------------------------------------------
        private sealed class Node
        {
            public string Path;             // where the record was read from
            public JsonElement Record;
            public string Kind;
            public bool IsTarget;
            public string ViaNote;          // how this ancestor was reached
        }

        private static int KindRank(string kind) => kind switch
        {
            "facets" => 0, "density" => 1,
            "mahalanobis" => 2, "diversity" => 2, "hsm" => 2,
            "inverted" => 3, "patch_map" => 3,
            "termini" => 4, "corridor" => 5,
            "union" => 6,
            _ => 7,
        };

        /// <summary>
        /// A recorded source path, made followable. Corridor records written
        /// before v1.57.1 could store ``source_termini`` as a bare layer
        /// NAME; a name with no separator is tried next to the dataset that
        /// recorded it (its own workspace), which is where the termini
        /// live in every real run.
        /// </summary>
        private static string ResolveSourcePath(string src, string basePath)
        {
            if (string.IsNullOrWhiteSpace(src)) return src;
            if (src.IndexOfAny(new[] { '\\', '/' }) >= 0) return src;
            int cut = (basePath ?? "").LastIndexOfAny(new[] { '\\', '/' });
            return cut > 0 ? basePath.Substring(0, cut + 1) + src : src;
        }

        private static List<Node> CollectChain(string targetPath,
                                               JsonElement targetRec,
                                               Func<string, string> fetchXml)
        {
            var found = new List<Node>();
            var visited = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            void Visit(string path, JsonElement rec, bool isTarget, string via)
            {
                string norm = (path ?? "").Trim();
                if (norm.Length > 0 && !visited.Add(norm)) return;
                found.Add(new Node
                {
                    Path = path, Record = rec, Kind = S(rec, "record") ?? "?",
                    IsTarget = isTarget, ViaNote = via,
                });
                foreach (var (srcPath0, via2) in SourcePaths(rec))
                {
                    string srcPath = ResolveSourcePath(srcPath0, path);
                    if (string.IsNullOrWhiteSpace(srcPath)) continue;
                    if (visited.Contains(srcPath.Trim())) continue;
                    var xml = fetchXml?.Invoke(srcPath);
                    var srcRec = ExtractRecord(xml);
                    if (srcRec.HasValue)
                        Visit(srcPath, srcRec.Value, false, via2);
                }
            }

            Visit(targetPath, targetRec, true, null);
            return found.OrderBy(n => KindRank(n.Kind)).ToList();
        }

        private static IEnumerable<(string path, string via)> SourcePaths(
            JsonElement rec)
        {
            string kind = S(rec, "record");
            if (kind == "density" || kind == "mahalanobis")
                yield return (S(rec, "source_facet_raster"),
                              "the land facet raster this was built from");
            if (kind == "mahalanobis" || kind == "inverted")
                yield return (S(rec, kind == "inverted"
                                  ? "source_raster" : "source_density_raster"),
                              "the raster this was derived from");
            if (kind == "diversity")
                yield return (S(rec, "source_raster"),
                              "the categorical raster this was computed from");
            // Only the normalized/external HSM shapes carry source_raster;
            // build-from-factors records store their inputs in "factors",
            // which are habitat rasters listed (with weights) in the HSM
            // section itself, not walked as a linear ancestor chain.
            if (kind == "hsm")
                yield return (S(rec, "source_raster"),
                              "the raster this habitat model came from");
            if (kind == "patch_map")
                yield return (S(rec, "source_raster"),
                              "the habitat model the patches were cut from");
            if (kind == "termini")
                yield return (S(rec, "source_raster"),
                              "the analysis surface the termini were cut from");
            if (kind == "corridor")
            {
                yield return (S(rec, "source_termini"),
                              "the termini this corridor connects");
                // Only the single-corridor (per-raster) shape carries this;
                // the shared polygon output's summary shape has one cost
                // raster PER corridor instead (listed, not chased here).
                if (S(rec, "source_cost_raster") != null)
                    yield return (S(rec, "source_cost_raster"),
                                  "the cost surface this corridor was built "
                                  + "from");
            }
        }

        // ---- per-kind narratives -------------------------------------------
        private static void DescribeNode(List<Block> blocks, Node node,
                                         Func<string, string> fetchXml)
        {
            var rec = node.Record;
            switch (node.Kind)
            {
                case "facets": DescribeFacets(blocks, node); break;
                case "density": DescribeDensity(blocks, node, fetchXml); break;
                case "mahalanobis": DescribeMahalanobis(blocks, node, fetchXml); break;
                case "diversity": DescribeDiversity(blocks, node, fetchXml); break;
                case "inverted": DescribeInverted(blocks, node, fetchXml); break;
                case "termini": DescribeTermini(blocks, node, fetchXml); break;
                case "corridor": DescribeCorridor(blocks, node, fetchXml); break;
                case "union": DescribeUnion(blocks, node, fetchXml); break;
                case "hsm": DescribeHsm(blocks, node, fetchXml); break;
                case "patch_map": DescribePatchMap(blocks, node, fetchXml); break;
                default:
                    blocks.Add(new Block(BlockKind.Heading,
                        (node.IsTarget ? "This dataset" : node.Path)
                        + " — unrecognized record kind \"" + node.Kind + "\""));
                    break;
            }
            blocks.Add(new Block(BlockKind.Separator, ""));
        }

        private static string Header(Node node, string title)
        {
            return node.IsTarget ? title + "  (this dataset)" : title;
        }

        /// <summary>The heading kind for a section: the described dataset's own
        /// section is highlighted (rendered in Ganado Red) so the eye lands on
        /// it among the ancestor sections.</summary>
        private static BlockKind HeadKind(Node node)
        {
            return node.IsTarget ? BlockKind.HeadingTarget : BlockKind.Heading;
        }

        private static void AddOrigin(List<Block> blocks, Node node)
        {
            if (!node.IsTarget && !string.IsNullOrEmpty(node.Path))
                blocks.Add(new Block(BlockKind.KeyValue, "Read from", node.Path));
            var created = Created(node.Record);
            if (created.HasValue)
                blocks.Add(new Block(BlockKind.KeyValue, "Created",
                    created.Value.ToString("yyyy-MM-dd HH:mm 'UTC'",
                                           CultureInfo.InvariantCulture)));
        }

        private static void DescribeFacets(List<Block> blocks, Node node)
        {
            var rec = node.Record;
            blocks.Add(new Block(HeadKind(node),
                Header(node, "Step 1 — Land Facet Clustering")));
            AddOrigin(blocks, node);

            string catSrc = null;
            var srcObj = O(rec, "source");
            if (srcObj.HasValue)
                catSrc = S(srcObj.Value, "categorical_raster");
            blocks.Add(new Block(BlockKind.Body,
                "The landscape was divided into land facets by fuzzy c-means "
                + "clustering of continuous variables within each class of a "
                + "categorical raster"
                + (string.IsNullOrEmpty(catSrc) ? "." : " (" + catSrc + ").")));

            var vars = Arr(rec, "variables").ToList();
            if (vars.Count > 0)
            {
                blocks.Add(new Block(BlockKind.SubHeading,
                    "Clustering variables, in the order used (this order also "
                    + "governs the later Mahalanobis statistics):"));
                foreach (var v in vars)
                {
                    string nm = S(v, "name") ?? "(unnamed)";
                    string src = S(v, "source");
                    blocks.Add(new Block(BlockKind.Bullet,
                        nm + (string.IsNullOrEmpty(src) ? "" : "   [" + src + "]")));
                }
            }

            var p = O(rec, "params");
            if (p.HasValue)
            {
                double? fuzz = D(p.Value, "fuzziness");
                double? conf = D(p.Value, "confusion_threshold");
                var parts = new List<string>();
                if (fuzz.HasValue)
                    parts.Add("fuzziness " + fuzz.Value.ToString("0.##",
                        CultureInfo.InvariantCulture));
                if (conf.HasValue)
                    parts.Add("confusion-index threshold " + conf.Value.ToString(
                        "0.##", CultureInfo.InvariantCulture)
                        + " (cells with a less decisive cluster assignment "
                        + "became NoData)");
                string om = S(p.Value, "outlier_method");
                if (om == "gaussian_kde_scott")
                    parts.Add("outliers screened by Gaussian kernel density "
                              + "(Scott bandwidth)");
                if (parts.Count > 0)
                    blocks.Add(new Block(BlockKind.Body,
                        "Settings: " + string.Join("; ", parts) + "."));

                var cats = Arr(p.Value, "categories").ToList();
                if (cats.Count > 0)
                {
                    blocks.Add(new Block(BlockKind.SubHeading,
                        "Per-category clustering:"));
                    foreach (var c in cats)
                    {
                        string lab = S(c, "category_label") ?? "Class";
                        double? k = D(c, "k");
                        double? ofr = D(c, "outlier_fraction");
                        double? cells = D(c, "cells");
                        blocks.Add(new Block(BlockKind.Bullet, string.Format(
                            CultureInfo.InvariantCulture,
                            "{0}: {1} clusters{2}{3}", lab,
                            k.HasValue ? k.Value.ToString("0") : "?",
                            ofr.HasValue
                                ? ", " + (ofr.Value * 100).ToString("0.#")
                                  + "% flagged as outliers" : "",
                            cells.HasValue
                                ? ", from " + cells.Value.ToString("#,##0")
                                  + " cells" : "")));
                    }
                }
            }

            var facets = Arr(rec, "facets").ToList();
            if (facets.Count > 0)
            {
                blocks.Add(new Block(BlockKind.SubHeading,
                    "The land facets (raster value — facet):"));
                foreach (var f in facets)
                {
                    double? fid = D(f, "facet_id");
                    double? count = D(f, "count");
                    blocks.Add(new Block(BlockKind.Bullet, string.Format(
                        CultureInfo.InvariantCulture, "{0} — {1}{2}",
                        fid.HasValue ? fid.Value.ToString("0") : "?",
                        FacetLabel(f) ?? "(unlabelled)",
                        count.HasValue
                            ? "   (" + count.Value.ToString("#,##0") + " cells)"
                            : "")));
                }
            }
        }

        private static void DescribeDensity(List<Block> blocks, Node node,
                                            Func<string, string> fetchXml)
        {
            var rec = node.Record;
            blocks.Add(new Block(HeadKind(node),
                Header(node, "Step 2 — Land Facet Density")));
            AddOrigin(blocks, node);

            var nbr = O(rec, "neighborhood");
            string nbrText = nbr.HasValue
                ? (S(nbr.Value, "description") ?? S(nbr.Value, "shape")) : null;
            blocks.Add(new Block(BlockKind.Body,
                "For every land facet, the proportion of the surrounding "
                + "neighborhood composed of that facet (0–1), one band per "
                + "facet" + (nbrText == null ? "." : ", over a " + nbrText + ".")));

            string denom = S(rec, "denominator");
            if (denom == "all_in_raster_cells")
                blocks.Add(new Block(BlockKind.Body,
                    "The denominator counted every neighborhood cell inside the "
                    + "raster (the original ArcMap rule): NoData cells dilute "
                    + "the proportion, and windows at the raster edge are "
                    + "renormalized to their in-raster part."));
            else if (denom == "non_nodata_cells_only")
                blocks.Add(new Block(BlockKind.Warning,
                    "The denominator counted only cells holding data (NoData "
                    + "ignored) — NOT the original tool's rule; values are not "
                    + "comparable across analyses."));

            double? sf = D(rec, "scale_factor");
            if (sf.HasValue && sf.Value > 0)
                blocks.Add(new Block(BlockKind.Body, string.Format(
                    CultureInfo.InvariantCulture,
                    "Values are stored as integers scaled by {0:#,##0}; divide "
                    + "by {0:#,##0} for the 0–1 proportion. Later tools do this "
                    + "automatically, and treat 100% (not the mean) as density's "
                    + "ideal value in Mahalanobis analyses.", 1.0 / sf.Value)));

            var bands = Arr(rec, "bands").ToList();
            if (bands.Count > 0)
            {
                blocks.Add(new Block(BlockKind.SubHeading,
                    "Band — facet assignments:"));
                foreach (var b in bands)
                {
                    double? bn = D(b, "band");
                    blocks.Add(new Block(BlockKind.Bullet, string.Format(
                        CultureInfo.InvariantCulture, "Band {0} — {1}",
                        bn.HasValue ? bn.Value.ToString("0") : "?",
                        FacetLabel(b) ?? "(unlabelled)")));
                }
            }

            AddSourceCheck(blocks, "Source land facet raster",
                S(rec, "source_facet_raster"), S(rec, "source_analysis_id"),
                Created(rec), fetchXml);
        }

        private static void DescribeMahalanobis(List<Block> blocks, Node node,
                                                Func<string, string> fetchXml)
        {
            var rec = node.Record;
            blocks.Add(new Block(HeadKind(node),
                Header(node, "Step 3 — Land Facet Mahalanobis cost surface")));
            AddOrigin(blocks, node);

            string lab = FacetLabel(O(rec, "facet"));
            double? n = D(rec, "n_sample");
            string blocksPath = S(rec, "wildland_blocks");
            blocks.Add(new Block(BlockKind.Body,
                "A Mahalanobis distance cost (resistance) surface"
                + (lab == null ? "" : " for the facet \"" + lab + "\"")
                + ": how far each cell departs, in multivariate standard "
                + "deviations, from the conditions typical of that facet."));
            blocks.Add(new Block(BlockKind.Body,
                "The mean vector and covariance matrix were computed ONLY from "
                + "the facet's cells inside the Wildland Blocks"
                + (blocksPath == null ? "" : " (" + blocksPath + ")")
                + (n.HasValue
                    ? " — " + n.Value.ToString("#,##0",
                        CultureInfo.InvariantCulture) + " cells" : "")
                + " — while the surface itself covers the whole analysis area, "
                + "because a corridor must cross the ground between the blocks. "
                + "\"Best\" throughout this surface means \"most like what "
                + "exists inside the Wildland Blocks.\""));

            string ot = S(rec, "out_type");
            if (ot != null)
                blocks.Add(new Block(BlockKind.KeyValue, "Cell values", ot));

            var vars = Arr(rec, "variables").ToList();
            if (vars.Count > 0)
                blocks.Add(new Block(BlockKind.Body, "Variables, in order: "
                    + string.Join(", ", vars.Select(v => S(v, "name") ?? "?"))
                    + "."));

            double? db = D(rec, "density_band");
            double? ideal = D(rec, "density_ideal");
            if (db.HasValue)
                blocks.Add(new Block(BlockKind.Body, string.Format(
                    CultureInfo.InvariantCulture,
                    "Density (band {0:0} of the density raster) was included "
                    + "with a characteristic value of {1} rather than its "
                    + "mean — the prototypical cell of a facet is one whose "
                    + "whole neighborhood is that facet. Only the mean was "
                    + "overridden; the covariance kept the observed values.",
                    db.Value,
                    ideal.HasValue ? ideal.Value.ToString("0.##",
                        CultureInfo.InvariantCulture) : "1.0")));

            AddSourceCheck(blocks, "Source land facet raster",
                S(rec, "source_facet_raster"), S(rec, "source_analysis_id"),
                Created(rec), fetchXml);
            string dens = S(rec, "source_density_raster");
            if (!string.IsNullOrEmpty(dens))
                AddSourceCheck(blocks, "Source density raster", dens,
                    S(rec, "analysis_id"), Created(rec), fetchXml);
        }

        private static void DescribeDiversity(List<Block> blocks, Node node,
                                              Func<string, string> fetchXml)
        {
            var rec = node.Record;
            blocks.Add(new Block(HeadKind(node),
                Header(node, "Step 3 (alternative) — Diversity Indices "
                             + "(facet interspersion)")));
            AddOrigin(blocks, node);

            string index = S(rec, "index");
            string nbr = S(rec, "neighborhood");
            blocks.Add(new Block(BlockKind.Body,
                "Neighborhood diversity of the categorical raster: the "
                + (index ?? "diversity index")
                + (nbr == null ? "" : " within a " + nbr)
                + ". High values mark ground where many classes are present in "
                + "balanced amounts."));
            if (B(rec, "exclude_if_any_null") == true)
                blocks.Add(new Block(BlockKind.Body,
                    "Neighborhoods containing any NoData cell were themselves "
                    + "set to NoData."));
            blocks.Add(new Block(BlockKind.Body,
                "In the Land Facet workflow this surface, inverted (Invert "
                + "Raster), becomes the cost surface for the corridor of "
                + "maximum facet interspersion."));

            AddSourceCheck(blocks, "Computed from",
                S(rec, "source_raster"), S(rec, "source_analysis_id"),
                Created(rec), fetchXml);
        }

        private static void DescribeInverted(List<Block> blocks, Node node,
                                             Func<string, string> fetchXml)
        {
            var rec = node.Record;
            blocks.Add(new Block(HeadKind(node),
                Header(node, "Step 4 — Invert Raster (least-cost surface)")));
            AddOrigin(blocks, node);

            double? c = D(rec, "constant");
            string lab = FacetLabel(O(rec, "facet"));
            double? band = D(rec, "band");
            blocks.Add(new Block(BlockKind.Body,
                "Each cell is the reciprocal 1/(x + c)"
                + (c.HasValue
                    ? " with c = " + c.Value.ToString("0.###",
                        CultureInfo.InvariantCulture) : "")
                + ", turning a surface where high values are good into a "
                + "least-cost surface where that good ground is cheap to "
                + "cross."));
            if (lab != null || band.HasValue)
                blocks.Add(new Block(BlockKind.Body,
                    "It was inverted from "
                    + (band.HasValue
                        ? "band " + band.Value.ToString("0",
                            CultureInfo.InvariantCulture) : "a band")
                    + (lab == null ? "" : " (\"" + lab + "\")")
                    + " of the source raster."));
            if (B(rec, "scale_applied") == true)
                blocks.Add(new Block(BlockKind.Body,
                    "Stored density integers were converted back to 0–1 "
                    + "proportions before the constant was added, reproducing "
                    + "the published cost curve."));

            AddSourceCheck(blocks, "Inverted from",
                S(rec, "source_raster"), S(rec, "source_analysis_id"),
                Created(rec), fetchXml);
        }

        private static void DescribeTermini(List<Block> blocks, Node node,
                                            Func<string, string> fetchXml)
        {
            var rec = node.Record;
            blocks.Add(new Block(HeadKind(node),
                Header(node, "Step 5 — Identify Termini Polygons "
                             + "(corridor endpoints)")));
            AddOrigin(blocks, node);

            string blocksPath = S(rec, "wildland_blocks");
            double? nBlocks = D(rec, "n_blocks");
            double? pct = D(rec, "relative_size_pct");
            string mode = S(rec, "threshold_mode");
            double? thr = D(rec, "threshold_value");

            blocks.Add(new Block(BlockKind.Body,
                "The corridor termini: the concentrations of the analysis "
                + "surface inside each Wildland Block"
                + (blocksPath == null ? "" : " (" + blocksPath
                    + (nBlocks.HasValue
                        ? ", " + nBlocks.Value.ToString("0",
                            CultureInfo.InvariantCulture) + " blocks" : "")
                    + ")")
                + " that the least-cost corridors run between. Cells above the "
                + "threshold were aggregated into regions (edge- or corner-"
                + "touching cells connect), each region clipped to its block."));

            if (mode == "block_median")
                blocks.Add(new Block(BlockKind.Body,
                    "Threshold rule: each Wildland Block's own MEDIAN, computed "
                    + "from its own cells — the manual's rule for diversity "
                    + "surfaces (\"the half of all cells inside each Wildland "
                    + "Block with the highest H'\"). Each polygon's Threshold "
                    + "field holds the median its block used."));
            else
                blocks.Add(new Block(BlockKind.Body,
                    "Threshold rule: cells strictly greater than "
                    + (thr.HasValue
                        ? thr.Value.ToString("0.####", CultureInfo.InvariantCulture)
                        : "the fixed value")
                    + (thr.HasValue && thr.Value == 0
                        ? " — any density at all, the manual's recommendation "
                          + "for density surfaces." : ".")));

            if (pct.HasValue)
                blocks.Add(new Block(BlockKind.Body, string.Format(
                    CultureInfo.InvariantCulture,
                    "Within each block (and each category), only regions at "
                    + "least {0:0.#}% of the size of the block's largest "
                    + "region were kept (geodesic areas).", pct.Value)));

            var facets = Arr(rec, "categories").ToList();
            if (facets.Count > 0)
            {
                blocks.Add(new Block(BlockKind.SubHeading,
                    "Termini per category and Wildland Block (the output's "
                    + "Category and Block fields):"));
                int incomplete = 0;
                foreach (var f in facets)
                {
                    double? nt = D(f, "n_termini");
                    string lab = S(f, "label") ?? FacetLabel(f) ?? "(unlabelled)";

                    // Per-block counts (records from 2026-08-08 on carry
                    // "by_block"; older ones degrade to the total alone).
                    var perBlock = new List<(int block, int n)>();
                    foreach (var bb in Arr(f, "by_block"))
                    {
                        double? blk = D(bb, "block");
                        double? n = D(bb, "n_termini");
                        if (blk.HasValue && n.HasValue)
                            perBlock.Add(((int)blk.Value, (int)n.Value));
                    }
                    bool missingSome = perBlock.Any(p => p.n == 0);
                    if (missingSome) incomplete++;

                    string text;
                    if (perBlock.Count > 0 && perBlock.All(p => p.n == 0))
                        text = lab + " — NO termini in any Wildland Block";
                    else
                    {
                        text = lab + (nt.HasValue
                            ? " — " + nt.Value.ToString("0") + " terminus polygon"
                              + (nt.Value == 1 ? "" : "s") : "");
                        if (perBlock.Count > 0)
                            text += " (" + string.Join("; ", perBlock.Select(p =>
                                p.n == 0
                                    ? "NONE in Block " + p.block
                                    : "Block " + p.block + ": " + p.n)) + ")";
                    }
                    // A category missing termini in any block renders in the
                    // alert red -- these are the lines the summary warning
                    // below refers to (Jeff, 2026-08-10).
                    blocks.Add(new Block(
                        missingSome ? BlockKind.BulletAlert : BlockKind.Bullet,
                        text));
                }
                if (incomplete > 0)
                    blocks.Add(new Block(BlockKind.Warning,
                        (incomplete == 1
                            ? "One category lacks"
                            : incomplete + " categories lack")
                        + " termini in one or more Wildland Blocks (marked "
                        + "above) — a corridor for such a category has "
                        + "nothing to connect to in the missing block, so no "
                        + "corridor can be built for it between those "
                        + "blocks."));
            }

            AddSourceCheck(blocks, "Cut from",
                S(rec, "source_raster"), S(rec, "source_analysis_id"),
                Created(rec), fetchXml);
        }

        /// <summary>
        /// A short "what kind of cost surface is this" label, resolved by
        /// reading the raster's OWN linkage record (Mahalanobis vs Invert
        /// Raster). Null if the path is empty or the record can't be read
        /// or recognized -- callers just omit the parenthetical in that
        /// case, they don't treat it as an error.
        /// </summary>
        private static string CostSurfaceKindLabel(string path,
                                                    Func<string, string> fetchXml)
        {
            if (string.IsNullOrWhiteSpace(path)) return null;
            string xml = fetchXml?.Invoke(path);
            if (xml == null) return null;
            var rec = ExtractRecord(xml);
            if (!rec.HasValue) return null;
            string kind = S(rec.Value, "record");
            if (kind == "mahalanobis")
            {
                string ot = S(rec.Value, "out_type");
                return "Mahalanobis distance"
                    + (string.IsNullOrEmpty(ot) ? "" : ", " + ot);
            }
            if (kind == "inverted") return "inverted raster";
            return null;
        }

        private static void DescribeCorridor(List<Block> blocks, Node node,
                                             Func<string, string> fetchXml)
        {
            var rec = node.Record;
            blocks.Add(new Block(HeadKind(node),
                Header(node, "Step 6 — Least-Cost Corridor")));
            AddOrigin(blocks, node);

            var pcts = Arr(rec, "percentiles")
                .Select(p => p.ValueKind == JsonValueKind.Number
                            ? p.GetDouble() : (double?)null)
                .Where(v => v.HasValue).Select(v => v.Value).ToList();
            string pctText = pcts.Count > 0
                ? string.Join(", ", pcts.Select(p => p.ToString("0.##",
                    CultureInfo.InvariantCulture) + "%"))
                : null;

            var corridors = Arr(rec, "corridors").ToList();
            if (corridors.Count > 0)
            {
                // The shared polygon output: every category/block-pair this
                // run produced, in ONE feature class.
                blocks.Add(new Block(BlockKind.Body,
                    "The least-cost corridor polygons between pairs of "
                    + "Wildland Blocks, combining the accumulated cost "
                    + "distance from each block (computed without Spatial "
                    + "Analyst) into a surface that is zero along the "
                    + "literal least-cost path and rises away from it, cut "
                    + "at percentile-width thresholds"
                    + (pctText == null ? "." : " (" + pctText + ").")));
                blocks.Add(new Block(BlockKind.SubHeading,
                    "Corridors in this output (the Category, Block_A and "
                    + "Block_B fields):"));
                foreach (var c in corridors)
                {
                    string lab = S(c, "label") ?? FacetLabel(c) ?? "(unlabelled)";
                    double? ba = D(c, "block_a");
                    double? bb = D(c, "block_b");
                    double? np = D(c, "n_pieces");
                    string entryCostPath = S(c, "cost_raster");
                    string costKind = CostSurfaceKindLabel(entryCostPath, fetchXml);
                    string costText = string.IsNullOrEmpty(entryCostPath) ? "" :
                        " — cost surface: " + Path.GetFileName(entryCostPath)
                        + (costKind == null ? "" : " (" + costKind + ")");
                    blocks.Add(new Block(BlockKind.Bullet, string.Format(
                        CultureInfo.InvariantCulture,
                        "{0} — Wildland Block {1} ↔ {2}{3}{4}", lab,
                        ba.HasValue ? ba.Value.ToString("0") : "?",
                        bb.HasValue ? bb.Value.ToString("0") : "?",
                        np.HasValue
                            ? " (" + np.Value.ToString("0") + " percentile "
                              + "polygon piece" + (np.Value == 1 ? "" : "s")
                              + ")" : "",
                        costText)));
                }
            }
            else
            {
                // A single corridor's own cost raster. The category dict
                // here is this tool's own {"category_id","label"} shape,
                // not the land-facet-clustering shape FacetLabel reads.
                var catObj = O(rec, "category");
                string lab = catObj.HasValue ? S(catObj.Value, "label") : null;
                double? ba = D(rec, "block_a");
                double? bb = D(rec, "block_b");
                blocks.Add(new Block(BlockKind.Body,
                    "A least-cost corridor cost surface"
                    + (lab == null ? "" : " for \"" + lab + "\"")
                    + (ba.HasValue && bb.HasValue
                        ? ", between Wildland Blocks "
                          + ba.Value.ToString("0", CultureInfo.InvariantCulture)
                          + " and "
                          + bb.Value.ToString("0", CultureInfo.InvariantCulture)
                        : "")
                    + ": the accumulated cost distance from each block "
                    + "(computed without Spatial Analyst), combined into a "
                    + "surface that is zero along the literal least-cost "
                    + "path and rises away from it."
                    + (pctText == null ? "" : " Cut into percentile-width "
                        + "polygons at " + pctText + ".")));
            }
            if (B(rec, "geodesic") == true)
                blocks.Add(new Block(BlockKind.Body,
                    "The source cost raster's coordinate system is "
                    + "geographic, so true geodesic ground distances were "
                    + "used rather than planar ones."));

            AddSourceCheck(blocks, "Termini used",
                ResolveSourcePath(S(rec, "source_termini"), node.Path),
                S(rec, "source_analysis_id"),
                Created(rec), fetchXml);
            string costPath = ResolveSourcePath(
                S(rec, "source_cost_raster"), node.Path);
            if (!string.IsNullOrEmpty(costPath))
                AddSourceCheck(blocks, "Cost surface",
                    costPath, S(rec, "source_analysis_id"), Created(rec),
                    fetchXml);
        }

        /// <summary>
        /// A union has MULTIPLE sources, possibly from entirely different
        /// analyses -- unlike every other kind, there is no single linear
        /// ancestor chain to walk (see the ``kind != "union"`` skip in
        /// BuildReport), so this renders its own "Sources used" section,
        /// each with an independent staleness check via the same
        /// AddSourceCheck helper every other kind uses for ONE source.
        /// </summary>
        private static void DescribeUnion(List<Block> blocks, Node node,
                                          Func<string, string> fetchXml)
        {
            var rec = node.Record;
            var sources = Arr(rec, "sources").ToList();
            blocks.Add(new Block(HeadKind(node),
                Header(node, "Union of " + sources.Count + " source"
                    + (sources.Count == 1 ? "" : "s"))));
            AddOrigin(blocks, node);

            blocks.Add(new Block(BlockKind.Body,
                "The geometric union of every source's selected (or all) "
                + "features, dissolved into ONE combined multipart polygon "
                + "-- per-input attributes are NOT preserved; the point is "
                + "a single combined shape answering \"is this ground part "
                + "of ANY corridor\", not which one. A modernized port of "
                + "the CorridorDesigner \"Create union of all corridors\" "
                + "tool."));

            if (B(rec, "mixed_analysis") == true)
            {
                var ids = Arr(rec, "source_analysis_ids")
                    .Select(v => v.ValueKind == JsonValueKind.String ? v.GetString() : null)
                    .Where(v => !string.IsNullOrEmpty(v));
                blocks.Add(new Block(BlockKind.Warning,
                    "These sources carried DIFFERENT Land Facet analysis "
                    + "ids (" + string.Join(", ", ids) + ") -- this union "
                    + "combines genuinely unrelated analyses (different "
                    + "species, different clustering runs), not just "
                    + "different categories within one analysis."));
            }

            blocks.Add(new Block(BlockKind.SubHeading, "Sources used:"));
            int i = 1;
            foreach (var s in sources)
            {
                string path = S(s, "path");
                AddSourceCheck(blocks, "Source " + i++, path,
                    S(s, "analysis_id"), Created(rec), fetchXml);

                string kind2 = S(s, "record_kind");
                string label = S(s, "label");
                blocks.Add(new Block(BlockKind.Note, kind2 == null
                    ? "No Land Facet linkage record."
                    : "Kind: " + kind2
                      + (string.IsNullOrEmpty(label) ? "" : " (\"" + label + "\")")
                      + "."));

                double? nTotal = D(s, "n_total");
                double? nSel = D(s, "n_selected");
                bool selected = B(s, "selected") == true;
                if (nTotal.HasValue && nSel.HasValue)
                    blocks.Add(new Block(BlockKind.Note, selected
                        ? string.Format(CultureInfo.InvariantCulture,
                            "{0:0} of {1:0} features were selected and used.",
                            nSel.Value, nTotal.Value)
                        : string.Format(CultureInfo.InvariantCulture,
                            "All {0:0} features were used (no selection "
                            + "was active).", nTotal.Value)));
            }
        }

        /// <summary>
        /// The habitat-suitability-model kind, written by the CorridorDesigner
        /// HSM chain (hsm_tools.py). Four shapes share the one kind, told
        /// apart by "method": "geometric"/"additive" from the Habitat
        /// Suitability Model and Combine Habitat Factors tools (with a
        /// "factors" list of {path, weight[, remap_file]}), "normalized" from
        /// Normalize Existing HSM (anchors in "params"), and "external" for a
        /// record-less raster adopted into the chain when a Modify HSM tool
        /// first touched it. The Modify tools themselves only append
        /// post_processing steps, rendered generically by
        /// AddPostProcessingSteps after this section.
        /// </summary>
        private static void DescribeHsm(List<Block> blocks, Node node,
                                        Func<string, string> fetchXml)
        {
            var rec = node.Record;
            string tool = S(rec, "tool");
            string method = S(rec, "method");

            string title = "Habitat Suitability Model";
            if (method == "external") title += " (externally built)";
            else if (!string.IsNullOrEmpty(tool)
                     && tool != "Habitat Suitability Model")
                title += " — " + tool;
            blocks.Add(new Block(HeadKind(node), Header(node, title)));
            AddOrigin(blocks, node);

            var factors = Arr(rec, "factors").ToList();
            if (method == "geometric" || method == "additive")
            {
                bool preScored = tool == "Combine Habitat Factors";
                blocks.Add(new Block(BlockKind.Body,
                    "A habitat suitability surface on the CorridorDesigner "
                    + "0–100 scale (100 = best habitat), built by combining "
                    + (preScored
                        ? "habitat factor rasters already scored on 0–100"
                        : "habitat factor rasters, each first scored onto "
                          + "0–100 with its remap table")
                    + " as a weighted " + method + " mean"
                    + (method == "geometric"
                        ? " — each factor's score raised to the power "
                          + "weight/100 and multiplied together, so a score "
                          + "of 0 in ANY factor makes the cell 0 overall: "
                          + "ground unsuitable in one factor is unsuitable, "
                          + "period."
                        : " — each factor's score times weight/100, "
                          + "summed.")));
            }
            else if (method == "normalized")
            {
                var p = O(rec, "params");
                double? worst = p.HasValue ? D(p.Value, "worst") : null;
                double? best = p.HasValue ? D(p.Value, "best") : null;
                bool? clamp = p.HasValue ? B(p.Value, "clamp") : null;
                blocks.Add(new Block(BlockKind.Body,
                    "A habitat suitability model built outside the HSM "
                    + "tools, linearly stretched onto the CorridorDesigner "
                    + "0–100 scale (100 = best habitat) so it can feed the "
                    + "Modify HSM and corridor tools"
                    + (worst.HasValue && best.HasValue
                        ? string.Format(CultureInfo.InvariantCulture,
                            ": the source value {0:0.####} was anchored to "
                            + "0 (worst habitat) and {1:0.####} to 100 "
                            + "(best).", worst.Value, best.Value)
                        : ".")));
                if (worst.HasValue && best.HasValue && worst.Value > best.Value)
                    blocks.Add(new Block(BlockKind.Body,
                        "The anchors are reversed (worst > best), so the "
                        + "model's sense was inverted: LOW source values "
                        + "became high suitability."));
                if (clamp == true)
                    blocks.Add(new Block(BlockKind.Body,
                        "Source values beyond the anchors were clamped to "
                        + "0–100."));
                else if (clamp == false)
                    blocks.Add(new Block(BlockKind.Warning,
                        "Source values beyond the anchors were NOT clamped "
                        + "— this surface can hold values outside 0–100, "
                        + "which the downstream HSM tools do not expect."));
            }
            else if (method == "external")
            {
                blocks.Add(new Block(BlockKind.Body,
                    "This raster was not built by the HSM tools and carried "
                    + "no analysis record when "
                    + (string.IsNullOrEmpty(tool) ? "a Modify HSM tool" : tool)
                    + " modified it, so it was adopted into the chain as an "
                    + "externally built habitat suitability model, assumed "
                    + "to already be on the 0–100 (100 = best) scale."));
            }
            else
            {
                blocks.Add(new Block(BlockKind.Body,
                    "A habitat suitability surface on the CorridorDesigner "
                    + "0–100 scale (100 = best habitat)."));
            }

            if (factors.Count > 0)
            {
                blocks.Add(new Block(BlockKind.SubHeading,
                    "Habitat factors and weights:"));
                foreach (var f in factors)
                {
                    string fPath = S(f, "path") ?? "(unnamed factor)";
                    double? w = D(f, "weight");
                    string remap = S(f, "remap_file");
                    blocks.Add(new Block(BlockKind.Bullet, fPath
                        + (w.HasValue
                            ? "  — weight " + w.Value.ToString("0.##",
                                CultureInfo.InvariantCulture) + "%" : "")
                        + (string.IsNullOrEmpty(remap)
                            ? "" : ", scored with " + remap)));
                }
                if (factors.Count > 1)
                    blocks.Add(new Block(BlockKind.Note,
                        "The first factor defined the output grid; the "
                        + "others were resampled to it where their grids "
                        + "differed."));
            }

            // The standing cost-surface guidance: an HSM is high-is-good,
            // so the route to the corridor tools runs through Invert Raster,
            // exactly like a density or diversity surface.
            blocks.Add(new Block(BlockKind.Body,
                "An HSM is high-is-good, NOT a cost surface: run it through "
                + "Invert Raster before Least-Cost Corridor, and cut the "
                + "corridor termini (Identify Termini Polygons) from the "
                + "HSM itself, not from the inverted surface."));

            string src = S(rec, "source_raster");
            if (!string.IsNullOrEmpty(src))
                AddSourceCheck(blocks,
                    method == "normalized" ? "Normalized from"
                                           : "Original raster",
                    src, S(rec, "source_analysis_id"), Created(rec),
                    fetchXml);
        }

        /// <summary>
        /// The patch_map kind, written by Create Habitat Patches
        /// (the CorridorDesigner PatchConfiguration port): the HSM averaged
        /// over a moving window, thresholded, grouped into 8-connected
        /// patches, each classified by geodesic area as too-small /
        /// breeding / population. "params" carries window/threshold/hectare
        /// settings; "classes" is a 3-entry per-class summary.
        /// </summary>
        private static void DescribePatchMap(List<Block> blocks, Node node,
                                             Func<string, string> fetchXml)
        {
            var rec = node.Record;
            blocks.Add(new Block(HeadKind(node),
                Header(node, "Habitat Patches")));
            AddOrigin(blocks, node);

            var p = O(rec, "params");
            string window = p.HasValue ? S(p.Value, "window") : null;
            double? thr = p.HasValue ? D(p.Value, "threshold") : null;
            double? bHa = p.HasValue ? D(p.Value, "breeding_ha") : null;
            double? pHa = p.HasValue ? D(p.Value, "population_ha") : null;
            blocks.Add(new Block(BlockKind.Body,
                "Polygons of contiguous suitable habitat, classified by "
                + "whether each patch is large enough to support breeding "
                + "or a population: the habitat model was averaged over a "
                + "circular moving window"
                + (window == null ? "" : " of " + window)
                + (thr.HasValue
                    ? string.Format(CultureInfo.InvariantCulture,
                        ", cells averaging at least {0:0.##} became "
                        + "suitable habitat", thr.Value)
                    : "")
                + ", touching cells (edges or corners) were grouped into "
                + "patches, and each patch was classified by its geodesic "
                + "area"
                + (bHa.HasValue && pHa.HasValue
                    ? string.Format(CultureInfo.InvariantCulture,
                        " (breeding patch ≥ {0:0.##} ha, population "
                        + "patch ≥ {1:0.##} ha).", bHa.Value, pHa.Value)
                    : ".")));

            var classes = Arr(rec, "classes").ToList();
            if (classes.Count > 0)
            {
                blocks.Add(new Block(BlockKind.SubHeading,
                    "Patches by class:"));
                foreach (var c in classes)
                {
                    string nm = S(c, "name") ?? "(unnamed class)";
                    double? n = D(c, "n_patches");
                    double? ha = D(c, "area_ha");
                    bool emptyPop = nm == "Population patch"
                        && n.HasValue && n.Value == 0;
                    blocks.Add(new Block(
                        emptyPop ? BlockKind.BulletAlert : BlockKind.Bullet,
                        string.Format(CultureInfo.InvariantCulture,
                            "{0} — {1} patch{2}{3}", nm,
                            n.HasValue ? n.Value.ToString("#,##0") : "?",
                            n.HasValue && n.Value == 1 ? "" : "es",
                            ha.HasValue
                                ? ", " + ha.Value.ToString("#,##0.#")
                                  + " ha total" : "")));
                    if (emptyPop)
                        blocks.Add(new Block(BlockKind.Warning,
                            "NO patch reaches the population size — under "
                            + "these settings the landscape holds no patch "
                            + "large enough for a population."));
                }
            }

            AddSourceCheck(blocks, "Built from",
                S(rec, "source_raster"), S(rec, "source_analysis_id"),
                Created(rec), fetchXml);
        }

        // ---- staleness ------------------------------------------------------
        private static void AddSourceCheck(List<Block> blocks, string label,
                                           string srcPath, string expectedId,
                                           DateTime? childCreated,
                                           Func<string, string> fetchXml)
        {
            if (string.IsNullOrWhiteSpace(srcPath)) return;
            blocks.Add(new Block(BlockKind.KeyValue, label, srcPath));

            string xml = fetchXml?.Invoke(srcPath);
            if (xml == null)
            {
                blocks.Add(new Block(BlockKind.Note,
                    "That source could not be found (or its metadata could not "
                    + "be read) — it may have been moved, renamed or deleted "
                    + "since this dataset was made."));
                return;
            }
            var srcRec = ExtractRecord(xml);
            if (!srcRec.HasValue)
            {
                // Three different situations, three different sentences. Only
                // when a chained analysis id was RECORDED at derivation time is
                // "no longer" accurate; a record-less Jenness product (e.g. an
                // output of another tool in the suite) simply never had one,
                // and its own provenance summary is the useful thing to show.
                if (LooksLikeJennessProduct(xml))
                {
                    string sum = ExtractSummary(xml);
                    blocks.Add(new Block(BlockKind.Note,
                        "The source was not made by the Land Facet chain tools, "
                        + "but it is a Jenness Enterprises product"
                        + (string.IsNullOrWhiteSpace(sum)
                            ? "." : ". Its own provenance summary reads: \""
                                    + sum + "\"")));
                }
                else if (!string.IsNullOrEmpty(expectedId))
                {
                    blocks.Add(new Block(BlockKind.Warning,
                        "The source carried a Land Facet record when this "
                        + "dataset was made, but NO LONGER does — it has "
                        + "probably been overwritten by something else. This "
                        + "dataset may be out of date."));
                }
                else
                {
                    blocks.Add(new Block(BlockKind.Note,
                        "The source exists but carries no Land Facet analysis "
                        + "record."));
                }
                return;
            }
            string nowId = S(srcRec.Value, "analysis_id");
            if (!string.IsNullOrEmpty(expectedId) && !string.IsNullOrEmpty(nowId)
                && !string.Equals(expectedId, nowId, StringComparison.Ordinal))
            {
                blocks.Add(new Block(BlockKind.Warning,
                    "STALE? The dataset now at that path records a DIFFERENT "
                    + "analysis than the one this dataset was built from — the "
                    + "source has been regenerated since. This dataset may be "
                    + "out of date; consider re-running the tool that made it."));
                return;
            }
            var srcCreated = Created(srcRec.Value);
            if (childCreated.HasValue && srcCreated.HasValue
                && srcCreated.Value > childCreated.Value)
                blocks.Add(new Block(BlockKind.Warning,
                    "STALE? The source at that path was written AFTER this "
                    + "dataset was created. This dataset may be out of date; "
                    + "consider re-running the tool that made it."));
        }
    }
}
