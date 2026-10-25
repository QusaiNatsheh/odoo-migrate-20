"""Build catalog/removed_fields.json: fields defined on a model in Odoo 19 Community but nowhere in Odoo 20.

Usage: python scripts/build_field_catalog.py /path/to/odoo19/odoo /path/to/odoo20/odoo
Parses Python only (no imports): every class with _name/_inherit, every `x = fields.Y(...)` assignment.
"""

import ast
import json
import sys
from collections import defaultdict
from pathlib import Path


def model_names(cls: ast.ClassDef) -> list[str]:
    names = []
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            key = stmt.targets[0].id
            if key in ("_name", "_inherit"):
                v = stmt.value
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    names.append(v.value)
                elif isinstance(v, ast.List | ast.Tuple):
                    names += [e.value for e in v.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return names


def _field_assign(stmt) -> tuple[str, str] | None:
    """`x = fields.Y(...)` or `x: T = fields.Y(...)`."""
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
        target, value = stmt.targets[0].id, stmt.value
    elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
        target, value = stmt.target.id, stmt.value
    else:
        return None
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and isinstance(value.func.value, ast.Name)
        and value.func.value.id == "fields"
    ):
        return target, value.func.attr
    return None


def collect(root: Path) -> tuple[dict[str, dict[str, str]], set[str]]:
    """Fields per model, including fields inherited from mixins and parents named in _inherit."""
    own: dict[str, dict[str, str]] = defaultdict(dict)
    parents: dict[str, set[str]] = defaultdict(set)
    models: set[str] = set()
    for path in root.rglob("*.py"):
        if "tests" in path.parts or "upgrade_code" in path.parts or "migrations" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            names = model_names(cls)
            if not names:
                continue
            models.update(names)
            own_name = _own_name(cls)
            target_models = [own_name] if own_name else names
            for m in target_models:
                parents[m].update(n for n in names if n != m)
            for stmt in cls.body:
                hit = _field_assign(stmt)
                if hit:
                    for m in target_models:
                        own[m][hit[0]] = hit[1]
    fields: dict[str, dict[str, str]] = {}

    def resolve(model: str, seen: frozenset = frozenset()) -> dict[str, str]:
        if model in fields:
            return fields[model]
        result: dict[str, str] = {}
        for parent in parents.get(model, ()):
            if parent not in seen:
                result.update(resolve(parent, seen | {model}))
        result.update(own.get(model, {}))
        if not seen:
            fields[model] = result
        return result

    for m in models:
        resolve(m)
    return fields, models


def _own_name(cls: ast.ClassDef) -> str | None:
    for stmt in cls.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "_name"
            and isinstance(stmt.value, ast.Constant)
        ):
            return stmt.value.value
    return None


def _similar(name: str, ftype: str, added: dict[str, str]) -> list[str]:
    """Added fields of the same type that share a word with the removed one (e.g. product_uom -> uom_id)."""
    words = {w for w in name.split("_") if w not in ("id", "ids", "is", "l10n")}
    return sorted(a for a, t in added.items() if t == ftype and words & set(a.split("_")))


def main(old_root: str, new_root: str, out: str):
    old_fields, _ = collect(Path(old_root))
    new_fields, new_models = collect(Path(new_root))
    removed_models = sorted(m for m in old_fields if m not in new_models)
    removed = {}
    for model, fields in sorted(old_fields.items()):
        if model not in new_models:
            continue
        gone = {f: t for f, t in fields.items() if f not in new_fields.get(model, {}) and not f.startswith("_")}
        if gone:
            added = {f: t for f, t in new_fields.get(model, {}).items() if f not in fields}
            removed[model] = {
                f: {"type": t, "possible_replacements": _similar(f, t, added)} for f, t in sorted(gone.items())
            }

    def module_names(root):
        return {
            m.parent.name for sub in ("addons", "odoo/addons") for m in (Path(root) / sub).glob("*/__manifest__.py")
        }

    removed_modules = sorted(module_names(old_root) - module_names(new_root))
    data = {
        "source": {"old": old_root, "new": new_root},
        "removed_models": removed_models,
        "removed_fields": removed,
        "removed_modules": removed_modules,
    }
    Path(out).write_text(json.dumps(data, indent=1, sort_keys=True))
    print(
        f"{len(removed_models)} removed models, {sum(len(v) for v in removed.values())} removed fields on "
        f"{len(removed)} models -> {out}"
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "src/odoo_migrate/catalog/removed_fields.json")
