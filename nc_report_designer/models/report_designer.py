"""Server side of the report designer.

The designer shows a report rendered with ``nc_report_designer`` in the context:
every element then carries ``data-ncd="<view id>:<path>"`` (see ir_ui_view.py),
the path being the element-child indexes from the root of that view's combined
arch. Operations sent back by the client name their targets that way; they are
turned into ``<xpath>`` specs appended to one designer view per template (an
extension view inheriting the template, applied after every other one), so each
spec is computed against the fully combined arch as it is now.
"""
import ast
import re
import secrets
from copy import deepcopy

from lxml import etree, html as lxml_html
from lxml.builder import E
from markupsafe import escape

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.template_inheritance import apply_inheritance_specs

from .ir_ui_view import (
    CONTENT_DIRECTIVES, NCD_ATTR, NCD_LEAF_ATTR, SKIPPED_TAGS, VOID_TAGS,
    element_children, is_leaf, iter_paths, path_to_str,
)

CUSTOM_KEY_PREFIX = 'nc_report_designer.custom_'
CUSTOM_PRIORITY = 9999
EMPTY_ARCH = '<data/>'
# Marks the node an operation creates or moves, to find where it lands.
MARK = 'data-ncd-mark'
# Tags the elements of a template without the designer's changes (see _designer_tree).
BASE_ATTR = 'data-ncd-base'
DESIGNER_ATTRS = {NCD_ATTR, NCD_LEAF_ATTR, MARK, 'contenteditable', 'draggable', 'spellcheck'}

# Attributes tried, in order, to address an element with a short XPath.
XPATH_ATTRS = ('id', 'name', 't-field', 't-out', 't-esc', 't-foreach', 't-call', 't-set', 't-if', 't-elif')
TABLE_PARTS = {'table', 'thead', 'tbody', 'tfoot', 'tr'}
# Never kept from text typed in the preview.
FORBIDDEN_TAGS = {
    'script', 'style', 'iframe', 'object', 'embed', 'form', 'input', 'textarea', 'select',
    'button', 'link', 'meta', 'base', 'svg', 'math', 't',
}
ATTR_NAME_RE = re.compile(r'^[A-Za-z_:][-\w:.]*$')
# A record(set) expression: name(.field)* optionally followed by recordset methods.
EXPR_RE = re.compile(
    r'^\s*([A-Za-z_]\w*)((?:\.[A-Za-z_]\w*)*)'
    r'((?:\.(?:sudo|filtered|sorted|with_context|with_company|filtered_domain)\(.*\))*)\s*$',
    re.S,
)
PREFERRED_COLUMNS = (
    'quantity', 'product_uom_qty', 'product_qty', 'qty', 'price_unit', 'discount',
    'price_subtotal', 'amount', 'price_total', 'date',
)
NUMERIC_TYPES = {'integer', 'float', 'monetary'}
# QWeb directives holding a Python expression (t-att-<name> too); t-attf-<name> holds text
# with {{ expressions }}.
# Formatters (ir.qweb.field.<key>) offered in the Display section, by field type
WIDGET_LABELS = {
    'char': "Text",
    'text': "Text with line breaks",
    'html': "HTML",
    'integer': "Whole number",
    'float': "Number",
    'monetary': "Amount with currency",
    'float_time': "Hours (HH:MM)",
    'time': "Time of day",
    'duration': "Duration (2 hours, 5 minutes)",
    'date': "Date",
    'datetime': "Date and time",
    'relative': "Relative date (3 days ago)",
    'selection': "Choice label",
    'many2one': "Name",
    'contact': "Contact card (address, phone...)",
    'barcode': "Barcode",
    'image': "Image",
    'image_url': "Image from a URL",
}
WIDGETS_BY_TYPE = {
    'char': ['char', 'text', 'barcode', 'image_url'],
    'text': ['text', 'char'],
    'html': ['html', 'text'],
    'integer': ['integer', 'float', 'float_time', 'duration', 'barcode'],
    'float': ['float', 'monetary', 'integer', 'float_time', 'time', 'duration'],
    'monetary': ['monetary', 'float'],
    'date': ['date', 'relative'],
    'datetime': ['datetime', 'date', 'relative'],
    'selection': ['selection'],
    'many2one': ['many2one', 'contact'],
    'binary': ['image'],
}

PYTHON_DIRECTIVES = {'t-if', 't-elif', 't-field', 't-out', 't-esc', 't-raw', 't-foreach', 't-value', 't-options'}
# How the Expressions section of the designer names them.
EXPRESSION_LABELS = {
    't-field': "Shows the field",
    't-out': "Shows",
    't-esc': "Shows",
    't-raw': "Shows (HTML)",
    't-if': "Shown only if",
    't-elif': "Else, shown if",
    't-foreach': "Repeated for each item of",
    't-value': "Sets a variable to",
    't-options': "Display options",
}
# Bootstrap grid column classes: col, col-6, col-md-4, col-auto...
COL_CLASS_RE = re.compile(r'^col(?:-(?:sm|md|lg|xl|xxl))?(?:-(?:\d{1,2}|auto))?$')
WIDTH_RE = re.compile(r'^\d{1,3}(?:\.\d+)?%$')


def _classes(node):
    return (node.get('class') or '').split()


def _is_grid_row(node):
    return 'row' in _classes(node)


def _is_grid_column(node):
    return any(COL_CLASS_RE.match(cls) for cls in _classes(node))


def _col_width(node):
    """Width of a grid column in twelfths, None when not fixed (col, col-auto)."""
    for cls in _classes(node):
        match = COL_CLASS_RE.match(cls)
        if match:
            number = cls.rsplit('-', 1)[-1]
            if number.isdigit():
                return int(number)
    return None


def _style_prop(style, prop):
    for part in (style or '').split(';'):
        name, _sep, value = part.partition(':')
        if name.strip().lower() == prop:
            return value.strip()
    return ''


def _col_percent(node):
    """Free width of a grid column ("37.5%": col-auto + style width), or None."""
    width = _style_prop(node.get('style'), 'width')
    return width if WIDTH_RE.match(width) else None


def _col_size(node):
    """Width of a grid column: twelfths (int), a percentage ("37.5%") or None (automatic)."""
    return _col_percent(node) or _col_width(node)


def _with_col_width(node, width):
    """Class attribute of ``node`` with its column classes replaced by col-<width>,
    col-auto for a percentage (sized by its style), col if None."""
    others = [cls for cls in _classes(node) if not COL_CLASS_RE.match(cls)]
    if isinstance(width, str):
        first = 'col-auto'
    else:
        first = f'col-{width}' if width else 'col'
    return ' '.join([first] + others)


def _spread(total, count):
    """``count`` integers adding up to ``total``, as even as possible."""
    base, extra = divmod(total, count)
    return [base + (1 if index < extra else 0) for index in range(count)]


def _even_widths(count):
    """``count`` widths in twelfths, as even as possible (3 -> 4, 4, 4; 5 -> 3, 3, 2, 2, 2)."""
    return _spread(12, count)


def _even_percents(count):
    """``count`` percentages adding up to 100 (3 -> 33.4%, 33.3%, 33.3%)."""
    return [f'{tenths / 10:g}%' for tenths in _spread(1000, count)]


def _with_style_prop(style, prop, value):
    parts = [part.strip() for part in (style or '').split(';') if part.strip()]
    parts = [part for part in parts if part.split(':', 1)[0].strip().lower() != prop]
    if value:
        parts.append(f'{prop}: {value}')
    return '; '.join(parts) + (';' if parts else '')

BLOCKS = [
    ('text', 'Text', 'fa-paragraph', '<p>New text</p>'),
    ('heading', 'Heading', 'fa-header', '<h3>Heading</h3>'),
    ('columns2', 'Two columns', 'fa-columns',
     '<div class="row"><div class="col-6"><p>Left column</p></div>'
     '<div class="col-6"><p>Right column</p></div></div>'),
    ('columns3', 'Three columns', 'fa-th-large',
     '<div class="row"><div class="col-4"><p>Column 1</p></div><div class="col-4"><p>Column 2</p></div>'
     '<div class="col-4"><p>Column 3</p></div></div>'),
    ('box', 'Box', 'fa-square-o', '<div class="border rounded p-2 mb-2"><p>Box content</p></div>'),
    ('table', 'Table', 'fa-table',
     '<table class="table table-sm table-bordered"><thead><tr><th>Column 1</th><th>Column 2</th></tr></thead>'
     '<tbody><tr><td>Cell</td><td>Cell</td></tr></tbody></table>'),
    ('list', 'Bullet list', 'fa-list-ul', '<ul><li>First item</li><li>Second item</li></ul>'),
    ('image', 'Image', 'fa-picture-o',
     '<img src="/web/static/img/placeholder.png" style="max-height: 120px;" alt="Image"/>'),
    ('logo', 'Company logo', 'fa-building-o',
     '<img t-if="company and company.logo" t-att-src="image_data_uri(company.logo)" '
     'style="max-height: 60px;" alt="Logo"/>'),
    ('date', 'Print date', 'fa-calendar',
     # not context_timestamp(): only report rendering defines it (not e.g. the layout preview)
     '<span t-out="datetime.date.today()" t-options="{\'widget\': \'date\'}"/>'),
    ('conditional', 'Conditional section', 'fa-question-circle-o',
     '<div t-if="True"><p>Shown only when the condition is true</p></div>'),
    ('signature', 'Signature lines', 'fa-pencil',
     '<div class="row mt-5"><div class="col-6"><div class="border-top pt-1">Signature</div></div>'
     '<div class="col-6"><div class="border-top pt-1">Date</div></div></div>'),
    ('divider', 'Divider', 'fa-minus', '<hr/>'),
    ('spacer', 'Spacer', 'fa-arrows-v', '<div style="height: 24px;"/>'),
    ('page_break', 'Page break', 'fa-scissors', '<div style="page-break-before: always;"/>'),
]
BLOCKS_BY_KEY = {key: xml for key, _name, _icon, xml in BLOCKS}


def _quote(value):
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    return None


def _strip_marks(element):
    for node in element.iter(etree.Element):
        node.attrib.pop(MARK, None)


def _parse_fragment(xml):
    try:
        wrapper = etree.fromstring(f'<data>{xml}</data>')
    except etree.XMLSyntaxError as e:
        raise UserError(f"Invalid XML: {e}") from e
    elements = element_children(wrapper)
    if not elements:
        raise UserError("The XML contains no element.")
    for element in elements:
        element.tail = None
    return elements


class NcReportDesigner(models.AbstractModel):
    _name = 'nc.report.designer'
    _description = 'Report Designer'

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_access(self):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._("Only administrators can design reports."))

    def _report(self, report_id):
        report = self.env['ir.actions.report'].browse(int(report_id)).exists()
        if not report or not report.report_type.startswith('qweb-'):
            raise UserError(self.env._("This report does not exist or is not a QWeb report."))
        return report

    def _parse_target(self, target):
        view_id, _sep, path = (target or '').partition(':')
        if not view_id.isdigit():
            raise UserError(self.env._("Invalid element reference: %s", target))
        view = self.env['ir.ui.view'].browse(int(view_id)).exists()
        if not view:
            raise UserError(self.env._("The template of this element no longer exists; reload the preview."))
        return view, tuple(int(index) for index in path.split('.')) if path else ()

    def _combined(self, view):
        return view.with_context(inherit_branding=False, nc_report_designer=False)._get_combined_arch()

    def _node(self, root, path):
        node = root
        for index in path:
            children = element_children(node)
            if index >= len(children):
                raise UserError(self.env._("This element changed since the preview was drawn; reload the preview."))
            node = children[index]
        return node

    def _locate(self, target):
        view, path = self._parse_target(target)
        root = self._combined(view)
        return view, root, self._node(root, path)

    def _matches(self, root, expr):
        try:
            return etree.ETXPath(expr)(root)
        except (etree.XPathError, TypeError):
            return []

    def _own_xpath(self, root, node):
        """A short XPath matching only ``node``, based on its own attributes."""
        for attr in XPATH_ATTRS:
            value = node.get(attr)
            if not value or len(value) > 120 or '\n' in value or not (quoted := _quote(value)):
                continue
            expr = f'//{node.tag}[@{attr}={quoted}]'
            if self._matches(root, expr) == [node]:
                return expr
        classes = [_quote(cls) for cls in (node.get('class') or '').split()]
        if classes and all(classes):
            expr = f"//{node.tag}[hasclass({', '.join(classes)})]"
            if self._matches(root, expr) == [node]:
                return expr
        return None

    def _xpath_for(self, root, node):
        """An XPath for an inheritance spec that locates ``node`` in ``root``."""
        if node is root:
            raise UserError(self.env._("The root of a template cannot be changed this way."))
        if expr := self._own_xpath(root, node):
            return expr
        steps = []
        current = node
        while current is not root:
            parent = current.getparent()
            same_tag = [child for child in parent if child.tag == current.tag]
            steps.insert(0, f'{current.tag}[{same_tag.index(current) + 1}]')
            current = parent
            anchor = f'/{root.tag}' if current is root else self._own_xpath(root, current)
            if anchor:
                expr = f"{anchor}/{'/'.join(steps)}"
                if self._matches(root, expr)[:1] == [node]:
                    return expr
        raise UserError(self.env._("Could not build a path to this element."))

    def _custom_view(self, view):
        return self.env['ir.ui.view'].search([
            ('inherit_id', '=', view.id), ('key', '=like', f'{CUSTOM_KEY_PREFIX}%'),
        ], limit=1)

    def _check_custom_view(self, view_id):
        view = self.env['ir.ui.view'].browse(int(view_id)).exists()
        if not view or not (view.key or '').startswith(CUSTOM_KEY_PREFIX):
            raise UserError(self.env._("This view was not made by the report designer."))
        return view

    def _write_specs(self, view, specs):
        """Append inheritance specs to the designer view of ``view``.

        :return: an undo/redo history entry
        """
        custom = self._custom_view(view)
        before = custom.arch if custom else EMPTY_ARCH
        arch = etree.fromstring(before)
        arch.text = '\n'
        for spec in specs:
            _strip_marks(spec)
            spec.tail = '\n'
            arch.append(spec)
        new_arch = etree.tostring(arch, encoding='unicode')
        if custom:
            custom.write({'arch': new_arch})
        else:
            custom = self._set_designer_arch(view, new_arch)
        return {'view_id': custom.id, 'base_id': view.id, 'before': before, 'after': custom.arch}

    def _set_designer_arch(self, view, arch):
        """Give template ``view`` this designer view XML (none when ``arch`` is empty)."""
        custom = self._custom_view(view)
        if not arch or arch == EMPTY_ARCH:
            custom.unlink()
            return self.env['ir.ui.view']
        if custom:
            if custom.arch != arch:
                custom.write({'arch': arch})
            return custom
        return self.env['ir.ui.view'].create({
            'name': f'Report Designer: {view.key or view.name}',
            'type': 'qweb',
            'mode': 'extension',
            'inherit_id': view.id,
            'priority': CUSTOM_PRIORITY,
            'key': f'{CUSTOM_KEY_PREFIX}{view.key or view.id}',
            'arch': arch,
        })

    def _landing(self, view, root, specs):
        """Apply ``specs`` to a copy of ``root`` and return the target of the
        element carrying MARK (a new or moved element), or False."""
        try:
            result = apply_inheritance_specs(deepcopy(root), [deepcopy(spec) for spec in specs])
        except Exception as e:  # noqa: BLE001 - shown to the user, nothing is written
            raise UserError(self.env._("This change cannot be applied: %s", e)) from e
        for node, path in iter_paths(result):
            if node.get(MARK):
                return f'{view.id}:{path_to_str(path)}'
        return False

    def _is_text_only(self, node):
        if node.tag in VOID_TAGS or node.tag in TABLE_PARTS or node.tag == 't' or node.getparent() is None:
            return False
        if any(node.get(attr) for attr in CONTENT_DIRECTIVES):
            return False
        return not any(
            child.tag == 't' or any(attr.startswith('t-') for attr in child.attrib)
            for child in node.iterdescendants(etree.Element)
        )

    def _holds_text(self, node):
        """Whether the content of ``node`` is static text (maybe with plain markup)."""
        if any(node.get(attr) for attr in CONTENT_DIRECTIVES + ('t-set', 't-foreach')):
            return False
        return not any(
            child.tag == 't' or any(attr.startswith('t-') for attr in child.attrib)
            for child in node.iterdescendants(etree.Element)
        )

    def _text_holder(self, node):
        """Element whose content the Text box edits: ``node`` itself, or the plain
        <t> (no directive, e.g. ``<t name="title">Vendor Credit Note</t>``) holding all
        of its content, which is kept so that other modules still find it; or None."""
        if self._is_text_only(node):
            return node
        if (node.tag in VOID_TAGS or node.tag in TABLE_PARTS or node.tag == 't' or node.getparent() is None
                or any(node.get(attr) for attr in CONTENT_DIRECTIVES)):
            return None
        holder = node
        while True:
            children = element_children(holder)
            if (len(children) != 1 or (holder.text or '').strip() or (children[0].tail or '').strip()
                    or children[0].tag != 't' or any(attr.startswith('t-') for attr in children[0].attrib)):
                return None
            holder = children[0]
            if self._holds_text(holder):
                return holder

    def _is_shared(self, view):
        """Layouts used by every report (and by previews with no record at all)."""
        return (view.key or '').startswith('web.')

    def _template_info(self, view):
        return {
            'id': view.id,
            'key': view.key or '',
            'name': view.name,
            'shared': self._is_shared(view),
            'custom_id': self._custom_view(view).id or False,
        }

    # ------------------------------------------------------------------
    # Variables in scope (to know which fields can be dropped where)
    # ------------------------------------------------------------------

    def _doc_var(self, report):
        """The variable the report's templates loop over ``docs`` with: in its main
        template, or in a template it calls (e.g. sale.report_saleorder calls
        sale.report_saleorder_raw, which loops with ``doc``)."""
        IrUiView = self.env['ir.ui.view']
        queue = [IrUiView._get_template_view(report.report_name, raise_if_not_found=False)]
        seen = set()
        while queue and len(seen) < 10:
            view = queue.pop(0)
            if not view or view.id in seen:
                continue
            seen.add(view.id)
            for node in self._combined(view).iter(etree.Element):
                if node.get('t-foreach', '').strip() == 'docs' and node.get('t-as'):
                    return node.get('t-as')
                called = node.get('t-call')
                if called and '{' not in called and not self._is_shared_key(called):
                    queue.append(IrUiView._get_template_view(called, raise_if_not_found=False))
        return 'o'

    def _base_scope(self, report, view=None):
        """Variables available everywhere in ``view`` (a template of ``report``).

        A shared layout gets none of the report's record variables: other reports
        name them differently, and previews (e.g. Configure Document Layout)
        render the layouts without any record."""
        scope = {} if view and self._is_shared(view) else {'docs': report.model, self._doc_var(report): report.model}
        scope.setdefault('company', 'res.company')
        scope.setdefault('user', 'res.users')
        return scope

    def _resolve(self, expr, scope):
        """Model of a record(set) expression such as ``o.order_line``, or None."""
        match = EXPR_RE.match(expr or '')
        if not match or match.group(1) not in scope:
            return None
        model = scope[match.group(1)]
        for name in filter(None, match.group(2).split('.')):
            field = self.env[model]._fields.get(name)
            if not field or not field.relational:
                return None
            model = field.comodel_name
        return model

    def _guess_loop_model(self, loop_node, var, scope):
        """Model of a loop variable over an unknown expression, guessed from the
        fields the loop body reads on it among the x2many targets in scope."""
        pattern = re.compile(rf'\b{re.escape(var)}\.([A-Za-z]\w*)\b(?!\s*\()')
        names = set()
        for node in loop_node.iter(etree.Element):
            for value in node.attrib.values():
                names.update(pattern.findall(value))
        if not names:
            return None
        for model in dict.fromkeys(scope.values()):
            for field in self.env[model]._fields.values():
                if field.type in ('one2many', 'many2many') and field.comodel_name in self.env:
                    comodel_fields = self.env[field.comodel_name]._fields
                    if all(name in comodel_fields for name in names):
                        return field.comodel_name
        return None

    def _scope_at(self, report, view, node, include_self):
        """Variables holding records, and their model, at ``node`` of ``view``
        (inside it when ``include_self``, else at its position among its siblings)."""
        scope = self._base_scope(report, view)
        chain = list(reversed(list(node.iterancestors()))) + [node]
        for element in chain:
            for sibling in reversed(list(element.itersiblings(preceding=True))):
                if isinstance(sibling.tag, str) and sibling.get('t-set') and sibling.get('t-value'):
                    if model := self._resolve(sibling.get('t-value'), scope):
                        scope[sibling.get('t-set')] = model
            if element is node and not include_self:
                break
            loop, var = element.get('t-foreach'), element.get('t-as')
            if loop and var:
                if model := self._resolve(loop, scope) or self._guess_loop_model(element, var, scope):
                    scope[var] = model
        return scope

    def _set_definition(self, node):
        """``(kind, text)``: how the <t t-set> ``node`` computes its value."""
        if node.get('t-value'):
            return 'value', node.get('t-value').strip()
        if node.get('t-valuef'):
            return 'format', node.get('t-valuef').strip()
        body = ' '.join(''.join(node.itertext()).split())
        if len(node):
            return 'content', self.env._("the content inside it (%s)", body[:80] or '…') if body \
                else self.env._("the content inside it")
        return 'text', body

    def _chain_variables(self, report, view, node, include_self, scope, variables, where):
        """Collect in ``variables`` (name → description) the variables set before
        ``node`` (by <t t-set> elements before it or before an element around it) and
        the loops around it; ``scope`` (name → model) is updated as for _scope_at."""
        chain = list(reversed(list(node.iterancestors()))) + [node]
        for element in chain:
            for sibling in reversed(list(element.itersiblings(preceding=True))):
                if not isinstance(sibling.tag, str) or not sibling.get('t-set'):
                    continue
                name = sibling.get('t-set')
                kind, text = self._set_definition(sibling)
                model = self._resolve(sibling.get('t-value'), scope) if kind == 'value' else None
                if model:
                    scope[name] = model
                condition = next((sibling.get(attr) for attr in ('t-if', 't-elif') if sibling.get(attr)), '')
                variables.pop(name, None)
                variables[name] = {'var': name, 'kind': 'set', 'how': kind, 'expression': text,
                                   'condition': condition, 'model': model, 'where': where}
            if element is node and not include_self:
                break
            loop, var = element.get('t-foreach'), element.get('t-as')
            if loop and var:
                model = self._resolve(loop, scope) or self._guess_loop_model(element, var, scope)
                if model:
                    scope[var] = model
                variables.pop(var, None)
                variables[var] = {'var': var, 'kind': 'loop', 'how': 'loop', 'expression': loop.strip(),
                                  'condition': '', 'model': model, 'where': where}
                for suffix, text in (
                    ('index', self.env._("position in the loop, from 0")),
                    ('first', self.env._("True for the first item")),
                    ('last', self.env._("True for the last item")),
                    ('size', self.env._("number of items")),
                ):
                    variables[f'{var}_{suffix}'] = {
                        'var': f'{var}_{suffix}', 'kind': 'loop_info', 'how': 'info', 'expression': text,
                        'condition': '', 'model': None, 'where': where}

    def _call_path(self, start, key, depth=4):
        """``[(view, t-call element)]`` from the template ``start`` down to the one
        calling the template ``key`` (e.g. invoice with payments → invoice → invoice
        document); the elements are in copies of the combined archs. Shared layouts
        are not followed."""
        IrUiView = self.env['ir.ui.view']
        root = deepcopy(self._combined(start))
        calls = [el for el in root.iter(etree.Element) if el.get('t-call')]
        for call in calls:
            if call.get('t-call') == key:
                return [(start, call)]
        if depth > 1:
            for call in calls:
                called = call.get('t-call')
                if called.startswith('web.') or '{' in called:
                    continue
                sub = IrUiView._get_template_view(called, raise_if_not_found=False)
                if sub and (path := self._call_path(sub, key, depth - 1)):
                    return [(start, call)] + path
        return []

    def _template_variables(self, report, view, node, include_self):
        """Every variable available at ``node`` with how it is computed, for the
        expression editor: the report's own, those set by the main template of the
        report before it calls this one, and those of this template."""
        scope = self._base_scope(report, view)
        variables = {}
        if not self._is_shared(view):
            doc_var = self._doc_var(report)
            variables['docs'] = {'var': 'docs', 'kind': 'report', 'how': 'info', 'condition': '', 'where': '',
                                 'expression': self.env._("the records printed"), 'model': report.model}
            variables[doc_var] = {'var': doc_var, 'kind': 'loop', 'how': 'loop', 'condition': '', 'where': '',
                                  'expression': 'docs', 'model': report.model}
        for name, model, text in (
            ('company', 'res.company', self.env._("the company of the document (set by the layout)")),
            ('user', 'res.users', self.env._("the user printing")),
            ('res_company', 'res.company', self.env._("the company of the user printing")),
        ):
            variables.setdefault(name, {'var': name, 'kind': 'report', 'how': 'info', 'condition': '', 'where': '',
                                        'expression': text, 'model': model})
        main = self.env['ir.ui.view']._get_template_view(report.report_name, raise_if_not_found=False)
        if main and main != view and view.key and not self._is_shared(view):
            for caller, call in self._call_path(main, view.key):
                # the variables set before the call, and those set inside it (passed along)
                end = E.t()
                call.append(end)
                self._chain_variables(report, caller, end, False, scope, variables, caller.name)
        self._chain_variables(report, view, node, include_self, scope, variables, '')
        for name, model, text in (
            ('time', None, self.env._("Python's time module, e.g. time.strftime('%Y')")),
            ('context_timestamp', None, self.env._("context_timestamp(date) gives a date in the user's time zone")),
        ):
            variables.setdefault(name, {'var': name, 'kind': 'tool', 'how': 'info', 'condition': '', 'where': '',
                                        'expression': text, 'model': model})
        IrModel = self.env['ir.model']
        result = []
        for item in variables.values():
            if item['model'] and item['model'] not in self.env:
                item['model'] = None
            item['model_name'] = (IrModel._get(item['model']).name or item['model']) if item['model'] else ''
            item['model'] = item['model'] or False
            result.append(item)
        return result

    def _scope_list(self, scope, columns=None):
        """Variables for the field palette; ``columns``: variables of the lines of the
        table around the selection, whose fields are dropped as new columns."""
        IrModel = self.env['ir.model']
        result = [
            {'var': var, 'model': model, 'model_name': IrModel._get(model).name or model, 'column': False}
            for var, model in scope.items()
            if var != 'docs' and model in self.env
        ]
        for var, model in (columns or {}).items():
            if var not in scope and model in self.env:
                result.append({'var': var, 'model': model, 'model_name': IrModel._get(model).name or model,
                               'column': True})
        return result

    # ------------------------------------------------------------------
    # Content builders
    # ------------------------------------------------------------------

    def _field_info(self, scope, var, path):
        """``(field, label, expression)`` of ``var.path`` (path may cross many2one fields)."""
        if var not in scope:
            raise UserError(self.env._(
                "“%s” is not available at this spot: drop the field inside the part of the report "
                "that shows it (for line fields, inside the lines table).", var))
        model = scope[var]
        names = (path or '').split('.')
        field = None
        for index, name in enumerate(names):
            field = self.env[model]._fields.get(name)
            if not field:
                raise UserError(self.env._("Unknown field %(field)s on %(model)s", field=name, model=model))
            if index < len(names) - 1:
                if not field.relational:
                    raise UserError(self.env._("%s is not a relational field.", name))
                model = field.comodel_name
        string = self.env[model].fields_get([field.name], ['string'])[field.name]['string']
        return field, string, f'{var}.{path}'

    def _target_of(self, view, root, node):
        return next(f'{view.id}:{path_to_str(path)}' for element, path in iter_paths(root) if element is node)

    def _table_of(self, node):
        return node if node.tag == 'table' else next(node.iterancestors('table'), None)

    def _table_loop_vars(self, report, view, table):
        """Variables of the loops drawing the rows of ``table`` (``line`` of
        ``<tr t-foreach="o.invoice_line_ids" t-as="line">``), with their model: their
        fields make the columns of the table."""
        result = {}
        for element in table.iter(etree.Element):
            var = element.get('t-as')
            if not element.get('t-foreach') or not var or var in result:
                continue
            if element.tag != 'tr' and next(element.iter('tr'), None) is None:
                continue
            cell = next(element.iter('td', 'th'), None)
            if cell is None or self._table_of(cell) is not table:
                continue
            scope = self._scope_at(report, view, cell, include_self=True)
            if var in scope:
                result[var] = scope[var]
        return result

    def _field_elements(self, scope, var, path, label):
        field, string, expr = self._field_info(scope, var, path)
        if field.type in ('one2many', 'many2many'):
            return [self._lines_table(expr, field, scope)]
        if isinstance(field, fields.Image) or (field.type == 'binary' and 'image' in field.name):
            value = E.div({'t-field': expr, 't-options': '{"widget": "image"}', 'style': 'max-width: 200px;'})
        elif field.type == 'html':
            value = E.div({'t-field': expr})
        elif field.type == 'boolean':
            value = E.span({'t-out': f"'Yes' if {expr} else 'No'"})
        else:
            value = E.span({'t-field': expr})
        if not label:
            return [value]
        if label == 'stacked':
            # label above the value, as in the header boxes of invoices / orders
            return [E.strong(string), value if value.tag == 'div' else E.div(value)]
        strong = E.strong(f'{string}:')
        strong.tail = ' '
        return [E.div(strong, value)]

    def _lines_table(self, expr, field, scope):
        comodel = self.env[field.comodel_name]
        item, counter = 'line', 2
        while item in scope:
            item, counter = f'line{counter}', counter + 1
        columns = ['display_name'] + [name for name in PREFERRED_COLUMNS if name in comodel._fields][:3]
        descriptions = comodel.fields_get(columns, ['string', 'type'])
        head, body = E.tr(), E.tr({'t-foreach': expr, 't-as': item})
        for name in columns:
            description = descriptions.get(name, {})
            align = {'class': 'text-end'} if description.get('type') in NUMERIC_TYPES else {}
            title = self.env._('Description') if name == 'display_name' else description.get('string', name)
            head.append(E.th(align, title))
            body.append(E.td(align, E.span({'t-field': f'{item}.{name}'})))
        return E.table({'class': 'table table-sm o_main_table'}, E.thead(head), E.tbody(body))

    def _clean_inner_html(self, html):
        """Content typed in the preview → (text, [elements]) for an XML spec."""
        wrapper = lxml_html.fragment_fromstring(html or '', create_parent='div')
        for node in list(wrapper.iterdescendants()):
            if not isinstance(node.tag, str) or node.tag in FORBIDDEN_TAGS:
                node.drop_tree()  # comments, scripts, forms...: the tail text is kept
        for node in wrapper.iter(etree.Element):
            for attr in list(node.attrib):
                value = node.attrib[attr].strip().lower()
                if (attr in DESIGNER_ATTRS or attr.startswith(('data-oe-', 'on', 't-'))
                        or value.startswith(('javascript:', 'vbscript:', 'data:text'))):
                    del node.attrib[attr]
            classes = [cls for cls in (node.get('class') or '').split() if not cls.startswith('ncd-')]
            if classes:
                node.set('class', ' '.join(classes))
            else:
                node.attrib.pop('class', None)
        wrapper = etree.fromstring(etree.tostring(wrapper, method='xml', encoding='unicode'))
        return wrapper.text, list(wrapper)

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def _insert(self, target, position, elements):
        view, root, node = self._locate(target)
        if position not in ('before', 'after', 'inside'):
            raise UserError(self.env._("Invalid position: %s", position))
        if position == 'inside' and is_leaf(node):
            raise UserError(self.env._("Nothing can be dropped inside this element; drop it before or after."))
        if position == 'inside' and node.tag in TABLE_PARTS and any(el.tag not in TABLE_PARTS | {'td', 'th'} for el in elements):
            raise UserError(self.env._("Drop it inside a table cell."))
        if node is root:
            raise UserError(self.env._("Nothing can be added outside the template."))
        spec = E.xpath(expr=self._xpath_for(root, node), position=position)
        for index, element in enumerate(elements):
            if index == 0:
                element.set(MARK, '1')
            spec.append(element)
        select = self._landing(view, root, [spec])
        return {'history': [self._write_specs(view, [spec])], 'select': select}

    def _op_insert_block(self, report, op):
        xml = BLOCKS_BY_KEY.get(op.get('block'))
        if not xml:
            raise UserError(self.env._("Unknown block: %s", op.get('block')))
        if op.get('new_column'):
            view, root, row, column = self._new_column_target(op)
            return self._grid_add(view, root, row, column, op['position'], _parse_fragment(xml), op.get('visible'))
        return self._insert(op['target'], op.get('position'), _parse_fragment(xml))

    def _op_insert_xml(self, report, op):
        return self._insert(op['target'], op.get('position'), _parse_fragment(op.get('xml') or ''))

    def _op_insert_field(self, report, op):
        view, root, node = self._locate(op['target'])
        inside = op.get('position') == 'inside'
        scope = self._scope_at(report, view, node, include_self=inside)
        table = self._table_of(node)
        if op.get('var') not in scope and table is not None and not op.get('new_column') \
                and op.get('var') in self._table_loop_vars(report, view, table):
            # a field of the table's lines dropped on its header (or anywhere outside the
            # lines): it becomes a new column, after the cell it was dropped on
            cell = node if node.tag in ('td', 'th', 'table') else next(node.iterancestors('td', 'th'), table)
            target = self._target_of(view, root, cell)
            return self._op_table_column(report, {
                'target': target, 'action': 'left' if op.get('position') == 'before' else 'right',
                'var': op['var'], 'path': op.get('path'),
            })
        if op.get('var') not in scope and self._is_shared(view):
            raise UserError(self.env._(
                "This spot belongs to the shared layout “%s”, used by every report: fields of "
                "the printed record cannot be placed there. Drop the field inside the document "
                "itself.", view.key))
        if op.get('new_column'):
            view, root, row, column = self._new_column_target(op)
            elements = self._field_elements(scope, op.get('var'), op.get('path'), op.get('label') and 'stacked')
            return self._grid_add(view, root, row, column, op['position'], elements, op.get('visible'))
        elements = self._field_elements(scope, op.get('var'), op.get('path'), bool(op.get('label')))
        return self._insert(op['target'], op.get('position'), elements)

    def _op_remove(self, report, op):
        view, root, node = self._locate(op['target'])
        spec = E.xpath(expr=self._xpath_for(root, node), position='replace')
        return {'history': [self._write_specs(view, [spec])], 'select': False}

    def _op_duplicate(self, report, op):
        _view, _root, node = self._locate(op['target'])
        copy = deepcopy(node)
        copy.tail = None
        return self._insert(op['target'], 'after', [copy])

    def _move(self, view, root, source, target, position):
        if target is source or source in target.iterancestors():
            raise UserError(self.env._("An element cannot be moved inside itself."))
        if position == 'inside' and is_leaf(target):
            raise UserError(self.env._("Nothing can be dropped inside this element; drop it before or after."))
        spec = E.xpath(
            E.xpath(expr=self._xpath_for(root, source), position='move'),
            expr=self._xpath_for(root, target), position=position,
        )
        source.set(MARK, '1')
        select = self._landing(view, root, [spec])
        return {'history': [self._write_specs(view, [spec])], 'select': select}

    def _op_move(self, report, op):
        position = op.get('position')
        if position not in ('before', 'after', 'inside'):
            raise UserError(self.env._("Invalid position: %s", position))
        source_view, source_path = self._parse_target(op['source'])
        target_view, target_path = self._parse_target(op['target'])
        if source_view == target_view:
            root = self._combined(source_view)
            return self._move(source_view, root, self._node(root, source_path), self._node(root, target_path), position)
        # Across templates: copy into the target template, then remove from the source one.
        # Never into or out of a shared layout: the element's variables (o, doc, line...)
        # do not exist there, and removing it would change every report.
        for view in (source_view, target_view):
            if self._is_shared(view):
                raise UserError(self.env._(
                    "This would move the element into or out of the shared layout “%s”, used by "
                    "every report. Drop it inside the document itself, or edit the layout's XML.",
                    view.key))
        source_root = self._combined(source_view)
        source = self._node(source_root, source_path)
        copy = deepcopy(source)
        copy.tail = None
        result = self._insert(op['target'], position, [copy])
        spec = E.xpath(expr=self._xpath_for(source_root, source), position='replace')
        result['history'].append(self._write_specs(source_view, [spec]))
        return result

    def _op_move_sibling(self, report, op):
        view, root, node = self._locate(op['target'])
        up = op.get('direction') == 'up'
        sibling = node.getprevious() if up else node.getnext()
        while sibling is not None and not isinstance(sibling.tag, str):
            sibling = sibling.getprevious() if up else sibling.getnext()
        if sibling is None:
            raise UserError(self.env._("This element is already the first one.") if up
                            else self.env._("This element is already the last one."))
        return self._move(view, root, node, sibling, 'before' if up else 'after')

    # ------------------------------------------------------------------
    # Expressions (t-if, t-out, t-att-class... on an element and the <t> inside it)
    # ------------------------------------------------------------------

    def _expression_error(self, name, value):
        """Why ``value`` is not a valid expression for the directive ``name``, or None."""
        value = (value or '').strip()
        if not value:
            return None
        if name in PYTHON_DIRECTIVES or (name.startswith('t-att-') and name != 't-att-'):
            sources = [value]
        elif name.startswith('t-attf-'):
            sources = re.findall(r'\{\{(.*?)\}\}', value, re.S) + re.findall(r'#\{(.*?)\}', value, re.S)
        else:
            return None
        for source in sources:
            try:
                ast.parse(source.strip(), mode='eval')
            except SyntaxError as e:
                return self.env._("Invalid expression “%(expr)s”: %(error)s", expr=source.strip(), error=e.msg)
        return None

    def _check_expression(self, name, value):
        if error := self._expression_error(name, value):
            raise UserError(error)

    def _expression_items(self, node, path=()):
        """Editable expressions of ``node`` itself (``path`` empty) and of the <t>
        elements inside it (not crossing other elements)."""
        items = []
        for name, value in node.attrib.items():
            if name in PYTHON_DIRECTIVES or name.startswith(('t-att-', 't-attf-')):
                if name.startswith('t-attf-'):
                    label = self.env._("Attribute “%s” (text with {{ }})", name[7:])
                elif name.startswith('t-att-'):
                    label = self.env._("Attribute “%s”", name[6:])
                else:
                    label = EXPRESSION_LABELS.get(name, name)
                if name == 't-value' and node.get('t-set'):
                    label = self.env._("Sets “%s” to", node.get('t-set'))
                items.append({'kind': 'expression', 'child': list(path), 'attr': name, 'value': value,
                              'label': label, 'inside': bool(path), 'format': name.startswith('t-attf-')})
        if path and node.tag == 't' and self._holds_text(node) and (node.text or '').strip() and not len(node):
            # static text of a <t> (e.g. shown under a condition): editable as is
            condition = next((node.get(attr) for attr in ('t-if', 't-elif') if node.get(attr)), None)
            if condition:
                label = self.env._("Text shown if %s", condition)
            elif 't-else' in node.attrib:
                label = self.env._("Text shown otherwise")
            else:
                label = self.env._("Text")
            items.append({'kind': 'text', 'child': list(path), 'attr': '', 'value': node.text.strip(),
                          'label': label, 'inside': True, 'format': False})
        for index, child in enumerate(element_children(node)):
            if child.tag == 't':
                items.extend(self._expression_items(child, path + (index,)))
        return items

    def _child_t(self, node, child_path):
        """The <t> inside ``node`` at ``child_path`` (element-child indexes)."""
        for index in child_path or []:
            children = element_children(node)
            if index >= len(children) or children[index].tag != 't':
                raise UserError(self.env._("This element changed since the preview was drawn; reload the preview."))
            node = children[index]
        return node

    # ------------------------------------------------------------------
    # Display options (t-options) of a value shown
    # ------------------------------------------------------------------

    def _parse_options(self, source):
        """``{name: {'value', 'expr'}}`` of a ``t-options`` dict (constants as values,
        anything else as expression source); None when it is not a plain dict."""
        if not (source or '').strip():
            return {}
        try:
            tree = ast.parse(source.strip(), mode='eval').body
        except SyntaxError:
            return None
        if not isinstance(tree, ast.Dict) or not all(
                isinstance(key, ast.Constant) and isinstance(key.value, str) for key in tree.keys):
            return None
        result = {}
        for key, value in zip(tree.keys, tree.values):
            try:
                constant = ast.literal_eval(value)
                if isinstance(constant, tuple):
                    constant = list(constant)
                result[key.value] = {'value': constant, 'expr': False}
            except (ValueError, TypeError, SyntaxError):
                result[key.value] = {'value': ast.unparse(value), 'expr': True}
        return result

    def _format_options(self, options):
        """The ``t-options`` expression of ``{name: {'value', 'expr'}}`` ('' when empty)."""
        parts = []
        for name, item in (options or {}).items():
            if not re.match(r'^[A-Za-z_]\w*$', name or ''):
                raise UserError(self.env._("Invalid option name: %s", name))
            value = (item or {}).get('value')
            if (item or {}).get('expr'):
                value = str(value or '').strip()
                if not value:
                    continue
                if error := self._expression_error('t-out', value):
                    raise UserError(error)
                parts.append(f'{name!r}: {value}')
            elif value is not None and value != '':
                if not isinstance(value, (str, int, float, bool, list)):
                    raise UserError(self.env._("Invalid value for the option %s", name))
                parts.append(f'{name!r}: {value!r}')
        return '{' + ', '.join(parts) + '}' if parts else ''

    def _value_field(self, scope, expr):
        """The field shown by ``o.partner_id.name`` (a field path of a record in scope)."""
        match = re.match(r'^\s*([A-Za-z_]\w*)((?:\.[A-Za-z_]\w*)+)\s*$', expr or '')
        if not match or match.group(1) not in scope:
            return None
        model, field = scope[match.group(1)], None
        for name in match.group(2)[1:].split('.'):
            if not model or model not in self.env:
                return None
            field = self.env[model]._fields.get(name)
            if not field:
                return None
            model = field.comodel_name if field.relational else None
        return field

    def _record_candidates(self, scope, model):
        """Expressions of records of ``model`` reachable at that spot (o.currency_id,
        company.currency_id...), for options taking a record."""
        result = []
        for var, var_model in scope.items():
            if var == 'docs' or var_model not in self.env:
                continue
            if var_model == model:
                result.append((var, var))
            for field in self.env[var_model]._fields.values():
                if field.type != 'many2one' or field.comodel_name not in self.env:
                    continue
                path = f'{var}.{field.name}'
                if field.comodel_name == model:
                    result.append((path, f'{field.string} ({path})'))
                elif field.name == 'company_id':
                    # one step further through the company (o.company_id.currency_id)
                    for sub in self.env[field.comodel_name]._fields.values():
                        if sub.type == 'many2one' and sub.comodel_name == model:
                            sub_path = f'{path}.{sub.name}'
                            result.append((sub_path, f'{field.string} › {sub.string} ({sub_path})'))
        return list(dict.fromkeys(result))[:40]

    def _option_specs(self, widget, scope):
        """Options of the ``ir.qweb.field.<widget>`` formatter, json-friendly."""
        Converter = self.env.get(f'ir.qweb.field.{widget}')
        if Converter is None:
            return []

        def clean(value):
            if isinstance(value, dict):
                return {str(k): clean(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [clean(v) for v in value]
            if value is None or isinstance(value, (bool, int, float)):
                return value
            return str(value)

        specs = []
        for name, spec in Converter.get_available_options().items():
            if spec.get('type') in ('json',) or name == 'widget':
                continue
            spec = clean(dict(spec))
            spec['name'] = name
            if spec.get('type') == 'model':
                spec['candidates'] = [
                    {'expr': expr, 'label': label} for expr, label in self._record_candidates(scope, spec.get('params'))]
            specs.append(spec)
        return specs

    def _display_info(self, report, view, node):
        """How the value shown by ``node`` is formatted: its field type, the
        formatters (widgets) that suit it with their options, the current values."""
        directive = next((name for name in ('t-field', 't-out', 't-esc') if node.get(name)), None)
        if not directive:
            return False
        scope = self._scope_at(report, view, node, include_self=False)
        field = self._value_field(scope, node.get(directive))
        field_type = field.type if field else ''
        if field_type and field_type in WIDGETS_BY_TYPE:
            keys = list(WIDGETS_BY_TYPE[field_type])
        elif field_type:
            keys = [field_type]
        else:
            keys = list(WIDGET_LABELS)
        if field_type == 'many2one' and field.comodel_name != 'res.partner':
            keys = [key for key in keys if key != 'contact']
        values = self._parse_options(node.get('t-options'))
        current = ((values or {}).get('widget') or {}).get('value') or ''
        if current and current not in keys:
            keys.append(current)
        default = field_type if field_type and f'ir.qweb.field.{field_type}' in self.env else ''
        widgets = [{'key': '', 'label': self.env._("Default (%s)", WIDGET_LABELS.get(default, default) or self.env._("as is"))}]
        widgets += [{'key': key, 'label': WIDGET_LABELS.get(key, key)} for key in keys
                    if key != default and f'ir.qweb.field.{key}' in self.env]
        options = {widget['key']: self._option_specs(widget['key'] or default, scope) if (widget['key'] or default) else []
                   for widget in widgets}
        return {
            'parsed': values is not None,
            'field_type': field_type,
            'field_label': field.string if field else '',
            'widget': current,
            'widgets': widgets,
            'options': options,
            'values': values or {},
        }

    def _op_set_display(self, report, op):
        """Set the display options (t-options) of the element ``op['target']`` from
        ``op['options']``: ``{name: {'value', 'expr'}}``; nothing removes them."""
        view, root, node = self._locate(op['target'])
        value = self._format_options(op.get('options'))
        spec = E.xpath(E.attribute(value, name='t-options'), expr=self._xpath_for(root, node), position='attributes')
        return {'history': [self._write_specs(view, [spec])], 'select': op['target']}

    def _op_set_expression(self, report, op):
        """Change one expression of the element ``op['target']`` or of a <t> inside it
        (``op['child']``: element-child indexes from the element)."""
        view, root, node = self._locate(op['target'])
        node = self._child_t(node, op.get('child'))
        name = op.get('attr') or ''
        if not name.startswith('t-') or not ATTR_NAME_RE.match(name):
            raise UserError(self.env._("Invalid attribute name: %s", name))
        value = str(op.get('value') or '').strip()
        self._check_expression(name, value)
        spec = E.xpath(E.attribute(value, name=name), expr=self._xpath_for(root, node), position='attributes')
        return {'history': [self._write_specs(view, [spec])], 'select': op['target']}

    def _op_set_attrs(self, report, op):
        view, root, node = self._locate(op['target'])
        spec = E.xpath(expr=self._xpath_for(root, node), position='attributes')
        for name, value in (op.get('attrs') or {}).items():
            if not ATTR_NAME_RE.match(name) or name in DESIGNER_ATTRS:
                raise UserError(self.env._("Invalid attribute name: %s", name))
            self._check_expression(name, value)
            spec.append(E.attribute(str(value or '').strip(), name=name))
        if not len(spec):
            return {'history': [], 'select': op['target']}
        return {'history': [self._write_specs(view, [spec])], 'select': op['target']}

    def _op_set_text(self, report, op):
        """Replace the static text of the element ``op['target']``, or of the <t> inside
        it at ``op['child']`` (element-child indexes, as for set_expression)."""
        view, root, node = self._locate(op['target'])
        if op.get('child'):
            node = self._child_t(node, op['child'])
            if not self._holds_text(node):
                raise UserError(self.env._("This part has dynamic content; edit its XML instead."))
        else:
            node = self._text_holder(node)
            if node is None:
                raise UserError(self.env._("This element has dynamic content; edit its XML instead."))
        text, children = self._clean_inner_html(op.get('html'))
        spec = E.xpath(*children, expr=self._xpath_for(root, node), position='replace', mode='inner')
        spec.text = text
        if not text and not children:
            # an empty inner replacement keeps the old content: blank it explicitly
            spec.text = ' '
        return {'history': [self._write_specs(view, [spec])], 'select': op['target']}

    def _op_replace_xml(self, report, op):
        view, root, node = self._locate(op['target'])
        elements = _parse_fragment(op.get('xml') or '')
        elements[0].set(MARK, '1')
        spec = E.xpath(*elements, expr=self._xpath_for(root, node), position='replace')
        select = self._landing(view, root, [spec])
        return {'history': [self._write_specs(view, [spec])], 'select': select}

    def _row_cells(self, row):
        """Cells of a table row (also inside <t> wrappers), merged cells excluded."""
        return [
            cell for cell in row.iterdescendants('td', 'th')
            if next(cell.iterancestors('tr')) is row and not cell.get('colspan')
        ]

    def _op_table_column(self, report, op):
        """Add a column left / right of a cell (an empty one, or one showing the field
        ``op['var'].op['path']`` of the table's lines, with its label in the header), or
        delete the cell's column."""
        view, root, cell = self._locate(op['target'])
        action = op.get('action')
        if cell.tag == 'table':
            # dropped on the table itself: a new last column
            first_row = next(cell.iter('tr'), None)
            cells = self._row_cells(first_row) if first_row is not None else []
            if not cells or action == 'delete':
                raise UserError(self.env._("Select a table cell first."))
            cell, action = cells[-1], 'right'
        if cell.tag not in ('td', 'th') or action not in ('left', 'right', 'delete'):
            raise UserError(self.env._("Select a table cell first."))
        field_column = None
        if op.get('var') and action != 'delete':
            table = self._table_of(cell)
            loop_vars = self._table_loop_vars(report, view, table)
            if op['var'] not in loop_vars:
                raise UserError(self.env._(
                    "“%s” is not one of the lines of this table: its fields cannot make a column.", op['var']))
            field, string, _expr = self._field_info(loop_vars, op['var'], op.get('path'))
            field_column = {
                'label': string,
                'numeric': field.type in NUMERIC_TYPES,
            }
        row = next(cell.iterancestors('tr'), None)
        table = next(cell.iterancestors('table'), None)
        if row is None or table is None or cell not in self._row_cells(row):
            raise UserError(self.env._("This cell cannot be used to change the columns."))
        index = self._row_cells(row).index(cell)
        specs = []
        for tr in table.iter('tr'):
            if next(tr.iterancestors('table')) is not table:
                continue
            cells = self._row_cells(tr)
            if index >= len(cells):
                continue
            reference = cells[index]
            if action == 'delete':
                specs.append(E.xpath(expr=self._xpath_for(root, reference), position='replace'))
                continue
            if field_column is None:
                new_cell = E.th(self.env._('New column')) if reference.tag == 'th' else E.td()
            elif reference.tag == 'th':
                new_cell = E.th(E.span(field_column['label']))
            else:
                # the field in the rows drawn by the lines loop, empty cells elsewhere
                # (totals, sections, notes...)
                scope = self._scope_at(report, view, reference, include_self=False)
                if op['var'] in scope:
                    new_cell = E.td(*self._field_elements(scope, op['var'], op.get('path'), False))
                else:
                    new_cell = E.td()
            if field_column and field_column['numeric']:
                new_cell.set('class', 'text-end')
            if tr is row:
                new_cell.set(MARK, '1')
            specs.append(E.xpath(
                new_cell, expr=self._xpath_for(root, reference),
                position='before' if action == 'left' else 'after',
            ))
        select = self._landing(view, root, specs) if action != 'delete' else False
        return {'history': [self._write_specs(view, specs)], 'select': select}

    # ------------------------------------------------------------------
    # Grid columns (Bootstrap .row > .col-N) and column widths
    # ------------------------------------------------------------------

    def _grid_columns(self, row):
        """Columns of a grid row (also inside <t> wrappers)."""
        columns = []
        for child in element_children(row):
            if child.tag == 't':
                columns.extend(self._grid_columns(child))
            elif _is_grid_column(child):
                columns.append(child)
        return columns

    def _grid_row_of(self, node):
        """``(row, column or None)`` when ``node`` is a grid row or one of its columns."""
        if _is_grid_row(node) and self._grid_columns(node):
            return node, None
        if _is_grid_column(node):
            parent = node.getparent()
            while parent is not None and parent.tag == 't':
                parent = parent.getparent()
            if parent is not None and _is_grid_row(parent) and node in self._grid_columns(parent):
                return parent, node
        return None, None

    def _sequential_specs(self, view, root, steps):
        """Build specs one after the other on a working copy of ``root``, so that each
        XPath is computed on the tree as the previous specs left it.

        :param steps: ``[(node of root, build(xpath) -> spec)]``
        :return: ``(specs, target of the MARK node or False)``
        """
        work = deepcopy(root)
        paths = []
        for step_node, _build in steps:
            paths.append(next(path for node, path in iter_paths(root) if node is step_node))
        work_nodes = [self._node(work, path) for path in paths]
        specs = []
        for work_node, (_node, build) in zip(work_nodes, steps):
            spec = build(self._xpath_for(work, work_node))
            try:
                work = apply_inheritance_specs(work, [deepcopy(spec)])
            except Exception as e:  # noqa: BLE001 - shown to the user, nothing is written
                raise UserError(self.env._("This change cannot be applied: %s", e)) from e
            specs.append(spec)
        select = False
        for node, path in iter_paths(work):
            if node.get(MARK):
                select = f'{view.id}:{path_to_str(path)}'
                break
        return specs, select

    def _width_step(self, node, width):
        """Step giving a grid column a width: twelfths (int, col-N class) or a percentage
        ("37.5%": col-auto class + style width, which the PDF engine renders like col-N)."""
        attributes = [E.attribute(_with_col_width(node, width), name='class')]
        style = _with_style_prop(node.get('style'), 'width', width if isinstance(width, str) else '')
        if style != (node.get('style') or ''):
            attributes.append(E.attribute(style, name='style'))
        return node, lambda xpath: E.xpath(*deepcopy(attributes), expr=xpath, position='attributes')

    def _even_column_widths(self, columns, count):
        """Even widths for ``count`` columns, in the unit the columns use now: automatic
        (None, all ``col``) stays automatic, percentages stay percentages, anything else
        becomes grid steps."""
        sizes = [_col_size(col) for col in columns]
        if all(size is None for size in sizes):
            return [None] * count
        if all(isinstance(size, str) for size in sizes):
            return _even_percents(count)
        return _even_widths(count) if count <= 12 else [None] * count

    def _visible_columns(self, view, root, columns, visible):
        """The ``columns`` drawn on the page (``visible``: their targets, from the
        preview); columns hidden by a condition for this record keep their width."""
        if visible is None:
            return columns
        visible = set(visible)
        paths = {node: f'{view.id}:{path_to_str(path)}' for node, path in iter_paths(root)}
        shown = [col for col in columns if paths.get(col) in visible]
        return shown or columns

    def _grid_add(self, view, root, row, reference, position, content, visible=None):
        """Add a column holding ``content`` before / after the column ``reference``;
        the widths of the visible columns are evened out so they fill the row."""
        columns = self._visible_columns(view, root, self._grid_columns(row), visible)
        widths = self._even_column_widths(columns, len(columns) + 1)
        new_column = E.div(*content, {MARK: '1'})
        new_column.set('class', _with_col_width(new_column, widths[-1]))
        if isinstance(widths[-1], str):
            new_column.set('style', f'width: {widths[-1]};')
        steps = [self._width_step(col, width) for col, width in zip(columns, widths) if _col_size(col) != width]
        steps.append((reference, lambda xpath: E.xpath(deepcopy(new_column), expr=xpath, position=position)))
        specs, select = self._sequential_specs(view, root, steps)
        return {'history': [self._write_specs(view, specs)], 'select': select}

    def _op_grid_column(self, report, op):
        """Add a column before / after the selected one (or at the start / end of the
        selected row), or delete the selected column. Widths are evened out."""
        view, root, node = self._locate(op['target'])
        action = op.get('action')
        row, column = self._grid_row_of(node)
        if row is None or action not in ('left', 'right', 'delete') or (action == 'delete' and column is None):
            raise UserError(self.env._("Select a column of a row of columns first."))
        columns = self._grid_columns(row)
        visible = op.get('visible')
        if action != 'delete':
            reference = column if column is not None else (columns[0] if action == 'left' else columns[-1])
            result = self._grid_add(view, root, row, reference, 'before' if action == 'left' else 'after',
                                    [E.p(self.env._('New column'))], visible)
            return {**result, 'select': result['select'] or op['target']}
        if len(columns) == 1:
            raise UserError(self.env._("This is the last column: delete the whole row instead."))
        shown = self._visible_columns(view, root, columns, visible)
        remaining = [col for col in shown if col is not column]
        widths = self._even_column_widths(shown, len(remaining)) if remaining else []
        steps = [self._width_step(col, width) for col, width in zip(remaining, widths) if _col_size(col) != width]
        steps.append((column, lambda xpath: E.xpath(expr=xpath, position='replace')))
        specs, _select = self._sequential_specs(view, root, steps)
        return {'history': [self._write_specs(view, specs)], 'select': False}

    def _new_column_target(self, op):
        """``(view, root, row, reference column)`` for a drop "as a new column" next to
        the column ``op['target']``."""
        view, root, node = self._locate(op['target'])
        row, column = self._grid_row_of(node)
        if column is None or op.get('position') not in ('before', 'after'):
            raise UserError(self.env._("Drop it between two columns."))
        return view, root, row, column

    def _op_column_widths(self, report, op):
        """Resize grid columns or table cells.

        ``op['columns']``: ``[{'target': column, 'width': 1..12}]`` (grid columns, in twelfths)
        ``op['cells']``: ``[{'target': cell, 'width': '25%'}]`` (table cells)
        """
        steps = []
        view = root = None
        items = [(item, 'grid') for item in op.get('columns') or []] + \
                [(item, 'cell') for item in op.get('cells') or []]
        if not items:
            return {'history': [], 'select': op.get('target') or False}
        for item, kind in items:
            item_view, path = self._parse_target(item.get('target'))
            if view is None:
                view, root = item_view, self._combined(item_view)
            elif item_view != view:
                raise UserError(self.env._("These columns come from different templates and cannot be resized together."))
            node = self._node(root, path)
            width = item.get('width')
            if kind == 'grid':
                # None: automatic (col, the visible columns share the row equally)
                valid = width is None or (isinstance(width, int) and 1 <= width <= 12) or \
                        (isinstance(width, str) and WIDTH_RE.match(width) and float(width[:-1]) <= 100)
                if not _is_grid_column(node) or not valid:
                    raise UserError(self.env._("Invalid column width."))
                steps.append(self._width_step(node, width))
            else:
                if node.tag not in ('td', 'th') or not WIDTH_RE.match(str(width or '')):
                    raise UserError(self.env._("Invalid column width."))
                style = _with_style_prop(node.get('style'), 'width', width)
                steps.append((node, lambda xpath, style=style: E.xpath(
                    E.attribute(style, name='style'), expr=xpath, position='attributes')))
        specs, _select = self._sequential_specs(view, root, steps)
        return {'history': [self._write_specs(view, specs)], 'select': op.get('target') or False}

    OPERATIONS = {
        'insert_block', 'insert_xml', 'insert_field', 'remove', 'duplicate', 'move',
        'move_sibling', 'set_attrs', 'set_text', 'replace_xml', 'table_column',
        'grid_column', 'column_widths', 'set_expression', 'set_display',
    }

    # ------------------------------------------------------------------
    # Client API
    # ------------------------------------------------------------------

    @api.model
    def get_setup(self, report_id, res_id=False):
        """What the designer needs to open a report."""
        self._check_access()
        report = self._report(report_id)
        Model = self.env[report.model]
        record = Model.browse(int(res_id)).exists() if res_id else Model
        if not record:
            record = Model.search([], limit=1)
        paper = report.get_paperformat()
        return {
            'report': {
                'id': report.id,
                'name': report.name,
                'model': report.model,
                'model_name': self.env['ir.model']._get(report.model).name,
                'report_name': report.report_name,
                'report_type': report.report_type,
                # made with Duplicate: can be deleted
                'copied_from': report.ncd_copied_from_id.name or False,
            },
            'res_id': record.id or False,
            'paper': {
                'width': paper.print_page_width if paper else 210,
                'height': paper.print_page_height if paper else 297,
                'margin_left': paper.margin_left if paper else 7,
                'margin_right': paper.margin_right if paper else 7,
                'name': paper.name if paper else 'A4',
            },
            'scope': self._scope_list(self._base_scope(report)),
            'blocks': [{'key': key, 'name': name, 'icon': icon} for key, name, icon, _xml in BLOCKS],
        }

    @api.model
    def get_fields(self, model):
        """Fields of ``model`` for the field palette."""
        self._check_access()
        if model not in self.env:
            raise UserError(self.env._("Unknown model %s", model))
        Model = self.env[model]
        result = []
        for name, description in Model.fields_get(attributes=['string', 'type', 'relation']).items():
            if name.startswith('__') or description['type'] in ('properties', 'properties_definition', 'json'):
                continue
            field = Model._fields[name]
            relation = description.get('relation') or False
            result.append({
                'name': name,
                'string': description['string'],
                'type': description['type'],
                'relation': relation,
                'relation_name': (self.env['ir.model']._get(relation).name or relation) if relation in self.env else '',
                'image': isinstance(field, fields.Image),
            })
        return sorted(result, key=lambda f: f['string'].lower())

    @api.model
    def get_node(self, report_id, target):
        """Description of the template element behind a selected preview element."""
        self._check_access()
        report = self._report(report_id)
        view, root, node = self._locate(target)
        can_contain = not is_leaf(node)
        holder = self._text_holder(node)
        text_only = holder is not None
        source = deepcopy(node)
        source.tail = None
        inner_html = ''
        if text_only:
            inner_html = str(escape(holder.text or '')) + ''.join(
                etree.tostring(child, encoding='unicode') for child in holder)
            if holder is not node:
                inner_html = inner_html.strip()
        try:
            xpath = self._xpath_for(root, node)
        except UserError:
            xpath = ''
        row, column = self._grid_row_of(node)
        grid = False
        if row is not None:
            columns = self._grid_columns(row)
            sizes = [_col_size(col) for col in columns]
            grid = {
                'is_column': column is not None,
                'count': len(columns),
                'index': columns.index(column) if column is not None else -1,
                'widths': sizes,
                'mode': 'percent' if any(isinstance(size, str) for size in sizes)
                        else 'auto' if all(size is None for size in sizes) else 'grid',
            }
        return {
            'grid': grid,
            # the text of a plain <t> holding all the content is in the Text box already
            'expressions': [item for item in self._expression_items(node)
                            if not (text_only and item['kind'] == 'text')],
            'target': target,
            'tag': node.tag,
            'attrs': dict(node.attrib),
            'source': etree.tostring(source, encoding='unicode'),
            'text_only': text_only,
            'inner_html': inner_html,
            'can_contain': can_contain,
            'is_cell': node.tag in ('td', 'th'),
            'xpath': xpath,
            'template': self._template_info(view),
            'scope': self._scope_list(
                self._scope_at(report, view, node, include_self=can_contain),
                self._table_loop_vars(report, view, table) if (table := self._table_of(node)) is not None else None,
            ),
            'variables': self._template_variables(report, view, node, include_self=can_contain),
            'display': self._display_info(report, view, node),
        }

    @api.model
    def check_expression(self, attr, value):
        """Error message for an invalid expression of the directive ``attr``, or False."""
        self._check_access()
        return self._expression_error(attr or '', value) or False

    @api.model
    def get_hidden(self, report_id, view_ids, rendered, style_hidden=()):
        """Elements of the previewed templates that are not visible on the page:

        * not rendered: their condition (t-if, t-elif, t-else, on them or on a <t>
          around them) is false for this record, or they repeat over an empty list;
          only the outermost ones, where their container is on the page;
        * ``style_hidden``: rendered but hidden by their classes / style (found by the
          client).

        Each one says whether the designer's changes hide it (``by_designer``: the
        condition, classes or style come from them), so that the user finds what they
        hid among the template's own optional parts.

        :param rendered: the ``data-ncd`` targets present in the preview
        """
        self._check_access()
        self._report(report_id)
        rendered = set(rendered or [])
        style_hidden = set(style_hidden or [])
        result = []
        for view in self.env['ir.ui.view'].browse([int(view_id) for view_id in view_ids or []]).exists():
            if view.type != 'qweb' or (view.key or '').startswith(CUSTOM_KEY_PREFIX):
                continue
            root, base_of = self._designer_tree(view)
            targets = {}
            for node, path in iter_paths(root):
                targets[node] = f'{view.id}:{path_to_str(path)}'
            for node, target in targets.items():
                if not self._selectable(node):
                    continue
                if target in style_hidden:
                    base = base_of(node)
                    result.append({
                        'target': target,
                        'label': self._node_label(node),
                        'template': view.key or view.name,
                        'kind': 'style',
                        'expression': '',
                        'own_condition': False,
                        'by_designer': base is not node and (
                            base is None or any(base.get(attr) != node.get(attr) for attr in ('class', 'style'))),
                    })
                    continue
                if target in rendered:
                    continue
                # the nearest selectable container must be on the page (or be the template's top)
                container = next((parent for parent in node.iterancestors() if self._selectable(parent)), None)
                if container is not None and targets.get(container) not in rendered:
                    continue
                reason = self._hidden_reason(node, container)
                if not reason:
                    continue
                holder, directive = reason.pop('holder'), reason.pop('directive')
                base = base_of(holder)
                by_designer = base is not holder and (base is None or base.get(directive) != holder.get(directive))
                result.append({
                    'target': target,
                    'label': self._node_label(node),
                    'template': view.key or view.name,
                    'by_designer': by_designer,
                    # the template's own condition, put back by "Show again"
                    'original_expression': (base.get(directive) or '') if by_designer and base is not None else '',
                    **reason,
                })
        return result

    def _designer_tree(self, view):
        """The combined arch of ``view``, and a function giving, for one of its nodes,
        the same node in the arch without the designer's changes (None: the designer
        added it; the node itself when the view has no designer changes)."""
        custom = self._custom_view(view)
        if custom:
            base = view.with_context(
                inherit_branding=False, nc_report_designer=False, nc_report_designer_base=True,
            )._get_combined_arch()
            base_nodes = list(base.iter(etree.Element))
            tagged = deepcopy(base)
            for index, node in enumerate(tagged.iter(etree.Element)):
                node.set(BASE_ATTR, str(index))
            try:
                # replays the designer view on the tagged copy: same result as the combined arch
                root = apply_inheritance_specs(tagged, etree.fromstring(custom.arch))
            except Exception:  # noqa: BLE001 - fall back to "nothing known about the designer"
                root = None
            if root is not None:
                def base_of(node):
                    index = node.get(BASE_ATTR)
                    return base_nodes[int(index)] if index is not None else None
                return root, base_of
        return self._combined(view), lambda node: node

    def _selectable(self, node):
        """Whether the preview annotates ``node`` (see ir_ui_view.annotate_tree)."""
        if not isinstance(node.tag, str) or node.tag in SKIPPED_TAGS:
            return False
        return not any(
            ancestor.get('t-ignore') or ancestor.tag in ('head', 'script', 'style')
            for ancestor in node.iterancestors()
        )

    def _hidden_reason(self, node, container):
        """Why ``node`` may be missing from the page: the closest condition or loop on it
        or on the <t> elements between it and ``container``."""
        current = node
        while current is not None and current is not container:
            if current is node or current.tag == 't':
                for directive, kind in (('t-if', 'condition'), ('t-elif', 'condition'),
                                        ('t-else', 'otherwise'), ('t-foreach', 'loop')):
                    if current.get(directive) is not None:
                        return {
                            'kind': kind,
                            'expression': current.get(directive) or '',
                            # a plain t-if on the element itself can simply be removed
                            'own_condition': current is node and directive == 't-if',
                            'holder': current,
                            'directive': directive,
                        }
            current = current.getparent()
        return None

    def _node_label(self, node):
        tag = node.tag
        name = node.get('name')
        classes = [cls for cls in _classes(node) if not cls.startswith('o_')][:2]
        label = f'{tag}[{name}]' if name else '.'.join([tag] + classes)
        directive = next((node.get(attr) for attr in ('t-field', 't-out', 't-esc') if node.get(attr)), None)
        if directive:
            return f'{label} {directive}'
        text = ' '.join(''.join(node.itertext()).split())
        return f'{label} “{text[:40]}{"…" if len(text) > 40 else ""}”' if text else label

    @api.model
    def apply_operation(self, report_id, op):
        """Apply one designer operation.

        :return: ``{'history': [undo entries], 'select': target of the element to select}``
        """
        self._check_access()
        report = self._report(report_id)
        if op.get('type') not in self.OPERATIONS:
            raise UserError(self.env._("Unknown operation: %s", op.get('type')))
        label = self._describe(op)
        result = getattr(self, f"_op_{op['type']}")(report, op)
        for entry in result['history']:
            entry['label'] = label
        self._log_change(report, label, result['history'])
        return result

    @api.model
    def restore(self, entries, side, report_id=False):
        """Undo (``side='before'``) or redo (``side='after'``) history entries."""
        self._check_access()
        if side not in ('before', 'after'):
            raise UserError(self.env._("Invalid history side."))
        logged = []
        for entry in (reversed(entries) if side == 'before' else entries):
            custom = self._check_custom_view(entry['view_id'])
            before = custom.arch
            custom.write({'arch': entry[side] or EMPTY_ARCH})
            logged.append({'base_id': custom.inherit_id.id, 'before': before, 'after': custom.arch})
        label = next((entry.get('label') for entry in entries if entry.get('label')), '')
        name = self.env._("Undo") if side == 'before' else self.env._("Redo")
        report = self.env['ir.actions.report'].browse(int(report_id)).exists() if report_id else None
        self._log_change(report, f'{name}: {label}' if label else name, logged)
        return True

    # ------------------------------------------------------------------
    # Dated list of the changes
    # ------------------------------------------------------------------

    OPERATION_LABELS = {
        'insert_block': "Add",
        'insert_xml': "Add",
        'insert_field': "Add field",
        'remove': "Delete",
        'duplicate': "Duplicate",
        'move': "Move",
        'move_sibling': "Move",
        'set_attrs': "Change",
        'set_text': "Edit text of",
        'replace_xml': "Edit XML of",
        'table_column': "Table column",
        'grid_column': "Columns of",
        'column_widths': "Column widths of",
        'set_expression': "Change",
        'set_display': "Format of",
    }

    def _describe(self, op):
        """A short description of operation ``op`` for the list of changes."""
        kind = op.get('type')
        label = self.OPERATION_LABELS.get(kind, kind or '')
        subject = ''
        if kind == 'insert_field':
            path = op.get('path') or []
            path = path if isinstance(path, str) else '.'.join(map(str, path))
            subject = '.'.join(part for part in (op.get('var'), path) if part)
        elif kind == 'insert_block':
            subject = next((name for key, name, _icon, _xml in BLOCKS if key == op.get('block')), '')
        else:
            try:
                _view, _root, node = self._locate(op.get('source') or op.get('target'))
                subject = self._node_label(node)
            except UserError:
                pass
        if kind == 'set_expression' and op.get('attr'):
            subject = f"{op['attr']} · {subject}" if subject else op['attr']
        return f'{label} {subject}'.strip()

    def _log_change(self, report, name, entries):
        """Record a change: ``entries`` as made by _write_specs (or with only base_id,
        before and after); several of the same template are merged."""
        by_base = {}
        for entry in entries:
            base_id = entry.get('base_id') or self.env['ir.ui.view'].browse(entry['view_id']).inherit_id.id
            before = entry['before'] if entry['before'] and entry['before'] != EMPTY_ARCH else False
            after = entry['after'] if entry['after'] and entry['after'] != EMPTY_ARCH else False
            if base_id in by_base:
                by_base[base_id]['after'] = after
            else:
                by_base[base_id] = {'base_id': base_id, 'before': before, 'after': after}
        logged = [entry for entry in by_base.values() if entry['before'] != entry['after']]
        if not logged:
            return self.env['nc.report.designer.change']
        return self.env['nc.report.designer.change'].create({
            'name': name or self.env._("Change"),
            'report_id': report.id if report else False,
            'view_ids': [(6, 0, list(by_base))],
            'entries': logged,
        })

    def _changes_of(self, view_ids):
        return self.env['nc.report.designer.change'].search([('view_ids', 'in', [int(v) for v in view_ids])])

    def _state_at(self, change_id, view_ids):
        """The designer view XML of the templates ``view_ids`` just after change
        ``change_id`` (0: before every listed change), for those changed since:
        ``{template id: arch or False}``."""
        view_ids = {int(view_id) for view_id in view_ids}
        state = {}
        later = self._changes_of(view_ids).filtered(lambda change: change.id > int(change_id or 0))
        for change in later.sorted('id'):
            for entry in change.entries or []:
                if entry['base_id'] in view_ids and entry['base_id'] not in state:
                    state[entry['base_id']] = entry['before']
        return state

    def _apply_state(self, state):
        """Give the templates the designer view XML of ``state`` (see _state_at).

        :return: the entries of the change made (base_id, before, after)
        """
        entries = []
        for base_id, arch in state.items():
            view = self.env['ir.ui.view'].browse(base_id).exists()
            if not view:
                continue
            before = self._custom_view(view).arch or False
            self._set_designer_arch(view, arch)
            entries.append({'base_id': base_id, 'before': before, 'after': arch or False})
        return entries

    # ------------------------------------------------------------------
    # Rename / duplicate
    # ------------------------------------------------------------------

    MAX_COPIED_TEMPLATES = 20

    def _clean_name(self, name):
        name = ' '.join((name or '').split())
        if not name:
            raise UserError(self.env._("Give the report a name."))
        return name

    @api.model
    def rename_report(self, report_id, name):
        """Rename a report (also its entry in the Print menu)."""
        self._check_access()
        report = self._report(report_id)
        report.name = self._clean_name(name)
        return True

    @api.model
    def get_report_settings(self, report_id):
        """What the Edit dialog of the designer shows."""
        self._check_access()
        report = self._report(report_id)
        return {
            'name': report.name,
            'print_menu': bool(report.binding_model_id),
            'print_company_ids': report.ncd_print_company_ids.ids,
            'multi_company': len(self.env['res.company'].sudo().search([], limit=2)) > 1,
        }

    @api.model
    def save_report_settings(self, report_id, values):
        """Name, and whether (and for which companies) the report is in the Print menu."""
        self._check_access()
        report = self._report(report_id)
        report.name = self._clean_name(values.get('name'))
        if values.get('print_menu'):
            if not report.binding_model_id:
                report.create_action()
            companies = self.env['res.company'].browse(values.get('print_company_ids') or []).exists()
        else:
            report.unlink_action()
            companies = self.env['res.company']
        report.ncd_print_company_ids = [(6, 0, companies.ids)]
        return True

    # Emails --------------------------------------------------------------

    def _email_templates(self, report):
        if 'mail.template' not in self.env or 'report_template_ids' not in self.env['mail.template']._fields:
            return None
        return self.env['mail.template'].search([('model', '=', report.model)])

    @api.model
    def get_email_templates(self, report_id):
        """The email templates of the report's model, with the reports they attach
        (None: emails are not installed)."""
        self._check_access()
        report = self._report(report_id)
        templates = self._email_templates(report)
        if templates is None:
            return None
        return [{
            'id': template.id,
            'name': template.name,
            'reports': [{'id': r.id, 'name': r.name} for r in template.report_template_ids],
            'uses_this': report in template.report_template_ids,
        } for template in templates]

    @api.model
    def set_email_report(self, report_id, template_id, use=True):
        """Attach the report to the emails of a template instead of the other reports of
        the same records (``use``), or stop attaching it."""
        self._check_access()
        report = self._report(report_id)
        templates = self._email_templates(report)
        template = templates.filtered(lambda t: t.id == int(template_id)) if templates is not None else None
        if not template:
            raise UserError(self.env._("This email template cannot be found."))
        if use:
            others = template.report_template_ids.filtered(lambda r: r.model == report.model)
            template.report_template_ids = [(3, other.id) for other in others] + [(4, report.id)]
        else:
            template.report_template_ids = [(3, report.id)]
        return True

    # Delete --------------------------------------------------------------

    @api.model
    def delete_report(self, report_id):
        """Delete a report made with Duplicate, and its templates.

        :return: the id of the report it was copied from (to design next), or False
        """
        self._check_access()
        report = self._report(report_id)
        if not report.ncd_copied_from_id or not (report.report_name or '').startswith('nc_report_designer.copy_'):
            raise UserError(self.env._(
                "Only reports made with Duplicate can be deleted. To stop offering this one, "
                "remove it from the Print menu (Edit) or from the emails."))
        masters = self.env['ir.actions.report'].search([
            '|', ('ncd_other_report_id', '=', report.id), ('ncd_rule_ids.target_report_id', '=', report.id),
        ])
        if masters:
            raise UserError(self.env._(
                "“%(report)s” is used for some companies by “%(masters)s”: change that first (Companies).",
                report=report.name, masters=', '.join(masters.mapped('name'))))
        source = report.ncd_copied_from_id
        # emails attaching the copy attach the report it was copied from again
        templates = self._email_templates(report)
        if templates is not None and source:
            for template in templates.filtered(lambda t: report in t.report_template_ids):
                template.report_template_ids = [(3, report.id), (4, source.id)]
        # the copied templates share the copy's token: nc_report_designer.copy_<token>_<key>
        token = report.report_name[len('nc_report_designer.copy_'):].split('_', 1)[0]
        views = self.env['ir.ui.view'].search([('key', '=like', f'nc_report_designer.copy_{token}_%')])
        customs = self.env['ir.ui.view'].search([('inherit_id', 'in', views.ids)])
        # its listed changes go too (those also touching a shared layout are kept)
        self._changes_of(views.ids).filtered(lambda change: change.view_ids <= views).unlink()
        report.unlink()
        customs.unlink()
        views.unlink()
        return source.id or False

    def _own_templates(self, report):
        """The templates of ``report``: its main one and those it calls (not the
        layouts shared by all reports), in calling order."""
        IrUiView = self.env['ir.ui.view']
        main = IrUiView._get_template_view(report.report_name, raise_if_not_found=False)
        if not main:
            raise UserError(self.env._("The template of this report (%s) cannot be found.", report.report_name))
        found = []

        def walk(view, depth):
            if view in found or depth > 6 or len(found) >= self.MAX_COPIED_TEMPLATES:
                return
            found.append(view)
            for node in self._combined(view).iter():
                called = node.get('t-call') if isinstance(node.tag, str) else None
                if not called or '{' in called or self._is_shared_key(called):
                    continue
                if sub := IrUiView._get_template_view(called, raise_if_not_found=False):
                    walk(sub, depth + 1)

        walk(main, 0)
        return found

    def _is_shared_key(self, key):
        return (key or '').startswith('web.')

    @api.model
    def duplicate_report(self, report_id, name, print_menu=True):
        """A copy of the report with copies of its own templates (as they print now,
        designer changes included), to change separately. The shared layouts stay shared.

        :return: the id of the copy
        """
        self._check_access()
        report = self._report(report_id)
        name = self._clean_name(name)
        templates = self._own_templates(report)
        token = secrets.token_hex(3)
        keys = {
            view.key: f'nc_report_designer.copy_{token}_{re.sub(r"[^\w.]+", "_", view.key or str(view.id))}'
            for view in templates
        }
        for view in templates:
            root = self._combined(view)
            for node in root.iter():
                if isinstance(node.tag, str) and node.get('t-call') in keys:
                    node.set('t-call', keys[node.get('t-call')])
            if root.get('t-name'):
                root.set('t-name', keys[view.key])
            self.env['ir.ui.view'].create({
                'name': f'{name}: {view.name}',
                'type': 'qweb',
                'key': keys[view.key],
                'arch': etree.tostring(root, encoding='unicode'),
            })
        main_key = keys[templates[0].key]
        copy = report.copy({
            'name': name,
            'report_name': main_key,
            'report_file': main_key,
            'ncd_copied_from_id': report.ncd_copied_from_id.id or report.id,
            'binding_model_id': report.binding_model_id.id if print_menu else False,
        })
        return copy.id

    # ------------------------------------------------------------------
    # Report per company
    # ------------------------------------------------------------------

    @api.model
    def get_company_routing(self, report_id, res_id=False):
        """The companies the report applies to, and the reports used for the others.

        ``record``: when the previewed record prints with another report, that report."""
        self._check_access()
        report = self._report(report_id)

        def info(target):
            return {'id': target.id, 'name': target.name} if target else False

        result = {
            'company_ids': report.ncd_company_ids.ids,
            'company_names': report.ncd_company_ids.mapped('name'),
            'rules': [{
                'id': rule.id,
                'company_ids': rule.company_ids.ids,
                'report_id': rule.target_report_id.id,
            } for rule in report.ncd_rule_ids],
            'other_report_id': report.ncd_other_report_id.id or False,
            'multi_company': len(self.env['res.company'].sudo().search([], limit=2)) > 1,
            'record': False,
        }
        if res_id and report.ncd_company_ids and report.model in self.env:
            record = self.env[report.model].browse(int(res_id)).exists()
            if record:
                company = report._ncd_company_of(record)
                target = report._ncd_report_for(company)
                if target != report:
                    result['record'] = {'company': company.name, 'report': info(target)}
        return result

    @api.model
    def save_company_routing(self, report_id, values):
        """Save the companies the report applies to (none: all companies) and the reports
        used for the others: ``{'company_ids', 'rules': [{'company_ids', 'report_id'}],
        'other_report_id'}``."""
        self._check_access()
        report = self._report(report_id)
        Company = self.env['res.company']
        Report = self.env['ir.actions.report']
        companies = Company.browse(values.get('company_ids') or []).exists()
        rules = []
        if companies:
            used = set(companies.ids)
            for rule in values.get('rules') or []:
                rule_companies = Company.browse(rule.get('company_ids') or []).exists()
                target = Report.browse(int(rule.get('report_id') or 0)).exists()
                if not rule_companies and not target:
                    continue  # an empty line
                if not rule_companies:
                    raise UserError(self.env._("Choose the companies of each line, or remove the line."))
                if not target:
                    raise UserError(self.env._(
                        "Choose the report used for %s.", ', '.join(rule_companies.mapped('name'))))
                if twice := rule_companies.filtered(lambda company: company.id in used):
                    raise UserError(self.env._(
                        "%s is listed more than once: a company can only use one report.",
                        ', '.join(twice.mapped('name'))))
                used |= set(rule_companies.ids)
                report._ncd_check_target(target)
                rules.append((rule_companies, target))
            other = Report.browse(int(values.get('other_report_id') or 0)).exists()
            if not other:
                raise UserError(self.env._("Choose the report used for the other companies."))
            report._ncd_check_target(other)
        else:
            other = Report
        report.ncd_rule_ids.unlink()
        report.write({
            'ncd_company_ids': [(6, 0, companies.ids)],
            'ncd_other_report_id': other.id or False,
            'ncd_rule_ids': [(0, 0, {
                'sequence': index,
                'company_ids': [(6, 0, rule_companies.ids)],
                'target_report_id': target.id,
            }) for index, (rule_companies, target) in enumerate(rules)],
        })
        return True

    @api.model
    def get_history(self, view_ids, limit=300):
        """The changes made with the designer to the templates ``view_ids`` (those of the
        preview), the latest first."""
        self._check_access()
        changes = self._changes_of(view_ids)
        return {
            'changes': [{
                'id': change.id,
                'name': change.name,
                'date': fields.Datetime.to_string(change.create_date),
                'user': change.create_uid.name or '',
                'templates': [view.key or view.name for view in change.view_ids],
            } for change in changes[:limit]],
            'more': max(len(changes) - limit, 0),
        }

    @api.model
    def reset_to(self, change_id, view_ids, report_id=False):
        """Put the templates ``view_ids`` back as they were just after change ``change_id``
        (0: before every listed change). This is a change of its own (it can be undone the
        same way)."""
        self._check_access()
        state = self._state_at(change_id, view_ids)
        if not state:
            return False
        if int(change_id or 0):
            change = self.env['nc.report.designer.change'].browse(int(change_id)).exists()
            if not change:
                raise UserError(self.env._("This change no longer exists."))
            date = fields.Datetime.context_timestamp(self, change.create_date).strftime('%Y-%m-%d %H:%M')
            name = self.env._("Back to “%(name)s” of %(date)s", name=change.name, date=date)
        else:
            name = self.env._("Back to the original templates")
        report = self.env['ir.actions.report'].browse(int(report_id)).exists() if report_id else None
        self._log_change(report, name, self._apply_state(state))
        return True

    @api.model
    def get_changes(self, view_ids):
        """Designer views of the given templates (those shown in the preview)."""
        self._check_access()
        customs = self.env['ir.ui.view'].search([
            ('inherit_id', 'in', [int(view_id) for view_id in view_ids]),
            ('key', '=like', f'{CUSTOM_KEY_PREFIX}%'),
        ])
        result = []
        for custom in customs:
            specs = element_children(etree.fromstring(custom.arch))
            result.append({
                'id': custom.id,
                'arch': custom.arch,
                'count': len(specs),
                'template': self._template_info(custom.inherit_id),
            })
        return result

    @api.model
    def save_change_arch(self, view_id, arch):
        """Save the XML of a designer view edited by hand."""
        self._check_access()
        custom = self._check_custom_view(view_id)
        before = custom.arch
        try:
            etree.fromstring(arch)
        except etree.XMLSyntaxError as e:
            raise UserError(self.env._("Invalid XML: %s", e)) from e
        custom.write({'arch': arch})
        entry = {
            'view_id': custom.id, 'base_id': custom.inherit_id.id, 'before': before, 'after': custom.arch,
            'label': self.env._("Edit the XML of the changes of %s", custom.inherit_id.key or custom.inherit_id.name),
        }
        self._log_change(None, entry['label'], [entry])
        return entry

    @api.model
    def reset_change(self, view_id):
        """Drop every designer change of a template."""
        self._check_access()
        custom = self._check_custom_view(view_id)
        base = custom.inherit_id
        self._log_change(None, self.env._("Reset %s to its original", base.key or base.name),
                         [{'base_id': base.id, 'before': custom.arch, 'after': False}])
        custom.unlink()
        return True
