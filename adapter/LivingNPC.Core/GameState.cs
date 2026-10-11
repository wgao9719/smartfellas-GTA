using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace LivingNPC.Core
{
    public enum Ownership { Unknown, Ambient, ModOwned, GameScriptOwned }
    public enum ControlPolicy { Ambient, ServiceLimited, Protected }
    public sealed class WorldState
    {
        public bool Online, MissionActive, CutsceneActive, RandomEventActive, Paused, Loading;
        public bool Known = true;
        public bool Safe => Known && !Online && !MissionActive && !CutsceneActive && !RandomEventActive && !Paused && !Loading;
        public string ProtagonistId = "";
        public DateTime GameDatetime;
    }
    public sealed class ActorState
    {
        public string ActorId = "", RuntimeInstanceId = "", Name = "";
        public int Handle;
        public long Generation;
        public bool Exists, Alive, Busy, InCombat, MissionEntity, CanMove;
        public float Distance;
        public Ownership Ownership;
        public ControlPolicy Policy;
        public float X, Y, Z;
        public PedRole Role;
        public string RoleReason = "unclassified", Kind = "ambient_ephemeral";
        public PedFacts? Facts;
    }
    public enum EffectStatus { Running, Completed, Failed, TakenOver }
    public sealed class EffectObservation
    {
        public EffectStatus Status;
        public string Reason = "";
        public Dictionary<string, object> Facts = new Dictionary<string, object>();
    }
    // Every implementation method is called only from its owning game/script thread.
    public interface IGameAdapter
    {
        WorldState ReadWorld();
        ActorState? Resolve(string actorId);
        bool Start(Command command, ActorState actor, string actionId, JObject arguments);
        EffectObservation Observe(Command command, ActorState actor, string actionId);
        void Release(Command command, ActorState actor, string actionId);
        void Forget(Command command);
        bool SubtitleRendered { get; }
        void ShowSubtitle(string speaker, string text);
        void ClearSubtitle();
        void ShowStatus(string state, string message);
        // Registers the ped as mod-owned under the same actor id. False refuses and drops the binding.
        bool Pin(Command command, ActorState actor, string displayName);
    }
}
