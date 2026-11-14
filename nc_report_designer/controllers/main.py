import logging
import re

from markupsafe import escape
from werkzeug.exceptions import Forbidden, NotFound

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)
TARGET_RE = re.compile(r'^\d+:\d+(\.\d+)*$')
# designer targets of the elements quoted in a template error
NCD_IN_ERROR_RE = re.compile(r'data-ncd="(\d+:[\d.]+)"')
MAX_FORCED_ATTEMPTS = 12

ERROR_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/><title>Report error</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 24px; color: #333; }}
h3 {{ color: #a33; }}
pre {{ white-space: pre-wrap; background: #f6f6f6; padding: 12px; border-radius: 4px; font-size: 12px; }}
</style></head>
<body data-ncd-error="1">
<h3>The report could not be drawn</h3>
<p>The last change may have broken the template: use <b>Undo</b>, or fix the element in the
<b>Changes</b> tab.</p>
<pre>{message}</pre>
</body></html>"""


class _Discard(Exception):
    """Rolls back what was written to draw an earlier state of the report."""


class ReportDesignerController(http.Controller):

    def _render_preview(self, env, report, res_id, forced, state=None, at=False):
        """:param state: designer view XML of the templates as they were at an earlier
            point (see nc.report.designer._state_at): written for this rendering only"""
        # the forced targets as one string: it is part of the compiled templates' cache key
        html = None
        try:
            with env.cr.savepoint():
                if state:
                    env['nc.report.designer']._apply_state(state)
                html, _type = env['ir.actions.report'].with_context(
                    nc_report_designer=True,
                    nc_report_designer_force=','.join(sorted(forced)) or False,
                    nc_report_designer_at=at or False,
                )._render_qweb_html(report.id, [res_id])
                if state:
                    raise _Discard()
        except _Discard:
            pass
        finally:
            if state:
                # the templates compiled with the earlier XML must not be used afterwards
                env.invalidate_all()
                env.registry.clear_cache('templates')
        return html

    @http.route('/nc_report_designer/preview/<int:report_id>/<int:res_id>', type='http', auth='user')
    def preview(self, report_id, res_id, **kwargs):
        """The report rendered as HTML for one record, with designer source mapping."""
        env = request.env
        if not env.user.has_group('base.group_system'):
            raise Forbidden()
        report = env['ir.actions.report'].browse(report_id).exists()
        if not report:
            raise NotFound()
        # hidden elements drawn anyway (hatched): the selected one, or all of them while
        # the Hidden tab is open
        forced = {target for target in (kwargs.get('force') or '').split(',') if TARGET_RE.match(target)}
        # the report as it was just after an earlier change (at=0: before them all), for
        # the templates of the preview (views)
        state, at = None, False
        if (kwargs.get('at') or '').isdigit():
            view_ids = [int(v) for v in (kwargs.get('views') or '').split(',') if v.isdigit()]
            at = f"{kwargs['at']}:{','.join(map(str, sorted(view_ids)))}"
            state = env['nc.report.designer']._state_at(int(kwargs['at']), view_ids)
            forced = set()
        dropped = set()
        for _attempt in range(MAX_FORCED_ATTEMPTS):
            try:
                html = self._render_preview(env, report, res_id, forced, state, at)
                break
            except Exception as e:  # noqa: BLE001 - shown in the preview
                if not forced:
                    _logger.info("Report designer preview of %s failed: %s", report.report_name, e)
                    html = ERROR_PAGE.format(message=escape(str(e)))
                    break
                # A hidden element may not draw for this record (its condition guarded its
                # content): leave out the forced element around the failing one, else half
                # of them, and try again.
                failing = NCD_IN_ERROR_RE.findall(str(e))
                culprits = {
                    target for target in forced
                    if any(ncd == target or ncd.startswith(f'{target}.') for ncd in failing)
                } or set(sorted(forced)[len(forced) // 2:])
                forced -= culprits
                dropped |= culprits
        else:
            # too many attempts: the page as it is
            dropped |= forced
            try:
                html = self._render_preview(env, report, res_id, set(), state, at)
            except Exception as e:  # noqa: BLE001 - shown in the preview
                html = ERROR_PAGE.format(message=escape(str(e)))
        if isinstance(html, bytes):
            html = html.decode()
        if dropped:
            # tells the designer what could not be drawn
            html = html.replace('<body', f'<body data-ncd-unforced="{len(dropped)}"', 1)
        return request.make_response(html, headers=[
            ('Content-Type', 'text/html; charset=utf-8'),
            ('Cache-Control', 'no-store'),
        ])
