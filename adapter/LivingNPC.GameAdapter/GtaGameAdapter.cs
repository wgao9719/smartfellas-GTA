using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Threading;
using GTA;
using GTA.Math;
using GTA.Native;
using LivingNPC.Core;
using Newtonsoft.Json.Linq;

namespace LivingNPC.GameAdapter
{
    internal sealed class GtaGameAdapter : IGameAdapter
    {
        private sealed class Binding
        {
            public Ped Ped = null!;
            public string ActorId = "", Instance = "", Kind = "ambient_ephemeral", Name = "Nearby resident";
            public int Marker, Model;
            public long Generation;
            public bool ModOwned = false;
            public PedFacts? StaticFacts, Facts;
            public int FactsFrame = -1;
        }
        private sealed class TaskRecord
        {
            public int Duration, Hash;
            public ActionMotionTracker Motion = null!;
            public bool ObservedActive;
        }
        private const string Decorator = "lls_runtime_binding_v1";
        private readonly int thread = Thread.CurrentThread.ManagedThreadId;
        private readonly Dictionary<string, Binding> bindings = new Dictionary<string, Binding>();
        private readonly Dictionary<string, TaskRecord> tasks = new Dictionary<string, TaskRecord>();
        private string subtitle = "", status = "Living Los Santos: connecting";
        private DateTimeOffset subtitleUntil;
        public string? SelectedActorId { get; private set; }
        public bool Recording { get; private set; }
        public bool SubtitleRendered { get; private set; }
        public void Forget(Command command) { OnScriptThread(); tasks.Remove(command.Id); }
        public GtaGameAdapter()
        {
            if (!Function.Call<bool>(Hash.DECOR_IS_REGISTERED_AS_TYPE, Decorator, 3)) Function.Call(Hash.DECOR_REGISTER, Decorator, 3);
        }
        private void OnScriptThread() { if (Thread.CurrentThread.ManagedThreadId != thread) throw new InvalidOperationException("Game API outside script thread"); }
        public WorldState ReadWorld()
        {
            OnScriptThread();
            var player = Game.Player.Character;
            string protagonist = player.Model.Hash == Game.GenerateHash("player_zero") ? "michael" : player.Model.Hash == Game.GenerateHash("player_one") ? "franklin" : player.Model.Hash == Game.GenerateHash("player_two") ? "trevor" : "unknown";
            return new WorldState { Online = Function.Call<bool>(Hash.NETWORK_IS_GAME_IN_PROGRESS), MissionActive = Game.IsMissionActive,
                CutsceneActive = Game.IsCutsceneActive, RandomEventActive = Game.IsRandomEventActive, Paused = Game.IsPaused,
                Loading = Game.IsLoading, GameDatetime = World.CurrentDate, ProtagonistId = protagonist, Known = protagonist != "unknown" };
        }
        // Binds the nearest registered or fully civilian ambient ped. Refused peds are never decorated.
        public ActorState? SelectNearest(long generation, out ActorState? refusal)
        {
            OnScriptThread(); refusal = null;
            if (!ReadWorld().Safe) return null;
            var player = Game.Player.Character;
            foreach (var ped in World.GetNearbyPeds(player, 8f).Where(p => p.Exists() && !p.IsDead).OrderBy(p => p.Position.DistanceTo(player.Position)))
            {
                var existing = bindings.Values.FirstOrDefault(b => b.Ped.Handle == ped.Handle && b.Generation == generation && Valid(b));
                if (existing != null) { SelectedActorId = existing.ActorId; return Resolve(existing.ActorId); }
                var facts = Sample(ped, null); var classified = PedClassifier.Classify(facts);
                if (classified.Ownership != Ownership.Ambient || classified.Role != PedRole.Civilian || classified.Policy != ControlPolicy.Ambient)
                {
                    refusal ??= new ActorState { Name = PedRoleTable.Label(classified.Role), Role = classified.Role, RoleReason = classified.Reason, Ownership = classified.Ownership,
                        Policy = classified.Policy, Facts = facts, Exists = true, Alive = true, Generation = generation, Distance = ped.Position.DistanceTo(player.Position) };
                    continue;
                }
                if (bindings.Count >= 36) return null;
                var id = Guid.NewGuid().ToString();
                var marker = new Random(Guid.NewGuid().GetHashCode()).Next(1, int.MaxValue);
                if (!Function.Call<bool>(Hash.DECOR_SET_INT, ped.Handle, Decorator, marker)) return null;
                bindings[id] = new Binding { Ped = ped, ActorId = id, Instance = Guid.NewGuid().ToString(), Marker = marker, Model = ped.Model.Hash, Generation = generation };
                SelectedActorId = id;
                return Resolve(id);
            }
            return null;
        }
        // Static facts are cached per binding. Dynamic facts are sampled at most once per frame.
        private PedFacts? Sample(Ped ped, Binding? binding)
        {
            try
            {
                if (ped == null || !ped.Exists()) return null;
                var frame = Game.FrameCount;
                if (binding?.Facts != null && binding.FactsFrame == frame) return binding.Facts;
                var model = ped.Model; var known = binding?.StaticFacts;
                if (known == null || known.ModelHash != model.Hash) known = new PedFacts { ModelHash = model.Hash, Human = ped.IsHuman, AnimalModel = model.IsAnimalPed, PedType = Function.Call<int>(Hash.GET_PED_TYPE, ped.Handle) };
                var facts = new PedFacts { ModelHash = known.ModelHash, Human = known.Human, AnimalModel = known.AnimalModel, PedType = known.PedType,
                    RelationshipGroupHash = ped.RelationshipGroup.Hash, Cuffed = ped.IsCuffed, InPoliceVehicle = ped.IsInPoliceVehicle,
                    PlayerGroup = Function.Call<bool>(Hash.IS_PED_GROUP_MEMBER, ped.Handle, Function.Call<int>(Hash.GET_PED_GROUP_INDEX, Game.Player.Character.Handle)),
                    MissionEntity = ped.IsPersistent, InModRegistry = binding?.ModOwned == true,
                    BelongsToModScript = Function.Call<bool>(Hash.DOES_ENTITY_BELONG_TO_THIS_SCRIPT, ped.Handle, false) };
#if DEVELOPMENT
                facts.OwningScript = OwningScript(ped);
#endif
                if (binding != null) { binding.StaticFacts = known; binding.Facts = facts; binding.FactsFrame = frame; }
                return facts;
            }
            catch (Exception) { return null; }
        }
#if DEVELOPMENT
        private static string? OwningScript(Ped ped)
        {
            var name = Function.Call<string>(Hash.GET_ENTITY_SCRIPT, ped.Handle, new OutputArgument());
            if (string.IsNullOrEmpty(name)) return null;
            var safe = new string(name.Where(c => c < 128 && (char.IsLetterOrDigit(c) || c == '_')).Take(64).ToArray());
            return safe.Length > 0 ? safe : "unnamed_script";
        }
#endif
        public string[] PruneBindings(long generation)
        {
            OnScriptThread();
            var removed = new List<string>();
            foreach (var binding in bindings.Values.ToArray())
            {
                // Registered pins can despawn or be taken by a script as well.
                if (binding.Generation == generation && Valid(binding) && (binding.ModOwned ? PedClassifier.Classify(Sample(binding.Ped, binding)).Ownership == Ownership.ModOwned
                    : !binding.Ped.IsPersistent && binding.Ped.Position.DistanceTo(Game.Player.Character.Position) <= 60)) continue;
                bindings.Remove(binding.ActorId); removed.Add(binding.ActorId);
                if (SelectedActorId == binding.ActorId) SelectedActorId = null;
            }
            return removed.ToArray();
        }
        private bool Valid(Binding value) => value.Ped.Exists() && value.Ped.Model.Hash == value.Model &&
            Function.Call<bool>(Hash.DECOR_EXIST_ON, value.Ped.Handle, Decorator) && Function.Call<int>(Hash.DECOR_GET_INT, value.Ped.Handle, Decorator) == value.Marker;
        public ActorState? Resolve(string actorId)
        {
            OnScriptThread();
            if (!bindings.TryGetValue(actorId, out var binding) || !Valid(binding)) return null;
            var ped = binding.Ped; var pos = ped.Position;
            var facts = Sample(ped, binding); var classified = PedClassifier.Classify(facts);
            return new ActorState { ActorId = actorId, RuntimeInstanceId = binding.Instance, Name = binding.Name, Handle = ped.Handle, Generation = binding.Generation,
                Exists = true, Alive = !ped.IsDead, MissionEntity = facts?.MissionEntity ?? true, Ownership = classified.Ownership, Policy = classified.Policy,
                Role = classified.Role, RoleReason = classified.Reason, Facts = facts, Kind = binding.Kind,
                Busy = ped.IsInVehicle() || ped.IsRagdoll || ped.IsFleeing || ped.IsOnFire,
                InCombat = ped.IsInCombat, CanMove = !ped.IsInVehicle() && !ped.IsRagdoll,
                Distance = pos.DistanceTo(Game.Player.Character.Position), X = pos.X, Y = pos.Y, Z = pos.Z };
        }
        public bool Start(Command command, ActorState actor, string actionId, JObject arguments)
        {
            OnScriptThread();
            if (!bindings.TryGetValue(actor.ActorId, out var binding) || !Valid(binding)) return false;
            if (!TryPosition(binding.Ped, out var origin)) return false;
            var motion = new ActionMotionTracker(MonotonicMilliseconds(), origin.X, origin.Y, origin.Z);
            if (!motion.IsValid) return false;
            var duration = (int)Math.Ceiling(1000 * ((double?)arguments["duration_seconds"] ?? (actionId == "face_player" ? 1.5 : 3)));
            if (actionId == "face_player") binding.Ped.Task.TurnTo(Game.Player.Character, duration);
            else if (actionId == "wait_here") binding.Ped.Task.StandStill(duration);
            else return false;
            tasks[command.Id] = new TaskRecord { Motion = motion, Duration = duration,
                Hash = Game.GenerateHash(actionId == "face_player" ? "SCRIPT_TASK_TURN_PED_TO_FACE_ENTITY" : "SCRIPT_TASK_STAND_STILL") };
            return true;
        }
        private static double MonotonicMilliseconds() => Stopwatch.GetTimestamp() * (1000.0 / Stopwatch.Frequency);
        private static bool TryPosition(Ped ped, out Vector3 position)
        {
            position = default;
            try
            {
                if (ped == null || !ped.Exists()) return false;
                position = ped.Position;
                return ActionMotionTracker.IsFinite(position.X) && ActionMotionTracker.IsFinite(position.Y) && ActionMotionTracker.IsFinite(position.Z);
            }
            catch (Exception) { return false; }
        }
        public EffectObservation Observe(Command command, ActorState actor, string actionId)
        {
            OnScriptThread();
            if (!tasks.TryGetValue(command.Id, out var task) || !bindings.TryGetValue(actor.ActorId, out var binding) || !Valid(binding)) return new EffectObservation { Status = EffectStatus.Failed, Reason = "binding_lost" };
            if (!TryPosition(binding.Ped, out var position))
                return new EffectObservation { Status = EffectStatus.Failed, Reason = "movement_sample_unavailable" };
            if (!task.Motion.TrySample(MonotonicMilliseconds(), position.X, position.Y, position.Z, out var motion))
                return new EffectObservation { Status = EffectStatus.Failed, Reason = task.Motion.FailureReason };
            var elapsed = motion!.ElapsedMilliseconds;
            var taskStatus = Function.Call<int>(Hash.GET_SCRIPT_TASK_STATUS, binding.Ped.Handle, task.Hash);
            var performing = taskStatus == 0 || taskStatus == 1;
            task.ObservedActive |= performing;
            if (actionId == "face_player")
            {
                if (!TryPosition(Game.Player.Character, out var playerPosition))
                    return new EffectObservation { Status = EffectStatus.Failed, Reason = "heading_unavailable" };
                var dx = (double)playerPosition.X - position.X;
                var dy = (double)playerPosition.Y - position.Y;
                var currentHeading = binding.Ped.Heading;
                if ((dx == 0 && dy == 0) || !ActionMotionTracker.IsFinite(currentHeading))
                    return new EffectObservation { Status = EffectStatus.Failed, Reason = "heading_unavailable" };
                var heading = Math.Atan2(-dx, dy) * 180 / Math.PI;
                var error = Math.Abs(((currentHeading - heading) % 360 + 540) % 360 - 180);
                if (task.ObservedActive && elapsed >= 250 && error <= 20)
                    return new EffectObservation { Status = EffectStatus.Completed, Reason = "heading_observed", Facts = new Dictionary<string, object> { ["heading_error_degrees"] = error, ["distance_meters"] = motion.DistanceMeters } };
            }
            else if (motion.DisplacementMeters > 1.5)
                return new EffectObservation { Status = EffectStatus.Failed, Reason = "actor_left_wait_position" };
            else if (task.ObservedActive && elapsed >= task.Duration)
                return new EffectObservation { Status = EffectStatus.Completed, Reason = "wait_position_observed", Facts = new Dictionary<string, object> { ["elapsed_ms"] = elapsed, ["displacement_meters"] = motion.DisplacementMeters, ["distance_meters"] = motion.DistanceMeters } };
            if ((!performing && elapsed > 500 && elapsed < task.Duration - 100) || elapsed > task.Duration + 1000)
                return new EffectObservation { Status = EffectStatus.TakenOver, Reason = "task_ended_without_observed_completion" };
            return new EffectObservation { Status = EffectStatus.Running };
        }
        public void Release(Command command, ActorState actor, string actionId)
        {
            OnScriptThread();
            if (tasks.TryGetValue(command.Id, out var task) && bindings.TryGetValue(actor.ActorId, out var binding) && Valid(binding))
            {
                var statusNow = Function.Call<int>(Hash.GET_SCRIPT_TASK_STATUS, binding.Ped.Handle, task.Hash);
                if (statusNow == 0 || statusNow == 1) binding.Ped.Task.ClearAll();
            }
            tasks.Remove(command.Id);
        }
        public void Reset() { OnScriptThread(); bindings.Clear(); tasks.Clear(); SelectedActorId = null; Recording = false; ClearSubtitle(); }
        public void ShowSubtitle(string speaker, string text) { OnScriptThread(); SubtitleRendered = false; subtitle = Clean(speaker + ": " + text); subtitleUntil = DateTimeOffset.UtcNow.AddSeconds(30); }
        public void ClearSubtitle() { OnScriptThread(); subtitle = ""; SubtitleRendered = false; }
        public void ShowStatus(string state, string message) { OnScriptThread(); Recording = state == "recording"; status = Clean(message); }
        private static string Clean(string text) => text.Replace("~", "").Replace("<", "").Replace(">", "");
        public void Render(string? typed)
        {
            OnScriptThread();
            if (Game.IsPaused) return;
            if (typed != null) GTA.UI.Screen.ShowSubtitle("You: " + Clean(typed) + "_", 100);
            else if (subtitle.Length > 0 && DateTimeOffset.UtcNow < subtitleUntil) { GTA.UI.Screen.ShowSubtitle(subtitle, 100); SubtitleRendered = true; }
            if (Recording) GTA.UI.Screen.ShowHelpTextThisFrame("Living Los Santos: Recording");
            else if (status.Length > 0) GTA.UI.Screen.ShowHelpTextThisFrame(status);
        }
    }
}
