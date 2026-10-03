"""APK-independent tests of cover selection, incomplete exports and optional ZIPs."""
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import extract_songs as es


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'output'
        self.song = 'Fixture.Artist.0'
        self.info = es.SongInfo('曲名: Test', 'Artist', 'Illustrator')
        self.resources = []
        self.exported = {}
        self.payload = io.BytesIO()
        self.warnings = []
        self.log = []

    def prepare(self, names, absent_bundles=(), absent_exports=()):
        self.resources = [es.Resource(f'Assets/Tracks/{self.song}/{name}',
                                     f'assets/aa/Android/{i}.bundle')
                          for i, name in enumerate(names)]
        with ZipFile(self.payload, 'w') as archive:
            for resource in self.resources:
                if resource.name not in absent_bundles:
                    archive.writestr(resource.bundle_path, b'fixture bundle')
                if resource.name not in absent_exports:
                    stem = Path(resource.name).stem
                    ext = Path(resource.name).suffix
                    export_name = stem if ext == '.json' else stem + '.jpeg' if ext == '.jpg' else resource.name
                    self.exported[export_name] = resource.name.encode()
        self.payload.seek(0)

    def converter(self, cmd, **kwargs):
        directory = Path(cmd[cmd.index('-o') + 1])
        for name, content in self.exported.items():
            (directory / name).write_bytes(content)
        return subprocess.CompletedProcess(cmd, 0, stdout='')

    def export(self, diffs=('IN',), make_zip=False, info=None, per_diff=None):
        with ZipFile(self.payload) as archive, patch('extract_songs.subprocess.run', self.converter):
            return es.extract_difficulties(
                archive, self.song, self.resources, diffs, self.output,
                self.root / 'asmc.exe', info or self.info,
                per_diff if per_diff is not None else {d: es.DifficultyInfo(d, 'Charter') for d in diffs},
                progress=self.log.append, warning=self.warnings.append, make_zip=make_zip)

    def test_correct_cover_per_difficulty_and_zip_contents(self):
        names = ['music.wav', 'Illustration.jpg']
        for diff in ('EZ', 'HD', 'IN', 'AT'):
            names += [f'Chart_{diff}.json', f'Illustration_{diff}.jpg',
                      f'IllustrationBlur_{diff}.jpg', f'IllustrationLowRes_{diff}.jpg']
        self.prepare(names)
        paths = self.export(('EZ', 'HD', 'IN', 'AT'), make_zip=True)
        for folder in paths:
            info = (folder / 'info.txt').read_text(encoding='utf-8')
            self.assertIn(f'Picture: Illustration_{folder.name}.jpg', info)
            self.assertEqual(len(list(folder.glob('*.jpg'))), 13)
            package_path = self.output / f'曲名_ Test_{folder.name}.zip'
            with ZipFile(package_path) as package:
                self.assertIsNone(package.testzip())
                self.assertEqual(set(package.namelist()), {p.name for p in folder.iterdir()})
                self.assertEqual(package.read('info.txt'), (folder / 'info.txt').read_bytes())
                self.assertTrue(all('/' not in n for n in package.namelist()))
        self.assertEqual(self.warnings, [])

    def test_zip_off_preserves_existing_behavior(self):
        self.prepare(['Chart_IN.json', 'music.wav', 'Illustration.jpg'])
        folder = self.export()[0]
        self.assertTrue((folder / 'info.txt').is_file())
        self.assertFalse(list(self.output.glob('*.zip')))

    def test_missing_media_exports_chart_with_warning_and_zip_without_info(self):
        self.prepare(['Chart_IN.json'])
        folder = self.export(make_zip=True)[0]
        self.assertTrue((folder / 'Chart_IN.json').is_file())
        self.assertFalse((folder / 'info.txt').exists())
        self.assertTrue(any('不生成 info.txt' in w for w in self.warnings))
        with ZipFile(next(self.output.glob('*.zip'))) as package:
            self.assertEqual(package.namelist(), ['Chart_IN.json'])

    def test_empty_metadata_does_not_prevent_export(self):
        self.prepare(['Chart_IN.json', 'music.wav', 'Illustration.jpg'])
        folder = self.export(info=es.SongInfo('', 'Artist', ''))[0]
        self.assertTrue((folder / 'Chart_IN.json').exists())
        self.assertFalse((folder / 'info.txt').exists())
        self.assertEqual(len(self.warnings), 1)

    def test_missing_difficulty_metadata_does_not_prevent_export(self):
        self.prepare(['Chart_IN.json', 'music.wav', 'Illustration.jpg'])
        folder = self.export(per_diff={})[0]
        self.assertFalse((folder / 'info.txt').exists())
        self.assertIn('难度信息', self.warnings[0])

    def test_absent_optional_bundle_or_export_is_recoverable(self):
        for kind in ('bundle', 'export'):
            with self.subTest(kind=kind):
                self.payload = io.BytesIO()
                self.output = self.root / kind
                args = {'absent_bundles': ['Illustration.jpg']} if kind == 'bundle' else {'absent_exports': ['Illustration.jpg']}
                self.exported = {}
                self.prepare(['Chart_IN.json', 'music.wav', 'Illustration.jpg'], **args)
                folder = self.export()[0]
                self.assertTrue((folder / 'music.wav').exists())
                self.assertFalse((folder / 'info.txt').exists())

    def test_missing_chart_is_a_failure(self):
        self.prepare(['Chart_IN.json', 'music.wav'], absent_bundles=['Chart_IN.json'])
        with self.assertRaisesRegex(es.ExtractionError, 'Missing chart'):
            self.export()
        self.assertFalse((self.output / self.song / 'IN').exists())

    def test_converter_failure_stays_a_failure(self):
        self.prepare(['Chart_IN.json', 'music.wav', 'Illustration.jpg'])
        with ZipFile(self.payload) as archive, patch('extract_songs.subprocess.run',
                  return_value=subprocess.CompletedProcess([], 7, stdout='failed')):
            with self.assertRaisesRegex(es.ExtractionError, 'AssetStudio failed'):
                es.extract_difficulties(archive, self.song, self.resources, ['IN'], self.output,
                                        self.root / 'asmc.exe', self.info,
                                        {'IN': es.DifficultyInfo('IN', 'Charter')})
        self.assertFalse((self.output / self.song / 'IN').exists())

    def test_zip_collision_fails_without_overwrite_or_partial_directory(self):
        self.prepare(['Chart_IN.json', 'music.wav', 'Illustration.jpg'])
        self.output.mkdir()
        path = self.output / es.zip_filename(self.info.name, self.song, 'IN')
        path.write_bytes(b'keep me')
        with self.assertRaisesRegex(es.ExtractionError, 'ZIP already exists'):
            self.export(make_zip=True)
        self.assertEqual(path.read_bytes(), b'keep me')
        self.assertFalse((self.output / self.song / 'IN').exists())

    def test_no_borrowing_other_difficulty_cover(self):
        self.prepare(['Chart_HD.json', 'music.wav', 'Illustration_IN.jpg'])
        folder = self.export(('HD',))[0]
        self.assertFalse((folder / 'info.txt').exists())
        self.assertTrue((folder / 'Illustration_IN.jpg').exists())

    def test_select_cover_fallbacks(self):
        self.assertEqual(es.picture_for_difficulty({'Illustration_IN.jpg', 'Illustration.jpg'}, 'IN'), 'Illustration_IN.jpg')
        self.assertEqual(es.picture_for_difficulty({'IllustrationBlur_IN.jpg', 'Illustration.jpg'}, 'IN'), 'IllustrationBlur_IN.jpg')
        self.assertEqual(es.picture_for_difficulty({'Illustration_IN.jpg'}, 'IN_Error'), 'Illustration_IN.jpg')
        self.assertEqual(es.picture_for_difficulty({'IllustrationBlur.jpg'}, 'C9'), 'IllustrationBlur.jpg')
        self.assertIsNone(es.picture_for_difficulty({'Illustration_AT.jpg'}, 'IN'))
        self.assertEqual(es.zip_filename('', self.song, 'IN'), self.song + '_IN.zip')


if __name__ == '__main__':
    unittest.main()
