def test_sec001_old_security(make_module, helpers):
    m = make_module(
        {
            "security/ir.model.access.csv": "id,name\n",
            "security/rules.xml": """
        <odoo><record id="r" model="ir.rule"><field name="name">x</field></record></odoo>
        """,
        }
    )
    assert len(helpers.rule("SEC001").scan(m)) == 2


def test_man001_version_and_moved_keys(make_module, helpers):
    m = make_module(
        {
            "__manifest__.py": """
        {
            'name': 'X',
            'version': '19.0.1.2.0',
            'depends': ['base'],
            'data': [
                'views/a.xml',
            ],
            'init_xml': ['data/init.xml'],
            'demo_xml': ['demo/demo.xml'],
        }
        """
        }
    )
    findings = helpers.rule("MAN001").scan(m)
    assert len(findings) == 3 and all(f.fixable for f in findings)
    after = helpers.fixed("MAN001", m, "__manifest__.py")
    import ast

    d = ast.literal_eval(after)
    assert d["version"] == "20.0.1.2.0"
    assert d["data"] == ["views/a.xml", "data/init.xml"] and d["demo"] == ["demo/demo.xml"]
    assert "init_xml" not in d and "demo_xml" not in d


def test_xml001_t_esc_server_only(make_module, helpers):
    m = make_module(
        {
            "views/report.xml": '<odoo><template id="t"><span t-esc="o.name"/></template></odoo>\n',
            "static/src/x.xml": '<templates><t t-name="w"><span t-esc="props.x"/></t></templates>\n',
        }
    )
    findings = helpers.rule("XML001").scan(m)
    assert [f.file for f in findings] == ["views/report.xml"]
    assert 't-out="o.name"' in helpers.fixed("XML001", m, "views/report.xml")
    assert "t-esc" in (m.path / "static/src/x.xml").read_text()


def test_xml002_t_raw_is_manual(make_module, helpers):
    m = make_module({"views/r.xml": '<odoo><template id="t"><div t-raw="o.html"/></template></odoo>\n'})
    assert len(helpers.rule("XML002").scan(m)) == 1 and helpers.rule("XML002").fix(m) == []


def test_xml006_font_awesome(make_module, helpers):
    m = make_module({"views/v.xml": '<odoo><button icon="fa-check" string="x"/><i class="fa fa-lock"/></odoo>\n'})
    assert len(helpers.rule("XML006").scan(m)) == 2


def test_xml007_report_file_removed(make_module, helpers):
    m = make_module(
        {
            "report/r.xml": """
        <odoo>
            <record id="r" model="ir.actions.report">
                <field name="name">R</field>
                <field name="report_file">my_module.report_x</field>
            </record>
        </odoo>
        """
        }
    )
    after = helpers.fixed("XML007", m, "report/r.xml")
    assert "report_file" not in after and '<field name="name">R</field>' in after


def test_fld001_view_uses_removed_field(make_module, helpers):
    m = make_module(
        {
            "views/p.xml": """
        <odoo>
            <record id="v" model="ir.ui.view">
                <field name="name">p</field>
                <field name="model">res.partner</field>
                <field name="inherit_id" ref="base.view_partner_form"/>
                <field name="arch" type="xml">
                    <xpath expr="//field[@name='company_type']" position="after">
                        <field name="name"/>
                    </xpath>
                </field>
            </record>
        </odoo>
        """
        }
    )
    findings = helpers.rule("FLD001").scan(m)
    assert len(findings) == 1 and "company_type" in findings[0].message


def test_fld003_removed_model(make_module, helpers):
    m = make_module(
        {"data/d.xml": '<odoo><record id="b" model="res.bank"><field name="name">B</field></record></odoo>'}
    )
    assert len(helpers.rule("FLD003").scan(m)) == 1


def test_man001_removed_dependency(make_module, helpers):
    m = make_module({"__manifest__.py": "{'name': 'X', 'version': '20.0.1.0', 'depends': ['base_vat', 'sale']}"})
    findings = helpers.rule("MAN001").scan(m)
    assert len(findings) == 1 and "base_vat" in findings[0].message and not findings[0].fixable
