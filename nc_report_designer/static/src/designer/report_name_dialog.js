import { Component, onMounted, proxy, signal, t, useProps } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

/** Asks for a report name (Rename / Duplicate in the designer). */
export class ReportNameDialog extends Component {
    static template = "nc_report_designer.ReportNameDialog";
    static components = { Dialog };
    props = useProps({
        title: t.string(),
        name: t.string(),
        confirmLabel: t.string(),
        help: t.string().optional(),
        printMenu: t.boolean().optional(), // shows the "Print menu" checkbox
        onConfirm: t.function(), // (name, { printMenu }) => Promise
        close: t.function(),
    });

    setup() {
        this.inputRef = signal.ref();
        this.state = proxy({ name: this.props.name, printMenu: true, error: "", saving: false });
        onMounted(() => {
            this.inputRef()?.focus();
            this.inputRef()?.select();
        });
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.confirm();
        }
    }

    async confirm() {
        if (!this.state.name.trim()) {
            this.state.error = "Give the report a name.";
            return;
        }
        this.state.saving = true;
        try {
            await this.props.onConfirm(this.state.name.trim(), { printMenu: this.state.printMenu });
        } catch (error) {
            this.state.error = error.data?.message || error.message || String(error);
            return;
        } finally {
            this.state.saving = false;
        }
        this.props.close();
    }
}
