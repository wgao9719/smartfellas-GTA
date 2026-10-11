using System;
using System.IO;
using System.Linq;
using LivingNPC.Core;
using Xunit;

public sealed class DiagnosticsTests
{
    [Fact]
    public void InstalledConfigurationAndDiagnosticsShareDataDirectory()
    {
        var scripts = Path.Combine(Path.GetTempPath(), "game", "scripts");
        Assert.Equal(Path.Combine(scripts, "LivingNPC", "LivingNPC.ini"), AdapterPaths.Configuration(scripts));
        Assert.Equal(Path.GetDirectoryName(AdapterPaths.Configuration(scripts)), AdapterPaths.DataDirectory(scripts));
    }

    [Fact]
    public void LoggerUsesUniqueTimestampedFilesAndBoundsEachFile()
    {
        var directory = Path.Combine(Path.GetTempPath(), "lls-log-" + Guid.NewGuid().ToString("N"));
        try
        {
            string first;
            using (var log = new AdapterLog(directory, 2048))
            {
                first = log.FilePath;
                log.Write("adapter_loaded", "version=fixture\nsecond-line");
                for (var i = 0; i < 100; i++) log.Write("fixture", new string('x', 800));
            }
            using (var log = new AdapterLog(directory))
            {
                Assert.NotEqual(first, log.FilePath);
                log.Write("adapter_stopped");
            }
            Assert.InRange(new FileInfo(first).Length, 1, 2048);
            var lines = File.ReadAllLines(first);
            Assert.Contains("adapter_loaded version=fixture second-line", lines[0]);
            Assert.All(lines, line => Assert.True(DateTimeOffset.TryParse(line.Split(' ')[0], out _)));
            Assert.Equal(2, Directory.GetFiles(directory, "LivingNPC-*.log").Length);
            Assert.DoesNotContain(lines, line => line.StartsWith("second-line"));
        }
        finally { if (Directory.Exists(directory)) Directory.Delete(directory, true); }
    }

    [Fact]
    public void UnwritableLogDestinationDoesNotThrowIntoGameThread()
    {
        var path = Path.GetTempFileName();
        try
        {
            using (var log = new AdapterLog(path))
            {
                log.Write("adapter_loaded");
                log.Dispose();
                Assert.True(log.Failed);
                log.Write("after_close");
            }
        }
        finally { File.Delete(path); }
    }
}
