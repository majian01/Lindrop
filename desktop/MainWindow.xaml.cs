using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Threading;
using Microsoft.VisualBasic.FileIO;
using Microsoft.Win32;
using Forms = System.Windows.Forms;

namespace Lindrop.Windows;

public partial class MainWindow : Window
{
    private readonly LocalState state;
    private readonly DispatcherTimer batch;
    private readonly HashSet<string> files = new(StringComparer.OrdinalIgnoreCase);
    private TransferEngine? engine;
    private Forms.NotifyIcon? tray;
    private System.Drawing.Icon? trayIcon;
    private Window? picker;
    private Window? banner;
    private string? offerId;
    private int scanId;
    private bool busy;
    private bool quitting;
    private bool dropTargetActive;

    internal MainWindow(LocalState state, bool smoke = false, LaunchContext launch = default)
    {
        this.state = state;
        InitializeComponent();
        batch = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(550) };
        batch.Tick += (_, _) => { batch.Stop(); ChooseDevice(); };
        RefreshHistory();
        if (smoke) { RefreshSettings(); return; }
        Log($"启动 PID={Environment.ProcessId} sandboxed={launch.Sandboxed} elevated={launch.Elevated} state={state.DirectoryPath}");
        try { ShellIntegration.RefreshRegistration(); }
        catch (Exception error) when (error is UnauthorizedAccessException or System.Security.SecurityException or IOException)
        { Log($"更新资源管理器入口失败 · {error.Message}"); }
        RefreshSettings();

        Closing += (_, e) =>
        {
            if (quitting) return;
            e.Cancel = true;
            Hide();
        };
        trayIcon = System.Drawing.Icon.ExtractAssociatedIcon(Path.Combine(AppContext.BaseDirectory, "Lindrop.exe"));
        tray = new Forms.NotifyIcon
        {
            Icon = trayIcon ?? System.Drawing.SystemIcons.Application,
            Text = "邻渡 · Lindrop",
            Visible = true,
        };
        tray.DoubleClick += (_, _) => ShowMain();
        tray.MouseUp += (_, e) =>
        {
            if (e.Button != Forms.MouseButtons.Right) return;
            var menu = new ContextMenu
            {
                Placement = System.Windows.Controls.Primitives.PlacementMode.MousePoint,
                Resources = new ResourceDictionary { MergedDictionaries = { Resources } },
            };
            var open = new MenuItem { Header = "打开邻渡" };
            open.Click += (_, _) => ShowMain();
            var send = new MenuItem { Header = "发送文件" };
            send.Click += (_, _) => { ShowMain(); Browse_Click(this, new RoutedEventArgs()); };
            var quit = new MenuItem { Header = "退出邻渡" };
            quit.Click += async (_, _) => await Quit();
            menu.Items.Add(open);
            menu.Items.Add(send);
            menu.Items.Add(quit);
            menu.IsOpen = true;
        };
        StartEngine();
    }

    internal void ActivateWith(string[] args)
    {
        if (args.FirstOrDefault() == "--background") return;
        if (args.FirstOrDefault() == "--send") QueueFiles(args.Skip(1), "右键菜单");
        else ShowMain();
    }

    private void ShowMain()
    {
        Show();
        if (WindowState == WindowState.Minimized) WindowState = WindowState.Normal;
        Activate();
    }

    private void TitleBar_MouseLeftButtonDown(object sender, System.Windows.Input.MouseButtonEventArgs e)
    {
        if (e.ClickCount == 2) ToggleMaximize();
        else DragMove();
    }

    private void Minimize_Click(object sender, RoutedEventArgs e) => WindowState = WindowState.Minimized;
    private void Maximize_Click(object sender, RoutedEventArgs e) => ToggleMaximize();
    private void CloseWindow_Click(object sender, RoutedEventArgs e) => Close();

    private void ToggleMaximize()
    {
        MaxHeight = SystemParameters.WorkArea.Height;
        WindowState = WindowState == WindowState.Maximized ? WindowState.Normal : WindowState.Maximized;
    }

    private string Inbox => state.Settings.Folder ?? Path.Combine(state.DirectoryPath, "Received");

    private void StartEngine()
    {
        try
        {
            engine = new TransferEngine(state);
            engine.Message += message => Dispatcher.BeginInvoke(async () => await OnMessage(message));
            engine.Log += line => Dispatcher.BeginInvoke(() => Log(line));
            engine.Exited += () => Dispatcher.BeginInvoke(() =>
            {
                if (!quitting)
                {
                    busy = false;
                    banner?.Close();
                    SetStatus("传输进程已退出，请从托盘退出后重开", false);
                }
            });
            engine.Start();
        }
        catch (Exception error) { Fail(error); }
    }

    private async Task OnMessage(JsonElement message)
    {
        if (quitting) return;
        try
        {
            string Get(string key) => message.GetProperty(key).GetString() ?? "";
            switch (Get("kind"))
            {
                case "ready":
                    LocalState.CheckWritable(Inbox);
                    await Write(new { cmd = "listen", folder = Inbox });
                    break;
                case "log": Log(Get("text")); break;
                case "status":
                    SetStatus(Get("text"), true);
                    if (Get("text") == "已停止传输") FinishProgress("已停止传输");
                    break;
                case "error":
                    SetStatus(Get("text"), false);
                    FinishProgress(Get("text"));
                    break;
                case "progress":
                    ShowTransferProgress(Get("direction"), Get("name"),
                        message.GetProperty("current").GetDouble(),
                        message.GetProperty("total").GetDouble(),
                        message.GetProperty("speed").GetDouble());
                    break;
                case "busy":
                    busy = message.GetProperty("value").GetBoolean();
                    StopTransfer.IsEnabled = busy || preparingSend;
                    if (!busy && !preparingSend && progressWindow != null && progressStop?.IsEnabled == true)
                        FinishProgress("传输已结束");
                    if (!busy && files.Count > 0) RestartBatch();
                    break;
                case "offer": ShowOffer(Get("id"), Get("name"), message.GetProperty("count").GetInt32()); break;
                case "offer_closed":
                    if (Get("id") == offerId) banner?.Close();
                    break;
                case "peers": ShowPeers(message); break;
                case "done":
                    var time = DateTime.Now.ToString("yyyy-MM-dd HH:mm");
                    var paths = message.GetProperty("files").EnumerateArray().Select(value => value.GetString()!).ToArray();
                    state.Settings.History.InsertRange(0, paths.Select(path => new HistoryEntry(path, Get("direction"), Get("peer"), time)));
                    state.Save();
                    RefreshHistory();
                    SetStatus($"{Get("direction")}完成 · {paths.Length} 个文件", true);
                    FinishProgress($"{Get("direction")}完成 · {paths.Length} 个文件", completed: true);
                    break;
            }
        }
        catch (Exception error) { Fail(error); }
    }

    private async Task Write(object command)
    {
        if (engine == null) throw new InvalidOperationException("传输进程未启动");
        await engine.WriteAsync(command);
    }

    private void SetStatus(string message, bool okay)
    {
        StatusText.Text = message;
        StatusText.ToolTip = message;
        StatusDot.Fill = new SolidColorBrush(okay ? Color.FromRgb(49, 146, 105) : Color.FromRgb(182, 111, 51));
        Log(message);
    }

    private void Log(string line)
    {
        try { File.AppendAllText(Path.Combine(state.DirectoryPath, "Lindrop-Windows.log"), $"{DateTime.Now:O} {line}\n"); }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private void Fail(Exception error, bool dialog = false)
    {
        SetStatus(error.Message, false);
        Log(error.ToString());
        if (dialog) ShowGlassDialog("操作未完成", error.Message, confirm: false);
    }

    private void RefreshHistory()
    {
        if (HistoryList == null || Search == null) return;
        var term = Search.Text.Trim();
        var results = state.Settings.History.Where(entry =>
            $"{entry.FileName} {entry.Description}".Contains(term, StringComparison.CurrentCultureIgnoreCase)).ToArray();
        HistoryList.ItemsSource = results;
        HistoryEmpty.Visibility = results.Length == 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    private void RefreshSettings()
    {
        FolderText.Text = Inbox;
        StartupCheck.IsChecked = ShellIntegration.StartupEnabled;
        MenuCheck.IsChecked = ShellIntegration.MenuEnabled;
    }

    private void TransferPage_Click(object sender, RoutedEventArgs e) => SetPage(false);
    private void SettingsPage_Click(object sender, RoutedEventArgs e) => SetPage(true);

    private void SetPage(bool settings)
    {
        TransferPane.Visibility = settings ? Visibility.Collapsed : Visibility.Visible;
        SettingsPane.Visibility = settings ? Visibility.Visible : Visibility.Collapsed;
        PageTitle.Text = settings ? "设置" : "传输";
        TransferNav.Tag = !settings ? "Selected" : null;
        SettingsNav.Tag = settings ? "Selected" : null;
    }

    private void Search_Changed(object sender, TextChangedEventArgs e) => RefreshHistory();
    private void Search_FocusChanged(object sender, System.Windows.Input.KeyboardFocusChangedEventArgs e)
    {
        Search.BeginAnimation(WidthProperty, new DoubleAnimation(
            Search.IsKeyboardFocusWithin ? 224 : 160,
            TimeSpan.FromMilliseconds(SystemParameters.ClientAreaAnimation ? 220 : 0))
        {
            EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut },
        });
    }
    private void Inbox_Click(object sender, RoutedEventArgs e) => TryAction(() => Open(Inbox));
    private void Open_Click(object sender, RoutedEventArgs e)
    {
        if (Entry(sender) is not { } entry) return;
        TryAction(() =>
        {
            RequireHistoryFile(entry.Path);
            try { Open(entry.Path); }
            catch (System.ComponentModel.Win32Exception error) when (error.NativeErrorCode is 2 or 3)
            {
                RequireHistoryFile(entry.Path);
                throw;
            }
        });
    }

    private static void RequireHistoryFile(string path)
    {
        try
        {
            if ((File.GetAttributes(path) & FileAttributes.Directory) == 0) return;
        }
        catch (FileNotFoundException) { }
        catch (DirectoryNotFoundException) { }
        throw new FileNotFoundException("文件已被删除或移走。", path);
    }

    private void Reveal_Click(object sender, RoutedEventArgs e)
    {
        if (Entry(sender) is not { } entry) return;
        TryAction(() =>
        {
            RequireHistoryFile(entry.Path);
            Process.Start(new ProcessStartInfo("explorer.exe", $"/select,\"{entry.Path}\"") { UseShellExecute = true });
        });
    }

    private static HistoryEntry? Entry(object sender) => (sender as FrameworkElement)?.DataContext as HistoryEntry;

    private void HistoryMenu_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button button || Entry(sender) is not { } entry) return;
        var menu = CreateHistoryMenu(entry);
        menu.PlacementTarget = button;
        menu.IsOpen = true;
    }

    private ContextMenu CreateHistoryMenu(HistoryEntry entry)
    {
        var menu = new ContextMenu { Resources = new ResourceDictionary { MergedDictionaries = { Resources } } };
        if (entry.Direction == "接收")
        {
            var trash = new MenuItem { Header = "移到回收站" };
            trash.Click += (_, _) => TryAction(() =>
            {
                try { FileSystem.DeleteFile(entry.Path, UIOption.OnlyErrorDialogs, RecycleOption.SendToRecycleBin, UICancelOption.ThrowException); }
                catch (FileNotFoundException) { }
                catch (DirectoryNotFoundException) { }
                catch (OperationCanceledException) { return; }
                RemoveRecords(state.Settings.History.Where(row => StringComparer.OrdinalIgnoreCase.Equals(row.Path, entry.Path)));
            });
            menu.Items.Add(trash);
        }
        var forget = new MenuItem { Header = "移除历史记录" };
        forget.Click += (_, _) => ConfirmRemoveRecords([entry]);
        menu.Items.Add(forget);
        return menu;
    }

    private void HistorySelection_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (RemoveSelected != null && sender is ListBox list)
            RemoveSelected.IsEnabled = list.SelectedItems.Count > 0;
    }

    private void RemoveSelected_Click(object sender, RoutedEventArgs e) =>
        ConfirmRemoveRecords(HistoryList.SelectedItems.Cast<HistoryEntry>().ToArray());

    private void ConfirmRemoveRecords(HistoryEntry[] entries)
    {
        if (entries.Length == 0) return;
        if (ShowGlassDialog($"移除 {entries.Length} 条历史记录？", "此操作只删除记录，文件会保留。", confirm: true))
            TryAction(() => RemoveRecords(entries));
    }

    private void RemoveRecords(IEnumerable<HistoryEntry> entries)
    {
        var previous = state.Settings.History;
        var remove = new HashSet<HistoryEntry>(entries, ReferenceEqualityComparer.Instance);
        state.Settings.History = previous.Where(row => !remove.Contains(row)).ToList();
        try { state.Save(); }
        catch { state.Settings.History = previous; throw; }
        RefreshHistory();
    }

    internal void VerifyHistory(string path)
    {
        var entry = state.Settings.History.Single();
        var sent = CreateHistoryMenu(entry);
        var received = CreateHistoryMenu(entry with { Direction = "接收" });
        if (sent.Items.Count != 1 || received.Items.Count != 2 || sent.Items[0] is not MenuItem remove ||
            !Equals(remove.Header, "移除历史记录"))
            throw new InvalidOperationException("History menu does not protect sent files.");
        var missing = path + ".missing";
        try
        {
            RequireHistoryFile(missing);
            throw new InvalidOperationException("The missing history file was accepted.");
        }
        catch (FileNotFoundException error) when (error.FileName == missing) { }
        var second = entry with { Path = missing };
        state.Settings.History.Add(second);
        RefreshHistory();
        HistoryList.SelectedItems.Add(entry);
        HistoryList.SelectedItems.Add(second);
        if (!RemoveSelected.IsEnabled || HistoryList.SelectedItems.Count != 2)
            throw new InvalidOperationException("History multi-selection did not enable removal.");
        RemoveRecords(HistoryList.SelectedItems.Cast<HistoryEntry>().ToArray());
        if (!File.Exists(path) || new LocalState(state.Home, state.DirectoryPath).Settings.History.Count != 0)
            throw new InvalidOperationException("History removal did not persist independently of the file.");
    }

    private static void Open(string path) => Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });

    private void TryAction(Action action)
    {
        try { action(); }
        catch (Exception error) { Fail(error, dialog: true); }
    }

    private async void Folder_Click(object sender, RoutedEventArgs e)
    {
        if (busy) { SetStatus("传输期间无法更改接收目录", false); return; }
        var previous = state.Settings.Folder;
        try
        {
            var folder = ChooseFolder("选择接收目录", Inbox);
            if (folder is null || busy) return;
            LocalState.CheckWritable(folder);
            state.Settings.Folder = folder;
            try { state.Save(); }
            catch { state.Settings.Folder = previous; throw; }
            RefreshSettings();
            await Write(new { cmd = "listen", folder = Inbox });
        }
        catch (Exception error) { Fail(error, dialog: true); }
    }

    private string? ChooseFolder(string title, string current)
    {
        var dialog = CreateFolderDialog(title, current);
        return dialog.ShowDialog(this) == true ? dialog.FolderName : null;
    }

    internal static OpenFolderDialog CreateFolderDialog(string title, string current)
    {
        var dialog = new OpenFolderDialog { Title = title };
        if (Directory.Exists(current)) dialog.InitialDirectory = Path.GetFullPath(current);
        return dialog;
    }

    private void Startup_Click(object sender, RoutedEventArgs e)
    {
        TryAction(() => ShellIntegration.SetStartup(StartupCheck.IsChecked == true));
        RefreshSettings();
    }

    private void Menu_Click(object sender, RoutedEventArgs e)
    {
        TryAction(() => ShellIntegration.SetMenu(MenuCheck.IsChecked == true));
        RefreshSettings();
    }

    private void Browse_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new OpenFileDialog { Multiselect = true, Title = "选择要发送的文件" };
        if (dialog.ShowDialog(this) == true) QueueFiles(dialog.FileNames, "文件选择");
    }

    private void OnDragOver(object sender, DragEventArgs e)
    {
        var files = e.Data.GetDataPresent(DataFormats.FileDrop);
        SetDropTargetActive(files && IsOverDropZone(e));
        e.Effects = files ? DragDropEffects.Copy : DragDropEffects.None;
        e.Handled = true;
    }

    private void OnDragLeave(object sender, DragEventArgs e)
    {
        if (!IsOverDropZone(e)) SetDropTargetActive(false);
    }

    private bool IsOverDropZone(DragEventArgs e) =>
        DropZone.IsVisible && new Rect(DropZone.RenderSize).Contains(e.GetPosition(DropZone));

    private void OnDrop(object sender, DragEventArgs e)
    {
        SetDropTargetActive(false);
        e.Handled = StageDrop(e.Data);
    }

    private void SetDropTargetActive(bool active)
    {
        if (dropTargetActive == active) return;
        dropTargetActive = active;
        DropLabel.Text = active ? "松开发送文件" : "将文件拖到这里发送";
        var duration = new Duration(TimeSpan.FromMilliseconds(SystemParameters.ClientAreaAnimation ? 230 : 0));
        var easing = new CubicEase { EasingMode = EasingMode.EaseOut };
        var scale = active ? 1.018 : 1;
        DropScale.BeginAnimation(ScaleTransform.ScaleXProperty, new DoubleAnimation(scale, duration) { EasingFunction = easing });
        DropScale.BeginAnimation(ScaleTransform.ScaleYProperty, new DoubleAnimation(scale, duration) { EasingFunction = easing });
        DropFill.BeginAnimation(SolidColorBrush.ColorProperty, new ColorAnimation(
            active ? Color.FromRgb(228, 239, 255) : Color.FromRgb(241, 243, 247), duration) { EasingFunction = easing });
        DropStroke.BeginAnimation(SolidColorBrush.ColorProperty, new ColorAnimation(
            active ? Color.FromRgb(107, 165, 244) : Color.FromRgb(228, 231, 239), duration) { EasingFunction = easing });
    }

    private bool StageDrop(IDataObject data)
    {
        if (data.GetData(DataFormats.FileDrop) is not string[] paths) return false;
        QueueFiles(paths, "拖放");
        return true;
    }

    private void QueueFiles(IEnumerable<string> paths, string source)
    {
        var incoming = paths.ToArray();
        Log($"发送请求 · {source} · {incoming.Length} 个文件");
        if (incoming.Length == 0 || !incoming.All(File.Exists))
        {
            Fail(new IOException("请选择存在的文件，文件夹暂不支持"), dialog: true);
            return;
        }
        foreach (var path in incoming) files.Add(path);
        if (busy) { SetStatus($"上一笔传输完成后将显示设备列表 · {files.Count} 个文件", true); return; }
        RestartBatch();
    }

    private void RestartBatch()
    {
        batch.Stop();
        batch.Start();
    }

    internal void VerifySendInputs(string path)
    {
        SetDropTargetActive(true);
        if (DropLabel.Text != "松开发送文件" || !DropScale.HasAnimatedProperties)
            throw new InvalidOperationException("Drop-zone feedback did not start.");
        SetDropTargetActive(false);
        busy = true;
        var data = new DataObject(DataFormats.FileDrop, new[] { path });
        if (!StageDrop(data) || !files.Remove(path) || batch.IsEnabled)
            throw new InvalidOperationException("Drop staging failed during a transfer.");
        ActivateWith(["--send", path]);
        if (!files.Remove(path))
            throw new InvalidOperationException("Explorer argument staging failed during a transfer.");
        busy = false;
    }

    private Window Popup(UIElement content, int width) => new()
    {
        WindowStyle = WindowStyle.None, ResizeMode = ResizeMode.NoResize, AllowsTransparency = true,
        Background = Brushes.Transparent, Width = width, SizeToContent = SizeToContent.Height,
        UseLayoutRounding = true, SnapsToDevicePixels = true,
        ShowInTaskbar = false, Topmost = true, FontFamily = FontFamily, FontSize = FontSize,
        Resources = new ResourceDictionary { MergedDictionaries = { this.Resources } },
        Content = CreateGlassSurface(content)
    };

    private UIElement CreateGlassSurface(UIElement content)
    {
        var surface = new Grid { Margin = new Thickness(8) };
        surface.Children.Add(new Border
        {
            Background = Brushes.White, CornerRadius = new CornerRadius(26), IsHitTestVisible = false,
            Effect = new System.Windows.Media.Effects.DropShadowEffect { Opacity = 0.17, BlurRadius = 16, ShadowDepth = 3 }
        });
        surface.Children.Add(new Border
        {
            Background = (Brush)FindResource("GlassFill"), BorderBrush = (Brush)FindResource("GlassEdge"),
            BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(26), Padding = new Thickness(18),
            Child = content
        });
        System.Windows.Media.TextOptions.SetTextFormattingMode(surface, TextFormattingMode.Display);
        return surface;
    }

    private bool ShowGlassDialog(string title, string message, bool confirm)
    {
        var content = new StackPanel();
        var heading = Label(title, 18);
        heading.FontWeight = FontWeights.SemiBold;
        content.Children.Add(heading);
        var text = Label(message);
        text.Foreground = (Brush)FindResource("Secondary");
        text.Margin = new Thickness(0, 10, 0, 22);
        content.Children.Add(text);
        var actions = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        var dialog = Popup(content, 410);
        dialog.Owner = this;
        dialog.Topmost = false;
        dialog.WindowStartupLocation = WindowStartupLocation.CenterOwner;
        if (confirm)
        {
            var cancel = ActionButton("取消", "Pill", (_, _) => dialog.DialogResult = false);
            cancel.IsCancel = true;
            actions.Children.Add(cancel);
        }
        var accept = ActionButton(confirm ? "移除记录" : "好", "Primary", (_, _) => dialog.DialogResult = true);
        accept.IsDefault = !confirm;
        actions.Children.Add(accept);
        content.Children.Add(actions);
        dialog.KeyDown += (_, e) => { if (e.Key == System.Windows.Input.Key.Escape) dialog.DialogResult = false; };
        return dialog.ShowDialog() == true;
    }

    private Button ActionButton(string title, string style, RoutedEventHandler clicked)
    {
        var button = new Button { Content = title, Style = (Style)FindResource(style), Margin = new Thickness(4, 0, 0, 0) };
        button.Click += clicked;
        return button;
    }

    private static TextBlock Label(string title, double size = 14) => new()
    {
        Text = title, FontSize = size, VerticalAlignment = VerticalAlignment.Center, TextWrapping = TextWrapping.Wrap,
    };

    private void ShowOffer(string id, string name, int count)
    {
        banner?.Close();
        var row = new DockPanel();
        var buttons = new StackPanel { Orientation = Orientation.Horizontal };
        DockPanel.SetDock(buttons, Dock.Right);
        buttons.Children.Add(ActionButton("拒绝", "Pill", async (_, _) => await Decide(id, false)));
        buttons.Children.Add(ActionButton("接收", "Primary", async (_, _) => await Decide(id, true)));
        row.Children.Add(buttons);
        row.Children.Add(Label($"{name} 想发送 {count} 个文件"));
        var popup = Popup(row, 470);
        popup.ShowActivated = false;
        var area = SystemParameters.WorkArea;
        popup.Left = area.Left + (area.Width - popup.Width) / 2;
        popup.Top = area.Top + 10;
        popup.Closed += (_, _) => { if (banner == popup) { banner = null; offerId = null; } };
        banner = popup;
        offerId = id;
        popup.Show();
    }

    private async Task Decide(string id, bool accept)
    {
        banner?.Close();
        try
        {
            if (accept) ShowTransferProgress("接收", "正在建立连接", 0, 0, 0);
            await Write(new { cmd = "decision", id, accept });
        }
        catch (Exception error) { Fail(error); }
    }

    private StackPanel? devices;
    private Window? progressWindow;
    private TextBlock? progressTitle;
    private TextBlock? progressDetail;
    private ProgressBar? transferProgress;
    private Button? progressStop;

    private void ShowTransferProgress(string direction, string name, double current, double total, double speed)
    {
        if (progressWindow == null)
        {
            var panel = new DockPanel();
            progressStop = ActionButton("停止", "Pill", StopTransfer_Click);
            progressStop.VerticalAlignment = VerticalAlignment.Center;
            DockPanel.SetDock(progressStop, Dock.Right);
            panel.Children.Add(progressStop);
            var content = new StackPanel { Margin = new Thickness(0, 0, 14, 0) };
            progressTitle = Label("", 13);
            progressTitle.TextWrapping = TextWrapping.NoWrap;
            progressTitle.TextTrimming = TextTrimming.CharacterEllipsis;
            progressDetail = Label("", 12);
            progressDetail.Foreground = (Brush)FindResource("Secondary");
            progressDetail.Margin = new Thickness(0, 5, 0, 0);
            transferProgress = new ProgressBar { Height = 4, Margin = new Thickness(0, 9, 0, 0), Maximum = 100,
                Foreground = (Brush)FindResource("Accent"), BorderThickness = new Thickness(0) };
            content.Children.Add(progressTitle);
            content.Children.Add(transferProgress);
            content.Children.Add(progressDetail);
            panel.Children.Add(content);
            var popup = Popup(panel, 480);
            popup.ShowActivated = false;
            var area = SystemParameters.WorkArea;
            popup.Left = area.Left + (area.Width - popup.Width) / 2;
            popup.Top = area.Top + 10;
            popup.Closed += (_, _) =>
            {
                if (progressWindow == popup)
                {
                    progressWindow = null;
                    progressTitle = null;
                    progressDetail = null;
                    progressStop = null;
                    transferProgress = null;
                }
            };
            progressWindow = popup;
            popup.Show();
        }
        progressStop!.IsEnabled = true;
        progressTitle!.Text = $"{direction} · {name}";
        transferProgress!.IsIndeterminate = total <= 0;
        transferProgress.Value = total > 0 ? Math.Clamp(current / total * 100, 0, 100) : 0;
        progressDetail!.Text = total > 0
            ? $"{current / 1e6:F1} / {total / 1e6:F1} MB · {transferProgress.Value:F0}% · {speed / 1e6:F2} MB/s"
            : "正在准备传输…";
    }

    private void FinishProgress(string text, bool completed = false)
    {
        var popup = progressWindow;
        if (popup == null || progressStop == null) return;
        progressStop.IsEnabled = false;
        progressTitle!.Text = text;
        transferProgress!.IsIndeterminate = false;
        if (completed) transferProgress.Value = 100;
        progressDetail!.Text = completed ? "文件已校验" : "当前任务已结束";
        var timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(3) };
        timer.Tick += (_, _) =>
        {
            timer.Stop();
            if (progressWindow == popup && progressStop?.IsEnabled == false) popup.Close();
        };
        timer.Start();
    }

    internal void VerifyTransferProgress()
    {
        ShowTransferProgress("发送", "发送测试.bin", 25, 100, 1_000_000);
        if (transferProgress!.Value != 25 || transferProgress.IsIndeterminate ||
            progressStop?.IsEnabled != true || !progressDetail!.Text.Contains("1.00 MB/s"))
            throw new InvalidOperationException("Sending progress display failed.");
        ShowTransferProgress("接收", "接收测试.bin", 0, 0, 0);
        if (!transferProgress.IsIndeterminate)
            throw new InvalidOperationException("Connection progress display failed.");
        ShowTransferProgress("接收", "接收测试.bin", 100, 100, 2_000_000);
        FinishProgress("接收完成", completed: true);
        if (transferProgress.Value != 100 || progressStop!.IsEnabled)
            throw new InvalidOperationException("Completion progress display failed.");
        progressWindow!.Close();
    }

    internal void SavePreview(string directory)
    {
        void Capture(string name)
        {
            var surface = (FrameworkElement)Content;
            surface.Measure(new Size(940, 650));
            surface.Arrange(new Rect(0, 0, 940, 650));
            surface.UpdateLayout();
            var picture = new System.Windows.Media.Imaging.RenderTargetBitmap(
                940, 650, 96, 96, PixelFormats.Pbgra32);
            picture.Render(surface);
            var encoder = new System.Windows.Media.Imaging.PngBitmapEncoder();
            encoder.Frames.Add(System.Windows.Media.Imaging.BitmapFrame.Create(picture));
            using var file = File.Create(Path.Combine(directory, name));
            encoder.Save(file);
        }
        Measure(new Size(940, 650));
        Arrange(new Rect(0, 0, 940, 650));
        Capture("transfer.png");
        SetPage(true);
        Capture("settings.png");
        SetPage(false);
    }
    private bool preparingSend;
    private System.Threading.CancellationTokenSource? preparationCancel;

    private async void StopTransfer_Click(object sender, RoutedEventArgs e)
    {
        StopTransfer.IsEnabled = false;
        preparationCancel?.Cancel();
        batch.Stop();
        picker?.Close();
        files.Clear();
        banner?.Close();
        SetStatus("正在停止传输…", true);
        if (progressStop != null) progressStop.IsEnabled = false;
        if (progressDetail != null) progressDetail.Text = "正在停止传输…";
        try { await Write(new { cmd = "cancel" }); }
        catch (Exception error) { Fail(error); }
    }

    private void ChooseDevice()
    {
        if (busy || files.Count == 0) return;
        if (files.Count > 250)
        {
            files.Clear();
            Fail(new IOException("每批最多发送 250 个文件"), dialog: true);
            return;
        }
        if (picker != null)
        {
            if (pickerLabel != null) pickerLabel.Text = $"发送 {files.Count} 个文件";
            picker.Activate();
            return;
        }
        var panel = new StackPanel();
        pickerLabel = Label($"发送 {files.Count} 个文件", 20);
        panel.Children.Add(pickerLabel);
        devices = new StackPanel { Margin = new Thickness(0, 12, 0, 14) };
        panel.Children.Add(new ScrollViewer
        {
            MaxHeight = 260, Content = devices,
            VerticalScrollBarVisibility = ScrollBarVisibility.Hidden,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
        });
        var controls = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        var popup = Popup(panel, 390);
        controls.Children.Add(ActionButton("重新扫描", "Pill", async (_, _) => await Scan()));
        controls.Children.Add(ActionButton("取消", "Quiet", (_, _) => popup.Close()));
        panel.Children.Add(controls);
        popup.WindowStartupLocation = WindowStartupLocation.CenterScreen;
        popup.Closed += (_, _) => { _ = StopScan(scanId); picker = null; pickerLabel = null; devices = null; files.Clear(); };
        popup.KeyDown += (_, e) => { if (e.Key == System.Windows.Input.Key.Escape) popup.Close(); };
        picker = popup;
        popup.Show();
        _ = Scan();
    }

    private TextBlock? pickerLabel;

    private async Task StopScan(int serial)
    {
        if (quitting) return;
        try { await Write(new { cmd = "cancel_scan", id = serial }); }
        catch (Exception error) { Log($"停止设备扫描 · {error.Message}"); }
    }

    private async Task Scan()
    {
        if (devices == null) return;
        devices.Children.Clear();
        devices.Children.Add(Label("正在查找附近设备…"));
        try { await Write(new { cmd = "scan", id = ++scanId }); }
        catch (Exception error)
        {
            devices?.Children.Clear();
            devices?.Children.Add(Label(error.Message));
            Fail(error);
        }
    }

    private void ShowPeers(JsonElement message)
    {
        if (devices == null || message.GetProperty("id").GetInt32() != scanId) return;
        devices.Children.Clear();
        if (message.TryGetProperty("error", out var error))
        {
            devices.Children.Add(Label(error.GetString() ?? "搜索失败"));
            return;
        }
        foreach (var item in message.GetProperty("items").EnumerateArray())
        {
            var peer = item.GetProperty("id").ToString();
            var name = item.GetProperty("name").GetString();
            if (string.IsNullOrWhiteSpace(name)) name = $"小米设备 {peer}";
            var button = ActionButton(name, "Pill", async (_, _) => await Send(peer, name));
            button.Margin = new Thickness(0, 0, 0, 6);
            devices.Children.Add(button);
        }
        if (devices.Children.Count == 0) devices.Children.Add(Label("未发现设备。请打开手机的小米互传，开启「可被发现」。"));
    }

    private async Task Send(string peer, string name)
    {
        if (busy || preparingSend) { SetStatus("正在处理一笔传输，请稍后选择设备", false); return; }
        var paths = files.ToArray();
        picker?.Close();
        preparingSend = true;
        ShowTransferProgress("发送", "正在准备文件", 0, 0, 0);
        preparationCancel = new System.Threading.CancellationTokenSource();
        StopTransfer.IsEnabled = true;
        try
        {
            var timer = Stopwatch.StartNew();
            SetStatus($"正在校验 {paths.Length} 个文件", true);
            var checksums = await SendChecksums.Prepare(paths, preparationCancel.Token);
            preparationCancel.Token.ThrowIfCancellationRequested();
            Log($"发送校验准备 · {timer.Elapsed.TotalSeconds:F3} 秒 · {checksums.Sum(item => item.Size)} 字节");
            if (busy || quitting) throw new InvalidOperationException("传输暂不可用，请稍后再试。");
            SetStatus($"正在向 {name} 发送 {paths.Length} 个文件", true);
            await Write(new { cmd = "send", peer, name, files = paths, checksums, scan = scanId });
        }
        catch (OperationCanceledException) { SetStatus("已停止传输", true); FinishProgress("已停止传输"); }
        catch (Exception error) { Fail(error, dialog: true); FinishProgress(error.Message); }
        finally
        {
            preparationCancel?.Dispose();
            preparationCancel = null;
            preparingSend = false;
            StopTransfer.IsEnabled = busy;
        }
    }

    private async Task Quit()
    {
        if (quitting) return;
        quitting = true;
        batch.Stop();
        tray?.Dispose();
        trayIcon?.Dispose();
        banner?.Close();
        progressWindow?.Close();
        picker?.Close();
        Hide();
        if (engine != null) await engine.DisposeAsync();
        Application.Current.Shutdown();
    }
}
