# PhiExtract

从 Phigros APK 提取歌曲资源（谱面 / 音频 / 曲绘），并生成 `info.txt`，支持 3.19.0.1、3.20、4.0 和 4.0.1 等已验证版本。

曲名 / 曲师 / 画师 / 定数 / 谱师 直接从 APK 自带元数据读取。

- **TUI**：`song_tui.py` —— 搜索曲目、勾选难度、填写元数据后导出（推荐）
- **CLI**：`extract_songs.py` —— 脚本化批量导出

## 环境要求

- Windows x64 + Python 3.9+
- .NET 6 或更高版本的运行时（[.NET Runtime](https://dotnet.microsoft.com/download/dotnet)；6 / 7 / 8 / 9 / 10 均可）
- `AssetStudioModCLI` 裁剪过的必要部分已随仓库附带在 `asmc/`，无需另外安装

```powershell
python -m pip install -r requirements.txt
```

## TUI界面

```powershell
python song_tui.py                                       # APK 路径留空，在界面里填
python song_tui.py D:\games\com.phi40.apk -o D:\out      # 也可以直接传参
```

也可以双击 `start_tui.bat`。用法：

1. 填 APK 路径 → 点「加载 APK」
2. 左侧搜索曲目（曲目 ID 的任意子串，不区分大小写），方向键切换
3. 右侧勾选难度（含 Legacy、SP、第九章 C9 特例）；Level / Charter / Name / Composer / Illustrator 已自动填好，可直接修改
4. 可选勾选「自动 ZIP」（默认关闭），确认输出目录（默认 `./extracted/`）→ 点「开始导出」

自动 ZIP 保留原难度目录，另外在输出目录生成 `曲名_难度.zip`（用界面 Name 值，空值回退到曲目 ID；非法文件名字符替换为 `_`，过长名称截断）。ZIP 内文件直接位于根目录，无额外嵌套目录；同名 ZIP 或已有难度目录不会覆盖。

快捷键：`Ctrl+F` 搜索、`Ctrl+E` 导出、`Ctrl+Q` 退出。导出在后台线程执行。底部显示进度与错误。

连续加载两个 APK 时会与**上一个成功加载的版本**比较：新增曲目绿色 `＋`、移除曲目红色 `－`，共同曲目的难度增减同样标色。被移除的曲目只能查看，无法从当前 APK 导出。

## 命令行

```powershell
python extract_songs.py com.phi40.apk --list
python extract_songs.py com.phi40.apk -s 000AinSophAur -o ./extracted
python extract_songs.py com.phi40.apk -s 000AinSophAur -s Igallta -o ./extracted
python extract_songs.py com.phi40.apk --all -o ./extracted_all

# 特殊谱面：3.19.0.1 的愚人节 SP；4.0 的第九章未完成 C9 谱面
python extract_songs.py Phi_3.19.0.1.apk -s OblivionPHIN --info -o ./extracted
python extract_songs.py Phi_4.0.apk -s TrueHomeTrueWorld --info -o ./extracted

# 每个难度一个目录，并自动写入 info.txt（与 TUI 输出一致）
python extract_songs.py com.phi40.apk -s Igallta --info -o ./extracted

# 新版本差分曲绘 + 每个难度额外压缩为 曲名_难度.zip（--zip 隐含 --info）
python extract_songs.py Phi_4.0.1.apk -s WhatdoyouwantmorethanaHappyending --zip -o ./extracted
# TUI 也可从命令行预先打开开关，界面仍可取消
python song_tui.py Phi_4.0.1.apk --zip

# 把游戏自带的曲目元数据导出成 JSON（曲名/曲师/画师/定数/谱师/试听段）
python extract_songs.py com.phi40.apk --meta -o meta.json   # 推荐：直接写 UTF-8 文件
python extract_songs.py com.phi40.apk --meta                # 输出到终端（非 ASCII 转义，管道安全）
```

`-s` 接受曲目 ID 或唯一子串，可重复；匹配到多首会报错，请改用完整 ID。`--all` 导出全部曲目（4.0 包含 327 首常规曲目与 1 首第九章特例），注意可能占用几十 GB 磁盘。
不加 `--info` / `--zip` 时按原有方式导出（一首曲目一个目录，所有难度混放，不写 `info.txt`）。

> 旧版 cmd 控制台/管道默认不是 UTF-8，中日文曲目 ID 可能乱码或读取报错；先执行 `set PYTHONUTF8=1` 即可。

## 输出结构

```
<输出目录>/<曲目 ID>/<难度>/
├── Chart_XX.json      # 谱面
├── music.wav          # 音频
├── Illustration.jpg   # 主曲绘（有则导出）
├── IllustrationBlur.jpg / IllustrationLowRes.jpg  # 找到的其他曲绘一并导出
└── info.txt
```

`info.txt`（RPE 格式，第一行必须是 `#`）字段：`Name / Song / Chart / Picture / Level / Composer / Illustration / Illustrator / Charter`，其中 Song / Chart / Picture 与实际文件名一致，`Level` 形如 `IN 15.9`。
画师字段同时写 `Illustration`（RPE 文档用名）和 `Illustrator`（兼容部分社区工具）；`Picture` 指向该难度优先选择的曲绘，其他找到的曲绘也会一并保存。

### 差分曲绘与缺失资源

4.0.1 的 `WhatdoyouwantmorethanaHappyending` 按 EZ/HD/IN/AT 提供独立曲绘。优先选择 `Illustration_<难度>.jpg`，其次是该难度低清/模糊图，最后才回退到通用曲绘；不会误借另一个难度的图。各难度目录保留全部已找到的曲绘（该曲共 12 张：四个难度各三张），`Picture` 仅引用当前难度适用的那张。

缺少音频、适用曲绘或无法填写合法的必填元数据时，**仍导出谱面和其它可用资源，但跳过 `info.txt`**；TUI 弹警告并写入日志，CLI 打印 `WARNING`，可继续导出/压缩，不返回失败。`Unknown` 回退值仍可用于生成 info。谱面本身缺失、bundle 损坏/解密失败或转换器执行失败仍视为错误。已有输出不会被覆盖。

## 元数据来源

游戏启动场景 `level0` 里存着自己的曲目表（Unity 二进制序列化，字段无名），本工具按位置解析：

- 旧版本直接读取 APK 的 `assets/bin/Data/level0`。
- 4.0.1 将场景打包进 `assets/bin/Data/data.unity3d`（UnityFS）；工具读取目录表并只解压覆盖 `level0` 的块，不全量展开场景。LZ4 解压依赖 `requirements.txt` 中的 `lz4`。
- 已验证覆盖率：3.19.0.1 为 303/310，3.20 为 315/321，4.0 为 321/328，4.0.1 为 **328/335**。4.0.1 的 `What do you want more than a Happy ending?`、`True Home, True World (Rework)` 均有真实元数据。
- 元数据来源缺失、损坏或缺少解压依赖时会明确警告，再使用回退值；不会悄悄把整份曲目表当成不存在，也不阻止谱面导出。

```
songsId, songsName(检索名), songsTitle(显示名), int32,
float[] levels(定数，0 = 无此难度), illustrator,
string[] charter(每难度谱师), composer, string[] difficulty,
float previewTime, float previewEndTime, ...
```

少数没有记录的曲目（`Random.SobremSilentroom.1`~`.6` 这类占位条目）会自动回退成「从曲目 ID 推断 + Unknown」，仍可手动填写。

### 特殊难度与第九章

- **Legacy**（旧谱面）：按 APK 中的 `Chart_Legacy.json` 独立列出，原有定数和谱师仍从游戏曲目表读取。
- **SP**（3.19.0.1 愚人节）：`Oblivion: PHIN` 在 APK 中只有 `Chart_IN.json`，但实际是 2026 音符的 SP 谱面；界面标记为 SP，使用 `--info` 导出时改名为 `Chart_SP.json`，并保留主图、模糊图、低清图。3.20 已无此曲目。无游戏曲目表元数据时，未知画师/谱师保持 `Unknown`，可在 TUI 修改。
- **C9**（4.0 第九章未完成演出）：以单独的 `TrueHomeTrueWorld.C9.0` 列出；谱面和音频来自 `c9s.*` 加密包，需要 `pycryptodome`。谱面导出为 `Chart_C9.json`（458 个音符）；包内只有可用的 `IllustrationBlur.jpg`，按约定将其写进 `info.txt` 的 `Picture`，**不是正式高清曲绘**。`C9` 是本工具标记特例的标签，并非游戏正式公布的难度/元数据。解密文件只在临时导出目录存在，失败时清理；不覆盖现有导出。

## 实现要点

- 从 APK 的 `assets/aa/catalog.json` 解析：`m_EntryDataString` → dependencyKey → bucket → bundle 条目 → `m_InternalIds` 得到真实文件名（旧包回退到旧式 key 命名）。
- 只把需要的 bundle 从 APK 流式复制到临时目录。
- 先在临时目录完成导出并校验谱面，缺少可选音频/曲绘时警告降级；目录/ZIP 准备完毕后再提交，ZIP 用排他方式发布以防覆盖。真正的转换失败返回非零退出码和具体错误。

## 其他

```powershell
python bundle_mapping_check.py com.phi40.apk   # 排查 catalog → bundle 映射
```

自定义 CLI 路径：`--asmc "C:\path\to\AssetStudioModCLI.exe"`（默认用自带的 `asmc/`）。当前只为 `-g none` 命名方式适配了谱面 TextAsset、图片 Texture2D 和音频 AudioClip；游戏版本或 CLI 格式变化会明确报错，而不是静默忽略。
