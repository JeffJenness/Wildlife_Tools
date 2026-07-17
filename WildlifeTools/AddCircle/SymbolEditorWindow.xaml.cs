using ArcGIS.Core.CIM;
using ArcGIS.Desktop.Internal.Mapping.Symbology;
using System;
using System.Windows;

namespace WildlifeTools
{
    /// <summary>
    /// Hosts Esri's own Format-Symbol editor (<see cref="SymbolEditingControl"/>, the
    /// same control behind the "Format Polygon Symbol" pane) in a modal dialog so the
    /// user can design the circle's polygon symbol — gallery plus full properties.
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
