using System;
using System.IO;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;

namespace Lindrop.Windows;

internal sealed record SendChecksum(long Size, string Md5);

internal static class SendChecksums
{
    internal static async Task<SendChecksum[]> Prepare(string[] paths, CancellationToken cancellation = default)
    {
        var result = new SendChecksum[paths.Length];
        for (var i = 0; i < paths.Length; i++)
        {
            var path = paths[i];
            await using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 256 * 1024,
                FileOptions.SequentialScan | FileOptions.Asynchronous);
            var length = input.Length;
            var digest = await MD5.HashDataAsync(input, cancellation).ConfigureAwait(false);
            result[i] = new SendChecksum(length, Convert.ToHexStringLower(digest));
        }
        return result;
    }
}
