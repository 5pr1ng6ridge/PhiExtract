# PhiExtract

从 Phigros APK 提取歌曲资源（谱面 / 音频 / 曲绘），并生成可直接使用的 `info.txt`。

- **TUI**：`song_tui.py` —— 搜索曲目、勾选难度、填写元数据后导出（推荐）
- **CLI**：`extract_songs.py` —— 脚本化批量导出

## 环境要求

- Windows x64 + Python 3.9+
- .NET 6 或更高版本的运行时（[.NET Runtime](https://dotnet.microsoft.com/download/dotnet)，不是 SDK；6 / 7 / 8 / 9 / 10 均可）
- `AssetStudioModCLI` 已随仓库附带在 `asmc/`，无需另外安装

```powershell
python -m pip install -r requirements-tui.txt
```

## 终端界面（推荐）

```powershell
python song_tui.py                                       # APK 路径留空，在界面里填
python song_tui.py D:\games\com.phi40.apk -o D:\out      # 也可以直接传参
```

也可以双击 `start_tui.bat`。用法：

1. 填 APK 路径 → 点「加载 APK」
2. 左侧搜索曲目（曲目 ID 的任意子串，不区分大小写），方向键切换
3. 右侧勾选难度，填 Level / Charter，以及 Name / Composer / Illustrator
4. 确认输出目录（默认 `./extracted/`）→ 点「开始导出」

快捷键：`Ctrl+F` 搜索、`Ctrl+E` 导出、`Ctrl+Q` 退出。导出在后台线程执行，界面不会卡住，底部显示进度与错误。

连续加载两个 APK 时会与**上一个成功加载的版本**比较：新增曲目绿色 `＋`、移除曲目红色 `－`，共同曲目的难度增减同样标色。被移除的曲目只能查看，无法从当前 APK 导出。

## 命令行

```powershell
python extract_songs.py com.phi40.apk --list
python extract_songs.py com.phi40.apk -s 000AinSophAur -o ./extracted
python extract_songs.py com.phi40.apk -s 000AinSophAur -s Igallta -o ./extracted
python extract_songs.py com.phi40.apk --all -o ./extracted_all
```

`-s` 接受曲目 ID 或唯一子串，可重复；匹配到多首会报错，请改用完整 ID。`--all` 导出全部曲目（4.0 包约 327 首），注意可能占用几十 GB 磁盘。

## 输出结构

```
<输出目录>/<曲目 ID>/<难度>/
├── Chart_XX.json      # 谱面
├── music.wav          # 音频
├── Illustration.jpg   # 曲绘（无原图时回退低清 / 模糊图）
└── info.txt
```

`info.txt` 字段：`Name / Song / Chart / Picture / Level / Composer / Illustrator / Charter`，其中 Song / Chart / Picture 与实际导出的文件名一致（图片是 `.jpg`，不是 `.png`）。

几点注意：

- **已有目录不会被覆盖**：重新导出请换输出目录，或先自行清理旧目录。
- Name / Composer 只能由曲目 ID 推断，Level / Charter / Illustrator 无法从 catalog 可靠获取 —— 请在界面上核对后再导出。
- 缺曲绘的曲目会直接报错，不会生成无效的 `info.txt`；`.c9Locked` 加密版本跳过；章节封面不属于歌曲资源。

## 实现要点

- 从 APK 的 `assets/aa/catalog.json` 解析：`m_EntryDataString` → dependencyKey → bucket → bundle 条目 → `m_InternalIds` 得到真实文件名（旧包回退到旧式 key 命名）。
- 只把需要的 bundle 从 APK **流式**复制到临时目录，不会把整包 2–3 GB 读入内存。
- 先在临时目录完成导出并校验每个文件非空，再原子重命名提交；失败返回非零退出码和具体错误，不会误报成功。

## 其他

```powershell
python bundle_mapping_check.py com.phi40.apk   # 排查 catalog → bundle 映射
```

自定义 CLI 路径：`--asmc "C:\path\to\AssetStudioModCLI.exe"`（默认用自带的 `asmc/`）。当前只为 `-g none` 命名方式适配了谱面 TextAsset、图片 Texture2D 和音频 AudioClip；游戏版本或 CLI 格式变化会明确报错，而不是静默忽略。

## 打包分发

```
PhiExtract/
├── song_tui.py              # 终端界面
├── extract_songs.py         # 提取核心 + 命令行
├── bundle_mapping_check.py  # 排查 catalog → bundle 映射
├── start_tui.bat            # 双击启动 TUI
├── requirements-tui.txt     # TUI 依赖（textual）
├── README.md
└── asmc/                    # AssetStudioModCLI 0.18.0（Windows x64，8 MB）
```

整个目录压成 zip 即可发给别人，对方只需要自己装 Python 依赖和 .NET Runtime。

`asmc/` 是上游发布的**裁剪过的 Windows x64 最小集**，删掉了用不到的部分（约省 50 MB），其余文件逐个验证过都是必需的，不要单独删：

| 删除内容 | 结果 |
|---|---|
| `runtimes/` 下的 linux / macOS / win-x86 / win-arm64 | 可删，本工具只在 Windows x64 跑 |
| `AssetStudioFBXWrapper.dll` + `runtimes/win-x64/native/AssetStudioFBXNative.dll` | 可删，本工具只导出 `tex2d,textAsset,audio`，不用 FBX/3D |
| `Mono.Cecil*.dll` | **不能删**，启动时即报 `TypeInitializationException` |
| `Newtonsoft.Json.dll` | **不能删**，图片导出会静默少文件 |
| `runtimes/win-x64/native/fmod.dll` | **不能删**，音频导出失败 |
| `runtimes/win-x64/native/Texture2DDecoderNative.dll` | **不能删**，曲绘（ASTC/ETC）解不出来 |

另外 `asmc/AssetStudioModCLI.runtimeconfig.json` 里加了 `"rollForward": "LatestMajor"`：上游只认 .NET 6，加上后 .NET 6/7/8/9/10 都能跑（.NET 6 已停止支持，新机器往往只有 8+）。
