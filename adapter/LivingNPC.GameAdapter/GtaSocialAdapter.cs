using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using GTA;
using GTA.Math;
using GTA.Native;
using LivingNPC.Core;

namespace LivingNPC.GameAdapter
{
    internal sealed partial class GtaGameAdapter
    {
        private sealed class SocialPed
        {
            public Binding Binding = null!;
            public ActionMotionTracker Motion = null!;
            public int TaskHash;
            public bool Chat;
            public double PhaseAt;
        }
        private sealed class NativeSocialScene
        {
            public SocialPed[] Peds = Array.Empty<SocialPed>();
            public double Until;
        }
        private sealed class OverheadLine
        {
            public string Scene = "", Actor = "", Text = "";
            public SocialLineObservation? Observation;
        }
        private readonly Dictionary<string, NativeSocialScene> socialScenes = new Dictionary<string, NativeSocialScene>();
        private readonly Dictionary<string, OverheadLine> overhead = new Dictionary<string, OverheadLine>();
        private readonly Queue<Ped> nearbyScan = new Queue<Ped>();
        private readonly List<string> nearby = new List<string>();
        private DateTimeOffset nextNearbyScan;
        private int nextNearbySample;
        public string[] NearbyActors => nearby.Where(bindings.ContainsKey).ToArray();
        public int BoundActorCount => bindings.Count;
        private const string GestureDictionary = "gestures@m@standing@casual";
        private static readonly HashSet<string> SpeechContexts = new HashSet<string> { "GENERIC_HI", "GENERIC_BYE", "CHAT_STATE" };

        // At most one candidate is classified/bound and one nearby identity is sampled per tick.
        public ActorState? ScanNearby(long generation, DateTimeOffset now)
        {
            OnScriptThread();
            if (now >= nextNearbyScan && (nearbyScan.Count == 0 || nearby.Count >= 8))
            {
                nearbyScan.Clear();
                var player = Game.Player.Character;
                foreach (var ped in World.GetNearbyPeds(player, 35f).Where(p => p.Exists() && !p.IsDead && p.Handle != player.Handle).OrderBy(p => p.Position.DistanceTo(player.Position)).Take(32)) nearbyScan.Enqueue(ped);
                nearby.RemoveAll(id => !bindings.TryGetValue(id, out var b) || !Valid(b) || b.Ped.Position.DistanceTo(player.Position) > 35);
                nextNearbyScan = now.AddSeconds(1);
            }
            if (nearbyScan.Count > 0 && nearby.Count < 8)
            {
                var ped = nearbyScan.Dequeue();
                if (ped.Exists() && !ped.IsDead)
                {
                    var binding = bindings.Values.FirstOrDefault(b => b.Ped.Handle == ped.Handle && b.Generation == generation && Valid(b));
                    if (binding == null && bindings.Count < BindingRetention.MaxBindings)
                    {
                        var classification = PedClassifier.Classify(Sample(ped, null));
                        if (classification.Role == PedRole.Civilian && classification.Ownership == Ownership.Ambient && classification.Policy == ControlPolicy.Ambient && !ped.IsInVehicle() && !ped.IsInCombat && !ped.IsFleeing)
                        {
                            var candidate = new Binding { ActorId = Guid.NewGuid().ToString(), Instance = Guid.NewGuid().ToString(), Ped = ped, Generation = generation };
                            var sample = ReadBindingSample(candidate);
                            if (sample != null)
                            {
                                candidate.Guard = new RuntimeBindingGuard(sample);
                                if (candidate.Guard.IsValid) { binding = candidate; bindings[binding.ActorId] = binding; }
                            }
                        }
                    }
                    if (binding != null && !nearby.Contains(binding.ActorId)) nearby.Add(binding.ActorId);
                }
            }
            if (nearby.Count == 0) return null;
            nextNearbySample %= nearby.Count;
            return Resolve(nearby[nextNearbySample++]);
        }
        private bool IsSocialActor(string actorId) => socialScenes.Values.Any(s => s.Peds.Any(p => p.Binding.ActorId == actorId));
        public bool BeginSocial(string sceneId, ActorState[] actors, double seconds)
        {
            OnScriptThread();
            if (!ReadWorld().Safe || socialScenes.Count >= 2 || socialScenes.ContainsKey(sceneId) || actors.Length < 2 || actors.Length > 3 || seconds <= 0 || seconds > 45) return false;
            var scene = new NativeSocialScene { Until = MonotonicMilliseconds() + seconds * 1000 };
            var records = new List<SocialPed>();
            foreach (var actor in actors)
            {
                var current = Resolve(actor.ActorId);
                if (!SocialController.Eligible(current, actor.Generation) || current!.RuntimeInstanceId != actor.RuntimeInstanceId || IsSocialActor(actor.ActorId) || tasks.Values.Any(t => t.ActorId == actor.ActorId) || !bindings.TryGetValue(actor.ActorId, out var binding)) return false;
                if (actors.Any(a => a.AreaId != actor.AreaId || a.InteriorId != actor.InteriorId || SocialController.Distance(a, actor) > 6)) return false;
                var pos = binding.Ped.Position;
                records.Add(new SocialPed { Binding = binding, Motion = new ActionMotionTracker(MonotonicMilliseconds(), pos.X, pos.Y, pos.Z), PhaseAt = MonotonicMilliseconds() });
            }
            scene.Peds = records.ToArray(); socialScenes[sceneId] = scene;
            try
            {
                for (int i = 0; i < scene.Peds.Length; i++)
                {
                    var current = scene.Peds[i]; var peer = scene.Peds[(i + 1) % scene.Peds.Length].Binding.Ped;
                    if (current.Binding.Ped.Position.DistanceTo(peer.Position) > 3)
                    {
                        Function.Call(Hash.TASK_GO_TO_ENTITY, current.Binding.Ped.Handle, peer.Handle, 4000, 2.0f, 1.0f, 0f, 0);
                        current.TaskHash = Game.GenerateHash("SCRIPT_TASK_GO_TO_ENTITY");
                    }
                    else { current.Binding.Ped.Task.TurnTo(peer, 600); current.TaskHash = Game.GenerateHash("SCRIPT_TASK_TURN_PED_TO_FACE_ENTITY"); }
                }
                return true;
            }
            catch (Exception) { EndSocial(sceneId); return false; }
        }
        public bool SocialValid(string sceneId)
        {
            OnScriptThread();
            if (!socialScenes.TryGetValue(sceneId, out var scene) || MonotonicMilliseconds() >= scene.Until || !ReadWorld().Safe) return false;
            try
            {
                var now = MonotonicMilliseconds();
                foreach (var item in scene.Peds)
                {
                    var state = Resolve(item.Binding.ActorId);
                    if (!SocialController.Eligible(state, item.Binding.Generation) || state!.RuntimeInstanceId != item.Binding.Instance || !TryPosition(item.Binding.Ped, out var pos)) return false;
                    if (!item.Motion.TrySample(now, pos.X, pos.Y, pos.Z, out var sample) || sample!.DistanceMeters > 6) return false;
                    var status = Function.Call<int>(Hash.GET_SCRIPT_TASK_STATUS, item.Binding.Ped.Handle, item.TaskHash);
                    if (item.Chat && status != 0 && status != 1) return false;
                }
                for (int i = 0; i < scene.Peds.Length; i++)
                {
                    var item = scene.Peds[i]; var peer = scene.Peds[(i + 1) % scene.Peds.Length].Binding.Ped;
                    var distance = item.Binding.Ped.Position.DistanceTo(peer.Position);
                    if (distance > 6) return false;
                    if (!item.Chat && distance <= 3 && now - item.PhaseAt >= 600)
                    {
                        Function.Call(Hash.TASK_CHAT_TO_PED, item.Binding.Ped.Handle, peer.Handle, 16, 0f, 0f, 0f, 0f, 0f);
                        Function.Call(Hash.TASK_LOOK_AT_ENTITY, item.Binding.Ped.Handle, peer.Handle, Math.Min(30000, (int)(scene.Until - now)), 0, 2);
                        item.TaskHash = Game.GenerateHash("SCRIPT_TASK_CHAT_TO_PED"); item.Chat = true;
                    }
                    if (!item.Chat && now - item.PhaseAt > 4500) return false;
                }
                return true;
            }
            catch (Exception) { return false; }
        }
        public bool SocialReady(string sceneId) { OnScriptThread(); return socialScenes.TryGetValue(sceneId, out var scene) && scene.Peds.All(p => p.Chat); }
        public void EndSocial(string sceneId)
        {
            OnScriptThread();
            if (!socialScenes.TryGetValue(sceneId, out var scene)) return;
            foreach (var item in scene.Peds)
            {
                try
                {
                    // Never clear the game/mission's replacement tasks or suppress danger reactions.
                    var current = Resolve(item.Binding.ActorId);
                    if (!SocialController.Eligible(current, item.Binding.Generation) || current!.RuntimeInstanceId != item.Binding.Instance) continue;
                    var status = Function.Call<int>(Hash.GET_SCRIPT_TASK_STATUS, item.Binding.Ped.Handle, item.TaskHash);
                    if (status == 0 || status == 1) { Function.Call(Hash.STOP_ANIM_TASK, item.Binding.Ped.Handle, GestureDictionary, "gesture_hello", -4f); item.Binding.Ped.Task.ClearAll(); }
                }
                catch (Exception) { }
            }
            foreach (var key in overhead.Where(p => p.Value.Scene == sceneId).Select(p => p.Key).ToArray()) overhead.Remove(key);
            socialScenes.Remove(sceneId);
        }
        public void AmbientSpeech(string actorId, string context)
        {
            OnScriptThread();
            if (!SpeechContexts.Contains(context) || !bindings.TryGetValue(actorId, out var binding) || !Valid(binding) || !IsSocialActor(actorId)) return;
            Function.Call(Hash.PLAY_PED_AMBIENT_SPEECH_NATIVE, binding.Ped.Handle, context, "SPEECH_PARAMS_STANDARD");
        }
        public void ShowSocialLine(string sceneId, string commandId, string actorId, string text)
        {
            OnScriptThread();
            if (!socialScenes.ContainsKey(sceneId) || !IsSocialActor(actorId) || !DisplayText.IsSafe(text) || overhead.Count >= 4) return;
            overhead[commandId] = new OverheadLine { Scene = sceneId, Actor = actorId, Text = text };
            Function.Call(Hash.REQUEST_ANIM_DICT, GestureDictionary);
        }
        public SocialLineObservation? ObserveSocialLine(string commandId) { OnScriptThread(); return overhead.TryGetValue(commandId, out var line) ? line.Observation : null; }
        public void ClearSocialLine(string commandId) { OnScriptThread(); overhead.Remove(commandId); }
        private static void TextComponents(string text)
        {
            var chunk = new StringBuilder(); var iterator = StringInfo.GetTextElementEnumerator(text);
            while (iterator.MoveNext())
            {
                var element = iterator.GetTextElement();
                if (Encoding.UTF8.GetByteCount(chunk.ToString()) + Encoding.UTF8.GetByteCount(element) > 90) { Function.Call(Hash.ADD_TEXT_COMPONENT_SUBSTRING_PLAYER_NAME, chunk.ToString()); chunk.Clear(); }
                chunk.Append(element);
            }
            if (chunk.Length > 0) Function.Call(Hash.ADD_TEXT_COMPONENT_SUBSTRING_PLAYER_NAME, chunk.ToString());
        }
        private void RenderSocial()
        {
            if (!ReadWorld().Safe) return;
            foreach (var line in overhead.Values.ToArray())
            {
                try
                {
                    if (!socialScenes.TryGetValue(line.Scene, out var scene) || !bindings.TryGetValue(line.Actor, out var speaker) || !Valid(speaker)) continue;
                    var actor = Resolve(line.Actor); var player = Game.Player.Character;
                    if (!SocialController.Eligible(actor, speaker.Generation, 12) || Function.Call<int>(Hash.GET_INTERIOR_FROM_ENTITY, player.Handle).ToString(CultureInfo.InvariantCulture) != actor!.InteriorId || !Function.Call<bool>(Hash.HAS_ENTITY_CLEAR_LOS_TO_ENTITY, player.Handle, speaker.Ped.Handle, 17)) continue;
                    var head = speaker.Ped.Position + new Vector3(0, 0, 2.1f);
                    using (var x = new OutputArgument())
                    using (var y = new OutputArgument())
                    {
                        if (!Function.Call<bool>(Hash.GET_SCREEN_COORD_FROM_WORLD_COORD, head.X, head.Y, head.Z, x, y)) continue;
                        var sx = x.GetResult<float>(); var sy = y.GetResult<float>();
                        Function.Call(Hash.SET_TEXT_SCALE, 0f, 0.30f);
                        Function.Call(Hash.SET_TEXT_FONT, 0);
                        Function.Call(Hash.SET_TEXT_COLOUR, 255, 255, 255, (int)(255 * Math.Min(1, Math.Max(0.2, (12 - actor.Distance) / 4))));
                        Function.Call(Hash.SET_TEXT_CENTRE, true);
                        Function.Call(Hash.SET_TEXT_WRAP, Math.Max(0f, sx - 0.15f), Math.Min(1f, sx + 0.15f));
                        Function.Call(Hash.SET_TEXT_OUTLINE);
                        Function.Call(Hash.BEGIN_TEXT_COMMAND_DISPLAY_TEXT, "STRING"); TextComponents(line.Text);
                        Function.Call(Hash.END_TEXT_COMMAND_DISPLAY_TEXT, sx, sy, 0);
                        if (line.Observation != null) continue;
                        var world = ReadWorld();
                        var actors = scene.Peds.Select(p => Resolve(p.Binding.ActorId)).ToArray();
                        if (actors.Any(a => !SocialController.Eligible(a, speaker.Generation, 12))) continue;
                        var stamp = DateTimeOffset.UtcNow;
                        var witnesses = new List<object>();
                        foreach (var listener in actors.Select(a => a!).Where(a => a.ActorId != line.Actor))
                        {
                            var los = Function.Call<bool>(Hash.HAS_ENTITY_CLEAR_LOS_TO_ENTITY, bindings[listener.ActorId].Ped.Handle, speaker.Ped.Handle, 17);
                            witnesses.Add(new { actor_id = listener.ActorId, runtime_instance_id = listener.RuntimeInstanceId, sampled_at = stamp.ToString("O"), position = new { x = listener.X, y = listener.Y, z = listener.Z }, area_id = listener.AreaId, interior_id = listener.InteriorId, exists = listener.Exists, alive = listener.Alive, line_of_sight = los, can_hear = los && listener.InteriorId == actor.InteriorId && SocialController.Distance(listener, actor) <= 10, confidence = 0.9 });
                        }
                        line.Observation = new SocialLineObservation { DisplayedAt = stamp, World = world, Actors = actors.Select(a => a!).ToArray(), Witnesses = witnesses.ToArray() };
                        if (Function.Call<bool>(Hash.HAS_ANIM_DICT_LOADED, GestureDictionary)) Function.Call(Hash.TASK_PLAY_ANIM, speaker.Ped.Handle, GestureDictionary, "gesture_hello", 4f, -4f, 1000, 48, 0f, false, false, false);
                    }
                }
                catch (Exception) { /* Missing native evidence never receives a delivery receipt. */ }
            }
        }
    }
}
