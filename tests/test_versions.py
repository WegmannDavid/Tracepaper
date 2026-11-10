import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tracepaper import versions as V


def run(*args, cwd):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class VersionsTest(unittest.TestCase):
    """A Tracepaper repository on "GitHub" (a bare one), the checkout projects use, and a project."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.origin, self.checkout, self.dev, self.project = (root / n for n in ("origin.git", "checkout", "dev", "project"))
        run("git", "init", "-q", "--bare", "-b", "main", str(self.origin), cwd=root)
        run("git", "clone", "-q", str(self.origin), str(self.dev), cwd=root)
        for c in ("git config user.email t@example.com", "git config user.name T"):
            run(*c.split(), cwd=self.dev)
        self.commit("First version")
        run("git", "clone", "-q", str(self.origin), str(self.checkout), cwd=root)
        self.project.mkdir()
        (self.project / "views.py").write_text("from tracepaper.views import save_scene\n")
        patcher = mock.patch.object(V, "CHECKOUT", self.checkout)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def commit(self, subject):
        """Push a new version to GitHub."""
        run("git", "commit", "-q", "--allow-empty", "-m", subject, cwd=self.dev)
        run("git", "push", "-q", "origin", "HEAD:main", cwd=self.dev)

    def check(self):
        return V.check(self.project)

    def test_projects_without_tracepaper_hear_nothing(self):
        (self.project / "views.py").write_text("import cadquery\n")
        self.assertEqual(self.check(), "")

    def test_a_project_without_a_record_is_asked_to_check(self):
        self.assertIn("has no .tracepaper-version yet", self.check())
        V.adopt(self.project)
        self.assertEqual(self.check(), "")

    def test_a_newer_version_is_offered_until_kept(self):
        V.adopt(self.project)
        self.commit("Sliders that bend parts")
        report = self.check()
        self.assertIn("A newer Tracepaper is on GitHub", report)
        self.assertIn("Sliders that bend parts", report)
        self.assertIn("Mention this to the user", report)
        self.assertIn("keeping", V.adopt(self.project, skip=True))
        self.assertEqual(self.check(), "")  # not offered again
        self.commit("Erasing with the mouse")
        self.assertIn("Erasing with the mouse", self.check())  # but a newer one is

    def test_an_updated_checkout_forces_a_migration(self):
        V.adopt(self.project)
        old = json.loads((self.project / V.RECORD).read_text())["version"]
        self.commit("New drawing format")
        run("git", "pull", "-q", "--ff-only", cwd=self.checkout)  # updated for another project
        report = self.check()
        self.assertIn("has to be migrated", report)
        self.assertIn("New drawing format", report)
        self.assertNotIn("newer Tracepaper is on GitHub", report)
        with self.assertRaises(SystemExit):
            V.adopt(self.project, skip=True)  # the old version is gone
        V.adopt(self.project)
        self.assertNotEqual(json.loads((self.project / V.RECORD).read_text())["version"], old)
        self.assertEqual(self.check(), "")

    def test_the_tracepaper_checkout_itself_hears_nothing(self):
        (self.checkout / "notes.md").write_text("tracepaper view build\n")
        self.assertEqual(V.check(self.checkout), "")


if __name__ == "__main__":
    unittest.main()
