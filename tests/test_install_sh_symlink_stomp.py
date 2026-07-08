"""Regression for #21454: re-running install.sh on a symlinked prior install.

Older versions of ``install.sh`` created ``$command_link_dir/hermes`` as a
symlink to the pip-generated entry point at ``$HERMES_BIN`` (i.e.
``venv/bin/hermes``). When ``setup_path()`` later switched to writing a bash
shim with ``cat > "$command_link_dir/hermes" <<EOF``, the redirect followed
the existing symlink and overwrote the pip entry point with the shim. The
shim's ``exec "$HERMES_BIN" "$@"`` then self-recursed and ``hermes`` hung on
every invocation.

These tests pin the fix for both public launchers: ``setup_path()`` must remove
``$command_link_dir/<command>`` before writing through the redirect, so each shim
is created as a regular file in ``command_link_dir`` and the venv entry point is
left intact.
"""

from __future__ import annotations

import re
import stat
import subprocess
from pathlib import Path



REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


def _extract_setup_path_shim_block() -> str:
    """Return the install.sh shim-write block used by setup_path()."""
    text = INSTALL_SH.read_text()
    match = re.search(
        r"(?P<block>mkdir -p \"\$command_link_dir\".*?write_launcher_shim \"hermes\" \"\$HERMES_BIN\")",
        text,
        re.DOTALL,
    )
    assert match is not None, (
        "Could not locate the setup_path shim-write block in scripts/install.sh"
    )
    return match["block"]


def test_setup_path_shim_block_removes_old_link_before_writing() -> None:
    """Static guard: the rm must precede the cat heredoc, not follow it."""
    block = _extract_setup_path_shim_block()
    rm_idx = block.find('rm -f "$command_link_dir/$command_name"')
    cat_idx = block.find('cat > "$command_link_dir/$command_name" <<EOF')
    assert rm_idx != -1, (
        "setup_path() must `rm -f` $command_link_dir/$command_name before the "
        "`cat >` heredoc, otherwise an existing symlink (left by older "
        "installs) will be followed and the pip entry point overwritten. "
        "See #21454."
    )
    assert cat_idx != -1, "expected `cat >` heredoc still present"
    assert rm_idx < cat_idx, (
        "`rm -f` must come *before* the `cat >` heredoc, not after."
    )


def test_re_running_setup_path_block_preserves_pip_entry_point(tmp_path: Path) -> None:
    """Behavioral repro: simulate prior-install symlink + new-install heredoc.

    Layout mirrors a real install:

        tmp/
          venv/bin/hades         <- pip entry point (the one we must preserve)
          venv/bin/hermes        <- compatibility pip entry point
          local_bin/hades        <- symlink -> ../venv/bin/hades  (old install)
          local_bin/hermes       <- symlink -> ../venv/bin/hermes (old install)

    Then we run the exact shim-write block from setup_path() with
    ``HERMES_BIN`` and ``command_link_dir`` pointed at this fixture. The fix
    requires that, after the run:

      * both venv entry points still contain their original pip-script bodies
      * both local command paths are regular files (not symlinks) holding shims
    """
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    pip_entries = {
        "hades": venv_bin / "hades",
        "hermes": venv_bin / "hermes",
    }
    pip_markers = {
        name: f"#!/usr/bin/env python\n# pip-generated {name} entry point -- must not be overwritten\n"
        for name in pip_entries
    }
    for name, pip_entry in pip_entries.items():
        pip_entry.write_text(pip_markers[name])
        pip_entry.chmod(pip_entry.stat().st_mode | stat.S_IXUSR)

    command_link_dir = tmp_path / "local_bin"
    command_link_dir.mkdir()
    shim_paths = {name: command_link_dir / name for name in pip_entries}
    for name, shim_path in shim_paths.items():
        # Reproduce the prior-install state: shim path is a symlink to the
        # pip-generated entry point.
        shim_path.symlink_to(pip_entries[name])
        assert shim_path.is_symlink()

    block = _extract_setup_path_shim_block()
    # Drive the block with the real env vars setup_path() sets.
    script = (
        "set -e\n"
        f"HADES_BIN={pip_entries['hades']!s}\n"
        f"HERMES_BIN={pip_entries['hermes']!s}\n"
        f"command_link_dir={command_link_dir!s}\n"
        f"{block}\n"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0, (
        f"shim-write block failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )

    for name, pip_entry in pip_entries.items():
        # The pip entry point must still be the original pip script, not a
        # re-written self-recursing bash shim.
        assert pip_entry.read_text() == pip_markers[name], (
            f"venv/bin/{name} was overwritten by setup_path() -- symlink-stomp "
            "regression (#21454)."
        )

        # The shim path itself must now be a regular file holding the launcher.
        shim_path = shim_paths[name]
        assert shim_path.exists()
        assert not shim_path.is_symlink(), (
            f"command_link_dir/{name} must be replaced with a regular file, not "
            "left as a symlink -- otherwise the next install will stomp again."
        )
        shim_text = shim_path.read_text()
        assert "unset PYTHONPATH" in shim_text
        assert "unset PYTHONHOME" in shim_text
        assert f'exec "{pip_entry}"' in shim_text
        shim_mode = shim_path.stat().st_mode
        assert shim_mode & stat.S_IXUSR, "shim must be user-executable"
