namespace WildlifeTools
{
    /// <summary>
    /// "Compass graphic 2" (Graphics group): identical behavior to
    /// <see cref="CompassRoseGraphicTool"/> but places the Compass2.svg artwork —
    /// the Compass_2d design (black cardinal star, gray intercardinal star, twin
    /// rings). Focal point is the compass center.
    /// </summary>
    internal class Compass2GraphicTool : CompassRoseGraphicTool
    {
        protected override CompassArt Art => CompassArt.Compass2;
    }

    /// <summary>
    /// "North arrow 2" (Cartography group, layout-only): identical behavior to
    /// <see cref="CompassRoseNorthArrowTool"/> but places the Compass2.svg artwork.
    /// </summary>
    internal class Compass2NorthArrowTool : CompassRoseNorthArrowTool
    {
        protected override CompassArt Art => CompassArt.Compass2;
    }

    /// <summary>
    /// "Compass graphic 2 - dot" (Graphics group): the Compass_2d design plus the
    /// Sample_North gradient spearhead above the north point (Compass2Dot.svg).
    /// Focal point is the compass center, below the canvas midpoint.
    /// </summary>
    internal class Compass2DotGraphicTool : CompassRoseGraphicTool
    {
        protected override CompassArt Art => CompassArt.Compass2Dot;
    }

    /// <summary>
    /// "North arrow 2 - dot" (Cartography group, layout-only): north arrow version of
    /// <see cref="Compass2DotGraphicTool"/>.
    /// </summary>
    internal class Compass2DotNorthArrowTool : CompassRoseNorthArrowTool
    {
        protected override CompassArt Art => CompassArt.Compass2Dot;
    }
}
