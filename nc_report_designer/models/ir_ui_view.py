from odoo import api, models
from odoo.fields import Domain

# Attribute carrying "<view id>:<element path>" on every rendered element of a
# template compiled for the designer; the path is the list of element-child
# indexes from the root of the view's combined arch, e.g. "0.3.1".
NCD_ATTR = 'data-ncd'
# Elements whose content comes from an expression or which cannot hold children.
NCD_LEAF_ATTR = 'data-ncd-leaf'

VOID_TAGS = {'area', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'wbr'}
CONTENT_DIRECTIVES = ('t-field', 't-out', 't-esc', 't-raw', 't-call')
# Elements never offered for selection.
SKIPPED_TAGS = {'t', 'html', 'head', 'body', 'title', 'meta', 'link', 'script', 'style', 'base'}


def element_children(node):
    return [child for child in node if isinstance(child.tag, str)]


def iter_paths(root):
    """Yield ``(element, path)`` for every element of ``root`` (pre-order)."""
    stack = [(root, ())]
    while stack:
        node, path = stack.pop()
        yield node, path
        children = element_children(node)
        for index in range(len(children) - 1, -1, -1):
            stack.append((children[index], path + (index,)))


def path_to_str(path):
    return '.'.join(map(str, path))


def is_leaf(node):
    return node.tag in VOID_TAGS or any(node.get(attr) for attr in CONTENT_DIRECTIVES)


CONDITION_DIRECTIVES = ('t-if', 't-elif', 't-else')
# Marks an element shown although hidden (see force_visible); the preview hatches it.
NCD_GHOST_ATTR = 'data-ncd-ghost'


def annotate_tree(root, view_id, force=None):
    """Add designer source-mapping attributes to the elements of a combined arch.

    :param force: targets ("<view id>:<path>", comma-separated) of hidden elements to
        show anyway
    """
    force = set((force or '').split(',')) - {''}
    skipped = set()
    for node, path in iter_paths(root):
        if node in skipped:
            continue
        if node.get('t-ignore') or node.tag in ('head', 'script', 'style'):
            skipped.update(node.iterdescendants())
            continue
        if node.tag in SKIPPED_TAGS:
            continue
        target = f'{view_id}:{path_to_str(path)}'
        node.set(NCD_ATTR, target)
        if is_leaf(node):
            node.set(NCD_LEAF_ATTR, '1')
        if target in force:
            force_visible(node)


def force_visible(node):
    """Show ``node`` whatever its condition and those of the elements around it (in this
    template): each t-if / t-elif / t-else becomes t-if="True" (what follows it in that
    condition chain is then hidden: this only serves to look at the element). The
    element, and the elements around it that were hidden, are marked to be hatched."""
    current = node
    while current is not None:
        if any(current.get(name) is not None for name in CONDITION_DIRECTIVES):
            condition = current.get('t-if')
            for name in CONDITION_DIRECTIVES:
                current.attrib.pop(name, None)
            current.set('t-if', 'True')
            node.set(NCD_GHOST_ATTR, '1')
            if current is not node and current.tag != 't':
                if condition:
                    # hatched only when it is hidden for this record
                    current.set(f't-att-{NCD_GHOST_ATTR}', f"None if ({condition}) else '1'")
                else:
                    current.set(NCD_GHOST_ATTR, '1')
        current = current.getparent()


class IrUiView(models.Model):
    _inherit = 'ir.ui.view'

    def _get_view_etrees(self):
        trees = super()._get_view_etrees()
        if self.env.context.get('nc_report_designer'):
            force = self.env.context.get('nc_report_designer_force')
            for view, tree in zip(self, trees):
                annotate_tree(tree, view.id, force)
        return trees

    @api.model
    def _get_inheriting_views_domain(self):
        domain = super()._get_inheriting_views_domain()
        if self.env.context.get('nc_report_designer_base'):
            # the templates as they are without the report designer's changes
            domain &= Domain('key', 'not like', 'nc_report_designer.custom_%')
        return domain
