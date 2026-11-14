from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Changes are listed from this version on: the designer views made before become
    one first change each, dated when they were last written, so the list can go back
    to the original templates."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    customs = env['ir.ui.view'].with_context(active_test=False).search([
        ('key', '=like', 'nc_report_designer.custom_%'),
    ], order='write_date, id')
    for custom in customs:
        change = env['nc.report.designer.change'].create({
            'name': env._("Changes made before the list was kept"),
            'view_ids': [(6, 0, custom.inherit_id.ids)],
            'entries': [{'base_id': custom.inherit_id.id, 'before': False, 'after': custom.arch}],
        })
        cr.execute(
            "UPDATE nc_report_designer_change SET create_date = %s, create_uid = %s WHERE id = %s",
            (custom.write_date, custom.write_uid.id or SUPERUSER_ID, change.id),
        )
