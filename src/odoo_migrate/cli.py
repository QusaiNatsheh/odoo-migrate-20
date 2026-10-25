"""odoo-migrate scan|fix|rules"""

import argparse
import sys
from pathlib import Path

from . import report, runner
from .registry import all_rules


def main(argv=None):
    parser = argparse.ArgumentParser(prog="odoo-migrate", description="Odoo 19 -> 20 module migration assistant")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="report what breaks on Odoo 20 (never changes files)")
    s.add_argument("path", type=Path, help="a module, or a directory of modules")
    s.add_argument("--format", choices=["md", "json", "html"], default="md")
    s.add_argument("--output", type=Path, help="write the report to this file instead of stdout")
    s.add_argument("--odoo-src", type=Path, help="Odoo 20 source checkout: also check views and imports against it")
    f = sub.add_parser("fix", help="apply safe fixes (dry run unless --write)")
    f.add_argument("path", type=Path)
    f.add_argument("--odoo-src", type=Path, help="Odoo 20 source checkout, to also run Odoo's own upgrade_code")
    f.add_argument("--python", default="python3", help="Python that can run odoo-bin (default: python3)")
    f.add_argument("--write", action="store_true", help="write the changes (default: show the diff only)")
    f.add_argument("--allow-dirty", action="store_true", help="allow writing over uncommitted Git changes")
    f.add_argument("--output", type=Path, help="write the report with diffs to this file")
    f.add_argument("--format", choices=["md", "json", "html"], default="md")
    t = sub.add_parser("tasks", help="write manual findings as self-contained tasks (for a developer or an LLM)")
    t.add_argument("path", type=Path)
    t.add_argument("--odoo-src", type=Path, help="Odoo 20 source checkout: adds the relevant Odoo 20 code")
    t.add_argument("--out", type=Path, default=Path("migration-tasks"))
    sub.add_parser("rules", help="list all rules")
    args = parser.parse_args(argv)

    if args.cmd == "rules":
        for r in all_rules():
            print(f"{r.id:7} {r.severity:8} {'auto-fix' if r.safe else 'report  '}  {r.title}")
        return 0
    if getattr(args, "odoo_src", None):
        from . import context

        context.ODOO_SRC = args.odoo_src.resolve()
    modules = runner.find_modules(args.path)
    if not modules:
        print(f"No Odoo module found at {args.path}", file=sys.stderr)
        return 2
    results = {m.name: runner.scan(m) for m in modules}
    if args.cmd == "tasks":
        from . import tasks

        for m in modules:
            md, js, n = tasks.write(m, results[m.name], args.out)
            print(f"{m.name}: {n} task(s) -> {md}, {js}")
        return 0
    fixes = None
    if args.cmd == "fix":
        fixes = {
            m.name: runner.fix(
                m, args.odoo_src, dry_run=not args.write, allow_dirty=args.allow_dirty, python=args.python
            )
            for m in modules
        }
        if args.write:
            results = {m.name: runner.scan(m) for m in modules}  # what is left after the fixes
    if getattr(args, "format", "md") == "json":
        out = report.to_json(results)
    elif getattr(args, "format", "md") == "html":
        out = report.to_html(results, fixes)
    else:
        out = report.to_markdown(results, fixes)
    if args.output:
        args.output.write_text(out, encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        print(out)
    if fixes:
        for name, fr in fixes.items():
            state = "written" if fr["written"] else "dry run, nothing written"
            print(
                f"{name}: {len(fr['files'])} file(s) changed ({state})"
                + (f", backup at {fr['backup']}" if fr["backup"] else ""),
                file=sys.stderr,
            )
    return 1 if any(f.severity == "error" for fs in results.values() for f in fs) else 0


if __name__ == "__main__":
    sys.exit(main())
