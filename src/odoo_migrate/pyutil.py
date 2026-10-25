"""Helpers for Python rules built on libcst: parse once, find nodes with line numbers, rewrite safely."""

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

from .engine import FileChange, Finding, Module, Rule


@lru_cache(maxsize=256)
def _parse_cached(path: str, mtime_ns: int, size: int) -> tuple[str, MetadataWrapper | None]:
    text = Path(path).read_text(encoding="utf-8")
    try:
        return text, MetadataWrapper(cst.parse_module(text))
    except cst.ParserSyntaxError:
        return text, None


def parse(path: Path) -> tuple[str, MetadataWrapper | None]:
    """Parse once per file version: every Python rule reuses the tree (the cache key includes mtime and size)."""
    st = path.stat()
    return _parse_cached(str(path), st.st_mtime_ns, st.st_size)


def dotted(node: cst.BaseExpression | None) -> str:
    """'a.b.c' for Name/Attribute chains, '' otherwise."""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        base = dotted(node.value)
        return f"{base}.{node.attr.value}" if base else node.attr.value
    return ""


def code(node: cst.CSTNode) -> str:
    return cst.Module([]).code_for_node(node)


class _Collector(cst.CSTVisitor):
    """One pass over a file for several rules: hits are (rule index, line, message, code)."""

    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, matchers: list[Callable[[cst.CSTNode], str | None]]):
        self.matchers = matchers
        self.hits: list[tuple[int, int, str, str]] = []

    def on_visit(self, node: cst.CSTNode) -> bool:
        for i, match in enumerate(self.matchers):
            message = match(node)
            if message:
                pos = self.get_metadata(PositionProvider, node)
                self.hits.append((i, pos.start.line, message, code(node)))
        return True


def scan_python(rules: list["PythonRule"], module: Module) -> list[Finding]:
    """Scan all Python files once for all given rules."""
    findings = []
    for rule in rules:
        rule.current_module = module
    for path in module.files(".py"):
        _text, wrapper = parse(path)
        if wrapper is None:
            continue
        collector = _Collector([r.match for r in rules])
        wrapper.visit(collector)
        for i, line, message, snippet in collector.hits:
            rule = rules[i]
            findings.append(rule.finding(module, path, line, message, snippet, rule.fixable(snippet)))
    return findings


class PythonRule(Rule):
    """Subclasses implement `match(node) -> message | None` and optionally `transformer() -> CSTTransformer`."""

    def match(self, node: cst.CSTNode) -> str | None:
        raise NotImplementedError

    def transformer(self) -> cst.CSTTransformer | None:
        return None

    def fixable(self, node_code: str) -> bool:
        """Whether a specific hit will be rewritten by `fix` (default: all hits when the rule is safe)."""
        return self.safe

    def scan(self, module: Module) -> list[Finding]:
        return scan_python([self], module)

    def fix(self, module: Module) -> list[FileChange]:
        changes = []
        for path in module.files(".py"):
            text, wrapper = parse(path)
            transformer = self.transformer()
            if wrapper is None or transformer is None:
                continue
            new = wrapper.module.visit(transformer).code
            new = self.post_fix(new, text)
            if new != text:
                changes.append(FileChange(path, new, text))
        return changes

    def post_fix(self, new: str, old: str) -> str:
        """Hook for import edits after the tree rewrite."""
        return new


def ensure_import(source: str, line: str) -> str:
    """Add an import line after the last top-level import, if not already present."""
    if line in source:
        return source
    lines = source.splitlines(keepends=True)
    last = -1
    for i, raw in enumerate(lines):
        if raw.startswith(("import ", "from ")) and not raw.startswith("from . "):
            last = i
    insert_at = last + 1 if last >= 0 else 0
    lines.insert(insert_at, line + "\n")
    return "".join(lines)
