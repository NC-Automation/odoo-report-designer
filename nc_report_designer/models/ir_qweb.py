from odoo import models


class IrQweb(models.AbstractModel):
    _inherit = 'ir.qweb'

    def _get_template_cache_keys(self):
        # Templates compiled for the designer carry source-mapping attributes: keep
        # them in their own cache entries so normal printing never sees them.
        # The element forced visible (hidden one selected in the designer) changes them too.
        # An earlier state of the report (looked at in the Changes tab) too.
        return super()._get_template_cache_keys() + [
            'nc_report_designer', 'nc_report_designer_force', 'nc_report_designer_at',
        ]
