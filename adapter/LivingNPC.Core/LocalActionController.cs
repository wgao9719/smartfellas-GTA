using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace LivingNPC.Core
{
    public interface ILocalNavigation
    {
        bool TryPavement(TargetVector requested, out TargetVector point);
        bool CorridorSafe(TargetVector from, TargetVector to);
    }

    // Native observations enter here; neither model text nor task dispatch proves success.
    public sealed class LocalActionController
    {
        public const double ArrivalTolerance = 0.75, SegmentLength = 3;
        private readonly string action;
        private readonly ILocalNavigation navigation;
        private readonly TargetVector origin, threat;
        private readonly ActionMotionTracker motion;
        private readonly double duration, radius, followLimit, fleeDistance, initialThreatDistance;
        private TargetVector goal, lastPosition;
        private double issuedAt, activeMilliseconds, lastSample;
        private bool observedTask, observedLeg;
        private int wanderStep;
        private EffectObservation? terminal;
        public TargetVector Destination { get; private set; }
        public bool Hold { get; private set; }
        public int Revision { get; private set; }
        public string Gesture { get; }
        public double MaximumPlayerDistance => action == "follow_player" ? followLimit : 30;
        public double RemainingMilliseconds(double now) => Math.Max(250, duration - (now - issuedAt));

        public static bool Moves(string id) => id == "follow_player" || id == "go_to_safe_point" || id == "wander_nearby" || id == "flee_from_threat";
        public static bool Supported(string id) => Moves(id) || id == "stop_following" || id == "play_verified_gesture";
        public static double Distance(TargetVector a, TargetVector b) => Math.Sqrt(Math.Pow(a.X - b.X, 2) + Math.Pow(a.Y - b.Y, 2) + Math.Pow(a.Z - b.Z, 2));
        private static TargetVector Towards(TargetVector from, TargetVector to, double distance)
        {
            var length = Distance(from, to);
            var scale = length > 0 ? Math.Min(1, distance / length) : 0;
            return new TargetVector(from.X + (to.X - from.X) * scale, from.Y + (to.Y - from.Y) * scale, from.Z + (to.Z - from.Z) * scale);
        }
        // Positive public-area allowlist, deliberately limited until a larger map is reviewed.
        // Legion Square plaza; roads, interiors, obstructions and script entities are checked natively too.
        public static bool ApprovedArea(TargetVector point) =>
            point.X >= 160 && point.X <= 240 && point.Y >= -1000 && point.Y <= -870 && point.Z >= 25 && point.Z <= 40;

        private LocalActionController(string action, JObject args, double now, TargetVector origin, TargetVector player, TargetVector threat, ILocalNavigation navigation)
        {
            this.action = action; this.origin = lastPosition = origin; this.threat = threat; this.navigation = navigation;
            motion = new ActionMotionTracker(now, origin.X, origin.Y, origin.Z); issuedAt = lastSample = now;
            duration = 1000 * ((double?)args["duration_seconds"] ?? (action == "follow_player" ? 20 : 10));
            radius = (double?)args["radius"] ?? 5; followLimit = (double?)args["max_follow_meters"] ?? 15;
            fleeDistance = (double?)args["distance_meters"] ?? 6; initialThreatDistance = Distance(origin, threat);
            Gesture = (string?)args["gesture_id"] ?? "";
            goal = origin;
            if (action == "go_to_safe_point" && args["point"] is JObject point)
                goal = new TargetVector((double)point["x"]!, (double)point["y"]!, (double)point["z"]!);
            else if (action == "go_to_safe_point") goal = new TargetVector(origin.X + 4, origin.Y, origin.Z);
            else if (action == "flee_from_threat")
            {
                var dx = origin.X - threat.X; var dy = origin.Y - threat.Y; var length = Math.Sqrt(dx * dx + dy * dy);
                if (length < 0.1) { Fail("threat_direction_unavailable"); return; }
                goal = new TargetVector(origin.X + dx / length * fleeDistance, origin.Y + dy / length * fleeDistance, origin.Z);
            }
            if (!motion.IsValid || !ActionMotionTracker.IsFinite(Distance(origin, player))) { Fail("movement_sample_unavailable"); return; }
            if (!Moves(action)) { Destination = origin; Hold = true; Revision = 1; return; }
            if (action == "follow_player" && Distance(origin, player) > followLimit) { Fail("follow_distance_exceeded"); return; }
            if (action == "wander_nearby") goal = WanderGoal();
            if (action != "follow_player" && !ValidateGoal()) { Fail("no_safe_destination"); return; }
            Plan(now, origin, player);
        }
        public static LocalActionController? Create(string action, JObject args, double now, TargetVector origin, TargetVector player, TargetVector threat, ILocalNavigation navigation)
        {
            if (!Supported(action) || ActionRegistry.Default.ValidateArguments(action, args) != null) return null;
            var controller = new LocalActionController(action, args, now, origin, player, threat, navigation);
            return controller.terminal == null ? controller : null;
        }
        private bool ValidateGoal()
        {
            if (Distance(origin, goal) > (action == "wander_nearby" ? radius : 15) || !navigation.TryPavement(goal, out var safe)) return false;
            if (Distance(goal, safe) > 1 || Distance(origin, safe) > (action == "wander_nearby" ? radius : 15) || Distance(origin, safe) < 1) return false;
            goal = safe;
            return navigation.CorridorSafe(origin, goal);
        }
        private TargetVector WanderGoal()
        {
            var angle = wanderStep++ * Math.PI / 2;
            return new TargetVector(origin.X + Math.Cos(angle) * Math.Min(4, radius), origin.Y + Math.Sin(angle) * Math.Min(4, radius), origin.Z);
        }
        private void Plan(double now, TargetVector position, TargetVector player)
        {
            var target = action == "follow_player" ? Towards(position, player, Math.Max(0, Distance(position, player) - 2)) : goal;
            Hold = Distance(position, target) <= ArrivalTolerance;
            Destination = Hold ? position : Towards(position, target, SegmentLength);
            if (!navigation.CorridorSafe(position, Destination)) { Fail("unsafe_route"); return; }
            issuedAt = now; observedLeg = false; Revision++;
        }
        private EffectObservation Fail(string reason) => terminal = new EffectObservation { Status = EffectStatus.Failed, Reason = reason };
        private EffectObservation Complete(string reason, ActionMotionSample sample, TargetVector position, TargetVector player)
        {
            return terminal = new EffectObservation { Status = EffectStatus.Completed, Reason = reason, Facts = new Dictionary<string, object> {
                ["elapsed_ms"] = sample.ElapsedMilliseconds, ["distance_meters"] = sample.DistanceMeters,
                ["displacement_meters"] = sample.DisplacementMeters, ["player_distance_meters"] = Distance(position, player),
                ["destination_error_meters"] = Distance(position, goal), ["threat_distance_meters"] = Distance(position, threat),
                ["task_observed"] = observedTask, ["animation_observed"] = action == "play_verified_gesture",
                ["follow_task_absent"] = action == "stop_following" } };
        }
        public EffectObservation Observe(double now, TargetVector position, TargetVector player, bool taskActive, bool animationActive = false, bool priorFollowActive = false)
        {
            if (terminal != null) return terminal;
            if (!motion.TrySample(now, position.X, position.Y, position.Z, out var sample)) return Fail(motion.FailureReason);
            if (!ActionMotionTracker.IsFinite(Distance(position, player))) return Fail("player_sample_unavailable");
            var interval = now - lastSample; lastSample = now;
            var elapsed = sample!.ElapsedMilliseconds;
            if (taskActive) { observedTask = observedLeg = true; }
            if (action == "play_verified_gesture")
            {
                if (animationActive) activeMilliseconds += interval;
                else if (activeMilliseconds > 0) return Fail("animation_interrupted");
                if (sample.DisplacementMeters > 1.5) return Fail("actor_left_gesture_position");
                if (activeMilliseconds >= 1000 && animationActive) return Complete("animation_observed", sample, position, player);
                if (elapsed > 5000) return Fail("animation_not_observed");
                return new EffectObservation { Status = EffectStatus.Running };
            }
            if (action == "stop_following")
            {
                if (observedTask && !taskActive)
                    return terminal = new EffectObservation { Status = EffectStatus.TakenOver, Reason = "stop_task_taken_over" };
                if (sample.DisplacementMeters > 1.5) return Fail("actor_did_not_stop");
                if (!priorFollowActive && taskActive && observedTask && elapsed >= 250) return Complete("follow_stop_observed", sample, position, player);
                if (elapsed > 1500) return Fail("follow_stop_not_observed");
                return new EffectObservation { Status = EffectStatus.Running };
            }
            if (!navigation.CorridorSafe(lastPosition, position)) return Fail("unsafe_route");
            lastPosition = position;
            if (Distance(position, player) > MaximumPlayerDistance) return Fail("follow_distance_exceeded");
            if (sample.DistanceMeters > (action == "follow_player" ? 60 : 40)) return Fail("travel_budget_exceeded");
            if (action == "wander_nearby" && sample.DisplacementMeters > radius) return Fail("wander_radius_exceeded");
            // An unexpected task ending is a takeover, even when the duration just elapsed.
            var atLeg = Distance(position, Destination) <= ArrivalTolerance;
            if (!taskActive && (observedLeg || now - issuedAt > 500))
                return terminal = new EffectObservation { Status = EffectStatus.TakenOver, Reason = "task_ended_without_observed_completion" };
            if (observedTask && action == "go_to_safe_point" && Distance(position, goal) <= ArrivalTolerance)
                return Complete("safe_point_arrival_observed", sample, position, player);
            if (observedTask && action == "flee_from_threat" && Distance(position, threat) >= initialThreatDistance + fleeDistance - ArrivalTolerance)
                return Complete("threat_distance_observed", sample, position, player);
            if (elapsed >= duration)
            {
                if (observedTask && taskActive && (action == "follow_player" || action == "wander_nearby" && sample.DistanceMeters >= 0.5))
                    return Complete(action == "follow_player" ? "follow_interval_observed" : "wander_interval_observed", sample, position, player);
                return Fail("destination_timeout");
            }
            if (observedLeg && (atLeg || Hold) && now - issuedAt >= 250)
            {
                if (action == "wander_nearby" && Distance(position, goal) <= ArrivalTolerance)
                {
                    goal = WanderGoal();
                    if (!ValidateGoal()) return Fail("no_safe_destination");
                }
                Plan(now, position, player);
                if (terminal != null) return terminal;
            }
            return new EffectObservation { Status = EffectStatus.Running };
        }
    }
}
