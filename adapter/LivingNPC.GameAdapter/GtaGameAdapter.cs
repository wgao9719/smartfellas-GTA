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
            public string ActorId = "", Instance = "";
            public int Marker, Model;
            public long Generation;
            public bool ModOwned = false;
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
        private readonly HashSet<int> civilianModels = new HashSet<int>(new[] {
            "a_m_y_business_01", "a_m_m_business_01", "a_f_y_business_01", "a_m_y_beach_01", "a_f_y_beach_01", "a_m_y_hipster_01"
        }.Select(Game.GenerateHash));
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
        public ActorState? SelectNearest(long generation)
        {
            OnScriptThread();
            if (!ReadWorld().Safe) return null;
            var player = Game.Player.Character;
            var ped = World.GetNearbyPeds(player, 8f).Where(p => p.Exists() && p.IsHuman && !p.IsDead && !p.IsPersistent && civilianModels.Contains(p.Model.Hash))
                .OrderBy(p => p.Position.DistanceTo(player.Position)).FirstOrDefault();
            if (ped == null) return null;
            var existing = bindings.Values.FirstOrDefault(b => b.Ped.Handle == ped.Handle && b.Generation == generation && Valid(b));
            if (existing != null) { SelectedActorId = existing.ActorId; return Resolve(existing.ActorId); }
            if (bindings.Count >= 36) return null;
            var id = Guid.NewGuid().ToString();
            var marker = new Random(Guid.NewGuid().GetHashCode()).Next(1, int.MaxValue);
            if (!Function.Call<bool>(Hash.DECOR_SET_INT, ped.Handle, Decorator, marker)) return null;
            var binding = new Binding { Ped = ped, ActorId = id, Instance = Guid.NewGuid().ToString(), Marker = marker, Model = ped.Model.Hash, Generation = generation };
            bindings[id] = binding; SelectedActorId = id;
            return Resolve(id);
        }
        public string[] PruneBindings(long generation)
        {
            OnScriptThread();
            var removed = new List<string>();
            foreach (var binding in bindings.Values.ToArray())
            {
                if (binding.ModOwned) continue;
                if (binding.Generation == generation && Valid(binding) && !binding.Ped.IsPersistent && binding.Ped.Position.DistanceTo(Game.Player.Character.Position) <= 60) continue;
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
            var ownership = binding.ModOwned ? Ownership.ModOwned : ped.IsPersistent ? Ownership.GameScriptOwned : Ownership.Ambient;
            return new ActorState { ActorId = actorId, RuntimeInstanceId = binding.Instance, Name = "Nearby resident", Handle = ped.Handle, Generation = binding.Generation,
                Exists = true, Alive = !ped.IsDead, MissionEntity = ped.IsPersistent, Ownership = ownership,
                Policy = ownership == Ownership.GameScriptOwned || !civilianModels.Contains(ped.Model.Hash) ? ControlPolicy.Protected : ControlPolicy.Ambient,
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
