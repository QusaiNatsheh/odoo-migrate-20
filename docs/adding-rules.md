# Adding a rule

Each rule detects one Odoo 19 → 20 change, explains it, and optionally fixes it.

## 1. Confirm the change in the source first

Before writing code, find the evidence in the Odoo 20 source. Examples:
- a removed function
- a new error in `ir_ui_view.py`
- a changed `.rng` schema
- Odoo's own modules being changed the same way in 20.0

Write the evidence paths into the rule's `evidence` list. Do not add rules from release notes or memory alone.

## 2. Choose a base class

| Base class | File | Use for |
|---|---|---|
| `PythonRule` | `pyutil.py` | Python code. Implement `match(node) -> message or None` on libcst nodes. For a fix, implement `transformer()`. Files are parsed once for all rules. |
| `RegexXmlRule` | `rules/xml_data.py` | A regular expression in server-side XML (`static/` is skipped), with an optional text replacement |
| `Rule` | `engine.py` | Anything else. Implement `scan(module)` and optionally `fix(module)`. |

Rules that need the real Odoo 20 code read `context.ODOO_SRC`, and must return nothing when it is `None`.

## 3. Decide whether the fix is safe

Set `safe=True` only when the rewrite keeps behaviour identical in every case it touches. When only some cases
are safe:
- rewrite only those cases
- override `fixable()` so the report marks the other cases as manual

Examples:
- `get_param` with a string default is rewritten; with an integer default, it is not.
- `check_access_rights(op, raise_exception=x)` with a variable `x` is left alone.

## 4. Register it

Add the rule to the `RULES` list of a module in `src/odoo_migrate/rules/`. IDs must be unique. Use the prefix
of the area: `PY`, `XML`, `SEC`, `MAN`, `FLD` or `VIEW`.

## 5. Test it with "before" and "after" code

Every rule needs a test in `tests/`, using the `make_module` fixture:
- a case that must be found
- a case that must not be found (a false-positive guard)
- for fixes: the exact text after `fix`

`tests/test_runner.py::test_fix_is_idempotent` checks that running `fix` twice changes nothing the second time.

## 6. Measure it

When the rule matches a pattern, add it to `CHANGE_TYPES` in `scripts/evaluate_detection.py`. Then run the
evaluation against the Odoo 19 and 20 sources and look at the false-negative and false-positive modules it
lists. Investigate each one: it can be a gap in the rule or an error in the ground-truth definition.
