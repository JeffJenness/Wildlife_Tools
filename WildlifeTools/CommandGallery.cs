using System.Windows.Input;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;

namespace WildlifeTools
{
    /// <summary>
    /// Base class for the add-in's in-ribbon galleries (the control behind
    /// Esri's Analysis > Tools group). Derived classes supply titled
    /// sections of existing DAML command IDs; items are built from those
    /// buttons' own definitions via the framework's plugin wrappers, so
    /// captions, icons and tooltips live in one place, and clicking an
    /// item executes the same command as the original button.
    /// </summary>
    internal abstract class CommandGallery : Gallery
    {
        /// <summary>A gallery item that remembers its DAML command. The
        /// framework wrapper is exposed for the item template: binding
        /// IsEnabled to Command.Enabled lets condition-gated buttons
        /// (e.g. Remove SLC's layout-only condition) grey out in the
        /// gallery exactly as they would on the ribbon, updating live as
        /// the wrapper raises property-change notifications.</summary>
        private class CommandGalleryItem : GalleryItem
        {
            public string CommandId { get; }

            public IPlugInWrapper Command { get; }

            public CommandGalleryItem(string commandId, IPlugInWrapper wrapper)
                : base(wrapper.Caption, wrapper.LargeImage, wrapper.Tooltip)
            {
                CommandId = commandId;
                Command = wrapper;
            }
        }

        protected CommandGallery((string Group, string[] Ids)[] sections)
        {
            // Gallery items are COMMANDS, not a persistent choice: without
            // this, the framework latches the clicked item as "selected"
            // and a second click on the same item never fires (Jeff hit
            // this re-running the Rectangle background tool).
            AlwaysFireOnClick = true;

            foreach (var (group, ids) in sections)
            {
                foreach (string id in ids)
                {
                    var wrapper = FrameworkApplication.GetPlugInWrapper(id);
                    if (wrapper == null)
                    {
                        continue;
                    }

                    Add(new CommandGalleryItem(id, wrapper)
                    {
                        Group = group,
                    });
                }
            }
        }

        protected override void OnClick(GalleryItem item)
        {
            ClearSelection();
            if (item is CommandGalleryItem cmdItem
                && FrameworkApplication.GetPlugInWrapper(cmdItem.CommandId)
                    is ICommand command
                && command.CanExecute(null))
            {
                command.Execute(null);
            }
        }
    }

    /// <summary>
    /// The Home Range group's gallery: LoCoH, Kernel Density and
    /// Mahalanobis tools, one titled row per classic menu grouping.
    /// </summary>
    internal class HomeRangeGallery : CommandGallery
    {
        public HomeRangeGallery() : base(new[]
        {
            ("Local Convex Hulls", new[]
            {
                "WildlifeTools_LoCoHStandard",
                "WildlifeTools_LoCoHAdaptive",
                "WildlifeTools_LoCoHFixedSphere",
                "WildlifeTools_LocohParameterExplorer",
            }),
            ("Kernel Density Tools", new[]
            {
                "WildlifeTools_KernelDensityGenerate",
                "WildlifeTools_KernelDensityProportion",
                "WildlifeTools_KernelDensityContours",
            }),
            ("Mahalanobis Tools", new[]
            {
                "WildlifeTools_MahalanobisRaster",
                "WildlifeTools_MahalanobisPoints",
                "WildlifeTools_MahalanobisCovariance",
                "WildlifeTools_MahalanobisChiSquare",
            }),
        })
        {
        }
    }

    /// <summary>
    /// The Geometric Tools group's gallery, with Jeff's three thematic
    /// rows: tessellations, random-sample generators, and tools that
    /// derive new geometry from existing geometry.
    /// </summary>
    internal class GeometricGallery : CommandGallery
    {
        public GeometricGallery() : base(new[]
        {
            ("Tessellations", new[]
            {
                "WildlifeTools_Voronoi",
                "WildlifeTools_AdaptiveVoronoi",
                "WildlifeTools_RepeatingShapes",
            }),
            ("Random Samples", new[]
            {
                "WildlifeTools_RandomPoints",
                "WildlifeTools_RandomArrays",
                "WildlifeTools_GrtsSample",
            }),
            ("Geometry on Geometry", new[]
            {
                "WildlifeTools_Centerline",
                "WildlifeTools_LongestLine",
                "WildlifeTools_RadiatingLines",
                "WildlifeTools_InscribedGeometry",
                "WildlifeTools_ConcaveHull",
                "WildlifeTools_EstShortestPath",
            }),
        })
        {
        }
    }

    /// <summary>
    /// The Ecological Analysis group's gallery. One row so far — the
    /// Kappa accuracy-assessment suite — with more ecological analysis
    /// tools planned for future rows.
    /// </summary>
    internal class EcologicalGallery : CommandGallery
    {
        public EcologicalGallery() : base(new[]
        {
            ("Kappa Tools", new[]
            {
                "WildlifeTools_ClassAccuracy",
                "WildlifeTools_CompareAccuracy",
                "WildlifeTools_AccSampleSize",
            }),
        })
        {
        }
    }

    /// <summary>
    /// The Cartography group's gallery: hillshades, graphic backgrounds
    /// and the standalone cartographic helpers. The Add Graphics and
    /// North Arrows tool palettes stay OUT of the gallery (map/layout
    /// placement tools cannot be gallery items) and sit beside it in
    /// the group.
    /// </summary>
    internal class CartographyGallery : CommandGallery
    {
        public CartographyGallery() : base(new[]
        {
            ("Hillshade Variations", new[]
            {
                "WildlifeTools_EnhancedHillshade",
                "WildlifeTools_MDOWHillshade",
                "WildlifeTools_MultiSunHillshade",
                "WildlifeTools_BlurredHillshade",
                "WildlifeTools_SimpleHypsometricShading",
            }),
            ("Graphic Backgrounds", new[]
            {
                "WildlifeTools_AddGraphicCircle",
                "WildlifeTools_RectangleBackground",
                "WildlifeTools_PolygonBackground",
                "WildlifeTools_McpBackground",
            }),
            ("Cartographic Helpers", new[]
            {
                "WildlifeTools_RemoveSlc",
                "WildlifeTools_ShadowPolygons",
            }),
        })
        {
        }
    }

    /// <summary>
    /// The Corridor Designer group's gallery: the five classic menu
    /// groupings as titled rows. The Corridor Analysis Data Report stays
    /// OUT of the gallery, as its own button beside it in the group.
    /// </summary>
    internal class CorridorDesignerGallery : CommandGallery
    {
        public CorridorDesignerGallery() : base(new[]
        {
            ("Habitat Suitability Modeling", new[]
            {
                "WildlifeTools_HsmRemapBuilder",
                "WildlifeTools_HabitatSuitabilityModel",
                "WildlifeTools_CombineHabitatFactors",
                "WildlifeTools_NormalizeExistingHSM",
                "WildlifeTools_ReclassifyFeaturesInHSM",
                "WildlifeTools_CriticalHabitatFeature",
                "WildlifeTools_HabitatPatches",
            }),
            ("Land Facet Corridor Modeling", new[]
            {
                "WildlifeTools_LandFacet",
                "WildlifeTools_LandFacetDensity",
                "WildlifeTools_LandFacetMahalanobis",
            }),
            ("Build and Modify Corridors", new[]
            {
                "WildlifeTools_IdentifyTermini",
                "WildlifeTools_LeastCostCorridor",
                "WildlifeTools_FillCorridorHoles",
                "WildlifeTools_UnionCorridors",
            }),
            ("Evaluate Corridors", new[]
            {
                "WildlifeTools_PatchAnalysis",
                "WildlifeTools_Bottleneck",
                "WildlifeTools_WeightedSummaryStatistics",
                "WildlifeTools_Histogram",
            }),
            ("Ancillary Corridor Tools", new[]
            {
                "WildlifeTools_DiversityIndices",
                "WildlifeTools_InvertRaster",
                "WildlifeTools_ClipData",
                "WildlifeTools_Crosstab",
                "WildlifeTools_CumulativeSurface",
            }),
        })
        {
        }
    }

    /// <summary>
    /// The Topographic Analysis group's gallery: the Roughness, TPI and
    /// Aspect families plus the standalone wetness/slope/extremes tools,
    /// one titled row per classic ribbon grouping.
    /// </summary>
    internal class TopographicGallery : CommandGallery
    {
        public TopographicGallery() : base(new[]
        {
            ("Topographic Roughness", new[]
            {
                "WildlifeTools_SurfaceAreaRatio",
                "WildlifeTools_VRM",
                "WildlifeTools_ACR",
                "WildlifeTools_TRI",
                "WildlifeTools_FractalDimension",
                "WildlifeTools_LSRIOnDEM",
                "WildlifeTools_LSRIOnContourPolylines",
                "WildlifeTools_TopographyAlongLines",
                "WildlifeTools_SplitLinesIntoSegments",
            }),
            ("Wetness, Slope and Extreme Values", new[]
            {
                "WildlifeTools_TopographicWetnessIndex",
                "WildlifeTools_SlopeZonalStatistics",
                "WildlifeTools_FindRasterExtremes",
            }),
            ("TPI Tools", new[]
            {
                "WildlifeTools_SimpleTPI",
                "WildlifeTools_SlopePosition",
                "WildlifeTools_Landform",
                "WildlifeTools_GeneralClassification",
                "WildlifeTools_ClassificationBuilder",
            }),
            ("Aspect Tools", new[]
            {
                "WildlifeTools_AspectFocalStatistics",
                "WildlifeTools_ExtractAspectValuesToPoints",
                "WildlifeTools_AspectTransformation",
                "WildlifeTools_AspectZonalStatistics",
            }),
        })
        {
        }
    }
}
