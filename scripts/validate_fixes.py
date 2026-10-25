"""Fix validation: install each module on Odoo 20 and run its tests, in three variants.

- v19:   Odoo's 19.0 module, unchanged
- fixed: the 19.0 module after `odoo-migrate fix` (Odoo's upgrade_code scripts + our safe rules), no manual edits
- v20:   Odoo's real 20.0 module (reference)

Each variant is copied into a scratch addons directory placed before Odoo's addons on the addons path, so it
shadows the real module. Nothing is written to the Odoo checkouts; their `git status` is checked after each run.

Usage: python scripts/validate_fixes.py ODOO19_SRC ODOO20_SRC WORKDIR OUT.json module [module ...]
"""

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from odoo_migrate import context
from odoo_migrate.engine import Module
from odoo_migrate.runner import fix, scan


def git_state(path: Path) -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=path, capture_output=True, text=True).stdout


def run_odoo(odoo20: Path, addons_dir: Path, module: str, workdir: Path, port: int) -> dict:
    db = f"omig_{module}_{uuid.uuid4().hex[:6]}"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    cmd = [
        "python3",
        str(odoo20 / "odoo-bin"),
        "-d",
        db,
        f"--addons-path={addons_dir},{odoo20 / 'addons'}",
        f"--data-dir={workdir / 'data'}",
        "-i",
        module,
        "--test-tags",
        f"/{module}",
        "--stop-after-init",
        f"--http-port={port}",
        "--log-level=test",
        "--max-cron-threads=0",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, env=env)
        log = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        log = (exc.stdout or b"").decode(errors="replace") + "\nTIMEOUT"
    finally:
        subprocess.run(["dropdb", "--if-exists", "--force", db], capture_output=True)
    m = re.search(r"(\d+) failed, (\d+) error\(s\) of (\d+) tests", log)
    install_error = None
    for line in log.splitlines():
        if re.search(
            r" (CRITICAL|ERROR) .*(Failed to load registry|ParseError|while parsing|Failed to initialize"
            r"|module .* not installable|Some modules are not loaded|invalid module names)",
            line,
        ):
            install_error = line.split(" odoo.", 1)[-1][:300]
            break
    if m is None and install_error is None and ("Traceback" in log or "TIMEOUT" in log):
        tail = [line for line in log.splitlines() if line.strip()][-1:]
        install_error = tail[0][:300] if tail else "unknown error"
    tests = {"failed": int(m.group(1)), "errors": int(m.group(2)), "total": int(m.group(3))} if m else None
    installed = install_error is None and (tests is not None or "Modules loaded" in log)
    (workdir / "logs").mkdir(exist_ok=True)
    (workdir / "logs" / f"{module}_{addons_dir.name}.log").write_text(log)
    return {
        "installed": installed,
        "install_error": install_error,
        "tests": tests,
        "passed": installed and (tests is None or tests["failed"] + tests["errors"] == 0),
    }


def validate(module: str, odoo19: Path, odoo20: Path, work: Path, port: int) -> dict:
    root = work / module
    shutil.rmtree(root, ignore_errors=True)
    variants = {}
    for variant, src in (
        ("v19", odoo19 / "addons" / module),
        ("fixed", odoo19 / "addons" / module),
        ("v20", odoo20 / "addons" / module),
    ):
        dst = root / variant / module
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
        variants[variant] = dst.parent
    fixed_module = Module(variants["fixed"] / module)
    fix_result = fix(fixed_module, odoo20, dry_run=False, allow_dirty=True)
    remaining = scan(fixed_module)
    result = {
        "module": module,
        "files_changed_by_fix": len(fix_result["files"]),
        "remaining_errors": sum(f.severity == "error" for f in remaining),
        "remaining_rules": sorted({f.rule for f in remaining if f.severity == "error"}),
    }
    before = git_state(odoo20)
    for k, (variant, path) in enumerate(variants.items()):
        result[variant] = run_odoo(odoo20, path, module, root, port + k)
        if git_state(odoo20) != before:
            raise SystemExit(f"SAFETY STOP: {odoo20} changed while testing {module}/{variant}")
    return result


def main(odoo19: str, odoo20: str, work: str, out: str, *modules: str):
    odoo19, odoo20, work = Path(odoo19), Path(odoo20), Path(work)
    context.ODOO_SRC = odoo20.resolve()  # remaining findings include view and import checks
    work.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(validate, m, odoo19, odoo20, work, 18100 + 10 * i) for i, m in enumerate(modules)]
        results = [f.result() for f in futures]
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(results, indent=1))

    def fmt(r: dict) -> str:
        if r["passed"]:
            text = "PASS"
        elif not r["installed"]:
            text = "install failed"
        else:
            text = f"{r['tests']['failed'] + r['tests']['errors']} test failures"
        return text + (f" ({r['tests']['total']} tests)" if r["tests"] else "")

    print(f"{'module':28}{'v19 (unchanged)':24}{'after fix':24}{'real 20.0':24}left")
    for r in results:
        print(f"{r['module']:28}{fmt(r['v19']):24}{fmt(r['fixed']):24}{fmt(r['v20']):24}{r['remaining_rules']}")


if __name__ == "__main__":
    main(*sys.argv[1:])
