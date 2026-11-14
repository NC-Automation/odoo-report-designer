import { Component, onMounted, proxy, signal, t, useProps } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { FieldList } from "./field_list";

const MODEL = "nc.report.designer";

// Building blocks offered under the text box: [label, text inserted, title]
const PYTHON_SNIPPETS = [
    ["and", " and ", "Both conditions are true"],
    ["or", " or ", "One of the conditions is true"],
    ["not", "not ", "The condition is false"],
    ["==", " == ", "Equal to"],
    ["!=", " != ", "Different from"],
    [">", " > ", "Greater than"],
    ["<", " < ", "Less than"],
    ["in", " in ", "One of a list: x in ('a', 'b')"],
    ["( )", "()", "Group"],
    ["'text'", "''", "A text value"],
    ["True", "True", "Yes"],
    ["False", "False", "No"],
];
const FORMAT_SNIPPETS = [["{{ }}", "{{  }}", "An expression inside the text"]];

const KEYWORDS = new Set([
    "and", "or", "not", "in", "is", "if", "else", "for", "lambda", "None", "True", "False",
]);
const BUILTINS = new Set([
    "any", "all", "len", "str", "int", "float", "bool", "round", "min", "max", "sum", "abs",
    "sorted", "list", "dict", "set", "tuple", "range", "enumerate", "zip", "format", "hasattr", "getattr",
]);
// strings, numbers, names, ".name", operators/brackets, spaces, anything else
const TOKEN_RE = /('(?:[^'\\\n]|\\.)*'?|"(?:[^"\\\n]|\\.)*"?)|(\d+(?:\.\d+)?)|([A-Za-z_]\w*)|(\.\s*[A-Za-z_]\w*)|([-+*/%<>=!&|^~]+|[()[\]{},:])|(\s+)|(.)/gs;

// To join several values in the "value shown" editor
const JOIN_SNIPPETS = [
    ["+ ' ' +", " + ' ' + ", "Join with a space"],
    ["+ ', ' +", " + ', ' + ", "Join with a comma"],
    ["+ ' - ' +", " + ' - ' + ", "Join with a dash"],
    ["str( or '')", "str( or '')", "As text, empty when missing (needed to join numbers, dates or empty fields)"],
];

// Text and layout helpers of the "value shown" editor:
// [label, text inserted, title, part selected after inserting ([from, to] in the text)]
const TEXT_SNIPPETS = [
    ["↵ Line break", " + '\\n' + ", "Continue on the next line"],
    ["¶ Blank line", " + '\\n\\n' + ", "Leave an empty line between them"],
    ["•", " + ' • ' + ", "Separate with a bullet"],
    ["|", " + ' | ' + ", "Separate with a vertical bar"],
    ["Label:", "'Label: ' + ", "Text printed before the value", [1, 6]],
    ["(…)", " + ' (' + ", "Opening bracket: close it with ')'"],
    [")", " + ')'", "Closing bracket"],
    ["UPPER", ".upper()", "In capitals (after a text value)"],
    ["Title", ".title()", "Capital On Each Word (after a text value)"],
];

/**
 * ``path`` as text that can be joined to other texts: empty fields give '' (not
 * False), numbers and dates are turned into text, links show their name.
 */
function asText(path, type) {
    if (type === "many2one") {
        return `str(${path}.display_name or '')`;
    }
    return `str(${path} or '')`;
}

// Offered after a dot on lines / tags fields: [name, text inserted, caret back from the end, help]
const RECORDSET_METHODS = [
    ["filtered", "filtered(lambda r: )", 1, "Only the lines for which the condition is true"],
    ["mapped", "mapped('')", 2, "The values of a field of every line"],
    ["sorted", "sorted(key=lambda r: )", 1, "The lines sorted"],
    ["ids", "ids", 0, "Their ids"],
];
// CSS properties copied to measure where the caret is in the text box
const MIRROR_PROPS = [
    "boxSizing", "width", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
    "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth", "borderStyle",
    "fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing", "tabSize",
    "whiteSpace", "overflowWrap", "wordBreak", "scrollbarGutter",
];
const MAX_SUGGESTIONS = 60;

/**
 * Tokens of a Python expression with their color class: variables (names available
 * at that spot), fields (after a dot), methods (after a dot, called), keywords,
 * builtins, strings, numbers, operators.
 */
export function highlightPython(source, variables) {
    const tokens = [];
    let match;
    TOKEN_RE.lastIndex = 0;
    while ((match = TOKEN_RE.exec(source))) {
        const [text, string, number, name, attr, operator] = match;
        let cls = "";
        if (string !== undefined) {
            cls = "o_ncd_h_str";
        } else if (number !== undefined) {
            cls = "o_ncd_h_num";
        } else if (name !== undefined) {
            cls = KEYWORDS.has(name) ? "o_ncd_h_kw"
                : variables.has(name) ? "o_ncd_c_var"
                : BUILTINS.has(name) ? "o_ncd_h_fn"
                : "o_ncd_h_name";
        } else if (attr !== undefined) {
            const called = /^\s*\(/.test(source.slice(TOKEN_RE.lastIndex));
            tokens.push({ text: ".", cls: "o_ncd_h_op" });
            tokens.push({ text: text.slice(1), cls: called ? "o_ncd_h_fn" : "o_ncd_c_field" });
            continue;
        } else if (operator !== undefined) {
            cls = "o_ncd_h_op";
        }
        tokens.push({ text, cls });
    }
    return tokens;
}

/** Tokens of a text with {{ }} / #{ } expressions: the text plain, the expressions colored. */
export function highlightFormat(source, variables) {
    const tokens = [];
    const re = /(\{\{|#\{)([\s\S]*?)(\}\}|\}|$)/g;
    let last = 0;
    let match;
    while ((match = re.exec(source)) && match[0]) {
        if (match.index > last) {
            tokens.push({ text: source.slice(last, match.index), cls: "o_ncd_h_text" });
        }
        tokens.push({ text: match[1], cls: "o_ncd_h_brace" });
        tokens.push(...highlightPython(match[2], variables));
        if (match[3]) {
            tokens.push({ text: match[3], cls: "o_ncd_h_brace" });
        }
        last = re.lastIndex;
    }
    if (last < source.length) {
        tokens.push({ text: source.slice(last), cls: "o_ncd_h_text" });
    }
    return tokens;
}

/**
 * Dialog to write a QWeb expression (conditions, values shown, attributes): a text box
 * with every variable available at that spot of the report, their fields expandable
 * (o.partner_id.name...); clicking or dragging a field inserts it at the cursor.
 */
export class ExpressionEditor extends Component {
    static template = "nc_report_designer.ExpressionEditor";
    static components = { Dialog, FieldList };
    props = useProps({
        title: t.string(),
        attr: t.string(),
        value: t.string().optional(),
        help: t.string().optional(),
        scope: t.array(),
        // every variable of that spot, with how it is computed (see _template_variables)
        variables: t.array().optional(),
        // editing the value shown by an element: fields clicked are joined
        joinFields: t.boolean().optional(),
        loadFields: t.function(),
        onSave: t.function(),
        close: t.function(),
    });

    setup() {
        this.orm = useService("orm");
        this.textRef = signal.ref();
        this.highlightRef = signal.ref();
        this.completionRef = signal.ref();
        const openVars = {};
        // open the closest record: the line of a table, else the document
        const records = this.records;
        const closest = records.filter((v) => v.kind === "loop" || v.kind === "set").at(-1) || records[0];
        if (closest) {
            openVars[closest.var] = true;
        }
        this.state = proxy({
            value: this.props.value || "", error: "", search: "", openVars, saving: false, showHelpers: false,
            completion: null, // {items, active, start, end, top, left}
        });
        this.snippets = this.isFormat ? FORMAT_SNIPPETS : PYTHON_SNIPPETS;
        onMounted(() => {
            const text = this.textRef();
            text.focus();
            text.setSelectionRange(text.value.length, text.value.length);
        });
    }

    get allVariables() {
        if (this.props.variables?.length) {
            return this.props.variables;
        }
        // older callers: the record variables only
        return this.props.scope
            .filter((v) => !v.column)
            .map((v) => ({ ...v, kind: "report", how: "info", expression: "", condition: "", where: "" }));
    }

    /** Variables holding records: their fields can be browsed. */
    get records() {
        return this.allVariables.filter((v) => v.model);
    }

    /** Variables set by the templates that hold other values (texts, amounts, flags...). */
    get values() {
        return this.filterBySearch(this.allVariables.filter((v) => !v.model && (v.kind === "set" || v.kind === "loop")));
    }

    /** Loop counters and tools (time, context_timestamp...). */
    get helpers() {
        return this.filterBySearch(this.allVariables.filter((v) => !v.model && v.kind !== "set" && v.kind !== "loop"));
    }

    filterBySearch(variables) {
        const search = this.state.search.trim().toLowerCase();
        return search ? variables.filter((v) => v.var.toLowerCase().includes(search)) : variables;
    }

    /** How a variable gets its value, in a few words. */
    definition(variable) {
        switch (variable.how) {
            case "value":
                return `= ${variable.expression}`;
            case "format":
                return `= "${variable.expression}"`;
            case "text":
                return `= "${variable.expression}"`;
            case "content":
                return `= ${variable.expression}`;
            case "loop":
                return `each item of ${variable.expression}`;
            default:
                return variable.expression || "";
        }
    }

    definitionTitle(variable) {
        const lines = [this.definition(variable)];
        if (variable.condition) {
            lines.push(`only if ${variable.condition}`);
        }
        if (variable.where) {
            lines.push(`set in ${variable.where}`);
        }
        return lines.join("\n");
    }

    /** The expression split in colored tokens, drawn under the (transparent) text box. */
    get tokens() {
        const names = new Set(this.allVariables.map((v) => v.var));
        const value = this.state.value;
        const tokens = this.isFormat ? highlightFormat(value, names) : highlightPython(value, names);
        // a final line break needs a character after it to take its height
        tokens.push({ text: "​", cls: "" });
        return tokens;
    }

    syncScroll() {
        const highlight = this.highlightRef();
        if (highlight) {
            highlight.scrollTop = this.textRef().scrollTop;
            highlight.scrollLeft = this.textRef().scrollLeft;
        }
    }

    get isFormat() {
        return this.props.attr.startsWith("t-attf-");
    }

    /** Insert ``text`` at the cursor; ``select``: [from, to] of it to select after. */
    insert(text, select = null) {
        const el = this.textRef();
        const start = el.selectionStart ?? el.value.length;
        const end = el.selectionEnd ?? el.value.length;
        // inside {{ }} for text attributes, the cursor goes between the braces
        const caret = text === "()" || text === "''" ? 1 : text === "{{  }}" ? 3 : text === "str( or '')" ? 4 : text.length;
        el.value = el.value.slice(0, start) + text + el.value.slice(end);
        this.state.value = el.value;
        this.closeCompletion();
        this.state.error = "";
        el.focus();
        if (select) {
            el.setSelectionRange(start + select[0], start + select[1]);
        } else {
            el.setSelectionRange(start + caret, start + caret);
        }
        this.syncScroll();
    }

    async insertField(payload) {
        const path = `${payload.var}.${payload.path}`;
        const el = this.textRef();
        const before = el.value.slice(0, el.selectionStart).trimEnd();
        if (!this.props.joinFields || !before) {
            this.insert(path);
        } else if (before.endsWith("+")) {
            // continuing a join ('Label: ' + ...): as text
            this.insert(asText(path, payload.type));
        } else if (/(\(|\[|,|\b(and|or|not|in|if|else))$/.test(before)) {
            this.insert(path);
        } else {
            // value shown with a field already there: join them with a space
            await this.wrapLoneField();
            this.insert(` + ' ' + ${asText(path, payload.type)}`);
        }
    }

    /**
     * Before joining something to a value that is a single field (o.invoice_date),
     * turn it into text, as a date, a number or an empty field cannot be joined.
     */
    async wrapLoneField() {
        const el = this.textRef();
        const existing = el.value.trim();
        const before = el.value.slice(0, el.selectionStart).trimEnd();
        if (/^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$/.test(existing) && before === existing) {
            const type = await this.fieldType(existing);
            el.value = asText(existing, type);
            this.state.value = el.value;
            el.setSelectionRange(el.value.length, el.value.length);
        }
    }

    /** Type of the field at ``path`` (o.partner_id.name), or "". */
    async fieldType(path) {
        const [name, ...names] = path.split(".");
        let model = this.allVariables.find((v) => v.var === name)?.model;
        let type = "";
        for (const fieldName of names) {
            if (!model) {
                return "";
            }
            const field = (await this.props.loadFields(model)).find((f) => f.name === fieldName);
            if (!field) {
                return "";
            }
            type = field.type;
            model = field.relation;
        }
        return type;
    }

    /** Joining helpers of the "value shown" editor. */
    get joinSnippets() {
        return this.props.joinFields ? JOIN_SNIPPETS : [];
    }

    get textSnippets() {
        return this.props.joinFields ? TEXT_SNIPPETS : [];
    }

    async insertSnippet(snippet) {
        if (snippet[1].startsWith(" + ")) {
            await this.wrapLoneField();
        }
        this.insert(snippet[1], snippet[3]);
    }

    insertVariable(variable) {
        this.insert(variable.var);
    }

    onFieldDragStart(ev, payload) {
        // a native drop into the text box inserts it where the mouse is
        ev.dataTransfer.effectAllowed = "copy";
        ev.dataTransfer.setData("text/plain", `${payload.var}.${payload.path}`);
    }

    toggleVar(name) {
        this.state.openVars[name] = !this.state.openVars[name];
    }

    onInput(ev) {
        this.state.value = ev.target.value;
        this.state.error = "";
        this.syncScroll();
        this.updateCompletion();
    }

    // ------------------------------------------------------------------
    // Autocomplete
    // ------------------------------------------------------------------

    onKeydown(ev) {
        const completion = this.state.completion;
        if (completion) {
            const count = completion.items.length;
            if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
                ev.preventDefault();
                completion.active = (completion.active + (ev.key === "ArrowDown" ? 1 : count - 1)) % count;
                this.scrollActiveIntoView();
                return;
            }
            if ((ev.key === "Enter" && !ev.ctrlKey) || ev.key === "Tab") {
                ev.preventDefault();
                this.acceptCompletion(completion.items[completion.active]);
                return;
            }
            if (ev.key === "Escape") {
                // closes the list, not the dialog
                ev.preventDefault();
                ev.stopPropagation();
                this.closeCompletion();
                return;
            }
        }
        if (ev.ctrlKey && ev.key === " ") {
            ev.preventDefault();
            this.updateCompletion(true);
        } else if (ev.ctrlKey && ev.key === "Enter") {
            ev.preventDefault();
            this.save();
        } else if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(ev.key)) {
            this.closeCompletion();
        }
    }

    setActiveCompletion(index) {
        // the list may be closing (an item left under the mouse while it re-renders)
        if (this.state.completion) {
            this.state.completion.active = index;
        }
    }

    closeCompletion() {
        this.completionRequest = (this.completionRequest || 0) + 1;
        this.state.completion = null;
    }

    /**
     * Suggestions for the word before the cursor: the variables of that spot (and
     * Python keywords / functions) for a name, the fields of the record for
     * ``o.partner_id.`` (following the relations), recordset methods for lines.
     * ``force`` (Ctrl+Space): also with nothing typed yet.
     */
    async updateCompletion(force = false) {
        const el = this.textRef();
        const request = (this.completionRequest = (this.completionRequest || 0) + 1);
        if (!el || el.selectionStart !== el.selectionEnd) {
            this.state.completion = null;
            return;
        }
        const caret = el.selectionStart;
        const before = el.value.slice(0, caret);
        let code = before;
        if (this.isFormat) {
            const open = Math.max(before.lastIndexOf("{{"), before.lastIndexOf("#{"));
            if (open < 0 || before.lastIndexOf("}") > open) {
                this.state.completion = null;
                return;
            }
            code = before.slice(open + 2);
        }
        // not inside a text between quotes
        const quotes = code.replace(/\\./g, "").match(/['"]/g) || [];
        if (quotes.length % 2) {
            this.state.completion = null;
            return;
        }
        const match = code.match(/((?:[A-Za-z_]\w*\.)*)([A-Za-z_]\w*)?$/);
        const chain = match[1] ? match[1].slice(0, -1).split(".") : [];
        const partial = match[2] || "";
        const charBefore = code[code.length - match[0].length - 1];
        if ((!chain.length && !partial && !force) || charBefore === "." || /\d/.test(charBefore || "")) {
            this.state.completion = null;
            return;
        }
        const items = chain.length ? await this.memberSuggestions(chain, partial) : this.nameSuggestions(partial);
        if (request !== this.completionRequest) {
            return; // the text changed meanwhile
        }
        if (!items.length || (items.length === 1 && items[0].insert === partial)) {
            this.state.completion = null;
            return;
        }
        this.state.completion = {
            items: items.slice(0, MAX_SUGGESTIONS),
            active: 0,
            start: caret - partial.length,
            end: caret,
            ...this.caretPosition(el, caret - partial.length),
        };
    }

    rank(items, partial) {
        const lower = partial.toLowerCase();
        const scored = [];
        for (const item of items) {
            const name = item.label.toLowerCase();
            const score = name.startsWith(lower) ? 0
                : name.includes(lower) ? 1
                : (item.detail || "").toLowerCase().includes(lower) ? 2 : -1;
            if (score >= 0) {
                scored.push([score, item]);
            }
        }
        scored.sort((a, b) => a[0] - b[0] || (a[1].priority || 0) - (b[1].priority || 0) || a[1].label.localeCompare(b[1].label));
        return scored.map(([, item]) => item);
    }

    nameSuggestions(partial) {
        const items = this.allVariables.map((v) => ({
            label: v.var,
            insert: v.var,
            cls: "o_ncd_c_var",
            detail: v.model_name || this.definition(v),
            detailCls: v.model_name ? "o_ncd_c_table" : "",
        }));
        for (const word of KEYWORDS) {
            items.push({ label: word, insert: word, cls: "o_ncd_h_kw", detail: "", priority: 2 });
        }
        for (const name of BUILTINS) {
            items.push({ label: name, insert: `${name}()`, caretBack: 1, cls: "o_ncd_h_fn", detail: "function", priority: 3 });
        }
        return partial ? this.rank(items, partial) : items.filter((item) => item.cls === "o_ncd_c_var");
    }

    async memberSuggestions(chain, partial) {
        const variable = this.allVariables.find((v) => v.var === chain[0]);
        let model = variable?.model;
        // a set variable may hold several records (e.g. lines = o.invoice_line_ids.sorted(...))
        let many = variable?.var === "docs" || variable?.kind === "set";
        if (!model) {
            return [];
        }
        for (const name of chain.slice(1)) {
            const field = (await this.props.loadFields(model)).find((f) => f.name === name);
            if (!field?.relation) {
                return [];
            }
            model = field.relation;
            many = field.type !== "many2one";
        }
        const items = (await this.props.loadFields(model)).map((field) => ({
            label: field.name,
            insert: field.name,
            cls: "o_ncd_c_field",
            detail: field.relation_name ? `${field.string} → ${field.relation_name}` : field.string,
            detailCls: field.relation_name ? "o_ncd_c_table" : "",
        }));
        if (many) {
            for (const [name, insert, caretBack, help] of RECORDSET_METHODS) {
                items.push({ label: name, insert, caretBack, cls: "o_ncd_h_fn", detail: help, priority: 1 });
            }
        }
        return partial ? this.rank(items, partial) : items;
    }

    acceptCompletion(item) {
        const completion = this.state.completion;
        if (!item || !completion) {
            return;
        }
        const el = this.textRef();
        el.value = el.value.slice(0, completion.start) + item.insert + el.value.slice(completion.end);
        const caret = completion.start + item.insert.length - (item.caretBack || 0);
        this.state.value = el.value;
        this.state.error = "";
        this.closeCompletion();
        el.focus();
        el.setSelectionRange(caret, caret);
        this.syncScroll();
    }

    /** Where the character at ``index`` is drawn, relative to the text box. */
    caretPosition(el, index) {
        const style = getComputedStyle(el);
        const mirror = document.createElement("div");
        for (const prop of MIRROR_PROPS) {
            mirror.style[prop] = style[prop];
        }
        Object.assign(mirror.style, { position: "absolute", visibility: "hidden", top: "0", left: "0", height: "auto", overflow: "hidden" });
        mirror.textContent = el.value.slice(0, index);
        const marker = document.createElement("span");
        marker.textContent = "​";
        mirror.appendChild(marker);
        el.parentElement.appendChild(mirror);
        const top = marker.offsetTop + marker.offsetHeight - el.scrollTop + 2;
        const left = Math.min(marker.offsetLeft - el.scrollLeft, Math.max(0, el.clientWidth - 320));
        mirror.remove();
        return { top, left };
    }

    scrollActiveIntoView() {
        // after the next render
        setTimeout(() => {
            this.completionRef()?.querySelector(".active")?.scrollIntoView({ block: "nearest" });
        });
    }

    async save() {
        const value = this.textRef().value.trim();
        this.state.saving = true;
        try {
            const error = await this.orm.call(MODEL, "check_expression", [this.props.attr, value]);
            if (error) {
                this.state.error = error;
                return;
            }
            await this.props.onSave(value);
            this.props.close();
        } finally {
            this.state.saving = false;
        }
    }
}
