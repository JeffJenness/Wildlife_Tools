using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Xml;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Reads the machine-readable Land Facet linkage records out of dataset
    /// metadata and turns them into the human-readable "Describe Land Facet
    /// Background" report.
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
            Title, Heading, SubHeading, Body, Bullet, KeyValue, Warning, Note,
            ChainStep, ChainStepCurrent, Separator,
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
                new Block(BlockKind.Title, "Land Facet Background"),
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
            string kind = S(rec.Value, "record") ?? "";
            blocks.Add(new Block(BlockKind.Heading,
                "Where this dataset sits in the analysis"));
            AddChainMap(blocks, kind);
            blocks.Add(new Block(BlockKind.Separator, ""));

            // ---- one section per ancestor, root first ---------------------
            foreach (var node in nodes)
                DescribeNode(blocks, node, fetchXml);

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
                 + "surface"),
                (Array.Empty<string>(),
                 "5.  Least-cost corridor modelling (CorridorDesigner / "
                 + "Cost Distance)"),
            };
            foreach (var (stepKeys, text) in steps)
                blocks.Add(new Block(
                    stepKeys.Contains(kind)
                        ? BlockKind.ChainStepCurrent : BlockKind.ChainStep,
                    text));
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
            "mahalanobis" => 2, "diversity" => 2,
            "inverted" => 3,
            _ => 4,
        };

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
                foreach (var (srcPath, via2) in SourcePaths(rec))
                {
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
            blocks.Add(new Block(BlockKind.Heading,
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
            blocks.Add(new Block(BlockKind.Heading,
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
            blocks.Add(new Block(BlockKind.Heading,
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
            blocks.Add(new Block(BlockKind.Heading,
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
            blocks.Add(new Block(BlockKind.Heading,
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
