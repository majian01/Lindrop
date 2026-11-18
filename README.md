# 邻渡（Lindrop）

**兼容小米互传的非官方 Windows 文件传输客户端。** 无需连接同一个 Wi-Fi 或局域网。 通过蓝牙发现与握手、Wi-Fi Direct 直连传输，兼容手机原生小米互传，无需另装手机 App。

**0.2.1 Preview 已把电脑端文件服务改为独立 Rust 实现，不加载 `MiDropTransfer.dll`、`LIBEAY32.dll` 或 `ssleay32.dll`。** 为兼容手机原生 PC 接收通道，发送时仍需单独准备手机自带的 `pc.bks` 身份资源；“没有小米 DLL”不代表“没有任何原生协议材料”。邻渡是独立社区项目，与小米及其关联公司无隶属或授权背书关系。“小米互传 / Xiaomi Share”仅用于描述协议兼容性，不是本项目名称。

[English](#english) · [安装](#installation) · [原生身份文件](#native-identity) · [源码构建](#build-from-source) · [旧版 DLL 指南](#legacy-dll) · [出处与许可](#third-party)

**GitHub 当前仅发布源码，尚未发布可直接下载的 EXE/便携安装包。** 请先按[从源码构建](#build-from-source)生成 `dist\Lindrop-win-x64`，再运行生成目录中的安装脚本。仓库内的 `scripts\Install-Lindrop.cmd` 是打包模板，不能直接当作安装包运行。

## 0.2.4：卸载确认框缺字修复

针对部分 Windows 环境下卸载确认正文空白、按钮文字残缺的问题，安装器和卸载器启用 Inno 内置的 `windows11` 样式，让常见消息框使用安装器自身的控件绘制。该样式名称不改变 Windows 10 兼容目标。0.2.3 的字体设置继续保留；更新安装包后，安装目录中的卸载器才会一并更新。

Setup and Uninstall now use Inno's built-in `windows11` style for their common message dialogs, addressing blank body text and incomplete button captions observed on this machine's native message-box rendering path. This style does not change the Windows 10 compatibility target. Run the new installer to update the generated uninstaller. See [Inno Setup WizardStyle](https://jrsoftware.org/inno-setup/documentation/topic_setup_wizardstyle.htm).

## 0.2.3：安装与卸载的中文字体

安装器、卸载器及欢迎页显式使用 Microsoft YaHei UI（微软雅黑 UI），减少对系统中文字体回退的依赖；英文界面中的中文项目名也使用相同字体。中文语言文件和 PowerShell 脚本继续使用 Unicode 编码。需要运行新安装包更新安装目录中的卸载器，已生成的旧卸载器不会因源码修改而自动改变。

Setup and Uninstall now explicitly use Microsoft YaHei UI, including welcome headings and the Chinese product name in the English UI. Run the new installer to update the generated uninstaller. Font settings follow the [Inno Setup language options](https://jrsoftware.org/inno-setup/documentation/topic_langoptionssection.htm).

## 0.2.2：独立项目命名

项目、窗口、托盘、右键菜单、可执行文件和安装程序统一使用「邻渡 / Lindrop」。手机端发现及发送方名称为「邻渡」（恰好 6 个 UTF-8 字节，符合发送握手长度限制）。传输协议中的厂商标识、手机包名、原生资源来源和第三方署名保留原样。

此版本不改变已测通的传输流程。原项目名的历史版本说明仅为出处和迁移参考；新安装目录与注册表入口使用独立名称。更新前退出旧程序；若要沿用旧安装目录的设置和历史，可在两个程序都退出后将旧 `data` 目录复制到新安装目录，收到的文件不自动移动。

Lindrop 0.2.2 uses an independent name throughout its UI, executable, installer and Explorer integration. It keeps vendor protocol identifiers and upstream attribution intact. Exit the previous application before use. To retain settings/history from another installation, copy its `data` folder while both applications are closed; received files are not moved automatically. All new distribution archives exclude native identity files.

## 0.2.1：发现与发送准备优化

后台接收监听同时维护近期设备缓存，打开设备选择窗可直接使用 **2 秒内**实际收到的广播；过期设备和忙碌设备会失效，新设备继续实时发现。缓存只在内存中保存，设备、地址和广播队列均有限额。

关闭选择窗、重新扫描和开始发送会结束旧扫描。发送时不再创建无用的第二个扫描器；蓝牙广播按实际 `STARTED` 状态推进，并把原有 0.5 秒首次发现机会与建组、HTTPS 启动重叠。只提升当前任务的接收/拒绝回应优先级，避免广播队列满时丢掉关键握手。

2026-11-17，当前小米 15 / AX210 的小样本结果：

| 指标 | 结果与限制 |
| --- | --- |
| 已在后台发现的设备 | 两次均命中近期缓存，首个设备列表直接返回 |
| 开始发送到广播接收请求 | 旧版样本 1.328 秒；本次两次 0.891 / 0.859 秒，约少 0.44–0.47 秒 |
| 本机 HTTPS 回环 | 约 725 MB/s；Rust 接收、落盘及 MD5 全流程约 260 MB/s，不能当成无线实速 |
| 同一 32 MiB 文件的 TCP 缓存对比 | 系统 64 KiB：手机正文 8.134 秒，4.13 MB/s；1 MiB：7.459 秒，4.50 MB/s |
| 实际直连频率 | 两次均为 2412 MHz（2.4 GHz）；未测到频道宽度，不据此假定 20/40 MHz |

TCP 只有一对样本，约 9% 差异不足以证明稳定提速，所以产品仍使用系统默认缓存。握手准备缩短不等于文件正文提速；当前无线吞吐仍是主要瓶颈。当前使用的 [WiFiDirectAdvertisement](https://learn.microsoft.com/en-us/uwp/api/windows.devices.wifidirect.wifidirectadvertisement) / LegacySettings API 没有频道选择属性，不能只修改广播字段就声称已切换 5 GHz。此版本没有改变网卡驱动或全局网络参数。

## 功能与兼容性

- 手机与电脑双向发送，多文件拖放、设备选择和资源管理器右键发送。
- 托盘后台监听；顶部接收确认条、发送/接收进度、百分比、速度与「停止本次传输」。
- 接收位置设置、历史搜索、多选移除记录、打开文件/所在目录；移除记录不删除关联文件。
- 登录启动、圆角 WPF 界面；应用和默认接收文件可安装在 D/E 盘。
- 发送服务采用 Rust HTTP/1.1 流式传输，支持 keep-alive、HEAD、单段 Range 和 MD5 回执；接收继续使用 Rust 流式下载。

| 项目 | 要求与实际状态 |
| --- | --- |
| 系统目标 | Windows 10 21H2（19044）及以上 x64，包括 22H2 和 Windows 11 |
| 硬件 | 支持 Wi-Fi Direct GO 的无线网卡；支持 BLE 广播、扫描与 GATT 的蓝牙及配套驱动 |
| 当前实测 | Windows 11、Intel AX210、小米 15、澎湃 OS 3、小米互传 4.13.12 |
| 独立服务实测 | 2026-11-16：原生 BKS + 独立 Rust 服务通过真机小文件、32 MiB + 空文件多文件传输、MD5 校验和任务结束 |
| 尚未覆盖 | Win10 真机无线传输、ARM 电脑、高通/联发科网卡实测、大规模跨版本回归 |

Wi-Fi Direct 与原 Wi-Fi 并发、直连频段和吞吐量取决于网卡及驱动，不保证所有设备用 5 GHz 或达到标称速率。Windows 10 是技术兼容目标；[.NET 10 官方系统支持](https://github.com/dotnet/core/blob/main/release-notes/10.0/supported-os.md) 按具体系统版别及生命周期区分。

这里的后台运行是用户会话内的托盘进程，不是系统服务。关闭主窗口后继续接收，从托盘退出才结束。

<a id="installation"></a>
## 安装、更新与卸载

以下步骤适用于已经自行构建或取得的完整安装包；从 GitHub 克隆源码后，应先完成[源码构建](#build-from-source)。

1. 双击安装包 `Lindrop-0.2.4-preview-Setup-win10-win11-x64.exe`，无需额外安装 Python、.NET 或 Rust。ZIP 版请先完整解压，再双击 `Install-Lindrop.cmd`。
2. 安装向导可以选择 D/E 盘。按普通用户安装及运行，无需管理员权限；以管理员运行会影响来自普通资源管理器的拖放。
3. 手机开启小米互传及「可被发现」。电脑点击「发送文件」、拖入文件或使用右键菜单，在手机确认接收；反向发送时在电脑顶部确认。
4. 更新前从托盘退出旧版。Preview 与 0.1.x 的界面和历史格式保持兼容，但本次不自动修改现有安装；可先保留旧安装包回退。

本次邻渡源码、安装包和 ZIP 均不包含 `Identity/pc.bks`。需要电脑发送时，请按下节单独准备原生身份文件；缺少身份文件时接收仍可运行，电脑发送会给出提示。

设置中可启用登录启动及右键入口；Windows 11 经典右键入口可能位于「显示更多选项」。卸载可用 Windows 设置 → 应用 → 邻渡 (Lindrop)，或 ZIP 版的 `Uninstall-Lindrop.cmd`。卸载保留/备份用户数据，不删除收到的文件。

### 尽量不占用 C 盘

程序、运行环境、身份资源、历史、日志及默认接收目录跟随安装位置：

```text
D:\Programs\Lindrop\
  Lindrop.exe
  lindrop-transfer.exe
  Identity\pc.bks
  python\
  backend\
  data\state.json
  data\Lindrop-Windows.log
  data\Received\
  data\tmp\
```

临时目录使用应用的 `data\tmp`；只有安装目录不可写才回退到用户 LocalAppData。旧版 LocalAppData 配置会只读迁移，原接收文件不移动。Windows 的注册表、快捷方式、密码学组件及安装器临时目录仍由系统管理，可能位于系统盘；不修改系统 TEMP 或全局权限。

构建流程会修正本项目生成 EXE 的低完整性文件标签，避免旧版出现的 Temp/其他盘拒绝访问。签名不能代替这个标签修复；本地签名也不是公开受信任的发行者签名。

<a id="native-identity"></a>
## 原生 pc.bks：如何获取、校验与放置

手机的 PC 接收路径使用内置的证书信任库。该原生 BKS 实际还带有一个密封 RSA 私钥条目；只复制公钥证书不能完成服务端 TLS 握手。独立实现读取原生 BKS、验证完整性及公私钥匹配，在内存中交给系统 TLS；不导出明文私钥文件。

| 已验证资源 | 值 |
| --- | --- |
| 手机应用版本 | 小米互传 4.13.12，包名 `com.miui.mishare.connectivity` |
| APK 内资源 | `res/aO.bks`，对应 `raw/pc` |
| BKS SHA-256 | `7429ee33c15dfe959dce153cc6928a86d539aa85229990f71c7af59148003fbb` |
| 证书 SHA-256 指纹 | `af74e29aa73a1880fd8dff5097152bbdc52ecedf0b429788268da97b991eda45` |

### 从自己的手机取得 APK

仅准备身份文件时需要 [Android 官方 Platform Tools](https://developer.android.com/tools/releases/platform-tools)。手机开启 USB 调试并授权电脑，以下命令在源码目录运行：

```powershell
New-Item -ItemType Directory -Path .\work\phone -Force | Out-Null
adb devices
adb shell pm path com.miui.mishare.connectivity
```

最后一条返回 `package:/data/app/.../base.apk`。把 `package:` 后的**实际路径**填入下一条命令，不要照抄省略号：

```powershell
adb pull '/data/app/实际目录/base.apk' '.\work\phone\mishare.apk'
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Extract-NativeIdentity.ps1 `
  -ApkPath '.\work\phone\mishare.apk'
```

脚本只取出并校验已验证的 BKS 资源，保存到 `work\identity\pc.bks`；遇到不同资源会停止，不覆盖已有不同文件。不需要安装 APK，也不需要反编译工具。其他版本需重新验证，不要随便找同名证书替代。

### 放置位置

- 已安装软件：`<安装目录>\Identity\pc.bks`，例如 `D:\Programs\Lindrop\Identity\pc.bks`。
- ZIP 包：`app\Identity\pc.bks`。
- 从源码构建：使用 `-NativeIdentityPath '.\work\identity\pc.bks'`，脚本自动复制。

原生 BKS 是来自手机的身份材料，不属于本项目新增 MIT 代码。公开源码排除 APK、BKS、PFX、私钥和反编译结果；本机可互操作不等于已经取得该资源的公开再分发授权。0.2.0 不再需要小米笔记本管家或其 DLL。

<a id="build-from-source"></a>
## 从源码构建与验证

需要 Windows x64、.NET 10 SDK、支持 edition 2024 的 Rust stable、Python 3.12 x64/pip。通常使用 Rust MSVC 工具链及 Visual Studio C++ Build Tools；本机使用 .NET SDK 10.0.404 和 Rust 1.100.0。`Cargo.lock` 和 `requirements.txt` 固定依赖版本，不包含开发工具链或依赖本体；首次构建需要从 crates.io、PyPI 和 python.org 下载依赖。`-PythonRuntimePath` 可以复用已经装齐依赖的嵌入式 Python，Cargo 离线构建也需要预先准备缓存。

从源码创建开发环境并完成最小构建：

```powershell
git clone https://github.com/majian01/Lindrop.git
cd Lindrop
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Build.ps1 `
  -Python '.\.venv\Scripts\python.exe'
```

这个最小构建不包含原生身份资源，手机向电脑发送可用。若需电脑向手机发送，先按[身份文件说明](#native-identity)准备文件，再添加 `-NativeIdentityPath '.\work\identity\pc.bks'`。要将 Microsoft VC++ 运行库随成品分发，再添加 `-VcRuntimePath '实际合法运行库目录'`；省略它时，目标电脑需要已有适用的 x64 VC++ 运行库。两个参数均为可选项，不要照抄不存在的示例路径。

构建完成后选择安装，或直接运行生成的应用：

```powershell
.\dist\Lindrop-win-x64\Install-Lindrop.cmd
# Or run without registering an uninstall entry:
.\dist\Lindrop-win-x64\app\Lindrop.exe
```

输出 `dist\Lindrop-win-x64`，包含 self-contained .NET Release、Rust Release、Python 3.12.10 嵌入式运行环境。`-PythonRuntimePath` 可复用准备好的嵌入式 Python。省略 `NativeIdentityPath` 可构建不含原生身份的包，接收可用，发送前需补齐该文件。不会下载或复制小米 DLL。

`VcRuntimePath` 为具有适用再分发许可的 x64 `msvcp140.dll`、`vcruntime140.dll`、`vcruntime140_1.dll`；它们是 Microsoft 运行库，不是小米 DLL。依赖内置不等于没有操作系统和驱动依赖。

```powershell
# Use a Python environment with requirements.txt installed, or the bundled app\python\python.exe.
python -B -m unittest discover -s tests -p 'test_*.py'
python -B .\tests\independent_loopback.py .\dist\Lindrop-win-x64\app\lindrop-transfer.exe
.\dist\Lindrop-win-x64\app\Lindrop.exe --smoke .\work\smoke
# Optional: use a separately supplied native BKS and its exported public cert.
python -B .\tests\native_identity_loopback.py `
  .\dist\Lindrop-win-x64\app\lindrop-transfer.exe .\work\identity\pc.bks .\work\identity\pc-public-cert.pem
```

`independent_loopback.py` 不需要任何小米资源。原生身份对照测试启用证书链校验，同时检查新自签证书被原生信任锚拒绝；它与真机测试各自验证不同层面。`tests/rust_loopback.py` 是历史 DLL 对照实验，不是当前独立构建的必跑项。

`native_identity_loopback.py` 是可选的原生身份对照测试，需要自行准备 `pc.bks` 及其对应的公钥证书 `pc-public-cert.pem`；`Extract-NativeIdentity.ps1` 只提取 BKS，不会生成该 PEM。普通安装和不使用厂商资源的单元/HTTPS 测试不需要运行这一项。

安装器由 Inno Setup 7.1.1 编译：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Build-Installer.ps1
```

<a id="english"></a>
## English

**Lindrop (邻渡)** is an independent Windows file transfer client compatible with Xiaomi Share. It discovers devices using BLE and transfers files over Wi-Fi Direct, without requiring a shared router or Internet connection. It supports bidirectional file transfer, multiple files, drag and drop, Explorer integration, history and a floating progress/stop bar. Lindrop is not an official Xiaomi product and is not affiliated with or endorsed by Xiaomi. Xiaomi Share / MiShare is used only to identify compatibility with the native phone service.

**This GitHub repository currently publishes source code only, with no downloadable EXE or portable release attached.** Follow [Build from source](#build-from-source) first. The scripts under `scripts/` are packaging templates; run the installer from the generated `dist\Lindrop-win-x64` folder, not directly from the source checkout.

### Independent 0.2.1 Preview

The PC HTTPS file service is implemented in Rust. It does not load or bundle `MiDropTransfer.dll`, `LIBEAY32.dll` or `ssleay32.dll`. Native PC-to-phone compatibility still requires an externally supplied `Identity/pc.bks` resource: the phone trusts its native certificate, and that original BKS also contains a sealed matching private-key entry. New self-signed certificates are not interchangeable with this identity.

On 2026-11-16, the independent service completed a real transfer, MD5 verification and task completion with a Xiaomi 15 / HyperOS 3 / MiShare 4.13.12 on Windows 11 + Intel AX210. This confirms basic interoperability, not full performance or cross-device coverage. Windows 10 21H2 build 19044+ x64 is the compatibility target, but no Win10 wireless-device validation has been completed. Qualcomm/MediaTek adapters and ARM PCs remain untested.

### Discovery and setup improvements in 0.2.1

The existing background listener supplies a bounded in-memory cache of advertisements actually observed within two seconds. Busy or expired devices are invalidated. Closing/rescanning/sending cancels the previous scan, and sending no longer starts a redundant watcher. Radio startup follows its real status; the initial discovery interval overlaps group creation and HTTPS startup. Only responses matching the current peer/device/task receive priority.

Two live runs reused the discovery cache and prepared the offer in 0.891/0.859 seconds, versus the previous 1.328-second sample. This is a small sample, not a p50/p95 guarantee. Loopback HTTPS reached approximately 725 MB/s, while Rust receiving with disk writes and MD5 reached 260 MB/s. Wireless performance is a separate limit.

For the same 32 MiB content, a single TCP-buffer comparison measured 8.134 seconds (4.13 MB/s) with the system's 64 KiB send buffer and 7.459 seconds (4.50 MB/s) with 1 MiB. Both used 2412 MHz; channel width was not measured. A single pair does not establish a stable improvement, so the product keeps the system default. No driver or global network settings were changed. Existing Win10/ARM/cross-adapter validation limits still apply.

### Install and use

Double-click `Lindrop-0.2.4-preview-Setup-win10-win11-x64.exe`, choose a writable location such as `D:\Programs\Lindrop`, and run as a normal user. The Lindrop package bundles its runtimes but excludes the native identity. PC-to-phone sending requires a separately supplied `Identity/pc.bks`; phone-to-PC receiving remains available without it. No separate Python, .NET or Rust installation is needed. ZIP users should extract everything and run `Install-Lindrop.cmd`. Exit the old tray application before updating. The current installed application is not silently replaced by a research run.

Enable Xiaomi Share discovery on the phone. Send using drag/drop, the send button or Explorer menu; accept on the recipient. Settings, logs, history, default received files and app temporary files follow the installation folder. Windows itself still manages system temporary files, registry, shortcuts and cryptographic storage. Uninstall through Windows Settings or the ZIP uninstaller; received files are retained.

### Obtain the native identity

Use the official Android Platform Tools and USB debugging on your own phone. Run `adb shell pm path com.miui.mishare.connectivity`, then `adb pull` the actual returned APK path (without the `package:` prefix). Run `scripts\Extract-NativeIdentity.ps1 -ApkPath <your-apk>` from the source folder. This extracts the supported 4.13.12 resource to `work\identity\pc.bks` and verifies SHA-256 `7429ee33c15dfe959dce153cc6928a86d539aa85229990f71c7af59148003fbb`. Unknown resources are rejected.

For an installed application, place the resource at `<install-folder>\Identity\pc.bks`; for a ZIP build, use `app\Identity\pc.bks`. For builds, supply `-NativeIdentityPath`. Without it, receiving remains available and sending reports the missing identity. Keep this native resource outside public source and the MIT license; successful extraction is not a redistribution grant. No plaintext private key is exported.

### Build and attribution

Requirements: .NET 10 SDK, Rust stable with edition 2024 support, Python 3.12 x64/pip, and Windows build tools. Use the PowerShell clone, environment, build and test commands above. The first build downloads dependencies from crates.io, PyPI and python.org; lockfiles pin versions and do not include their contents. `-PythonRuntimePath` can reuse an embedded runtime with its dependencies already installed. Native identity and legally redistributable VC++ files are optional build inputs; without bundled VC++ files, the destination PC must already have the appropriate runtime. The independent HTTPS test needs no Xiaomi files. The optional native-identity test requires a separately supplied matching public PEM certificate, which the BKS extraction script does not generate. Source archives exclude APKs, BKS/PFX/private keys, decompiled vendor source files and proprietary DLLs.

New code is MIT-licensed; adapted Tandem code retains its original notice. All dependency, native-resource and historical runtime sources are listed below in this same README. The historical 0.1.x DLL extraction guide is retained only for older releases.

<a id="legacy-dll"></a>
<details>
<summary>旧版 0.1.x 的小米 DLL 获取指南 / Historical DLL instructions</summary>

**以下仅记录 0.1.x 历史版本的获取、校验与放置方法，不适用于当前 0.2.4 独立实现及其构建脚本。当前版本无需准备或加载这些 DLL，也没有 `-XiaomiRuntimePath` 参数。**

## 小米 DLL：获取、校验与放置

### 是否需要自行准备

历史 0.1.x 文件名含 `local` 的本机测试包曾包含这些运行库，未作为本仓库的公开二进制发布。只有维护历史版本时才需要参考下列提取方法；当前 0.2.4 不使用这三个 DLL。本节的 7-Zip 仅用于历史研究中的安装包解包。

目前代码只验证下面这套 **PC Manager 5.8.1.130、x64** 运行库，不能用任意新版同名 DLL 替代，也不是从手机 APK 中获取。

| 文件 | SHA-256（完整值） |
| --- | --- |
| `MiDropTransfer.dll` | `fac9fde6d7b86a83555ea51fc7523dd3d03ec23c55611625ef5c1537ee8b95aa` |
| `LIBEAY32.dll` | `0dfd5ed5265a014213c71b12df30141cdebadf3410c58ba9cf85d1c7ff6bbef0` |
| `ssleay32.dll` | `73d263a26866fe2a6d163e9bc955e60227bbe7560d6465a21cddcab5e2fd3655` |

### 从哪里获取

来源是小米笔记本 **PC Manager 5.8.1.130 官方安装包**：[小米官方 CDN 下载](https://cdn.cnbj1.fds.api.mi-img.com/ota-packages/TS4L_XiaomiPCManager_feature_p52_5.8.1.130_b5a04836.exe)。也可从自己合法安装的同版本 PC Manager 目录中查找并复制这三个文件，然后核对上表哈希；安装目录可能因机型而不同，本文不假定一个未经验证的固定管家路径。

无需为了获取 DLL 在非小米电脑上安装整个管家。可以只下载安装包，再提取其内嵌文件。以下命令在源码根目录的 PowerShell 中运行：

```powershell
New-Item -ItemType Directory -Path '.\work\vendor' -Force | Out-Null
Invoke-WebRequest 'https://cdn.cnbj1.fds.api.mi-img.com/ota-packages/TS4L_XiaomiPCManager_feature_p52_5.8.1.130_b5a04836.exe' `
  -OutFile '.\work\vendor\PCManager-5.8.1.130.exe'
```

<a id="dll-extraction"></a>
### 不安装管家，直接提取 DLL

先从 [7-Zip 官网](https://www.7-zip.org/) 安装 x64 版 7-Zip。下面的做法是从该固定版本 EXE 中复制内嵌 7z 数据，再用 7-Zip 只提取三个 DLL，**不会运行小米安装程序**。它仅适用于上面的准确版本和文件大小；直接把 EXE 当普通 ZIP 打开不一定能找到实际文件。若包版本或格式不匹配，应停止并检查来源，不要修改偏移硬套。

```powershell
$ErrorActionPreference = 'Stop'
$installer = (Resolve-Path '.\work\vendor\PCManager-5.8.1.130.exe').Path
$sevenZip = Join-Path $env:ProgramFiles '7-Zip\7z.exe'
$archive = Join-Path $PWD 'work\vendor\PCManager-components.7z'
$runtime = Join-Path $PWD 'work\vendor\XiaomiRuntime'
if (!(Test-Path -LiteralPath $sevenZip)) { throw 'Install 7-Zip x64 first, or set $sevenZip to your 7z.exe.' }
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$reader = [IO.File]::OpenRead($installer)
try {
    if ($reader.Length -ne 766644624) { throw 'This recipe requires the exact PC Manager 5.8.1.130 package.' }
    $reader.Position = 290304352
    $header = New-Object byte[] 32
    if ($reader.Read($header, 0, 32) -ne 32 -or [BitConverter]::ToString($header, 0, 6) -ne '37-7A-BC-AF-27-1C') { throw 'Embedded archive header mismatch.' }
    $remaining = [BitConverter]::ToUInt64($header, 12) + [BitConverter]::ToUInt64($header, 20)
    if ($remaining -gt ($reader.Length - $reader.Position)) { throw 'Truncated installer.' }
    $writer = [IO.File]::Create($archive)
    try {
        $writer.Write($header, 0, 32)
        $buffer = New-Object byte[] (4 * 1024 * 1024)
        while ($remaining -gt 0) {
            $count = $reader.Read($buffer, 0, [int][Math]::Min($buffer.Length, $remaining))
            if ($count -eq 0) { throw 'Unexpected end of installer.' }
            $writer.Write($buffer, 0, $count)
            $remaining -= $count
        }
    } finally { $writer.Dispose() }
} finally { $reader.Dispose() }
& $sevenZip e -y "-o$runtime" $archive 'MiDropTransfer.dll' 'LIBEAY32.dll' 'ssleay32.dll'
if ($LASTEXITCODE -ne 0) { throw 'DLL extraction failed.' }
Get-ChildItem -LiteralPath $runtime -Filter '*.dll' | Get-FileHash -Algorithm SHA256
```

提取结果在 `work\vendor\XiaomiRuntime`。将命令输出的三个 SHA-256 与上表逐项比较；程序发送时也会检查这三个文件的哈希。不匹配时会报告 `Unsupported Xiaomi component version`，应重新准备准确版本。

### DLL 放到哪里

三个文件必须在**同一个** `XiaomiRuntime` 文件夹内，结构如下：

```text
MiShare.exe
XiaomiRuntime/
  MiDropTransfer.dll
  LIBEAY32.dll
  ssleay32.dll
```

| 使用场景 | 完整位置 |
| --- | --- |
| 已安装的软件 | `<安装目录>\XiaomiRuntime\`，如 `D:\Programs\MiShare\XiaomiRuntime\` |
| 解压后直接运行 `app\MiShare.exe` | 解压目录下 `app\XiaomiRuntime\` |
| 从源码构建和准备安装包 | `dist\MiShare-win-x64\app\XiaomiRuntime\` |
| 存放提取出的原始 DLL，交给构建脚本 | 源码目录下 `work\vendor\XiaomiRuntime\` |

这里的相对位置始终以 **MiShare.exe 所在文件夹**为准，不是接收文件目录。不要放到 `System32`、`python` 或源码 `transport` 目录。已安装时先从系统托盘退出 MiShare，再复制：

```powershell
$installDirectory = 'D:\Programs\MiShare' # Change this to your chosen installation folder
$target = Join-Path $installDirectory 'XiaomiRuntime'
New-Item -ItemType Directory -Path $target -Force | Out-Null
Copy-Item '.\work\vendor\XiaomiRuntime\MiDropTransfer.dll' $target -Force
Copy-Item '.\work\vendor\XiaomiRuntime\LIBEAY32.dll' $target -Force
Copy-Item '.\work\vendor\XiaomiRuntime\ssleay32.dll' $target -Force
```

历史版本曾使用 `-XiaomiRuntimePath` 复制这些文件，并在安装前检查是否齐全。这个参数已经从当前构建脚本移除，不要用于 0.2.4。维护旧版本遇到依赖模块缺失时，还需检查适用的 x64 VC++ 运行库；随意替换成其他版本的小米 DLL 并不可靠。

这些文件是小米/其依赖方的组件，不属于本项目 MIT 授权范围；官方链接和提取步骤不代表公开再分发授权。公开仓库不应提交这三个 DLL；本机完整包也不应未经授权直接作为公开 Release 上传。具体出处和许可证说明已合并在本 README 后部。

## 源码与依赖边界

这是混合实现，并未全部改为 Rust：

| 目录 | 用途 |
| --- | --- |
| `desktop/` | C# / .NET 10 WPF 界面、托盘、历史、Windows 集成、校验与进程管理 |
| `desktop/transport.py` | 桌面与传输进程的 JSON 标准输入输出桥接 |
| `transport/` | Python 蓝牙发现、协议握手及 Windows Wi-Fi Direct 编排 |
| `rust-receiver/` | Rust HTTPS 接收、流式写盘、文件校验及进度 |
| `tests/` | 协议解析、组网和接收等单元测试 |
| `scripts/` | Release 构建、普通用户安装及卸载 |
| `LICENSES/` | 上游许可证、锁定依赖清单及出处 |

**历史 0.1.x 发送功能曾调用小米闭源 `MiDropTransfer.dll` 及其 `LIBEAY32.dll`、`ssleay32.dll` 依赖；当前 0.2.4 已使用独立 Rust 文件服务，不加载这些 DLL。** 本仓库不包含厂商二进制或它们的反编译源码文件，项目 MIT 许可不覆盖厂商材料。历史完整本机包不代表获得了公开再分发授权，不随本次源码发布。


</details>

<a id="third-party"></a>

## 第三方来源与许可 / Third-party sources and licenses

This section distinguishes adapted source, open-source dependencies, and proprietary runtime files. The top-level MIT license applies to new project code only; it does not relicense any upstream component.

本节区分改编源码、开源依赖与闭源运行库。项目根目录 MIT 许可仅覆盖新增项目代码，不替代第三方许可。

### Tandem: adapted receiver / 改编的接收代码

- Upstream: https://github.com/wumai2580/tandem
- Exact source: https://github.com/wumai2580/tandem/blob/89274a33d0ef4a8ae9a68e5767c221883ed5e907/tandem/mtapc.py
- Revision: `89274a33d0ef4a8ae9a68e5767c221883ed5e907`
- License: MIT; Copyright (c) 2026 Tandem contributors.
- Derived file: `transport/receiver.py`. Changes include Windows WLAN association/routing, retries, Wi-Fi Direct group handling, integrity/progress reporting, cancellation and desktop integration. Additional protocol code interoperates with the same native sharing service.
- Full original notice: `transport/LICENSE` and `LICENSES/Tandem-MIT.txt`.

`transport/LICENSE` 保留改编接收文件 `transport/receiver.py` 的上游 MIT 通知；它不表示同目录所有文件都来自 Tandem。其他新增项目文件的许可见根目录 `LICENSE`，第三方组件分别遵守 `LICENSES/` 中的原始许可。

### Open-source dependencies / 开源依赖

| Component | Source | License / included notice |
| --- | --- | --- |
| .NET / WPF 10.0.15 | https://github.com/dotnet/runtime ; https://github.com/dotnet/wpf | MIT and upstream third-party notices; `LICENSES/dotnet/` |
| Python 3.12.10 embedded x64 | https://www.python.org/downloads/release/python-31210/ | PSF and bundled component licenses; `LICENSES/Python-3.12.10.txt` |
| PyWinRT / winrt-runtime | https://github.com/pywinrt/pywinrt | MIT; installed wheel notices in `LICENSES/python/` |
| cryptography | https://github.com/pyca/cryptography | Apache-2.0 OR BSD-3-Clause; bundled native dependency notices included |
| PyCryptodome | https://www.pycryptodome.org/ | BSD / public-domain portions and bundled component licenses; `LICENSES/python/` |
| cffi / typing_extensions | https://cffi.readthedocs.io/ ; https://github.com/python/typing_extensions | MIT-0 / PSF-derived terms; installed wheel notices included |
| pycparser | https://github.com/eliben/pycparser | BSD-3-Clause; installed wheel license included |
| Rust crates | Exact versions in `rust-receiver/Cargo.lock` | Per-crate upstream URL, license and checksum in `LICENSES/rust-dependencies.csv`; texts in `LICENSES/rust/` |

Python package versions and upstream URLs are recorded in `LICENSES/python-dependencies.csv`. Cargo.lock also contains dependencies for other targets; unfetched entries are listed separately and are not asserted to be linked into the Windows binary. The inventory is a lockfile/build-input inventory, not a claim that every listed crate is linked at runtime.

### Native identity and format references / 原生身份与格式参考

The independently written service and BKS importer do not copy Xiaomi machine code or decompiled function bodies. BKS parsing and PKCS#12 key derivation follow the public [Bouncy Castle store format](https://github.com/bcgit/bc-java/blob/main/prov/src/main/java/org/bouncycastle/jcajce/provider/keystore/bc/BcKeyStoreSpi.java) and [RFC 7292 Appendix B](https://www.rfc-editor.org/rfc/rfc7292#appendix-B). Cryptographic primitives come from the already listed PyCryptodome and cryptography packages.

`Identity/pc.bks` is a separate native resource from MiShare 4.13.12, `res/aO.bks` / `raw/pc`. It contains a trusted certificate and a sealed RSA private-key entry. It is not our MIT-licensed code. The public source archive excludes this resource, APKs, plaintext keys and PFX files. Local extraction and successful interoperability do not establish a redistribution grant; keep the resource separate when publishing source. Lindrop 0.2.2 source, ZIP and EXE distributions exclude this identity. Historical MiShare-branded `local` installers included it for the existing local environment and are not equivalent to the new identity-free distributions.

### Historical Xiaomi runtime / 历史小米运行库

Historical 0.1.x local builds used `MiDropTransfer.dll`, `LIBEAY32.dll` and `ssleay32.dll`, extracted from Xiaomi notebook PC Manager 5.8.1.130 during interoperability research. Original vendor download recorded during development:

https://cdn.cnbj1.fds.api.mi-img.com/ota-packages/TS4L_XiaomiPCManager_feature_p52_5.8.1.130_b5a04836.exe

The public source archive excludes these binaries, vendor installers, APKs and decompiled vendor code. `LICENSES/Xiaomi-runtime-SHA256.txt` identifies the three files used by historical 0.1.x test bundles. The 0.2 independent package contains none of them. No Xiaomi redistribution license has been established; obtain permission or replace the runtime before publishing a full binary Release. Merely linking this vendor URL or recording hashes grants no rights.

`LIBEAY32.dll` and `ssleay32.dll` are legacy OpenSSL/SSLeay dependencies. Their corresponding upstream license notice is retained in `LICENSES/OpenSSL-legacy-SSLeay.txt`; vendor modifications and exact build provenance have not been independently verified.

This product includes software developed by the OpenSSL Project for use in the OpenSSL Toolkit (http://www.openssl.org/).

This product includes cryptographic software written by Eric Young (eay@cryptsoft.com).

This product includes software written by Tim Hudson (tjh@cryptsoft.com).

These acknowledgements do not imply endorsement or a Xiaomi DLL redistribution grant.

### Microsoft Visual C++ runtime / 微软 VC++ 运行库

The local test bundle also includes matching x64 `msvcp140.dll`, `vcruntime140.dll` and `vcruntime140_1.dll` (14.44.35211.0) from the existing development environment's Microsoft runtime files. They are Microsoft components, not covered by this project's MIT license. No Qt, PySide or Shiboken application code is included. The build script accepts a separately supplied runtime directory and does not infer redistribution rights. For public binary distribution, obtain the runtime and applicable license through Microsoft's official redistribution channels: https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files . Hashes are recorded in `LICENSES/Microsoft-VC-runtime-SHA256.txt`.

### Installer engine / 安装引擎

The double-click EXE installer is generated using Inno Setup 7.1.1, by Jordan Russell and Martijn Laan: https://jrsoftware.org/inno-setup . Its original copyright and website notices remain in the installer engine. License text: `LICENSES/Inno-Setup.txt`. The Simplified Chinese translation is maintained by Zhenghan Yang (Kira), supplied in the official distribution; upstream: https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation . Our `scripts/Lindrop.iss` and `Build-Installer.ps1` are MIT-licensed project code. Inno Setup is not needed on the target PC. For commercial use of Inno Setup, consult the vendor's current terms.

### Design references and branding / 设计参考与名称

Visual direction was inspired by Apple's public Liquid Glass overview: https://developer.apple.com/cn/documentation/technologyoverviews/liquid-glass . No Apple implementation, framework, icon or logo was copied into this source tree. The project icon was created during project development. Xiaomi/MiShare and Apple names remain the property of their respective owners. This community project is not endorsed by them.
