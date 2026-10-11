using System;
using System.Collections.Generic;
using System.Linq;

namespace LivingNPC.Core
{
    public readonly struct TargetVector
    {
        public double X { get; }
        public double Y { get; }
        public double Z { get; }
        public TargetVector(double x, double y, double z) { X = x; Y = y; Z = z; }
    }

    public sealed class TargetCandidate
    {
        public int Handle { get; }
        public double Distance { get; }
        public TargetVector Position { get; }
        public TargetCandidate(int handle, double distance, TargetVector position)
        { Handle = handle; Distance = distance; Position = position; }
    }

    // Geometry only. Classification and binding remain on the script thread.
    public static class TargetScorer
    {
        public const double MaximumDistance = 8;
        public const double MaximumAngleDegrees = 45;

        public static IEnumerable<TargetCandidate> Rank(IEnumerable<TargetCandidate> candidates, TargetVector cameraPosition, TargetVector lookDirection)
        {
            return candidates.Select(candidate => new { Candidate = candidate, Score = Score(candidate, cameraPosition, lookDirection) })
                .Where(item => item.Score.HasValue).OrderBy(item => item.Score)
                .ThenBy(item => item.Candidate.Distance).ThenBy(item => item.Candidate.Handle).Select(item => item.Candidate);
        }

        public static double? Score(TargetCandidate candidate, TargetVector cameraPosition, TargetVector lookDirection)
        {
            if (!Finite(candidate.Distance) || candidate.Distance < 0 || candidate.Distance > MaximumDistance) return null;
            var dx = candidate.Position.X - cameraPosition.X;
            var dy = candidate.Position.Y - cameraPosition.Y;
            var dz = candidate.Position.Z - cameraPosition.Z;
            var offsetLength = Math.Sqrt(dx * dx + dy * dy + dz * dz);
            var lookLength = Math.Sqrt(lookDirection.X * lookDirection.X + lookDirection.Y * lookDirection.Y + lookDirection.Z * lookDirection.Z);
            if (!Finite(offsetLength) || !Finite(lookLength) || offsetLength <= 0 || lookLength <= 0) return null;
            var cosine = dx / offsetLength * (lookDirection.X / lookLength) + dy / offsetLength * (lookDirection.Y / lookLength) + dz / offsetLength * (lookDirection.Z / lookLength);
            var angle = Math.Acos(Math.Max(-1, Math.Min(1, cosine))) * 180 / Math.PI;
            if (!Finite(angle) || angle > MaximumAngleDegrees + 1e-9) return null;
            // Equal weights for normalized distance and angle; range stays player-relative.
            return candidate.Distance / MaximumDistance + Math.Min(angle, MaximumAngleDegrees) / MaximumAngleDegrees;
        }

        private static bool Finite(double value) => !double.IsNaN(value) && !double.IsInfinity(value);
    }
}
