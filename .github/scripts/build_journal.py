#!/usr/bin/env python3
"""Build landing-page JSON from the journal directory."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import markdown

ENTRY_NAME = re.compile(r"^[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*$")
TAG_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
EXPECTED_FILES = {"main.md", "tags.json"}


class JournalError(ValueError):
    """Raised when journal source data is invalid."""


def title_from_folder(folder_name: str) -> str:
    """Convert an entry folder slug into its display title."""
    words = re.sub(r"[-_]+", " ", folder_name).strip()
    if not words:
        raise JournalError("Journal folder names must contain a title")
    return words.title()


def read_json(path: Path) -> Any:
    """Read JSON and report its path when parsing fails."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise JournalError(f"Unable to read {path}: {error}") from error


def read_tags(path: Path) -> list[str]:
    """Read and validate an entry's ordered tag list."""
    tags = read_json(path)
    if not isinstance(tags, list):
        raise JournalError(f"{path} must contain a JSON array")

    seen: set[str] = set()
    validated: list[str] = []
    for tag in tags:
        if not isinstance(tag, str) or not TAG_NAME.fullmatch(tag):
            raise JournalError(
                f"Tags in {path} must be lowercase words separated by dashes"
            )
        if tag in seen:
            raise JournalError(f"Duplicate tag {tag!r} in {path}")
        seen.add(tag)
        validated.append(tag)
    return validated


def original_commit_date(repo_root: Path, source_file: Path) -> str:
    """Return the calendar date of the commit that first added a source file."""
    try:
        relative_path = source_file.resolve().relative_to(repo_root.resolve())
    except ValueError as error:
        raise JournalError(f"{source_file} is outside {repo_root}") from error

    command = [
        "git",
        "-C",
        str(repo_root),
        "log",
        "--follow",
        "--diff-filter=A",
        "--format=%cI",
        "--",
        relative_path.as_posix(),
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    dates = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or not dates:
        detail = result.stderr.strip() or "file has not been committed"
        raise JournalError(
            f"Unable to find original commit for {source_file}: {detail}"
        )
    return dates[0][:10]


def render_markdown(path: Path) -> str:
    """Render trusted journal Markdown into stable HTML."""
    source = path.read_text(encoding="utf-8").strip()
    return markdown.markdown(source, extensions=["extra", "sane_lists"]).strip()


def scan_journal(journal_dir: Path, repo_root: Path) -> list[dict[str, Any]]:
    """Validate and render all journal entry folders."""
    if not journal_dir.is_dir():
        raise JournalError(f"Journal directory does not exist: {journal_dir}")

    entries: list[dict[str, Any]] = []
    for entry_dir in sorted(journal_dir.iterdir(), key=lambda path: path.name):
        if entry_dir.name == ".gitkeep":
            continue
        if not entry_dir.is_dir():
            raise JournalError(f"Only entry folders are allowed in {journal_dir}")
        if not ENTRY_NAME.fullmatch(entry_dir.name):
            raise JournalError(f"Invalid journal folder name: {entry_dir.name}")

        files = {path.name for path in entry_dir.iterdir() if path.is_file()}
        if files != EXPECTED_FILES:
            raise JournalError(
                f"{entry_dir} must contain exactly main.md and tags.json"
            )

        main_file = entry_dir / "main.md"
        entries.append(
            {
                "id": entry_dir.name,
                "title": title_from_folder(entry_dir.name),
                "body": render_markdown(main_file),
                "tags": read_tags(entry_dir / "tags.json"),
                "date": original_commit_date(repo_root, main_file),
            }
        )
    return entries


def load_existing_ideas(path: Path) -> list[dict[str, Any]]:
    """Read the existing generated ideas list."""
    ideas = read_json(path)
    if not isinstance(ideas, list) or not all(isinstance(item, dict) for item in ideas):
        raise JournalError(f"{path} must contain a JSON array of objects")
    identifiers = [item["id"] for item in ideas if isinstance(item.get("id"), str)]
    if len(identifiers) != len(set(identifiers)):
        raise JournalError(f"{path} contains duplicate idea IDs")
    return ideas


def merge_ideas(
    existing: list[dict[str, Any]], scanned: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Update existing positions, remove deleted IDs, then append new IDs."""
    pending = {entry["id"]: entry for entry in scanned}
    merged: list[dict[str, Any]] = []

    for idea in existing:
        identifier = idea.get("id")
        if identifier in pending:
            merged.append(pending.pop(identifier))

    merged.extend(entry for entry in scanned if entry["id"] in pending)
    return merged


def write_json_if_changed(path: Path, value: Any) -> bool:
    """Write formatted JSON only when its semantic value changed."""
    if path.exists() and read_json(path) == value:
        return False
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return True


def build(
    journal_dir: Path, repo_root: Path, ideas_file: Path, tags_file: Path
) -> dict[str, Any]:
    """Build ideas and tags, returning a compact operation summary."""
    scanned = scan_journal(journal_dir, repo_root)
    existing = load_existing_ideas(ideas_file)
    ideas = merge_ideas(existing, scanned)
    tags = sorted({tag for idea in scanned for tag in idea["tags"]})

    ideas_changed = write_json_if_changed(ideas_file, ideas)
    tags_changed = write_json_if_changed(tags_file, tags)
    return {
        "ideas": len(ideas),
        "tags": len(tags),
        "ideas_changed": ideas_changed,
        "tags_changed": tags_changed,
    }


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal-dir", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--ideas-file", type=Path, required=True)
    parser.add_argument("--tags-file", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    """Run the journal build command."""
    args = parse_args()
    try:
        summary = build(
            args.journal_dir, args.repo_root, args.ideas_file, args.tags_file
        )
    except JournalError as error:
        raise SystemExit(f"error: {error}") from error
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
