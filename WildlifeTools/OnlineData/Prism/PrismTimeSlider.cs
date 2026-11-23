// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Award notice, marking of modified versions, and
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
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading.Tasks;
using ArcGIS.Core.CIM;
using ArcGIS.Core.Events;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using ArcGIS.Desktop.Mapping.Events;

namespace WildlifeTools.OnlineData.Prism
{
    /// <summary>Opens the map's time slider on a single date when layers of
    /// a PRISM Climate Data file are added to a map.
    ///
    /// Why (seen in ArcGIS Pro 3.7, 22 November 2026). When a layer that
    /// has a time step arrives in a map, the map's time stays OFF until the
    /// time slider is first used. The first step then only turns the time
    /// on, with a span one step long that ENDS on the layer's first date,
    /// and ArcGIS Pro draws a multidimensional layer only when the span
    /// STARTS exactly on one of the layer's dates: the map goes empty. As
    /// the slider steps on, the span keeps its length in days, so with
    /// monthly data its start leaves the first of the month and the layer
    /// comes and goes. With a single date and a Span of 0 the slider steps
    /// from each date of the file to the next. The span belongs to the
    /// map's time slider: no property of a layer sets it, and a Python tool
    /// cannot reach a map view's time, so it is done here.
    ///
    /// What is done. (a) The map view's time is set to the date the layer
    /// shows, as start and end, through the public MapView.Time; when the
    /// map's time is still off this turns it on, so that the user's first
    /// step is a step. (b) The time slider's own Span is set to 0, the
    /// state a user makes by typing 0 in the Span box of the Time tab.
    /// (a) alone does not last: the next step opens a span again. ArcGIS
    /// Pro offers no public way to do (b), so it is done by reflection on
    /// Pro's own time slider object (ViewerPaneViewModel.TimeSliderVM:
    /// UseSpecifiedDuration, DurationValue). REVALIDATE AFTER EVERY ARCGIS
    /// PRO UPGRADE: when a name is no longer there, (b) is skipped, the log
    /// says so, and the user sets Span to 0 by hand as the help describes.
    /// (c) Until the slider has stepped three times as a single date, a
    /// change of the map's time to a span is closed again, to the span's
    /// end: this catches a first use of the slider that comes after the
    /// looks of (a) are over.
    ///
    /// The guards, all of which must hold:
    ///   1. A layer just added is a multidimensional layer of a netCDF file
    ///      that the PRISM Climate Data tool wrote (the file holds the
    ///      tool's marker), with time steps that are single dates.
    ///   2. EVERY layer of the map that uses time is such a layer. A map
    ///      that holds any other layer with time (GPS tracks, a netCDF
    ///      file made some other way, a time-aware service) is left alone.
    ///   3. The map's view is the active view. A time that is already a
    ///      single date is never moved.
    /// Nothing is said to the user, and nothing here can fail a tool: every
    /// error is swallowed.
    ///
    /// THE LOG IS OFF (Jeff, 22 November 2026: "I do not want it to keep
    /// the log file. But keep the code that creates it in case we have to
    /// start recreating those logs again in the future."). No file is
    /// written unless a file named as <see cref="LogSwitchPath"/> exists:
    /// make an empty file of that name to turn the log on, and delete it
    /// to turn the log off again; ArcGIS Pro need not be restarted. When
    /// on, the log (<see cref="LogPath"/>) says what was added, what each
    /// guard answered, the map's time and the slider's own settings at
    /// each look, and for ten minutes every change of that map's time. It
    /// is written only when a netCDF layer was among the layers added. It
    /// was this log that showed why the first versions failed.</summary>
    internal static class PrismTimeSlider
    {
        /// <summary>A map this class is looking after.</summary>
        private sealed class Attended
        {
            public string MapUri;
            public DateTime Date;        // the date the added layer shows
            public bool SpanZero;        // the slider's own Span is fixed at 0
            public int GoodSteps;        // changes of the map's time to a single date since then
            public bool Retired;         // the slider steps as it should, or the map is no longer ours: hands off
            public bool Fixing;          // a change is under way
            public DateTime LogUntil;    // changes of the map's time are written down until then
            public int LogLeft;          // and no more than this many of them
        }

        private enum Outcome { NoView, Single, Closed }

        private const BindingFlags Any = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

        private static SubscriptionToken _token, _timeToken;
        private static readonly object Gate = new object();
        private static readonly HashSet<string> Busy = new HashSet<string>(StringComparer.Ordinal);
        private static readonly Dictionary<string, Attended> Maps = new Dictionary<string, Attended>(StringComparer.Ordinal);

        // ArcGIS Pro readies its time slider a moment after the layers
        // arrive, so the map is looked at several times over about ten
        // seconds, until its time is a single date and the slider's Span
        // is 0 twice running.
        private static readonly int[] WaitsMs = { 300, 500, 700, 1000, 1500, 2000, 2000, 2000 };

        private static string SettingsFolder
        {
            get
            {
                string home = Environment.GetEnvironmentVariable("LOCALAPPDATA");
                if (string.IsNullOrEmpty(home)) home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
                return Path.Combine(home, "JennessEnterprises", "WildlifeTools");
            }
        }

        /// <summary>Where the log is written when it is on: beside the
        /// add-in's settings, in the Windows user's own application
        /// data.</summary>
        public static string LogPath => Path.Combine(SettingsFolder, "prism_time_slider.log");

        /// <summary>The log's switch. The log is written only while a file
        /// of this name exists (its contents do not matter).</summary>
        public static string LogSwitchPath => Path.Combine(SettingsFolder, "prism_time_slider_log_on.txt");

        /// <summary>True while the log is switched on.</summary>
        private static bool Logging
        {
            get
            {
                try
                {
                    return File.Exists(LogSwitchPath);
                }
                catch (Exception)
                {
                    return false;
                }
            }
        }

        /// <summary>Begins listening for layers being added. Safe to call
        /// more than once.</summary>
        public static void Start()
        {
            try
            {
                if (_token == null)
                {
                    _token = LayersAddedEvent.Subscribe(OnLayersAdded);
                    _timeToken = MapViewTimeChangedEvent.Subscribe(OnTimeChanged);
                    Log("listening for layers of PRISM Climate Data files (add-in "
                        + typeof(PrismTimeSlider).Assembly.GetName().Version + ")");
                }
            }
            catch (Exception ex)
            {
                // without the listener the time slider opens as ArcGIS Pro opens it
                Log("could not listen for layers: " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        private static void OnLayersAdded(LayerEventsArgs args)
        {
            List<Layer> added;
            try
            {
                added = args?.Layers?.Where(l => l != null).ToList();
            }
            catch (Exception ex)
            {
                Log("could not read the layers that were added: " + ex.GetType().Name + ": " + ex.Message);
                return;
            }
            if (added == null || added.Count == 0) return;
            _ = AttendAsync(added);
        }

        private static async Task AttendAsync(List<Layer> added)
        {
            try
            {
                Attended a = await QueuedTask.Run(() => Look(added));
                if (a == null) return;
                lock (Gate)
                {
                    if (!Busy.Add(a.MapUri))
                    {
                        Log("   this map is already being attended to");
                        return;
                    }
                    Maps[a.MapUri] = a;
                }
                try
                {
                    int look = 0, settled = 0;
                    foreach (int wait in WaitsMs)
                    {
                        await Task.Delay(wait);
                        look++;
                        string head = "   look " + look + ": ";
                        Outcome o = await QueuedTask.Run(() => SingleDate(a, head, keepPlace: false));
                        if (o == Outcome.NoView) break;
                        Log(head + (await OnUiAsync(() => SpanToZero(a)) ?? "the time slider could not be asked"));
                        settled = (o == Outcome.Single && a.SpanZero) ? settled + 1 : 0;
                        if (settled >= 2) break;          // a single date and a Span of 0, twice running
                    }
                }
                finally
                {
                    lock (Gate) Busy.Remove(a.MapUri);
                }
            }
            catch (Exception ex)
            {
                // never let this reach the user: the worst case is the time slider as ArcGIS Pro opens it
                Log("   stopped by " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        /// <summary>On the main CIM thread: the map and the date to show
        /// when guards 1 and 2 hold, else null.</summary>
        private static Attended Look(List<Layer> added)
        {
            var mine = new List<Layer>();
            foreach (Layer l in added)
            {
                mine.Add(l);
                if (l is ILayerContainer group) mine.AddRange(group.GetLayersAsFlattenedList());
            }
            Map map = mine.Select(l => l.Map).FirstOrDefault(m => m != null);
            if (map == null) return null;

            // guard 1: a layer of a file the tool wrote was just added. Most additions end here, cheaply and
            // without a word in the log: only raster layers are looked at, and a file is opened only for a
            // netCDF layer that shows a date.
            var notes = new List<string>();
            DateTime? date = null;
            string home = ProjectFolder();
            foreach (Layer l in mine)
            {
                if (!(l is BasicRasterLayer)) continue;
                string json = Definition(l);
                if (json == null
                    || !PrismLayerFacts.TryReadNetCdfLayer(json, out string nc, out DateTime shown, out bool single, home))
                    continue;
                bool usesTime = PrismLayerFacts.IsTimeAware(json);
                bool ours = PrismLayerFacts.WrittenByTool(nc);
                notes.Add("   added: " + l.Name + " | file " + nc + " | shows " + Stamp(shown)
                    + " | single date " + single + " | uses time " + usesTime + " | written by the tool " + ours);
                // A layer of a file the tool wrote is given the metadata the tool wrote beside the file: a
                // layer of a netCDF variable otherwise shows none (PrismLayerMetadata). This is apart from
                // the time slider, and is done whatever the guards below answer.
                if (ours) notes.Add("      " + PrismLayerMetadata.Give(l, nc));
                if (single && usesTime && ours && (date == null || shown < date.Value)) date = shown;
            }
            if (notes.Count == 0) return null;                    // no netCDF layer was added: nothing to say
            Log("layers added to map \"" + map.Name + "\" (" + mine.Count + " in all)");
            foreach (string n in notes) Log(n);
            if (date == null)
            {
                Log("   left alone: none of them is a layer of a file this tool wrote, with single dates and time in use");
                return null;
            }
            if (!OnlyOurs(map, out string why))                   // guard 2
            {
                Log("   left alone: " + why);
                return null;
            }
            Log("   no other layer of the map uses time: the time slider will be opened on " + Stamp(date.Value));
            return new Attended
            {
                MapUri = map.URI,
                Date = date.Value,
                LogUntil = DateTime.Now.AddMinutes(10),
                LogLeft = 60,
            };
        }

        /// <summary>Guard 2, on the main CIM thread: every layer of the map
        /// that uses time is a layer of a file this tool wrote.</summary>
        private static bool OnlyOurs(Map map, out string why)
        {
            why = null;
            string home = ProjectFolder();
            foreach (Layer l in map.GetLayersAsFlattenedList())
            {
                bool? usesTime = UsesTime(l);
                if (usesTime == false) continue;
                string json = Definition(l);
                bool ours = json != null
                    && PrismLayerFacts.TryReadNetCdfLayer(json, out string nc, out _, out bool single, home)
                    && single && PrismLayerFacts.WrittenByTool(nc);
                if (!ours)
                {
                    why = "layer \"" + l.Name + "\" (" + l.GetType().Name + ") "
                        + (usesTime == null ? "could not be read, so it may use time"
                            : "uses time and is not a layer of a file this tool wrote");
                    return false;
                }
            }
            return true;
        }

        /// <summary>The folder of the open project, from which a layer's
        /// definition may name the folder of its file; null when there is
        /// none.</summary>
        private static string ProjectFolder()
        {
            try
            {
                string aprx = ArcGIS.Desktop.Core.Project.Current?.Path;
                return string.IsNullOrWhiteSpace(aprx) ? null : Path.GetDirectoryName(aprx);
            }
            catch (Exception)
            {
                return null;
            }
        }

        private static string Definition(Layer l)
        {
            try
            {
                return l.GetDefinition()?.ToJson();
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>Whether a layer uses time, by ArcGIS Pro's own rule
        /// (the layer's time definition, or its table's, says so). Null
        /// when the layer's definition cannot be read.</summary>
        private static bool? UsesTime(Layer l)
        {
            CIMBaseLayer def;
            try
            {
                def = l.GetDefinition();
            }
            catch (Exception)
            {
                return null;
            }
            if (def == null) return null;
            object time = Member(def, "TimeDefinition");
            if (time == null)
            {
                object table = Member(def, "FeatureTable");
                if (table != null) time = Member(table, "TimeDefinition");
            }
            return time is CIMTimeDataDefinition t && t.UseTime;
        }

        /// <summary>On the main CIM thread: guard 3, and the change to the
        /// map's time. A time that is off is turned on at the layer's date;
        /// a span is closed, to the layer's date, or to its own end when
        /// <paramref name="keepPlace"/> is true (the slider has been in
        /// use: do not send the user back to the first date).</summary>
        private static Outcome SingleDate(Attended a, string head, bool keepPlace)
        {
            MapView view = MapView.Active;
            if (view?.Map == null || !string.Equals(view.Map.URI, a.MapUri, StringComparison.Ordinal))
            {
                Log(head + (view == null ? "no map view is active" : "another map is the active view") + ": stopped");
                return Outcome.NoView;
            }
            TimeRange now = view.Time;
            bool off = now == null || (!now.Start.HasValue && !now.End.HasValue);
            if (!off && now.Start.HasValue && now.End.HasValue && now.Start.Value == now.End.Value)
            {
                Log(head + "the map's time is the single date " + Stamp(now.Start.Value));
                return Outcome.Single;               // a single date already: leave it where it is
            }
            DateTime to = a.Date;
            if (keepPlace && !off) to = now.End ?? now.Start ?? a.Date;
            view.Time = new TimeRange
            {
                Start = to,
                End = to,
                TimeReference = now?.TimeReference,
            };
            Log(head + (off ? "the map's time was off: turned on at the single date "
                : "the map's time was " + Describe(now) + ": set to the single date ") + Stamp(to));
            return Outcome.Closed;
        }

        // ---- the time slider's own Span, by reflection on ArcGIS Pro's time slider object -------------------

        /// <summary>On the UI thread: sets the Span of the map's time
        /// slider to 0, as typing 0 in the Span box of the Time tab does,
        /// and says what was found and done.</summary>
        private static string SpanToZero(Attended a)
        {
            object slider = SliderOf(a.MapUri);
            if (slider == null) return "the time slider of the map could not be reached: its Span was not set";
            string before = SliderState(slider);
            if (!(Member(slider, "Initialized") is bool ready) || !ready || (Member(slider, "Initializing") is bool busy && busy))
                return "the time slider is not ready yet (" + before + ")";
            if (!(Member(slider, "UseSpecifiedDuration") is bool fixedSpan) || !(Member(slider, "DurationValue") is double span))
                return "the time slider has no Span that can be read here (" + before + ")";
            if (fixedSpan && span == 0.0)
            {
                a.SpanZero = true;
                return "the time slider's Span is 0 (" + before + ")";
            }
            bool one = SetMember(slider, "UseSpecifiedDuration", true);
            bool two = SetMember(slider, "DurationValue", 0.0);
            return "the time slider was " + before + ": its Span " + (one && two ? "was set to 0" : "COULD NOT be set")
                + ", now " + SliderState(slider);
        }

        /// <summary>On the UI thread: ArcGIS Pro's time slider object for
        /// the open view of a map, or null.</summary>
        private static object SliderOf(string mapUri)
        {
            try
            {
                Pane hit = null;
                Pane active = FrameworkApplication.Panes.ActivePane;
                if (IsPaneOf(active, mapUri)) hit = active;
                else
                {
                    foreach (Pane p in FrameworkApplication.Panes)
                    {
                        if (IsPaneOf(p, mapUri))
                        {
                            hit = p;
                            break;
                        }
                    }
                }
                return hit == null ? null : Member(hit, "TimeSliderVM");
            }
            catch (Exception)
            {
                return null;
            }
        }

        private static bool IsPaneOf(Pane p, string mapUri)
        {
            return p is IMapPane mp && string.Equals(mp.MapView?.Map?.URI, mapUri, StringComparison.Ordinal);
        }

        /// <summary>The slider's own settings, for the log.</summary>
        private static string SliderState(object slider)
        {
            if (slider == null) return "no time slider";
            var sb = new StringBuilder();
            sb.Append("start ").Append(OaDate(Member(slider, "StartTime"), Member(slider, "UseStartTime")));
            sb.Append(", end ").Append(OaDate(Member(slider, "EndTime"), Member(slider, "UseEndTime")));
            sb.Append(", span ").Append(Text(Member(slider, "DurationValue"))).Append(' ').Append(Text(Member(slider, "DurationUnit")));
            sb.Append(Member(slider, "UseSpecifiedDuration") is bool f ? (f ? " (fixed)" : " (not fixed)") : " (?)");
            sb.Append(", step ").Append(Text(Member(slider, "StepIntervalValue"))).Append(' ').Append(Text(Member(slider, "StepIntervalUnit")));
            sb.Append(Member(slider, "UseStepNumber") is bool n && n ? " by number of steps"
                : Member(slider, "UseStepLayer") is bool y && y ? " by layer" : " by interval");
            if (Member(slider, "UseSpanStepInterval") is bool s && s) sb.Append(", step is the span");
            if (Member(slider, "IsTimeSnapping") is bool snap && snap) sb.Append(", snapping");
            if (Member(slider, "StartTimeLocked") is bool sl && sl) sb.Append(", start locked");
            if (Member(slider, "EndTimeLocked") is bool el && el) sb.Append(", end locked");
            return sb.ToString();
        }

        private static string OaDate(object value, object inUse)
        {
            string used = inUse is bool u && !u ? " (not in use)" : "";
            try
            {
                return value is double d ? Stamp(DateTime.FromOADate(d)) + used : "?" + used;
            }
            catch (ArgumentException)
            {
                return Text(value) + used;
            }
        }

        private static string Text(object o)
        {
            return o == null ? "?" : Convert.ToString(o, System.Globalization.CultureInfo.InvariantCulture);
        }

        /// <summary>A property or field of an object by name, public or
        /// not; null when it is not there or cannot be read.</summary>
        private static object Member(object o, string name)
        {
            if (o == null) return null;
            try
            {
                for (Type t = o.GetType(); t != null; t = t.BaseType)
                {
                    PropertyInfo p = t.GetProperty(name, Any | BindingFlags.DeclaredOnly);
                    if (p != null && p.CanRead && p.GetIndexParameters().Length == 0) return p.GetValue(o);
                    FieldInfo f = t.GetField(name, Any | BindingFlags.DeclaredOnly);
                    if (f != null) return f.GetValue(o);
                }
            }
            catch (Exception)
            {
                // not readable: the same as not there
            }
            return null;
        }

        private static bool SetMember(object o, string name, object value)
        {
            if (o == null) return false;
            try
            {
                for (Type t = o.GetType(); t != null; t = t.BaseType)
                {
                    PropertyInfo p = t.GetProperty(name, Any | BindingFlags.DeclaredOnly);
                    if (p != null && p.CanWrite)
                    {
                        p.SetValue(o, value);
                        return true;
                    }
                }
            }
            catch (Exception ex)
            {
                Log("   setting " + name + " failed: " + (ex.InnerException ?? ex).GetType().Name + ": "
                    + (ex.InnerException ?? ex).Message);
            }
            return false;
        }

        private static async Task<string> OnUiAsync(Func<string> work)
        {
            try
            {
                var dispatcher = System.Windows.Application.Current?.Dispatcher;
                if (dispatcher == null) return null;
                return await dispatcher.InvokeAsync(work).Task;
            }
            catch (Exception ex)
            {
                return "stopped by " + ex.GetType().Name + ": " + ex.Message;
            }
        }

        // ---- changes of the map's time, after the looks are over -----------------------------------------------

        /// <summary>Every change of the time of a map this class looks
        /// after. For ten minutes it is written to the log, with the
        /// slider's own settings. Until the slider has stepped three times
        /// as a single date, a change to a span is closed again: that is
        /// what happens when the slider is first used after the looks are
        /// over, and ArcGIS Pro turns the time on with its span.</summary>
        private static void OnTimeChanged(MapViewTimeChangedEventArgs args)
        {
            try
            {
                string uri = args?.MapView?.Map?.URI;
                if (uri == null) return;
                TimeRange t = args.CurrentTime;
                bool off = t == null || (!t.Start.HasValue && !t.End.HasValue);
                bool single = !off && t.Start.HasValue && t.End.HasValue && t.Start.Value == t.End.Value;
                Attended a;
                bool write, fix = false;
                lock (Gate)
                {
                    if (!Maps.TryGetValue(uri, out a)) return;
                    write = a.LogLeft > 0 && DateTime.Now <= a.LogUntil && Logging;
                    if (write) a.LogLeft--;
                    if (single && a.SpanZero && !a.Retired && ++a.GoodSteps >= 3) a.Retired = true;
                    if (!off && !single && !a.Retired && !a.Fixing && !Busy.Contains(uri))
                    {
                        a.Fixing = true;
                        fix = true;
                    }
                }
                if (write)
                {
                    string text = "   the map's time changed to " + (off ? "off" : Describe(t));
                    var dispatcher = System.Windows.Application.Current?.Dispatcher;
                    if (dispatcher == null) Log(text);
                    else dispatcher.BeginInvoke(new Action(() => Log(text + " | slider: " + SliderState(SliderOf(uri)))));
                }
                if (fix) _ = FixAsync(a);
            }
            catch (Exception)
            {
                // never let this reach the user
            }
        }

        private static async Task FixAsync(Attended a)
        {
            const string head = "   a span after the looks were over: ";
            try
            {
                Outcome o = await QueuedTask.Run(() =>
                {
                    Map map = MapView.Active?.Map;
                    if (map != null && string.Equals(map.URI, a.MapUri, StringComparison.Ordinal)
                        && !OnlyOurs(map, out string why))
                    {
                        a.Retired = true;                // the map has other time data now: hands off for good
                        Log(head + "left alone, " + why);
                        return Outcome.NoView;
                    }
                    return SingleDate(a, head, keepPlace: true);
                });
                if (o != Outcome.NoView)
                    Log(head + (await OnUiAsync(() => SpanToZero(a)) ?? "the time slider could not be asked"));
            }
            catch (Exception ex)
            {
                Log(head + "stopped by " + ex.GetType().Name + ": " + ex.Message);
            }
            finally
            {
                lock (Gate) a.Fixing = false;
            }
        }

        // ---- the log -------------------------------------------------------------------------------------------

        private static string Stamp(DateTime d)
        {
            return d.ToString("yyyy-MM-dd HH:mm:ss.fff");
        }

        private static string Describe(TimeRange t)
        {
            if (t == null) return "(no time)";
            return (t.Start.HasValue ? Stamp(t.Start.Value) : "(no start)") + " to "
                + (t.End.HasValue ? Stamp(t.End.Value) : "(no end)");
        }

        /// <summary>One dated line in the log, when the log is switched on
        /// (see <see cref="LogSwitchPath"/>); otherwise nothing. Never
        /// throws. The log starts afresh when it passes 200 KB.</summary>
        private static void Log(string text)
        {
            try
            {
                if (!Logging) return;
                string path = LogPath;
                lock (Gate)
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(path));
                    var info = new FileInfo(path);
                    if (info.Exists && info.Length > 200 * 1024) info.Delete();
                    File.AppendAllText(path, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + "  " + text
                        + Environment.NewLine, Encoding.UTF8);
                }
            }
            catch (Exception)
            {
                // a log that cannot be written is not worth a word
            }
        }
    }
}
