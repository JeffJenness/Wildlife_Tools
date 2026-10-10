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

using System.Windows;
using ArcGIS.Desktop.Mapping;
using ArcGIS.Desktop.Mapping.Controls;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// A small modal host for building a selection expression: Esri's own
    /// Query Builder control whenever a MapMember is available -- a real
    /// map layer, or the TEMPORARY invisible layer the Bottleneck window
    /// stands up for a disk-picked feature class (the builder control
    /// binds only to map members). The plain SQL text box remains solely
    /// for the no-open-map case. Used by the Bottleneck Analysis window's
    /// SQL... buttons.
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
            global::WildlifeTools.Corridor.JennessIcon.Apply(this);
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
                    + "'{0}' to use (the interactive builder needs an "
                    + "open map).", sourceName);
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
