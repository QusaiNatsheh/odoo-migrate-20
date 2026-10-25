# LinkedIn posts: odoo-migrate-20 (drafts)

Drafts for review before posting. Replace `<repo link>` with the GitHub URL once the repository is public.
Every claim comes from `docs/change-catalog.md`, which was checked against the 19.0 and 20.0 Community source.

---

## Post 1: what breaks when you upgrade to Odoo 20

**Attach:** `docs/screenshots/scan_report.png`

What breaks when a custom module moves from Odoo 19 to Odoo 20? I compared the two Community source trees and the
584 modules they share. These are the changes most custom modules will hit:

1. **Access rights:** `ir.model.access.csv` and `ir.rule` are gone. Both become rows of `security/ir.access.csv`
   (id, name, model, group, operation "crud", domain). Odoo ships a converter: `odoo-bin upgrade_code`.
2. **Manifest version:** a module still at `19.0.x.y` is marked not installable. Use `20.0.x.y`.
3. **System parameters:** `get_param` / `set_param` are removed. Use `get_str`, `get_int`, `get_float`, `get_bool`.
   Note that `get_str` returns '' rather than False.
4. **QWeb:** `t-esc` and `t-raw` are gone from server templates. `t-out` remains.
5. **Icons:** Font Awesome is no longer bundled, so `fa fa-*` icons render blank. Buttons use Material Symbols
   names now (`icon="check"`).
6. **Removed fields:** 999 fields left standard models. For example, `res.partner.company_type`, and `datas` on
   attachments (use `raw`).
7. **Removed modules:** 50 modules were removed or merged, including `base_vat`, `base_iban` and `hr_org_chart`. A
   dependency on one of them blocks installation.
8. **Python:** `odoo.osv` is removed (use `Domain`), and `read_group` now returns tuples.

One warning if you use Odoo's `upgrade_code` command: started through `odoo-bin`, it rewrites all of Odoo's own
addons too, not just your path. Run it on a throwaway copy of the source. (I learned this the hard way: it changed
1,011 files in my checkout.)

Full list with evidence: <repo link>/blob/main/docs/change-catalog.md

#Odoo #Odoo20 #OdooDevelopment #Python #ERP

---

## Post 2: scanning a real module

**Attach:** `docs/screenshots/scan_report.png` and `docs/screenshots/fix_diff.png`

I built a small tool that tells you what will break before you upgrade a module to Odoo 20.

```
odoo-migrate scan ./my_module --odoo-src ~/src/odoo20
odoo-migrate fix  ./my_module --odoo-src ~/src/odoo20   # shows the diff, writes nothing
```

On Odoo's own `website_sale_loyalty` 19.0 module, it finds, among others:
• a `get_param` that needs a typed replacement
• a test import that no longer exists in 20
• an inherited view whose XPath (`//t[@t-set='force_coupon']`) matches nothing in the Odoo 20 parent view: the
  error that actually stops this module from installing

Safe fixes are applied as a reviewable diff: access rights through Odoo's own converter, plus `get_param`,
`t-esc` and manifest changes. Everything else becomes a task with the surrounding code and the Odoo 20 reference,
ready for a developer or an LLM. No file is written without a clean Git tree or a backup.

<repo link>

#Odoo #Odoo20 #DeveloperTools #Python

---

## Post 3: results on Odoo's own modules

**Attach:** the detection and fix-validation tables from the README

How well does an automatic Odoo 19 → 20 migration tool actually work? I measured it against the best ground truth
available: Odoo's own 20.0 code.

**Detection, across 583 modules:** for each change type, every module where Odoo had to make the change was
flagged, with no false flags. For example: 206 access-rights conversions, 114 `get_param` changes, 73 `t-esc`. And
96–99% of removed-field warnings match changes Odoo itself made.

**Will it install?** I installed 9 small modules on Odoo 20 in three versions:
• original 19.0 code: 0 of 9 install
• after automatic fixes: 3 of 9 install, and 1 passes all its tests
• Odoo's real 20.0 code: 9 of 9

So most real migrations still need a developer. But for every module that still failed, the error that stopped it
had already been reported by the tool, in the right file. It doesn't do the migration for you. It tells you exactly
where to look.

<repo link>

#Odoo #Odoo20 #SoftwareEngineering #Testing
