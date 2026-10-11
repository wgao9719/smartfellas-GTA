using System;
using System.Threading;

namespace LivingNPC.Core
{
    // Construction is unbound: SHVDN constructs on the domain thread and ticks on the script thread.
    public sealed class ThreadAffinity
    {
        private int thread = -1;
        public bool IsCurrent => Volatile.Read(ref thread) == Thread.CurrentThread.ManagedThreadId;
        public void Bind()
        {
            Interlocked.CompareExchange(ref thread, Thread.CurrentThread.ManagedThreadId, -1);
            Verify();
        }
        public void Verify()
        {
            if (!IsCurrent) throw new InvalidOperationException("Game API outside script thread");
        }
    }
}
