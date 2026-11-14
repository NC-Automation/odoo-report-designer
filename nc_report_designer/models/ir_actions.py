from odoo import api, models


class IrActionsActions(models.Model):
    _inherit = 'ir.actions.actions'

    @api.model
    def get_bindings(self, model_name):
        """Leave out of the Print menu the reports set to show there only for other
        companies than the current one (ir.actions.report ncd_print_company_ids)."""
        result = super().get_bindings(model_name)
        reports = result.get('report')
        if reports:
            self.env.cr.execute(
                "SELECT report_id, array_agg(company_id) FROM nc_report_designer_print_company_rel"
                " WHERE report_id = ANY(%s) GROUP BY report_id",
                [[action['id'] for action in reports]],
            )
            restricted = dict(self.env.cr.fetchall())
            if restricted:
                company = self.env.company.id
                result['report'] = [
                    action for action in reports
                    if action['id'] not in restricted or company in restricted[action['id']]
                ]
        return result
