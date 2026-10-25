# What breaks when a custom module moves from Odoo 19 to Odoo 20

Built by comparing the Odoo 19.0 and 20.0 Community source code (19.0 at commit `6394ce4b7`, 2026-08-29;
20.0 at commit `73041605`, 2026-10-06), and by measuring which changes Odoo's developers applied across the 584
modules that exist in both versions. Paths are relative to the Odoo source root. Enterprise was not examined.

**Severity:**
- **Break**: install or update fails, or code raises an error.
- **Silent**: no error, but behaviour or display changes.
- **Deprecated**: works with a warning.

**Rule:** the `odoo-migrate` rule that detects the change. *auto* means it can be fixed automatically.

## Odoo now ships its own source upgrade scripts

Odoo 20 contains `odoo/upgrade_code/`, run with `odoo-bin upgrade_code`. The scripts relevant to 19 → 20 are:

| Script | What it rewrites |
|---|---|
| `19.4-00-ir-access` | `ir.model.access.csv` + `ir.rule` records → `security/ir.access.csv` |
| `19.3-00-base64-in-xml` | `type="base64"` → `type="bytes"` in XML |
| `19.1-00-t-call` | `t-call` template syntax |
| `19.4-00-ormcache-on-transaction` | `registry.clear_cache` → `transaction.invalidate_ormcache` |
| `19.5-00-tuple-rec_names_search` | `_rec_names_search` lists → tuples |
| `20.0-00-search-date-filters` | removes `start_month`/`end_month`/`start_year`/`end_year` from date filters |

Two more scripts (`19.3-00-account-groups`, `19.3-00-account-report-foldable`) only apply to Odoo's own data.

**Caution:** when started through `odoo-bin`, `upgrade_code` replaces `--addons-path` with `odoo.addons.__path__`.
That path always includes Odoo's own addons and the user's data directory, so the command rewrites those too. Run it on
a throwaway copy of the source, never on a working checkout. `odoo-migrate` does this automatically.

The scripts do not cover most of the changes below. `odoo-migrate` runs them, then applies its own rules.

## Security

| Change | 19 | 20 | Severity | Rule |
|---|---|---|---|---|
| `ir.model.access` and `ir.rule` models removed | `security/ir.model.access.csv`, `<record model="ir.rule">` | `security/ir.access.csv`: `id,name,model_id,group_id/id,operation,domain` | Break | SEC001 (Odoo script) |
| Access line semantics | access line + group rule narrowing it | one line per group; a line with a domain *is* the grant; a line without a group is a global restriction | Break | SEC001 |
| `res.groups.model_access`, `rule_groups` | fields | `access_ids` | Break | PY010 |

Example from Odoo's `sale` module:
- 19: `access_sale_order,...,sales_team.group_sale_salesman,1,1,1,0` plus the rule `sale_order_personal_rule`
- 20: `sale_order_personal_rule,Personal Orders,sale.order,sales_team.group_sale_salesman,cru,"['|',('user_id','=',user.id),('user_id','=',False)]"`

Odoo converted 204 modules' access files and removed 103 rule files.

## Python API

| Change | 19 | 20 | Severity | Rule |
|---|---|---|---|---|
| System parameters | `get_param(key, default)`, `set_param` | `get_str/get_int/get_float/get_bool`, `set_str/...` (`get_str` returns `''`, not `False`) | Break | PY001 (auto for string values) |
| `odoo.osv` package | `from odoo.osv import expression` | `from odoo.fields import Domain`; `Domain.AND/OR/TRUE/FALSE` | Break | PY002 (auto for AND/OR/TRUE/FALSE) |
| `read_group` | `read_group(domain, fields, groupby, ...)` → list of dicts | old `_read_group` signature → tuples | Break | PY003 (manual) |
| Access checks | `check_access_rights`, `check_access_rule`, `_filter_access_rules(_python)` | `check_access`, `has_access`, `_filtered_access` | Break | PY004 (auto) |
| Recursion check | `_check_recursion(parent)` (True = no cycle) | `_has_cycle(field_name)` (True = cycle) | Break | PY005 (auto, negated) |
| `toggle_active` | method | `action_archive` / `action_unarchive` | Break | PY003 (manual) |
| `api.deprecated` | `odoo.api.deprecated` | `odoo.tools.func.deprecated` | Break | PY006 (auto) |
| `odoo.tools` exports | SQL helpers, `Query`, `ormcache` re-exported | import from `odoo.tools.sql`, `odoo.models`, `odoo.api` | Break | PY007 (auto) |
| Removed helpers | `tools.pycompat`, `flatten`, `create_unique_index`, `pg_varchar`, `check_method_name`, `SingleTransactionCase`, `suppress_traceback()` | removed | Break | PY007 / PY003 (manual) |
| `group_operator=` | converted with a warning | ignored: aggregation silently lost | Silent | PY008 (auto) |
| `self._cr/_uid/_context` | deprecated | still deprecated | Deprecated | PY009 (auto) |
| `_check_access` override | supported | deprecated; override `_access_domain` | Deprecated | PY003 |

## Views, QWeb and data

| Change | 19 | 20 | Severity | Rule |
|---|---|---|---|---|
| QWeb `t-esc`, `t-raw` | directives | removed from server QWeb (`t-out` only) | Break | XML001 (auto), XML002 (manual) |
| Date filter attributes | `start_month`, `end_month`, `start_year`, `end_year` | rejected by the view schema | Break | XML003 (Odoo script) |
| Calendar `date_delay` | attribute | removed (use `date_stop`) | Break | XML005 (manual) |
| `ir.actions.report.report_file` | field | removed | Break | XML007 (auto) |
| Font Awesome | `fa fa-*` bundled | not bundled. Elements: `class="oi" data-icon=...`. Button `icon=` attributes: a Material Symbols name from the 482 that Odoo 20 ships (`web/tooling/icons/icons_wishlist.txt`), e.g. `icon="check"`. `icon="oi-..."` is not used by Odoo 20 buttons either | Silent (blank icons) | XML006 (manual) |
| `type="base64"` in XML | supported | deprecated, use `type="bytes"` | Deprecated | XML004 (Odoo script) |

## Models and fields removed from standard modules

`scripts/build_field_catalog.py` compares every model's fields (including inherited mixins) in both versions:
- **79 models** removed, including `res.bank`, `account.group`, `ir.rule` and `ir.model.access`.
- **999 fields** removed from 295 models that still exist.

Examples verified by hand in the source:
- `res.partner.company_type` and `company_registry` are gone; `is_company` is now computed.
- `stock.move.product_uom` and `purchase.order.line.product_uom_id` became `uom_id`.
- `portal` no longer provides `website_message_ids`.

Rules FLD001 (views), FLD002 (Python) and FLD003 (removed models) use this catalog. Renames are only *suggested*
(same field type and a shared word), never applied.

## Manifest

| Change | Severity | Rule |
|---|---|---|
| Version `19.0.x` → module not installable on 20 | Break | MAN001 (auto → `20.0.x`) |
| `init_xml`, `update_xml`, `demo_xml` ignored | Silent (files not loaded) | MAN001 (auto → `data`/`demo`) |
| 50 modules removed or merged (`base_vat`, `base_iban`, `hr_org_chart`, `l10n_sa_withholding_tax`, ...) | Break | MAN001 (manual) |
| Asset bundles removed (`web._assets_jquery`, `web.qunit_suite_tests`, ...) | Break | MAN001 (manual) |

## Already required by 19, so not a 19 → 20 change

These were measured in 0 modules of the 19 → 20 diff:
- `_sql_constraints` → `models.Constraint`
- `<tree>` → `<list>`
- `attrs=`
- `name_get`
- `groups_id` → `group_ids`
- `numbercall` / `doall` on crons

## Not covered

- JavaScript / Owl changes (Odoo ships an `owl3-migration` script for those).
- Database data migration.
- Enterprise modules.
