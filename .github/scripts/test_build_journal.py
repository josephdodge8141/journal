"""Tests for the journal generator."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - tests invoke only the local Git executable.
import tempfile
import unittest
from pathlib import Path
from typing import Any

from build_journal import JournalError, build, title_from_folder


class JournalBuilderTest(unittest.TestCase):
    """Exercise generation against a real temporary Git history."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.journal = self.root / "journal"
        self.output = self.root / "output"
        self.journal.mkdir()
        self.output.mkdir()
        self.ideas_file = self.output / "ideas.json"
        self.tags_file = self.output / "tags.json"
        self._git("init", "-b", "main")
        self._git("config", "user.name", "Journal Test")
        self._git("config", "user.email", "journal@example.com")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _git(self, *arguments: str, date: str | None = None) -> None:
        environment = os.environ.copy()
        if date:
            environment["GIT_AUTHOR_DATE"] = date
            environment["GIT_COMMITTER_DATE"] = date
        subprocess.run(  # nosec B603 B607 - fixed Git command in an isolated test repo.
            ["git", "-C", str(self.root), *arguments],
            check=True,
            capture_output=True,
            env=environment,
        )

    def _write_entry(self, name: str, body: str, tags: list[str]) -> None:
        entry = self.journal / name
        entry.mkdir(exist_ok=True)
        (entry / "main.md").write_text(body, encoding="utf-8")
        (entry / "tags.json").write_text(
            json.dumps(tags, indent=2) + "\n", encoding="utf-8"
        )

    def _commit(self, message: str, date: str) -> None:
        self._git("add", "-A", "journal")
        self._git("commit", "-m", message, date=date)

    def test_build_preserves_dates_positions_and_no_op_files(self) -> None:
        self._write_entry("first-idea", "Original body.", ["thought", "idea"])
        self._commit("Add first idea", "2027-01-01T10:00:00-0700")

        self._write_entry("first-idea", "Updated **body**.", ["thought"])
        self._write_entry("second_idea", "Second body.", ["project"])
        self._commit("Update journal", "2027-02-02T11:30:00-0700")

        existing = [
            {
                "id": "first-idea",
                "title": "Old title",
                "body": "<p>Old body.</p>",
                "tags": ["idea"],
                "date": "2000-01-01",
            },
            {
                "id": "deleted-idea",
                "title": "Deleted Idea",
                "body": "<p>Delete me.</p>",
                "tags": ["idea"],
                "date": "2000-01-01",
            },
            {"title": "Legacy idea"},
        ]
        self.ideas_file.write_text(json.dumps(existing), encoding="utf-8")
        self.tags_file.write_text('["old"]', encoding="utf-8")

        summary = build(self.journal, self.root, self.ideas_file, self.tags_file)
        ideas = json.loads(self.ideas_file.read_text(encoding="utf-8"))

        self.assertEqual([idea["id"] for idea in ideas], ["first-idea", "second_idea"])
        self.assertEqual(ideas[0]["date"], "2027-01-01")
        self.assertEqual(ideas[1]["date"], "2027-02-02")
        self.assertEqual(ideas[0]["body"], "<p>Updated <strong>body</strong>.</p>")
        self.assertEqual(
            json.loads(self.tags_file.read_text(encoding="utf-8")),
            ["project", "thought"],
        )
        self.assertTrue(summary["ideas_changed"])
        self.assertTrue(summary["tags_changed"])

        ideas_bytes = self.ideas_file.read_bytes()
        tags_bytes = self.tags_file.read_bytes()
        no_op_summary = build(self.journal, self.root, self.ideas_file, self.tags_file)
        self.assertFalse(no_op_summary["ideas_changed"])
        self.assertFalse(no_op_summary["tags_changed"])
        self.assertEqual(self.ideas_file.read_bytes(), ideas_bytes)
        self.assertEqual(self.tags_file.read_bytes(), tags_bytes)

    def test_deleted_folder_removes_generated_entry(self) -> None:
        self._write_entry("temporary-idea", "Temporary.", ["idea"])
        self._commit("Add temporary idea", "2027-03-01T08:00:00+0000")
        self.ideas_file.write_text("[]\n", encoding="utf-8")
        self.tags_file.write_text("[]\n", encoding="utf-8")
        build(self.journal, self.root, self.ideas_file, self.tags_file)

        entry = self.journal / "temporary-idea"
        (entry / "main.md").unlink()
        (entry / "tags.json").unlink()
        entry.rmdir()
        self._git("add", "journal")
        self._git("commit", "-m", "Delete temporary idea")

        build(self.journal, self.root, self.ideas_file, self.tags_file)
        self.assertEqual(read_json(self.ideas_file), [])
        self.assertEqual(read_json(self.tags_file), [])

    def test_rename_preserves_original_date(self) -> None:
        self._write_entry("old-name", "Body.", ["idea"])
        self._commit("Add old name", "2027-04-01T08:00:00+0000")
        self._git("mv", "journal/old-name", "journal/new_name")
        self._commit("Rename idea", "2027-05-01T08:00:00+0000")
        self.ideas_file.write_text("[]\n", encoding="utf-8")
        self.tags_file.write_text("[]\n", encoding="utf-8")

        build(self.journal, self.root, self.ideas_file, self.tags_file)
        ideas = read_json(self.ideas_file)

        self.assertEqual(ideas[0]["id"], "new_name")
        self.assertEqual(ideas[0]["date"], "2027-04-01")

    def test_recreated_folder_uses_new_incarnation_date(self) -> None:
        self._write_entry("returning-idea", "First body.", ["idea"])
        self._commit("Add idea", "2027-06-01T08:00:00+0000")
        entry = self.journal / "returning-idea"
        (entry / "main.md").unlink()
        (entry / "tags.json").unlink()
        entry.rmdir()
        self._commit("Delete idea", "2027-07-01T08:00:00+0000")
        self._write_entry("returning-idea", "Second body.", ["idea"])
        self._commit("Recreate idea", "2027-08-01T08:00:00+0000")
        self.ideas_file.write_text("[]\n", encoding="utf-8")
        self.tags_file.write_text("[]\n", encoding="utf-8")

        build(self.journal, self.root, self.ideas_file, self.tags_file)
        ideas = read_json(self.ideas_file)

        self.assertEqual(ideas[0]["date"], "2027-08-01")

    def test_rejects_extra_entry_files(self) -> None:
        self._write_entry("invalid-idea", "Body.", [])
        (self.journal / "invalid-idea" / "extra.txt").write_text(
            "extra", encoding="utf-8"
        )
        self._git("add", "journal")
        self._git("commit", "-m", "Add invalid idea")
        self.ideas_file.write_text("[]\n", encoding="utf-8")
        self.tags_file.write_text("[]\n", encoding="utf-8")

        with self.assertRaises(JournalError):
            build(self.journal, self.root, self.ideas_file, self.tags_file)

    def test_title_uses_folder_name(self) -> None:
        self.assertEqual(
            title_from_folder("an-interesting_idea"), "An Interesting Idea"
        )


def read_json(path: Path) -> Any:
    """Read JSON in assertions without importing an implementation helper."""
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
