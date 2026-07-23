from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts import verify_qwen_engineering_vendor as verifier


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _verification_copy(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    paths = (
        "agent/review_evidence.py",
        "hermes_cli/engineering_dist",
        "hermes_cli/engineering_review",
        "hermes_cli/subcommands/review.py",
        "packages/hermes-engineering/src",
        "third_party/qwen-code",
    )
    for relative in paths:
        source = REPOSITORY_ROOT / relative
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
    return root


@pytest.mark.parametrize(
    ("relative_path", "source", "message"),
    [
        (
            "packages/hermes-engineering/src/forbidden-side-effect.ts",
            'import "./submit.js";\nexport const value = 1;\n',
            "forbidden import",
        ),
        (
            "packages/hermes-engineering/src/forbidden-require.ts",
            'const value = require("./telemetry.js");\nexport { value };\n',
            "forbidden import",
        ),
        (
            "hermes_cli/engineering_review/forbidden_mutation.py",
            'import subprocess\nsubprocess.run(["git", "push"], check=True)\n',
            "remote mutation command",
        ),
    ],
)
def test_verifier_rejects_negative_temp_copy(
    tmp_path: Path,
    relative_path: str,
    source: str,
    message: str,
) -> None:
    root = _verification_copy(tmp_path)
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        verifier.verify(root)
