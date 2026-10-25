"""Migration report: Markdown (for humans and PRs), JSON (for tools), HTML (single self-contained page)."""

import html
import json
from collections import Counter
from dataclasses import asdict

from .engine import Finding
from .registry import all_rules


def _rules():
    return {r.id: r for r in all_rules()}


def to_json(results: dict[str, list[Finding]]) -> str:
    return json.dumps({m: [asdict(f) for f in fs] for m, fs in results.items()}, indent=1)


def to_markdown(results: dict[str, list[Finding]], fix_results: dict[str, dict] | None = None) -> str:
    rules = _rules()
    lines = ["# Odoo 19 → 20 migration report", ""]
    total = Counter(f.severity for fs in results.values() for f in fs)
    lines.append(
        f"{len(results)} module(s): **{total['error']} errors** (break on Odoo 20), "
        f"{total['warning']} warnings (deprecated), {total['info']} notes."
    )
    lines.append("")
    lines += ["| Module | Errors | Warnings | Auto-fixable |", "|---|---|---|---|"]
    for module, fs in results.items():
        c = Counter(f.severity for f in fs)
        lines.append(f"| {module} | {c['error']} | {c['warning']} | {sum(f.fixable for f in fs)} |")
    for module, fs in results.items():
        lines += ["", f"## {module}", ""]
        if not fs:
            lines.append("No known Odoo 20 issues found.")
            continue
        for rule_id, count in sorted(Counter(f.rule for f in fs).items()):
            rule = rules[rule_id]
            lines += ["", f"### {rule_id} · {rule.title} ({rule.severity}, {count})", "", rule.explanation, ""]
            for f in [f for f in fs if f.rule == rule_id]:
                mark = "auto-fix" if f.fixable else "manual"
                lines.append(
                    f"- `{f.file}:{f.line}` [{mark}] {f.message}"
                    + (f" — `{f.snippet}`" if f.snippet and len(f.snippet) < 90 else "")
                )
        if fix_results and module in fix_results:
            fr = fix_results[module]
            lines += ["", "### Proposed changes", ""]
            if fr["official_log"]:
                lines += [
                    "Odoo `upgrade_code` log (review warnings):",
                    "",
                    "```",
                    fr["official_log"][-3000:],
                    "```",
                    "",
                ]
            for f in fr["files"]:
                lines += [f"`{f['file']}` ({f['status']})", "", "```diff", f["diff"].rstrip(), "```", ""]
    return "\n".join(lines) + "\n"


_CSS = """
:root{--bg:#fcfcfb;--text:#2b2b29;--muted:#6b6b66;--line:#e6e5e0;--card:#fff;--err:#c4322f;--warn:#a86b00;
--fix:#1a7f4f;--man:#5a4bc4;--add:#e8f5ec;--del:#fbeaea}
@media (prefers-color-scheme:dark){:root{--bg:#1a1a19;--text:#f0efec;--muted:#a9a89f;--line:#33332f;--card:#222220;
--add:#16301f;--del:#3a1b1b}}
*{box-sizing:border-box}body{font:14px/1.55 system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);
max-width:1100px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 8px}
h3{font-size:15px;margin:20px 0 4px}.muted{color:var(--muted)}table{border-collapse:collapse;width:100%;margin:12px 0}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line)}th{color:var(--muted);font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;margin:10px 0}
.badge{display:inline-block;border-radius:10px;padding:0 8px;font-size:12px;font-weight:600;margin-right:6px;
border:1px solid currentColor}.error{color:var(--err)}.warning{color:var(--warn)}.fix{color:var(--fix)}
.manual{color:var(--man)}code{font:12.5px ui-monospace,monospace}ul{margin:6px 0;padding-left:18px}
li{margin:3px 0}pre{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:8px 10px;
overflow-x:auto;font:12.5px/1.45 ui-monospace,monospace}.ln{display:block;white-space:pre}.add{background:var(--add)}
.del{background:var(--del)}
"""


def _diff_html(diff: str) -> str:
    out = []
    for line in diff.splitlines():
        cls = (
            "add"
            if line.startswith("+") and not line.startswith("+++")
            else ("del" if line.startswith("-") and not line.startswith("---") else "")
        )
        text = html.escape(line) or " "
        out.append(f'<span class="ln {cls}">{text}</span>')  # one block per line: no extra line breaks
    return "<pre>" + "".join(out) + "</pre>"


def to_html(results: dict[str, list[Finding]], fix_results: dict[str, dict] | None = None) -> str:
    """Self-contained report page (no external assets), readable in light and dark mode."""
    e = html.escape
    rules = _rules()
    total = Counter(f.severity for fs in results.values() for f in fs)
    parts = [
        "<h1>Odoo 19 → 20 migration report</h1>",
        f"<p class='muted'>{len(results)} module(s) · <span class='error'><b>{total['error']} errors</b></span> "
        f"(break on Odoo 20) · <span class='warning'>{total['warning']} warnings</span> (deprecated)</p>",
        "<table><tr><th>Module</th><th>Errors</th><th>Warnings</th><th>Auto-fixable</th><th>Manual</th></tr>",
    ]
    for module, fs in results.items():
        c = Counter(f.severity for f in fs)
        fixable = sum(f.fixable for f in fs)
        parts.append(
            f"<tr><td><b>{e(module)}</b></td><td>{c['error']}</td><td>{c['warning']}</td>"
            f"<td>{fixable}</td><td>{len(fs) - fixable}</td></tr>"
        )
    parts.append("</table>")
    for module, fs in results.items():
        parts.append(f"<h2>{e(module)}</h2>")
        if not fs:
            parts.append("<p class='muted'>No known Odoo 20 issues found.</p>")
        for rule_id, count in sorted(Counter(f.rule for f in fs).items()):
            rule = rules[rule_id]
            items = "".join(
                f"<li><span class='badge {'fix' if f.fixable else 'manual'}'>{'auto-fix' if f.fixable else 'manual'}"
                f"</span><code>{e(f.file)}:{f.line}</code> {e(f.message)}</li>"
                for f in fs
                if f.rule == rule_id
            )
            parts.append(
                f"<div class='card'><h3><span class='badge {rule.severity}'>{rule.severity}</span>"
                f"{e(rule_id)} · {e(rule.title)} <span class='muted'>({count})</span></h3>"
                f"<p class='muted'>{e(rule.explanation)}</p><ul>{items}</ul></div>"
            )
        if fix_results and module in fix_results:
            fr = fix_results[module]
            parts.append("<h3>Proposed changes</h3>")
            if fr["official_log"]:
                parts.append(
                    f"<p class='muted'>Odoo <code>upgrade_code</code> log</p><pre>{e(fr['official_log'][-3000:])}</pre>"
                )
            for f in fr["files"]:
                parts.append(
                    f"<p><code>{e(f['file'])}</code> <span class='muted'>({f['status']})</span></p>"
                    + _diff_html(f["diff"])
                )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Odoo 20 migration report</title><style>{_CSS}</style></head><body>" + "".join(parts) + "</body></html>"
    )
