import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from lanelib import selftest  # noqa: E402

LOG = """# Changelog

## 2.2.0 — 2026-12-01

- New option `foo`.

## 2.1.1 — 2026-11-27

- Hint off by default.

## 2.1.0 — 2026-11-27

- Hooks.
"""


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


class UpdateCase(unittest.TestCase):
    def test_changes_since_keeps_only_newer_sections(self):
        out = selftest.changes_since(LOG, "2.1.0")
        self.assertIn("## 2.2.0", out)
        self.assertIn("New option `foo`.", out)
        self.assertIn("## 2.1.1", out)
        self.assertNotIn("## 2.1.0", out)
        self.assertEqual(selftest.changes_since(LOG, "2.2.0"), "")
        self.assertEqual(selftest.changes_since(LOG, "garbage"), "")

    def test_update_prints_what_changed(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        origin, clone = tmp / "origin", tmp / "clone"
        origin.mkdir()
        git(origin, "init", "-q", "-b", "main")
        (origin / "VERSION").write_text("2.1.0\n")
        (origin / "CHANGELOG.md").write_text(LOG.split("## 2.1.1")[0].replace("## 2.2.0 — 2026-12-01\n\n- New option `foo`.\n\n", "") + "## 2.1.0 — 2026-11-27\n\n- Hooks.\n")
        git(origin, "add", "-A")
        git(origin, "commit", "-q", "-m", "2.1.0")
        subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
        (origin / "VERSION").write_text("2.2.0\n")
        (origin / "CHANGELOG.md").write_text(LOG)
        git(origin, "commit", "-q", "-am", "2.2.0")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = selftest.update(clone)
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("lanes updated 2.1.0 -> 2.2.0", out)
        self.assertIn("New option `foo`.", out)
        self.assertNotIn("- Hooks.", out)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(selftest.update(clone), 0)
        self.assertIn("lanes is up to date (2.2.0)", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
