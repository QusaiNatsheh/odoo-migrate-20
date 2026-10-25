"""Detection evaluation against Odoo's own 19.0 -> 20.0 migration of its Community modules.

Ground truth per module and change type: the 19.0 module contains the old pattern and Odoo's real 20.0 version of
the same module no longer does (Odoo had to change it). The tool "detects" a change type when its scan of the
19.0 module reports the corresponding rule. Read-only on both source trees.

Usage: python scripts/evaluate_detection.py /path/odoo19/odoo /path/odoo20/odoo results/detection.json
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from odoo_migrate.engine import Module
from odoo_migrate.rules.python_api import IMPORT_MOVES, REMOVED_TOOLS
from odoo_migrate.runner import scan

SKIP_DIRS = {"static", "i18n", "__pycache__"}  # tests/ included: the tool scans them and Odoo migrated them


def _texts(module_dir: Path, suffixes: tuple[str, ...]) -> str:
    parts = []
    for p in module_dir.rglob("*"):
        if p.is_file() and p.suffix in suffixes and not SKIP_DIRS & set(p.relative_to(module_dir).parts):
            parts.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _count(pattern: str, text: str) -> int:
    return len(re.findall(pattern, text))


# change type -> (rule id, file suffixes, regex for the old pattern)
CHANGE_TYPES = {
    "access rights (ir.model.access / ir.rule)": ("SEC001", None, None),
    "get_param / set_param": ("PY001", (".py",), r"\.(?:get|set)_param\("),
    "QWeb t-esc": ("XML001", (".xml",), r"\bt-esc="),
    "QWeb t-raw": ("XML002", (".xml",), r"\bt-raw="),
    "search filter date-range attributes": ("XML003", (".xml",), r"\b(?:start|end)_(?:month|year)="),
    "type=base64 in XML": ("XML004", (".xml",), r"""type=["']base64["']"""),
    "Font Awesome icons": ("XML006", (".xml",), r"\bfa fa-[\w-]+|\bicon=[\"']fa-"),
    "report_file field": ("XML007", (".xml",), r"""<field name=["']report_file["']"""),
    "removed access-check methods": (
        "PY004",
        (".py",),
        r"\.(?:check_access_rights|check_access_rule|_filter_access_rules)\(",
    ),
    "self._cr / _uid / _context": ("PY009", (".py",), r"\bself\._(?:cr|uid|context)\b"),
    "odoo.tools import moves": (
        "PY007",
        (".py",),
        r"from odoo\.tools import [^\n]*\b(?:"
        + "|".join(
            [
                "column_exists",
                "table_exists",
                "create_column",
                "create_index",
                "index_exists",
                "drop_index",
                "Query",
                "ormcache",
                "drop_constraint",
                "add_constraint",
            ]
        )
        + r")\b",
    ),
}


# Every name the Odoo 20 source shows as moved or removed from odoo.tools (see docs/change-catalog.md).
TOOLS_MOVED = set(IMPORT_MOVES) | REMOVED_TOOLS
TOOLS_REMOVED_MODULES = {"odoo.tools.pycompat", "odoo.tools.query", "odoo.tools.populate"}


def _tools_imports(module_dir: Path) -> int:
    """Moved names imported from odoo.tools, read from the syntax tree (handles multi-line imports)."""
    import ast

    count = 0
    for p in module_dir.rglob("*.py"):
        if SKIP_DIRS & set(p.relative_to(module_dir).parts):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "odoo.tools":
                count += sum(a.name in TOOLS_MOVED for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module in ("odoo.tools.misc", "odoo.tools.sql"):
                count += sum(a.name in REMOVED_TOOLS for a in node.names)  # moved names are valid there
            elif isinstance(node, ast.ImportFrom) and node.module in TOOLS_REMOVED_MODULES:
                count += 1
    return count


def needed(change: str, old_dir: Path, new_dir: Path) -> tuple[int, int]:
    """(occurrences in 19, occurrences in 20) of the old pattern."""
    rule, suffixes, pattern = CHANGE_TYPES[change]
    if rule == "PY007":
        return _tools_imports(old_dir), _tools_imports(new_dir)
    if rule == "SEC001":
        old = len(list(old_dir.rglob("ir.model.access.csv"))) + _count(
            r"""model=["']ir\.(?:rule|model\.access)["']""", _texts(old_dir, (".xml",))
        )
        new = len(list(new_dir.rglob("ir.model.access.csv"))) + _count(
            r"""model=["']ir\.(?:rule|model\.access)["']""", _texts(new_dir, (".xml",))
        )
        return old, new

    def code(d):  # ignore Python comments: the tool does not report commented-out code
        text = _texts(d, suffixes)
        return re.sub(r"(?m)^\s*#.*$", "", text) if suffixes == (".py",) else text

    return _count(pattern, code(old_dir)), _count(pattern, code(new_dir))


def main(old_root: str, new_root: str, out: str):
    old_addons, new_addons = Path(old_root) / "addons", Path(new_root) / "addons"
    common = sorted(
        p.parent.name
        for p in old_addons.glob("*/__manifest__.py")
        if (new_addons / p.parent.name / "__manifest__.py").exists()
    )
    stats = defaultdict(lambda: {"tp": 0, "fn": 0, "fp": 0, "tn": 0, "fn_modules": [], "fp_modules": []})
    field_flags = defaultdict(int)
    field_examples: list[str] = []
    for name in common:
        findings = scan(Module(old_addons / name))
        flagged_rules = {f.rule for f in findings}
        for f in findings:
            if f.rule in ("FLD001", "FLD002"):
                # Confirmed when Odoo's 20.0 version of the same file uses the field name fewer times.
                m = re.search(r"\.(\w+) does not exist", f.message)
                old_file, new_file = old_addons / name / f.file, new_addons / name / f.file
                if m:
                    word = rf"\b{m.group(1)}\b"
                    old_n = _count(word, old_file.read_text(encoding="utf-8", errors="replace"))
                    new_n = (
                        _count(word, new_file.read_text(encoding="utf-8", errors="replace")) if new_file.exists() else 0
                    )
                    field_flags[f"{f.rule}_confirmed" if new_n < old_n else f"{f.rule}_unconfirmed"] += 1
                    if new_n >= old_n and len(field_examples) < 15:
                        field_examples.append(f"{name}/{f.file}:{f.line} {f.message[:100]}")
        for change, (rule, _, _) in CHANGE_TYPES.items():
            old_n, new_n = needed(change, old_addons / name, new_addons / name)
            truth = old_n > 0 and new_n < old_n
            flag = rule in flagged_rules
            s = stats[change]
            key = "tp" if truth and flag else "fn" if truth else "fp" if flag else "tn"
            s[key] += 1
            if key in ("fn", "fp") and len(s[f"{key}_modules"]) < 10:
                s[f"{key}_modules"].append(name)
    rows = {}
    for change, s in stats.items():
        tp, fn, fp = s["tp"], s["fn"], s["fp"]
        rows[change] = {
            **s,
            "rule": CHANGE_TYPES[change][0],
            "recall": tp / (tp + fn) if tp + fn else None,
            "precision": tp / (tp + fp) if tp + fp else None,
        }
    result = {
        "modules": len(common),
        "change_types": rows,
        "field_rule_findings": dict(field_flags),
        "field_unconfirmed_examples": field_examples,
    }
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(result, indent=1))
    print(f"{len(common)} modules in both versions\n")
    print(f"{'change type':42}{'rule':8}{'needed':>7}{'found':>7}{'recall':>8}{'flags':>7}{'precision':>10}")
    for change, r in sorted(rows.items(), key=lambda kv: -(kv[1]["tp"] + kv[1]["fn"])):
        need, flags = r["tp"] + r["fn"], r["tp"] + r["fp"]
        rec = f"{r['recall']:.0%}" if r["recall"] is not None else "-"
        prec = f"{r['precision']:.0%}" if r["precision"] is not None else "-"
        print(f"{change:42}{r['rule']:8}{need:7}{r['tp']:7}{rec:>8}{flags:7}{prec:>10}")
    for rule in ("FLD001", "FLD002"):
        ok, bad = field_flags[f"{rule}_confirmed"], field_flags[f"{rule}_unconfirmed"]
        if ok + bad:
            print(f"{rule}: {ok + bad} findings, {ok / (ok + bad):.0%} confirmed by Odoo's own 20.0 change")


if __name__ == "__main__":
    main(*sys.argv[1:4])
