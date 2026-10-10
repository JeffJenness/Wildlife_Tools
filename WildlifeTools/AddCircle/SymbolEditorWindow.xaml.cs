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
using ArcGIS.Desktop.Internal.Mapping.Symbology;
using System;
using System.Windows;

namespace WildlifeTools
{
    /// <summary>
    /// Hosts Esri's own Format-Symbol editor (<see cref="SymbolEditingControl"/>, the
    /// same control behind the "Format Polygon Symbol" pane) in a modal dialog so the
    /// user can design a symbol — gallery plus full properties. Used for the Graphic
    /// Background tools' polygon symbols and (since 2026-10-10) for the Point
    /// Coordinates marker, a point symbol: the gallery follows the symbol type passed
    /// in, and the caller sets Title ("Format Point Symbol").
    ///
    /// SymbolEditingControl is a public type in an Internal namespace (like the element
    /// tool base used by the compass tools): undocumented/unsupported, so revalidate
    /// after each ArcGIS Pro upgrade.
    /// </summary>
    public partial class SymbolEditorWindow : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private readonly SymbolEditingControl _editor;

        /// <summary>The edited symbol, valid after the dialog returns true.</summary>
        public CIMSymbolReference Result { get; private set; }

        /// <param name="symbol">The polygon symbol reference to start editing from.</param>
        public SymbolEditorWindow(CIMSymbolReference symbol)
        {
            InitializeComponent();
            global::WildlifeTools.Corridor.JennessIcon.Apply(this);

            _editor = new SymbolEditingControl
            {
                Symbol = symbol,
                ShowGallery = true,          // Gallery + Properties tabs, like the real pane
                IsSimpleContext = true,      // plain symbol edit (not layer symbology)
                EnableAutoApply = true,      // edits flow straight into .Symbol
                ShowApplyCancel = false,     // this window supplies OK / Cancel
                // GalleryFilter is the gallery's SEARCH text, not a symbol-type filter —
                // leave it empty so the full polygon gallery shows (the gallery is already
                // scoped to polygon symbols by the CIMPolygonSymbol being edited). Setting
                // it to "Polygon" searched for that word and returned only ~8 symbols.
            };
            EditorHost.Child = _editor;
        }

        private void OnOk(object sender, RoutedEventArgs e)
        {
            try
            {
                Result = _editor.Symbol;
            }
            catch
            {
                Result = null;
            }
            DialogResult = true;
        }

        private void OnCancel(object sender, RoutedEventArgs e) => DialogResult = false;
    }
}
