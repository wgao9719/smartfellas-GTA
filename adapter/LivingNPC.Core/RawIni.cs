using System;
using System.Collections.Generic;
using System.IO;
using System.Security;

namespace LivingNPC.Core
{
    // Comments occupy a whole line. Everything after the first '=' is literal, except outer whitespace.
    public sealed class RawIni
    {
        private readonly Dictionary<string, Dictionary<string, string>> sections = new Dictionary<string, Dictionary<string, string>>(StringComparer.OrdinalIgnoreCase);
        public bool HasInvalidValues { get; private set; }

        public static RawIni Load(string path)
        {
            try { return Parse(File.ReadAllText(path)); }
            catch (FileNotFoundException) { return new RawIni(); }
            catch (DirectoryNotFoundException) { return new RawIni(); }
            catch (IOException) { return new RawIni { HasInvalidValues = true }; }
            catch (UnauthorizedAccessException) { return new RawIni { HasInvalidValues = true }; }
            catch (SecurityException) { return new RawIni { HasInvalidValues = true }; }
        }

        public static RawIni Parse(string text)
        {
            var result = new RawIni(); string section = "";
            using (var reader = new StringReader(text.TrimStart('\uFEFF')))
            {
                string? line;
                while ((line = reader.ReadLine()) != null)
                {
                    line = line.Trim();
                    if (line.Length == 0 || line.StartsWith(";", StringComparison.Ordinal) || line.StartsWith("//", StringComparison.Ordinal)) continue;
                    if (line.StartsWith("[", StringComparison.Ordinal))
                    {
                        section = line.EndsWith("]", StringComparison.Ordinal) ? line.Substring(1, line.Length - 2).Trim() : "";
                        if (section.Length == 0) result.HasInvalidValues = true;
                        continue;
                    }
                    var equals = line.IndexOf('=');
                    if (section.Length == 0 || equals <= 0) { result.HasInvalidValues = true; continue; }
                    var key = line.Substring(0, equals).Trim();
                    if (key.Length == 0) { result.HasInvalidValues = true; continue; }
                    if (!result.sections.TryGetValue(section, out var values))
                        result.sections[section] = values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                    values[key] = line.Substring(equals + 1).Trim();
                }
            }
            return result;
        }

        public string GetString(string section, string key, string fallback) =>
            sections.TryGetValue(section, out var values) && values.TryGetValue(key, out var value) ? value : fallback;

        public bool GetBoolean(string section, string key, bool fallback)
        {
            var value = GetString(section, key, fallback.ToString());
            if (bool.TryParse(value, out var parsed)) return parsed;
            HasInvalidValues = true; return fallback;
        }

        public T GetEnum<T>(string section, string key, T fallback) where T : struct, Enum
        {
            var value = GetString(section, key, fallback.ToString());
            if (Enum.TryParse<T>(value, true, out var parsed) && Enum.IsDefined(typeof(T), parsed)) return parsed;
            HasInvalidValues = true; return fallback;
        }
    }
}
