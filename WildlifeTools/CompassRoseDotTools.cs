namespace WildlifeTools
{
    /// <summary>
    /// "Compass Rose with Dot" (Graphics group): identical behavior to
    /// <see cref="CompassRoseGraphicTool"/> but places the CompassRoseDot.svg artwork —
    /// the compass rose with the black "north dot" above the north arrowhead. The stamped
    /// focal point is the compass center (feature-class origin 0,0), NOT the box center.
    /// </summary>
    internal class CompassRoseDotGraphicTool : CompassRoseGraphicTool
    {
        protected override CompassArt Art => CompassArt.Dot;
    }

    /// <summary>
    /// "Compass North with Dot" (Cartography group, layout-only): identical behavior to
    /// <see cref="CompassRoseNorthArrowTool"/> but places the CompassRoseDot.svg artwork,
    /// with the focal point on the compass center.
    /// </summary>
    internal class CompassRoseDotNorthArrowTool : CompassRoseNorthArrowTool
    {
        protected override CompassArt Art => CompassArt.Dot;
    }
}
