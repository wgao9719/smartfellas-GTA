using System;
using System.Linq;
using GTA;
using GTA.Math;
using GTA.Native;
using LivingNPC.Core;
using Newtonsoft.Json.Linq;

namespace LivingNPC.GameAdapter
{
    internal sealed partial class GtaGameAdapter
    {
        private sealed class PavementNavigation : ILocalNavigation
        {
            private readonly Ped ped;
            public PavementNavigation(Ped ped) { this.ped = ped; }
            private static Vector3 Vector(TargetVector p) => new Vector3((float)p.X, (float)p.Y, (float)p.Z);
            public bool TryPavement(TargetVector requested, out TargetVector point)
            {
                point = default;
                if (!LocalActionController.ApprovedArea(requested)) return false;
                var result = new OutputArgument();
                if (!Function.Call<bool>(Hash.GET_SAFE_COORD_FOR_PED, (float)requested.X, (float)requested.Y, (float)requested.Z, true, result, 0)) return false;
                point = ToCoreVector(result.GetResult<Vector3>());
                return LocalActionController.ApprovedArea(point) && LocalActionController.Distance(requested, point) <= 1;
            }
            private bool Safe(TargetVector point)
            {
                if (!TryPavement(point, out _) || Function.Call<bool>(Hash.IS_POINT_ON_ROAD, (float)point.X, (float)point.Y, (float)point.Z, 0) ||
                    Function.Call<int>(Hash.GET_INTERIOR_AT_COORDS, (float)point.X, (float)point.Y, (float)point.Z) != 0) return false;
                return true;
            }
            public bool CorridorSafe(TargetVector from, TargetVector to)
            {
                var distance = LocalActionController.Distance(from, to);
                if (!ActionMotionTracker.IsFinite(distance) || distance > 30 || Math.Abs(from.Z - to.Z) > 1) return false;
                var midpoint = (Vector(from) + Vector(to)) * 0.5f;
                // Query each pool once per corridor, never once per pavement sample.
                if (World.GetNearbyVehicles(midpoint, (float)distance / 2 + 4).Length > 0 ||
                    World.GetNearbyProps(midpoint, (float)distance / 2 + 10).Any(prop => Function.Call<bool>(Hash.IS_ENTITY_A_MISSION_ENTITY, prop.Handle)) ||
                    World.GetNearbyPeds(midpoint, (float)distance / 2 + 10).Any(other => other.Handle != ped.Handle && other.Handle != Game.Player.Character.Handle &&
                        Function.Call<bool>(Hash.IS_ENTITY_A_MISSION_ENTITY, other.Handle))) return false;
                var steps = Math.Max(1, (int)Math.Ceiling(distance / 0.5));
                var dx = to.X - from.X; var dy = to.Y - from.Y;
                var horizontal = Math.Sqrt(dx * dx + dy * dy);
                var sideX = horizontal > 0 ? -dy / horizontal * 0.6 : 0.6;
                var sideY = horizontal > 0 ? dx / horizontal * 0.6 : 0;
                for (var i = 0; i <= steps; i++)
                {
                    var t = (double)i / steps;
                    var point = new TargetVector(from.X + dx * t, from.Y + dy * t, from.Z + (to.Z - from.Z) * t);
                    if (!Safe(point) || !Safe(new TargetVector(point.X + sideX, point.Y + sideY, point.Z)) ||
                        !Safe(new TargetVector(point.X - sideX, point.Y - sideY, point.Z))) return false;
                }
                if (distance < 0.1) return true;
                var ray = World.Raycast(Vector(from) + new Vector3(0, 0, 0.6f), Vector(to) + new Vector3(0, 0, 0.6f), 0.4f, IntersectFlags.Map | IntersectFlags.Objects | IntersectFlags.Vehicles, ped);
                return !ray.DidHit;
            }
        }
        private bool StartLocal(Command command, ActorState actor, Binding binding, string action, JObject args)
        {
            if (!TryPosition(binding.Ped, out var origin) || !TryPosition(Game.Player.Character, out var player)) return false;
            var threat = player;
            if (args["threat_actor_id"] is JValue threatId)
            {
                var target = TargetPed((string)threatId!, binding.Ped);
                if (args["threat_position"] != null || target == null || !TryPosition(target, out threat)) return false;
            }
            else if (args["threat_position"] is JObject point) threat = new Vector3((float)point["x"]!, (float)point["y"]!, (float)point["z"]!);
            if (action == "flee_from_threat" && origin.DistanceTo(threat) > 15) return false;
            var prior = tasks.Values.FirstOrDefault(t => t.ActorId == actor.ActorId);
            if (action == "stop_following" && (prior == null || prior.Action != "follow_player" || !TaskActive(binding.Ped, prior.Hash))) return false;
            if (prior != null && action != "stop_following") return false;
            var local = LocalActionController.Create(action, args, MonotonicMilliseconds(), ToCoreVector(origin), ToCoreVector(player), ToCoreVector(threat), new PavementNavigation(binding.Ped));
            if (local == null) return false;
            var task = new TaskRecord { ActorId = actor.ActorId, Action = action, Local = local, PriorHash = prior?.Hash ?? 0 };
            if (action == "play_verified_gesture")
            {
                switch (local.Gesture)
                {
                    case "wave": task.Dictionary = "friends@frj@ig_1"; task.Animation = "wave_a"; break;
                    case "shrug": task.Dictionary = "gestures@m@standing@casual"; task.Animation = "gesture_shrug_hard"; break;
                    case "point": task.Dictionary = "gestures@m@standing@casual"; task.Animation = "gesture_point"; break;
                    default: return false;
                }
                if (!Function.Call<bool>(Hash.DOES_ANIM_DICT_EXIST, task.Dictionary)) return false;
                Function.Call(Hash.REQUEST_ANIM_DICT, task.Dictionary);
                task.Hash = Game.GenerateHash("SCRIPT_TASK_PLAY_ANIM");
            }
            else IssueLocalTask(binding.Ped, task, MonotonicMilliseconds());
            tasks[command.Id] = task;
            return true;
        }
        private static bool TaskActive(Ped ped, int hash)
        {
            var status = Function.Call<int>(Hash.GET_SCRIPT_TASK_STATUS, ped.Handle, hash);
            return status == 0 || status == 1;
        }
        private static void IssueLocalTask(Ped ped, TaskRecord task, double now)
        {
            var local = task.Local!;
            if (local.Hold)
            {
                // A different task hash lets stop-follow observe replacement even while follow was holding.
                if (task.Action == "stop_following")
                {
                    Function.Call(Hash.TASK_PAUSE, ped.Handle, Math.Min(1000, (int)task.Local!.RemainingMilliseconds(now)));
                    task.Hash = Game.GenerateHash("SCRIPT_TASK_PAUSE");
                }
                else { ped.Task.StandStill(Math.Min(1000, (int)task.Local!.RemainingMilliseconds(now))); task.Hash = Game.GenerateHash("SCRIPT_TASK_STAND_STILL"); }
            }
            else
            {
                var destination = local.Destination;
                Function.Call(Hash.TASK_GO_STRAIGHT_TO_COORD, ped.Handle, (float)destination.X, (float)destination.Y, (float)destination.Z, 1.0f, (int)Math.Min(5000, task.Local!.RemainingMilliseconds(now)), 0.0f, 0.25f);
                task.Hash = Game.GenerateHash("SCRIPT_TASK_GO_STRAIGHT_TO_COORD");
            }
            task.Revision = local.Revision;
        }
        private EffectObservation ObserveLocal(TaskRecord task, Binding binding)
        {
            if (!TryPosition(binding.Ped, out var position) || !TryPosition(Game.Player.Character, out var player))
                return new EffectObservation { Status = EffectStatus.Failed, Reason = "movement_sample_unavailable" };
            var animationActive = false;
            if (task.Dictionary.Length > 0)
            {
                if (!task.AnimationDispatched && Function.Call<bool>(Hash.HAS_ANIM_DICT_LOADED, task.Dictionary))
                {
                    Function.Call(Hash.TASK_PLAY_ANIM, binding.Ped.Handle, task.Dictionary, task.Animation, 4.0f, -4.0f, 2500, 48, 0.0f, false, false, false);
                    task.AnimationDispatched = true;
                }
                animationActive = Function.Call<bool>(Hash.IS_ENTITY_PLAYING_ANIM, binding.Ped.Handle, task.Dictionary, task.Animation, 3);
            }
            var observation = task.Local!.Observe(MonotonicMilliseconds(), ToCoreVector(position), ToCoreVector(player),
                TaskActive(binding.Ped, task.Hash), animationActive, task.PriorHash != 0 && TaskActive(binding.Ped, task.PriorHash));
            if (observation.Status == EffectStatus.Running && task.Local.Revision != task.Revision && task.Dictionary.Length == 0)
                IssueLocalTask(binding.Ped, task, MonotonicMilliseconds());
            return observation;
        }
    }
}
