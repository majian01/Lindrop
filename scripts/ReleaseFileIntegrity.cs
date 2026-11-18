// MIT: Lindrop Community contributors. Reads only the mandatory file label.
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class LindropReleaseFileIntegrity
{
    [DllImport("advapi32.dll", EntryPoint = "GetNamedSecurityInfoW", CharSet = CharSet.Unicode)]
    private static extern uint GetNamedSecurityInfo(string name, int kind, uint info,
        IntPtr owner, IntPtr group, IntPtr dacl, out IntPtr sacl, out IntPtr descriptor);
    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool GetAce(IntPtr acl, int index, out IntPtr ace);
    [DllImport("advapi32.dll")]
    private static extern IntPtr GetSidSubAuthorityCount(IntPtr sid);
    [DllImport("advapi32.dll")]
    private static extern IntPtr GetSidSubAuthority(IntPtr sid, uint index);
    [DllImport("kernel32.dll")]
    private static extern IntPtr LocalFree(IntPtr memory);

    public static int ReadLevel(string path)
    {
        IntPtr sacl, descriptor;
        uint error = GetNamedSecurityInfo(path, 1, 0x10, IntPtr.Zero, IntPtr.Zero,
            IntPtr.Zero, out sacl, out descriptor);
        // Filesystems without security descriptors cannot carry a Low label.
        if (error == 1 || error == 50) return 8192;
        if (error != 0) throw new Win32Exception((int)error, path);
        try
        {
            // A file without a mandatory label is treated as Medium by Windows.
            if (sacl == IntPtr.Zero) return 8192;
            int count = (ushort)Marshal.ReadInt16(sacl, 4);
            for (int index = 0; index < count; index++)
            {
                IntPtr ace;
                if (!GetAce(sacl, index, out ace)) throw new Win32Exception();
                if (Marshal.ReadByte(ace) != 17) continue;
                IntPtr sid = IntPtr.Add(ace, 8);
                byte parts = Marshal.ReadByte(GetSidSubAuthorityCount(sid));
                return Marshal.ReadInt32(GetSidSubAuthority(sid, (uint)(parts - 1)));
            }
            return 8192;
        }
        finally { LocalFree(descriptor); }
    }
}
