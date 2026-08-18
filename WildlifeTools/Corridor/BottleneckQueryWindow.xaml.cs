using System.Windows;
using ArcGIS.Desktop.Mapping;
using ArcGIS.Desktop.Mapping.Controls;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// A small modal host for building a selection expression: Esri's own
    /// Query Builder control when the source is a MAP LAYER, or a plain
    /// SQL text box when the source is a feature class on disk (the
    /// builder control needs a MapMember). Used by the Bottleneck
    /// Analysis window's SQL... buttons.
    /// </summary>
    public partial class BottleneckQueryWindow
        : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private readonly QueryBuilderControl _builder;

        public string Expression { get; private set; }

        public BottleneckQueryWindow(MapMember member, string initialExpr,
                                     string sourceName)
        {
            InitializeComponent();
            if (member != null)
            {
                txtPrompt.Text = string.Format(
                    "Build the expression selecting the features of '{0}' "
                    + "to use.", sourceName);
                _builder = new QueryBuilderControl
                {
                    ConfigureControl = new QueryBuilderControlProperties
                    {
                        MapMember = member,
                        Expression = initialExpr ?? "",
                        EditClauseMode = true,
                        AutoValidate = true,
                    },
                };
                host.Content = _builder;
            }
            else
            {
                txtPrompt.Text = string.Format(
                    "Enter a SQL expression selecting the features of "
                    + "'{0}' to use (the interactive builder is available "
                    + "when the source is a map layer).", sourceName);
                txtExpr.Text = initialExpr ?? "";
                txtExpr.Visibility = Visibility.Visible;
                host.Visibility = Visibility.Collapsed;
            }
        }

        private void OnClear(object sender, RoutedEventArgs e)
        {
            if (_builder != null) _builder.ClearExpression();
            else txtExpr.Text = "";
        }

        private void OnOk(object sender, RoutedEventArgs e)
        {
            Expression = _builder != null
                ? (_builder.Expression ?? "").Trim()
                : txtExpr.Text.Trim();
            DialogResult = true;
            Close();
        }

        private void OnCancel(object sender, RoutedEventArgs e)
        {
            DialogResult = false;
            Close();
        }
    }
}
