"""Core types: a module on disk, findings, file changes, and the rule base class."""

import ast
import difflib
from dataclasses import dataclass, field
from pathlib import Path

SEVERITIES = ("error", "warning", "info")  # error: breaks on Odoo 20; warning: deprecated; info: worth a look


@dataclass
class Module:
    path: Path

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def manifest_path(self) -> Path:
        return self.path / "__manifest__.py"

    @property
    def manifest(self) -> dict:
        try:
            return ast.literal_eval(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, SyntaxError):
            return {}

    def files(self, *suffixes: str) -> list[Path]:
        skip = {".git", "__pycache__", "node_modules", ".odoo-migrate-backup"}
        return sorted(
            p
            for p in self.path.rglob("*")
            if p.is_file() and p.suffix in suffixes and not skip.intersection(p.relative_to(self.path).parts)
        )

    def rel(self, path: Path) -> str:
        return str(path.relative_to(self.path))


@dataclass
class Finding:
    rule: str
    severity: str
    file: str
    line: int
    message: str
    fixable: bool = False
    snippet: str = ""


@dataclass
class FileChange:
    path: Path
    new_text: str | None  # None deletes the file
    old_text: str | None = None  # None creates the file

    def diff(self, root: Path) -> str:
        rel = str(self.path.relative_to(root))
        old = (self.old_text or "").splitlines(keepends=True)
        new = (self.new_text or "").splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(
                old,
                new,
                f"a/{rel}" if self.old_text is not None else "/dev/null",
                f"b/{rel}" if self.new_text is not None else "/dev/null",
            )
        )


@dataclass
class Rule:
    """A migration rule: detect, explain, and optionally fix one Odoo 19 -> 20 change.

    `safe` fixes are applied by `odoo-migrate fix`; unsafe fixes are only shown as diffs.
    """

    id: str = ""
    title: str = ""
    severity: str = "error"
    explanation: str = ""
    evidence: list[str] = field(default_factory=list)  # paths in the Odoo 20 source that show the change
    safe: bool = False

    def scan(self, module: Module) -> list[Finding]:
        raise NotImplementedError

    def fix(self, module: Module) -> list[FileChange]:
        return []

    def finding(
        self, module: Module, path: Path, line: int, message: str, snippet: str = "", fixable: bool | None = None
    ) -> Finding:
        return Finding(
            self.id,
            self.severity,
            module.rel(path),
            line,
            message,
            self.safe if fixable is None else fixable,
            snippet.strip()[:200],
        )


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1
