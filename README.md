# PhiExtract

从 Phigros APK 提取歌曲资源（谱面 / 音频 / 曲绘），并生成 `info.txt`，自动提取 曲名 / 曲师 / 画师 / 定数 / 谱师。

## 环境要求

- Windows x64 + Python 3.9+
- .NET 6 或更高版本的运行时（[.NET Runtime](https://dotnet.microsoft.com/download/dotnet)

```powershell
python -m pip install -r requirements.txt
```

## TUI界面

双击 `start_tui.bat` 或：

```powershell
python song_tui.py                                       
python song_tui.py D:\games\Phi_4.0.1.apk -o D:\out
python song_tui.py Phi_4.0.1.apk --zip      
```
连续加载两个 APK 时会与上一个成功加载的版本比较并标注变化曲目。

## 命令行

```powershell
python extract_songs.py Phi_4.0.apk --list
python extract_songs.py Phi_4.0.apk -s 000AinSophAur -o ./extracted
python extract_songs.py Phi_4.0.apk -s 000AinSophAur -s Igallta -o ./extracted
python extract_songs.py Phi_4.0.apk --all -o ./extracted_all

# 特殊谱面：愚人节 SP；4.0 的第九章剧情内 thtw 谱面
python extract_songs.py Phi_3.19.0.1.apk -s OblivionPHIN --info -o ./extracted
python extract_songs.py Phi_4.0.apk -s TrueHomeTrueWorld --info -o ./extracted

# 把曲目信息（曲名/曲师/画师/定数/谱师/试听段）导出成 JSON
python extract_songs.py com.phi40.apk --meta -o meta.json  
python extract_songs.py com.phi40.apk --meta                # 输出到终端
```

`-s` 接受曲目 ID 或唯一子串。`--all` 导出全部曲目。

> 旧版 cmd 控制台/管道默认不是 UTF-8，中日文曲目 ID 可能乱码或读取报错；先执行 `set PYTHONUTF8=1` 即可

## 实现要点

- 从 APK 的 `assets/aa/catalog.json` 解析：`m_EntryDataString` → dependencyKey → bucket → bundle 条目 → `m_InternalIds` 得到真实文件名（旧包回退到旧式 key 命名）。
- 把需要的 bundle 从 APK 流式复制到临时目录。
- 旧版本谱面数据直接来自 APK 的 `assets/bin/Data/level0`。
- 4.0.1 将场景打包进了 `assets/bin/Data/data.unity3d`（UnityFS）。工具读取目录表并解压覆盖 `level0` 的块。依赖 `requirements.txt` 中的 `lz4`。
