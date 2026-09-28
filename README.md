# PhiExtract

从 Phigros APK 提取歌曲资源（谱面 / 音频 / 曲绘），并生成 `info.txt`，支持4.0版本。

曲名 / 曲师 / 画师 / 定数 / 谱师 直接从 APK 自带元数据读取。

- **TUI**：`song_tui.py` —— 搜索曲目、勾选难度、填写元数据后导出（推荐）
- **CLI**：`extract_songs.py` —— 脚本化批量导出

## 环境要求

- Windows x64 + Python 3.9+
- .NET 6 或更高版本的运行时（[.NET Runtime](https://dotnet.microsoft.com/download/dotnet)；6 / 7 / 8 / 9 / 10 均可）
- `AssetStudioModCLI` 裁剪过的必要部分已随仓库附带在 `asmc/`，无需另外安装

```powershell
python -m pip install -r requirements-tui.txt
```

## TUI界面

```powershell
python song_tui.py                                       # APK 路径留空，在界面里填
python song_tui.py D:\games\com.phi40.apk -o D:\out      # 也可以直接传参
```

也可以双击 `start_tui.bat`。用法：

1. 填 APK 路径 → 点「加载 APK」
2. 左侧搜索曲目（曲目 ID 的任意子串，不区分大小写），方向键切换
3. 右侧勾选难度；Level / Charter / Name / Composer / Illustrator 已自动填好，可直接修改
4. 确认输出目录（默认 `./extracted/`）→ 点「开始导出」

快捷键：`Ctrl+F` 搜索、`Ctrl+E` 导出、`Ctrl+Q` 退出。导出在后台线程执行。底部显示进度与错误。

连续加载两个 APK 时会与**上一个成功加载的版本**比较：新增曲目绿色 `＋`、移除曲目红色 `－`，共同曲目的难度增减同样标色。被移除的曲目只能查看，无法从当前 APK 导出。

## 命令行

```powershell
python extract_songs.py com.phi40.apk --list
python extract_songs.py com.phi40.apk -s 000AinSophAur -o ./extracted
python extract_songs.py com.phi40.apk -s 000AinSophAur -s Igallta -o ./extracted
python extract_songs.py com.phi40.apk --all -o ./extracted_all

# 每个难度一个目录，并自动写入 info.txt（与 TUI 输出一致）
python extract_songs.py com.phi40.apk -s Igallta --info -o ./extracted

# 把游戏自带的曲目元数据导出成 JSON（曲名/曲师/画师/定数/谱师/试听段）
python extract_songs.py com.phi40.apk --meta -o meta.json   # 推荐：直接写 UTF-8 文件
python extract_songs.py com.phi40.apk --meta                # 输出到终端（非 ASCII 转义，管道安全）
```

`-s` 接受曲目 ID 或唯一子串，可重复；匹配到多首会报错，请改用完整 ID。`--all` 导出全部曲目（4.0 包约 327 首），注意可能占用几十 GB 磁盘。
不加 `--info` 时按原有方式导出（一首曲目一个目录，所有难度混放，不写 `info.txt`）。

> 旧版 cmd 控制台/管道默认不是 UTF-8，中日文曲目 ID 可能乱码或读取报错；先执行 `set PYTHONUTF8=1` 即可。

## 输出结构

```
<输出目录>/<曲目 ID>/<难度>/
├── Chart_XX.json      # 谱面
├── music.wav          # 音频
├── Illustration.jpg   # 曲绘
└── info.txt
```

`info.txt`（RPE 格式，第一行必须是 `#`）字段：`Name / Song / Chart / Picture / Level / Composer / Illustration / Illustrator / Charter`，其中 Song / Chart / Picture 与实际文件名一致，`Level` 形如 `IN 15.9`。
画师字段同时写 `Illustration`（RPE 文档用名）（哇。我都不知道RPE有这一项。）和 `Illustrator`（phira可能更喜欢这个），读取方用哪个都能认。

## 元数据来源

游戏启动场景 `assets/bin/Data/level0` 里存着自己的曲目表（Unity 二进制序列化，字段无名），本工具按位置解析：

```
songsId, songsName(检索名), songsTitle(显示名), int32,
float[] levels(定数，0 = 无此难度), illustrator,
string[] charter(每难度谱师), composer, string[] difficulty,
float previewTime, float previewEndTime, ...
```

少数没有记录的曲目（`Random.SobremSilentroom.1`~`.6` 这类占位条目）会自动回退成「从曲目 ID 推断 + Unknown」，仍可手动填写。

## 实现要点

- 从 APK 的 `assets/aa/catalog.json` 解析：`m_EntryDataString` → dependencyKey → bucket → bundle 条目 → `m_InternalIds` 得到真实文件名（旧包回退到旧式 key 命名）。
- 只把需要的 bundle 从 APK 流式复制到临时目录。
- 先在临时目录完成导出并校验每个文件非空，再原子重命名提交；失败返回非零退出码和具体错误。

## 其他

```powershell
python bundle_mapping_check.py com.phi40.apk   # 排查 catalog → bundle 映射
```

自定义 CLI 路径：`--asmc "C:\path\to\AssetStudioModCLI.exe"`（默认用自带的 `asmc/`）。当前只为 `-g none` 命名方式适配了谱面 TextAsset、图片 Texture2D 和音频 AudioClip；游戏版本或 CLI 格式变化会明确报错，而不是静默忽略。
