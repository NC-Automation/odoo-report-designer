import { Component, onWillStart, proxy, t, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { RecordSelector } from "@web/core/record_selectors/record_selector";
import { MultiRecordSelector } from "@web/core/record_selectors/multi_record_selector";

const MODEL = "nc.report.designer";

/**
 * The companies a report applies to (none: all of them) and, for the others, the
 * report used instead: one line per group of companies, and one for any other company.
 * The report then works as a master: printing it, or attaching it to an email, uses
 * the right report for the company of each record.
 */
export class CompanyRouting extends Component {
    static template = "nc_report_designer.CompanyRouting";
    static components = { Dialog, RecordSelector, MultiRecordSelector };
    props = useProps({
        reportId: t.number(),
        reportName: t.string(),
        model: t.string(),
        onSaved: t.function(),
        close: t.function(),
    });

    setup() {
        this.orm = useService("orm");
        this.nextKey = 1;
        this.state = proxy({
            loaded: false,
            companyIds: [],
            rules: [],
            otherReportId: false,
            multiCompany: true,
            error: "",
            saving: false,
        });
        onWillStart(async () => {
            const routing = await this.orm.call(MODEL, "get_company_routing", [this.props.reportId]);
            this.state.companyIds = routing.company_ids;
            this.state.rules = routing.rules.map((rule) => ({
                key: this.nextKey++,
                companyIds: rule.company_ids,
                reportId: rule.report_id,
            }));
            this.state.otherReportId = routing.other_report_id;
            this.state.multiCompany = routing.multi_company;
            this.state.loaded = true;
        });
    }

    get reportDomain() {
        return [
            ["model", "=", this.props.model],
            ["report_type", "in", ["qweb-pdf", "qweb-html"]],
            ["id", "!=", this.props.reportId],
            ["ncd_company_ids", "=", false],
        ];
    }

    /** Companies already given a report elsewhere in the dialog. */
    takenBy(skipKey) {
        const ids = [...this.state.companyIds];
        for (const rule of this.state.rules) {
            if (rule.key !== skipKey) {
                ids.push(...rule.companyIds);
            }
        }
        return ids;
    }

    companyDomain(skipKey) {
        const taken = this.takenBy(skipKey);
        return taken.length ? [["id", "not in", taken]] : [];
    }

    setCompanies(ids) {
        this.state.companyIds = ids;
        this.state.error = "";
    }

    addRule() {
        this.state.rules.push({ key: this.nextKey++, companyIds: [], reportId: false });
    }

    removeRule(rule) {
        this.state.rules = this.state.rules.filter((r) => r.key !== rule.key);
    }

    validate() {
        if (!this.state.companyIds.length) {
            return "";
        }
        for (const rule of this.state.rules) {
            if (rule.companyIds.length && !rule.reportId) {
                return _t("Choose the report used for the companies of each line.");
            }
            if (!rule.companyIds.length && rule.reportId) {
                return _t("Choose the companies of each line, or remove the line.");
            }
        }
        if (!this.state.otherReportId) {
            return _t("Choose the report used for the other companies.");
        }
        return "";
    }

    async save() {
        this.state.error = this.validate();
        if (this.state.error) {
            return;
        }
        this.state.saving = true;
        try {
            await this.orm.call(MODEL, "save_company_routing", [
                this.props.reportId,
                {
                    company_ids: this.state.companyIds,
                    rules: this.state.rules.map((rule) => ({
                        company_ids: rule.companyIds,
                        report_id: rule.reportId,
                    })),
                    other_report_id: this.state.otherReportId,
                },
            ]);
        } catch (error) {
            this.state.error = error.data?.message || error.message || String(error);
            return;
        } finally {
            this.state.saving = false;
        }
        await this.props.onSaved();
        this.props.close();
    }
}
