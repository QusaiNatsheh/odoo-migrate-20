"""Run Odoo's own source upgrade scripts (`odoo-bin upgrade_code`) on a copy of a module.

Odoo 20 ships scripts in odoo/upgrade_code/ for the 19.x -> 20.0 changes it can rewrite itself (access rights,
base64 -> bytes, t-call, date filters, ...). We run them instead of re-implementing them.

Isolation matters: when started through odoo-bin, upgrade_code replaces --addons-path with odoo.addons.__path__,
which always includes Odoo's own addons and the user's data directory, and it rewrites all of them. So we never
run it on the user's Odoo checkout. We copy only the core framework (odoo/odoo, no addons/ folder) into a
throwaway directory, give the process a throwaway HOME, and check that the real checkout is unchanged afterwards.
"""

import ast
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# Scripts that only make sense on Odoo's own localization / account report data; they fail on other modules.
ODOO_INTERNAL = {"19.3-00-account-groups", "19.3-00-account-report-foldable"}
VERSIONED = re.compile(r"^(19\.[1-9]|20\.0)-\d\d-")


def available_scripts(odoo_src: Path) -> list[str]:
    return sorted(
        p.stem
        for p in (odoo_src / "odoo" / "upgrade_code").glob("*.py")
        if VERSIONED.match(p.stem) and p.stem not in ODOO_INTERNAL
    )


def _git_state(path: Path) -> str | None:
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=path, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def _manifest_depends(module_dir: Path) -> list[str]:
    try:
        return list(ast.literal_eval((module_dir / "__manifest__.py").read_text(encoding="utf-8")).get("depends", []))
    except (OSError, ValueError, SyntaxError):
        return []


def dependency_dirs(module_path: Path, odoo_src: Path) -> dict[str, Path]:
    """Transitive dependencies, found in the Odoo source (addons/, odoo/addons/) or next to the module."""
    roots = [odoo_src / "addons", odoo_src / "odoo" / "addons", module_path.parent]
    found: dict[str, Path] = {}
    todo = _manifest_depends(module_path)
    while todo:
        name = todo.pop()
        if name in found or name == module_path.name:
            continue
        for root in roots:
            if (root / name / "__manifest__.py").exists():
                found[name] = root / name
                todo.extend(_manifest_depends(root / name))
                break
    return found


def run_on_copy(module_path: Path, odoo_src: Path, python: str = "python3") -> tuple[Path, str]:
    """Copy the module into a fresh addons directory, run the 19.x -> 20.0 scripts there, return (copy, log)."""
    odoo_src = odoo_src.resolve()
    if not (odoo_src / "odoo-bin").exists() or not (odoo_src / "odoo" / "upgrade_code").is_dir():
        raise FileNotFoundError(f"{odoo_src} is not an Odoo 20 source checkout (odoo-bin, odoo/upgrade_code)")
    workdir = Path(tempfile.mkdtemp(prefix="odoo-migrate-"))
    addons = workdir / "addons"
    copy = addons / module_path.name
    shutil.copytree(module_path, copy, ignore=shutil.ignore_patterns("__pycache__", ".git", ".odoo-migrate-backup"))
    # Odoo's converters read the dependencies' files (e.g. groups, manifests), so copy them in too. Only the
    # target module is diffed back; the copies are discarded.
    for name, path in dependency_dirs(module_path, odoo_src).items():
        if path.parent != odoo_src / "odoo" / "addons":  # base & co. live in the throwaway framework copy
            shutil.copytree(
                path, addons / name, ignore=shutil.ignore_patterns("__pycache__", ".git", "static", "i18n", "tests")
            )
    # Throwaway framework: odoo-bin + odoo/ package only. Its built-in base addon may get rewritten; it is discarded.
    sandbox = workdir / "odoo-src"
    shutil.copytree(odoo_src / "odoo", sandbox / "odoo", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(odoo_src / "odoo-bin", sandbox / "odoo-bin")
    home = workdir / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    before = _git_state(odoo_src)
    logs = []
    for script in available_scripts(odoo_src):
        proc = subprocess.run(
            [python, str(sandbox / "odoo-bin"), "upgrade_code", "--script", script, "--addons-path", str(addons)],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=workdir,
            env=env,
        )
        out = "\n".join(
            line for line in (proc.stdout + proc.stderr).splitlines() if str(sandbox) not in line
        ).strip()  # hide rewrites of the throwaway framework
        if proc.returncode not in (0, 1):  # upgrade_code exits 1 when it changed files ("dirty")
            logs.append(f"[{script}] FAILED (exit {proc.returncode}): {out.splitlines()[-1] if out else ''}")
        elif out:
            logs.append(f"[{script}]\n{out}")
    after = _git_state(odoo_src)
    if before is not None and after != before:
        raise RuntimeError(
            f"SAFETY STOP: the Odoo checkout at {odoo_src} changed while running upgrade_code. "
            "Inspect it with `git status` before doing anything else."
        )
    shutil.rmtree(sandbox, ignore_errors=True)
    shutil.rmtree(home, ignore_errors=True)
    return copy, "\n".join(logs)
