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

using ArcGIS.Core.CIM;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;
using System;
using System.Linq;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools
{
    /// <summary>
    /// Cartography-group ribbon button (layout-only): moves Esri's intrusive Service
    /// Layer Credits text off the page. When a layout contains a Service Layer Credits
    /// DYNAMIC TEXT element, Pro routes the credits into that element instead of
    /// stamping them inside the map frame — so this tool creates one parked below the
    /// page (top edge 0.5 inches below the page bottom, left edge flush with the page
    /// left edge), where it never prints. If such an element already exists on the
    /// layout, it is repositioned there instead of duplicated. The layout view is
    /// refreshed afterward so the in-frame credits disappear immediately.
    /// </summary>
    internal sealed class RemoveSlcButton : Button
    {
        /// <summary>The dynamic-text tag Pro resolves to the service layer credits.</summary>
        private const string SlcDynamicText =
            "<dyn type=\"layout\" property=\"serviceLayerCredits\"/>";

        protected override async void OnClick()
        {
            try
            {
                await QueuedTask.Run(() =>
                {
                    LayoutView lv = LayoutView.Active;
                    if (lv?.Layout is not Layout layout)
                        throw new InvalidOperationException("No active layout view.");

                    CIMPage page = layout.GetPage();
                    double metersPerPageUnit = 0.0254;
                    try { metersPerPageUnit = page.Units.ConversionFactor; } catch { }
                    double halfInch = 0.5 * 0.0254 / metersPerPageUnit;   // 0.5 in -> page units
                    double oneInch = 2.0 * halfInch;

                    // An existing Service Layer Credits dynamic text element? Reposition it.
                    GraphicElement existing = layout.GetElementsAsFlattenedList()
                        .OfType<GraphicElement>()
                        .FirstOrDefault(ge =>
                        {
                            try
                            {
                                return ge.GetGraphic() is CIMTextGraphic tg &&
                                       tg.Text != null &&
                                       tg.Text.Contains("serviceLayerCredits", StringComparison.OrdinalIgnoreCase);
                            }
                            catch { return false; }
                        });

                    if (existing != null)
                    {
                        // Top-left of the element: page left edge, half an inch below the
                        // page bottom (page y = 0 at the bottom edge).
                        existing.SetAnchor(Anchor.TopLeftCorner);
                        existing.SetAnchorPoint(new Coordinate2D(0.0, -halfInch));
                    }
                    else
                    {
                        // Park a new SLC dynamic text box below the page: top at -0.5 in,
                        // left flush with the page, page-wide and an inch tall.
                        Envelope env = EnvelopeBuilderEx.CreateEnvelope(
                            0.0, -halfInch - oneInch, page.Width, -halfInch);
                        CIMTextSymbol txtSym = SymbolFactory.Instance.ConstructTextSymbol(
                            ColorFactory.Instance.BlackRGB, 8, "Tahoma", "Regular");
                        ElementFactory.Instance.CreateTextGraphicElement(
                            layout, TextType.RectangleParagraph, env, txtSym,
                            SlcDynamicText, "Service Layer Credits", false, null);
                    }

                    // Make the in-frame credits disappear right away.
                    lv.Refresh();
                });
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not relocate the service layer credits:" + Environment.NewLine + ex.Message,
                    "Remove SLC");
            }
        }
    }
}
