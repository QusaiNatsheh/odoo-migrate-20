import textwrap

import pytest

from odoo_migrate import context
from odoo_migrate.rules import against_source


@pytest.fixture
def odoo_src(tmp_path, monkeypatch):
    """A tiny fake Odoo 20 tree: one module with a form view and a Python package."""
    src = tmp_path / "odoo20"
    files = {
        "odoo-bin": "",
        "odoo/api/__init__.py": "def model(f):\n    return f\n",
        "odoo/http/__init__.py": "from .router import Controller\n",
        "odoo/http/router.py": "class Controller:\n    pass\nroot = None\n",
        "addons/sale/__manifest__.py": "{'name': 'Sale'}",
        "addons/sale/tests/__init__.py": "",
        "addons/sale/tests/common.py": "class SaleCommon:\n    pass\n",
        "addons/sale/views/sale_views.xml": """
            <odoo>
              <record id="view_order_form" model="ir.ui.view">
                <field name="model">sale.order</field>
                <field name="arch" type="xml">
                  <form><field name="partner_id"/><group name="totals"/></form>
                </field>
              </record>
              <record id="view_order_form_ext" model="ir.ui.view">
                <field name="model">sale.order</field>
                <field name="inherit_id" ref="view_order_form"/>
                <field name="arch" type="xml">
                  <field name="partner_id" position="after"><field name="added_by_extension"/></field>
                </field>
              </record>
            </odoo>""",
    }
    for rel, content in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content).lstrip("\n"))
    against_source.view_index.cache_clear()
    monkeypatch.setattr(context, "ODOO_SRC", src)
    return src


VIEW = """
<odoo>
  <record id="my_ext" model="ir.ui.view">
    <field name="model">sale.order</field>
    <field name="inherit_id" ref="sale.view_order_form"/>
    <field name="arch" type="xml">
      <data>
        <field name="partner_id" position="after"><field name="x"/></field>
        <xpath expr="//group[@name='totals']" position="inside"><field name="y"/></xpath>
        <field name="added_by_extension" position="after"><field name="z"/></field>
        <xpath expr="//field[@name='gone_in_20']" position="after"><field name="w"/></xpath>
      </data>
    </field>
  </record>
  <record id="my_ext2" model="ir.ui.view">
    <field name="inherit_id" ref="sale.view_that_was_removed"/>
    <field name="arch" type="xml"><field name="a" position="after"/></field>
  </record>
</odoo>
"""


def test_view001_reports_only_missing_targets(odoo_src, make_module, helpers):
    m = make_module({"views/v.xml": VIEW})
    messages = [f.message for f in helpers.rule("VIEW001").scan(m)]
    assert len(messages) == 2
    assert any("gone_in_20" in s for s in messages)
    assert any("view_that_was_removed does not exist" in s for s in messages)


def test_view001_inactive_without_source(make_module, helpers, monkeypatch):
    monkeypatch.setattr(context, "ODOO_SRC", None)
    assert helpers.rule("VIEW001").scan(make_module({"views/v.xml": VIEW})) == []


def test_py011_imports(odoo_src, make_module, helpers):
    m = make_module(
        {
            "models/a.py": """
        from odoo import api
        from odoo.http import Controller, request
        from odoo.addons.sale.tests.common import SaleCommon, OldHelper
        from odoo.addons.sale.tests import removed_module
        from odoo.addons.my_module.models import x
        from odoo.addons.some_oca_module import y
        from odoo import http
        root = http.root
        """
        }
    )
    messages = sorted(f.message for f in helpers.rule("PY011").scan(m))
    assert len(messages) == 4, messages
    assert any("request not found in odoo.http" in s for s in messages)
    assert any("OldHelper not found" in s for s in messages)
    assert any("removed_module not found" in s for s in messages)
    assert any("odoo.http.root moved" in s for s in messages)
