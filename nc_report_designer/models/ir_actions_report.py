import io

from odoo import api, fields, models
from odoo.exceptions import UserError


class NcReportCompanyRule(models.Model):
    """Companies of the records printed with a report that use another report instead
    (see ir.actions.report ncd_company_ids)."""
    _name = 'nc.report.company.rule'
    _description = 'Report for Companies'
    _order = 'sequence, id'

    report_id = fields.Many2one('ir.actions.report', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    company_ids = fields.Many2many(
        'res.company', 'nc_report_company_rule_company_rel', 'rule_id', 'company_id',
        string='Companies', required=True,
    )
    target_report_id = fields.Many2one('ir.actions.report', string='Report used', required=True, ondelete='cascade')


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    # A "master" report: printed (or attached to an email) for records of these companies;
    # records of the companies of a rule use the rule's report, those of any other company
    # the "other companies" report. No company: the report is used for every company.
    ncd_company_ids = fields.Many2many(
        'res.company', 'nc_report_designer_report_company_rel', 'report_id', 'company_id',
        string='Applies to companies', copy=False,
    )
    ncd_rule_ids = fields.One2many('nc.report.company.rule', 'report_id', string='Reports of other companies', copy=False)
    ncd_other_report_id = fields.Many2one(
        'ir.actions.report', string='Report for other companies', ondelete='set null', copy=False,
    )
    # duplicated with the report designer: its templates are copies of those of this report
    ncd_copied_from_id = fields.Many2one('ir.actions.report', string='Copied from', ondelete='set null', copy=False)
    # in the Print menu only while one of these companies is the current one (none: all)
    ncd_print_company_ids = fields.Many2many(
        'res.company', 'nc_report_designer_print_company_rel', 'report_id', 'company_id',
        string='Print menu for companies', copy=False,
    )

    def action_open_report_designer(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'nc_report_designer.designer',
            'name': self.env._('Report Designer'),
            'params': {'report_id': self.id},
        }

    def _get_rendering_context_model(self, report):
        # a copy has its own template name: it uses the values the original's report model computes
        model = super()._get_rendering_context_model(report)
        if model is None and report.ncd_copied_from_id:
            return self._get_rendering_context_model(report.ncd_copied_from_id)
        return model

    # ------------------------------------------------------------------
    # Report per company
    # ------------------------------------------------------------------

    def _ncd_company_of(self, record):
        field = record._fields.get('company_id')
        if field is not None and field.type == 'many2one' and field.comodel_name == 'res.company':
            return record.company_id or self.env.company
        return self.env.company

    def _ncd_report_for(self, company):
        """The report used to print a record of ``company`` with this (master) report."""
        self.ensure_one()
        if not self.ncd_company_ids or company in self.ncd_company_ids:
            return self
        for rule in self.ncd_rule_ids:
            if company in rule.company_ids:
                return rule.target_report_id
        return self.ncd_other_report_id or self

    def _ncd_routes(self, report_ref, res_ids):
        """``[(report, ids)]`` printing ``res_ids`` with ``report_ref`` and the company
        rules of that report, or None when they all use the report itself."""
        if not res_ids or self.env.context.get('nc_report_designer') or self.env.context.get('ncd_routed'):
            return None
        report = self._get_report(report_ref)
        if not report.ncd_company_ids or report.model not in self.env:
            return None
        routes = {}
        records = self.env[report.model].sudo().browse(res_ids).exists()
        for record in records:
            routes.setdefault(report._ncd_report_for(self._ncd_company_of(record)), []).append(record.id)
        if not routes or list(routes) == [report]:
            return None
        return list(routes.items())

    def _ncd_render_routed(self, method, routes, data):
        routed = self.with_context(ncd_routed=True)
        results = [getattr(routed, method)(report.id, ids, data=dict(data or {})) for report, ids in routes]
        if len(results) == 1:
            return results[0]
        kinds = {kind for _content, kind in results}
        if kinds == {'pdf'}:
            streams = [io.BytesIO(content) for content, _kind in results]
            with self._merge_pdfs(streams) as merged:
                content = merged.getvalue()
            for stream in streams:
                stream.close()
            return content, 'pdf'
        contents = [content if isinstance(content, bytes) else content.encode() for content, _kind in results]
        return b''.join(contents), results[0][1]

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        routes = self._ncd_routes(report_ref, [res_ids] if isinstance(res_ids, int) else res_ids)
        if routes is None:
            return super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)
        return self._ncd_render_routed('_render_qweb_pdf', routes, data)

    @api.model
    def _render_qweb_html(self, report_ref, docids, data=None):
        routes = self._ncd_routes(report_ref, [docids] if isinstance(docids, int) else docids)
        if routes is None:
            return super()._render_qweb_html(report_ref, docids, data=data)
        return self._ncd_render_routed('_render_qweb_html', routes, data)

    def _ncd_check_target(self, target):
        if target == self:
            raise UserError(self.env._("A report cannot be used instead of itself."))
        if target.model != self.model:
            raise UserError(self.env._(
                "“%(report)s” prints %(model)s records, not those of this report.",
                report=target.name, model=target.model))
        if not (target.report_type or '').startswith('qweb-'):
            raise UserError(self.env._("“%s” is not a QWeb report.", target.name))
        if target.ncd_company_ids:
            raise UserError(self.env._(
                "“%s” chooses a report per company itself: pick a report that does not.", target.name))
