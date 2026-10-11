using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;

namespace LivingNPC.Core
{
    // This embedded metadata never activates a capability or selects a native API.
    public sealed class ActionRegistry
    {
        private readonly Dictionary<string, JObject> actions;
        public static ActionRegistry Default { get; } = Load();
        public string Version { get; }
        public IReadOnlyCollection<string> ActionIds => actions.Keys;
        private ActionRegistry(JObject data)
        {
            if ((int?)data["schema_version"] != 1 || (bool?)data["grants_capabilities"] != false) throw new InvalidDataException("Unsupported action registry");
            Version = (string?)data["registry_version"] ?? throw new InvalidDataException("Missing registry version");
            var definitions = data["actions"] as JArray ?? throw new InvalidDataException("Missing registry actions");
            if (definitions.Count == 0 || definitions.Count > 64) throw new InvalidDataException("Invalid registry size");
            actions = definitions.Cast<JObject>().ToDictionary(item => (string)item["id"]!, item => item);
        }
        private static ActionRegistry Load()
        {
            using (var stream = typeof(ActionRegistry).GetTypeInfo().Assembly.GetManifestResourceStream("LivingNPC.action_registry.json") ?? throw new InvalidDataException("Action registry resource missing"))
            using (var reader = new StreamReader(stream)) return new ActionRegistry(JObject.Parse(reader.ReadToEnd()));
        }
        public JObject Metadata(string actionId) => actions.TryGetValue(actionId, out var item) ? (JObject)item.DeepClone() : throw new ArgumentException("Unknown action");
        public double Cooldown(string actionId) => (double)actions[actionId]["cooldown_seconds"]!;
        public string? ValidateArguments(string actionId, JObject arguments)
        {
            if (!actions.TryGetValue(actionId, out var item)) return "unknown_action";
            return Validate((JObject)item["arguments"]!, arguments);
        }
        private static string? Validate(JObject schema, JToken value)
        {
            switch ((string?)schema["type"])
            {
                case "object":
                    if (value is not JObject obj || (bool?)schema["additionalProperties"] != false) return "invalid_arguments";
                    var properties = (JObject)schema["properties"]!;
                    if (obj.Properties().Any(p => properties[p.Name] == null)) return "unknown_argument";
                    if (((JArray)schema["required"]!).Any(key => obj[(string?)key ?? ""] == null)) return "missing_argument";
                    foreach (var property in obj.Properties())
                    { var error = Validate((JObject)properties[property.Name]!, property.Value); if (error != null) return error; }
                    return null;
                case "number":
                    if (value.Type != JTokenType.Integer && value.Type != JTokenType.Float) return "invalid_argument_type";
                    double number;
                    try { number = (double)value; } catch (Exception) { return "invalid_argument_type"; }
                    if (double.IsNaN(number) || double.IsInfinity(number) || number < (double)schema["minimum"]! || number > (double)schema["maximum"]!) return "argument_out_of_range";
                    return null;
                case "string":
                    if (value.Type != JTokenType.String) return "invalid_argument_type";
                    var text = (string?)value ?? "";
                    var length = text.Length;
                    for (var i = 0; i + 1 < text.Length; i++) if (char.IsHighSurrogate(text[i]) && char.IsLowSurrogate(text[i + 1])) { length--; i++; }
                    if (length < (int)schema["minLength"]! || length > (int)schema["maxLength"]!) return "argument_out_of_range";
                    if (schema["enum"] is JArray choices && !choices.Any(choice => (string?)choice == text)) return "argument_out_of_range";
                    return null;
                default: return "unsupported_argument_schema";
            }
        }
    }
}
