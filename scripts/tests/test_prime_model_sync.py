import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
import tempfile
import unittest
from pathlib import Path
from overlord_py.prime_model_sync import sync_host_prime_models

class PrimeModelSyncTests(unittest.TestCase):
    def test_copies_host_models_into_persisted_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp) / "state" / "prime-agent-data"
            (home / ".prime" / "agent").mkdir(parents=True)
            (home / ".prime" / "agent" / "models.json").write_text('{"providers": {}}')
            result = sync_host_prime_models(home=home, prime_agent_data=target)
            self.assertTrue(result.copied)
            self.assertEqual((target / "models.json").read_text(), '{"providers": {}}')

    def test_skips_when_host_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = sync_host_prime_models(
                home=Path(tmp) / "home",
                prime_agent_data=Path(tmp) / "state",
            )
            self.assertFalse(result.copied)

    def test_noop_when_already_up_to_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp) / "state" / "prime-agent-data"
            src = home / ".prime" / "agent"
            src.mkdir(parents=True)
            (src / "models.json").write_text('{"a": 1}')
            target.mkdir(parents=True)
            (target / "models.json").write_text('{"a": 1}')
            result = sync_host_prime_models(home=home, prime_agent_data=target)
            self.assertFalse(result.copied)
            self.assertEqual(result.reason, "already up to date")

    def test_overwrites_stale_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp) / "state" / "prime-agent-data"
            src = home / ".prime" / "agent"
            src.mkdir(parents=True)
            (src / "models.json").write_text("new")
            target.mkdir(parents=True)
            (target / "models.json").write_text("old")
            result = sync_host_prime_models(home=home, prime_agent_data=target)
            self.assertTrue(result.copied)
            self.assertEqual((target / "models.json").read_text(), "new")

if __name__ == "__main__":
    unittest.main()
