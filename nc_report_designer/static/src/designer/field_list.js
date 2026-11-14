import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";

const TYPE_LABELS = {
    char: "text",
    text: "text",
    html: "html",
    integer: "number",
    float: "number",
    monetary: "amount",
    date: "date",
    datetime: "date",
    boolean: "yes/no",
    selection: "choice",
    many2one: "link",
    one2many: "lines",
    many2many: "tags",
    binary: "file",
};

/**
 * Fields of a model, draggable onto the report. Many2one fields expand to the
 * fields of the linked record (o.partner_id.city, ...).
 */
export class FieldList extends Component {
    static template = "nc_report_designer.FieldList";
    static props = {
        model: String,
        var: String,
        prefix: { type: String, optional: true },
        // fields of the lines of the table around the selection: dropped as new columns
        column: { type: Boolean, optional: true },
        depth: Number,
        search: { type: String, optional: true },
        loadFields: Function,
        onDragStart: Function,
        onDragEnd: Function,
        onInsert: Function,
    };

    setup() {
        this.state = useState({ fields: [], expanded: {} });
        onWillStart(async () => {
            this.state.fields = await this.props.loadFields(this.props.model);
        });
        onWillUpdateProps(async (next) => {
            if (next.model !== this.props.model) {
                this.state.fields = await next.loadFields(next.model);
                this.state.expanded = {};
            }
        });
    }

    get visibleFields() {
        const search = this.props.depth === 0 ? (this.props.search || "").trim().toLowerCase() : "";
        if (!search) {
            return this.state.fields;
        }
        return this.state.fields.filter(
            (f) => f.string.toLowerCase().includes(search) || f.name.includes(search)
        );
    }

    path(field) {
        return this.props.prefix ? `${this.props.prefix}.${field.name}` : field.name;
    }

    payload(field) {
        return { kind: "field", var: this.props.var, path: this.path(field), type: field.type, column: Boolean(this.props.column) };
    }

    canExpand(field) {
        return field.type === "many2one" && field.relation && this.props.depth < 3;
    }

    toggle(field) {
        this.state.expanded[field.name] = !this.state.expanded[field.name];
    }

    typeLabel(field) {
        if (field.image) {
            return "image";
        }
        return TYPE_LABELS[field.type] || field.type;
    }
}
FieldList.components = { FieldList };
