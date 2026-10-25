"""Run-wide settings shared by rules (set by the runner from the command line)."""

from pathlib import Path

ODOO_SRC: Path | None = None  # Odoo 20 source checkout; enables rules that compare against the real 20 code
