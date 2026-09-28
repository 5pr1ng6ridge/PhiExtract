#!/usr/bin/env python3
"""Extract Phigros song charts, music and illustrations from an APK.

Song metadata (display name, composer, illustrator, chart constant, charter) is read
from the game's own song table in the boot scene; `--meta` dumps it as JSON.

Needs Python 3.9+ (standard library) and the bundled asmc/AssetStudioModCLI.exe (.NET 6+).
Run `python extract_songs.py --help`.
"""
from __future__ import annotations

import argparse
import base64
from collections import defaultdict
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from typing import Callable, Mapping, Sequence
import zipfile

CATALOG = "assets/aa/catalog.json"
LEVEL0 = "assets/bin/Data/level0"
BUNDLE_ROOT = "assets/aa/Android/"
# Shipped with the project (Windows x64, .NET 6+). Ship the whole asmc/ folder.
BUNDLED_CLI = Path(__file__).resolve().parent / "asmc/AssetStudioModCLI.exe"
DEFAULT_CLI = BUNDLED_CLI


def default_cli() -> Path:
    """Prefer the bundled CLI, then the PhiTool Next install layout."""
    if BUNDLED_CLI.is_file():
        return BUNDLED_CLI
    raise ExtractionError("Failed to find AssetStudioModCLI")
ENTRY = struct.Struct("<7i")
U32 = struct.Struct("<I")
INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ExtractionError(Exception):
    pass


@dataclass(frozen=True)
class Resource:
    address: str
    bundle_path: str

    @property
    def name(self) -> str:
        return self.address.rsplit("/", 1)[-1]


class Catalog:
    def __init__(self, data: dict):
        keys = base64.b64decode(data["m_KeyDataString"], validate=True)
        buckets = base64.b64decode(data["m_BucketDataString"], validate=True)
        entries = base64.b64decode(data["m_EntryDataString"], validate=True)
        self.ids = data["m_InternalIds"]
        providers = data["m_ProviderIds"]
        self.asset_providers = {
            i for i, name in enumerate(providers) if name.endswith(".BundledAssetProvider")
        }
        self.bundle_providers = {
            i for i, name in enumerate(providers)
            if name.endswith(".AssetBundleProvider") or name.endswith(".C9SecretAssetBundleProvider")
        }
        if not self.asset_providers or not self.bundle_providers:
            raise ExtractionError("Catalog lacks a supported bundled-asset/bundle provider")
        if len(entries) < 4 or (len(entries) - 4) % ENTRY.size:
            raise ExtractionError("Invalid catalog entry table size")
        count = U32.unpack_from(entries)[0]
        if count != (len(entries) - 4) // ENTRY.size:
            raise ExtractionError("Invalid catalog entry count")
        self.entries = [ENTRY.unpack_from(entries, 4 + i * ENTRY.size) for i in range(count)]
        self.keys: list[tuple[str | int, list[int]]] = []
        if len(buckets) < 4:
            raise ExtractionError("Truncated bucket table")
        n = U32.unpack_from(buckets)[0]
        pos = 4
        for _ in range(n):
            if pos + 8 > len(buckets):
                raise ExtractionError("Truncated bucket entry")
            key_offset, count = struct.unpack_from("<II", buckets, pos)
            pos += 8
            if pos + count * 4 > len(buckets):
                raise ExtractionError("Truncated bucket values")
            refs = list(struct.unpack_from(f"<{count}I", buckets, pos)) if count else []
            pos += count * 4
            if key_offset + 5 > len(keys):
                raise ExtractionError("Bad catalog key offset")
            kind = keys[key_offset]
            length = U32.unpack_from(keys, key_offset + 1)[0]
            if kind in (0, 1):
                end = key_offset + 5 + length
                if end > len(keys):
                    raise ExtractionError("Truncated catalog key")
                text = keys[key_offset + 5:end].decode("utf-8" if kind == 0 else "utf-16-le")
            elif kind == 4:
                text = struct.unpack_from("<i", keys, key_offset + 1)[0]
            else:
                raise ExtractionError(f"Unsupported catalog key type {kind}")
            self.keys.append((text, refs))
        if pos != len(buckets):
            raise ExtractionError("Trailing bytes in bucket table")

    def get_bundle(self, resource_entry: int) -> tuple[str, str]:
        """Return (APK internal path, bundle key), never assume key == filename."""
        record = self.entries[resource_entry]
        dep = record[2]
        if dep < 0 or dep >= len(self.keys):
            raise ExtractionError(f"Invalid dependency key {dep} at entry {resource_entry}")
        legacy, refs = self.keys[dep]
        if not refs:
            raise ExtractionError(f"No bundle entry for dependency key {dep}")
        bundle_records = [self.entries[i] for i in refs if i < len(self.entries)
                          and self.entries[i][1] in self.bundle_providers]
        if len(bundle_records) != 1:
            raise ExtractionError(f"Expected one bundle entry for dependency {dep}; found {len(bundle_records)}")
        idx = bundle_records[0][0]
        if idx < 0 or idx >= len(self.ids):
            raise ExtractionError(f"Bad internal id index {idx}")
        path = self.ids[idx]
        if not isinstance(path, str) or not path.endswith(".bundle"):
            raise ExtractionError(f"Not a bundle internal id: {path!r}")
        filename = path.rsplit("/", 1)[-1]
        if filename in ("", ".", "..") or "\\" in filename:
            raise ExtractionError(f"Unsafe bundle filename: {filename!r}")
        return BUNDLE_ROOT + filename, str(legacy)

    def songs(self, archive: zipfile.ZipFile) -> dict[str, list[Resource]]:
        available = set(archive.namelist())
        songs: dict[str, dict[str, Resource]] = defaultdict(dict)
        for key, refs in self.keys:
            if not isinstance(key, str) or not key.startswith("Assets/Tracks/"):
                continue
            parts = key.split("/", 3)
            if len(parts) != 4 or not parts[2] or not parts[3]:
                continue
            song = parts[2]
            for i in refs:
                if i >= len(self.entries) or self.entries[i][1] not in self.asset_providers:
                    continue
                internal, legacy = self.get_bundle(i)
                if internal not in available:
                    # Older APKs stored bundles under their catalog key instead of internal id.
                    alternative = BUNDLE_ROOT + legacy.rsplit("/", 1)[-1]
                    if alternative not in available:
                        raise ExtractionError(f"Missing bundle for {key}: {internal} (fallback {alternative})")
                    internal = alternative
                previous = songs[song].get(key)
                if previous is not None and previous.bundle_path != internal:
                    raise ExtractionError(f"Conflicting bundles for {key}")
                songs[song][key] = Resource(key, internal)
        # Chapter covers also live under Assets/Tracks, but are not songs.
        return {song: sorted(resources.values(), key=lambda r: r.address)
                for song, resources in songs.items()
                if any(r.name.startswith("Chart_") and r.name.endswith(".json") for r in resources.values())
                and any(r.name.endswith(".wav") for r in resources.values())}


def safe_name(name: str) -> str:
    result = INVALID_NAME.sub("_", name).strip(" .")
    if not result or result.upper().split(".")[0] in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10))
    }:
        raise ExtractionError(f"Unsafe song name: {name!r}")
    return result


def expected_files(resources: list[Resource]) -> dict[str, str]:
    """Map AssetStudio base names to desired filename; reject ambiguous collisions."""
    expected = {}
    for resource in resources:
        name = resource.name
        if name.endswith(".c9Locked"):
            # An encrypted resource is intentionally NOT treated as an exported illustration.
            continue
        if name != safe_name(name):
            raise ExtractionError(f"Unsafe resource filename: {name!r}")
        if not name.lower().endswith((".json", ".jpg", ".jpeg", ".png", ".wav")):
            raise ExtractionError(f"Unsupported track asset: {resource.address}")
        base = Path(name).stem
        if base in expected and expected[base] != name:
            raise ExtractionError(f"Ambiguous export name {base!r} in song")
        expected[base] = name
    return expected


def find_exported(export_dir: Path, name: str) -> Path:
    """AssetStudio emits extensionless TextAssets and JPEG images as .jpeg."""
    base = Path(name).stem
    suffix = Path(name).suffix.lower()
    if suffix == ".json":
        candidates = [base, name, base + ".txt"]
    elif suffix in (".jpg", ".jpeg"):
        candidates = [base + ".jpeg", base + ".jpg"]
    else:
        candidates = [name]
    found = [export_dir / c for c in candidates if (export_dir / c).is_file()]
    if len(found) != 1 or found[0].stat().st_size == 0:
        raise ExtractionError(f"Expected exactly one non-empty export for {name!r}; found: {found}")
    return found[0]


def extract_song(archive: zipfile.ZipFile, song: str, resources: list[Resource],
                 output: Path, cli: Path) -> int:
    # Never let AssetStudio write into final output directly: verify first, then commit.
    destination = output / safe_name(song)
    if destination.exists():
        raise ExtractionError(f"Output already exists (not overwriting): {destination}")
    names = expected_files(resources)
    if not names:
        raise ExtractionError(f"No supported song assets for {song}")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".phiextract_", dir=output) as temp:
        root = Path(temp)
        bundles_dir = root / "bundles"
        export_dir = root / "export"
        bundles_dir.mkdir()
        export_dir.mkdir()
        bundles = sorted({resource.bundle_path for resource in resources
                          if not resource.name.endswith(".c9Locked")})
        for path in bundles:
            # Paths came from the catalog but always validate before filesystem writes.
            filename = path.rsplit("/", 1)[-1]
            with archive.open(path) as src, (bundles_dir / filename).open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
        print(f"  {song}: {len(bundles)} bundles -> AssetStudio", flush=True)
        cmd = [str(cli), str(bundles_dir), "-m", "export", "-t", "tex2d,textAsset,audio",
               "-g", "none", "-o", str(export_dir), "--image-format", "jpg",
               "--audio-format", "wav", "--log-level", "warning"]
        result = subprocess.run(cmd, cwd=str(cli.parent), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="replace")
        if result.returncode:
            raise ExtractionError(f"AssetStudio failed (exit {result.returncode}):\n{result.stdout[-4000:]}")
        planned = {name: find_exported(export_dir, name) for name in names.values()}
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(temp) / "ready"
        staging.mkdir()
        for name, source in planned.items():
            shutil.move(str(source), str(staging / name))
        # Same-volume atomic rename: never expose a partly populated song directory.
        if destination.exists():
            raise ExtractionError(f"Output appeared during export: {destination}")
        os.rename(staging, destination)
        print(f"  -> {destination} ({len(planned)} files)", flush=True)
        return len(planned)


@dataclass(frozen=True)
class SongInfo:
    name: str
    composer: str
    illustrator: str


@dataclass(frozen=True)
class DifficultyInfo:
    level: str
    charter: str


def available_difficulties(resources: list[Resource]) -> list[str]:
    charts = {r.name[6:-5] for r in resources
              if r.name.startswith("Chart_") and r.name.endswith(".json")}
    priority = ("EZ", "HD", "IN", "AT")
    return [d for d in priority if d in charts] + sorted(charts.difference(priority))


@dataclass(frozen=True)
class SongMeta:
    """The game's own metadata for one song (see read_song_meta).

    `difficulties`, `levels` and `charters` are parallel per-difficulty arrays;
    a level of 0 means the song has no chart for that difficulty.
    """

    songs_id: str
    name: str                       # display title (songsTitle)
    search_name: str                # normalized name the game searches on (songsName)
    composer: str
    illustrator: str
    difficulties: tuple[str, ...]
    levels: tuple[float, ...]
    charters: tuple[str, ...]
    preview: tuple[float, float]

    def _index(self, difficulty: str) -> int | None:
        # "EZ_Error" style bonus charts share the base difficulty's metadata.
        wanted = difficulty.removesuffix("_Error")
        for index, label in enumerate(self.difficulties):
            if label.removesuffix("_Error") == wanted:
                return index
        return None

    def level_text(self, difficulty: str) -> str:
        """"IN 14.4" style level text shown by the game, else the bare difficulty."""
        index = self._index(difficulty)
        if index is None or index >= len(self.levels) or self.levels[index] <= 0:
            return difficulty
        return f"{difficulty} {self.levels[index]:g}"

    def charter_text(self, difficulty: str) -> str:
        index = self._index(difficulty)
        if index is not None and index < len(self.charters) and self.charters[index].strip():
            return self.charters[index].strip()
        return "Unknown"


class _SongTable:
    """Cursor over the song table embedded in the boot scene's binary payload."""

    def __init__(self, blob: bytes):
        self.blob = blob

    def _count(self, offset: int, limit: int, label: str) -> int:
        count = struct.unpack_from("<i", self.blob, offset)[0]
        if not 0 <= count <= limit:
            raise ValueError(f"bad {label} length {count} at {offset}")
        return count

    def text(self, offset: int) -> tuple[str, int]:
        length = self._count(offset, 300, "string")
        value = self.blob[offset + 4:offset + 4 + length].decode("utf-8")
        end = offset + 4 + length
        return value, end + (-end) % 4

    def floats(self, offset: int) -> tuple[tuple[float, ...], int]:
        count = self._count(offset, 16, "float array")
        values = struct.unpack_from(f"<{count}f", self.blob, offset + 4) if count else ()
        return values, offset + 4 + count * 4

    def texts(self, offset: int) -> tuple[tuple[str, ...], int]:
        count = self._count(offset, 16, "string array")
        offset += 4
        values = []
        for _ in range(count):
            value, offset = self.text(offset)
            values.append(value)
        return tuple(values), offset

    def offsets(self, song_id: str):
        key = song_id.encode("utf-8")
        position = 0
        while (position := self.blob.find(key, position)) >= 0:
            start = position - 4
            if start >= 0 and struct.unpack_from("<i", self.blob, start)[0] == len(key):
                yield start
            position += 1

    def parse(self, start: int) -> dict:
        song_id, offset = self.text(start)
        name, offset = self.text(offset)
        title, offset = self.text(offset)
        offset += 4                                   # unknown int32, always 0 so far
        levels, offset = self.floats(offset)
        illustrator, offset = self.text(offset)
        charters, offset = self.texts(offset)
        composer, offset = self.text(offset)
        difficulties, offset = self.texts(offset)
        preview = tuple(struct.unpack_from("<2f", self.blob, offset))
        return dict(song_id=song_id, name=name, title=title, levels=levels,
                    illustrator=illustrator, charters=charters, composer=composer,
                    difficulties=difficulties, preview=preview)


def read_song_meta(archive: zipfile.ZipFile, song_ids: Sequence[str]) -> dict[str, SongMeta]:
    """Read the game's own song table: name, composer, illustrator, level, charter.

    Unity serializes one record per song into the boot scene without field names, so
    the layout is decoded positionally:

        string songsId, string songsName, string songsTitle, int32,
        float[] levels, string illustrator, string[] charter, string composer,
        string[] difficulty, float previewTime, float previewEndTime, ...

    Verified against com.phi320.apk and com.phi40.apk: levels[i] > 0 matches the
    exported Chart_<difficulty>.json set for every song that has a record. Songs
    without a record (the Random.* placeholders) are simply absent from the result.
    """
    try:
        table = _SongTable(archive.read(LEVEL0))
    except KeyError:
        return {}
    found: dict[str, SongMeta] = {}
    for song_id in song_ids:
        best: dict | None = None
        for start in table.offsets(song_id):
            try:
                record = table.parse(start)
            except (ValueError, UnicodeDecodeError, struct.error):
                continue
            if record["song_id"] != song_id or not record["difficulties"]:
                continue
            score = (len(record["difficulties"]), len(record["levels"]),
                     bool(record["composer"]), bool(record["illustrator"]))
            if best is None or score > best["score"]:
                record["score"] = score
                best = record
        if best is not None:
            found[song_id] = SongMeta(
                songs_id=song_id, name=best["title"] or best["name"],
                search_name=best["name"], composer=best["composer"],
                illustrator=best["illustrator"], difficulties=best["difficulties"],
                levels=best["levels"], charters=best["charters"],
                preview=best["preview"])
    return found


def song_metadata(meta: SongMeta | None, song: str, difficulties: Sequence[str]
                  ) -> tuple[SongInfo, dict[str, DifficultyInfo]]:
    """info.txt values for one song: the APK's table if present, else the song ID."""
    if meta is None:
        parts = song.rsplit(".", 2)
        name = parts[0] if len(parts) == 3 else song
        composer = parts[1] if len(parts) == 3 else "Unknown"
        return (SongInfo(name, composer, "Unknown"),
                {d: DifficultyInfo(d, "Unknown") for d in difficulties})
    return (SongInfo(meta.name, meta.composer or "Unknown", meta.illustrator or "Unknown"),
            {d: DifficultyInfo(meta.level_text(d), meta.charter_text(d)) for d in difficulties})


def render_info(song: SongInfo, difficulty: DifficultyInfo, chart: str,
                music: str, picture: str) -> str:
    values = (song.name, song.composer, song.illustrator, difficulty.level,
              difficulty.charter, chart, music, picture)
    if any(not v.strip() or "\n" in v or "\r" in v or "\x00" in v for v in values):
        raise ExtractionError("info.txt fields must be non-empty single-line text")
    # RPE documents the artist field as "Illustration"; most community tools read
    # "Illustrator", so write both keys and let the reader pick.
    return (f"#\nName: {song.name}\nSong: {music}\nChart: {chart}\n"
            f"Picture: {picture}\nLevel: {difficulty.level}\n"
            f"Composer: {song.composer}\nIllustration: {song.illustrator}\n"
            f"Illustrator: {song.illustrator}\nCharter: {difficulty.charter}\n")


def extract_difficulties(archive: zipfile.ZipFile, song: str, resources: list[Resource],
                         difficulties: Sequence[str], output: Path, cli: Path,
                         info: SongInfo, per_difficulty: Mapping[str, DifficultyInfo],
                         progress: Callable[[str], None] = print) -> list[Path]:
    """Export selected charts and shared media once; commit one directory per difficulty.

    Existing difficulty directories are never overwritten. A failed conversion leaves
    no completed output; a rare failure during commit retains any already committed
    directories and reports the failure instead of claiming total success.
    """
    available = available_difficulties(resources)
    selected = list(dict.fromkeys(difficulties))
    if not selected or any(d not in available or d not in per_difficulty for d in selected):
        raise ExtractionError(f"Choose available difficulties: {', '.join(available)}")
    lookup = {r.name: r for r in resources}
    picture = next((p for p in ("Illustration.jpg", "IllustrationLowRes.jpg", "IllustrationBlur.jpg")
                    if p in lookup), None)
    if picture is None:
        raise ExtractionError(f"{song} has no illustration; cannot make a valid info.txt")
    plans = {}
    for diff in selected:
        chart = f"Chart_{diff}.json"
        music = ("music_IN.wav" if diff == "IN" and "music_IN.wav" in lookup else "music.wav")
        if music not in lookup:
            raise ExtractionError(f"No music for {song} / {diff}")
        text = render_info(info, per_difficulty[diff], chart, music, picture)
        destination = output / safe_name(song) / safe_name(diff)
        if destination.exists():
            raise ExtractionError(f"Output already exists (not overwriting): {destination}")
        plans[diff] = (chart, music, text, destination)
    chosen = {name for chart, music, _, _ in plans.values() for name in (chart, music, picture)}
    selected_resources = [lookup[name] for name in chosen]
    expected_files(selected_resources)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".phiextract_", dir=output) as temp:
        root = Path(temp)
        bundles_dir, export_dir = root / "bundles", root / "export"
        bundles_dir.mkdir()
        export_dir.mkdir()
        bundles = sorted({r.bundle_path for r in selected_resources})
        progress(f"{song}: copying {len(bundles)} bundle(s)")
        for path in bundles:
            filename = path.rsplit("/", 1)[-1]
            with archive.open(path) as src, (bundles_dir / filename).open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
        progress("Converting assets with AssetStudioModCLI...")
        cmd = [str(cli), str(bundles_dir), "-m", "export", "-t", "tex2d,textAsset,audio",
               "-g", "none", "-o", str(export_dir), "--image-format", "jpg",
               "--audio-format", "wav", "--log-level", "warning"]
        result = subprocess.run(cmd, cwd=str(cli.parent), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="replace")
        if result.returncode:
            raise ExtractionError(f"AssetStudio failed (exit {result.returncode}):\n{result.stdout[-4000:]}")
        files = {name: find_exported(export_dir, name) for name in chosen}
        for diff, (chart, music, text, _) in plans.items():
            folder = root / "ready" / diff
            folder.mkdir(parents=True)
            for name in (chart, music, picture):
                shutil.copyfile(files[name], folder / name)
            (folder / "info.txt").write_text(text, encoding="utf-8", newline="\n")
        completed = []
        for diff, (_, _, _, destination) in plans.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise ExtractionError(f"Output appeared during export: {destination}")
            os.rename(root / "ready" / diff, destination)
            completed.append(destination)
            progress(f"Saved: {destination}")
        return completed


def select_songs(songs: dict[str, list[Resource]], selectors: list[str], all_songs: bool) -> list[str]:
    if all_songs:
        return sorted(songs)
    chosen = set()
    for token in selectors:
        matches = [name for name in songs if token.casefold() in name.casefold()]
        if token in songs:
            matches = [token]
        if not matches:
            raise ExtractionError(f"No song matches {token!r}; try --list")
        if len(matches) != 1:
            raise ExtractionError(f"Ambiguous selector {token!r}: {len(matches)} songs; use the full song ID")
        chosen.add(matches[0])
    return sorted(chosen)


def expand_path(value: Path) -> Path:
    """Expand ~ in paths typed by the user (PowerShell/cmd do not expand it for us)."""
    return Path(os.path.expanduser(str(value)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  python extract_songs.py com.phi40.apk --list\n"
               "  python extract_songs.py com.phi40.apk -s 000AinSophAur -o output\n"
               "  python extract_songs.py com.phi40.apk -s Igallta --info -o output\n"
               "  python extract_songs.py com.phi40.apk --meta -o meta.json\n"
               "  python extract_songs.py com.phi320.apk --all -o output_320")
    parser.add_argument("apk", type=Path, help="source game APK")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="list available track IDs")
    mode.add_argument("--meta", action="store_true", help="dump the game's song metadata table as JSON (to stdout, or to -o FILE)")
    mode.add_argument("-s", "--song", action="append", metavar="ID", help="exact or unique substring of song ID; repeatable")
    mode.add_argument("--all", action="store_true", help="export all tracks (requires substantial disk space)")
    parser.add_argument("-o", "--output", type=Path, help="output directory (required for export)")
    parser.add_argument("--info", action="store_true",
                        help="export one directory per difficulty with info.txt filled from the APK")
    parser.add_argument("--asmc", type=Path, default=default_cli(), help="AssetStudioModCLI.exe path")
    args = parser.parse_args(argv)
    apk = expand_path(args.apk)
    cli = expand_path(args.asmc)
    if not args.list and not args.meta and args.output is None:
        parser.error("-o/--output is required for extraction")
    if not args.list and not args.meta and not cli.is_file():
        parser.error(f"AssetStudioModCLI not found: {cli}")
    try:
        with zipfile.ZipFile(apk) as archive:
            catalog = Catalog(json.loads(archive.read(CATALOG)))
            songs = catalog.songs(archive)
            if args.list:
                for name in sorted(songs):
                    print(name)
                print(f"{len(songs)} tracks total", file=sys.stderr)
                return 0
            if args.meta:
                table = read_song_meta(archive, sorted(songs))
                records = {name: asdict(meta) for name, meta in sorted(table.items())}
                if args.output:
                    # Writing the file ourselves avoids the console/PowerShell encoding
                    # (cmd pipes mangle UTF-8, PowerShell 5.1 redirects to UTF-16).
                    destination = expand_path(args.output)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
                    print(f"Wrote {destination} ({len(table)}/{len(songs)} tracks)", file=sys.stderr)
                else:
                    # stdout: stay ASCII so any console/pipe encoding still yields valid JSON
                    print(json.dumps(records, indent=2))
                    print(f"{len(table)}/{len(songs)} tracks have game metadata", file=sys.stderr)
                return 0
            selected = select_songs(songs, args.song or [], args.all)
            output = expand_path(args.output).resolve()
            if not selected:
                raise ExtractionError("No tracks selected")
            print(f"Selected {len(selected)} track(s) from {apk}", flush=True)
            table = read_song_meta(archive, selected) if args.info else {}
            if args.info:
                print(f"Game metadata for {len(table)}/{len(selected)} track(s)", flush=True)
            successes = 0
            failures = []
            for song in selected:
                try:
                    if args.info:
                        difficulties = available_difficulties(songs[song])
                        info, per_diff = song_metadata(table.get(song), song, difficulties)
                        extract_difficulties(archive, song, songs[song], difficulties, output,
                                             cli.resolve(), info, per_diff)
                    else:
                        extract_song(archive, song, songs[song], output, cli.resolve())
                    successes += 1
                except (ExtractionError, OSError, zipfile.BadZipFile) as exc:
                    print(f"ERROR {song}: {exc}", file=sys.stderr, flush=True)
                    failures.append(song)
            print(f"Completed {successes}/{len(selected)} track(s); {len(failures)} failed", flush=True)
            return 1 if failures else 0
    except (ExtractionError, OSError, zipfile.BadZipFile, KeyError, ValueError,
            IndexError, UnicodeError, struct.error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
