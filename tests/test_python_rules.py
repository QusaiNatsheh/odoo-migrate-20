def test_py001_config_parameter(make_module, helpers):
    m = make_module(
        {
            "models/a.py": """
        from odoo import models
        class A(models.Model):
            _inherit = 'res.company'
            def f(self):
                icp = self.env['ir.config_parameter'].sudo()
                a = self.env['ir.config_parameter'].sudo().get_param('x.key')
                b = self.env['ir.config_parameter'].sudo().get_param('x.key', 'default')
                c = self.env['ir.config_parameter'].sudo().get_param('x.limit', 10)
                self.env['ir.config_parameter'].set_param('x.key', 'v')
                d = icp.get_param('x.other')
        """
        }
    )
    findings = helpers.rule("PY001").scan(m)
    assert len(findings) == 5
    assert sum(f.fixable for f in findings) == 3  # int default and unknown receiver stay manual
    after = helpers.fixed("PY001", m, "models/a.py")
    assert ".get_str('x.key')" in after and ".get_str('x.key', 'default')" in after
    assert ".get_param('x.limit', 10)" in after
    assert ".set_str('x.key', 'v')" in after
    assert "icp.get_param('x.other')" in after


def test_py002_osv(make_module, helpers):
    m = make_module(
        {
            "models/a.py": """
        from odoo import models
        from odoo.osv import expression
        def dom(a, b):
            return expression.AND([a, b]) or expression.OR([a, b]) or expression.TRUE_DOMAIN
        """,
            "models/b.py": """
        from odoo.osv import expression
        def dom(a):
            return expression.normalize_domain(a)
        """,
        }
    )
    assert {f.file for f in helpers.rule("PY002").scan(m)} == {"models/a.py", "models/b.py"}
    after = helpers.fixed("PY002", m, "models/a.py")
    assert "odoo.osv" not in after and "from odoo.fields import Domain" in after
    assert "Domain.AND([a, b]) or Domain.OR([a, b]) or Domain.TRUE" in after
    assert "odoo.osv" in helpers.fixed("PY002", m, "models/b.py")  # normalize_domain: left for a human


def test_py004_access_methods(make_module, helpers):
    m = make_module(
        {
            "models/a.py": """
        def f(self):
            self.check_access_rights('write')
            ok = self.check_access_rights('read', raise_exception=False)
            self.check_access_rule('read')
            return self._filter_access_rules('read')
        """
        }
    )
    after = helpers.fixed("PY004", m, "models/a.py")
    assert "self.check_access('write')" in after
    assert "ok = self.has_access('read')" in after
    assert "self.check_access('read')" in after
    assert "self._filtered_access('read')" in after


def test_py005_check_recursion_inverted(make_module, helpers):
    m = make_module(
        {
            "models/a.py": """
        def check(self):
            if not self._check_recursion(parent='parent_id'):
                raise ValueError()
        """
        }
    )
    after = helpers.fixed("PY005", m, "models/a.py")
    assert "if not not self._has_cycle(field_name='parent_id'):" in after


def test_py006_api_deprecated(make_module, helpers):
    m = make_module(
        {
            "models/a.py": """
        from odoo import api, models
        class A(models.Model):
            _name = 'a'
            @api.deprecated("old")
            def f(self):
                pass
        """
        }
    )
    after = helpers.fixed("PY006", m, "models/a.py")
    assert '@deprecated("old")' in after and "from odoo.tools.func import deprecated" in after


def test_py007_tools_imports(make_module, helpers):
    m = make_module(
        {
            "models/a.py": """
        from odoo.tools import column_exists, float_compare, ormcache
        from odoo.tools import pycompat
        """
        }
    )
    findings = helpers.rule("PY007").scan(m)
    assert len(findings) == 2 and findings[0].fixable and not findings[1].fixable
    after = helpers.fixed("PY007", m, "models/a.py")
    assert "from odoo.tools.sql import column_exists" in after
    assert "from odoo.tools import float_compare" in after
    assert "from odoo.api import ormcache" in after
    assert "from odoo.tools import pycompat" in after


def test_py008_group_operator(make_module, helpers):
    m = make_module({"models/a.py": "x = fields.Float(group_operator='avg')\n"})
    assert helpers.fixed("PY008", m, "models/a.py") == "x = fields.Float(aggregator='avg')\n"


def test_py009_env_shortcuts(make_module, helpers):
    m = make_module({"models/a.py": "def f(self):\n    return self._cr, self._uid, self._context, other._cr\n"})
    assert "self.env.cr, self.env.uid, self.env.context, other._cr" in helpers.fixed("PY009", m, "models/a.py")


def test_py003_manual_calls_are_not_rewritten(make_module, helpers):
    m = make_module({"models/a.py": "def f(self):\n    return self.read_group([], ['a:sum'], ['b'])\n"})
    findings = helpers.rule("PY003").scan(m)
    assert len(findings) == 1 and not findings[0].fixable
    assert helpers.rule("PY003").fix(m) == []


def test_fld002_removed_field_in_python(make_module, helpers):
    m = make_module(
        {
            "models/a.py": """
        from odoo import api, fields, models
        class Partner(models.Model):
            _inherit = 'res.partner'
            @api.depends('company_type')
            def _compute_x(self):
                for rec in self:
                    rec.x = rec.company_type == 'company'
        """
        }
    )
    msgs = [f.message for f in helpers.rule("FLD002").scan(m)]
    assert len(msgs) == 2 and all("res.partner.company_type" in s for s in msgs)


def test_syntax_error_files_are_skipped(make_module, helpers):
    m = make_module({"models/broken.py": "def f(:\n"})
    assert helpers.rule("PY001").scan(m) == []
