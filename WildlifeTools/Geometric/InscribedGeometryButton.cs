using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Geometric
{
    /// <summary>
    /// Geometric Tools ribbon button that opens the Maximum Inscribed
    /// Geometry geoprocessing tool from the bundled Jenness
    /// Enterprises Python toolbox: the largest circle, rectangle,
    /// convex polygon (MCP) or ellipse fitting entirely inside each
    /// polygon -- the dual of Esri's Minimum Bounding Geometry.
    /// </summary>
    internal class InscribedGeometryButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath =
                    ToolboxPaths.ToolPath("MaximumInscribedGeometry");
                if (toolPath == null)
                {
                    MessageBox.Show(
                        "Could not find JennessEnterprisesTools.pyt next to the add-in.",
                        "Wildlife Tools");
                    return;
                }

                Geoprocessing.OpenToolDialog(
                    toolPath, Geoprocessing.MakeValueArray());
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the Maximum Inscribed Geometry tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
