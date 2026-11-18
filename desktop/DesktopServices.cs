using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Linq;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Win32;

namespace Lindrop.Windows;

internal sealed record HistoryEntry(string Path, string Direction, string Peer, string Time)
{
    [System.Text.Json.Serialization.JsonIgnore]
    public string FileName => System.IO.Path.GetFileName(Path);

    [System.Text.Json.Serialization.JsonIgnore]
    public string Description => $"{Direction} · {Peer}";
}

internal sealed class Settings
{
    public string? Folder { get; set; }
    public List<HistoryEntry> History { get; set; } = [];
    [System.Text.Json.Serialization.JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalSettings { get; set; }
}

internal sealed class LocalState
{
    internal static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    internal string DirectoryPath { get; }
    internal string Home { get; }
    private bool Bundled => File.Exists(Path.Combine(Home, "backend", "receiver.py"));
    internal Settings Settings { get; }
    private string StateFile => Path.Combine(DirectoryPath, "state.json");

    internal LocalState(string home, string? data = null, string? legacyDirectory = null)
    {
        Home = home;
        var local = Bundled ? Path.Combine(home, "data") : Path.Combine(home, "work", "mishare", "desktop-data");
        var legacy = legacyDirectory ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MiShare-PC");
        var directory = data ?? local;
        try
        {
            CheckWritable(directory);
        }
        catch (Exception error) when (data is null && directory != legacy &&
            error is UnauthorizedAccessException or IOException)
        {
            directory = legacy;
            CheckWritable(directory);
        }
        DirectoryPath = directory;
        Settings = Load(StateFile) ?? (data is null && directory != legacy ? Load(Path.Combine(legacy, "state.json")) : null) ?? new Settings();
        if (data is null && !File.Exists(StateFile)) Save();
    }

    internal string TemporaryDirectory => Path.Combine(DirectoryPath, "tmp");

    internal void ConfigureProcessTemporaryDirectory()
    {
        Directory.CreateDirectory(TemporaryDirectory);
        Environment.SetEnvironmentVariable("TEMP", TemporaryDirectory);
        Environment.SetEnvironmentVariable("TMP", TemporaryDirectory);
    }

    private static Settings? Load(string path) => File.Exists(path)
        ? JsonSerializer.Deserialize<Settings>(File.ReadAllText(path), Json) : null;

    internal static void CheckWritable(string folder)
    {
        Directory.CreateDirectory(folder);
        var file = Path.Combine(folder, $".mishare-{Guid.NewGuid():N}.tmp");
        using var stream = new FileStream(file, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1, FileOptions.DeleteOnClose);
        stream.WriteByte(0);
    }

    internal void Save()
    {
        var temporary = Path.Combine(DirectoryPath, $"state-{Guid.NewGuid():N}.tmp");
        try
        {
            File.WriteAllText(temporary, JsonSerializer.Serialize(Settings, Json));
            File.Move(temporary, StateFile, overwrite: true);
        }
        finally
        {
            File.Delete(temporary);
        }
    }

    internal static string LocateHome()
    {
        if (File.Exists(Path.Combine(AppContext.BaseDirectory, "backend", "receiver.py")))
            return AppContext.BaseDirectory;
        for (var directory = new DirectoryInfo(AppContext.BaseDirectory); directory != null; directory = directory.Parent)
        {
            if (File.Exists(Path.Combine(directory.FullName, "outputs", "MiShare-PC", "receiver.py")))
                return directory.FullName;
        }
        throw new DirectoryNotFoundException("找不到 Lindrop 工作区。请从原项目中的 Windows 版启动。");
    }

    internal string BackendPython => Bundled ? Path.Combine(Home, "python", "python.exe")
        : Path.Combine(Home, "work", "mishare", "venv", "Scripts", "python.exe");
    internal string BackendSource => Bundled ? Path.Combine(Home, "backend") : Path.Combine(Home, "outputs", "MiShare-PC");
    internal string DefaultIdentity => Bundled ? Path.Combine(Home, "Identity", "pc.bks")
        : Path.Combine(Home, "outputs", "MiShare-Research", "phone-trust", "pc.bks");
}

internal sealed class TransferEngine : IAsyncDisposable
{
    private readonly Process process;
    private readonly SemaphoreSlim writeLock = new(1);
    internal event Action<JsonElement>? Message;
    internal event Action<string>? Log;
    internal event Action? Exited;

    internal TransferEngine(LocalState state)
    {
        if (!File.Exists(state.BackendPython))
            throw new FileNotFoundException("找不到已验证的 Lindrop 传输环境", state.BackendPython);
        var start = new ProcessStartInfo(state.BackendPython)
        {
            UseShellExecute = false,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false),
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
            CreateNoWindow = true,
            WorkingDirectory = state.BackendSource,
        };
        start.Environment["PYTHONIOENCODING"] = "utf-8";
        Directory.CreateDirectory(state.TemporaryDirectory);
        start.Environment["TEMP"] = state.TemporaryDirectory;
        start.Environment["TMP"] = state.TemporaryDirectory;
        start.ArgumentList.Add("-B");
        start.ArgumentList.Add("-u");
        start.ArgumentList.Add(Path.Combine(AppContext.BaseDirectory, "transport.py"));
        start.ArgumentList.Add(state.BackendSource);
        start.ArgumentList.Add(state.DefaultIdentity);
        process = new Process { StartInfo = start, EnableRaisingEvents = true };
        process.OutputDataReceived += (_, e) =>
        {
            if (e.Data == null) return;
            try
            {
                using var document = JsonDocument.Parse(e.Data);
                Message?.Invoke(document.RootElement.Clone());
            }
            catch (JsonException) { Log?.Invoke(e.Data); }
        };
        process.ErrorDataReceived += (_, e) => { if (e.Data != null) Log?.Invoke(e.Data); };
        process.Exited += (_, _) => Exited?.Invoke();
    }

    internal void Start()
    {
        process.Start();
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
    }

    internal async Task WriteAsync(object payload)
    {
        await writeLock.WaitAsync().ConfigureAwait(false);
        try
        {
            if (process.HasExited) throw new IOException("传输进程已退出");
            await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(payload, LocalState.Json)).ConfigureAwait(false);
            await process.StandardInput.FlushAsync().ConfigureAwait(false);
        }
        finally { writeLock.Release(); }
    }

    public async ValueTask DisposeAsync()
    {
        try
        {
            if (!process.HasExited)
            {
                await WriteAsync(new { cmd = "quit" });
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(4));
                await process.WaitForExitAsync(timeout.Token);
            }
        }
        catch (OperationCanceledException) { if (!process.HasExited) process.Kill(entireProcessTree: true); }
        catch (IOException) { }
        catch (InvalidOperationException) { }
        finally { process.Dispose(); }
    }
}

internal sealed class SingleInstance : IDisposable
{
    private readonly string name;
    private readonly Mutex mutex;
    private readonly CancellationTokenSource shutdown = new();
    internal bool IsPrimary { get; }

    internal SingleInstance()
    {
        using var identity = WindowsIdentity.GetCurrent();
        var owner = identity.User?.Value ?? Environment.UserName;
        name = "Lindrop.Windows." + Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(owner)))[..16];
        mutex = new Mutex(false, @"Local\" + name);
        try { IsPrimary = mutex.WaitOne(0); }
        catch (AbandonedMutexException) { IsPrimary = true; }
    }

    internal async Task ForwardAsync(string[] args)
    {
        await using var client = new NamedPipeClientStream(".", name, PipeDirection.Out, PipeOptions.Asynchronous);
        await client.ConnectAsync(3000);
        using var writer = new StreamWriter(client, new UTF8Encoding(false));
        await writer.WriteLineAsync(JsonSerializer.Serialize(args));
    }

    internal async Task ListenAsync(Action<string[]> received)
    {
        while (!shutdown.IsCancellationRequested)
        {
            try
            {
                await using var server = new NamedPipeServerStream(name, PipeDirection.In, 1,
                    PipeTransmissionMode.Byte, PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
                await server.WaitForConnectionAsync(shutdown.Token).ConfigureAwait(false);
                using var reader = new StreamReader(server, Encoding.UTF8);
                using var timeout = CancellationTokenSource.CreateLinkedTokenSource(shutdown.Token);
                timeout.CancelAfter(TimeSpan.FromSeconds(3));
                var line = await reader.ReadLineAsync(timeout.Token).ConfigureAwait(false);
                if (line is { Length: <= 300_000 } && JsonSerializer.Deserialize<string[]>(line) is { } args)
                    received(args);
            }
            catch (OperationCanceledException) { }
            catch (JsonException) { }
            catch (IOException) when (shutdown.IsCancellationRequested) { break; }
        }
    }

    public void Dispose()
    {
        shutdown.Cancel();
        if (IsPrimary) mutex.ReleaseMutex();
        mutex.Dispose();
    }
}

internal static class ShellIntegration
{
    private const string MenuKey = @"Software\Classes\*\shell\Lindrop-Windows";
    private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string Value = "Lindrop-Windows";
    // Replace the legacy entry only when the equivalent .NET entry is enabled.
    private const string LegacyMenuKey = @"Software\Classes\*\shell\Lindrop-PC";
    private const string LegacyValue = "Lindrop-PC";

    internal static bool MenuEnabled => Registry.GetValue(@"HKEY_CURRENT_USER\" + MenuKey + @"\command", "", null) is string;
    internal static bool StartupEnabled => Registry.GetValue(@"HKEY_CURRENT_USER\" + RunKey, Value, null) is string;

    internal static void RefreshRegistration()
    {
        var current = Executable();
        var commandKey = MenuKey + @"\command";
        var registered = Registry.GetValue(@"HKEY_CURRENT_USER\" + commandKey, "", null) as string;
        var commandText = $"\"{current}\" --send \"%1\"";
        var migrate = NeedsMigration(registered, "\" --send \"%1\"", current);
        if (migrate)
        {
            using var command = Registry.CurrentUser.OpenSubKey(commandKey, writable: true);
            command?.SetValue("", commandText);
        }
        if (migrate || StringComparer.OrdinalIgnoreCase.Equals(registered, commandText))
        {
            var icon = $"\"{current}\",0";
            if (!Equals(Registry.GetValue(@"HKEY_CURRENT_USER\" + MenuKey, "Icon", null), icon))
            {
                using var menu = Registry.CurrentUser.OpenSubKey(MenuKey, writable: true);
                menu?.SetValue("Icon", icon);
            }
        }
        if (NeedsMigration(Registry.GetValue(@"HKEY_CURRENT_USER\" + RunKey, Value, null) as string,
            "\" --background", current))
        {
            using var run = Registry.CurrentUser.OpenSubKey(RunKey, writable: true);
            run?.SetValue(Value, $"\"{current}\" --background");
        }
    }

    private static bool NeedsMigration(string? command, string suffix, string current)
    {
        if (command == null || command.Length <= suffix.Length + 1 || command[0] != '"' ||
            !command.EndsWith(suffix, StringComparison.OrdinalIgnoreCase)) return false;
        var previous = command[1..^suffix.Length];
        if (StringComparer.OrdinalIgnoreCase.Equals(previous, current) || !Path.IsPathFullyQualified(previous) ||
            !StringComparer.OrdinalIgnoreCase.Equals(Path.GetFileName(previous), "Lindrop.exe")) return false;
        var folder = Directory.GetParent(previous);
        return folder?.Name is "app" or "app-permissions" &&
            StringComparer.OrdinalIgnoreCase.Equals(folder.Parent?.Name, "Lindrop-Windows");
    }

    internal static void SetMenu(bool enabled)
    {
        if (!enabled)
        {
            Registry.CurrentUser.DeleteSubKeyTree(MenuKey, throwOnMissingSubKey: false);
            return;
        }
        var exe = Executable();
        using var menu = Registry.CurrentUser.CreateSubKey(MenuKey);
        menu.SetValue("MUIVerb", "用邻渡发送");
        menu.SetValue("MultiSelectModel", "Document");
        menu.SetValue("Icon", $"\"{exe}\",0");
        using var command = menu.CreateSubKey("command");
        command.SetValue("", $"\"{exe}\" --send \"%1\"");
        Registry.CurrentUser.DeleteSubKeyTree(LegacyMenuKey, throwOnMissingSubKey: false);
    }

    internal static void SetStartup(bool enabled)
    {
        using var key = Registry.CurrentUser.CreateSubKey(RunKey);
        if (!enabled)
        {
            key.DeleteValue(Value, throwOnMissingValue: false);
            return;
        }
        key.SetValue(Value, $"\"{Executable()}\" --background");
        key.DeleteValue(LegacyValue, throwOnMissingValue: false);
    }

    private static string Executable()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "Lindrop.exe");
        if (!File.Exists(path)) throw new FileNotFoundException("请使用 Lindrop.exe 启动后再启用 Windows 集成", path);
        return path;
    }
}
