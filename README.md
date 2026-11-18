# 邻渡 Lindrop

手机拍完照片，打开小米互传，就能发到 Windows。邻渡在电脑托盘里等着：有文件到来，点屏幕顶部的「接收」即可。手机和电脑不用连同一个 Wi-Fi。

[下载 Windows 版](https://github.com/majian01/Lindrop/releases) · [电脑发手机](#pc-to-phone) · [English](#english) · [从源码构建](#build) · [第三方出处](#credits)

## 下载和使用

- **安装版**：下载 `Setup-win10-win11-x64.exe` 结尾的文件，双击安装。可以装到 D 盘或其他可写目录。
- **便携版**：完整解压 ZIP，双击 `app\Lindrop.exe`。两个版本都已打包运行环境，无需另装 .NET、Python、Rust 或小米电脑管家。
- **一键安装脚本**：双击 `Install-Lindrop-Online.cmd` 即可下载 0.2.6 Preview，校验后打开安装窗口。用 `--verify-only` 可以只下载和校验。[下载脚本](https://github.com/majian01/Lindrop/releases/download/v0.2.6-preview/Install-Lindrop-Online.cmd)

打开邻渡，在手机上选择文件 → 小米互传 → 选择电脑 → 在电脑顶部点「接收」。接收目录可以在设置里改，收到的文件可以从历史记录打开。更新前先从托盘退出旧版；卸载保留已收到的文件。

要求 Windows 10 21H2 或更新版本（含 Windows 11）、x64、低功耗蓝牙（BLE）及支持 Wi-Fi Direct 的网卡和驱动。实机测试覆盖 Windows 10、Windows 11、澎湃 OS 3 和澎湃 OS 4。暂无 Linux 版。

<a id="pc-to-phone"></a>
## 电脑发手机

0.2.6 恢复了可选的小米文件服务。接收可以直接用。从电脑发送前，将同一套 **小米电脑管家 5.8.1.130 x64** 的 `MiDropTransfer.dll`、`LIBEAY32.dll`、`ssleay32.dll` 放到 `Lindrop.exe` 旁边的 `XiaomiRuntime` 文件夹。三个文件由用户从官方安装包或自己的同版本安装取得。

1. 下载小米电脑管家 5.8.1.130、提取脚本和 7-Zip x64。[小米官方下载](https://cdn.cnbj1.fds.api.mi-img.com/ota-packages/TS4L_XiaomiPCManager_feature_p52_5.8.1.130_b5a04836.exe) · [提取脚本](https://github.com/majian01/Lindrop/releases/download/v0.2.6-preview/Extract-XiaomiRuntime.ps1) · [7-Zip](https://www.7-zip.org/)
2. 在下载目录打开 PowerShell，运行下面的命令。将 `-OutputDirectory` 改成自己的目录。便携版的目标是解压目录下的 `app\XiaomiRuntime`。脚本只解包并校验三个 DLL，无需运行小米安装器。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Extract-XiaomiRuntime.ps1 `
  -InstallerPath .\TS4L_XiaomiPCManager_feature_p52_5.8.1.130_b5a04836.exe `
  -OutputDirectory 'D:\Programs\Lindrop\XiaomiRuntime'
```

3. 打开邻渡，选文件和手机，在手机上接受。若提示缺少 C++ 运行库，请安装微软 Visual C++ x64 运行库。[微软官方下载](https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist)

## English

Lindrop is an unofficial Xiaomi Share companion for Windows. Pick a file on your phone, choose your PC in Xiaomi Share, and accept it in the banner at the top of the screen. The two devices do not need to be on the same Wi-Fi network.

[Download for Windows](https://github.com/majian01/Lindrop/releases). Run the setup EXE, or extract the portable ZIP and open `app\Lindrop.exe`. The runtimes are included. You can change the download folder in Settings and open received files from History. Quit Lindrop from the tray before updating. Uninstalling keeps your received files.

Double-click `Install-Lindrop-Online.cmd` to download and verify the 0.2.6 Preview installer and open Setup. Run it with `--verify-only` to check the download without installing. [Download the script](https://github.com/majian01/Lindrop/releases/download/v0.2.6-preview/Install-Lindrop-Online.cmd)

**Phone-to-PC receiving works without extra setup.** For PC-to-phone sending, extract the three matching DLLs from Xiaomi PC Manager 5.8.1.130 x64 and place them in `XiaomiRuntime` alongside `Lindrop.exe`. Set `-OutputDirectory` to that folder in the command above. The extractor uses 7-Zip and verifies all three files. Install the Microsoft Visual C++ x64 Redistributable if Windows needs it. [Downloads and command](#pc-to-phone)

Requires Windows 10 21H2 or later, including Windows 11 (x64), Bluetooth LE, and Wi-Fi Direct. Hardware testing has covered Windows 10, Windows 11, Xiaomi HyperOS 3, and Xiaomi HyperOS 4. Linux is not supported.

<a id="build"></a>
## 从源码构建 / Build from source

在 Windows x64 上准备 .NET 10 SDK、Rust stable（MSVC 工具链）、Visual Studio C++ Build Tools、Python 3.12 x64（含 pip）和 Inno Setup 7.1.1。首次构建会下载依赖。

On Windows x64, install the .NET 10 SDK, Rust stable with the MSVC toolchain, Visual Studio C++ Build Tools, Python 3.12 x64 with pip, and Inno Setup 7.1.1. The first build downloads dependencies.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Build.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Build-Installer.ps1
```

应用在 / App: `dist\Lindrop-win-x64\app\Lindrop.exe`。安装包在 / Installer: `dist\`（如未将编译器加入 PATH，向第二个命令传入 / Pass `-ISCC` with the path to `ISCC.exe` if it is not on PATH）。

<a id="credits"></a>
## 许可与出处 / License and credits

本项目代码使用 [MIT](LICENSE)。邻渡是独立项目，与小米无隶属关系。第三方组件保留原许可。 / Lindrop is an independent project and is not affiliated with Xiaomi. Third-party components retain their original licenses.

- `transport/receiver.py` 改编自 [Tandem 的接收器](https://github.com/wumai2580/tandem/blob/89274a33d0ef4a8ae9a68e5767c221883ed5e907/tandem/mtapc.py)，保留其 [MIT 声明](LICENSES/Tandem-MIT.txt)。 / Adapted from Tandem under MIT.
- 运行环境 / Runtimes: [.NET/WPF](https://github.com/dotnet/wpf)、[Python 3.12 嵌入版](https://www.python.org/downloads/release/python-31210/)（含它随附的 VC++ 库 / including its bundled VC++ runtime）、[PyWinRT](https://github.com/pywinrt/pywinrt)、[cryptography](https://github.com/pyca/cryptography)、[PyCryptodome](https://github.com/Legrandin/pycryptodome) 及 Rust crates。完整清单与许可证 / Full inventory and notices: [LICENSES](LICENSES/).
- 安装器 / Installer: [Inno Setup](https://jrsoftware.org/inno-setup/) 和它的[简体中文翻译](https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation)。界面参考 / Design inspiration: [Liquid Glass](https://developer.apple.com/documentation/technologyoverviews/liquid-glass).
- BKS 格式参考 / BKS format references: [Bouncy Castle](https://github.com/bcgit/bc-java/blob/main/prov/src/main/java/org/bouncycastle/jcajce/provider/keystore/bc/BcKeyStoreSpi.java) 和 [RFC 7292](https://www.rfc-editor.org/rfc/rfc7292#appendix-B)。历史小米电脑管家 DLL 的出处保留在 [LICENSES](LICENSES/Xiaomi-runtime-SHA256.txt)，当前包不包含这些 DLL。 / Historical Xiaomi PC Manager DLL notices are retained; those DLLs are not included.
