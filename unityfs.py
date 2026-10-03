"""Narrow UnityFS v7/v8 reader for a single embedded file, not a Unity object parser.

Only overlapping blocks are decompressed; unrelated scene/texture data stays packed.
Supports raw and LZ4/LZ4HC blocks used by Phigros's packed boot scene.
"""
from __future__ import annotations

import io
import struct
from typing import BinaryIO

MAX_INFO = 8 * 1024 * 1024
MAX_BLOCK = 64 * 1024 * 1024
MAX_FILE = 32 * 1024 * 1024


class UnityFSError(ValueError):
    pass


def _read(stream: BinaryIO, count: int) -> bytes:
    data = stream.read(count)
    if len(data) != count:
        raise UnityFSError("Truncated UnityFS data")
    return data


def _cstring(stream: BinaryIO) -> str:
    data = bytearray()
    for _ in range(4096):
        byte = _read(stream, 1)
        if byte == b"\0":
            try:
                return data.decode("utf-8")
            except UnicodeError as exc:
                raise UnityFSError("Invalid UnityFS string") from exc
        data.extend(byte)
    raise UnityFSError("UnityFS string exceeds limit")


def _unpack(stream: BinaryIO, fmt: str) -> tuple:
    return struct.unpack(fmt, _read(stream, struct.calcsize(fmt)))


def _decompress(data: bytes, size: int, flags: int) -> bytes:
    method = flags & 0x3F
    if method == 0:
        plain = data
    elif method in (2, 3):
        try:
            from lz4.block import decompress
        except ImportError as exc:
            raise UnityFSError("Packed metadata needs lz4; python -m pip install -r requirements.txt") from exc
        try:
            plain = decompress(data, uncompressed_size=size)
        except Exception as exc:
            raise UnityFSError("Invalid UnityFS LZ4 block") from exc
    else:
        raise UnityFSError(f"Unsupported UnityFS compression: {method}")
    if len(plain) != size:
        raise UnityFSError("UnityFS decompressed size mismatch")
    return plain


def read_file(stream: BinaryIO, file_size: int, name: str) -> bytes:
    """Read a unique basename from a seekable UnityFS stream without writing files.

    Validate table sizes and compressed/uncompressed ranges before allocation/seeking.
    Declared sizes are capped because this reader only needs small metadata scenes.
    Unrecognized/encrypted formats raise, rather than masquerading as absent metadata.
    """
    if _cstring(stream) != "UnityFS":
        raise UnityFSError("Packed metadata is not UnityFS")
    version, = _unpack(stream, ">I")
    if version not in (7, 8):
        raise UnityFSError(f"Unsupported UnityFS version: {version}")
    _cstring(stream)  # Unity player version
    engine = _cstring(stream)
    size, compressed_info, raw_info, flags = _unpack(stream, ">QIII")
    if size != file_size:
        raise UnityFSError("UnityFS declared file size mismatch")
    if flags & ~0x3FF or not flags & 0x40:
        raise UnityFSError(f"Unsupported UnityFS flags: {flags:#x}")
    # The 0x200 bit meant encryption in old engines, padding in modern engines.
    try:
        engine_parts = tuple(int(p) for p in engine.split(".")[:2])
    except ValueError as exc:
        raise UnityFSError(f"Unrecognized Unity engine version: {engine}") from exc
    if flags & 0x200 and engine_parts < (2022, 1):
        raise UnityFSError("Unsupported old UnityFS encryption/padding flags")
    if not 0 < compressed_info <= MAX_INFO or not 0 < raw_info <= MAX_INFO:
        raise UnityFSError("UnityFS block table exceeds size limit")
    start = (stream.tell() + 15) & ~15
    table_offset = size - compressed_info if flags & 0x80 else start
    if table_offset < start or table_offset + compressed_info > size:
        raise UnityFSError("UnityFS block table outside file")
    stream.seek(table_offset)
    table = io.BytesIO(_decompress(_read(stream, compressed_info), raw_info, flags))
    _read(table, 16)  # Uncompressed data hash
    count, = _unpack(table, ">I")
    if count > (raw_info - table.tell()) // 10:
        raise UnityFSError("Invalid UnityFS block count")
    blocks = []
    for _ in range(count):
        raw, packed, block_flags = _unpack(table, ">IIH")
        if not 0 < raw <= MAX_BLOCK or not 0 < packed <= MAX_BLOCK:
            raise UnityFSError("UnityFS block exceeds size limit")
        if block_flags & ~0x7F or block_flags & 0x3F not in (0, 2, 3):
            raise UnityFSError(f"Unsupported UnityFS block flags: {block_flags:#x}")
        blocks.append((raw, packed, block_flags))
    nodes, = _unpack(table, ">I")
    if nodes > (raw_info - table.tell()) // 21:
        raise UnityFSError("Invalid UnityFS node count")
    total_raw = sum(raw for raw, _, _ in blocks)
    targets = []
    for _ in range(nodes):
        offset, length, _ = _unpack(table, ">qqI")
        path = _cstring(table)
        if offset < 0 or length < 0 or offset + length > total_raw:
            raise UnityFSError("UnityFS node outside decompressed data")
        if path.replace("\\", "/").rsplit("/", 1)[-1] == name:
            targets.append((offset, length))
    if table.tell() != raw_info:
        raise UnityFSError("Trailing UnityFS block table bytes")
    if len(targets) != 1:
        raise UnityFSError(f"Expected one UnityFS {name!r} file; found {len(targets)}")
    target_start, target_size = targets[0]
    if not 0 < target_size <= MAX_FILE:
        raise UnityFSError("UnityFS metadata file exceeds size limit")
    data_start = start if flags & 0x80 else start + compressed_info
    if flags & 0x200:
        data_start = (data_start + 15) & ~15
    data_end = table_offset if flags & 0x80 else size
    if data_start + sum(packed for _, packed, _ in blocks) > data_end:
        raise UnityFSError("UnityFS blocks outside file")
    target_end = target_start + target_size
    raw_offset = 0
    packed_offset = data_start
    result = bytearray()
    for raw, packed, block_flags in blocks:
        block_end = raw_offset + raw
        if raw_offset < target_end and block_end > target_start:
            stream.seek(packed_offset)
            block = _decompress(_read(stream, packed), raw, block_flags)
            result.extend(block[max(0, target_start - raw_offset):min(raw, target_end - raw_offset)])
        raw_offset = block_end
        packed_offset += packed
        if raw_offset >= target_end:
            break
    if len(result) != target_size:
        raise UnityFSError("Incomplete UnityFS metadata file")
    return bytes(result)
