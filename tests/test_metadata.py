"""Packed/standalone scene regression tests, independent of real APKs."""
import io
import struct
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import extract_songs as es
import unityfs


def text(value):
    data = value.encode('utf-8')
    return struct.pack('<I', len(data)) + data + b'\0' * (-len(data) % 4)


def texts(values):
    return struct.pack('<I', len(values)) + b''.join(text(v) for v in values)


def song_table():
    return (b'\0' * 16 + text('Fixture.Artist.0') + text('Search name') + text('歌曲标题')
            + struct.pack('<iI2f', 0, 2, 3.5, 14.9) + text('画师')
            + texts(['EZ Charter', 'IN Charter']) + text('Composer')
            + texts(['EZ', 'IN']) + struct.pack('<2f', 10.0, 25.0))


def bundle(scene, method=0, table_method=None, at_end=False, padded=True, nodes=None):
    if table_method is None:
        table_method = method

    def compress(data, kind):
        if kind == 0:
            return data
        from lz4.block import compress
        return compress(data, store_size=False)

    raw_data = b'unrelated prefix' * 5 + scene + b'unrelated suffix' * 5
    prefix_size = len(b'unrelated prefix' * 5)
    blocks = [raw_data[i:i + 97] for i in range(0, len(raw_data), 97)]
    packed = [compress(b, method) for b in blocks]
    table = b'\0' * 16 + struct.pack('>I', len(blocks))
    table += b''.join(struct.pack('>IIH', len(r), len(p), method)
                      for r, p in zip(blocks, packed))
    if nodes is None:
        nodes = [(prefix_size, len(scene), 'level0')]
    table += struct.pack('>I', len(nodes))
    table += b''.join(struct.pack('>qqI', off, size, 4) + name.encode() + b'\0'
                      for off, size, name in nodes)
    packed_table = compress(table, table_method)
    flags = 0x40 | table_method | (0x80 if at_end else 0) | (0x200 if padded else 0)
    prefix = b'UnityFS\0' + struct.pack('>I', 8) + b'5.x.x\0' + b'2022.3.62f2\0'
    header_size = (len(prefix) + 20 + 15) & ~15
    if at_end:
        body = b''.join(packed) + packed_table
    else:
        pad = b'\0' * (-(header_size + len(packed_table)) % 16) if padded else b''
        body = packed_table + pad + b''.join(packed)
    header = prefix + struct.pack('>QIII', header_size + len(body), len(packed_table), len(table), flags)
    header += b'\0' * (header_size - len(header))
    return header + body


class MetadataTest(unittest.TestCase):
    def read_meta(self, members, warnings=None):
        payload = io.BytesIO()
        with ZipFile(payload, 'w') as z:
            for name, data in members.items():
                z.writestr(name, data)
        payload.seek(0)
        with ZipFile(payload) as z:
            return es.read_song_meta(z, ['Fixture.Artist.0'], warning=warnings)

    def assert_meta(self, meta):
        record = meta['Fixture.Artist.0']
        self.assertEqual(record.name, '歌曲标题')
        self.assertEqual(record.composer, 'Composer')
        self.assertEqual(record.illustrator, '画师')
        self.assertEqual(record.charter_text('IN'), 'IN Charter')
        self.assertEqual(record.level_text('IN'), 'IN 14.9')
        self.assertEqual(record.preview, (10.0, 25.0))

    def test_standalone_source_is_preserved_and_preferred(self):
        warnings = []
        self.assert_meta(self.read_meta({es.LEVEL0: song_table(), es.PACKED_DATA: b'broken'}, warnings.append))
        self.assertEqual(warnings, [])

    def test_raw_packed_table_variants_and_cross_block_scene(self):
        for at_end in (False, True):
            for padded in (False, True):
                with self.subTest(at_end=at_end, padded=padded):
                    warnings = []
                    data = bundle(song_table(), at_end=at_end, padded=padded)
                    self.assert_meta(self.read_meta({es.PACKED_DATA: data}, warnings.append))
                    self.assertEqual(warnings, [])

    def test_lz4_and_lz4hc_packed_scene(self):
        for method in (2, 3):
            for at_end in (False, True):
                with self.subTest(method=method, at_end=at_end):
                    data = bundle(song_table(), method=method, at_end=at_end)
                    self.assert_meta(self.read_meta({es.PACKED_DATA: data}))

    def test_only_overlapping_blocks_are_decompressed(self):
        data = bundle(song_table())
        original = unityfs._decompress
        with patch('unityfs._decompress', wraps=original) as decompress:
            self.assertEqual(unityfs.read_file(io.BytesIO(data), len(data), 'level0'), song_table())
        # One table and three overlapping blocks; prefix/suffix blocks are skipped.
        self.assertEqual(decompress.call_count, 4)

    def test_missing_lz4_warns_but_does_not_prevent_fallback(self):
        data = bundle(song_table(), method=2)
        warnings = []
        with patch.dict('sys.modules', {'lz4.block': None}):
            self.assertEqual(self.read_meta({es.PACKED_DATA: data}, warnings.append), {})
        self.assertEqual(len(warnings), 1)
        self.assertIn('pip install -r requirements.txt', warnings[0])

    def test_absent_or_corrupt_sources_warn(self):
        for members in ({}, {es.PACKED_DATA: b'broken'}, {es.LEVEL0: b'no table'}):
            with self.subTest(members=list(members)):
                warnings = []
                self.assertEqual(self.read_meta(members, warnings.append), {})
                self.assertEqual(len(warnings), 1)

    def test_bad_size_missing_or_ambiguous_node_and_out_of_bounds_are_errors(self):
        scene = song_table()
        for data in (bundle(scene)[:-1],
                     bundle(scene, nodes=[(80, len(scene), 'level1')]),
                     bundle(scene, nodes=[(80, len(scene), 'level0'), (80, len(scene), 'other/level0')]),
                     bundle(scene, nodes=[(-1, len(scene), 'level0')]),
                     bundle(scene, nodes=[(0, 10**9, 'level0')])):
            with self.subTest(size=len(data)):
                with self.assertRaises(unityfs.UnityFSError):
                    unityfs.read_file(io.BytesIO(data), len(data), 'level0')

    def test_oversized_block_table_is_rejected_before_decompression(self):
        data = bytearray(bundle(song_table()))
        prefix = b'UnityFS\0' + struct.pack('>I', 8) + b'5.x.x\0' + b'2022.3.62f2\0'
        struct.pack_into('>I', data, len(prefix) + 12, unityfs.MAX_INFO + 1)
        with patch('unityfs._decompress') as decompress:
            with self.assertRaisesRegex(unityfs.UnityFSError, 'limit'):
                unityfs.read_file(io.BytesIO(data), len(data), 'level0')
        decompress.assert_not_called()


if __name__ == '__main__':
    unittest.main()
