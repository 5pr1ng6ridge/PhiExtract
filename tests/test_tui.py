"""Headless TUI checks without APK or AssetStudio dependencies."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

from textual.widgets import Checkbox, Input

from extract_songs import CATALOG, PACKED_DATA, Resource
from song_tui import SongTUI


class TuiExportTest(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_read_failure_warns_without_blocking_load(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            apk = root / 'fixture.apk'
            with ZipFile(apk, 'w') as archive:
                archive.writestr(CATALOG, '{}')
                archive.writestr(PACKED_DATA, b'broken')
            song = 'Fixture.Artist.0'
            app = SongTUI(None, root / 'output', root / 'asmc.exe')
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app.notify = Mock()
                app.query_one('#apk', Input).value = str(apk)
                with patch('song_tui.Catalog') as catalog:
                    catalog.return_value.songs.return_value = {
                        song: [Resource(f'Assets/Tracks/{song}/Chart_IN.json', 'bundle')]}
                    app.load_apk()
                await pilot.pause()
                self.assertEqual(app.current_apk, apk.resolve())
                self.assertIn(song, app.songs)
                self.assertEqual(app.meta, {})
                self.assertEqual(app.query_one('#illustrator', Input).value, 'Unknown')
                self.assertTrue(any(call.kwargs.get('severity') == 'warning'
                                    for call in app.notify.call_args_list))
                self.assertFalse(any(call.kwargs.get('severity') == 'error'
                                     for call in app.notify.call_args_list))

    async def test_zip_toggle_and_missing_info_warning(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            apk = root / 'fixture.apk'
            with ZipFile(apk, 'w'):
                pass
            cli = root / 'asmc.exe'
            cli.touch()
            app = SongTUI(None, root / 'output', cli)
            song = 'Fixture.Artist.0'
            calls = []

            def exporter(*args, **kwargs):
                calls.append((kwargs['make_zip'], args[6].name))
                kwargs['warning']('缺适用曲绘，不生成 info.txt；谱面已导出。')
                return [root / 'output' / song / 'IN']

            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app.current_apk = apk.resolve()
                app.query_one('#apk', Input).value = str(apk)
                app.songs = {song: [Resource(f'Assets/Tracks/{song}/Chart_IN.json', 'bundle')]}
                app.refresh_search()
                self.assertFalse(app.query_one('#auto-zip', Checkbox).value)
                app.query_one('#auto-zip', Checkbox).value = True
                app.query_one('#name', Input).value = ''
                app.notify = Mock()
                with patch('song_tui.extract_difficulties', exporter):
                    app.action_start_export()
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                self.assertEqual(calls, [(True, '')])
                self.assertFalse(app.exporting)
                self.assertTrue(any(call.kwargs.get('severity') == 'warning'
                                    for call in app.notify.call_args_list))
                self.assertFalse(any(call.kwargs.get('severity') == 'error'
                                     for call in app.notify.call_args_list))


if __name__ == '__main__':
    unittest.main()
