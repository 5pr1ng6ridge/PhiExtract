#!/usr/bin/env python3
"""PhiExtract TUI: choose songs and difficulties, then fill in info.txt metadata."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import zipfile

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from extract_songs import (CATALOG, Catalog, DifficultyInfo, ExtractionError, SongInfo,
                           available_difficulties, default_cli, extract_difficulties,
                           read_song_meta, song_metadata)

DIFFICULTIES = ("EZ", "HD", "IN", "AT", "Legacy", "EZ_Error", "HD_Error", "IN_Error")


class SongTUI(App):
    TITLE = "PhiExtract"
    CSS = """
    Screen { layout: vertical; }
    #paths { height: auto; padding: 0 1; }
    #apk, #output { width: 1fr; }
    /* Ghost buttons: no solid fill, accent outline only on hover/focus. */
    #load, #export {
        width: auto;
        min-width: 12;
        height: 3;
        margin-left: 1;
        background: transparent;
        border: round $primary 35%;
        color: $text-muted;
        text-style: none;
    }
    #load:hover, #export:hover {
        background: $primary 15%;
        border: round $primary;
        color: $text;
    }
    #load:focus, #export:focus {
        border: round $accent;
        color: $text;
        text-style: bold;
    }
    #load:disabled, #export:disabled {
        background: transparent;
        border: round $panel-lighten-2;
        color: $text-disabled;
        text-opacity: 0.6;
    }
    #body { height: 1fr; min-height: 12; }
    #left { width: 42%; border: solid $accent; padding: 0 1; }
    #right { width: 1fr; border: solid $accent; padding: 0 1; }
    #song-list { height: 1fr; }
    #status { height: auto; }
    .field { height: 3; }
    .field Label { width: 16; content-align: left middle; }
    .field Input { width: 1fr; }
    .difficulty { height: 3; }
    .difficulty Checkbox { width: 20; }
    .difficulty Input { width: 1fr; }
    .section-title { text-style: bold; margin-top: 1; }
    #log { height: 7; border: solid $surface-lighten-2; }
    """
    BINDINGS = [("ctrl+f", "focus_search", "搜索"), ("ctrl+e", "start_export", "导出"),
                ("ctrl+q", "quit", "退出")]

    def __init__(self, apk: Path | None, output: Path, cli: Path):
        super().__init__()
        self.initial_apk = apk
        self.initial_output = output
        self.cli = cli
        self.current_apk: Path | None = None
        self.songs: dict = {}
        self.meta: dict = {}
        self.previous_songs: dict | None = None
        self.added_songs: set[str] = set()
        self.removed_songs: set[str] = set()
        self.matches: list[str] = []
        self.song: str | None = None
        self.exporting = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="paths"):
            with Horizontal(classes="field"):
                yield Label("APK 路径")
                yield Input(value=str(self.initial_apk) if self.initial_apk else "", id="apk",
                            placeholder="选择或输入 APK 路径，例如 D:\\games\\com.phi40.apk")
                yield Button("加载 APK", id="load")
            with Horizontal(classes="field"):
                yield Label("输出目录")
                yield Input(value=display_path(self.initial_output), id="output",
                            placeholder="输出文件夹")
                yield Button("开始导出", id="export")
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Input(id="search", placeholder="搜索歌曲 ID / 曲名 / 作者 (Ctrl+F)")
                yield Static("先加载 APK", id="status")
                yield OptionList(id="song-list")
            with VerticalScroll(id="right"):
                yield Static("歌曲 / 选择难度", classes="section-title", id="song-title")
                for diff in DIFFICULTIES:
                    with Horizontal(id=f"row_{diff}", classes="difficulty"):
                        yield Checkbox(diff, id=f"check_{diff}")
                        yield Input(id=f"level_{diff}", placeholder="Level / 等级")
                        yield Input(value="Unknown", id=f"charter_{diff}", placeholder="Charter / 谱师")
                yield Static("歌曲信息", classes="section-title")
                for label, field in (("Name 曲名", "name"), ("Composer 曲师", "composer"),
                                     ("Illustrator 画师", "illustrator")):
                    with Horizontal(classes="field"):
                        yield Label(label)
                        yield Input(id=field)
                yield Static("info.txt 的 Song/Chart/Picture 自动填写实际文件名。")
        yield RichLog(id="log", wrap=True, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        for diff in DIFFICULTIES:
            self.query_one(f"#row_{diff}", Horizontal).display = False
        self.query_one("#export", Button).disabled = True
        if self.initial_apk:
            self.load_apk()
        else:
            self.query_one("#status", Static).update("填写 APK 路径后点「加载 APK」")
            self._log("请先填写 APK 路径，再点「加载 APK」")

    def _log(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)

    def load_apk(self) -> None:
        if self.exporting:
            return
        raw = self.query_one("#apk", Input).value.strip().strip('"')
        if not raw:
            self._log("APK 路径为空")
            self.notify("请先填写 APK 路径", severity="warning", timeout=6)
            return
        path = Path(raw).expanduser().resolve()
        try:
            with zipfile.ZipFile(path) as archive:
                songs = Catalog(json.loads(archive.read(CATALOG))).songs(archive)
                meta = read_song_meta(archive, sorted(songs))
            if not songs:
                raise ExtractionError("APK contains no songs")
        except (OSError, ValueError, KeyError, IndexError, zipfile.BadZipFile, ExtractionError) as exc:
            self._log(f"加载失败: {exc}")
            self.notify(f"加载失败: {exc}", severity="error", timeout=8)
            return
        previous = self.songs if self.current_apk is not None else None
        self.previous_songs = previous
        self.added_songs = set(songs).difference(previous) if previous is not None else set()
        self.removed_songs = set(previous).difference(songs) if previous is not None else set()
        self.current_apk, self.songs = path, songs
        self.meta = meta
        self.song = None  # Force the details panel to refresh even if the same song remains selected.
        self._log(f"已加载 {path.name}: {len(songs)} 首曲目；"
                  f"新增 {len(self.added_songs)}，移除 {len(self.removed_songs)}；"
                  f"自带元数据 {len(meta)} 首")
        self.refresh_search()

    def refresh_search(self) -> None:
        query = self.query_one("#search", Input).value.strip().casefold()
        # Include removed songs in search results for this comparison only.
        candidates = set(self.songs) | self.removed_songs
        self.matches = [name for name in sorted(candidates) if query in name.casefold()]
        listing = self.query_one("#song-list", OptionList)
        listing.clear_options()
        for name in self.matches:
            if name in self.added_songs:
                prompt = Text(f"＋ {name}", style="bold green")
            elif name in self.removed_songs:
                prompt = Text(f"－ {name}", style="bold red")
            else:
                prompt = Text(f"   {name}")
            listing.add_option(Option(prompt))
        legend = (f"匹配 {len(self.matches)} / {len(candidates)}；"
                  f"对比前一个apk＋新增 {len(self.added_songs)}，－移除 {len(self.removed_songs)}")
        self.query_one("#status", Static).update(legend)
        if self.matches:
            listing.highlighted = 0
            self.set_song(self.matches[0])
        else:
            self.song = None
            self.query_one("#song-title", Static).update("没有匹配结果")
            self.query_one("#export", Button).disabled = True
            for diff in DIFFICULTIES:
                self.query_one(f"#row_{diff}", Horizontal).display = False

    def set_song(self, song: str) -> None:
        if song == self.song:
            return
        self.song = song
        present = song in self.songs
        resources = self.songs[song] if present else self.previous_songs[song]
        available = available_difficulties(resources)
        old_available = (set(available_difficulties(self.previous_songs[song]))
                         if self.previous_songs is not None and song in self.previous_songs else set())
        new_available = (set(available_difficulties(self.songs[song])) if present else set())
        self.query_one("#export", Button).disabled = self.exporting or not present
        info, per_diff = song_metadata(self.meta.get(song), song, DIFFICULTIES)
        title = f"{song}  ·  {', '.join(per_diff[d].level for d in available)}"
        if not present:
            title += "  [已移除，不能从当前 APK 导出]"
        self.query_one("#song-title", Static).update(title)
        self.query_one("#name", Input).value = info.name
        self.query_one("#composer", Input).value = info.composer
        self.query_one("#illustrator", Input).value = info.illustrator
        for diff in DIFFICULTIES:
            self.query_one(f"#row_{diff}", Horizontal).display = diff in old_available | new_available
            check = self.query_one(f"#check_{diff}", Checkbox)
            added = self.previous_songs is not None and diff in new_available - old_available
            removed = self.previous_songs is not None and diff in old_available - new_available
            check.label = Text(("＋ " if added else "－ " if removed else "   ") + diff,
                               style="bold green" if added else "bold red" if removed else "")
            check.value = present and diff == ("IN" if "IN" in new_available else available[0])
            check.disabled = not present or diff not in new_available
            self.query_one(f"#level_{diff}", Input).value = per_diff[diff].level
            self.query_one(f"#charter_{diff}", Input).value = per_diff[diff].charter

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "song-list" and event.option_index < len(self.matches):
            self.set_song(self.matches[event.option_index])

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self.refresh_search()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "load":
            self.load_apk()
        elif event.button.id == "export":
            self.action_start_export()

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_start_export(self) -> None:
        if self.exporting:
            return
        try:
            if not self.song or self.current_apk is None:
                raise ExtractionError("请先加载 APK 并选择一首歌")
            if self.song not in self.songs:
                raise ExtractionError("这首歌已从当前 APK 移除，不能导出")
            if Path(self.query_one("#apk", Input).value.strip().strip('"')).resolve() != self.current_apk:
                raise ExtractionError("APK 路径已修改，请先点「加载 APK」")
            if not self.cli.is_file():
                raise ExtractionError(f"AssetStudioModCLI 不存在: {self.cli}（用 --asmc 指定路径）")
            selected = [d for d in DIFFICULTIES if d in available_difficulties(self.songs[self.song])
                        and self.query_one(f"#check_{d}", Checkbox).value]
            if not selected:
                raise ExtractionError("至少勾选一个难度")
            info = SongInfo(*(self.query_one(f"#{field}", Input).value.strip()
                              for field in ("name", "composer", "illustrator")))
            per_diff = {d: DifficultyInfo(self.query_one(f"#level_{d}", Input).value.strip(),
                                          self.query_one(f"#charter_{d}", Input).value.strip())
                        for d in selected}
            target = Path(self.query_one("#output", Input).value.strip().strip('"')).expanduser()
            if not str(target).strip() or not self.query_one("#output", Input).value.strip():
                raise ExtractionError("请填写输出目录")
            song, apk = self.song, self.current_apk
            self.exporting = True
            self.query_one("#export", Button).disabled = True
            self.query_one("#load", Button).disabled = True
            self._log(f"准备导出 {song}: {', '.join(selected)}")
            self.export_worker(apk, song, selected, target.resolve(), info, per_diff)
        except (OSError, ExtractionError) as exc:
            self._log(f"错误: {exc}")
            self.notify(str(exc), severity="error", timeout=8)

    @work(thread=True, exclusive=True)
    def export_worker(self, apk: Path, song: str, selected: list[str], output: Path,
                      info: SongInfo, per_diff: dict[str, DifficultyInfo]) -> None:
        try:
            with zipfile.ZipFile(apk) as archive:
                paths = extract_difficulties(
                    archive, song, self.songs[song], selected, output, self.cli, info, per_diff,
                    progress=lambda message: self.call_from_thread(self._log, message))
            self.call_from_thread(self._finish, f"成功: {len(paths)} 个难度，输出到 {paths[0].parent}", False)
        except Exception as exc:
            self.call_from_thread(self._finish, f"导出失败: {exc}", True)

    def _finish(self, message: str, error: bool) -> None:
        self.exporting = False
        self.query_one("#export", Button).disabled = False
        self.query_one("#load", Button).disabled = False
        self._log(message)
        self.notify(message, severity="error" if error else "information", timeout=10)


def display_path(value: Path) -> str:
    """Show the output directory relative to the working directory when it is inside it.

    Anything else stays absolute: rewriting an absolute path into "./..." would silently
    resolve it against the working directory instead of the directory the user meant.
    """
    try:
        relative = value.relative_to(Path.cwd())
    except ValueError:
        return str(value)
    text = relative.as_posix()
    return "./" if text in (".", "") else f"./{text}/"


def main() -> None:
    parser = argparse.ArgumentParser(description="PhiExtract 歌曲资源提取（终端界面）")
    parser.add_argument("apk", type=Path, nargs="?", help="源 APK；留空则在界面中填写")
    parser.add_argument("-o", "--output", type=Path, default=Path.cwd() / "extracted",
                        help="输出目录（默认 ./extracted/）")
    parser.add_argument("--asmc", type=Path, default=default_cli(), help="AssetStudioModCLI.exe 路径")
    args = parser.parse_args()
    SongTUI(args.apk, args.output, args.asmc.resolve()).run()


if __name__ == "__main__":
    main()
