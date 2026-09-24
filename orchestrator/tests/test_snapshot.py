"""Immutable source snapshot invariants (§2.1, §19.4)."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from orchestrator import snapshot


def _git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True)


def _commit_all(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", message)
    return subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["AI_BRIEF_STORE_DIR"] = str(Path(self._tmp.name) / "store")
        self.repo = str(Path(self._tmp.name) / "repo")
        Path(self.repo).mkdir()
        _git(self.repo, "init", "-q")
        (Path(self.repo) / "a.mdx").write_text("# A\nalpha\n")
        (Path(self.repo) / "sub").mkdir()
        (Path(self.repo) / "sub" / "b.mdx").write_text("# B\nbeta\n")
        (Path(self.repo) / "README.md").write_text("# readme\n")
        (Path(self.repo) / "notes.txt").write_text("ignore me\n")
        self.commit1 = _commit_all(self.repo, "c1")
        self.spec = {"name": "docs", "repo": self.repo, "include": ["**/*.mdx"],
                     "exclude": ["**/README.md"]}

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("AI_BRIEF_STORE_DIR", None)

    def test_admits_only_matching_files(self):
        resolved = snapshot.resolve_source(self.spec)
        paths = [f["path"] for f in resolved.manifest["files"]]
        self.assertEqual(paths, ["a.mdx", "sub/b.mdx"])  # README + txt excluded

    def test_snapshot_id_is_stable(self):
        a = snapshot.resolve_source(self.spec)
        b = snapshot.resolve_source(self.spec)
        self.assertEqual(a.snapshot_id, b.snapshot_id)

    def test_pinned_commit_survives_branch_moving(self):
        before = snapshot.resolve_source(self.spec)
        # Move the branch head: change a.mdx and commit again.
        (Path(self.repo) / "a.mdx").write_text("# A\nCHANGED\n")
        self.commit2 = _commit_all(self.repo, "c2")

        # Resolving HEAD now differs...
        head_now = snapshot.resolve_source(self.spec)
        self.assertNotEqual(before.snapshot_id, head_now.snapshot_id)

        # ...but pinning the original commit reproduces the original snapshot.
        pinned = snapshot.resolve_source({**self.spec, "ref": self.commit1})
        self.assertEqual(pinned.snapshot_id, before.snapshot_id)
        self.assertEqual(pinned.commit, self.commit1)

    def test_dirty_worktree_rejected_by_default(self):
        (Path(self.repo) / "a.mdx").write_text("uncommitted edit\n")
        with self.assertRaises(snapshot.DirtyWorkTreeError):
            snapshot.resolve_source(self.spec)
        # allow_dirty snapshots the committed tree anyway.
        resolved = snapshot.resolve_source(self.spec, allow_dirty=True)
        self.assertTrue(resolved.manifest["dirty_worktree"])

    def test_materialize_writes_admitted_files(self):
        snap = snapshot.resolve([self.spec])
        root = Path(snapshot.materialize(snap))
        self.assertTrue((root / "docs" / "a.mdx").exists())
        self.assertTrue((root / "docs" / "sub" / "b.mdx").exists())
        self.assertFalse((root / "docs" / "README.md").exists())
        # Idempotent second call.
        self.assertEqual(snapshot.materialize(snap), str(root))


if __name__ == "__main__":
    unittest.main()
