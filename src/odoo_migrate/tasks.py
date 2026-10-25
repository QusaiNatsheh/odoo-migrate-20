"""Turn manual findings into self-contained migration tasks (Markdown + JSON).

Each task carries what a developer or an LLM needs to propose a fix as a diff: the rule's explanation, the code
around the finding, and, when --odoo-src is given, the relevant Odoo 20 source (for example the parent view that
an inherited view no longer matches). Nothing is sent anywhere: the files are written locally.
"""

import json
import re
from pathlib import Path

from lxml import etree

from . import context
from .engine import Finding, Module
from .registry import all_rules

CONTEXT_LINES = 12
PROMPT = (
    "You are migrating an Odoo 19 module to Odoo 20. Propose the smallest change that fixes the problem below, as "
    "a unified diff against the shown file. Keep behaviour identical. If the change needs a business decision, say "
    "which decision instead of guessing."
)


def _excerpt(path: Path, line: int) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start, end = max(line - CONTEXT_LINES, 1), min(line + CONTEXT_LINES, len(lines))
    return "\n".join(f"{n:5} {'>' if n == line else ' '} {lines[n - 1]}" for n in range(start, end + 1))


def _parent_view(finding: Finding) -> str | None:
    if finding.rule != "VIEW001" or context.ODOO_SRC is None:
        return None
    from .rules.against_source import view_index

    m = re.search(r"Odoo 20 view ([\w.]+)", finding.message)
    if not m:
        return None
    archs, _ = view_index(context.ODOO_SRC)
    parts = [etree.tostring(a, encoding="unicode", pretty_print=True) for a in archs.get(m.group(1), [])]
    return "\n".join(parts)[:6000] or None


def build(module: Module, findings: list[Finding]) -> list[dict]:
    rules = {r.id: r for r in all_rules()}
    tasks = []
    for n, f in enumerate([f for f in findings if not f.fixable], start=1):
        path = module.path / f.file
        task = {
            "id": f"{module.name}-{n:03d}",
            "rule": f.rule,
            "title": rules[f.rule].title,
            "severity": f.severity,
            "file": f.file,
            "line": f.line,
            "problem": f.message,
            "background": rules[f.rule].explanation,
            "code": _excerpt(path, f.line) if path.exists() else "",
            "odoo20_reference": _parent_view(f),
            "prompt": PROMPT,
        }
        tasks.append(task)
    return tasks


def to_markdown(module_name: str, tasks: list[dict]) -> str:
    out = [
        f"# Manual migration tasks: {module_name}",
        "",
        f"{len(tasks)} task(s). Each can be handed to a developer or pasted into an LLM as is.",
        "",
    ]
    for t in tasks:
        out += [
            f"## {t['id']} · {t['rule']} · {t['title']}",
            "",
            f"**Where:** `{t['file']}:{t['line']}`  ",
            f"**Problem:** {t['problem']}  ",
            f"**Background:** {t['background']}",
            "",
            "```",
            t["code"],
            "```",
            "",
        ]
        if t["odoo20_reference"]:
            out += ["Odoo 20 parent view:", "", "```xml", t["odoo20_reference"].rstrip(), "```", ""]
    return "\n".join(out)


def write(module: Module, findings: list[Finding], out_dir: Path) -> tuple[Path, Path, int]:
    tasks = build(module, findings)
    out_dir.mkdir(parents=True, exist_ok=True)
    md, js = out_dir / f"{module.name}-tasks.md", out_dir / f"{module.name}-tasks.json"
    md.write_text(to_markdown(module.name, tasks), encoding="utf-8")
    js.write_text(json.dumps(tasks, indent=1, ensure_ascii=False), encoding="utf-8")
    return md, js, len(tasks)
