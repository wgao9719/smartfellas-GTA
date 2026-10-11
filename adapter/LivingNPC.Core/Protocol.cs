using System;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Newtonsoft.Json.Serialization;

namespace LivingNPC.Core
{
    public static class Wire
    {
        public static readonly JsonSerializerSettings Settings = new JsonSerializerSettings {
            ContractResolver = new DefaultContractResolver { NamingStrategy = new SnakeCaseNamingStrategy() },
            DateParseHandling = DateParseHandling.None, MaxDepth = 24,
            MissingMemberHandling = MissingMemberHandling.Error,
            NullValueHandling = NullValueHandling.Ignore
        };
        public static string Encode(object value) => JsonConvert.SerializeObject(value, Settings);
        public static T Decode<T>(string json) => JsonConvert.DeserializeObject<T>(json, Settings) ?? throw new JsonException("Empty response");
    }
    public sealed class SessionRequest
    {
        public int SchemaVersion { get; set; } = 1;
        public string ProfileId { get; set; } = "";
        public string SaveSlot { get; set; } = "";
        public bool ProfileConfirmed { get; set; }
        public string? ParentBranchId { get; set; }
        public string ProtagonistId { get; set; } = "franklin";
        public string GameDatetime { get; set; } = "";
        public long RuntimeGeneration { get; set; }
        public Dictionary<string, object> Manifest { get; set; } = new Dictionary<string, object>();
        public string[] Capabilities { get; set; } = Array.Empty<string>();
    }
    public sealed class SessionResponse
    {
        public string SessionId { get; set; } = "";
        public string Token { get; set; } = "";
        public string PolicyVersion { get; set; } = "";
        public string[] Capabilities { get; set; } = Array.Empty<string>();
        public string[] CapabilityFlags { get; set; } = Array.Empty<string>();
        public string BranchId { get; set; } = "";
        public int SchemaVersion { get; set; }
    }
    public sealed class EventEnvelope
    {
        [JsonIgnore] public volatile bool Accepted;
        public int SchemaVersion { get; set; } = 1;
        public string EventId { get; set; } = Guid.NewGuid().ToString("N");
        public string SessionId { get; set; } = "";
        public long Seq { get; set; }
        public string Kind { get; set; } = "";
        public string OccurredAtUtc { get; set; } = DateTimeOffset.UtcNow.ToString("O");
        public string GameDatetime { get; set; } = "";
        [JsonProperty(Required = Required.Always)] public string ProtagonistId { get; set; } = "";
        public string? ActorId { get; set; }
        public long RuntimeGeneration { get; set; }
        public object Payload { get; set; } = new object();
    }
    public sealed class Command
    {
        [JsonProperty(Required = Required.Always)] public int SchemaVersion { get; set; } = 1;
        public string Id { get; set; } = "";
        public long Seq { get; set; }
        public string Kind { get; set; } = "";
        public string SessionId { get; set; } = "";
        public string? ActorId { get; set; }
        public long RuntimeGeneration { get; set; }
        public DateTimeOffset ExpiresAt { get; set; }
        public string IdempotencyKey { get; set; } = "";
        public string? RuntimeInstanceId { get; set; }
        public JObject Payload { get; set; } = new JObject();
    }
    public sealed class CommandBatch
    {
        public List<Command> Commands { get; set; } = new List<Command>();
        public long Cursor { get; set; }
    }
    public sealed class Ack
    {
        [JsonIgnore] public string CommandId { get; set; } = "";
        public string SessionId { get; set; } = "";
        public long RuntimeGeneration { get; set; }
        public string Status { get; set; } = "";
        public string? Reason { get; set; }
        public Dictionary<string, object>? Observed { get; set; }
    }
}
