using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// The five CorridorDesigner habitat-suitability-model (HSM) chain
    /// buttons, each opening its geoprocessing tool from the bundled Jenness
    /// Enterprises Python toolbox. Original CorridorDesigner tools by Dan
    /// Majka (primary author); these ArcGIS Pro versions by Jeff Jenness,
    /// Dan Majka and Paul Beier. The tools live in the .pyt so the same
    /// logic is available
    /// in ModelBuilder and the Geoprocessing pane, with no Spatial Analyst.
    /// </summary>
    internal abstract class HsmToolButtonBase : Button
    {
        /// <summary>The .pyt tool class name this button opens.</summary>
        protected abstract string ToolName { get; }

        /// <summary>Human name used in error dialogs.</summary>
        protected abstract string DisplayName { get; }

        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath(ToolName);
                if (toolPath == null)
                {
                    MessageBox.Show(
                        "Could not find JennessEnterprisesTools.pyt next to the add-in.",
                        "Wildlife Tools");
                    return;
                }

                Geoprocessing.OpenToolDialog(toolPath, Geoprocessing.MakeValueArray());
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the " + DisplayName + " tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }

    /// <summary>HSM 1: reclassify factor rasters (ASCII remap files) and
    /// combine by weighted geometric or additive mean.</summary>
    internal class HabitatSuitabilityModelButton : HsmToolButtonBase
    {
        protected override string ToolName => "HabitatSuitabilityModel";
        protected override string DisplayName => "Create Habitat Suitability Model";
    }

    /// <summary>HSM 2: weighted combine of already-reclassified (0-100)
    /// factors, for iterating on weights without redoing the reclass.</summary>
    internal class CombineHabitatFactorsButton : HsmToolButtonBase
    {
        protected override string ToolName => "CombineHabitatFactors";
        protected override string DisplayName => "Combine Habitat Factors";
    }

    /// <summary>HSM 3: linear stretch of an externally built HSM onto the
    /// 0-100 suitability scale.</summary>
    internal class NormalizeExistingHsmButton : HsmToolButtonBase
    {
        protected override string ToolName => "NormalizeExistingHSM";
        protected override string DisplayName => "Normalize Existing HSM";
    }

    /// <summary>Modify HSM 1: burn polygon features into an HSM at a fixed
    /// suitability score.</summary>
    internal class ReclassifyFeaturesInHsmButton : HsmToolButtonBase
    {
        protected override string ToolName => "ReclassifyFeaturesInHSM";
        protected override string DisplayName => "Reclassify Features in HSM";
    }

    /// <summary>Modify HSM 2: scale an HSM by distance-band percentages from
    /// a critical habitat feature.</summary>
    internal class CriticalHabitatFeatureButton : HsmToolButtonBase
    {
        protected override string ToolName => "CriticalHabitatFeature";
        protected override string DisplayName => "Critical Habitat Feature";
    }

    /// <summary>The finished model's payoff dataset: patches of contiguous
    /// suitable habitat classified by whether each is large enough to
    /// support breeding or a population (the CorridorDesigner Patch
    /// Configuration port).</summary>
    internal class HabitatPatchesButton : HsmToolButtonBase
    {
        protected override string ToolName => "HabitatPatches";
        protected override string DisplayName => "Create Habitat Patches";
    }

    /// <summary>Data preparation: batch-clip rasters and feature classes to
    /// the analysis area, with optional reprojection and per-datum
    /// geographic transformations (the CorridorDesigner ClipAnalysisLayers
    /// port; the base class is just the shared open-a-.pyt-tool button).</summary>
    internal class ClipDataToAnalysisAreaButton : HsmToolButtonBase
    {
        protected override string ToolName => "ClipDataToAnalysisArea";
        protected override string DisplayName => "Clip Data to Analysis Area";
    }

    internal class PatchAnalysisButton : HsmToolButtonBase
    {
        protected override string ToolName => "PatchAnalysis";
        protected override string DisplayName => "Patch Analysis";
    }

    /// <summary>Evaluate Corridors 3: record + length/area-weighted
    /// statistics of numeric fields -- the per-unit-landscape description
    /// of an attribute (the CorridorDesigner Summary Statistics
    /// weighted-family port).</summary>
    internal class WeightedSummaryStatisticsButton : HsmToolButtonBase
    {
        protected override string ToolName => "WeightedSummaryStatistics";
        protected override string DisplayName => "Weighted Summary Statistics";
    }
}
