using System;

namespace Lindrop.Windows;

internal static class PlatformCompatibility
{
    internal const int MinimumWindowsBuild = 19044;
    internal static readonly Version MinimumWindowsVersion = new(10, 0, MinimumWindowsBuild);

    internal static bool Supports(Version version, bool is64BitProcess) =>
        is64BitProcess && version.CompareTo(MinimumWindowsVersion) >= 0;

    internal const string Requirement = "需要 Windows 10 21H2（内部版本 19044）或更新版本的 64 位 Windows，支持 Windows 11。可运行 winver 查看系统版本。";
}
