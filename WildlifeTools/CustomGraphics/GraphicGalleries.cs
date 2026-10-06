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
using System.Windows;
using System.Windows.Media;
using System.Windows.Threading;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Layouts;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.CustomGraphics
{
    /// <summary>One thumbnail in the Add Graphics or North Arrows gallery. The
    /// item template (GalleryTemplates.xaml, WildlifeGraphicGalleryItem) binds
    /// <see cref="GalleryItem.Thumbnail"/>, <see cref="GalleryItem.Text"/> and
    /// <see cref="GalleryItem.Tooltip"/>.</summary>
    internal sealed class GraphicGalleryItem : GalleryItem
    {
        public GraphicEntry Entry { get; }

        public GraphicGalleryItem(GraphicEntry entry, ImageSource picture, string tooltip, string group)
            : base(entry.Name, (object)picture, tooltip, group)
        {
            Entry = entry;
            Thumbnail = picture;
        }
    }

    /// <summary>
    /// The dropdown gallery of an Add Graphics / North Arrows split button,
    /// modeled on Esri's Insert &gt; North Arrow gallery (its DAML and classes
    /// were the pattern: esri_layouts_northArrowStyleGallery,
    /// NorthArrowStyleGallery). Thumbnails with names, three to a row, in two
    /// sections: the add-in's own designs, then the images the user has
    /// registered. The "Manage..." entry at the foot of the dropdown, where
    /// Esri's gallery has "More...", is a DAML button inside the gallery
    /// element.
    ///
    /// The list is filled at run time from the <see cref="GraphicLibrary"/>
    /// and rebuilt whenever the library changes. Clicking a thumbnail does
    /// what Esri's gallery does: it records the choice and makes the
    /// placement tool current; the tool reads the choice when the sketch is
    /// finished.
    /// </summary>
    internal abstract class GraphicGalleryBase : Gallery
    {
        private readonly GraphicKind _kind;
        private readonly string _toolId;
        private long _builtStamp = -1;

        protected GraphicGalleryBase(GraphicKind kind, string toolId)
        {
            _kind = kind;
            _toolId = toolId;

            // The thumbnails are COMMANDS, not a lasting selection: without
            // this the framework latches the clicked item and a second click
            // on it never fires (see CommandGallery).
            AlwaysFireOnClick = true;

            Rebuild();
            GraphicLibrary.Changed += OnLibraryChanged;
        }

        private void OnLibraryChanged(object sender, EventArgs e)
        {
            // Gallery items must be made on the UI thread.
            Dispatcher d = Application.Current?.Dispatcher;
            if (d == null)
            {
                return;
            }
            if (d.CheckAccess())
            {
                Rebuild();
            }
            else
            {
                d.BeginInvoke(new Action(Rebuild));
            }
        }

        protected override void OnDropDownOpened()
        {
            base.OnDropDownOpened();
            if (_builtStamp != GraphicLibrary.Stamp)
            {
                Rebuild();      // belt and braces, should a change notice be missed
            }
        }

        protected override void Uninitialize()
        {
            GraphicLibrary.Changed -= OnLibraryChanged;
            base.Uninitialize();
        }

        private void Rebuild()
        {
            try
            {
                long stamp = GraphicLibrary.Stamp;
                if (ItemCollection.Count > 0)
                {
                    Clear();
                }
                foreach (GraphicEntry entry in GraphicLibrary.All(_kind))
                {
                    Add(new GraphicGalleryItem(
                        entry,
                        GraphicLibrary.LoadPreview(entry, 96),
                        TipFor(entry),
                        entry.IsBuiltIn ? GraphicLibrary.BuiltInGroup : GraphicLibrary.CustomGroup(_kind)));
                }
                _builtStamp = stamp;
            }
            catch
            {
                // a gallery that cannot list its items must not take the ribbon down
            }
        }

        private string TipFor(GraphicEntry entry)
        {
            string what = entry.IsBuiltIn
                ? entry.Description
                : "Your image: " + GraphicImage.FileSummary(entry) + ".";
            string how = _kind == GraphicKind.NorthArrow
                ? "Choose it, then click the layout to place the north arrow at a default size, "
                  + "or draw a box to size it."
                : "Choose it, then draw a box on the map to size the graphic, or click a layout "
                  + "to place it at a default size.";
            return what + Environment.NewLine + Environment.NewLine + how;
        }

        protected override void OnClick(GalleryItem item)
        {
            ClearSelection();
            if (item is not GraphicGalleryItem chosen)
            {
                return;
            }
            try
            {
                if (_kind == GraphicKind.NorthArrow && LayoutView.Active == null)
                {
                    MessageBox.Show(
                        "North arrows are placed on a layout. Open a layout view, then choose "
                        + "the north arrow again.", "North Arrows");
                    return;
                }
                GraphicLibrary.SetCurrent(_kind, chosen.Entry.Id);
                // The property, not SetCurrentToolAsync: this is what Esri's own
                // gallery does, and it cannot deadlock the UI thread.
                FrameworkApplication.CurrentTool = _toolId;
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not start the placement tool:" + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }

    /// <summary>The Add Graphics gallery (Cartography group): picture graphics
    /// for a map or a layout.</summary>
    internal sealed class AddGraphicsGallery : GraphicGalleryBase
    {
        internal const string ToolId = "WildlifeTools_AddGraphicTool";

        public AddGraphicsGallery() : base(GraphicKind.Graphic, ToolId)
        {
        }
    }

    /// <summary>The North Arrows gallery (Cartography group, layout only):
    /// north arrows whose marker is the chosen artwork.</summary>
    internal sealed class NorthArrowsGallery : GraphicGalleryBase
    {
        internal const string ToolId = "WildlifeTools_AddNorthArrowTool";

        public NorthArrowsGallery() : base(GraphicKind.NorthArrow, ToolId)
        {
        }
    }

    /// <summary>
    /// "Manage..." at the foot of the Add Graphics and North Arrows galleries
    /// (where Esri's North Arrow gallery has "More..."): opens the Manage
    /// Custom Graphics and North Arrows window. One class behind two DAML
    /// buttons, so the window opens on the tab of the gallery it was called
    /// from.
    /// </summary>
    internal sealed class ManageGraphicsButton : Button
    {
        internal const string NorthArrowsId = "WildlifeTools_ManageNorthArrows";

        protected override void OnClick()
        {
            try
            {
                ManageGraphicsWindow.ShowWindow(
                    ID == NorthArrowsId ? GraphicKind.NorthArrow : GraphicKind.Graphic);
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not open the Manage Custom Graphics and North Arrows window:"
                    + Environment.NewLine + Environment.NewLine + ex.Message,
                    "Manage Custom Graphics and North Arrows");
            }
        }
    }
}
