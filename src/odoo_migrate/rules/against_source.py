"""Rules that compare a module against the real Odoo 20 source (active when --odoo-src is given).

VIEW001: an inherited view's xpath / locator no longer matches anything in the Odoo 20 parent view.
PY011:   an `odoo...` import no longer resolves in Odoo 20 (module or name moved/removed), and odoo.http.root.
"""

import ast
from collections import defaultdict
from functools import cache
from pathlib import Path

import libcst as cst
from lxml import etree

from .. import context
from ..engine import Rule
from ..pyutil import PythonRule, dotted
from .xml_data import parse_xml, server_xml

# Odoo registers hasclass() for view xpaths; lxml needs it to evaluate them.
etree.FunctionNamespace(None)["hasclass"] = lambda ctx, *classes: all(
    c in (ctx.context_node.get("class") or "").split() for c in classes
)


@cache
def view_index(odoo_src: Path) -> tuple[dict[str, list], dict[str, list]]:
    """(xmlid -> [arch], parent xmlid -> [archs of views inheriting it]) for all server XML in Odoo 20."""
    archs: dict[str, list] = defaultdict(list)
    children: dict[str, list] = defaultdict(list)
    roots = [odoo_src / "addons", odoo_src / "odoo" / "addons"]
    for root in roots:
        if not root.is_dir():
            continue
        for module_dir in root.iterdir():
            if not (module_dir / "__manifest__.py").exists():
                continue
            for path in module_dir.rglob("*.xml"):
                if {"static", "i18n", "tests"} & set(path.relative_to(module_dir).parts):
                    continue
                _index_file(path, module_dir.name, archs, children)
    return dict(archs), dict(children)


def _qualify(ref: str | None, module: str) -> str | None:
    if not ref:
        return None
    return ref if "." in ref else f"{module}.{ref}"


def _index_file(path: Path, module: str, archs: dict, children: dict) -> None:
    tree = parse_xml(path)
    if tree is None:
        return
    for el in tree.iter("record", "template"):
        if el.tag == "record" and el.get("model") != "ir.ui.view":
            continue
        xmlid = _qualify(el.get("id"), module)
        if el.tag == "record":
            arch_field = el.find("field[@name='arch']")
            arch = arch_field if arch_field is not None else None
            inherit = el.find("field[@name='inherit_id']")
            parent = _qualify(inherit.get("ref"), module) if inherit is not None else None
        else:
            arch = el
            parent = _qualify(el.get("inherit_id"), module)
        if arch is None or xmlid is None:
            continue
        archs[xmlid].append(arch)
        if parent:
            children[parent].append(arch)


def _candidates(parent: str, archs: dict, children: dict, depth: int = 0) -> list:
    """The parent's arch, its own ancestors (when it is an extension) and every view extending it."""
    found = list(archs.get(parent, [])) + list(children.get(parent, []))
    if depth < 4:
        for arch in archs.get(parent, []):
            if arch.tag == "field":  # <record model="ir.ui.view">: the arch field's record carries inherit_id
                inherit = arch.getparent().find("field[@name='inherit_id']")
                ref = inherit.get("ref") if inherit is not None else None
            else:  # <template inherit_id="...">
                ref = arch.get("inherit_id")
            if ref:
                found += _candidates(_qualify(ref, parent.split(".")[0]), archs, children, depth + 1)
    return found


def _locator_xpath(el) -> str | None:
    if el.tag == "xpath":
        return el.get("expr")
    if el.get("position") and el.get("name"):
        return f"//{el.tag}[@name='{el.get('name')}']"
    return None


def _matches(xpath: str, candidates: list) -> bool:
    for arch in candidates:
        try:
            if arch.xpath("." + xpath if xpath.startswith("//") else xpath):
                return True
        except etree.XPathError:
            return True  # an expression lxml cannot evaluate is not reported
    return False


class InheritedViewRule(Rule):
    def scan(self, module):
        if context.ODOO_SRC is None:
            return []
        archs, children = view_index(context.ODOO_SRC)
        own: dict[str, list] = defaultdict(list)
        own_children: dict[str, list] = defaultdict(list)
        for path in server_xml(module):
            _index_file(path, module.name, own, own_children)
        out = []
        for path in server_xml(module):
            tree = parse_xml(path)
            if tree is None:
                continue
            for el in tree.iter("record", "template"):
                if el.tag == "record":
                    inherit = el.find("field[@name='inherit_id']")
                    parent = inherit.get("ref") if inherit is not None else None
                    body = el.find("field[@name='arch']")
                else:
                    parent, body = el.get("inherit_id"), el
                if not parent or "." not in parent or body is None or parent.split(".")[0] == module.name:
                    continue
                if parent not in archs:
                    out.append(
                        self.finding(
                            module, path, el.sourceline or 1, f"parent view {parent} does not exist in Odoo 20", parent
                        )
                    )
                    continue
                cands = _candidates(parent, archs, children) + own_children.get(parent, [])
                specs = body if body.tag != "field" else (body[0] if len(body) else None)
                if specs is None:
                    continue
                for spec in specs if specs.tag == "data" or el.tag == "template" else [specs]:
                    xpath = _locator_xpath(spec) if isinstance(spec.tag, str) else None
                    if xpath and not _matches(xpath, cands):
                        out.append(
                            self.finding(
                                module,
                                path,
                                spec.sourceline or 1,
                                f"{xpath} matches nothing in the Odoo 20 view {parent} (or the "
                                "views extending it); the parent view changed",
                                xpath,
                            )
                        )
        return out


@cache
def _module_names(path: Path) -> set[str] | None:
    """Top-level names a Python module defines (None when it is too dynamic to tell)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
            if node.name == "__getattr__":
                return None
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    return None
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.If | ast.Try):
            return None  # conditional definitions: do not guess
    if path.name == "__init__.py":
        names |= {p.stem for p in path.parent.iterdir() if p.suffix == ".py" or (p / "__init__.py").exists()}
    return names


def _resolve(dotted_name: str, src: Path) -> Path | None:
    parts = dotted_name.split(".")
    if parts[:2] == ["odoo", "addons"] and len(parts) > 2:
        bases = [src / "addons" / parts[2], src / "odoo" / "addons" / parts[2]]
        rest = parts[3:]
    else:
        bases, rest = [src / "odoo"], parts[1:]
    for base in bases:
        target = base.joinpath(*rest)
        if (target / "__init__.py").exists():
            return target / "__init__.py"
        if target.with_suffix(".py").exists():
            return target.with_suffix(".py")
        if target.is_dir():
            return target  # namespace package (e.g. `odoo` itself in 20): exists, names not checked
    return None


def _known_root(dotted_name: str, src: Path) -> bool:
    """Only check imports into code that Odoo 20 Community itself provides."""
    parts = dotted_name.split(".")
    if parts[:2] == ["odoo", "addons"]:
        return len(parts) > 2 and any(
            (r / parts[2] / "__manifest__.py").exists() for r in (src / "addons", src / "odoo" / "addons")
        )
    return parts[0] == "odoo"


class ImportResolutionRule(PythonRule):
    def match(self, node):
        if isinstance(node, cst.Attribute) and dotted(node) in ("http.root", "odoo.http.root"):
            return "odoo.http.root moved: use `from odoo.http.router import root`"
        src = context.ODOO_SRC
        if src is None or not isinstance(node, cst.ImportFrom) or node.relative or node.module is None:
            return None
        mod = dotted(node.module)
        own = getattr(self, "current_module", None)
        if own and (mod == f"odoo.addons.{own.name}" or mod.startswith(f"odoo.addons.{own.name}.")):
            return None  # the module's own package
        if not mod.startswith("odoo") or not _known_root(mod, src):
            return None
        target = _resolve(mod, src)
        if target is None:
            return f"module {mod} does not exist in Odoo 20"
        if isinstance(node.names, cst.ImportStar):
            return None
        defined = _module_names(target) if target.is_file() else None
        if defined is None:
            return None
        missing = [dotted(a.name) for a in node.names if dotted(a.name) not in defined]
        return f"{', '.join(missing)} not found in {mod} in Odoo 20" if missing else None


RULES = [
    InheritedViewRule(
        id="VIEW001",
        title="Inherited view no longer matches its Odoo 20 parent",
        severity="error",
        explanation="Evaluates each xpath / field locator of views that inherit a standard view against the Odoo 20 "
        "parent view, its ancestors and every Odoo 20 view extending it. Needs --odoo-src.",
        evidence=["Odoo 20 view definitions"],
    ),
    ImportResolutionRule(
        id="PY011",
        title="Import does not resolve in Odoo 20",
        severity="error",
        explanation="Checks every `from odoo... import ...` against the Odoo 20 source (needs --odoo-src). "
        "Also flags odoo.http.root, which moved to odoo.http.router when odoo/http.py became a package.",
        evidence=["odoo/http/router.py"],
    ),
]
