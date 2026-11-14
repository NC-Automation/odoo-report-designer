from lxml import etree, html as lxml_html

from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.nc_report_designer.models.ir_ui_view import iter_paths, path_to_str

ARCH = """
<t t-name="nc_report_designer.test_report">
    <t t-call="web.html_container">
        <t t-foreach="docs" t-as="o">
            <div class="page">
                <h2 name="title">Hello</h2>
                <p class="intro">Intro <b>text</b></p>
                <table name="lines">
                    <thead><tr><th>Name</th></tr></thead>
                    <tbody>
                        <tr t-foreach="o.child_ids" t-as="line">
                            <td><span t-field="line.name"/></td>
                        </tr>
                    </tbody>
                </table>
                <div class="row" name="cols">
                    <div class="col-4"><span>A</span><span name="ref" t-if="o.ref"> ref</span></div>
                    <div class="col-4 text-end"><span>B</span><t t-out="o.name"/></div>
                    <div class="col-4"><span>C</span></div>
                </div>
            </div>
            <t t-set="company" t-value="res_company"/><!-- as web.external_layout does -->
            <t t-call="web.ncd_test_shared"/>
        </t>
    </t>
</t>
"""


class ReportDesignerCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # stands for a layout shared by all reports (web.external_layout, web.address_layout...)
        cls.shared_view = cls.env['ir.ui.view'].create({
            'name': 'nc_report_designer test shared layout',
            'type': 'qweb',
            'key': 'web.ncd_test_shared',
            'arch': '<t t-name="web.ncd_test_shared"><div class="shared_box"><p>Shared</p></div></t>',
        })
        cls.view = cls.env['ir.ui.view'].create({
            'name': 'nc_report_designer test report',
            'type': 'qweb',
            'key': 'nc_report_designer.test_report',
            'arch': ARCH,
        })
        cls.report = cls.env['ir.actions.report'].create({
            'name': 'Designer test',
            'model': 'res.partner',
            'report_type': 'qweb-pdf',
            'report_name': 'nc_report_designer.test_report',
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Designer Customer',
            'email': 'designer@example.com',
            'child_ids': [(0, 0, {'name': 'Child One'}), (0, 0, {'name': 'Child Two'})],
        })
        cls.designer = cls.env['nc.report.designer']

    def target(self, xpath, view=None):
        view = view or self.view
        root = self.designer._combined(view)
        node = root.xpath(xpath)[0]
        for element, path in iter_paths(root):
            if element is node:
                return f'{view.id}:{path_to_str(path)}'
        raise AssertionError(xpath)

    def render(self, designer=False):
        Report = self.env['ir.actions.report'].with_context(nc_report_designer=designer)
        html = Report._render_qweb_html(self.report.id, [self.partner.id])[0]
        return lxml_html.fromstring(html)

    def page(self):
        return self.render().xpath('//div[hasclass("page")]')[0]

    def apply(self, **op):
        return self.designer.apply_operation(self.report.id, op)


@tagged('post_install', '-at_install')
class TestReportDesigner(ReportDesignerCase):

    def test_annotation_only_in_designer(self):
        annotated = self.render(designer=True)
        h2 = annotated.xpath('//h2')[0]
        self.assertEqual(h2.get('data-ncd'), self.target('//h2'))
        # the cell of each line maps to the same template node
        cells = annotated.xpath('//td')
        self.assertEqual(len(cells), 2)
        self.assertEqual(cells[0].get('data-ncd'), cells[1].get('data-ncd'))
        self.assertTrue(annotated.xpath('//span[@data-ncd-leaf]'))
        self.assertFalse(self.render().xpath('//*[@data-ncd]'))

    def test_node_info_and_scope(self):
        node = self.designer.get_node(self.report.id, self.target('//h2'))
        self.assertTrue(node['text_only'])
        self.assertEqual(node['xpath'], "//h2[@name='title']")
        self.assertIn('o', [v['var'] for v in node['scope']])
        self.assertNotIn('line', [v['var'] for v in node['scope']])
        cell = self.designer.get_node(self.report.id, self.target('//td'))
        self.assertIn({'var': 'line', 'model': 'res.partner'},
                      [{'var': v['var'], 'model': v['model']} for v in cell['scope']])
        self.assertFalse(self.designer.get_node(self.report.id, self.target('//td/span'))['can_contain'])

    def test_set_text_and_undo(self):
        result = self.apply(type='set_text', target=self.target('//h2'), html='Invoice <b data-ncd="1:2">copy</b>')
        h2 = self.page().xpath('.//h2')[0]
        self.assertEqual(h2.text_content(), 'Invoice copy')
        self.assertFalse(h2.xpath('.//*[@data-ncd]'))
        custom = self.env['ir.ui.view'].browse(result['history'][0]['view_id'])
        self.assertTrue(custom.key.startswith('nc_report_designer.custom_'))
        self.assertEqual(custom.inherit_id, self.view)
        self.designer.restore(result['history'], 'before')
        self.assertEqual(self.page().xpath('.//h2')[0].text_content(), 'Hello')
        self.designer.restore(result['history'], 'after')
        self.assertEqual(self.page().xpath('.//h2')[0].text_content(), 'Invoice copy')

    def test_set_text_is_cleaned(self):
        self.apply(type='set_text', target=self.target('//p'),
                   html='Hi<script>alert(1)</script> <b onclick="x()" class="ncd-selected k">there</b><!-- c -->!')
        p = self.page().xpath('.//p')[0]
        self.assertEqual(p.text_content(), 'Hi there!')
        self.assertEqual(dict(p.xpath('.//b')[0].attrib), {'class': 'k'})
        with self.assertRaises(UserError):
            self.apply(type='set_text', target=self.target('//table'), html='x')

    def test_insert_field_with_label(self):
        result = self.apply(type='insert_field', target=self.target('//h2'), position='after',
                            var='o', path='email', label=True)
        page = self.page()
        self.assertIn('designer@example.com', page.text_content())
        self.assertIn('Email:', page.text_content())
        # the new element is selected: it is right after the title
        self.assertEqual(result['select'], self.target('//h2/following-sibling::*[1]'))

    def test_insert_line_field_scope(self):
        with self.assertRaises(UserError):
            self.apply(type='insert_field', target=self.target('//h2'), position='after', var='line', path='email')
        self.apply(type='insert_field', target=self.target('//td'), position='inside', var='line', path='display_name')
        self.assertEqual(len(self.page().xpath('.//td/span')), 4)

    def test_insert_lines_table(self):
        self.apply(type='insert_field', target=self.target('//p'), position='after', var='o', path='child_ids')
        tables = self.page().xpath('.//table')
        self.assertEqual(len(tables), 2)
        self.assertIn('Child Two', tables[1].text_content())

    def test_insert_block_move_remove(self):
        self.apply(type='insert_block', target=self.target('//p'), position='after', block='heading')
        self.assertEqual([el.tag for el in self.page()][:3], ['h2', 'p', 'h3'])
        self.apply(type='move', source=self.target('//h2'), target=self.target('//table'), position='after')
        self.assertEqual([el.tag for el in self.page()], ['p', 'h3', 'table', 'h2', 'div'])
        self.apply(type='move_sibling', target=self.target('//h3'), direction='up')
        self.assertEqual([el.tag for el in self.page()], ['h3', 'p', 'table', 'h2', 'div'])
        self.apply(type='remove', target=self.target('//p'))
        self.assertEqual([el.tag for el in self.page()], ['h3', 'table', 'h2', 'div'])
        # all changes are specs of a single designer view
        custom = self.designer._custom_view(self.view)
        self.assertEqual(len(etree.fromstring(custom.arch)), 4)

    def test_shared_layout_protected(self):
        shared_box = self.target('//div', self.shared_view)
        # the record of the report is not available in a shared layout
        node = self.designer.get_node(self.report.id, self.target('//p', self.shared_view))
        self.assertEqual({v['var'] for v in node['scope']}, {'company', 'user'})
        with self.assertRaises(UserError):
            self.apply(type='insert_field', target=shared_box, position='inside', var='o', path='email')
        # no element moves into or out of it
        with self.assertRaises(UserError):
            self.apply(type='move', source=self.target('//h2'), target=shared_box, position='inside')
        with self.assertRaises(UserError):
            self.apply(type='move', source=self.target('//p', self.shared_view),
                       target=self.target('//h2'), position='after')
        self.assertFalse(self.designer._custom_view(self.shared_view))
        self.assertFalse(self.designer._custom_view(self.view))
        # editing the layout itself is still possible
        self.apply(type='insert_field', target=shared_box, position='inside', var='company', path='name')
        self.apply(type='insert_block', target=shared_box, position='inside', block='date')
        self.assertIn(self.env.company.name, self.render().xpath('//div[hasclass("shared_box")]')[0].text_content())
        # ...and the layout still renders without any record (as in Configure Document Layout)
        self.env['ir.ui.view']._render_template('web.ncd_test_shared', {'company': self.env.company})

    def test_move_into_itself_refused(self):
        with self.assertRaises(UserError):
            self.apply(type='move', source=self.target('//table'), target=self.target('//td'), position='inside')

    def test_attributes_and_style(self):
        self.apply(type='set_attrs', target=self.target('//h2'),
                   attrs={'style': 'color: #ff0000;', 't-if': 'o.email'})
        h2 = self.page().xpath('.//h2')[0]
        self.assertEqual(h2.get('style'), 'color: #ff0000;')
        self.partner.email = False
        self.assertFalse(self.page().xpath('.//h2'))
        with self.assertRaises(UserError):
            self.apply(type='set_attrs', target=self.target('//p'), attrs={'data-ncd': 'x'})

    def test_table_columns(self):
        result = self.apply(type='table_column', target=self.target('//th'), action='right')
        self.assertEqual(len(self.page().xpath('.//thead//th')), 2)
        self.assertEqual(len(self.page().xpath('.//tbody//td')), 4)
        self.assertEqual(result['select'], self.target('//thead//th[2]'))
        self.apply(type='table_column', target=self.target('//thead//th[1]'), action='delete')
        self.assertEqual(self.page().xpath('.//thead//th')[0].text_content(), 'New column')
        self.assertFalse(self.page().xpath('.//tbody//td/span'))

    def columns(self):
        return [col.get('class') for col in self.page().xpath('.//div[@name="cols"]/div')]

    def test_grid_columns(self):
        node = self.designer.get_node(self.report.id, self.target('//div[@name="cols"]/div[2]'))
        self.assertEqual(node['grid'], {'is_column': True, 'count': 3, 'index': 1, 'widths': [4, 4, 4], 'mode': 'grid'})
        self.assertFalse(self.designer.get_node(self.report.id, self.target('//h2'))['grid'])

        # add after the 2nd: 4 even columns, the new one selected
        result = self.apply(type='grid_column', target=self.target('//div[@name="cols"]/div[2]'), action='right')
        self.assertEqual(self.columns(), ['col-3', 'col-3 text-end', 'col-3', 'col-3'])
        self.assertEqual(self.page().xpath('.//div[@name="cols"]/div[3]')[0].text_content(), 'New column')
        self.assertEqual(result['select'], self.target('//div[@name="cols"]/div[3]'))

        # resize (twelfths), then delete one: the 3 left are evened out again
        cols = [self.target(f'//div[@name="cols"]/div[{index}]') for index in (1, 2)]
        self.apply(type='column_widths', columns=[{'target': cols[0], 'width': 5}, {'target': cols[1], 'width': 1}])
        self.assertEqual(self.columns(), ['col-5', 'col-1 text-end', 'col-3', 'col-3'])
        self.apply(type='grid_column', target=self.target('//div[@name="cols"]/div[3]'), action='delete')
        self.assertEqual(self.columns(), ['col-4', 'col-4 text-end', 'col-4'])
        self.assertNotIn('New column', self.page().xpath('.//div[@name="cols"]')[0].text_content())

        # the row itself: add at the start
        self.apply(type='grid_column', target=self.target('//div[@name="cols"]'), action='left')
        self.assertEqual(self.page().xpath('.//div[@name="cols"]/div[1]')[0].text_content(), 'New column')
        with self.assertRaises(UserError):
            self.apply(type='column_widths', columns=[{'target': cols[0], 'width': 13}])
        with self.assertRaises(UserError):
            self.apply(type='grid_column', target=self.target('//h2'), action='right')

    def test_grid_percent_widths(self):
        cols = [self.target(f'//div[@name="cols"]/div[{index}]') for index in (1, 2, 3)]
        self.apply(type='column_widths', columns=[
            {'target': cols[0], 'width': '22.5%'}, {'target': cols[1], 'width': '45%'}, {'target': cols[2], 'width': '32.5%'}])
        page_cols = self.page().xpath('.//div[@name="cols"]/div')
        self.assertEqual([col.get('class') for col in page_cols], ['col-auto', 'col-auto text-end', 'col-auto'])
        self.assertEqual([col.get('style') for col in page_cols], ['width: 22.5%;', 'width: 45%;', 'width: 32.5%;'])
        node = self.designer.get_node(self.report.id, cols[0])
        self.assertEqual(node['grid']['mode'], 'percent')
        self.assertEqual(node['grid']['widths'], ['22.5%', '45%', '32.5%'])
        # a new column keeps the percentage mode, evened out
        self.apply(type='grid_column', target=cols[2], action='right')
        self.assertEqual([col.get('style') for col in self.page().xpath('.//div[@name="cols"]/div')],
                         ['width: 25%;'] * 4)
        # back to grid steps: the style width goes away
        self.apply(type='column_widths', columns=[{'target': target, 'width': 3} for target in cols])
        first = self.page().xpath('.//div[@name="cols"]/div')[0]
        self.assertEqual((first.get('class'), first.get('style')), ('col-3', None))
        with self.assertRaises(UserError):
            self.apply(type='column_widths', columns=[{'target': cols[0], 'width': '120%'}])

    def test_grid_columns_hidden_and_auto(self):
        cols = [self.target(f'//div[@name="cols"]/div[{index}]') for index in (1, 2, 3)]
        # the 3rd column is not shown for this record: only the 2 visible ones (+ the new
        # one) share the row, the hidden one keeps its width
        self.apply(type='set_attrs', target=cols[2], attrs={'t-if': 'False'})
        self.apply(type='grid_column', target=cols[1], action='right', visible=cols[:2])
        root = self.designer._combined(self.view)
        self.assertEqual([col.get('class') for col in root.xpath('//div[@name="cols"]/div')],
                         ['col-4', 'col-4 text-end', 'col-4', 'col-4'])
        self.assertEqual(len(self.page().xpath('.//div[@name="cols"]/div')), 3)
        # automatic widths (col): stay automatic when adding, nothing to rebalance
        cols = [self.target(f'//div[@name="cols"]/div[{index}]') for index in (1, 2, 3, 4)]
        self.apply(type='column_widths', columns=[{'target': target, 'width': None} for target in cols])
        node = self.designer.get_node(self.report.id, cols[0])
        self.assertEqual(node['grid']['mode'], 'auto')
        self.apply(type='grid_column', target=cols[0], action='left', visible=cols[:3])
        self.assertEqual([col.get('class') for col in self.page().xpath('.//div[@name="cols"]/div')],
                         ['col', 'col', 'col text-end', 'col'])

    def test_drop_field_as_new_column(self):
        result = self.apply(type='insert_field', target=self.target('//div[@name="cols"]/div[1]'),
                            position='after', var='o', path='email', label=True, new_column=True)
        cols = self.page().xpath('.//div[@name="cols"]/div')
        self.assertEqual([col.get('class') for col in cols], ['col-3', 'col-3', 'col-3 text-end', 'col-3'])
        self.assertEqual(cols[1].xpath('./strong')[0].text, 'Email')
        self.assertEqual(cols[1].xpath('./div/span')[0].text_content(), 'designer@example.com')
        self.assertEqual(result['select'], self.target('//div[@name="cols"]/div[2]'))
        self.apply(type='insert_block', target=self.target('//div[@name="cols"]/div[4]'),
                   position='after', block='text', new_column=True)
        self.assertEqual(len(self.page().xpath('.//div[@name="cols"]/div')), 5)
        with self.assertRaises(UserError):  # only next to a column
            self.apply(type='insert_block', target=self.target('//h2'), position='after', block='text', new_column=True)

    def test_hidden_elements(self):
        self.apply(type='set_attrs', target=self.target('//p'), attrs={'t-if': 'False'})
        self.apply(type='set_attrs', target=self.target('//h2'), attrs={'t-if': 'o.ref'})  # partner without ref
        annotated = self.render(designer=True)
        rendered = [el.get('data-ncd') for el in annotated.xpath('//*[@data-ncd]')]
        hidden = self.designer.get_hidden(self.report.id, [self.view.id], rendered)
        by_target = {item['target']: item for item in hidden}
        ref_span = self.target('//span[@name="ref"]')
        self.assertEqual(set(by_target), {self.target('//p'), self.target('//h2'), ref_span})
        self.assertEqual(by_target[self.target('//h2')]['expression'], 'o.ref')
        self.assertTrue(by_target[self.target('//h2')]['own_condition'])
        self.assertIn('Intro text', by_target[self.target('//p')]['label'])
        # what the user hid is told apart from the template's own optional parts
        self.assertEqual({target for target, item in by_target.items() if item['by_designer']},
                         {self.target('//p'), self.target('//h2')})
        self.assertFalse(by_target[ref_span]['by_designer'])
        # a template condition replaced by the designer is remembered, to be put back
        self.apply(type='set_attrs', target=ref_span, attrs={'t-if': 'False'})
        hidden = self.designer.get_hidden(self.report.id, [self.view.id], rendered)
        ref = next(item for item in hidden if item['target'] == ref_span)
        self.assertEqual((ref['by_designer'], ref['original_expression']), (True, 'o.ref'))
        # hidden by a class: reported when the client finds it hidden
        self.apply(type='set_attrs', target=self.target('//table'), attrs={'class': 'd-none'})
        hidden = self.designer.get_hidden(self.report.id, [self.view.id], rendered, [self.target('//table')])
        table = next(item for item in hidden if item['target'] == self.target('//table'))
        self.assertEqual((table['kind'], table['by_designer']), ('style', True))
        # a selected hidden element is drawn anyway in the preview, marked to be hatched
        Report = self.env['ir.actions.report'].with_context(
            nc_report_designer=True, nc_report_designer_force=self.target('//p'))
        forced = lxml_html.fromstring(Report._render_qweb_html(self.report.id, [self.partner.id])[0])
        self.assertEqual(forced.xpath('//p[@data-ncd-ghost]')[0].text_content(), 'Intro text')
        self.assertFalse(forced.xpath('//h2'))  # only that one
        # not cached for the others
        self.assertFalse(self.render(designer=True).xpath('//div[hasclass("page")]//p'))
        # all of them at once (Hidden tab open)
        Report = Report.with_context(nc_report_designer_force=f"{self.target('//h2')},{self.target('//p')}")
        forced = lxml_html.fromstring(Report._render_qweb_html(self.report.id, [self.partner.id])[0])
        self.assertEqual(len(forced.xpath('//div[hasclass("page")]/*[@data-ncd-ghost]')), 2)
        # brought back
        self.apply(type='set_attrs', target=self.target('//p'), attrs={'t-if': ''})
        self.assertTrue(self.page().xpath('.//p'))

    def test_line_fields_as_table_columns(self):
        # the header (outside the lines loop) offers the line fields, as columns
        header = self.designer.get_node(self.report.id, self.target('//th'))
        columns = [v for v in header['scope'] if v['column']]
        self.assertEqual([(v['var'], v['model']) for v in columns], [('line', 'res.partner')])
        self.assertNotIn('line', [v['var'] for v in header['scope'] if not v['column']])

        # dropped between / next to the header: label in the header, value in the lines
        result = self.apply(type='table_column', target=self.target('//th'), action='right',
                            var='line', path='function')
        self.assertEqual([th.text_content() for th in self.page().xpath('.//thead//th')], ['Name', 'Job Position'])
        root = self.designer._combined(self.view)
        self.assertEqual(root.xpath('//tbody/tr/td[2]/span/@t-field'), ['line.function'])
        self.assertEqual(len(self.page().xpath('.//tbody//td')), 4)
        self.assertEqual(result['select'], self.target('//thead//th[2]'))

        # dropped onto a header cell like any field: a new column too; numbers right-aligned
        self.apply(type='insert_field', target=self.target('//thead//th[1]'), position='inside',
                   var='line', path='color')
        root = self.designer._combined(self.view)
        self.assertEqual(root.xpath('//thead//th[2]/@class'), ['text-end'])
        self.assertEqual(root.xpath('//tbody/tr/td[2]/span/@t-field'), ['line.color'])
        # a field of the document is not a column of the lines
        with self.assertRaises(UserError):
            self.apply(type='table_column', target=self.target('//thead//th[1]'), action='right',
                       var='o', path='email')

    def test_expressions(self):
        column = self.target('//div[@name="cols"]/div[2]')
        node = self.designer.get_node(self.report.id, column)
        # the value shown by a <t> inside the element (no "field" on the element itself)
        self.assertEqual(node['expressions'], [{
            'kind': 'expression', 'child': [1], 'attr': 't-out', 'value': 'o.name', 'label': 'Shows',
            'inside': True, 'format': False,
        }])
        self.apply(type='set_expression', target=column, child=[1], attr='t-out', value='o.email')
        self.assertIn('designer@example.com', self.page().xpath('.//div[@name="cols"]/div[2]')[0].text_content())
        # attributes computed by expressions
        self.apply(type='set_expression', target=column, child=[], attr='t-attf-title', value='Hi {{ o.name }}')
        self.assertEqual(self.page().xpath('.//div[@name="cols"]/div[2]/@title'), ['Hi Designer Customer'])
        labels = [item['label'] for item in self.designer.get_node(self.report.id, column)['expressions']]
        self.assertIn('Attribute “title” (text with {{ }})', labels)
        # invalid Python is refused, whatever way it comes
        with self.assertRaises(UserError):
            self.apply(type='set_expression', target=column, child=[1], attr='t-out', value='o.name +')
        with self.assertRaises(UserError):
            self.apply(type='set_attrs', target=self.target('//h2'), attrs={'t-if': 'o.ref =='})
        with self.assertRaises(UserError):
            self.apply(type='set_expression', target=column, attr='t-attf-title', value='{{ o. }}')
        self.assertTrue(self.designer.check_expression('t-if', 'o.ref and'))
        self.assertFalse(self.designer.check_expression('t-if', "o.ref and o.name != 'x'"))

    def test_texts_inside_t(self):
        self.apply(type='replace_xml', target=self.target('//div[@name="cols"]/div[3]/span'), xml=(
            '<span name="c"><t name="c_title">Credit Note</t></span>'
            '<span name="d"><t t-if="o.ref">Has ref</t><t t-else="">No ref</t></span>'
        ))
        # static text in a plain <t>: edited as the element's text, the <t> is kept
        span = self.target('//span[@name="c"]')
        node = self.designer.get_node(self.report.id, span)
        self.assertTrue(node['text_only'])
        self.assertEqual(node['inner_html'], 'Credit Note')
        self.apply(type='set_text', target=span, html='Vendor Credit Note')
        self.assertEqual(self.page().xpath('.//span[@name="c"]')[0].text_content(), 'Vendor Credit Note')
        self.assertTrue(self.designer._combined(self.view).xpath('//t[@name="c_title"][text()="Vendor Credit Note"]'))
        # texts of conditional <t>: listed with the expressions, each editable
        span = self.target('//span[@name="d"]')
        node = self.designer.get_node(self.report.id, span)
        self.assertFalse(node['text_only'])
        texts = [item for item in node['expressions'] if item['kind'] == 'text']
        self.assertEqual([(t['label'], t['value'], t['child']) for t in texts], [
            ('Text shown if o.ref', 'Has ref', [0]), ('Text shown otherwise', 'No ref', [1]),
        ])
        self.apply(type='set_text', target=span, child=[1], html='Without reference')
        self.assertEqual(self.page().xpath('.//span[@name="d"]')[0].text_content(), 'Without reference')
        with self.assertRaises(UserError):
            self.apply(type='set_text', target=self.target('//div[@name="cols"]/div[2]'), child=[1], html='x')

    def test_template_variables(self):
        self.apply(type='replace_xml', target=self.target('//div[@name="cols"]/div[3]/span'),
                   xml='<t t-set="greeting" t-value="\'Hi \' + o.name"/><span name="g" t-out="greeting"/>')
        variables = {v['var']: v for v in self.designer.get_node(self.report.id, self.target('//td'))['variables']}
        self.assertEqual((variables['line']['kind'], variables['line']['expression'], variables['line']['model']),
                         ('loop', 'o.child_ids', 'res.partner'))
        self.assertIn('line_index', variables)
        self.assertEqual(variables['o']['model'], 'res.partner')
        self.assertIn('user', variables)
        self.assertNotIn('greeting', variables)  # set after the table
        variables = {v['var']: v for v in self.designer.get_node(self.report.id, self.target('//span[@name="g"]'))['variables']}
        self.assertEqual((variables['greeting']['how'], variables['greeting']['expression'], variables['greeting']['model']),
                         ('value', "'Hi ' + o.name", False))
        self.assertNotIn('line', variables)

    def test_display_options(self):
        span = self.target('//td/span')  # t-field="line.name" (a char)
        display = self.designer.get_node(self.report.id, span)['display']
        self.assertTrue(display['parsed'])
        self.assertEqual(display['field_type'], 'char')
        self.assertIn('barcode', [w['key'] for w in display['widgets']])
        self.assertIn('symbology', [spec['name'] for spec in display['options']['barcode']])
        self.apply(type='set_display', target=span, options={
            'widget': {'value': 'text', 'expr': False}, 'x': {'value': 'o.name', 'expr': True}, 'flag': {'value': True}})
        node = self.designer.get_node(self.report.id, span)
        self.assertEqual(node['attrs']['t-options'], "{'widget': 'text', 'x': o.name, 'flag': True}")
        self.assertEqual(node['display']['values'], {
            'widget': {'value': 'text', 'expr': False}, 'x': {'value': 'o.name', 'expr': True},
            'flag': {'value': True, 'expr': False}})
        # records options get the records of that spot that fit
        date = self.designer._option_specs('monetary', {'o': 'res.partner'})
        currency = next(spec for spec in date if spec['name'] == 'display_currency')
        self.assertIn('o.company_id.currency_id', [c['expr'] for c in currency['candidates']])
        self.apply(type='set_display', target=span, options={})
        self.assertNotIn('t-options', self.designer.get_node(self.report.id, span)['attrs'])
        with self.assertRaises(UserError):
            self.apply(type='set_display', target=span, options={'x': {'value': 'o.', 'expr': True}})
        with self.assertRaises(UserError):
            self.apply(type='set_display', target=span, options={'a b': {'value': 1}})
        # a hand-written expression stays out of the form
        self.assertIsNone(self.designer._parse_options("dict(widget='text')"))

    def test_table_cell_widths(self):
        self.apply(type='table_column', target=self.target('//th'), action='right')
        cells = [self.target(f'//thead//th[{index}]') for index in (1, 2)]
        self.apply(type='column_widths', cells=[{'target': cells[0], 'width': '70%'}, {'target': cells[1], 'width': '30%'}])
        self.assertEqual([th.get('style') for th in self.page().xpath('.//thead//th')], ['width: 70%;', 'width: 30%;'])
        with self.assertRaises(UserError):
            self.apply(type='column_widths', cells=[{'target': cells[0], 'width': '1px; color: red'}])

    def test_replace_xml_and_duplicate(self):
        self.apply(type='replace_xml', target=self.target('//p'), xml='<p class="intro">Replaced</p>')
        self.assertEqual(self.page().xpath('.//p')[0].text_content(), 'Replaced')
        self.apply(type='duplicate', target=self.target('//p'))
        self.assertEqual(len(self.page().xpath('.//p')), 2)
        with self.assertRaises(UserError):
            self.apply(type='replace_xml', target=self.target('//p'), xml='<p>broken')

    def test_changes_reset(self):
        self.apply(type='remove', target=self.target('//p'))
        changes = self.designer.get_changes([self.view.id])
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['count'], 1)
        self.designer.reset_change(changes[0]['id'])
        self.assertTrue(self.page().xpath('.//p'))
        with self.assertRaises(UserError):
            self.designer.reset_change(self.view.id)  # never a template that is not ours

    def test_history(self):
        def h2():
            return self.page().xpath('.//h2')[0].text_content()

        def names():
            return [change['name'] for change in self.designer.get_history([self.view.id])['changes']]

        first = self.apply(type='set_text', target=self.target('//h2'), html='One')
        self.apply(type='set_text', target=self.target('//h2'), html='Two')
        self.apply(type='remove', target=self.target('//p'))
        history = self.designer.get_history([self.view.id])['changes']
        self.assertEqual(len(history), 3)
        self.assertTrue(history[0]['name'].startswith('Delete p.intro'), history[0]['name'])
        self.assertTrue(history[2]['name'].startswith('Edit text of h2[title]'), history[2]['name'])
        self.assertTrue(history[0]['date'] and history[0]['user'])
        self.assertFalse(self.designer.get_history([self.shared_view.id])['changes'])
        # the state just after the first change, and before them all
        first_id = history[2]['id']
        state = self.designer._state_at(first_id, [self.view.id])
        self.assertIn('One', state[self.view.id])
        self.assertFalse(self.designer._state_at(0, [self.view.id])[self.view.id])
        self.assertFalse(self.designer._state_at(history[0]['id'], [self.view.id]))
        # undo / redo are listed
        self.designer.restore(first['history'], 'before', self.report.id)
        self.assertEqual(h2(), 'Hello')
        self.assertTrue(names()[0].startswith('Undo: Edit text of'), names()[0])
        # back to the first change: the later ones are undone, and it is a change itself
        self.designer.reset_to(first_id, [self.view.id], self.report.id)
        self.assertEqual(h2(), 'One')
        self.assertTrue(self.page().xpath('.//p'))
        self.assertTrue(names()[0].startswith('Back to'), names()[0])
        # back to the original templates, then forward again to the latest state
        latest = self.designer.get_history([self.view.id])['changes'][0]['id']
        self.designer.reset_to(0, [self.view.id], self.report.id)
        self.assertEqual(h2(), 'Hello')
        self.assertFalse(self.designer._custom_view(self.view))
        self.designer.reset_to(latest, [self.view.id], self.report.id)
        self.assertEqual(h2(), 'One')
        # resetting a template is listed too
        self.designer.reset_change(self.designer._custom_view(self.view).id)
        self.assertTrue(names()[0].startswith('Reset'), names()[0])

    def test_preview_at_earlier_point(self):
        """The Changes tab's preview of an earlier point draws it, then leaves the report as it is now."""
        from odoo.addons.nc_report_designer.controllers.main import ReportDesignerController
        self.apply(type='set_text', target=self.target('//h2'), html='Before')
        self.apply(type='set_text', target=self.target('//h2'), html='Now')
        earlier = self.designer.get_history([self.view.id])['changes'][-1]['id']
        state = self.designer._state_at(earlier, [self.view.id])
        html = ReportDesignerController()._render_preview(
            self.env, self.report, self.partner.id, [], state=state, at=earlier)
        html = html.decode() if isinstance(html, bytes) else str(html)
        self.assertIn('Before', html)
        self.assertNotIn('could not be drawn', html)
        self.assertEqual(self.page().xpath('.//h2')[0].text_content(), 'Now')

    def test_rename_and_duplicate(self):
        self.designer.rename_report(self.report.id, '  Designer   renamed ')
        self.assertEqual(self.report.name, 'Designer renamed')
        with self.assertRaises(UserError):
            self.designer.rename_report(self.report.id, '   ')
        # the original's changes go along with the copy
        self.apply(type='set_text', target=self.target('//h2'), html='Changed')
        copy = self.env['ir.actions.report'].browse(
            self.designer.duplicate_report(self.report.id, 'Designer copy', False))
        self.assertEqual(copy.name, 'Designer copy')
        self.assertEqual(copy.model, 'res.partner')
        self.assertEqual(copy.ncd_copied_from_id, self.report)
        self.assertFalse(copy.binding_model_id)
        self.assertNotEqual(copy.report_name, self.report.report_name)
        copy_view = self.env['ir.ui.view']._get_template_view(copy.report_name)
        self.assertIn('Changed', copy_view.arch)
        # the shared layout is still called, not copied
        self.assertIn('t-call="web.ncd_test_shared"', copy_view.arch)

        def h2(report):
            html = self.env['ir.actions.report']._render_qweb_html(report.id, self.partner.ids)[0]
            return lxml_html.fromstring(html).xpath('//h2')[0].text_content()

        self.assertEqual(h2(copy), 'Changed')
        # changing the copy leaves the original as it is, and the other way around
        root = self.designer._combined(copy_view)
        node = root.xpath('//h2')[0]
        target = next(f'{copy_view.id}:{path_to_str(path)}' for element, path in iter_paths(root) if element is node)
        self.designer.apply_operation(copy.id, {'type': 'set_text', 'target': target, 'html': 'Copy only'})
        self.assertEqual(h2(copy), 'Copy only')
        self.assertEqual(h2(self.report), 'Changed')
        # shown in the Print menu by default
        self.report.create_action()
        self.assertTrue(self.report.binding_model_id)
        listed = self.designer.duplicate_report(self.report.id, 'Listed copy')
        self.assertEqual(self.env['ir.actions.report'].browse(listed).binding_model_id, self.report.binding_model_id)

    def test_doc_var_in_called_template(self):
        # as sale.report_saleorder: the main template calls the one looping over docs
        self.env['ir.ui.view'].create({
            'name': 'ncd raw', 'type': 'qweb', 'key': 'nc_report_designer.test_raw',
            'arch': '<t t-name="nc_report_designer.test_raw"><t t-call="web.html_container">'
                    '<t t-foreach="docs" t-as="doc"><p t-field="doc.name"/></t></t></t>',
        })
        self.env['ir.ui.view'].create({
            'name': 'ncd main', 'type': 'qweb', 'key': 'nc_report_designer.test_main',
            'arch': '<t t-name="nc_report_designer.test_main"><t t-call="nc_report_designer.test_raw"/></t>',
        })
        report = self.env['ir.actions.report'].create({
            'name': 'Calls raw', 'model': 'res.partner', 'report_type': 'qweb-pdf',
            'report_name': 'nc_report_designer.test_main',
        })
        self.assertEqual(self.designer._doc_var(report), 'doc')
        self.assertEqual(self.designer._doc_var(self.report), 'o')

    def test_edit_print_menu_and_delete(self):
        other_company = self.env['res.company'].create({'name': 'NCD Print'})

        def in_print_menu():
            bindings = self.env['ir.actions.actions'].get_bindings('res.partner')
            return self.report.id in [action['id'] for action in bindings.get('report', [])]

        self.designer.save_report_settings(self.report.id, {'name': 'Edited', 'print_menu': True})
        self.assertEqual(self.report.name, 'Edited')
        self.assertTrue(in_print_menu())
        settings = self.designer.get_report_settings(self.report.id)
        self.assertTrue(settings['print_menu'])
        # only for another company: not in the menu of the current one
        self.designer.save_report_settings(self.report.id, {
            'name': 'Edited', 'print_menu': True, 'print_company_ids': [other_company.id]})
        self.assertFalse(in_print_menu())
        self.assertTrue(self.env['ir.actions.actions'].with_company(other_company)
                        .with_context(allowed_company_ids=[other_company.id, self.env.company.id])
                        .get_bindings('res.partner').get('report'))
        self.designer.save_report_settings(self.report.id, {
            'name': 'Edited', 'print_menu': True, 'print_company_ids': [self.env.company.id]})
        self.assertTrue(in_print_menu())
        self.designer.save_report_settings(self.report.id, {'name': 'Edited', 'print_menu': False})
        self.assertFalse(self.report.binding_model_id)
        self.assertFalse(self.report.ncd_print_company_ids)

        # only copies can be deleted, with their templates and their designer changes
        with self.assertRaises(UserError):
            self.designer.delete_report(self.report.id)
        copy = self.env['ir.actions.report'].browse(self.designer.duplicate_report(self.report.id, 'To delete'))
        view = self.env['ir.ui.view']._get_template_view(copy.report_name)
        root = self.designer._combined(view)
        node = root.xpath('//h2')[0]
        target = next(f'{view.id}:{path_to_str(path)}' for element, path in iter_paths(root) if element is node)
        self.designer.apply_operation(copy.id, {'type': 'set_text', 'target': target, 'html': 'Copy'})
        token = copy.report_name[len('nc_report_designer.copy_'):].split('_')[0]
        Views = self.env['ir.ui.view']
        self.assertTrue(Views.search([('key', '=like', f'nc_report_designer.copy_{token}_%')]))
        self.assertEqual(self.designer.delete_report(copy.id), self.report.id)
        self.assertFalse(copy.exists())
        self.assertFalse(Views.search([('key', '=like', f'nc_report_designer.copy_{token}_%')]))
        self.assertFalse(Views.search([('key', '=like', f'nc_report_designer.custom_nc_report_designer.copy_{token}_%')]))
        self.assertTrue(self.view.exists() and self.shared_view.exists())

    def test_use_for_emails(self):
        if 'mail.template' not in self.env:
            self.assertIsNone(self.designer.get_email_templates(self.report.id))
            return
        other = self.report.copy({'name': 'Other partner report'})
        template = self.env['mail.template'].create({
            'name': 'NCD partner email', 'model_id': self.env['ir.model']._get_id('res.partner'),
            'report_template_ids': [(4, other.id)],
        })
        listed = {t['id']: t for t in self.designer.get_email_templates(self.report.id)}
        self.assertFalse(listed[template.id]['uses_this'])
        self.designer.set_email_report(self.report.id, template.id, True)
        self.assertEqual(template.report_template_ids, self.report)  # instead of the other one
        self.designer.set_email_report(self.report.id, template.id, False)
        self.assertFalse(template.report_template_ids)

    def test_report_per_company(self):
        Company = self.env['res.company']
        main = self.env.company
        second, third, fourth = Company.create([{'name': 'NCD Second'}, {'name': 'NCD Third'}, {'name': 'NCD Fourth'}])

        def make_report(title):
            key = f'nc_report_designer.test_{title.lower()}'
            self.env['ir.ui.view'].create({
                'name': key, 'type': 'qweb', 'key': key,
                'arch': f'<t t-name="{key}"><t t-foreach="docs" t-as="o"><h1>{title} <t t-out="o.name"/></h1></t></t>',
            })
            return self.env['ir.actions.report'].create({
                'name': title, 'model': 'res.partner', 'report_type': 'qweb-pdf', 'report_name': key,
            })

        other, special = make_report('Other'), make_report('Special')
        partners = self.env['res.partner'].create([
            {'name': 'P main', 'company_id': main.id},
            {'name': 'P second', 'company_id': second.id},
            {'name': 'P third', 'company_id': third.id},
            {'name': 'P fourth', 'company_id': fourth.id},
        ])

        def printed(records):
            html = self.env['ir.actions.report']._render_qweb_html(self.report.id, records.ids)[0]
            return html.decode() if isinstance(html, bytes) else html

        # no company: the report is used for all of them
        self.assertNotIn('Other', printed(partners))
        # the other companies are required once companies are chosen
        with self.assertRaises(UserError):
            self.designer.save_company_routing(self.report.id, {'company_ids': [main.id]})
        with self.assertRaises(UserError):  # a company with two reports
            self.designer.save_company_routing(self.report.id, {
                'company_ids': [main.id], 'other_report_id': other.id,
                'rules': [{'company_ids': [main.id], 'report_id': special.id}],
            })
        with self.assertRaises(UserError):  # never the report itself
            self.designer.save_company_routing(self.report.id, {
                'company_ids': [main.id], 'other_report_id': self.report.id,
            })
        self.designer.save_company_routing(self.report.id, {
            'company_ids': [main.id], 'other_report_id': other.id,
            'rules': [{'company_ids': [second.id, third.id], 'report_id': special.id},
                      {'company_ids': [], 'report_id': False}],  # an empty line is dropped
        })
        routing = self.designer.get_company_routing(self.report.id, partners[1].id)
        self.assertEqual(routing['company_ids'], [main.id])
        self.assertEqual(len(routing['rules']), 1)
        self.assertEqual(routing['record']['report']['id'], special.id)
        self.assertFalse(self.designer.get_company_routing(self.report.id, partners[0].id)['record'])

        self.assertIn('Hello', printed(partners[0]))
        self.assertIn('Special P second', printed(partners[1]))
        self.assertIn('Other P fourth', printed(partners[3]))
        mixed = printed(partners)
        for text in ('Hello', 'Special P second', 'Special P third', 'Other P fourth'):
            self.assertIn(text, mixed)
        # the PDF too (HTML while testing), as attached to an email
        pdf = self.env['ir.actions.report']._render_qweb_pdf(self.report.id, partners[3].ids)[0]
        self.assertIn(b'Other P fourth', pdf)
        # the designer always shows the report itself
        self.assertIn('Hello', self.env['ir.actions.report'].with_context(nc_report_designer=True)
                      ._render_qweb_html(self.report.id, partners[3].ids)[0].decode())
        # back to all companies
        self.designer.save_company_routing(self.report.id, {'company_ids': []})
        self.assertFalse(self.report.ncd_rule_ids or self.report.ncd_other_report_id)
        self.assertIn('Hello', printed(partners[3]))

    def test_setup_and_fields(self):
        setup = self.designer.get_setup(self.report.id, self.partner.id)
        self.assertEqual(setup['res_id'], self.partner.id)
        self.assertIn('o', [v['var'] for v in setup['scope']])
        names = [f['name'] for f in self.designer.get_fields('res.partner')]
        self.assertIn('email', names)

    def test_access(self):
        user = self.env['res.users'].create({
            'name': 'Designer Stranger', 'login': 'ncd_stranger',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(Exception):
            self.designer.with_user(user).get_setup(self.report.id)


@tagged('post_install', '-at_install')
class TestReportDesignerHttp(HttpCase):

    def test_preview_route(self):
        partner = self.env['res.partner'].create({'name': 'Preview Customer'})
        report = self.env.ref('base.report_ir_model_overview', raise_if_not_found=False)
        if not report:
            self.skipTest('no base report to preview')
        model = self.env['ir.model']._get('res.partner')
        self.authenticate('admin', 'admin')
        response = self.url_open(f'/nc_report_designer/preview/{report.id}/{model.id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-ncd=', response.text)
        self.assertTrue(partner)
