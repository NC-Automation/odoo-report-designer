import { Component, onMounted, useRef, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

/** Asks for a report name (Rename / Duplicate in the designer). */
export class ReportNameDialog extends Component {
    static template = "nc_report_designer.ReportNameDialog";
    static components = { Dialog };
    static props = {
        title: String,
        name: String,
        confirmLabel: String,
        help: { type: String, optional: true },
        printMenu: { type: Boolean, optional: true }, // shows the "Print menu" checkbox
        onConfirm: Function, // (name, { printMenu }) => Promise
        close: Function,
    };

    setup() {
        this.inputRef = useRef("name");
        this.state = useState({ name: this.props.name, printMenu: true, error: "", saving: false });
        onMounted(() => {
            this.inputRef.el?.focus();
            this.inputRef.el?.select();
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
