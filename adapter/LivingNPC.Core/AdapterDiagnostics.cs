using System;
using System.Collections.Concurrent;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading.Tasks;

namespace LivingNPC.Core
{
    public static class AdapterPaths
    {
        public static string DataDirectory(string scriptsDirectory) => Path.Combine(Path.GetFullPath(scriptsDirectory), "LivingNPC");
        public static string Configuration(string scriptsDirectory) => Path.Combine(DataDirectory(scriptsDirectory), "LivingNPC.ini");
    }

    public static class TaskReleaseGuard
    {
        public static void Run(Action release, Action forget, AdapterLog log)
        {
            try { release(); }
            catch (Exception) { log.Write("task_release_failed"); }
            finally { forget(); }
        }
    }

    // Disk writes occur on one worker. Script-thread calls never wait for disk.
    // Callers supply operational codes only, never dialogue, URLs, or tokens.
    public sealed class AdapterLog : IDisposable
    {
        private readonly BlockingCollection<string> lines = new BlockingCollection<string>(256);
        private readonly Task writer;
        private bool disposed;
        public string FilePath { get; }
        public bool Failed { get; private set; }
        public AdapterLog(string directory, int maximumBytes = 2 * 1024 * 1024)
        {
            if (maximumBytes < 1024) throw new ArgumentOutOfRangeException(nameof(maximumBytes));
            FilePath = Path.Combine(directory, "LivingNPC-" + DateTimeOffset.UtcNow.ToString("yyyyMMddTHHmmssfffZ", CultureInfo.InvariantCulture) + "-" + Guid.NewGuid().ToString("N").Substring(0, 8) + ".log");
            writer = Task.Run(() =>
            {
                try
                {
                    Directory.CreateDirectory(directory);
                    using (var stream = new FileStream(FilePath, FileMode.CreateNew, FileAccess.Write, FileShare.Read))
                    {
                        foreach (var line in lines.GetConsumingEnumerable())
                        {
                            var bytes = Encoding.UTF8.GetBytes(line + "\n");
                            if (stream.Length + bytes.Length > maximumBytes) continue;
                            stream.Write(bytes, 0, bytes.Length);
                            stream.Flush();
                        }
                    }
                }
                catch (IOException) { Failed = true; }
                catch (UnauthorizedAccessException) { Failed = true; }
            });
        }
        public void Write(string code, string detail = "")
        {
            if (disposed || Failed) return;
            var text = (code + " " + detail).Replace('\r', ' ').Replace('\n', ' ');
            if (text.Length > 1024) text = text.Substring(0, 1024);
            var line = DateTimeOffset.UtcNow.ToString("o", CultureInfo.InvariantCulture) + " " + text;
            try { lines.TryAdd(line); }
            catch (InvalidOperationException) { }
        }
        public void Dispose()
        {
            if (disposed) return;
            disposed = true;
            lines.CompleteAdding();
            // Abort/reload joins diagnostics, never an ordinary Tick.
            writer.Wait(TimeSpan.FromSeconds(1));
        }
    }
}
