import subprocess

from odoo_migrate import runner
from odoo_migrate.cli import main


def test_fix_dry_run_changes_nothing(make_module):
    m = make_module({"views/r.xml": '<odoo><template id="t"><span t-esc="x"/></template></odoo>\n'})
    before = (m.path / "views/r.xml").read_text()
    result = runner.fix(m, dry_run=True)
    assert {f["file"] for f in result["files"]} == {"__manifest__.py", "views/r.xml"}
    assert (m.path / "views/r.xml").read_text() == before and not result["written"]


def test_fix_write_outside_git_makes_backup(make_module):
    m = make_module({"views/r.xml": '<odoo><template id="t"><span t-esc="x"/></template></odoo>\n'})
    result = runner.fix(m, dry_run=False)
    assert result["written"] and result["backup"]
    assert "t-out" in (m.path / "views/r.xml").read_text()
    assert "t-esc" in (m.path.parent / ".odoo-migrate-backup").glob("*/views/r.xml").__next__().read_text()


def test_fix_refuses_dirty_git(make_module):
    m = make_module({"views/r.xml": '<odoo><template id="t"><span t-esc="x"/></template></odoo>\n'})
    subprocess.run(["git", "init", "-q"], cwd=m.path, check=True)
    import pytest

    with pytest.raises(SystemExit, match="uncommitted"):
        runner.fix(m, dry_run=False)
    assert "t-esc" in (m.path / "views/r.xml").read_text()


def test_fix_is_idempotent(make_module):
    m = make_module({"models/a.py": "x = fields.Float(group_operator='sum')\n"})
    runner.fix(m, dry_run=False)
    assert runner.fix(m, dry_run=True)["files"] == []


def test_cli_scan_exit_code(make_module, capsys):
    m = make_module({})
    assert main(["scan", str(m.path)]) == 1  # 19.0 version is an error
    assert "MAN001" in capsys.readouterr().out


def test_tasks_written_for_manual_findings(make_module, tmp_path):
    import json

    from odoo_migrate import tasks

    m = make_module({"views/r.xml": '<odoo><template id="t"><div t-raw="x"/></template></odoo>\n'})
    md, js, n = tasks.write(m, runner.scan(m), tmp_path / "out")
    data = json.loads(js.read_text())
    assert n == len(data) >= 1 and data[0]["rule"] == "XML002"
    assert "t-raw" in data[0]["code"] and "XML002" in md.read_text()


def test_html_report(make_module):
    from odoo_migrate import report

    m = make_module({"views/r.xml": '<odoo><template id="t"><span t-esc="x"/></template></odoo>\n'})
    page = report.to_html({m.name: runner.scan(m)}, {m.name: runner.fix(m, dry_run=True)})
    assert page.startswith("<!doctype html>") and "XML001" in page and "auto-fix" in page
    assert '<span class="ln add">+' in page and "t-out" in page  # the diff is rendered line by line
