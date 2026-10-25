"""scan and fix over one module or a directory of modules.

`fix` always works on a temporary copy: Odoo's own upgrade scripts first (when --odoo-src is given), then our
safe rules one by one. The result is diffed against the original. Files are written back only when not a dry run,
and only after a Git cleanliness check or a backup copy.
"""

import difflib
import filecmp
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from . import official
from .engine import Finding, Module
from .pyutil import PythonRule, scan_python
from .registry import all_rules

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def find_modules(path: Path) -> list[Module]:
    path = path.resolve()
    if (path / "__manifest__.py").exists():
        return [Module(path)]
    return [Module(p.parent) for p in sorted(path.glob("*/__manifest__.py"))]


def scan(module: Module, only: set[str] | None = None) -> list[Finding]:
    rules = [r for r in all_rules() if not only or r.id in only]
    python_rules = [r for r in rules if isinstance(r, PythonRule)]
    findings = scan_python(python_rules, module) if python_rules else []
    for rule in rules:
        if not isinstance(rule, PythonRule):
            findings.extend(rule.scan(module))
    return sorted(findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.rule, f.file, f.line))


def _git_dirty(path: Path) -> bool | None:
    """True if the module has uncommitted changes in Git, False if clean, None if not in a Git repository."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--", "."], cwd=path, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return bool(out.stdout.strip())


def backup(module: Module) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = module.path.parent / ".odoo-migrate-backup" / f"{module.name}-{stamp}"
    shutil.copytree(module.path, dest, ignore=shutil.ignore_patterns("__pycache__", ".git"))
    return dest


def _tree_files(root: Path) -> set[str]:
    skip = {"__pycache__", ".git", ".odoo-migrate-backup"}
    return {
        str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and not skip & set(p.relative_to(root).parts)
    }


def _diff_trees(original: Path, changed: Path) -> list[dict]:
    out = []
    before, after = _tree_files(original), _tree_files(changed)
    for rel in sorted(before | after):
        a, b = original / rel, changed / rel
        if rel in before and rel in after and filecmp.cmp(a, b, shallow=False):
            continue
        old = a.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if rel in before else []
        new = b.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if rel in after else []
        status = "modified" if rel in before and rel in after else ("added" if rel in after else "deleted")
        out.append(
            {
                "file": rel,
                "status": status,
                "diff": "".join(
                    difflib.unified_diff(
                        old,
                        new,
                        f"a/{rel}" if rel in before else "/dev/null",
                        f"b/{rel}" if rel in after else "/dev/null",
                    )
                ),
            }
        )
    return out


def fix(
    module: Module,
    odoo_src: Path | None = None,
    dry_run: bool = True,
    allow_dirty: bool = False,
    python: str = "python3",
) -> dict:
    result = {
        "module": module.name,
        "official_log": "",
        "rules_applied": [],
        "files": [],
        "backup": None,
        "written": False,
    }
    if odoo_src:
        copy, log = official.run_on_copy(module.path, odoo_src, python)
        result["official_log"] = log
    else:
        copy = Path(tempfile.mkdtemp(prefix="odoo-migrate-")) / module.name
        shutil.copytree(module.path, copy, ignore=shutil.ignore_patterns("__pycache__", ".git", ".odoo-migrate-backup"))
    try:
        work = Module(copy)
        for rule in all_rules():
            if not rule.safe:
                continue
            changes = rule.fix(work)
            for c in changes:
                if c.new_text is None:
                    c.path.unlink()
                else:
                    c.path.write_text(c.new_text, encoding="utf-8")
            if changes:
                result["rules_applied"].append({"rule": rule.id, "files": sorted(work.rel(c.path) for c in changes)})
        result["files"] = _diff_trees(module.path, copy)
        if not dry_run and result["files"]:
            dirty = _git_dirty(module.path)
            if dirty and not allow_dirty:
                raise SystemExit(
                    f"{module.name}: uncommitted changes in Git. Commit or stash them, or pass "
                    "--allow-dirty (a backup copy is then made)."
                )
            if dirty is None or dirty:
                result["backup"] = str(backup(module))
            for f in result["files"]:
                src, dst = copy / f["file"], module.path / f["file"]
                if f["status"] == "deleted":
                    dst.unlink()
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            result["written"] = True
    finally:
        workdir = copy.parent.parent if copy.parent.name == "addons" else copy.parent
        shutil.rmtree(workdir, ignore_errors=True)
    return result
