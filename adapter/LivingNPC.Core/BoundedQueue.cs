using System;
using System.Collections.Generic;

namespace LivingNPC.Core
{
    public sealed class BoundedQueue<T>
    {
        private readonly Queue<T> values = new Queue<T>();
        private readonly object gate = new object();
        public int Capacity { get; }
        public BoundedQueue(int capacity) { if (capacity <= 0) throw new ArgumentOutOfRangeException(nameof(capacity)); Capacity = capacity; }
        public int Count { get { lock (gate) return values.Count; } }
        public int Remaining { get { lock (gate) return Capacity - values.Count; } }
        public bool TryAdd(T value) { lock (gate) { if (values.Count == Capacity) return false; values.Enqueue(value); return true; } }
        public bool TryTake(out T value) { lock (gate) { if (values.Count == 0) { value = default!; return false; } value = values.Dequeue(); return true; } }
        public bool TryPeek(out T value) { lock (gate) { if (values.Count == 0) { value = default!; return false; } value = values.Peek(); return true; } }
        public bool TryRemove(T expected) { lock (gate) { if (values.Count == 0 || !EqualityComparer<T>.Default.Equals(values.Peek(), expected)) return false; values.Dequeue(); return true; } }
        public void ReplaceOldest(T value) { lock (gate) { if (values.Count == Capacity) values.Dequeue(); values.Enqueue(value); } }
        public void Clear() { lock (gate) values.Clear(); }
    }
}
