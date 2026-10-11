using System.Runtime.Serialization;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace LivingNPC.Core
{
    // A model selection carries no actor or turn authority; the service binds those.
    public sealed class ActionProposal
    {
        [JsonProperty(Required = Required.Always)] public string ActionId { get; set; } = "";
        public JObject Arguments { get; set; } = new JObject();
        public string ReasonForPlayer { get; set; } = "";
        [OnDeserialized] private void Validate(StreamingContext context)
        {
            var error = ActionRegistry.Default.ValidateArguments(ActionId, Arguments);
            if (error != null || ReasonForPlayer.Length > 250) throw new JsonSerializationException(error ?? "reason_too_long");
        }
    }
}
