#!/usr/bin/env python3
"""Audit the local workspace-manual documentation.

Checks intentionally stay conservative:
- required operating-manual files exist;
- Markdown local links in the manual docs resolve;
- file-like backtick references in the manual docs resolve when they clearly
  point inside this repository;
- SOURCE_OF_TRUTH primary files exist;
- LOGBOOK has at least one timestamped entry.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_DOCS = [
    "AGENTS.md",
    "docs/CODEX_AGENTS.md",
    "docs/implementation_plan.md",
    "docs/README.md",
    "docs/PROJECT_OVERVIEW.md",
    "docs/ARCHITECTURE.md",
    "docs/CODING_STYLE.md",
    "docs/RUNTIME.md",
    "docs/TESTING.md",
    "docs/SOURCE_OF_TRUTH.md",
    "docs/MAINTENANCE.md",
    "docs/LOGBOOK.md",
    "docs/indexes/README.md",
    "docs/indexes/ROUTES_OR_ENTRYPOINTS.md",
    "docs/indexes/DATA_MODEL.md",
    "docs/indexes/SIDE_EFFECTS.md",
    "docs/indexes/DEPENDENCIES.md",
    "docs/indexes/SECURITY.md",
]

SOURCE_OF_TRUTH_FILES = [
    "AGENTS.md",
    "README.md",
    "SECURITY.md",
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "scripts/run_tests.sh",
    ".github/workflows/tests.yml",
    ".github/workflows/lint.yml",
    ".github/workflows/typecheck.yml",
    "hermes_cli/main.py",
    "hermes_cli/commands.py",
    "hermes_cli/web_server.py",
    "hermes_cli/dashboard_auth/routes.py",
    "hermes_cli/memory_oauth.py",
    "web/src/App.tsx",
    "apps/desktop/src/app/routes.ts",
    "tools/registry.py",
    "model_tools.py",
    "toolsets.py",
    "hermes_state.py",
    "hermes_cli/kanban_db.py",
    "cron/jobs.py",
    "cron/scheduler.py",
    "cron/scheduler_provider.py",
    "docker-compose.yml",
    "Dockerfile",
]

MANUAL_DOCS = [Path(p) for p in REQUIRED_DOCS if p != "AGENTS.md"]

LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
CODE_RE = re.compile(r"`([^`\n]+)`")
LOG_ENTRY_RE = re.compile(r"^### \d{4}-\d{2}-\d{2} \d{2}:\d{2} - .+", re.MULTILINE)


def _clean_link_target(raw: str) -> str:
    target = raw.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    if " " in target:
        target = target.split(" ", 1)[0]
    return target


def _is_external_or_anchor(target: str) -> bool:
    lower = target.lower()
    return (
        lower.startswith(("http://", "https://", "mailto:", "tel:", "app://"))
        or target.startswith("#")
        or target == ""
    )


def _strip_anchor_and_line(target: str) -> str:
    path = target.split("#", 1)[0]
    path = path.split("?", 1)[0]
    # Markdown file links in this repo sometimes include path:line.
    match = re.match(r"^(.*?):\d+$", path)
    if match:
        path = match.group(1)
    return path


def _resolve_doc_target(md_path: Path, target: str) -> Path:
    path_part = _strip_anchor_and_line(target)
    if path_part.startswith("/"):
        return Path(path_part)
    return (REPO_ROOT / md_path.parent / path_part).resolve()


def check_required_files(issues: list[str]) -> None:
    for rel in REQUIRED_DOCS:
        path = REPO_ROOT / rel
        if not path.exists():
            issues.append(f"missing required document: {rel}")


def check_markdown_links(issues: list[str]) -> None:
    for rel in MANUAL_DOCS:
        path = REPO_ROOT / rel
        if not path.exists() or path.suffix.lower() != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        for match in LINK_RE.finditer(text):
            target = _clean_link_target(match.group(1))
            if _is_external_or_anchor(target):
                continue
            resolved = _resolve_doc_target(rel, target)
            if not resolved.exists():
                issues.append(f"broken markdown link in {rel}: {target}")


def _looks_like_repo_path(value: str) -> bool:
    if any(ch in value for ch in " \t\n'\"()"):
        return False
    if any(ch in value for ch in "<>"):
        return False
    if "::" in value:
        return False
    if "your_" in value or "<skill>" in value:
        return False
    if ".hermes/" in value or value.startswith("./.hermes/"):
        return False
    if value.startswith(("$", "~", "<", "{", "http://", "https://")):
        return False
    if value in {"AGENTS.md", "README.md", "SECURITY.md", "pyproject.toml"}:
        return True
    if value.startswith(("./", "../", ".github/", "docs/", "scripts/", "tests/")):
        return True
    known_prefixes = (
        "agent/",
        "apps/",
        "cron/",
        "gateway/",
        "hermes_cli/",
        "plugins/",
        "tools/",
        "tui_gateway/",
        "ui-tui/",
        "web/",
        "website/",
    )
    return value.startswith(known_prefixes)


def _path_or_glob_exists(value: str) -> bool:
    path_text = _strip_anchor_and_line(value.rstrip(".,;:"))
    if any(ch in path_text for ch in "*?["):
        return any(REPO_ROOT.glob(path_text))
    return (REPO_ROOT / path_text).exists()


def check_file_references(issues: list[str]) -> None:
    for rel in MANUAL_DOCS:
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for match in CODE_RE.finditer(text):
            ref = match.group(1).strip()
            if not _looks_like_repo_path(ref):
                continue
            if not _path_or_glob_exists(ref):
                issues.append(f"missing file reference in {rel}: {ref}")


def check_source_of_truth_files(issues: list[str]) -> None:
    for rel in SOURCE_OF_TRUTH_FILES:
        if not (REPO_ROOT / rel).exists():
            issues.append(f"missing source-of-truth file: {rel}")


def check_logbook(issues: list[str]) -> None:
    path = REPO_ROOT / "docs/LOGBOOK.md"
    if not path.exists():
        issues.append("missing logbook: docs/LOGBOOK.md")
        return
    text = path.read_text(encoding="utf-8")
    if not LOG_ENTRY_RE.search(text):
        issues.append("docs/LOGBOOK.md has no timestamped entry")


def main() -> int:
    issues: list[str] = []
    check_required_files(issues)
    check_markdown_links(issues)
    check_file_references(issues)
    check_source_of_truth_files(issues)
    check_logbook(issues)

    if issues:
        print("docs audit failed:")
        for issue in issues:
            print(f"- {issue}")
        return 1

    print(f"docs audit passed: {len(REQUIRED_DOCS)} required docs checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
