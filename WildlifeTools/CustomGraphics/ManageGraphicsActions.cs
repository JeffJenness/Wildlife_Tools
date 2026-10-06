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

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.CustomGraphics
{
    /// <summary>
    /// The things the Manage Custom Graphics and North Arrows window does that
    /// need ArcGIS Pro: its message boxes, the help page, drawing a vector
    /// image's preview, and stamping a focal point on the selected element.
    /// Kept apart from <see cref="ManageGraphicsPanel"/> so the panel can be
    /// built and drawn outside Pro; each of these runs only from a click.
    /// </summary>
    internal static class ManageGraphicsActions
    {
        private const string Title = "Manage Custom Graphics and North Arrows";

        public static void Tell(string message) => MessageBox.Show(message, Title);

        public static bool Ask(string message) =>
            MessageBox.Show(message, Title, MessageBoxButton.YesNo, MessageBoxImage.Question)
                == MessageBoxResult.Yes;

        public static void OpenHelp() =>
            WebHelp.Open("cartography/manage-custom-graphics-and-north-arrows.html");

        /// <summary>
        /// Replaces a just-registered vector image's placeholder preview with
        /// ArcGIS Pro's drawing of it. Fire and forget: when the drawing is
        /// ready the library announces the change and the gallery and the
        /// window pick up the new picture; if Pro cannot draw it, the
        /// placeholder simply stays.
        /// </summary>
        public static async void DrawVectorPreview(GraphicKind kind, GraphicEntry entry)
        {
            try
            {
                string name = entry.Id + "_preview_pro.png";
                string path = Path.Combine(GraphicLibrary.StoreFolder, name);
                if (await GraphicPreviewRenderer.RenderAsync(
                        GraphicLibrary.FilePath(entry), entry.Aspect, path))
                {
                    GraphicLibrary.SetPreview(kind, entry, name);
                }
            }
            catch
            {
                // the placeholder stays
            }
        }

        /// <summary>
        /// Stamps a focal point on every element selected on the active layout
        /// or, failing a layout, in the active map's graphics layers. Returns
        /// a sentence saying what happened.
        /// </summary>
        public static Task<string> StampSelectedAsync(double fx, double fy)
        {
            return QueuedTask.Run(() =>
            {
                var elements = new List<Element>();
                string where;
                if (LayoutView.Active is LayoutView lv)
                {
                    where = "layout";
                    elements.AddRange(lv.GetSelectedElements());
                }
                else if (MapView.Active?.Map is Map map)
                {
                    where = "map";
                    foreach (GraphicsLayer gl in map.GetLayersAsFlattenedList().OfType<GraphicsLayer>())
                    {
                        elements.AddRange(gl.GetSelectedElements());
                    }
                }
                else
                {
                    return "Open a map or a layout, select an element on it, and click again.";
                }
                if (elements.Count == 0)
                {
                    return "Nothing is selected on the active " + where
                        + ". Select a graphic or a north arrow there, then click again.";
                }
                foreach (Element el in elements)
                {
                    CompassRose.StampFocalPoint(el, fx, fy);
                }
                string at = string.Format(CultureInfo.InvariantCulture, "X {0:0.###}, Y {1:0.###}", fx, fy);
                return elements.Count == 1
                    ? "Focal point (" + at + ") applied to \"" + elements[0].Name + "\"."
                    : "Focal point (" + at + ") applied to " + elements.Count + " selected elements.";
            });
        }
    }
}
