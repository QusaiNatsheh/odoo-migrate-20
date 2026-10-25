import textwrap
from pathlib import Path

import pytest

from odoo_migrate.engine import Module


@pytest.fixture
def make_module(tmp_path):
    """Create a module from {relative path: content}; returns a Module."""

    def make(files: dict[str, str], name: str = "my_module") -> Module:
        root = tmp_path / name
        files = {"__manifest__.py": "{'name': 'X', 'version': '19.0.1.0.0', 'depends': ['base'], 'data': []}", **files}
        for rel, content in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
        return Module(root)

    return make


def rule(rule_id):
    from odoo_migrate.registry import all_rules

    return next(r for r in all_rules() if r.id == rule_id)


def fixed(rule_id, module: Module, rel: str) -> str:
    """Apply one rule's fix in memory and return the new text of one file."""
    for change in rule(rule_id).fix(module):
        if change.path == module.path / rel:
            return change.new_text
    return (module.path / rel).read_text()


@pytest.fixture
def helpers():
    class H:
        rule = staticmethod(rule)
        fixed = staticmethod(fixed)

    return H


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")
