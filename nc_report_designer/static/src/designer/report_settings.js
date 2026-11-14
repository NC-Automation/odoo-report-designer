import { Component, onWillStart, proxy, t, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { MultiRecordSelector } from "@web/core/record_selectors/multi_record_selector";

const MODEL = "nc.report.designer";

function errorText(error) {
    return error.data?.message || error.message || String(error);
}

/** Edit a report: its name, and whether (and for which companies) it is in the Print menu. */
export class ReportEditDialog extends Component {
    static template = "nc_report_designer.ReportEditDialog";
    static components = { Dialog, MultiRecordSelector };
    props = useProps({
        reportId: t.number(),
        onSaved: t.function(), // (name) => void
        close: t.function(),
    });

    setup() {
        this.orm = useService("orm");
        this.state = proxy({ loaded: false, name: "", printMenu: true, printCompanyIds: [], multiCompany: false, error: "", saving: false });
        onWillStart(async () => {
            const settings = await this.orm.call(MODEL, "get_report_settings", [this.props.reportId]);
            Object.assign(this.state, {
                name: settings.name,
                printMenu: settings.print_menu,
                printCompanyIds: settings.print_company_ids,
                multiCompany: settings.multi_company,
                loaded: true,
            });
        });
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.save();
        }
    }

    async save() {
        if (!this.state.name.trim()) {
            this.state.error = _t("Give the report a name.");
            return;
        }
        this.state.saving = true;
        try {
            await this.orm.call(MODEL, "save_report_settings", [
                this.props.reportId,
                {
                    name: this.state.name.trim(),
                    print_menu: this.state.printMenu,
                    print_company_ids: this.state.printCompanyIds,
                },
            ]);
        } catch (error) {
            this.state.error = errorText(error);
            return;
        } finally {
            this.state.saving = false;
        }
        this.props.onSaved(this.state.name.trim());
        this.props.close();
    }
}

/** The email templates of the report's records: attach this report to their emails. */
export class ReportEmailsDialog extends Component {
    static template = "nc_report_designer.ReportEmailsDialog";
    static components = { Dialog };
    props = useProps({
        reportId: t.number(),
        reportName: t.string(),
        close: t.function(),
    });

    setup() {
        this.orm = useService("orm");
        this.state = proxy({ loaded: false, templates: null, error: "", busy: false });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.templates = await this.orm.call(MODEL, "get_email_templates", [this.props.reportId]);
        this.state.loaded = true;
    }

    otherReports(template) {
        return template.reports.filter((report) => report.id !== this.props.reportId);
    }

    async setUse(template, use) {
        this.state.busy = true;
        this.state.error = "";
        try {
            await this.orm.call(MODEL, "set_email_report", [this.props.reportId, template.id, use]);
            await this.load();
        } catch (error) {
            this.state.error = errorText(error);
        } finally {
            this.state.busy = false;
        }
    }
}
