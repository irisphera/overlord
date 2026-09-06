import errno
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from overlord_py.prime_model_sync import sync_host_prime_models


class PrimeModelSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.destination = Path(self.tmp.name) / "state" / "prime-agent-data"
        self.source = self.home / ".prime" / "agent" / "models.json"
        self.target = self.destination / "models.json"
        self.source.parent.mkdir(parents=True)
        self.content = json.dumps({
            "defaults": {"contextWindow": 12345},
            "providers": {
                "my-private-provider": {
                    "apiKey": "private-test-credential",
                    "models": [{"id": "my-model", "contextWindow": 12345}],
                    "modelOverrides": {"*": {"reasoning": False}},
                },
                "opencode": {"models": [{"id": "custom-route"}]},
            },
        }, indent=2).encode() + b"\n"
        self.source.write_bytes(self.content)
        self.source.chmod(0o600)

    def sync(self):
        return sync_host_prime_models(home=self.home, prime_agent_data=self.destination)

    def assert_source_unchanged(self, content=None, mode=0o600):
        self.assertEqual(self.source.read_bytes(), self.content if content is None else content)
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), mode)

    def test_seeds_exact_host_bytes_without_removing_custom_providers(self):
        result = self.sync()
        self.assertTrue(result.copied)
        self.assertEqual(self.target.read_bytes(), self.content)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        self.assert_source_unchanged()
        self.assertNotIn("private-test-credential", result.reason)

    def test_preserves_source_access_mode_in_seed(self):
        for mode in (0o400, 0o640):
            with self.subTest(mode=oct(mode)):
                self.source.chmod(mode)
                self.assertTrue(self.sync().copied)
                self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), mode)
                self.assert_source_unchanged(mode=mode)
                self.target.unlink()

    def test_existing_workspace_is_untouched_even_with_invalid_source(self):
        self.destination.mkdir(parents=True)
        self.target.write_bytes(b"workspace customization, even if not valid JSON")
        self.target.chmod(0o400)
        self.source.write_bytes(b"invalid host JSON")
        self.assertFalse(self.sync().copied)
        self.assertEqual(self.target.read_bytes(), b"workspace customization, even if not valid JSON")
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o400)
        self.assert_source_unchanged(content=b"invalid host JSON")

    def test_missing_source_does_not_create_destination(self):
        self.source.unlink()
        self.assertFalse(self.sync().copied)
        self.assertFalse(self.destination.exists())

    def test_invalid_json_or_container_shapes_do_not_create_destination(self):
        invalid_documents = [
            b'{"apiKey": "private-test-credential",',
            b"\xff",
            b"null",
            b"[]",
            b'{"defaults": []}',
            b'{"providers": []}',
            b'{"providers": {"custom": null}}',
            b'{"providers": {"custom": {"models": {}}}}',
            b'{"providers": {"custom": {"models": [null]}}}',
            b'{"providers": {"custom": {"models": [{"id": []}]}}}',
            b'{"providers": {"custom": {"modelOverrides": []}}}',
            b'{"providers": {"custom": {"modelOverrides": {"*": null}}}}',
        ]
        for document in invalid_documents:
            with self.subTest(document=document):
                self.source.write_bytes(document)
                with self.assertRaises(RuntimeError) as error:
                    self.sync()
                self.assertNotIn("private-test-credential", str(error.exception))
                self.assertFalse(self.destination.exists())
                self.assert_source_unchanged(content=document)

    def test_source_symlink_is_rejected_without_touching_referent(self):
        original = self.source.with_name("original.json")
        self.source.rename(original)
        self.source.symlink_to(original)
        with self.assertRaises(RuntimeError):
            self.sync()
        self.assertFalse(self.destination.exists())
        self.assertTrue(self.source.is_symlink())
        self.assertEqual(original.read_bytes(), self.content)
        self.assertEqual(stat.S_IMODE(original.stat().st_mode), 0o600)

    def test_target_symlink_is_rejected_without_touching_referent(self):
        self.destination.mkdir(parents=True)
        self.target.symlink_to(self.source)
        with self.assertRaises(RuntimeError):
            self.sync()
        self.assertTrue(self.target.is_symlink())
        self.assert_source_unchanged()

    def test_symlinked_destination_ancestor_is_rejected(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        self.destination.parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            self.sync()
        self.assertEqual(list(outside.iterdir()), [])
        self.assert_source_unchanged()

    def test_symlinked_source_ancestor_is_rejected(self):
        original = self.source.parent.with_name("original-agent")
        self.source.parent.rename(original)
        self.source.parent.symlink_to(original, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            self.sync()
        self.assertFalse(self.destination.exists())
        self.assert_source_unchanged()

    def test_nonregular_source_is_rejected_without_blocking(self):
        self.source.unlink()
        os.mkfifo(self.source)
        with self.assertRaises(RuntimeError):
            self.sync()
        self.assertFalse(self.destination.exists())
        self.assertTrue(stat.S_ISFIFO(self.source.stat().st_mode))

    def test_failed_publication_leaves_no_partial_target_or_temporary_file(self):
        with patch("overlord_py.prime_model_sync.os.link", side_effect=OSError(errno.EIO, "publication failed")):
            with self.assertRaises(RuntimeError):
                self.sync()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assert_source_unchanged()

    def test_failed_flush_leaves_no_partial_target_or_temporary_file(self):
        with patch("overlord_py.prime_model_sync.os.fsync", side_effect=OSError(errno.ENOSPC, "disk full")):
            with self.assertRaises(RuntimeError):
                self.sync()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assert_source_unchanged()

    def test_atomic_publication_does_not_replace_concurrent_creation(self):
        real_link = os.link

        def race(source, target, **kwargs):
            self.assertFalse(self.target.exists())
            self.target.write_bytes(b"concurrent workspace customization")
            self.target.chmod(0o400)
            return real_link(source, target, **kwargs)

        with patch("overlord_py.prime_model_sync.os.link", side_effect=race):
            self.assertFalse(self.sync().copied)
        self.assertEqual(self.target.read_bytes(), b"concurrent workspace customization")
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o400)
        self.assertEqual(list(self.destination.iterdir()), [self.target])
        self.assert_source_unchanged()


if __name__ == "__main__":
    unittest.main()
