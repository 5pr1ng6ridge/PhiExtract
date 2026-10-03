"""Small, APK-independent checks for the exceptional difficulty mappings."""
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from extract_songs import (APRIL_SP_SONG, C9_PASSWORD, C9_SONG, ExtractionError,
                           Resource, available_difficulties, copy_bundles,
                           expected_files, song_metadata)


def resource(song, name, encrypted=False):
    return Resource(f"Assets/Tracks/{song}/{name}", "assets/aa/Android/test.bundle", encrypted)


class SpecialDifficultiesTest(unittest.TestCase):
    def test_april_sp_is_not_mislabelled_in(self):
        assets = [resource(APRIL_SP_SONG, "Chart_IN.json"),
                  resource(APRIL_SP_SONG, "music.wav")]
        self.assertEqual(available_difficulties(assets), ["SP"])
        song, per_diff = song_metadata(None, APRIL_SP_SONG, ["SP"])
        self.assertEqual(song.name, "Oblivion: PHIN")
        self.assertEqual(per_diff["SP"].level, "SP")

    def test_other_in_and_legacy_are_unchanged(self):
        assets = [resource("Other.0", "Chart_IN.json"),
                  resource("Other.0", "Chart_Legacy.json")]
        self.assertEqual(available_difficulties(assets), ["IN", "Legacy"])

    def test_chapter9_is_separate_and_has_a_picture(self):
        assets = [resource(C9_SONG, "Chart.json", True),
                  resource(C9_SONG, "music.wav", True),
                  resource(C9_SONG, "IllustrationBlur.jpg", True)]
        self.assertEqual(available_difficulties(assets), ["C9"])
        self.assertEqual(set(expected_files(assets)), {"Chart", "music", "IllustrationBlur"})
        song, per_diff = song_metadata(None, C9_SONG, ["C9"])
        self.assertEqual(per_diff["C9"].level, "C9 (incomplete)")
        self.assertIn("unfinished", song.name)

    def test_encrypted_bundle_is_staged_and_bad_padding_rejected(self):
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad

        key = hashlib.sha512(C9_PASSWORD.encode()).digest()
        plain = b"UnityFS\0fixture"
        cipher = AES.new(key[:32], AES.MODE_CBC, key[32:48]).encrypt(pad(plain, 16))
        for contents, should_succeed in [(cipher, True), (cipher[:-1], False)]:
            buffer = io.BytesIO()
            with ZipFile(buffer, "w") as archive:
                archive.writestr("assets/aa/Android/test.bundle", contents)
            buffer.seek(0)
            with ZipFile(buffer) as archive, tempfile.TemporaryDirectory() as temp:
                destination = Path(temp)
                assets = [resource(C9_SONG, "Chart.json", True)]
                if should_succeed:
                    self.assertEqual(copy_bundles(archive, assets, destination), 1)
                    self.assertEqual((destination / "test.bundle").read_bytes(), plain)
                else:
                    with self.assertRaises(ExtractionError):
                        copy_bundles(archive, assets, destination)
                    self.assertFalse((destination / "test.bundle").exists())


if __name__ == "__main__":
    unittest.main()
