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
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Threading;
using ArcGIS.Core.CIM;
using ArcGIS.Core.Events;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Layouts.Events;
using ArcGIS.Desktop.Mapping;
using ArcGIS.Desktop.Mapping.Controls;
using ArcGIS.Desktop.Mapping.Events;

namespace WildlifeTools.ColorHelper
{
    /// <summary>
    /// Everything the Element Color Helper does that needs ArcGIS Pro: read
    /// the selection, scan and recolor the elements, keep the originals for
    /// Restore, watch the selection, and supply Pro's color picker. Kept
    /// apart from the panel so the panel can be built outside Pro.
    /// </summary>
    internal static class ElementColorActions
    {
        public static void OpenHelp() => WebHelp.Open("cartography/element-color-helper.html");

        // ---- the color picker ------------------------------------------------

        /// <summary>Pro's own color picker, with a way to read its color.
        /// Starts on white, the usual wish on a dark map.</summary>
        public static FrameworkElement CreatePicker(out Func<CIMColor> picked)
        {
            var picker = new ColorPickerControl
            {
                Height = 24,
                Width = 72,
                IsNoColorVisible = false,
                VerticalAlignment = System.Windows.VerticalAlignment.Center,
            };
            // The control draws its swatch as a bitmap sized to itself and
            // skips the first build from 0x0, so a color set before layout
            // leaves the swatch blank: set a sentinel and the real color once
            // layout is done (the Polar Plots window's recipe).
            picker.Loaded += (_, _) => picker.Dispatcher.BeginInvoke(new Action(() =>
            {
                picker.SelectedColor = new CIMRGBColor { R = 1, G = 2, B = 3, Alpha = 50 };
                picker.SelectedColor = new CIMRGBColor { R = 255, G = 255, B = 255, Alpha = 100 };
            }), DispatcherPriority.Loaded);
            picked = () => picker.SelectedColor;
            return picker;
        }

        // ---- watching the selection ------------------------------------------

        private sealed class Subscriptions : IDisposable
        {
            public readonly List<SubscriptionToken> Tokens = new List<SubscriptionToken>();
            public readonly List<Action> Unsubscribe = new List<Action>();

            public void Dispose()
            {
                foreach (Action u in Unsubscribe)
                {
                    try { u(); } catch { }
                }
                Unsubscribe.Clear();
            }
        }

        /// <summary>Calls <paramref name="onChange"/> whenever the selection
        /// of elements may have changed: elements selected or edited, a
        /// layout or map view activated. Dispose the result to stop.</summary>
        public static IDisposable Subscribe(Action onChange)
        {
            var s = new Subscriptions();
            SubscriptionToken t1 = ElementEvent.Subscribe(args =>
            {
                if (args.Hint == ElementEventHint.SelectionChanged
                    || args.Hint == ElementEventHint.ElementAdded
                    || args.Hint == ElementEventHint.ElementRemoved
                    || args.Hint == ElementEventHint.StyleChanged
                    || args.Hint == ElementEventHint.PropertyChanged)
                {
                    onChange();
                }
            });
            s.Unsubscribe.Add(() => ElementEvent.Unsubscribe(t1));
            SubscriptionToken t2 = LayoutViewEvent.Subscribe(_ => onChange());
            s.Unsubscribe.Add(() => LayoutViewEvent.Unsubscribe(t2));
            SubscriptionToken t3 = ActiveMapViewChangedEvent.Subscribe(_ => onChange());
            s.Unsubscribe.Add(() => ActiveMapViewChangedEvent.Unsubscribe(t3));
            SubscriptionToken t4 = MapSelectionChangedEvent.Subscribe(_ => onChange());
            s.Unsubscribe.Add(() => MapSelectionChangedEvent.Unsubscribe(t4));
            return s;
        }

        // ---- the selection -----------------------------------------------------

        /// <summary>The selected elements of the active layout, or of the
        /// active map's graphics layers, with groups opened up into their
        /// members. MCT.</summary>
        private static List<Element> SelectedElements(out string where)
        {
            var list = new List<Element>();
            if (LayoutView.Active is LayoutView lv)
            {
                where = "layout";
                AddFlattened(lv.GetSelectedElements(), list);
            }
            else if (MapView.Active?.Map is Map map)
            {
                where = "map";
                foreach (GraphicsLayer gl in map.GetLayersAsFlattenedList().OfType<GraphicsLayer>())
                {
                    AddFlattened(gl.GetSelectedElements(), list);
                }
            }
            else
            {
                where = "";
            }
            return list;
        }

        private static void AddFlattened(IEnumerable<Element> elements, List<Element> into)
        {
            foreach (Element el in elements ?? Enumerable.Empty<Element>())
            {
                if (el is GroupElement group)
                {
                    AddFlattened(group.Elements, into);
                }
                else if (el != null)
                {
                    into.Add(el);
                }
            }
        }

        private static string KindOf(Element el) => el switch
        {
            Legend => "legend",
            ScaleBar => "scale bar",
            NorthArrow => "north arrow",
            MapFrame => "map frame",
            TextElement => "text",
            PictureElement => "picture",
            GraphicElement => "graphic",
            _ => "element",
        };

        private static string Plural(string kind, int n) =>
            n == 1 ? kind : kind == "text" ? "text" : kind + "s";

        // ---- scanning -------------------------------------------------------------

        public sealed class ScanResult
        {
            public string Summary { get; set; }
            public List<ColorUse> Uses { get; set; } = new List<ColorUse>();
        }

        /// <summary>Every color in the selected elements, by role.</summary>
        public static Task<ScanResult> ScanAsync(WalkOptions options)
        {
            return QueuedTask.Run(() =>
            {
                var result = new ScanResult();
                List<Element> elements = SelectedElements(out string where);
                if (where.Length == 0)
                {
                    result.Summary = "No layout or map is active.";
                    return result;
                }
                if (elements.Count == 0)
                {
                    result.Summary = "Nothing is selected on the active " + where
                        + ". Select the elements to recolor.";
                    return result;
                }

                var uses = new Dictionary<string, ColorUse>();
                var kinds = new Dictionary<string, int>();
                foreach (Element el in elements)
                {
                    string kind = KindOf(el);
                    kinds[kind] = kinds.TryGetValue(kind, out int k) ? k + 1 : 1;
                    try
                    {
                        ColorWalker.Scan(el.GetDefinition(), el.Name, options, uses);
                    }
                    catch
                    {
                        // an element whose definition cannot be read is skipped
                    }
                }
                string what = string.Join(", ", kinds.OrderByDescending(p => p.Value)
                    .Select(p => p.Value + " " + Plural(p.Key, p.Value)));
                result.Summary = elements.Count + (elements.Count == 1 ? " element" : " elements")
                    + " selected on the " + where + ": " + what + ".";
                result.Uses = uses.Values.ToList();
                return result;
            });
        }

        // ---- changing, and changing back -----------------------------------------------

        private sealed class Snapshot
        {
            public Element Element;
            public CIMElement Before;
        }

        private static readonly List<Snapshot> Snapshots = new List<Snapshot>();

        public static bool HasSnapshots
        {
            get { lock (Snapshots) { return Snapshots.Count > 0; } }
        }


        /// <summary>Gives every selected element the new colors. The first
        /// time an element is changed, its definition from before the change
        /// is kept for <see cref="RestoreAsync"/>. Returns how many elements
        /// and how many colors changed.</summary>
        public static Task<(int Elements, int Colors)> ApplyAsync(WalkOptions options,
            IReadOnlyDictionary<string, (byte R, byte G, byte B)> newColors)
        {
            return QueuedTask.Run(() =>
            {
                int elementsChanged = 0, colorsChanged = 0;
                foreach (Element el in SelectedElements(out _))
                {
                    CIMElement def = el.GetDefinition();
                    if (def == null)
                    {
                        continue;
                    }
                    var before = (CIMElement)CIMObject.Clone(def);
                    int n = ColorWalker.Replace(def, options, newColors);
                    if (n == 0)
                    {
                        continue;
                    }
                    lock (Snapshots)
                    {
                        // Element overrides Equals, so a fresh wrapper of the same
                        // element still matches its snapshot.
                        bool kept = Snapshots.Any(s => s.Element.Equals(el));
                        if (!kept)
                        {
                            Snapshots.Add(new Snapshot { Element = el, Before = before });
                        }
                    }
                    el.SetDefinition(def);
                    elementsChanged++;
                    colorsChanged += n;
                }
                return (elementsChanged, colorsChanged);
            });
        }

        /// <summary>Puts back the definition each changed element had before
        /// this window first changed it. The snapshots are kept, so Restore
        /// can be repeated after further changes.</summary>
        public static Task<int> RestoreAsync()
        {
            return QueuedTask.Run(() =>
            {
                int n = 0;
                List<Snapshot> all;
                lock (Snapshots)
                {
                    all = Snapshots.ToList();
                }
                foreach (Snapshot s in all)
                {
                    try
                    {
                        s.Element.SetDefinition((CIMElement)CIMObject.Clone(s.Before));
                        n++;
                    }
                    catch
                    {
                        // the element is gone (deleted, or its layout closed)
                    }
                }
                return n;
            });
        }
    }
}
