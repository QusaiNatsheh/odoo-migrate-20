"""All rules, in report order. Each rule module exposes RULES: list[Rule]."""

from importlib import import_module
from pkgutil import iter_modules

from . import rules as rules_pkg
from .engine import Rule


def all_rules() -> list[Rule]:
    found: list[Rule] = []
    for info in sorted(iter_modules(rules_pkg.__path__), key=lambda m: m.name):
        found.extend(import_module(f"{rules_pkg.__name__}.{info.name}").RULES)
    ids = [r.id for r in found]
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"Duplicate rule ids: {ids}")
    return found
