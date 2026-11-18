using System;
using System.IO;
using System.Threading.Tasks;
using System.Windows;

namespace Lindrop.Windows;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        if (!PlatformCompatibility.Supports(Environment.OSVersion.Version, Environment.Is64BitProcess))
        {
            MessageBox.Show(PlatformCompatibility.Requirement, "邻渡", MessageBoxButton.OK, MessageBoxImage.Information);
            return 3;
        }
        var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        try
        {
            var home = LocalState.LocateHome();
            if (args is ["--smoke", var directory])
                return Smoke(home, directory);

            var launch = LaunchContext.Read();
            if (launch.Elevated == true)
            {
                if (args is not ["--background"])
                    MessageBox.Show("请从系统托盘退出以管理员权限运行的 Lindrop，再在资源管理器中直接双击 Lindrop.exe。普通权限的资源管理器无法将文件拖入管理员窗口。",
                        "邻渡", MessageBoxButton.OK, MessageBoxImage.Information);
                return 4;
            }

            using var instance = new SingleInstance();
            if (!instance.IsPrimary)
            {
                try { instance.ForwardAsync(args).GetAwaiter().GetResult(); }
                catch (Exception error) when (error is UnauthorizedAccessException or TimeoutException or IOException)
                {
                    throw new InvalidOperationException("无法把请求交给已运行的 Lindrop。请先从系统托盘退出旧实例，再双击新版 Lindrop.exe。", error);
                }
                return 0;
            }
            var state = new LocalState(home);
            state.ConfigureProcessTemporaryDirectory();
            var window = new MainWindow(state, launch: launch);
            app.MainWindow = window;
            _ = instance.ListenAsync(forwarded => app.Dispatcher.BeginInvoke(() => window.ActivateWith(forwarded)));
            window.ActivateWith(args);
            return app.Run();
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "邻渡", MessageBoxButton.OK, MessageBoxImage.Error);
            return 1;
        }
    }

    private static int Smoke(string home, string directory)
    {
        var report = Path.Combine(directory, "smoke.txt");
        try
        {
            if (PlatformCompatibility.Supports(new Version(10, 0, 19043), true) ||
                !PlatformCompatibility.Supports(new Version(10, 0, 19044), true) ||
                !PlatformCompatibility.Supports(new Version(10, 0, 19045), true) ||
                !PlatformCompatibility.Supports(new Version(10, 0, 22000), true) ||
                PlatformCompatibility.Supports(new Version(10, 0, 19045), false))
                throw new InvalidOperationException("Windows 10/11 compatibility boundaries or x64 requirement are incorrect.");
            var portableHome = Path.Combine(directory, "portable-home");
            var portableData = Path.Combine(portableHome, "data");
            Directory.CreateDirectory(Path.Combine(portableHome, "backend"));
            Directory.CreateDirectory(portableData);
            File.WriteAllText(Path.Combine(portableHome, "backend", "receiver.py"), "# bundle marker");
            var portableInbox = Path.Combine(portableData, "Received");
            File.WriteAllText(Path.Combine(portableData, "state.json"),
                System.Text.Json.JsonSerializer.Serialize(new Settings { Folder = portableInbox }, LocalState.Json));
            var portableState = new LocalState(portableHome);
            if (!StringComparer.OrdinalIgnoreCase.Equals(portableState.DirectoryPath, portableData) ||
                portableState.Settings.Folder != portableInbox)
                throw new InvalidOperationException("Bundled application state must stay beside the installation and prefer its local settings.");
            portableState.ConfigureProcessTemporaryDirectory();
            if (Environment.GetEnvironmentVariable("TEMP") != Path.Combine(portableData, "tmp") ||
                Environment.GetEnvironmentVariable("TMP") != Path.Combine(portableData, "tmp"))
                throw new InvalidOperationException("The process temporary directory must follow the local application data.");
            var legacyData = Path.Combine(directory, "legacy-state");
            Directory.CreateDirectory(legacyData);
            var legacyFile = Path.Combine(legacyData, "state.json");
            var legacySettings = System.Text.Json.JsonSerializer.Serialize(new Settings { Folder = Path.Combine(legacyData, "Received") }, LocalState.Json);
            File.WriteAllText(legacyFile, legacySettings);
            var migrationHome = Path.Combine(directory, "migration-home");
            Directory.CreateDirectory(Path.Combine(migrationHome, "backend"));
            File.WriteAllText(Path.Combine(migrationHome, "backend", "receiver.py"), "# bundle marker");
            var migrated = new LocalState(migrationHome, legacyDirectory: legacyData);
            if (migrated.DirectoryPath != Path.Combine(migrationHome, "data") ||
                migrated.Settings.Folder != Path.Combine(legacyData, "Received") ||
                !File.Exists(Path.Combine(migrated.DirectoryPath, "state.json")) || File.ReadAllText(legacyFile) != legacySettings)
                throw new InvalidOperationException("Legacy settings must be imported without moving or modifying the old user data.");
            var state = new LocalState(home, directory);
            var path = Path.Combine(directory, "照片.txt");
            File.WriteAllText(path, "Lindrop");
            state.Settings.History.Add(new(path, "发送", "小米", "2026-11-15 12:00"));
            state.Save();
            var restored = new LocalState(home, directory);
            var window = new MainWindow(restored, smoke: true);
            window.Measure(new Size(960, 680));
            window.Arrange(new Rect(0, 0, 960, 680));
            window.SavePreview(directory);
            window.VerifyHistory(path);
            window.VerifySendInputs(path);
            window.VerifyTransferProgress();
            var checksum = SendChecksums.Prepare([path]).GetAwaiter().GetResult()[0];
            if (checksum.Size != 7 || checksum.Md5 != Convert.ToHexStringLower(
                System.Security.Cryptography.MD5.HashData("Lindrop"u8)))
                throw new InvalidOperationException("The sender calculated an incorrect checksum.");
            var full = Path.GetFullPath(directory);
            var picker = MainWindow.CreateFolderDialog("选择接收目录", full.Replace('\\', '/'));
            if (picker.InitialDirectory != full ||
                !string.IsNullOrEmpty(MainWindow.CreateFolderDialog("选择接收目录", path).InitialDirectory))
                throw new InvalidOperationException("Folder picker did not normalize the saved path or ignore an unavailable directory.");
            VerifyTransport(restored).GetAwaiter().GetResult();
            File.WriteAllText(report, "PASS Windows 10/11 version policy, local application data/temp, read-only legacy migration, explicit state override, WPF layout, history, send inputs, folder picker path normalization, Python bridge ready and quit.");
            return 0;
        }
        catch (Exception error)
        {
            File.WriteAllText(report, error.ToString());
            return 2;
        }
    }

    private static async Task VerifyTransport(LocalState state)
    {
        var ready = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        await using var engine = new TransferEngine(state);
        engine.Message += message =>
        {
            if (message.GetProperty("kind").GetString() == "ready") ready.TrySetResult();
        };
        engine.Start();
        await ready.Task.WaitAsync(TimeSpan.FromSeconds(10));
    }
}
