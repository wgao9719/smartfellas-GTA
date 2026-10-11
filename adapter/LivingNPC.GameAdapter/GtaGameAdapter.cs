using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
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
            public RuntimeBindingGuard Guard = null!;
            public long Generation;
            public bool ModOwned = false, TookPersistence;
            public PedFacts? Facts;
            public int FactsFrame = -1;
        }
        private sealed class TaskRecord
        {
            public int Duration, Hash;
            public ActionMotionTracker Motion = null!;
            public bool ObservedActive;
        }
        private readonly AdapterLog log;
        private readonly ThreadAffinity thread = new ThreadAffinity();
        private readonly Dictionary<string, Binding> bindings = new Dictionary<string, Binding>();
        private readonly Dictionary<string, TaskRecord> tasks = new Dictionary<string, TaskRecord>();
        private readonly List<string> dropped = new List<string>();
        private readonly HashSet<string> targetFailures = new HashSet<string>();
        // Debug builds with EnableUnverifiedPinPersistence only.
        public bool PinPersistence { get; set; }
        private string subtitle = "", status = "Living Los Santos: connecting";
        private DateTimeOffset subtitleUntil;
        public string? SelectedActorId { get; private set; }
        public string? TargetError { get; private set; }
        public void ClearSelection() { OnScriptThread(); SelectedActorId = null; }
        public bool Recording { get; private set; }
        public bool SubtitleRendered { get; private set; }
        public void Forget(Command command) { OnScriptThread(); tasks.Remove(command.Id); }
        public GtaGameAdapter(AdapterLog log)
        {
            this.log = log;
        }
        public bool IsScriptThread => thread.IsCurrent;
        public void BindScriptThread() => thread.Bind();
        private void OnScriptThread() => thread.Verify();
        public WorldState ReadWorld()
        {
            OnScriptThread();
            var player = Game.Player.Character;
            string protagonist = player.Model.Hash == Game.GenerateHash("player_zero") ? "michael" : player.Model.Hash == Game.GenerateHash("player_one") ? "franklin" : player.Model.Hash == Game.GenerateHash("player_two") ? "trevor" : "unknown";
            return new WorldState { Online = Function.Call<bool>(Hash.NETWORK_IS_GAME_IN_PROGRESS), MissionActive = Game.IsMissionActive,
                CutsceneActive = Game.IsCutsceneActive, RandomEventActive = Game.IsRandomEventActive, Paused = Game.IsPaused,
                Loading = Game.IsLoading, GameDatetime = World.CurrentDate, ProtagonistId = protagonist, Known = protagonist != "unknown" };
        }
        // Binds a nearby civilian in the camera cone using read-only continuity samples.
        public ActorState? SelectTarget(long generation, out ActorState? refusal)
        {
            OnScriptThread(); refusal = null; SelectedActorId = null; TargetError = null;
            try { return SelectTargetCore(generation, out refusal); }
            catch (Exception) { return TargetFailed("target_sampling_failed", "pedestrian facts could not be sampled. Try again."); }
        }
        private ActorState? TargetFailed(string code, string message)
        {
            SelectedActorId = null; TargetError = "Targeting unavailable: " + message;
            if (targetFailures.Add(code)) log.Write(code);
            return null;
        }
        private ActorState? SelectTargetCore(long generation, out ActorState? refusal)
        {
            refusal = null;
            if (!ReadWorld().Safe) return null;
            var player = Game.Player.Character;
            if (!TryPosition(player, out var playerPosition))
                return TargetFailed("target_sampling_failed", "player position could not be sampled. Try again.");
            var cameraPosition = ToCoreVector(GameplayCamera.Position); var lookDirection = ToCoreVector(GameplayCamera.Direction);
            if (!TargetScorer.HasCameraGeometry(cameraPosition, lookDirection))
                return TargetFailed("target_camera_unavailable", "camera geometry could not be sampled. Try again.");
            var nearby = new Dictionary<int, Ped>(); var candidates = new List<TargetCandidate>(); var samplingFailed = false;
            foreach (var ped in World.GetAllPeds())
            {
                if (ped.Handle == player.Handle || !ped.Exists() || ped.IsDead || nearby.ContainsKey(ped.Handle)) continue;
                if (!TryPosition(ped, out var position)) { samplingFailed = true; continue; }
                var distance = position.DistanceTo(playerPosition);
                if (!TargetScorer.IsCandidate(ped.Handle, player.Handle, true, true, distance)) continue;
                nearby.Add(ped.Handle, ped); candidates.Add(new TargetCandidate(ped.Handle, distance, ToCoreVector(position)));
            }
            var eligible = new List<TargetCandidate>(); var registered = new Dictionary<int, Binding>();
            // Refusal feedback remains nearest by player distance, independently of the camera cone.
            foreach (var candidate in candidates.OrderBy(c => c.Distance).ThenBy(c => c.Handle))
            {
                var ped = nearby[candidate.Handle];
                var existing = bindings.Values.FirstOrDefault(b => b.Ped.Handle == ped.Handle);
                if (existing != null && (existing.Generation != generation || !Valid(existing))) { DropBinding(existing); existing = null; }
                var facts = Sample(ped, existing);
                if (facts == null) { samplingFailed = true; continue; }
                var classified = PedClassifier.Classify(facts);
                if (!TargetScorer.CanBind(classified, existing != null))
                {
                    refusal ??= new ActorState { Name = PedRoleTable.Label(classified.Role), Role = classified.Role, RoleReason = classified.Reason, Ownership = classified.Ownership,
                        Policy = classified.Policy, Facts = facts, Exists = true, Alive = true, Generation = generation, Distance = (float)candidate.Distance };
                    continue;
                }
                eligible.Add(candidate);
                if (existing != null) registered[ped.Handle] = existing;
            }
            foreach (var candidate in TargetScorer.Rank(eligible, cameraPosition, lookDirection))
            {
                if (registered.TryGetValue(candidate.Handle, out var existing)) return SelectBinding(existing);
                var ped = nearby[candidate.Handle];
                if (bindings.Count >= 36) return TargetFailed("target_binding_limit", "runtime binding limit reached. Try again after leaving the area.");
                var id = Guid.NewGuid().ToString();
                var binding = new Binding { Ped = ped, ActorId = id, Instance = Guid.NewGuid().ToString(), Generation = generation };
                var sample = ReadBindingSample(binding);
                if (sample == null) return TargetFailed("target_sampling_failed", "pedestrian facts could not be sampled. Try again.");
                binding.Guard = new RuntimeBindingGuard(sample);
                if (!binding.Guard.IsValid)
                    return TargetFailed("target_binding_lost", "pedestrian identity could not be established. Try again.");
                bindings.Add(id, binding);
                return SelectBinding(binding);
            }
            return samplingFailed ? TargetFailed("target_sampling_failed", "pedestrian facts could not be sampled. Try again.") : null;
        }
        private ActorState? SelectBinding(Binding binding)
        {
            var actor = Resolve(binding.ActorId);
            if (actor == null) { DropBinding(binding); return TargetFailed("target_binding_lost", "pedestrian identity changed during selection. Try again."); }
            if (actor.Facts == null) { DropBinding(binding); return TargetFailed("target_sampling_failed", "pedestrian facts could not be sampled. Try again."); }
            SelectedActorId = binding.ActorId;
            return actor;
        }
        private void DropBinding(Binding binding)
        {
            if (!bindings.Remove(binding.ActorId)) return;
            dropped.Add(binding.ActorId); ReleasePersistence(binding); binding.Guard.Invalidate();
            if (SelectedActorId == binding.ActorId) SelectedActorId = null;
        }
        private static TargetVector ToCoreVector(Vector3 vector) => new TargetVector(vector.X, vector.Y, vector.Z);
        // Facts are refreshed each frame, including role signals on same-model handles.
        private PedFacts? Sample(Ped ped, Binding? binding)
        {
            try
            {
                if (ped == null || !ped.Exists()) return null;
                var frame = Game.FrameCount;
                if (binding?.Facts != null && binding.FactsFrame == frame) return binding.Facts;
                var model = ped.Model;
                var facts = new PedFacts { ModelHash = model.Hash, Human = ped.IsHuman, AnimalModel = model.IsAnimalPed, PedType = Function.Call<int>(Hash.GET_PED_TYPE, ped.Handle),
                    RelationshipGroupHash = ped.RelationshipGroup.Hash, Cuffed = ped.IsCuffed, InPoliceVehicle = ped.IsInPoliceVehicle,
                    PlayerGroup = Function.Call<bool>(Hash.IS_PED_GROUP_MEMBER, ped.Handle, Function.Call<int>(Hash.GET_PED_GROUP_INDEX, Game.Player.Character.Handle)),
                    MissionEntity = ped.IsPersistent, InModRegistry = binding?.ModOwned == true, TookPersistence = binding?.TookPersistence == true,
                    BelongsToModScript = Function.Call<bool>(Hash.DOES_ENTITY_BELONG_TO_THIS_SCRIPT, ped.Handle, false) };
#if DEVELOPMENT
                facts.OwningScript = OwningScript(ped);
#endif
                if (binding != null) { binding.Facts = facts; binding.FactsFrame = frame; }
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
            foreach (var binding in bindings.Values.ToArray())
            {
                // Every tick, including pins and actors that are not selected.
                binding.Facts = null;
                try
                {
                    if (binding.Generation == generation && Valid(binding) && (binding.ModOwned || binding.Ped.Position.DistanceTo(Game.Player.Character.Position) <= 60)) continue;
                }
                catch (Exception) { binding.Guard.Invalidate(); }
                DropBinding(binding);
            }
            var removed = dropped.ToArray(); dropped.Clear(); return removed;
        }
        // FR-ID-03: the same actor id becomes mod-owned. Any refusal drops the binding.
        public bool Pin(Command command, ActorState actor, string displayName)
        {
            OnScriptThread();
            if (!bindings.TryGetValue(actor.ActorId, out var binding)) return false;
            binding.Facts = null;
            var current = Resolve(actor.ActorId);
            var before = ReadBindingSample(binding);
            if (!binding.Guard.Validate(before) || binding.Instance != actor.RuntimeInstanceId || binding.Generation != actor.Generation ||
                command.ActorId != actor.ActorId || command.RuntimeInstanceId != binding.Instance || command.RuntimeGeneration != binding.Generation ||
                binding.ModOwned || PinPolicy.Check(current, ReadWorld(), binding.Generation) != null || bindings.Values.Count(b => b.ModOwned) >= PinPolicy.MaxPins)
            {
                DropBinding(binding); return false;
            }
            try
            {
                binding.ModOwned = true;
#if DEVELOPMENT
                // Unverified: IsPersistent=true would clear the ped's tasks.
                if (PinPersistence) { binding.Ped.SetIsPersistentNoClearTask(true); binding.TookPersistence = true; }
#endif
                binding.Facts = null;
                var after = ReadBindingSample(binding);
                if (after != null && binding.Guard.AcceptPinTransition(before!, after, binding.TookPersistence))
                { binding.Kind = "spawned_persistent"; binding.Name = displayName; return true; }
            }
            catch (Exception) { }
            binding.Guard.Invalidate(); DropBinding(binding); return false;
        }
        // Never release a ped another script has taken.
        private void ReleasePersistence(Binding binding)
        {
            if (!binding.TookPersistence) return;
            binding.Facts = null;
            try { if (Valid(binding) && Function.Call<bool>(Hash.DOES_ENTITY_BELONG_TO_THIS_SCRIPT, binding.Ped.Handle, false)) binding.Ped.MarkAsNoLongerNeeded(); }
            catch (Exception) { }
            binding.TookPersistence = false;
        }
        private RuntimeBindingSample? ReadBindingSample(Binding binding)
        {
            try
            {
                var ped = binding.Ped;
                if (!ped.Exists()) return null;
                return new RuntimeBindingSample(true, !ped.IsDead, ped.Handle, ped.Model.Hash, ToCoreVector(ped.Position),
                    (long)MonotonicMilliseconds(), ped.Health, ped.MaxHealth, ped.IsInVehicle(), Sample(ped, binding));
            }
            catch (Exception) { return null; }
        }
        private bool Valid(Binding binding)
        {
            return binding.Guard.IsValid && binding.Guard.Validate(ReadBindingSample(binding));
        }
        public ActorState? Resolve(string actorId)
        {
            OnScriptThread();
            if (!bindings.TryGetValue(actorId, out var binding)) return null;
            if (!Valid(binding)) { DropBinding(binding); return null; }
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
            TaskReleaseGuard.Run(() =>
            {
                if (!tasks.TryGetValue(command.Id, out var task) || !bindings.TryGetValue(actor.ActorId, out var binding) || command.ActorId != actor.ActorId ||
                    binding.Instance != actor.RuntimeInstanceId || binding.Instance != command.RuntimeInstanceId ||
                    binding.Generation != actor.Generation || binding.Generation != command.RuntimeGeneration) return;
                // Continuity includes current ownership. Refresh even within the same frame.
                binding.Facts = null;
                if (!Valid(binding)) return;
                var statusNow = Function.Call<int>(Hash.GET_SCRIPT_TASK_STATUS, binding.Ped.Handle, task.Hash);
                if (statusNow == 0 || statusNow == 1) binding.Ped.Task.ClearAll();
            }, () => tasks.Remove(command.Id), log);
        }
        public void Reset() { OnScriptThread(); foreach (var binding in bindings.Values) { ReleasePersistence(binding); binding.Guard.Invalidate(); } bindings.Clear(); dropped.Clear(); tasks.Clear(); SelectedActorId = null; Recording = false; ClearSubtitle(); }
        // Dialogue renders verbatim. Unsafe text never renders, so it never earns a subtitle receipt.
        public void ShowSubtitle(string speaker, string text) { OnScriptThread(); SubtitleRendered = false; subtitle = DisplayText.IsSafe(speaker) && DisplayText.IsSafe(text) ? speaker + ": " + text : ""; subtitleUntil = DateTimeOffset.UtcNow.AddSeconds(30); }
        public void ClearSubtitle() { OnScriptThread(); subtitle = ""; SubtitleRendered = false; }
        public void ShowStatus(string state, string message) { OnScriptThread(); Recording = state == "recording"; status = DisplayText.Clean(message); }
        public void Render(string? typed, bool whispering = false)
        {
            OnScriptThread();
            if (Game.IsPaused) return;
            if (typed != null) GTA.UI.Screen.ShowSubtitle(TypedInput.Echo(typed) + "_", 100);
            else if (subtitle.Length > 0 && DateTimeOffset.UtcNow < subtitleUntil) { GTA.UI.Screen.ShowSubtitle(subtitle, 100); SubtitleRendered = true; }
            if (Recording) GTA.UI.Screen.ShowHelpTextThisFrame(whispering ? "Living Los Santos: Recording (whispering)" : "Living Los Santos: Recording");
            else if (status.Length > 0) GTA.UI.Screen.ShowHelpTextThisFrame(status);
        }
    }
}
