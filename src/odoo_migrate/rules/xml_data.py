"""XML data, views, QWeb, security and manifest changes between Odoo 19 and 20."""

import ast
import json
import re
from functools import cache
from importlib import resources
from pathlib import Path

from lxml import etree

from ..engine import FileChange, Module, Rule, line_of


@cache
def catalog() -> dict:
    return json.loads(resources.files("odoo_migrate.catalog").joinpath("removed_fields.json").read_text())


def parse_xml(path: Path):
    try:
        return etree.parse(str(path), etree.XMLParser(remove_comments=False, recover=False))
    except (etree.XMLSyntaxError, OSError):
        return None


def server_xml(module: Module) -> list[Path]:
    """XML loaded by the server (static/ holds client-side Owl templates, a different engine)."""
    return [p for p in module.files(".xml") if "static" not in p.relative_to(module.path).parts]


class RegexXmlRule(Rule):
    """Find a regex in server-side XML; optional safe text substitution."""

    def __init__(self, pattern: str, message: str, replace: tuple[str, str] | None = None, **kw):
        super().__init__(**kw)
        self.pattern = re.compile(pattern)
        self.message = message
        self.replace = replace

    def scan(self, module):
        out = []
        for path in server_xml(module):
            text = path.read_text(encoding="utf-8")
            for m in self.pattern.finditer(text):
                out.append(self.finding(module, path, line_of(text, m.start()), self.message.format(m=m), m.group(0)))
        return out

    def fix(self, module):
        if not (self.safe and self.replace):
            return []
        changes = []
        for path in server_xml(module):
            text = path.read_text(encoding="utf-8")
            new = re.sub(self.replace[0], self.replace[1], text)
            if new != text:
                changes.append(FileChange(path, new, text))
        return changes


class OfficialScriptRule(RegexXmlRule):
    """Detected here, fixed by Odoo's own `odoo-bin upgrade_code` script (run by `odoo-migrate fix --odoo-src`)."""

    def __init__(self, script: str, *a, **kw):
        super().__init__(*a, **kw)
        self.script = script


class OldSecurityRule(Rule):
    """ir.model.access.csv and ir.rule records: the models no longer exist."""

    script = "19.4-00-ir-access"

    def scan(self, module):
        out = []
        for path in module.files(".csv"):
            if path.name.endswith("ir.model.access.csv"):
                out.append(
                    self.finding(
                        module,
                        path,
                        1,
                        "ir.model.access.csv is not loaded in 20 (model removed); convert to security/ir.access.csv",
                    )
                )
        for path in server_xml(module):
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r"""model=["'](ir\.rule|ir\.model\.access)["']""", text):
                out.append(
                    self.finding(
                        module,
                        path,
                        line_of(text, m.start()),
                        f"{m.group(1)} records fail to load in 20; convert to ir.access rows",
                    )
                )
        return out


class ManifestRule(Rule):
    """Manifest checks: version series, removed keys, removed asset bundles."""

    REMOVED_BUNDLES = (
        "web._assets_jquery",
        "web.qunit_suite_tests",
        "website.assets_all_wysiwyg",
        "spreadsheet.assets_print",
        "im_livechat.qunit_embed_suite",
    )
    MOVED_KEYS = {"init_xml": "data", "update_xml": "data", "demo_xml": "demo"}

    def scan(self, module):
        path = module.manifest_path
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        m = module.manifest
        out = []
        version = str(m.get("version", ""))
        if version.startswith("19.0"):
            out.append(
                self.finding(
                    module,
                    path,
                    line_of(text, text.find(version)),
                    f"version {version} is for the 19.0 series; Odoo 20 marks the module not installable. Use 20.0.x.y",
                    version,
                    True,
                )
            )
        for key, target in self.MOVED_KEYS.items():
            if key in m:
                out.append(
                    self.finding(
                        module,
                        path,
                        line_of(text, text.find(f"'{key}'") if f"'{key}'" in text else text.find(f'"{key}"')),
                        f"'{key}' is ignored in 20 (files silently not loaded); move them to '{target}'",
                        key,
                        True,
                    )
                )
        gone = set(catalog().get("removed_modules", []))
        for dep in m.get("depends", []):
            if dep in gone:
                out.append(
                    self.finding(
                        module,
                        path,
                        line_of(text, text.find(f"'{dep}'") if f"'{dep}'" in text else text.find(f'"{dep}"')),
                        f"depends on '{dep}', which does not exist in Odoo 20 Community (removed or "
                        "merged into another module); the module cannot be installed",
                        dep,
                        False,
                    )
                )
        for bundle in self.REMOVED_BUNDLES:
            if bundle in text:
                out.append(
                    self.finding(
                        module,
                        path,
                        line_of(text, text.find(bundle)),
                        f"asset bundle {bundle} does not exist in 20",
                        bundle,
                        False,
                    )
                )
        return out

    def fix(self, module):
        path = module.manifest_path
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        new = re.sub(r"""(['"]version['"]\s*:\s*['"])19\.0(\.)""", r"\g<1>20.0\2", text)
        manifest = module.manifest
        moves = {k: v for k, v in self.MOVED_KEYS.items() if k in manifest}
        if moves:
            try:
                tree = ast.parse(new)
                d = tree.body[0].value
            except (SyntaxError, IndexError, AttributeError):
                d = None
            if isinstance(d, ast.Dict):
                merged = dict(manifest)
                for old, target in moves.items():
                    merged[target] = list(merged.get(target, [])) + [
                        f for f in merged.pop(old) if f not in merged.get(target, [])
                    ]
                new = _rewrite_manifest_lists(new, d, merged, moves)
        return [FileChange(path, new, text)] if new != text else []


def _rewrite_manifest_lists(text: str, d: ast.Dict, merged: dict, moves: dict) -> str:
    """Remove the moved keys and write the merged target lists, editing only those entries."""
    lines = text.splitlines(keepends=True)
    spans = []  # (start_line, end_line, key)
    for k, v in zip(d.keys, d.values, strict=True):
        if isinstance(k, ast.Constant) and k.value in set(moves) | set(moves.values()):
            spans.append((k.lineno, v.end_lineno, k.value))
    for start, end, key in sorted(spans, reverse=True):
        indent = re.match(r"\s*", lines[start - 1]).group(0)
        if key in moves:
            del lines[start - 1 : end]
            # drop a trailing comma line left behind
            continue
        items = "".join(f"{indent}    {f!r},\n" for f in merged[key])
        lines[start - 1 : end] = [f"{indent}{key!r}: [\n{items}{indent}],\n"]
    present = {s[2] for s in spans}
    for target in set(moves.values()) - present:
        close = max(i for i, raw in enumerate(lines) if raw.strip().startswith("}"))
        items = "".join(f"        {f!r},\n" for f in merged[target])
        lines.insert(close, f"    {target!r}: [\n{items}    ],\n")
    return "".join(lines)


class ViewFieldRule(Rule):
    """Fields used in views (and in xpath expressions) that no longer exist on the view's model in 20."""

    def scan(self, module):
        removed = catalog()["removed_fields"]
        out = []
        for path in server_xml(module):
            tree = parse_xml(path)
            if tree is None:
                continue
            for record in tree.iter("record"):
                if record.get("model") != "ir.ui.view":
                    continue
                model_el = record.find("field[@name='model']")
                model = (model_el.text or "").strip() if model_el is not None else ""
                gone = removed.get(model)
                arch = record.find("field[@name='arch']")
                if not gone or arch is None:
                    continue
                for el in arch.iter():
                    names = []
                    if el.tag == "field" and el.get("name"):
                        names.append(el.get("name"))
                    for attr in ("expr",):
                        names += re.findall(r"@name\s*=\s*['\"](\w+)['\"]", el.get(attr) or "")
                    for name in names:
                        if name in gone:
                            msg = f"{model}.{name} does not exist in Odoo 20" + _hint(model, name, gone[name])
                            out.append(
                                self.finding(
                                    module,
                                    path,
                                    el.sourceline or 1,
                                    msg,
                                    etree.tostring(el, encoding="unicode").split(">")[0] + ">",
                                )
                            )
        return out


class RemovedModelXmlRule(Rule):
    """XML that targets models removed in 20 (records, refs, views)."""

    def scan(self, module):
        removed = set(catalog()["removed_models"]) - {"ir.rule", "ir.model.access"}  # handled by SEC001
        removed.add("res.bank")
        out = []
        for path in server_xml(module):
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r"""(?:model=["']|<field name=["'](?:model|res_model)["']>\s*)([\w.]+)""", text):
                if m.group(1) in removed:
                    out.append(
                        self.finding(
                            module,
                            path,
                            line_of(text, m.start()),
                            f"model {m.group(1)} was removed in Odoo 20",
                            m.group(0),
                        )
                    )
        return out


class RemovedFieldPythonRule(Rule):
    """self.<field> / depends('<field>') in classes extending a model that lost that field in 20."""

    def scan(self, module):
        removed = catalog()["removed_fields"]
        removed_models = set(catalog()["removed_models"]) | {"res.bank"}
        out = []
        for path in module.files(".py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for cls in ast.walk(tree):
                if not isinstance(cls, ast.ClassDef):
                    continue
                models = _class_models(cls)
                for model in models:
                    if model in removed_models:
                        out.append(
                            self.finding(
                                module, path, cls.lineno, f"model {model} was removed in Odoo 20", f"class {cls.name}"
                            )
                        )
                own = {
                    t.id for s in cls.body if isinstance(s, ast.Assign) for t in s.targets if isinstance(t, ast.Name)
                }
                gone = {}
                for model in models:
                    gone.update({k: {**v, "model": model} for k, v in removed.get(model, {}).items() if k not in own})
                if not gone:
                    continue
                for node in ast.walk(cls):
                    name = None
                    if (
                        isinstance(node, ast.Attribute)
                        and isinstance(node.value, ast.Name)
                        and node.value.id in ("self", "record", "rec")
                        and node.attr in gone
                    ):
                        name = node.attr
                    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                        head = node.value.split(".")[0]
                        if head in gone and re.fullmatch(r"[\w.]+", node.value):
                            name = head
                    if name:
                        model = gone[name]["model"]
                        msg = f"{model}.{name} does not exist in Odoo 20" + _hint(model, name, gone[name])
                        out.append(self.finding(module, path, node.lineno, msg, name))
        return out


KNOWN_REPLACEMENTS = {
    ("ir.attachment", "datas"): "raw (bytes; datas held base64)",
    ("res.partner", "company_type"): "is_company (now computed)",
    ("stock.move", "product_uom"): "uom_id",
    ("purchase.order.line", "product_uom_id"): "uom_id",
}


def _hint(model: str, field: str, info: dict) -> str:
    if (model, field) in KNOWN_REPLACEMENTS:
        return f" (use {KNOWN_REPLACEMENTS[(model, field)]})"
    if info.get("possible_replacements"):
        return f" (possibly replaced by {', '.join(info['possible_replacements'][:3])})"
    return ""


def _class_models(cls: ast.ClassDef) -> list[str]:
    names = []
    for stmt in cls.body:
        if (
            isinstance(stmt, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_inherits" for t in stmt.targets)
            and isinstance(stmt.value, ast.Dict)
        ):
            names += [k.value for k in stmt.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in ("_name", "_inherit") for t in stmt.targets
        ):
            v = stmt.value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                names.append(v.value)
            elif isinstance(v, ast.List | ast.Tuple):
                names += [e.value for e in v.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return list(dict.fromkeys(names))


class ReportFileRule(Rule):
    """ir.actions.report.report_file was removed; delete the <field name="report_file"> line."""

    PATTERN = re.compile(r"""[ \t]*<field name=["']report_file["'][^>]*?(?:/>|>[^<]*</field>)[ \t]*\n?""")

    def scan(self, module):
        out = []
        for path in server_xml(module):
            text = path.read_text(encoding="utf-8")
            for m in self.PATTERN.finditer(text):
                out.append(
                    self.finding(
                        module,
                        path,
                        line_of(text, m.start()),
                        "report_file no longer exists on ir.actions.report",
                        m.group(0),
                    )
                )
        return out

    def fix(self, module):
        changes = []
        for path in server_xml(module):
            text = path.read_text(encoding="utf-8")
            new = self.PATTERN.sub("", text)
            if new != text:
                changes.append(FileChange(path, new, text))
        return changes


RULES = [
    OldSecurityRule(
        id="SEC001",
        title="ir.model.access / ir.rule replaced by ir.access",
        severity="error",
        safe=False,
        explanation="Odoo 20 removed the ir.model.access and ir.rule models. Access rights and record rules are "
        "rows of security/ir.access.csv (id,name,model_id,group_id/id,operation,domain). Odoo ships a converter: "
        "`odoo-bin upgrade_code --script 19.4-00-ir-access`; `odoo-migrate fix --odoo-src` runs it. Review its "
        "warnings: rules on other modules' models and orphan rules are skipped.",
        evidence=["odoo/addons/base/models/ir_access.py", "odoo/upgrade_code/19.4-00-ir-access.py"],
    ),
    ManifestRule(
        id="MAN001",
        title="Manifest: version series, removed keys and bundles",
        severity="error",
        safe=True,
        explanation="A 19.0.x version makes the module not installable on 20 (use 20.0.x). init_xml/update_xml/"
        "demo_xml are silently ignored in 20; their files move to data/demo. Dependencies on the 50 modules that "
        "Odoo 20 removed or merged (e.g. base_vat, base_iban, hr_org_chart) block installation.",
        evidence=["odoo/modules/module.py", "odoo/modules/loading.py"],
    ),
    RegexXmlRule(
        r"\bt-esc=",
        "t-esc no longer exists in server-side QWeb; use t-out (also escapes)",
        replace=(r"\bt-esc=", "t-out="),
        id="XML001",
        title="QWeb t-esc removed",
        severity="error",
        safe=True,
        explanation="Odoo 20 server QWeb only compiles t-out/t-field: the esc and raw directives were removed. "
        "t-esc -> t-out keeps escaping. Client-side Owl templates under static/ are not affected.",
        evidence=["odoo/addons/base/models/ir_qweb.py"],
    ),
    RegexXmlRule(
        r"\bt-raw=",
        "t-raw no longer exists; use t-out with a Markup value (check the value is safe HTML)",
        id="XML002",
        title="QWeb t-raw removed (manual)",
        severity="error",
        explanation="t-raw printed unescaped HTML. t-out only prints HTML unescaped when the value is Markup, "
        "so the Python side may need markupsafe.Markup().",
        evidence=["odoo/addons/base/models/ir_qweb.py"],
    ),
    OfficialScriptRule(
        "20.0-00-search-date-filters",
        r"\b(?:start|end)_(?:month|year)=",
        "start_month/end_month/start_year/end_year are rejected by the Odoo 20 view schema",
        id="XML003",
        title="Search filter date-range attributes removed",
        severity="error",
        safe=False,
        explanation="Removed from common.rng. Fixed by Odoo's script 20.0-00-search-date-filters.",
        evidence=["odoo/addons/base/rng/common.rng", "odoo/upgrade_code/20.0-00-search-date-filters.py"],
    ),
    OfficialScriptRule(
        "19.3-00-base64-in-xml",
        r"""type=["']base64["']""",
        'type="base64" is deprecated; use type="bytes"',
        id="XML004",
        title="XML field type base64 deprecated",
        severity="warning",
        safe=False,
        explanation="Fixed by Odoo's script 19.3-00-base64-in-xml.",
        evidence=["odoo/tools/convert.py"],
    ),
    RegexXmlRule(
        r"""\bdate_delay=""",
        "calendar view attribute date_delay was removed; use date_stop with a stored end field",
        id="XML005",
        title="Calendar date_delay removed (manual)",
        severity="error",
        explanation="Removed from calendar_view.rng.",
        evidence=["odoo/addons/base/rng/calendar_view.rng"],
    ),
    RegexXmlRule(
        r"""\bfa fa-[\w-]+|\bicon=["']fa-[\w-]+""",
        "Font Awesome is no longer bundled in 20; '{m[0]}' will most likely render blank. For an icon= attribute "
        'use a Material Symbols name that Odoo 20 ships (e.g. icon="check", icon="close", icon="warning"; the full '
        'list is web/tooling/icons/icons_wishlist.txt). For <i>/<span> elements use class="oi" data-icon="..."',
        id="XML006",
        title="Font Awesome icons (manual)",
        severity="warning",
        explanation="Odoo 20 removed font-awesome.css from the asset bundles and replaced it with web.icons_fonts. "
        "Odoo's own XML went from 690 files using 'fa fa-' to 0. Icon names differ, so this is not automated.",
        evidence=["addons/web/__manifest__.py"],
    ),
    ReportFileRule(
        id="XML007",
        title="ir.actions.report report_file removed",
        severity="error",
        safe=True,
        explanation="The field no longer exists; the line is removed.",
        evidence=["odoo/addons/base/models/ir_actions_report.py"],
    ),
    ViewFieldRule(
        id="FLD001",
        title="View uses a field removed from a standard model",
        severity="error",
        explanation="Built from a comparison of every model's fields in Odoo 19 and 20 Community "
        "(scripts/build_field_catalog.py). Example: res.partner.company_type, stock.move.product_uom -> uom_id.",
        evidence=["src/odoo_migrate/catalog/removed_fields.json"],
    ),
    RemovedFieldPythonRule(
        id="FLD002",
        title="Python uses a field removed from a standard model",
        severity="error",
        explanation="self.<field> or a field name string in a class extending a model that lost that field.",
        evidence=["src/odoo_migrate/catalog/removed_fields.json"],
    ),
    RemovedModelXmlRule(
        id="FLD003",
        title="XML references a removed model",
        severity="error",
        explanation="Models removed in 20 include res.bank and account.group.",
        evidence=["src/odoo_migrate/catalog/removed_fields.json"],
    ),
]
