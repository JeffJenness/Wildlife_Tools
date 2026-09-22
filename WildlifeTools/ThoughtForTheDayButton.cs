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
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using ArcGIS.Desktop.Framework.Contracts;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools
{
    /// <summary>
    /// Ribbon button for the "Thought for the Day" toy: picks a random inspirational
    /// quote from the deployed Inspirational_Quotes.json and shows it in the
    /// non-modal <see cref="QuoteWindow"/> card. Runs entirely on the UI thread.
    /// </summary>
    internal sealed class ThoughtForTheDayButton : Button
    {
        private const string QuotesFileName = "Inspirational_Quotes.json";

        private static readonly Random Rng = new();
        private static List<QuoteEntry> _quotes;

        protected override void OnClick()
        {
            try
            {
                List<QuoteEntry> quotes = LoadQuotes();
                if (quotes == null || quotes.Count == 0)
                {
                    MessageBox.Show("No inspirational quotes are available.", "Thought for the Day");
                    return;
                }

                // Avoid repeating any recently shown quote (tracked with the project).
                var recent = new HashSet<string>(Module1.RecentQuoteKeys);
                List<QuoteEntry> pool = quotes.Where(q => !recent.Contains(QuoteKey(q))).ToList();
                if (pool.Count == 0)
                {
                    pool = quotes;
                }

                QuoteEntry pick = pool[Rng.Next(pool.Count)];
                Module1.RecordRecentQuote(QuoteKey(pick));
                QuoteWindow.ShowQuote(pick.Quote, pick.Author);
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    $"Could not show a thought for the day:{Environment.NewLine}{Environment.NewLine}{ex.Message}",
                    "Thought for the Day");
            }
        }

        /// <summary>
        /// Loads (and caches) the quotes from the JSON file deployed in the add-in's
        /// Metadata folder.
        /// </summary>
        private static List<QuoteEntry> LoadQuotes()
        {
            if (_quotes != null)
            {
                return _quotes;
            }

            string installDir = Path.GetDirectoryName(
                System.Reflection.Assembly.GetExecutingAssembly().Location);
            string path = Path.Combine(installDir ?? string.Empty, "Metadata", QuotesFileName);
            if (!File.Exists(path))
            {
                return _quotes = new List<QuoteEntry>();
            }

            string json = File.ReadAllText(path);
            var options = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };
            _quotes = JsonSerializer.Deserialize<List<QuoteEntry>>(json, options) ?? new List<QuoteEntry>();
            return _quotes;
        }

        /// <summary>
        /// A stable identity for a quote, used to track recently shown ones across
        /// sessions. Uses a deterministic FNV-1a hash of the text (string.GetHashCode
        /// is randomized per process and cannot be persisted).
        /// </summary>
        private static string QuoteKey(QuoteEntry entry)
        {
            string text = (entry.Quote ?? string.Empty) + (char)1 + (entry.Author ?? string.Empty);
            const ulong offset = 14695981039346656037UL;
            const ulong prime = 1099511628211UL;
            ulong hash = offset;
            foreach (byte b in Encoding.UTF8.GetBytes(text))
            {
                hash ^= b;
                hash *= prime;
            }
            return hash.ToString("x16");
        }

        /// <summary>One entry from Inspirational_Quotes.json.</summary>
        private sealed class QuoteEntry
        {
            public string Quote { get; set; }
            public string Author { get; set; }
        }
    }
}
