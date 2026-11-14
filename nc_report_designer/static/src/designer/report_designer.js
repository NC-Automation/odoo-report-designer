import {
    Component,
    onWillStart,
    onWillUnmount,
    proxy,
    signal,
    useListener,
    useProps,
} from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { RecordSelector } from "@web/core/record_selectors/record_selector";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { FieldList } from "./field_list";
import { ExpressionEditor } from "./expression_editor";
import { CompanyRouting } from "./company_routing";
import { ReportNameDialog } from "./report_name_dialog";
import { ReportEditDialog, ReportEmailsDialog } from "./report_settings";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";

const MODEL = "nc.report.designer";
const SHOW_HIDDEN_KEY = "nc_report_designer.show_hidden";

function readShowHidden() {
    try {
        return Boolean(browser.localStorage.getItem(SHOW_HIDDEN_KEY));
    } catch {
        return false;
    }
}
const MM_TO_PX = 96 / 25.4;
const VOID_TAGS = new Set(["area", "br", "col", "embed", "hr", "img", "input", "wbr"]);
const TABLE_PARTS = new Set(["tr", "tbody", "thead", "tfoot"]);
const INLINE_CONTAINERS = new Set([
    "td", "th", "p", "span", "strong", "b", "em", "i", "u", "small", "a", "li", "label",
    "h1", "h2", "h3", "h4", "h5", "h6",
]);
const FONT_SIZES = ["8px", "9px", "10px", "11px", "12px", "13px", "14px", "16px", "18px", "20px", "24px", "28px", "32px"];

// Styles injected in the preview document.
const FRAME_CSS = `
html, body { margin: 0 !important; }
body { padding: 6mm var(--ncd-margin-right, 7mm) 6mm var(--ncd-margin-left, 7mm) !important; cursor: default; }
.ncd-hover { outline: 1px dashed #017e84 !important; outline-offset: 1px; cursor: pointer; }
.ncd-selected { outline: 2px solid #714b67 !important; outline-offset: 1px; }
.ncd-selected[draggable="true"] { cursor: grab; }
.ncd-editing { outline: 2px solid #e99d00 !important; outline-offset: 2px; cursor: text !important; }
.ncd-indicator { position: absolute; pointer-events: none; z-index: 100000; background: #00a09d; border-radius: 2px; display: none; }
.ncd-indicator.ncd-inside { background: rgba(0, 160, 157, 0.12); border: 2px dashed #00a09d; }
.ncd-indicator.ncd-newcol { background: #00a09d; border-radius: 3px; box-shadow: 0 0 0 2px rgba(0, 160, 157, 0.25); }
.ncd-indicator.ncd-newcol::after { content: "+ new column"; position: absolute; left: 50%; top: -3px; transform: translate(-50%, -100%); font: 11px sans-serif; color: #fff; background: #00a09d; padding: 1px 6px; border-radius: 3px; white-space: nowrap; }
div[data-ncd]:empty, p[data-ncd]:empty, td[data-ncd]:empty, th[data-ncd]:empty, li[data-ncd]:empty { min-height: 1.2em; min-width: 1em; }
div[data-ncd]:empty:not([style*="page-break"]), p[data-ncd]:empty { outline: 1px dotted #c8c8c8; }
[data-ncd][style*="page-break-before"] { border-top: 2px dashed #b4b4b4; }
[data-ncd][style*="page-break-before"]::before { content: "page break"; font: 10px sans-serif; color: #999; position: relative; top: -0.9em; background: #fff; padding: 0 4px; }
.ncd-col-handle { position: absolute; z-index: 100001; width: 13px; margin-left: -6px; cursor: col-resize; }
.ncd-col-handle::before { content: ""; position: absolute; left: 6px; top: 0; bottom: 0; border-left: 1px dashed #00a09d; }
.ncd-col-handle::after { content: ""; position: absolute; left: 3px; top: 50%; height: 26px; margin-top: -13px; width: 7px; border-radius: 4px; background: #00a09d; box-shadow: 0 0 0 1.5px #fff, 0 1px 3px rgba(0,0,0,.35); }
.ncd-col-handle:hover::after, .ncd-col-handle.ncd-active::after { background: #017e84; width: 9px; left: 2px; }
.ncd-col-handle:hover::before, .ncd-col-handle.ncd-active::before { border-left-style: solid; }
.ncd-col-label { position: absolute; z-index: 100002; transform: translate(-50%, -100%); margin-top: -3px; font: 11px sans-serif; background: #00a09d; color: #fff; padding: 1px 6px; border-radius: 3px; white-space: nowrap; pointer-events: none; }
body.ncd-resizing, body.ncd-resizing * { cursor: col-resize !important; user-select: none !important; }
[data-ncd-ghost] { position: relative; opacity: 0.85; min-height: 1.4em; min-width: 2em; }
[data-ncd-ghost="client"][data-ncd-ghost="client"] { display: revert !important; }
[data-ncd-ghost]::after { content: ""; position: absolute; inset: 0; pointer-events: none; z-index: 5;
    background: repeating-linear-gradient(-45deg, rgba(113, 75, 103, 0.28) 0 2px, rgba(113, 75, 103, 0) 2px 7px);
    outline: 1px dashed rgba(113, 75, 103, 0.8); }
`;
const COL_CLASS_RE = /^col(-(sm|md|lg|xl|xxl))?(-(\d{1,2}|auto))?$/;

/**
 * Content box of an element ({left, width}, viewport coordinates): what percentage
 * widths of its children are relative to (a .row may have padding).
 */
function contentBox(el) {
    const rect = el.getBoundingClientRect();
    const style = el.ownerDocument.defaultView.getComputedStyle(el);
    const startPad = parseFloat(style.paddingLeft) + parseFloat(style.borderLeftWidth);
    const endPad = parseFloat(style.paddingRight) + parseFloat(style.borderRightWidth);
    return { left: rect.left + startPad, width: rect.width - startPad - endPad, top: rect.top, height: rect.height };
}

/** Percentage of a column declared in its style ("37.5%" -> 37.5), else null. */
function stylePercent(el) {
    const match = /^([\d.]+)%$/.exec(el.style.width || "");
    return match ? Number(match[1]) : null;
}

function isGridColumn(el) {
    return [...el.classList].some((cls) => COL_CLASS_RE.test(cls));
}

/** Width of a grid column in twelfths from its classes, null when not fixed. */
function gridColumnWidth(el) {
    for (const cls of el.classList) {
        const match = COL_CLASS_RE.test(cls) && cls.match(/-(\d{1,2})$/);
        if (match) {
            return Number(match[1]);
        }
    }
    return null;
}

function parseStyle(style) {
    const result = {};
    for (const part of (style || "").split(";")) {
        const index = part.indexOf(":");
        if (index > 0) {
            const prop = part.slice(0, index).trim().toLowerCase();
            const value = part.slice(index + 1).trim();
            if (prop && value) {
                result[prop] = value;
            }
        }
    }
    return result;
}

function formatStyle(style) {
    return Object.entries(style)
        .map(([prop, value]) => `${prop}: ${value};`)
        .join(" ");
}

const SIDES = ["top", "right", "bottom", "left"];
const BORDER_STYLES = ["solid", "dashed", "dotted", "double"];

// "8" -> "8px"; other units ("50%", "5mm", "auto") are kept as typed.
function toCssLength(raw) {
    const value = (raw || "").trim().replace(",", ".");
    if (!value) {
        return "";
    }
    return /^-?\d+(\.\d+)?$/.test(value) ? `${value}px` : value;
}

// "8px" -> "8" (the boxes show pixels without the unit)
function displayLength(value) {
    return value && /^-?\d+(\.\d+)?px$/.test(value) ? value.slice(0, -2) : value || "";
}

// {top, right, bottom, left} of margin / padding, from the shorthand and the longhands
function readSides(style, prop) {
    const sides = { top: "", right: "", bottom: "", left: "" };
    if (style[prop]) {
        const [top, right = top, bottom = top, left = right] = style[prop].split(/\s+/);
        Object.assign(sides, { top, right, bottom, left });
    }
    for (const side of SIDES) {
        if (style[`${prop}-${side}`]) {
            sides[side] = style[`${prop}-${side}`];
        }
    }
    return sides;
}

function writeSides(style, prop, sides) {
    delete style[prop];
    for (const side of SIDES) {
        delete style[`${prop}-${side}`];
    }
    const values = SIDES.map((side) => sides[side]);
    if (values.every((value) => value && value === values[0])) {
        style[prop] = values[0];
    } else {
        for (const side of SIDES) {
            if (sides[side]) {
                style[`${prop}-${side}`] = sides[side];
            }
        }
    }
}

function parseBorderValue(value) {
    const border = { width: "", style: "", color: "" };
    for (const token of (value || "").split(/\s+(?![^(]*\))/)) {
        if (!token) {
            continue;
        } else if (/^(none|hidden|solid|dashed|dotted|double|groove|ridge|inset|outset)$/.test(token)) {
            border.style = token;
        } else if (/^(\d|\.|thin|medium|thick)/.test(token)) {
            border.width = token;
        } else {
            border.color = token;
        }
    }
    return border;
}

// {sides: {top: bool, ...}, width, style, color} of the border declarations
function readBorder(style) {
    const result = { sides: { top: false, right: false, bottom: false, left: false }, width: "", style: "", color: "" };
    const found = [];
    if (style.border && !/^(none|0)/.test(style.border)) {
        for (const side of SIDES) {
            result.sides[side] = true;
        }
        found.push(style.border);
    }
    for (const side of SIDES) {
        const value = style[`border-${side}`];
        if (value !== undefined) {
            result.sides[side] = !/^(none|0)/.test(value);
            if (result.sides[side]) {
                found.push(value);
            }
        }
    }
    Object.assign(result, parseBorderValue(found[0]));
    return result;
}

function writeBorder(style, border) {
    delete style.border;
    for (const side of SIDES) {
        delete style[`border-${side}`];
    }
    const active = SIDES.filter((side) => border.sides[side]);
    if (!active.length || border.style === "none") {
        return;
    }
    const value = `${border.width || "1px"} ${border.style || "solid"} ${border.color || "#000000"}`;
    if (active.length === 4) {
        style.border = value;
    } else {
        for (const side of active) {
            style[`border-${side}`] = value;
        }
    }
}

function toHexColor(value) {
    if (!value) {
        return "#000000";
    }
    if (/^#[0-9a-f]{6}$/i.test(value)) {
        return value;
    }
    if (/^#[0-9a-f]{3}$/i.test(value)) {
        return "#" + [...value.slice(1)].map((c) => c + c).join("");
    }
    const rgb = value.match(/^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i);
    if (rgb) {
        return "#" + rgb.slice(1, 4).map((n) => Number(n).toString(16).padStart(2, "0")).join("");
    }
    return "#000000";
}

export class ReportDesigner extends Component {
    static template = "nc_report_designer.ReportDesigner";
    static components = { RecordSelector, FieldList, Dropdown, DropdownItem };
    props = useProps();

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.canvasRef = signal.ref();
        this.iframeRef = signal.ref();
        this.xmlRef = signal.ref();
        this.changeXmlRef = signal.ref();
        this.newAttrNameRef = signal.ref();
        this.newAttrValueRef = signal.ref();

        this.fontSizes = FONT_SIZES;
        this.sides = SIDES;
        this.borderStyles = BORDER_STYLES;
        this.displayLength = displayLength;
        this.fieldsCache = {};
        this.undoStack = [];
        this.redoStack = [];
        this.drag = null; // payload of the current drag: field, block or move
        this.dropInfo = null;
        this.editing = null; // inline text edition in the preview
        this.viewIds = [];
        this.selectToken = 0;
        this.savedScroll = 0;

        this.state = proxy({
            reportId: false,
            resId: false,
            setup: null,
            previewUrl: false,
            frameHeight: 1123,
            zoom: 1,
            selected: false,
            node: null,
            nodeVersion: 0,
            ancestors: [],
            scope: [],
            openVars: {},
            fieldSearch: "",
            leftTab: "fields",
            rightTab: "properties",
            changes: [],
            editingChange: false,
            busy: false,
            history: { undo: 0, redo: 0 },
            previewError: false,
            showCondition: false,
            showAdvanced: false,
            hidden: [],
            showTemplateHidden: false,
            unforced: 0,
            showHidden: readShowHidden(), // the "Show hidden" switch
            timeline: [], // the dated list of the changes (Changes tab)
            timelineMore: 0,
            // the preview shows the report just after this change (0: before them all);
            // null: as it is now
            viewingAt: null,
            routing: null, // companies the report applies to (see openRouting)
        });

        this.loadFields = this.loadFields.bind(this);
        this.onPaletteDragStart = this.onPaletteDragStart.bind(this);
        this.onDragEnd = this.onDragEnd.bind(this);
        this.insertFromPalette = this.insertFromPalette.bind(this);

        useListener(window, "keydown", (ev) => this.onKeydown(ev));

        onWillStart(async () => {
            // report_id from the "Report Designer" button of a report, resId from the URL
            const reportId = this.props.action?.params?.report_id || this.props.resId;
            if (reportId) {
                await this.loadReport(reportId);
            }
        });
        onWillUnmount(() => this.frameObserver?.disconnect());
    }

    // ------------------------------------------------------------------
    // Getters
    // ------------------------------------------------------------------

    get reportDomain() {
        return [["report_type", "in", ["qweb-pdf", "qweb-html"]]];
    }

    get pageWidthPx() {
        return Math.round((this.state.setup?.paper.width || 210) * MM_TO_PX);
    }

    get pageHeightPx() {
        return Math.round((this.state.setup?.paper.height || 297) * MM_TO_PX);
    }

    get frameDoc() {
        return this.iframeRef()?.contentDocument || null;
    }

    get currentStyle() {
        return parseStyle(this.state.node?.attrs.style);
    }

    /** Whether the selection is visible on the real page (not hidden, not drawn hatched). */
    get selectedOnPage() {
        const el = this.elementsOf(this.state.selected)[0];
        return Boolean(el) && !el.closest("[data-ncd-ghost]") && el.ownerDocument.defaultView.getComputedStyle(el).display !== "none";
    }

    get selectedDrawn() {
        return this.elementsOf(this.state.selected).length > 0;
    }

    get boxSides() {
        const style = this.currentStyle;
        return { margin: readSides(style, "margin"), padding: readSides(style, "padding") };
    }

    get borderInfo() {
        const border = readBorder(this.currentStyle);
        const on = SIDES.filter((side) => border.sides[side]);
        border.all = on.length === 4;
        border.any = on.length > 0;
        return border;
    }

    /**
     * Effective values on the page (margins from classes, default paddings, ...), shown
     * greyed in the empty boxes so the user sees what is applied today.
     */
    get computedBox() {
        const el = this.state.selected && this.elementsOf(this.state.selected)[0];
        const result = { margin: {}, padding: {} };
        if (!el) {
            return result;
        }
        const computed = el.ownerDocument.defaultView.getComputedStyle(el);
        for (const prop of ["margin", "padding"]) {
            for (const side of SIDES) {
                result[prop][side] = String(Math.round(parseFloat(computed[`${prop}-${side}`] || computed.getPropertyValue(`${prop}-${side}`)) || 0));
            }
        }
        return result;
    }

    get conditionExpr() {
        const attrs = this.state.node?.attrs || {};
        return attrs["t-field"] || attrs["t-out"] || attrs["t-esc"] || "";
    }

    get visibilityMode() {
        const condition = this.state.node?.attrs["t-if"];
        if (!condition && !this.state.showCondition) {
            return "always";
        }
        if (condition && condition === this.conditionExpr) {
            return "empty";
        }
        return "custom";
    }

    get fieldDirective() {
        const attrs = this.state.node?.attrs || {};
        return ["t-field", "t-out", "t-esc", "t-raw"].find((name) => attrs[name] !== undefined) || false;
    }


    get attributeList() {
        return Object.entries(this.state.node?.attrs || {}).map(([name, value]) => ({ name, value }));
    }

    toHexColor(value) {
        return toHexColor(value);
    }

    // ------------------------------------------------------------------
    // Report / record
    // ------------------------------------------------------------------

    async loadReport(reportId, resId = false) {
        const setup = await this.orm.call(MODEL, "get_setup", [reportId, resId]);
        if (setup.report.id !== this.state.reportId) {
            this.undoStack = [];
            this.redoStack = [];
            this.syncHistory();
        }
        this.state.setup = setup;
        this.state.reportId = setup.report.id;
        this.props.updateActionState?.({ resId: setup.report.id }); // keeps the report in the URL
        this.state.resId = setup.res_id;
        this.state.scope = setup.scope;
        this.state.openVars = this.defaultOpenVars(setup.scope);
        this.clearSelection();
        this.state.viewingAt = null;
        this.loadRouting();
        if (setup.res_id) {
            this.reloadPreview();
        } else {
            this.state.previewUrl = false;
        }
    }

    onReportSelected(reportId) {
        if (reportId) {
            this.loadReport(reportId);
        }
    }

    onRecordSelected(resId) {
        if (resId) {
            this.state.resId = resId;
            this.loadRouting();
            this.reloadPreview();
        }
    }

    // ------------------------------------------------------------------
    // Rename / duplicate the report
    // ------------------------------------------------------------------

    /** The report selector shows the new name (the name service caches names). */
    showReportName(reportId, name) {
        this.env.services.name.addDisplayNames("ir.actions.report", { [reportId]: name });
    }

    editReport() {
        const { report } = this.state.setup;
        this.dialog.add(ReportEditDialog, {
            reportId: report.id,
            onSaved: (name) => {
                report.name = name;
                this.showReportName(report.id, name);
            },
        });
    }

    emailReport() {
        const { report } = this.state.setup;
        this.dialog.add(ReportEmailsDialog, { reportId: report.id, reportName: report.name });
    }

    deleteReport() {
        const { report } = this.state.setup;
        this.dialog.add(ConfirmationDialog, {
            title: _t("Delete the report"),
            body: _t(
                "Delete “%(name)s” and its templates? Emails that attach it go back to “%(source)s”. This cannot be undone.",
                { name: report.name, source: report.copied_from }
            ),
            confirmLabel: _t("Delete"),
            confirmClass: "btn-danger",
            confirm: async () => {
                const sourceId = await this.orm.call(MODEL, "delete_report", [report.id]);
                this.notification.add(_t("“%s” was deleted.", report.name), { type: "success" });
                this.undoStack = [];
                this.redoStack = [];
                this.syncHistory();
                if (sourceId) {
                    await this.loadReport(sourceId, this.state.resId);
                } else {
                    this.state.setup = null;
                    this.state.reportId = false;
                }
            },
            cancel: () => {},
        });
    }
    duplicateReport() {
        const { report } = this.state.setup;
        this.dialog.add(ReportNameDialog, {
            title: _t("Duplicate the report"),
            name: _t("%s (copy)", report.name),
            confirmLabel: _t("Duplicate"),
            printMenu: true,
            help: _t(
                "The copy gets its own copies of this report's templates, as they print now (your changes included): changing one does not change the other. The shared layout (header, footer) stays shared."
            ),
            onConfirm: async (name, { printMenu }) => {
                const copyId = await this.orm.call(MODEL, "duplicate_report", [report.id, name, printMenu]);
                this.showReportName(copyId, name);
                await this.loadReport(copyId, this.state.resId);
                this.notification.add(_t("You are now designing the copy “%s”.", name), { type: "success" });
            },
        });
    }

    // ------------------------------------------------------------------
    // Report per company
    // ------------------------------------------------------------------

    async loadRouting() {
        this.state.routing = await this.orm.call(MODEL, "get_company_routing", [this.state.reportId, this.state.resId || false]);
    }

    get routingSummary() {
        const routing = this.state.routing;
        if (!routing?.company_ids.length) {
            return _t("All companies");
        }
        return routing.company_names.join(", ");
    }

    openRouting() {
        const { report } = this.state.setup;
        this.dialog.add(CompanyRouting, {
            reportId: this.state.reportId,
            reportName: report.name,
            model: report.model,
            onSaved: () => this.loadRouting(),
        });
    }

    async openRoutedReport() {
        const target = this.state.routing?.record?.report;
        if (target) {
            await this.loadReport(target.id, this.state.resId);
        }
    }

    openPdf() {
        const { report } = this.state.setup;
        window.open(`/report/pdf/${report.report_name}/${this.state.resId}`, "_blank");
    }

    zoom(delta) {
        this.state.zoom = Math.min(2, Math.max(0.4, Math.round((this.state.zoom + delta) * 10) / 10));
    }

    defaultOpenVars(scope) {
        const open = {};
        for (const variable of scope) {
            open[variable.var] = !["company", "user"].includes(variable.var);
        }
        return open;
    }

    // ------------------------------------------------------------------
    // Preview frame
    // ------------------------------------------------------------------

    reloadPreview() {
        this.savedScroll = this.canvasRef()?.scrollTop || 0;
        // hidden elements drawn anyway (hatched): the selected one (showSelectedHidden) and,
        // while the Hidden tab is open, all of them (showAllHidden)
        const targets = [...new Set([this.forced, ...(this.hiddenForce || [])].filter(Boolean))];
        let query = targets.length ? `&force=${encodeURIComponent(targets.join(","))}` : "";
        if (this.state.viewingAt !== null) {
            // an earlier point of the list of changes, for the templates of the preview
            query = `&at=${this.state.viewingAt}&views=${this.viewIds.join(",")}`;
        }
        this.state.previewUrl = `/nc_report_designer/preview/${this.state.reportId}/${this.state.resId}?_=${Date.now()}${query}`;
    }

    /**
     * Make the selected element visible in the preview when it is hidden, hatched so it
     * is obvious that it is not on the real page: hidden by classes / style, it is shown
     * in place; not drawn (false condition), the preview is drawn again with that element
     * forced visible. Showing another element drops it.
     */
    showSelectedHidden(target) {
        const doc = this.frameDoc;
        if (!doc || this.state.previewError) {
            // the report itself cannot be drawn: forcing an element would only fail again
            return;
        }
        if (!this.state.showHidden) {
            // only the selected one is drawn (with "Show hidden", all of them stay)
            for (const el of doc.querySelectorAll('[data-ncd-ghost="client"]')) {
                if (el.dataset.ncd !== target) {
                    el.removeAttribute("data-ncd-ghost");
                }
            }
        }
        const elements = this.elementsOf(target);
        if (elements.length) {
            const win = doc.defaultView;
            for (const el of elements) {
                if (!el.hasAttribute("data-ncd-ghost") && win.getComputedStyle(el).display === "none") {
                    el.setAttribute("data-ncd-ghost", "client");
                }
            }
            if (this.forced && this.forced !== target && !elements[0].closest("[data-ncd-ghost='1']")) {
                this.forced = null; // the forced one is no longer selected: draw the page as it is
                this.reloadPreview();
            }
        } else if (this.forced !== target) {
            this.forced = target;
            this.reloadPreview();
        }
    }

    onIframeLoad() {
        const doc = this.frameDoc;
        if (!doc || !doc.body || !this.state.previewUrl) {
            return;
        }
        this.state.previewError = Boolean(doc.body.dataset.ncdError);
        // hidden elements asked to be drawn that could not be, for this record
        this.state.unforced = Number(doc.body.dataset.ncdUnforced || 0);
        const style = doc.createElement("style");
        style.textContent = FRAME_CSS;
        doc.head.appendChild(style);
        const paper = this.state.setup.paper;
        doc.documentElement.style.setProperty("--ncd-margin-left", `${paper.margin_left || 7}mm`);
        doc.documentElement.style.setProperty("--ncd-margin-right", `${paper.margin_right || 7}mm`);
        this.indicator = doc.createElement("div");
        this.indicator.className = "ncd-indicator";
        doc.body.appendChild(this.indicator);

        doc.addEventListener("mouseover", (ev) => this.onFrameMouseOver(ev));
        doc.addEventListener("mouseleave", () => this.setHover(null));
        doc.addEventListener("click", (ev) => this.onFrameClick(ev), true);
        doc.addEventListener("dblclick", (ev) => this.onFrameDblClick(ev), true);
        doc.addEventListener("dragstart", (ev) => this.onFrameDragStart(ev));
        doc.addEventListener("dragover", (ev) => this.onFrameDragOver(ev));
        doc.addEventListener("dragleave", (ev) => this.onFrameDragLeave(ev));
        doc.addEventListener("drop", (ev) => this.onFrameDrop(ev));
        doc.addEventListener("dragend", () => this.onDragEnd());
        doc.addEventListener("keydown", (ev) => this.onKeydown(ev));
        doc.addEventListener("submit", (ev) => ev.preventDefault(), true);

        if (this.state.viewingAt !== null) {
            // an earlier state, only to look at: its elements are not those of the templates now
            this.updateFrameHeight();
            if (this.canvasRef()) {
                this.canvasRef().scrollTop = this.savedScroll;
            }
            return;
        }
        this.viewIds = [...new Set([...doc.querySelectorAll("[data-ncd]")].map((el) => el.dataset.ncd.split(":")[0]))].map(Number);
        this.updateFrameHeight();
        this.frameObserver?.disconnect();
        const win = this.iframeRef().contentWindow;
        this.frameObserver = new win.ResizeObserver(() => this.updateFrameHeight());
        this.frameObserver.observe(doc.body);
        if (this.canvasRef()) {
            this.canvasRef().scrollTop = this.savedScroll;
        }
        if (this.state.previewError && (this.forced || this.hiddenForce?.length)) {
            // the hidden element(s) cannot be drawn for this record: draw the page as it is
            const all = Boolean(this.hiddenForce?.length);
            this.forced = null;
            this.hiddenForce = [];
            this.hiddenForceFailed = all;
            this.notification.add(
                all
                    ? _t("The hidden elements cannot all be drawn with this record: select one to see it.")
                    : _t("This hidden element cannot be shown with this record."),
                { type: "info" }
            );
            this.reloadPreview();
            return;
        }
        if (this.state.selected) {
            // also when it is not on the page: a hidden element stays selected (and is
            // drawn hatched), a removed one clears the selection (get_node fails)
            this.select(this.state.selected);
        }
        if (this.state.rightTab === "changes") {
            this.loadChanges();
        }
        this.loadHidden(); // also keeps the count of the Hidden tab up to date
    }

    updateFrameHeight() {
        const doc = this.frameDoc;
        if (!doc?.body) {
            return;
        }
        // Measure at a fixed baseline height: some layouts size their body / page on the
        // viewport (100vh, min-height: 100%), so measuring at the current frame height made
        // every resize grow the content, and the frame, forever (an endless render loop).
        const iframe = this.iframeRef();
        const current = iframe.style.height;
        iframe.style.height = `${this.pageHeightPx}px`;
        const content = Math.ceil(doc.body.scrollHeight);
        iframe.style.height = current;
        const height = Math.max(this.pageHeightPx, content + 24);
        if (Math.abs(height - this.state.frameHeight) > 2) {
            this.state.frameHeight = height;
        }
    }

    elementsOf(target) {
        const doc = this.frameDoc;
        return doc && target ? [...doc.querySelectorAll(`[data-ncd="${target}"]`)] : [];
    }

    closestNcd(node) {
        let el = node && node.nodeType === 1 ? node : node?.parentElement;
        el = el?.closest?.("[data-ncd]");
        return el || null;
    }

    setHover(el) {
        if (this.hovered === el) {
            return;
        }
        this.hovered?.classList.remove("ncd-hover");
        this.hovered = el;
        el?.classList.add("ncd-hover");
    }

    onFrameMouseOver(ev) {
        if (this.drag || this.editing || this.resizing) {
            return;
        }
        this.setHover(this.closestNcd(ev.target));
    }

    onFrameClick(ev) {
        if (this.editing && this.editing.el.contains(ev.target)) {
            return;
        }
        if (this.resizing || this.justResized || ev.target.closest?.(".ncd-col-handle")) {
            // the click ending a column resize must not change the selection
            ev.preventDefault();
            ev.stopPropagation();
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        if (this.editing) {
            this.finishInlineEdit(true);
        }
        const el = this.closestNcd(ev.target);
        if (el) {
            this.select(el.dataset.ncd);
        } else {
            this.clearSelection();
        }
    }

    async onFrameDblClick(ev) {
        if (this.editing) {
            return;
        }
        const el = this.closestNcd(ev.target);
        if (!el) {
            return;
        }
        ev.preventDefault();
        if (this.state.node?.target !== el.dataset.ncd) {
            // the clicks of the double-click may still be loading the node
            await this.select(el.dataset.ncd);
        }
        if (this.state.node?.target !== el.dataset.ncd) {
            return;
        }
        if (this.state.node.text_only) {
            this.startInlineEdit(el);
        } else if (this.state.node) {
            this.notification.add(
                _t("This element shows computed content. Change its field or expression in the panel on the right."),
                { type: "info" }
            );
        }
    }

    // ------------------------------------------------------------------
    // Selection
    // ------------------------------------------------------------------

    highlightSelection() {
        const doc = this.frameDoc;
        if (!doc) {
            return;
        }
        for (const el of doc.querySelectorAll(".ncd-selected")) {
            el.classList.remove("ncd-selected");
            el.removeAttribute("draggable");
        }
        for (const el of this.elementsOf(this.state.selected)) {
            el.classList.add("ncd-selected");
            el.setAttribute("draggable", "true");
        }
        this.updateResizeHandles();
    }

    // ------------------------------------------------------------------
    // Column resizing (grid columns and table columns)
    // ------------------------------------------------------------------

    /**
     * Columns that can be resized around the selection: the nearest table, or the row of
     * grid columns the selection is in (or is). Null when there is nothing to resize.
     */
    resizeContext() {
        const el = this.elementsOf(this.state.selected)[0];
        const doc = this.frameDoc;
        for (let current = el; current && current !== doc?.body; current = current.parentElement) {
            if (current.tagName === "TABLE") {
                const row = current.tHead?.rows[0] || current.rows[0];
                const cells = row ? [...row.cells] : [];
                if (cells.length < 2 || cells.some((cell) => !cell.dataset.ncd || cell.colSpan > 1)) {
                    return null;
                }
                return this.checkResizeTargets({ kind: "table", container: current, items: cells });
            }
            let row = null;
            if (current.classList.contains("row")) {
                row = current;
            } else if (isGridColumn(current) && current.parentElement?.classList.contains("row")) {
                row = current.parentElement;
            }
            if (row) {
                const columns = [...row.children].filter((child) => isGridColumn(child));
                if (columns.length < 2 || columns.some((col) => !col.dataset.ncd)) {
                    return null;
                }
                // free widths (col-auto + style width: 37%) or grid steps (col-4)
                const percent = columns.some((col) => /%$/.test(col.style.width));
                return this.checkResizeTargets({ kind: "grid", percent, container: row, items: columns });
            }
        }
        return null;
    }

    checkResizeTargets(context) {
        const targets = context.items.map((item) => item.dataset.ncd);
        const views = new Set(targets.map((target) => target.split(":")[0]));
        // columns repeated by a loop share one template element: they cannot be sized apart
        if (new Set(targets).size !== targets.length || views.size !== 1) {
            return null;
        }
        return context;
    }

    removeResizeHandles() {
        for (const el of this.frameDoc?.querySelectorAll(".ncd-col-handle, .ncd-col-label") || []) {
            el.remove();
        }
    }

    updateResizeHandles() {
        this.removeResizeHandles();
        if (this.resizing || !this.state.selected) {
            return;
        }
        const context = this.resizeContext();
        if (!context) {
            return;
        }
        const doc = this.frameDoc;
        const win = doc.defaultView;
        const box = context.container.getBoundingClientRect();
        const rects = context.items.map((item) => item.getBoundingClientRect());
        for (let index = 0; index < rects.length - 1; index++) {
            const handle = doc.createElement("div");
            handle.className = "ncd-col-handle";
            handle.title = _t("Drag to resize the columns");
            const x = (rects[index].right + rects[index + 1].left) / 2;
            Object.assign(handle.style, {
                left: `${x + win.scrollX}px`,
                top: `${box.top + win.scrollY}px`,
                height: `${box.height}px`,
            });
            handle.addEventListener("mousedown", (ev) => this.startColumnResize(ev, context, index, handle));
            doc.body.appendChild(handle);
        }
    }

    startColumnResize(ev, context, index, handle) {
        if (ev.button !== 0 || this.state.busy) {
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        const doc = this.frameDoc;
        const win = doc.defaultView;
        const box = context.kind === "grid" ? contentBox(context.container) : context.container.getBoundingClientRect();
        // grid steps (twelfths) for col-N columns, free percentages for tables and col-auto columns
        const grid = context.kind === "grid" && !context.percent;
        const total = grid ? 12 : 100;
        const minimum = grid ? 1 : 3;
        // declared widths first (col-4, width: 25%), measured ones otherwise
        const original = context.items.map((item) =>
            grid
                ? gridColumnWidth(item) || Math.max(1, Math.round((item.getBoundingClientRect().width / box.width) * 12))
                : (context.kind === "grid" && stylePercent(item)) || (item.getBoundingClientRect().width / box.width) * 100
        );
        const widths = [...original];
        const left = widths.slice(0, index).reduce((a, b) => a + b, 0);
        const right = left + widths[index] + widths[index + 1];
        const label = doc.createElement("div");
        label.className = "ncd-col-label";
        doc.body.appendChild(label);
        handle.classList.add("ncd-active");
        doc.body.classList.add("ncd-resizing");
        this.resizing = true;
        this.setHover(null);

        const format = (value) => (grid ? `${value}/12` : `${Math.round(value)}%`);
        const apply = () => {
            context.items.forEach((item, i) => {
                if (grid) {
                    item.style.flex = `0 0 ${(widths[i] / 12) * 100}%`;
                    item.style.maxWidth = `${(widths[i] / 12) * 100}%`;
                } else {
                    item.style.width = `${widths[i]}%`;
                }
            });
            const position = box.left + ((left + widths[index]) / total) * box.width;
            handle.style.left = `${position + win.scrollX}px`;
            label.style.left = `${position + win.scrollX}px`;
            label.style.top = `${box.top + win.scrollY}px`;
            label.textContent = `${format(widths[index])} | ${format(widths[index + 1])}`;
        };
        const onMove = (moveEv) => {
            let value = ((moveEv.clientX - box.left) / box.width) * total;
            value = grid ? Math.round(value) : Math.round(value);
            value = Math.min(Math.max(value, left + minimum), right - minimum);
            widths[index] = value - left;
            widths[index + 1] = right - value;
            apply();
        };
        const onUp = () => {
            doc.removeEventListener("mousemove", onMove, true);
            doc.removeEventListener("mouseup", onUp, true);
            window.removeEventListener("mouseup", onUp, true);
            label.remove();
            handle.classList.remove("ncd-active");
            doc.body.classList.remove("ncd-resizing");
            this.resizing = false;
            this.justResized = true;
            setTimeout(() => (this.justResized = false));
            this.saveColumnWidths(context, original, widths);
        };
        doc.addEventListener("mousemove", onMove, true);
        doc.addEventListener("mouseup", onUp, true);
        window.addEventListener("mouseup", onUp, true);
        apply();
    }

    /** Whole percents adding up to 100 when they nearly do, the rounding difference on the widest. */
    wholePercents(values) {
        const rounded = values.map((value) => Math.max(1, Math.round(value)));
        const sum = rounded.reduce((a, b) => a + b, 0);
        if (Math.abs(sum - 100) <= 3) {
            rounded[rounded.indexOf(Math.max(...rounded))] += 100 - sum;
        }
        return rounded;
    }

    /** Twelfths (at least 1 each) adding up to 12 for fractions adding up to 1 (largest remainders). */
    wholeTwelfths(fractions) {
        const exact = fractions.map((fraction) => fraction * 12);
        const widths = exact.map((value) => Math.max(1, Math.floor(value)));
        const order = exact.map((value, i) => [value - Math.floor(value), i]).sort((a, b) => b[0] - a[0]);
        for (let k = 0; widths.reduce((a, b) => a + b, 0) < 12 && k < order.length * 12; k++) {
            widths[order[k % order.length][1]]++;
        }
        while (widths.reduce((a, b) => a + b, 0) > 12 && Math.max(...widths) > 1) {
            widths[widths.indexOf(Math.max(...widths))]--;
        }
        return widths;
    }

    /** Switch the selected row of columns between grid steps (col-N) and free percentages. */
    setGridMode(mode) {
        const context = this.resizeContext();
        if (!context || context.kind !== "grid") {
            this.notification.add(_t("These columns cannot be switched (they may be repeated by a loop)."), {
                type: "warning",
            });
            return;
        }
        let current = "grid";
        if (context.percent) {
            current = "percent";
        } else if (context.items.every((item) => !gridColumnWidth(item))) {
            current = "auto";
        }
        if (mode === current) {
            return;
        }
        if (mode === "auto") {
            // plain `col`: the visible columns share the row equally, whatever their number
            const columns = context.items.map((item) => ({ target: item.dataset.ncd, width: null }));
            this.runOp({ type: "column_widths", target: this.state.selected, columns });
            return;
        }
        // Shares of the row from the declared widths (col-4 = 4/12, width: 25%), measured
        // against the row's content box when not declared; scaled so that the columns
        // fill the row: switching back and forth never shrinks it.
        const box = contentBox(context.container);
        const shares = context.items.map((item) => {
            const declared = context.percent ? stylePercent(item) : gridColumnWidth(item);
            if (declared) {
                return context.percent ? declared / 100 : declared / 12;
            }
            return item.getBoundingClientRect().width / box.width;
        });
        const sum = shares.reduce((a, b) => a + b, 0) || 1;
        const fractions = shares.map((share) => share / sum);
        let widths;
        if (mode === "percent") {
            widths = this.wholePercents(fractions.map((fraction) => fraction * 100)).map((value) => `${value}%`);
        } else {
            widths = this.wholeTwelfths(fractions);
        }
        const columns = context.items.map((item, i) => ({ target: item.dataset.ncd, width: widths[i] }));
        this.runOp({ type: "column_widths", target: this.state.selected, columns });
    }

    saveColumnWidths(context, original, widths) {
        if (context.kind === "grid" && context.percent) {
            if (widths.every((width, i) => Math.abs(width - original[i]) < 0.5)) {
                this.updateResizeHandles();
                return;
            }
            const percents = this.wholePercents(widths);
            const columns = context.items.map((item, i) => ({ target: item.dataset.ncd, width: `${percents[i]}%` }));
            this.runOp({ type: "column_widths", target: this.state.selected, columns });
            return;
        }
        if (context.kind === "grid") {
            const columns = context.items
                .map((item, i) => ({ target: item.dataset.ncd, width: widths[i], before: gridColumnWidth(item) }))
                .filter((column) => column.width !== column.before)
                .map(({ target, width }) => ({ target, width }));
            if (!columns.length) {
                this.updateResizeHandles();
                return;
            }
            this.runOp({ type: "column_widths", target: this.state.selected, columns });
            return;
        }
        if (widths.every((width, i) => Math.abs(width - original[i]) < 0.5)) {
            this.updateResizeHandles();
            return;
        }
        const rounded = this.wholePercents(widths);
        const cells = context.items.map((item, i) => ({ target: item.dataset.ncd, width: `${rounded[i]}%` }));
        this.runOp({ type: "column_widths", target: this.state.selected, cells });
    }

    gridColumn(action) {
        const el = this.elementsOf(this.state.selected)[0];
        const row = el && (el.classList.contains("row") ? el : el.parentElement);
        this.runOp({ type: "grid_column", target: this.state.selected, action, visible: this.visibleColumns(row) });
    }

    /**
     * Targets of the columns of a row drawn on the page: only those are evened out when
     * a column is added or deleted (the others are hidden by a condition for this record).
     */
    visibleColumns(row) {
        if (!row?.classList?.contains("row")) {
            return undefined;
        }
        return [...row.children]
            .filter((child) => isGridColumn(child) && child.dataset.ncd && !child.hasAttribute("data-ncd-ghost"))
            .map((child) => child.dataset.ncd);
    }

    clearSelection() {
        if (!this.state.showHidden) {
            for (const el of this.frameDoc?.querySelectorAll('[data-ncd-ghost="client"]') || []) {
                el.removeAttribute("data-ncd-ghost");
            }
        }
        if (this.forced) {
            this.forced = null;
            this.reloadPreview();
        }
        this.state.selected = false;
        this.state.node = null;
        this.state.ancestors = [];
        if (this.state.setup) {
            this.state.scope = this.state.setup.scope;
        }
        this.highlightSelection();
    }

    computeAncestors(el) {
        const ancestors = [];
        let current = el?.parentElement?.closest("[data-ncd]");
        while (current && ancestors.length < 8) {
            ancestors.unshift({ target: current.dataset.ncd, label: this.labelOf(current) });
            current = current.parentElement?.closest("[data-ncd]");
        }
        return ancestors;
    }

    labelOf(el) {
        const tag = el.tagName.toLowerCase();
        const name = el.getAttribute("name");
        if (name) {
            return `${tag}[${name}]`;
        }
        const cls = [...el.classList].find((c) => !c.startsWith("ncd-") && !c.startsWith("o_company"));
        return cls ? `${tag}.${cls}` : tag;
    }

    async select(target) {
        const token = ++this.selectToken;
        if (this.state.selected !== target) {
            this.state.showCondition = false;
        }
        this.state.selected = target;
        this.highlightSelection();
        const el = this.elementsOf(target)[0];
        this.state.ancestors = this.computeAncestors(el);
        let node;
        try {
            node = await this.orm.call(MODEL, "get_node", [this.state.reportId, target]);
        } catch {
            if (token === this.selectToken) {
                this.clearSelection();
            }
            return;
        }
        if (token !== this.selectToken) {
            return;
        }
        this.showSelectedHidden(target);
        this.state.node = node;
        this.state.nodeVersion++;
        this.state.scope = node.scope;
        for (const variable of node.scope) {
            if (!(variable.var in this.state.openVars)) {
                this.state.openVars[variable.var] = true;
            }
        }
        if (this.state.rightTab !== "properties") {
            this.state.rightTab = "properties";
        }
    }

    selectParent() {
        const parent = this.state.ancestors.at(-1);
        if (parent) {
            this.select(parent.target);
        }
    }

    // ------------------------------------------------------------------
    // Operations
    // ------------------------------------------------------------------

    syncHistory() {
        this.state.history = { undo: this.undoStack.length, redo: this.redoStack.length };
    }

    async runOp(op) {
        if (this.state.busy || this.state.viewingAt !== null) {
            return;
        }
        this.state.busy = true;
        try {
            const result = await this.orm.call(MODEL, "apply_operation", [this.state.reportId, op]);
            if (result.history.length) {
                this.undoStack.push(result.history);
                this.redoStack = [];
                this.syncHistory();
            }
            this.state.selected = result.select || false;
            if (!result.select) {
                this.state.node = null;
            }
            this.reloadPreview();
        } finally {
            this.state.busy = false;
        }
    }

    async undo() {
        if (this.state.busy || !this.undoStack.length || this.state.viewingAt !== null) {
            return;
        }
        this.state.busy = true;
        const entries = this.undoStack.pop();
        try {
            await this.orm.call(MODEL, "restore", [entries, "before", this.state.reportId]);
            this.redoStack.push(entries);
            this.clearSelection(); // element paths may have shifted
        } catch (error) {
            this.undoStack.push(entries);
            throw error;
        } finally {
            this.syncHistory();
            this.state.busy = false;
        }
        this.reloadPreview();
    }

    async redo() {
        if (this.state.busy || !this.redoStack.length || this.state.viewingAt !== null) {
            return;
        }
        this.state.busy = true;
        const entries = this.redoStack.pop();
        try {
            await this.orm.call(MODEL, "restore", [entries, "after", this.state.reportId]);
            this.undoStack.push(entries);
            this.clearSelection();
        } catch (error) {
            this.redoStack.push(entries);
            throw error;
        } finally {
            this.syncHistory();
            this.state.busy = false;
        }
        this.reloadPreview();
    }

    buildOp(payload, target, position, label, newColumn = false) {
        if (payload.kind === "field") {
            return { type: "insert_field", target, position, var: payload.var, path: payload.path, label, new_column: newColumn };
        }
        if (payload.kind === "block") {
            return { type: "insert_block", target, position, block: payload.block, new_column: newColumn };
        }
        if (payload.kind === "move") {
            return { type: "move", source: payload.source, target, position };
        }
        return null;
    }

    wantsLabel(el, position, inline) {
        const container = position === "inside" ? el : el.parentElement;
        return !(inline || (container && INLINE_CONTAINERS.has(container.tagName.toLowerCase())));
    }

    insertFromPalette(payload) {
        const el = this.elementsOf(this.state.selected)[0];
        if (!el) {
            this.notification.add(_t("Drag it onto the page, or select an element first: it is added after it."), {
                type: "info",
            });
            return;
        }
        const tag = el.tagName.toLowerCase();
        const position = tag === "td" || tag === "th" ? "inside" : "after";
        const inline = this.isInline(el);
        this.runOp(this.buildOp(payload, this.state.selected, position, this.wantsLabel(el, position, inline)));
    }

    remove() {
        if (this.state.selected) {
            this.runOp({ type: "remove", target: this.state.selected });
        }
    }

    duplicate() {
        this.runOp({ type: "duplicate", target: this.state.selected });
    }

    moveSibling(direction) {
        this.runOp({ type: "move_sibling", target: this.state.selected, direction });
    }

    tableColumn(action) {
        this.runOp({ type: "table_column", target: this.state.selected, action });
    }

    setAttrs(attrs) {
        this.runOp({ type: "set_attrs", target: this.state.selected, attrs });
    }

    setAttr(name, value) {
        const current = this.state.node?.attrs[name] || "";
        if ((value || "") !== current) {
            this.setAttrs({ [name]: value || "" });
        }
    }

    setStyle(prop, value) {
        const style = this.currentStyle;
        if (value) {
            style[prop] = value;
        } else {
            delete style[prop];
        }
        this.setAttr("style", formatStyle(style));
    }

    toggleStyle(prop, value) {
        this.setStyle(prop, this.currentStyle[prop] === value ? "" : value);
    }

    // ------------------------------------------------------------------
    // Expressions
    // ------------------------------------------------------------------

    /** Expressions of the selection not already edited by the Field / Visibility sections. */
    get otherExpressions() {
        const node = this.state.node;
        return (node?.expressions || []).filter(
            (item) => item.inside || (
                item.attr !== this.fieldDirective && item.attr !== "t-if"
                // display options edited in the Field section when they are a plain dict
                && !(item.attr === "t-options" && node.display?.parsed)
            )
        );
    }

    /**
     * Open the expression editor: the variables of this spot of the report (with their
     * fields) next to the text box.
     */
    editExpression({ title, attr, value, help, onSave, joinFields = false }) {
        this.dialog.add(ExpressionEditor, {
            title,
            attr,
            value: value || "",
            help: help || "",
            joinFields,
            scope: this.state.scope,
            variables: this.state.node?.variables || [],
            loadFields: this.loadFields,
            onSave,
        });
    }

    editFieldExpression() {
        const attr = this.fieldDirective;
        this.editExpression({
            title: attr === "t-field" ? _t("Field shown") : _t("Value shown"),
            attr: attr === "t-raw" ? attr : "t-out",
            value: this.state.node.attrs[attr],
            help: _t(
                "A field of a record, e.g. o.partner_id.name, or several joined: clicking another field adds it after a space."
            ),
            joinFields: true,
            onSave: (value) => this.setFieldExpression(value),
        });
    }

    /**
     * Whether ``value`` is a field of a record of that spot (o.partner_id.name):
     * shown with t-field, which formats it (dates, amounts, links...).
     */
    async isFieldPath(value) {
        const match = /^\s*([A-Za-z_]\w*)((?:\.[A-Za-z_]\w*)+)\s*$/.exec(value || "");
        if (!match) {
            return false;
        }
        const variables = this.state.node?.variables || [];
        let model = variables.find((v) => v.var === match[1])?.model
            || this.state.scope.find((v) => v.var === match[1] && !v.column)?.model;
        const names = match[2].slice(1).split(".");
        for (const [index, name] of names.entries()) {
            if (!model) {
                return false;
            }
            const field = (await this.loadFields(model)).find((f) => f.name === name);
            if (!field || (index < names.length - 1 && !field.relation)) {
                return false;
            }
            model = field.relation;
        }
        return true;
    }

    /**
     * Value shown by the selection: a field keeps / gets t-field; anything else
     * (several fields joined, a computation) is shown with t-out instead, as t-field
     * only takes one field.
     */
    async setFieldExpression(value) {
        value = (value || "").trim();
        const current = this.fieldDirective;
        if (!value || value === (this.state.node.attrs[current] || "").trim()) {
            return;
        }
        let directive = current;
        if (current !== "t-raw") {
            directive = (await this.isFieldPath(value)) ? "t-field" : current === "t-field" ? "t-out" : current;
        }
        const attrs = { [directive]: value };
        if (directive !== current) {
            attrs[current] = ""; // removes it
        }
        // line breaks ('\n') of the value are printed only with white-space: pre-line
        const style = this.currentStyle;
        if (value.includes("\\n") && !style["white-space"]) {
            style["white-space"] = "pre-line";
            attrs.style = formatStyle(style);
        }
        return this.setAttrs(attrs);
    }

    editCondition() {
        this.editExpression({
            title: _t("Show only when"),
            attr: "t-if",
            value: this.state.node.attrs["t-if"],
            help: _t("The element is printed only when this is true. Empty: always."),
            onSave: (value) => {
                this.state.showCondition = Boolean(value);
                return this.setAttr("t-if", value);
            },
        });
    }

    editOtherExpression(item) {
        this.editExpression({
            title: item.label,
            attr: item.attr,
            value: item.value,
            onSave: (value) => this.setExpression(item, value),
        });
    }

    setExpression(item, value) {
        if ((value || "").trim() === (item.value || "").trim()) {
            return;
        }
        return this.runOp({ type: "set_expression", target: this.state.selected, child: item.child, attr: item.attr, value });
    }

    /** Static text of a <t> inside the selection (e.g. shown under a condition). */
    setInnerText(item, value) {
        if ((value || "").trim() === (item.value || "").trim()) {
            return;
        }
        const html = (value || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        return this.runOp({ type: "set_text", target: this.state.selected, child: item.child, html });
    }

    setLength(prop, raw) {
        this.setStyle(prop, toCssLength(raw));
    }

    setSide(prop, side, raw) {
        const style = this.currentStyle;
        const sides = readSides(style, prop);
        sides[side] = toCssLength(raw);
        writeSides(style, prop, sides);
        this.setAttr("style", formatStyle(style));
    }

    /** Same value on the four sides (the "all" box in the middle of a ring). */
    setAllSides(prop, raw) {
        const style = this.currentStyle;
        const value = toCssLength(raw);
        writeSides(style, prop, { top: value, right: value, bottom: value, left: value });
        this.setAttr("style", formatStyle(style));
    }

    updateBorder(changes) {
        const style = this.currentStyle;
        const border = readBorder(style);
        if (changes.sides) {
            Object.assign(border.sides, changes.sides);
        }
        for (const key of ["width", "style", "color"]) {
            if (key in changes) {
                border[key] = changes[key];
            }
        }
        writeBorder(style, border);
        this.setAttr("style", formatStyle(style));
    }

    setBorderWidth(raw) {
        this.updateBorder({ width: toCssLength(raw) });
    }

    toggleBorderSide(side) {
        this.updateBorder({ sides: { [side]: !this.borderInfo.sides[side] } });
    }

    setBorderAll(on) {
        this.updateBorder({ sides: { top: on, right: on, bottom: on, left: on } });
    }

    setVisibility(mode) {
        if (mode === "always") {
            this.state.showCondition = false;
            this.setAttr("t-if", "");
        } else if (mode === "empty") {
            this.state.showCondition = false;
            this.setAttr("t-if", this.conditionExpr);
        } else {
            this.state.showCondition = true;
        }
    }

    toggleAdvanced() {
        this.state.showAdvanced = !this.state.showAdvanced;
    }

    // ------------------------------------------------------------------
    // Display options (t-options), from the options the formatters declare
    // ------------------------------------------------------------------

    /** Options of the formatter chosen for the value shown. */
    get displayOptions() {
        const display = this.state.node?.display;
        return display ? display.options[display.widget] || display.options[""] || [] : [];
    }

    /** Current value of an option (its default for lists of choices). */
    optionValue(spec) {
        const item = this.state.node.display.values[spec.name];
        if (item) {
            return item.value;
        }
        return spec.type === "array" ? spec.default_value || [] : undefined;
    }

    orEmpty(value) {
        return value === undefined || value === null ? "" : value;
    }

    /** [value, label] choices of a selection option. */
    selectionChoices(spec) {
        const params = spec.params;
        const choices = Array.isArray(params) ? params : params?.selection || [];
        return choices.map((choice) => (Array.isArray(choice) ? choice : [choice.field_name, choice.label]));
    }

    optionPlaceholder(spec) {
        if (spec.params?.placeholder) {
            return spec.params.placeholder;
        }
        const choice = this.selectionChoices(spec).find((c) => c[0] === spec.default_value);
        return choice ? _t("Default (%s)", choice[1]) : _t("Default");
    }

    saveDisplay(values) {
        return this.runOp({ type: "set_display", target: this.state.selected, options: values });
    }

    /**
     * Another formatter: its own options are kept when they also apply, the others
     * dropped; required ones get their default (e.g. the document's currency).
     */
    setDisplayWidget(key) {
        const display = this.state.node.display;
        const oldNames = new Set(this.displayOptions.map((spec) => spec.name));
        const specs = display.options[key] || display.options[""] || [];
        const newNames = new Set(specs.map((spec) => spec.name));
        const values = {};
        if (key) {
            values.widget = { value: key, expr: false };
        }
        for (const [name, item] of Object.entries(display.values)) {
            if (name !== "widget" && (newNames.has(name) || !oldNames.has(name))) {
                values[name] = item;
            }
        }
        for (const spec of specs) {
            if (spec.required && !values[spec.name]) {
                if (spec.type === "model" && spec.candidates?.length) {
                    values[spec.name] = { value: spec.candidates[0].expr, expr: true };
                } else if (spec.default_value !== undefined && spec.default_value !== null) {
                    values[spec.name] = { value: spec.default_value, expr: false };
                }
            }
        }
        return this.saveDisplay(values);
    }

    setOption(spec, value, expr = false) {
        const values = { ...this.state.node.display.values };
        if (value === "" || value === false || value === null || value === undefined) {
            delete values[spec.name];
        } else {
            values[spec.name] = { value, expr };
        }
        return this.saveDisplay(values);
    }

    toggleOptionItem(spec, name, checked) {
        const current = new Set(this.optionValue(spec) || []);
        if (checked) {
            current.add(name);
        } else {
            current.delete(name);
        }
        // keep the order of the choices
        const ordered = (spec.params?.params || []).map((c) => c.field_name).filter((n) => current.has(n));
        return this.setOption(spec, ordered);
    }

    hideWhenEmpty() {
        const expr = this.state.node.attrs["t-field"] || this.state.node.attrs["t-out"] || this.state.node.attrs["t-esc"];
        if (expr) {
            this.setAttr("t-if", expr);
        }
    }

    addAttribute() {
        const name = this.newAttrNameRef()?.value.trim();
        const value = this.newAttrValueRef()?.value || "";
        if (name) {
            this.setAttrs({ [name]: value });
        }
    }

    setText(html) {
        if (html !== this.state.node.inner_html) {
            this.runOp({ type: "set_text", target: this.state.selected, html });
        }
    }

    applyXml() {
        const xml = this.xmlRef()?.value;
        if (xml && xml !== this.state.node.source) {
            this.runOp({ type: "replace_xml", target: this.state.selected, xml });
        }
    }

    // ------------------------------------------------------------------
    // Inline text edition
    // ------------------------------------------------------------------

    startInlineEdit(el) {
        if (this.editing) {
            return;
        }
        this.setHover(null);
        this.editing = { el, target: el.dataset.ncd, original: el.innerHTML };
        el.removeAttribute("draggable");
        el.classList.add("ncd-editing");
        el.setAttribute("contenteditable", "true");
        el.setAttribute("spellcheck", "true");
        el.focus();
        const range = this.frameDoc.createRange();
        range.selectNodeContents(el);
        const selection = this.frameDoc.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        this.editing.onKeydown = (ev) => {
            if (ev.key === "Escape") {
                ev.preventDefault();
                this.finishInlineEdit(false);
            } else if (ev.key === "Enter" && !ev.shiftKey) {
                ev.preventDefault();
                this.finishInlineEdit(true);
            }
        };
        this.editing.onBlur = () => this.finishInlineEdit(true);
        el.addEventListener("keydown", this.editing.onKeydown);
        el.addEventListener("blur", this.editing.onBlur);
    }

    finishInlineEdit(save) {
        const editing = this.editing;
        if (!editing) {
            return;
        }
        this.editing = null;
        const { el } = editing;
        el.removeEventListener("keydown", editing.onKeydown);
        el.removeEventListener("blur", editing.onBlur);
        el.removeAttribute("contenteditable");
        el.removeAttribute("spellcheck");
        el.classList.remove("ncd-editing");
        if (!save) {
            el.innerHTML = editing.original;
            this.highlightSelection();
            return;
        }
        const html = el.innerHTML;
        if (html !== editing.original) {
            this.runOp({ type: "set_text", target: editing.target, html });
        } else {
            this.highlightSelection();
        }
    }

    // ------------------------------------------------------------------
    // Drag and drop
    // ------------------------------------------------------------------

    onPaletteDragStart(ev, payload) {
        this.drag = payload;
        ev.dataTransfer.effectAllowed = "copy";
        ev.dataTransfer.setData("text/plain", payload.path || payload.block || "");
        this.setHover(null);
    }

    onFrameDragStart(ev) {
        const el = this.closestNcd(ev.target);
        if (this.editing || !el || el.dataset.ncd !== this.state.selected) {
            ev.preventDefault();
            return;
        }
        this.drag = { kind: "move", source: this.state.selected };
        ev.dataTransfer.effectAllowed = "move";
        ev.dataTransfer.setData("text/plain", "move");
        this.setHover(null);
    }

    onDragEnd() {
        this.drag = null;
        this.dropInfo = null;
        this.hideIndicator();
    }

    isInline(el) {
        const display = el.ownerDocument.defaultView.getComputedStyle(el).display;
        return display === "inline" || display === "inline-block" || display === "inline-flex";
    }

    /**
     * A field or block held over the splice between two columns of a row (or over the
     * outer edge of the first / last one) is dropped as a new column there.
     */
    findColumnSplice(ev) {
        const doc = this.frameDoc;
        const start = ev.target?.nodeType === 1 ? ev.target : ev.target?.parentElement;
        for (let el = start; el && el !== doc.body; el = el.parentElement) {
            if (!el.classList.contains("row")) {
                continue;
            }
            const columns = [...el.children].filter((child) => isGridColumn(child) && child.dataset.ncd);
            if (!columns.length) {
                continue;
            }
            const rects = columns.map((column) => column.getBoundingClientRect());
            const splices = [{ x: rects[0].left, index: 0, position: "before" }];
            for (let i = 0; i < rects.length - 1; i++) {
                splices.push({ x: (rects[i].right + rects[i + 1].left) / 2, index: i, position: "after" });
            }
            splices.push({ x: rects.at(-1).right, index: rects.length - 1, position: "after" });
            const splice = splices.find((s) => Math.abs(ev.clientX - s.x) <= 10);
            if (!splice) {
                return null;
            }
            const row = el.getBoundingClientRect();
            const column = columns[splice.index];
            return {
                el: column,
                target: column.dataset.ncd,
                position: splice.position,
                newColumn: true,
                inline: false,
                rect: { left: splice.x, top: row.top, width: 0, height: row.height },
            };
        }
        return null;
    }

    /**
     * A field of the lines of a table (line.xxx, not a field of the document) held over
     * the edge between two cells of that table is dropped as a new table column there.
     */
    findTableSplice(ev) {
        const payload = this.drag;
        const baseVars = new Set((this.state.setup?.scope || []).map((variable) => variable.var));
        if (payload?.kind !== "field" || baseVars.has(payload.var)) {
            return null;
        }
        const start = ev.target?.nodeType === 1 ? ev.target : ev.target?.parentElement;
        const cell = start?.closest?.("td[data-ncd], th[data-ncd]");
        const table = cell?.closest("table");
        if (!cell || !table || cell.colSpan > 1) {
            return null;
        }
        const rect = cell.getBoundingClientRect();
        let position = null;
        if (ev.clientX - rect.left <= 8) {
            position = "before";
        } else if (rect.right - ev.clientX <= 8) {
            position = "after";
        } else if (cell.tagName === "TH") {
            // anywhere on a header cell: after it, or before it on its left half
            position = ev.clientX < rect.left + rect.width / 2 ? "before" : "after";
        }
        if (!position) {
            return null;
        }
        const box = table.getBoundingClientRect();
        return {
            el: cell,
            target: cell.dataset.ncd,
            position,
            newTableColumn: true,
            inline: false,
            rect: { left: position === "before" ? rect.left : rect.right, top: box.top, width: 0, height: box.height },
        };
    }

    computeDrop(ev) {
        if (this.drag && this.drag.kind !== "move") {
            const splice = this.findTableSplice(ev) || this.findColumnSplice(ev);
            if (splice) {
                return splice;
            }
        }
        let el = this.closestNcd(ev.target);
        if (!el) {
            return null;
        }
        let tag = el.tagName.toLowerCase();
        if (TABLE_PARTS.has(tag)) {
            const table = el.closest("table[data-ncd]");
            if (!table) {
                return null;
            }
            el = table;
            tag = "table";
        }
        if (this.drag?.kind === "move") {
            if (this.elementsOf(this.drag.source).some((source) => source === el || source.contains(el))) {
                return null;
            }
        }
        const rect = el.getBoundingClientRect();
        const inline = this.isInline(el);
        let position;
        if (tag === "td" || tag === "th") {
            position = "inside";
        } else if (inline) {
            position = ev.clientX < rect.left + rect.width / 2 ? "before" : "after";
        } else {
            const y = ev.clientY - rect.top;
            const canContain = !el.hasAttribute("data-ncd-leaf") && !VOID_TAGS.has(tag) && tag !== "table";
            if (canContain && y > rect.height * 0.25 && y < rect.height * 0.75) {
                position = "inside";
            } else {
                position = y < rect.height / 2 ? "before" : "after";
            }
        }
        return { el, target: el.dataset.ncd, position, inline, rect };
    }

    showIndicator(info) {
        const indicator = this.indicator;
        if (!indicator) {
            return;
        }
        const win = this.frameDoc.defaultView;
        const { rect, position, inline } = info;
        const left = rect.left + win.scrollX;
        const top = rect.top + win.scrollY;
        const newColumn = Boolean(info.newColumn || info.newTableColumn);
        indicator.classList.toggle("ncd-inside", position === "inside" && !newColumn);
        indicator.classList.toggle("ncd-newcol", newColumn);
        let box;
        if (newColumn) {
            box = { left: left - 3, top, width: 6, height: rect.height };
        } else if (position === "inside") {
            box = { left, top, width: rect.width, height: rect.height };
        } else if (inline) {
            box = { left: position === "before" ? left - 2 : left + rect.width - 1, top, width: 3, height: rect.height };
        } else {
            box = { left, top: position === "before" ? top - 2 : top + rect.height - 1, width: rect.width, height: 3 };
        }
        Object.assign(indicator.style, {
            display: "block",
            left: `${box.left}px`,
            top: `${box.top}px`,
            width: `${box.width}px`,
            height: `${box.height}px`,
        });
    }

    hideIndicator() {
        if (this.indicator) {
            this.indicator.style.display = "none";
        }
    }

    onFrameDragOver(ev) {
        if (!this.drag) {
            return;
        }
        const info = this.computeDrop(ev);
        if (!info) {
            this.dropInfo = null;
            this.hideIndicator();
            return;
        }
        ev.preventDefault();
        ev.dataTransfer.dropEffect = this.drag.kind === "move" ? "move" : "copy";
        this.dropInfo = info;
        this.showIndicator(info);
    }

    onFrameDragLeave(ev) {
        if (!ev.relatedTarget) {
            this.hideIndicator();
        }
    }

    onFrameDrop(ev) {
        ev.preventDefault();
        const drag = this.drag;
        const info = this.dropInfo || (drag && this.computeDrop(ev));
        this.onDragEnd();
        if (!drag || !info) {
            return;
        }
        if (drag.kind === "move" && info.target === drag.source) {
            return;
        }
        if (info.newTableColumn) {
            // the field's label in the header, its value in each line
            this.runOp({
                type: "table_column",
                target: info.target,
                action: info.position === "before" ? "left" : "right",
                var: drag.var,
                path: drag.path,
            });
            return;
        }
        // a new column gets its label above the value, as the header boxes have
        const label = info.newColumn || this.wantsLabel(info.el, info.position, info.inline);
        const op = this.buildOp(drag, info.target, info.position, label, Boolean(info.newColumn));
        if (op && info.newColumn) {
            op.visible = this.visibleColumns(info.el.parentElement);
        }
        this.runOp(op);
    }

    // ------------------------------------------------------------------
    // Keyboard
    // ------------------------------------------------------------------

    onKeydown(ev) {
        const target = ev.target;
        if (
            this.editing ||
            !this.state.setup ||
            document.querySelector(".modal") ||
            target?.closest?.("input, textarea, select, [contenteditable]")
        ) {
            return;
        }
        const ctrl = ev.ctrlKey || ev.metaKey;
        const key = ev.key.toLowerCase();
        if (ctrl && key === "z" && !ev.shiftKey) {
            ev.preventDefault();
            this.undo();
        } else if (ctrl && (key === "y" || (key === "z" && ev.shiftKey))) {
            ev.preventDefault();
            this.redo();
        } else if (!this.state.selected) {
            return;
        } else if (key === "delete") {
            ev.preventDefault();
            this.remove();
        } else if (key === "escape") {
            this.clearSelection();
        } else if (ev.altKey && key === "arrowup") {
            ev.preventDefault();
            this.selectParent();
        }
    }

    // ------------------------------------------------------------------
    // Fields palette
    // ------------------------------------------------------------------

    async loadFields(model) {
        if (!this.fieldsCache[model]) {
            this.fieldsCache[model] = this.orm.call(MODEL, "get_fields", [model]);
        }
        return this.fieldsCache[model];
    }

    setLeftTab(tab) {
        this.state.leftTab = tab;
    }

    toggleVar(variable) {
        this.state.openVars[variable] = !this.state.openVars[variable];
    }

    // ------------------------------------------------------------------
    // Changes tab
    // ------------------------------------------------------------------

    async showTab(tab) {
        this.state.rightTab = tab;
        if (tab !== "changes" && this.state.viewingAt !== null) {
            this.state.viewingAt = null; // back to the report as it is
            this.reloadPreview();
        }
        if (tab === "changes") {
            await this.loadChanges();
        } else if (tab === "hidden") {
            await this.loadHidden();
        }
    }

    /** The "Show hidden" switch (remembered in this browser). */
    async setShowHidden(on) {
        this.state.showHidden = on;
        try {
            browser.localStorage.setItem(SHOW_HIDDEN_KEY, on ? "1" : "");
        } catch {
            // private browsing...: only for this session
        }
        if (on) {
            this.hiddenForceFailed = false;
            await this.loadHidden(); // draws them (showAllHidden)
        } else {
            this.stopShowingAllHidden();
        }
    }

    /**
     * With the "Show hidden" switch on, every hidden element is drawn hatched on the
     * preview: those hidden by a condition are forced visible (the preview is drawn
     * again), those hidden by their classes / style are shown in place. Not possible
     * for the ones repeated over an empty list. Selecting one keeps the others drawn.
     */
    showAllHidden() {
        if (!this.state.showHidden) {
            return;
        }
        const doc = this.frameDoc;
        for (const item of this.state.hidden) {
            if (item.kind === "style") {
                for (const el of this.elementsOf(item.target)) {
                    el.setAttribute("data-ncd-ghost", "client");
                }
            }
        }
        if (this.hiddenForceFailed || !doc) {
            return;
        }
        // not drawn: listed again from a preview already forcing them, keep what is forced
        const wanted = new Set(this.hiddenForce || []);
        for (const item of this.state.hidden) {
            if (item.kind === "condition" || item.kind === "otherwise") {
                wanted.add(item.target);
            }
        }
        if (wanted.size !== (this.hiddenForce || []).length) {
            this.hiddenForce = [...wanted];
            this.reloadPreview();
        }
    }

    stopShowingAllHidden() {
        const selected = this.state.selected;
        for (const el of this.frameDoc?.querySelectorAll('[data-ncd-ghost="client"]') || []) {
            if (el.dataset.ncd !== selected) {
                el.removeAttribute("data-ncd-ghost");
            }
        }
        if (this.hiddenForce?.length) {
            if (selected && this.elementsOf(selected).some((el) => el.closest("[data-ncd-ghost='1']"))) {
                this.forced = selected; // the selected hidden element stays drawn
            }
            this.hiddenForce = [];
            this.reloadPreview();
        }
    }

    // ------------------------------------------------------------------
    // Hidden elements: not on the page for this record (condition, empty loop) or
    // hidden by their style / classes, so they cannot be clicked in the preview.
    // ------------------------------------------------------------------

    async loadHidden() {
        const doc = this.frameDoc;
        if (!doc?.body || !this.state.reportId) {
            this.state.hidden = [];
            return;
        }
        const win = doc.defaultView;
        const isHidden = (el) => el.getAttribute("data-ncd-ghost") === "client" || win.getComputedStyle(el).display === "none";
        const styleHidden = new Set();
        for (const el of doc.querySelectorAll("[data-ncd]")) {
            if (!isHidden(el)) {
                continue;
            }
            const parent = el.parentElement?.closest("[data-ncd]");
            if (!parent || !isHidden(parent)) {
                styleHidden.add(el.dataset.ncd); // only the outermost hidden element
            }
        }
        // an element drawn only because it is selected (forced, hatched) is not on the page
        const rendered = [...new Set(
            [...doc.querySelectorAll("[data-ncd]")].filter((el) => !el.closest("[data-ncd-ghost='1']")).map((el) => el.dataset.ncd)
        )];
        const hidden = this.viewIds.length
            ? await this.orm.call(MODEL, "get_hidden", [this.state.reportId, this.viewIds, rendered, [...styleHidden]])
            : [];
        // what the user hid first
        this.state.hidden = [...hidden.filter((item) => item.by_designer), ...hidden.filter((item) => !item.by_designer)];
        this.showAllHidden();
    }

    get hiddenByDesigner() {
        return this.state.hidden.filter((item) => item.by_designer);
    }

    get hiddenByTemplate() {
        return this.state.hidden.filter((item) => !item.by_designer);
    }

    toggleTemplateHidden() {
        this.state.showTemplateHidden = !this.state.showTemplateHidden;
    }

    selectHidden(item) {
        this.select(item.target);
    }

    /** Make a hidden element show again: drop its own condition, or the classes / style hiding it. */
    async showHidden(item) {
        if (item.kind === "style") {
            const node = await this.orm.call(MODEL, "get_node", [this.state.reportId, item.target]);
            const attrs = {};
            const classes = (node.attrs.class || "").split(/\s+/).filter((cls) => cls && !/^d(-[a-z]+)?-none$/.test(cls));
            if (classes.join(" ") !== (node.attrs.class || "").trim()) {
                attrs.class = classes.join(" ");
            }
            const style = parseStyle(node.attrs.style);
            if (style.display === "none" || style.visibility === "hidden") {
                delete style.display;
                delete style.visibility;
                attrs.style = formatStyle(style);
            }
            if (!Object.keys(attrs).length) {
                this.notification.add(_t("It is hidden by a style of the layout: select it and look at its Advanced settings."), {
                    type: "info",
                });
                this.select(item.target);
                return;
            }
            await this.runOp({ type: "set_attrs", target: item.target, attrs });
        } else if (item.own_condition) {
            // back to the template's own condition when the designer replaced one
            const condition = item.by_designer ? item.original_expression || "" : "";
            await this.runOp({ type: "set_attrs", target: item.target, attrs: { "t-if": condition } });
        }
    }

    async loadChanges() {
        if (!this.viewIds.length) {
            this.state.changes = [];
            this.state.timeline = [];
            this.state.timelineMore = 0;
            return;
        }
        const [changes, timeline] = await Promise.all([
            this.orm.call(MODEL, "get_changes", [this.viewIds]),
            this.orm.call(MODEL, "get_history", [this.viewIds]),
        ]);
        this.state.changes = changes;
        this.state.timeline = timeline.changes;
        this.state.timelineMore = timeline.more;
    }

    changeDate(change) {
        return formatDateTime(deserializeDateTime(change.date), { format: "MMM d, yyyy HH:mm" });
    }

    /** Show the report as it was just after this change (0: before them all, null: now). */
    viewAt(changeId) {
        const latest = this.state.timeline[0]?.id;
        const at = changeId === latest ? null : changeId;
        if (at === this.state.viewingAt) {
            return;
        }
        if (this.state.viewingAt === null) {
            this.clearSelection();
        }
        this.state.viewingAt = at;
        this.reloadPreview();
    }

    get viewingChange() {
        const at = this.state.viewingAt;
        return at ? this.state.timeline.find((change) => change.id === at) : null;
    }

    resetTo(changeId) {
        const change = this.state.timeline.find((c) => c.id === changeId);
        const later = changeId
            ? this.state.timeline.findIndex((c) => c.id === changeId)
            : this.state.timeline.length;
        this.dialog.add(ConfirmationDialog, {
            title: _t("Go back to this point"),
            body: change
                ? _t(
                      "Put the templates of this report back as they were just after “%(name)s” (%(date)s)? The %(count)s later change(s) are undone. Going back is listed as a change of its own: you can come back here the same way.",
                      { name: change.name, date: this.changeDate(change), count: later }
                  )
                : _t(
                      "Put the templates of this report back as they were before all %(count)s change(s)? Going back is listed as a change of its own: you can come back the same way.",
                      { count: later }
                  ),
            confirmLabel: _t("Go back"),
            confirm: async () => {
                await this.orm.call(MODEL, "reset_to", [changeId, this.viewIds, this.state.reportId]);
                // the designer views were rewritten (some made again): the undo steps no longer apply
                this.undoStack = [];
                this.redoStack = [];
                this.syncHistory();
                this.state.viewingAt = null;
                this.clearSelection();
                this.reloadPreview();
            },
            cancel: () => {},
        });
    }

    toggleChangeXml(change) {
        this.state.editingChange = this.state.editingChange === change.id ? false : change.id;
    }

    async saveChangeXml(change) {
        const arch = this.changeXmlRef()?.value;
        if (!arch || arch === change.arch) {
            return;
        }
        const entry = await this.orm.call(MODEL, "save_change_arch", [change.id, arch]);
        this.undoStack.push([entry]);
        this.redoStack = [];
        this.syncHistory();
        this.state.editingChange = false;
        this.reloadPreview();
    }

    resetChange(change) {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Reset template"),
            body: _t(
                "Drop all %(count)s designer change(s) of “%(name)s”? This cannot be undone.",
                { count: change.count, name: change.template.name }
            ),
            confirmLabel: _t("Reset"),
            confirm: async () => {
                await this.orm.call(MODEL, "reset_change", [change.id]);
                const keep = (entries) => entries.every((entry) => entry.view_id !== change.id);
                this.undoStack = this.undoStack.filter(keep);
                this.redoStack = this.redoStack.filter(keep);
                this.syncHistory();
                this.clearSelection();
                this.reloadPreview();
            },
            cancel: () => {},
        });
    }
}

registry.category("actions").add("nc_report_designer.designer", ReportDesigner);
