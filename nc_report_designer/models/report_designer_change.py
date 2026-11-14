from odoo import fields, models


class NcReportDesignerChange(models.Model):
    """One change made with the report designer, kept to list them with their date, look
    at the report as it was at any point and go back to it.

    ``entries`` holds, for each template the change touched, the XML of its designer view
    before and after it: ``[{'base_id': template id, 'before': arch, 'after': arch}]``
    (False: the template had no designer view).
    """
    _name = 'nc.report.designer.change'
    _description = 'Report Designer Change'
    _order = 'id desc'

    name = fields.Char(required=True)
    report_id = fields.Many2one('ir.actions.report', ondelete='set null')
    view_ids = fields.Many2many(
        'ir.ui.view', 'nc_report_designer_change_view_rel', 'change_id', 'view_id',
        string='Templates',
    )
    entries = fields.Json(required=True)
