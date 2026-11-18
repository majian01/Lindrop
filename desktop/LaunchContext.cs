using System;
using System.Runtime.InteropServices;
using System.Security.Principal;

namespace Lindrop.Windows;

internal readonly record struct LaunchContext(bool? Sandboxed, bool? Elevated)
{
    internal static LaunchContext Read()
    {
        using var identity = WindowsIdentity.GetCurrent();
        return new LaunchContext(ReadFlag(identity.AccessToken, 47), ReadFlag(identity.AccessToken, 20));
    }

    private static bool? ReadFlag(Microsoft.Win32.SafeHandles.SafeAccessTokenHandle token, int kind) =>
        GetTokenInformation(token, kind, out var value, sizeof(int), out _) ? value != 0 : null;

    [DllImport("advapi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetTokenInformation(Microsoft.Win32.SafeHandles.SafeAccessTokenHandle token,
        int kind, out int value, int size, out int returned);
}
