window.Page = window.Page || {};

window.Page.dashboard = {

    timer: null,

    // The saved widgets ([{ id, type, width, hidden, interface? }]) and
    // the built-in default (from /api/dashboard/layout).
    layout: [],
    defaultLayout: [],

    // What is on the page: [{ widget, element, graph? }].
    shown: [],

    // From the last /api/dashboard answer.
    interfaces: [],
    wanInterface: null,

    // The editor's working copy, saved only with "Save".
    draft: null,

    NAMES: {
        summary: "Overview (clients, download, upload)",
        status: "Internet check",
        usage: "Mobile data used",
        traffic: "Traffic graph",
        interfaces: "Interfaces",
        system: "System (CPU, RAM, uptime)"
    },

    TEMPLATES: {
        summary: "widgetSummary",
        status: "widgetStatus",
        usage: "widgetUsage",
        traffic: "widgetTraffic",
        interfaces: "widgetInterfaces",
        system: "widgetSystem"
    },

    // Columns out of 12 that make sense per widget.
    WIDTHS: {
        summary: [6, 8, 12],
        status: [4, 6, 8, 12],
        usage: [3, 4, 6, 8, 12],
        traffic: [4, 6, 8, 9, 12],
        interfaces: [3, 4, 6, 8, 12],
        system: [6, 8, 12]
    },

    WIDTH_LABELS: {
        3: "Quarter",
        4: "Third",
        6: "Half",
        8: "Two thirds",
        9: "Three quarters",
        12: "Full width"
    },


    // ------------------------------------------------------------
    // Layout
    // ------------------------------------------------------------

    async loadLayout() {

        try {

            const res = await fetch("/api/dashboard/layout");

            if (res.status === 401) {
                location.href = "/login";
                return;
            }

            const data = await res.json();

            this.layout = data.layout;
            this.defaultLayout = data.default;

        } catch {

            this.layout = [];

        }

    },


    /** Saves a layout; true when the router accepted it. */
    async saveLayout(layout) {

        try {

            const res = await fetch("/api/dashboard/layout", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ layout })
            });

            const data = await res.json();

            if (!res.ok || !data.success) {
                return data.message || "The dashboard layout could not be saved.";
            }

            this.layout = data.layout;

            return true;

        } catch {

            return "The router did not answer. Try again.";

        }

    },


    /** Saves a change made on the dashboard itself (not the editor). */
    async changeLayout(layout) {

        const result = await this.saveLayout(layout);

        if (result !== true) {
            ChaosModal.notice({ title: "Dashboard not saved", subtitle: result });
            return false;
        }

        this.render();
        this.update();

        return true;

    },


    newId() {

        return `traffic-${Date.now().toString(36)}${Math.floor(Math.random() * 1000)}`;

    },


    // ------------------------------------------------------------
    // Widgets on the page
    // ------------------------------------------------------------

    render() {

        this.shown.forEach(item => item.graph?.destroy());
        this.shown = [];

        dashboardGrid.replaceChildren();

        for (const widget of this.layout) {

            if (widget.hidden) continue;

            const template = document.getElementById(this.TEMPLATES[widget.type]);

            if (!template) continue;

            const element = template.content.firstElementChild.cloneNode(true);

            element.classList.add("widget");
            element.dataset.width = widget.width;
            element.dataset.widget = widget.id;

            dashboardGrid.appendChild(element);

            const item = { widget, element };

            if (widget.type === "traffic") this.setUpGraph(item);
            if (widget.type === "status") this.setUpStatus(item);

            this.shown.push(item);

        }

        // Empty until the first answer, filled every second.
        this.interfaceKey = null;

        // A new or re-rendered widget shows the last answer at once.
        if (this.lastStatus) this.renderStatus(this.lastStatus);
        if (this.lastUsage) this.renderUsage(this.lastUsage);

        this.updateStatus();
        this.updateUsage();

    },


    setUpGraph(item) {

        const { widget, element } = item;

        item.select = element.querySelector(".graph-select select");

        item.graph = new this.Graph(
            element.querySelector(".graph-area svg"),
            element.querySelector(".graph-tooltip")
        );

        item.graph.svg.setAttribute("aria-label",
            `Download and upload on ${widget.interface || "the WAN"} over the last 60 seconds`);

        ChaosSelect.enhance(item.select);

        item.select.onchange = () => {

            const layout = this.layout.map(w =>
                w.id === widget.id ? { ...w, interface: item.select.value } : w);

            this.changeLayout(layout);

        };

        element.querySelector(".widget-remove").onclick = () => {

            this.changeLayout(this.layout.filter(w => w.id !== widget.id));

        };

    },


    /** Interface choices for a graph: the WAN first, then the rest. */
    interfaceOptions(current = "") {

        const options = [{
            value: "",
            label: `WAN${this.wanInterface ? ` (${this.wanInterface})` : ""}`
        }];

        this.interfaces
            .filter(row => !row.wan)
            .forEach(row => options.push({ value: row.interface, label: `${row.interface} (${row.kind})` }));

        // A saved interface that is gone (unplugged, VPN down) stays
        // selectable, so the graph keeps its place.
        if (current && !options.some(o => o.value === current)) {
            options.push({ value: current, label: `${current} (not present)` });
        }

        return options;

    },


    fillSelect(select, options, value) {

        select.replaceChildren(...options.map(o => {
            const option = document.createElement("option");
            option.value = o.value;
            option.textContent = o.label;
            return option;
        }));

        select.value = value;

        ChaosSelect.refresh(select);

    },


    async update() {

        const graphs = [...new Set(
            this.shown.filter(item => item.graph).map(item => item.widget.interface)
        )];

        const query = graphs.map(encodeURIComponent).join(",");

        let data;

        try {

            const res = await fetch(`/api/dashboard?graphs=${query}`);

            if (res.status === 401) {
                location.href = "/login";
                return;
            }

            data = await res.json();

        } catch {

            return;

        }

        // The page was left while the request ran.
        if (!this.run) return;

        const values = {
            ...data,
            download: Header.formatRate(data.download),
            upload: Header.formatRate(data.upload)
        };

        dashboardGrid.querySelectorAll("[data-field]").forEach(field => {
            if (field.dataset.field in values && field.dataset.field !== "interfaces") {
                field.textContent = values[field.dataset.field];
            }
        });

        this.wanInterface = data.interface;
        this.interfaces = data.interfaces || [];

        // Rebuild the graph selects only when the interfaces change:
        // refreshing would close a dropdown that is open.
        const key = `${data.interface}|${this.interfaces.map(r => `${r.interface}:${r.kind}`).join(",")}`;
        const changed = key !== this.interfaceKey;

        this.interfaceKey = key;

        this.shown.forEach(item => {

            if (!item.graph) return;

            if (changed) {
                this.fillSelect(item.select, this.interfaceOptions(item.widget.interface), item.widget.interface);
            }

            item.graph.history = data.histories?.[item.widget.interface] || [];
            item.graph.draw();

        });

        this.renderInterfaces();

    },


    /** The table of every interface; a row opens its graph. */
    renderInterfaces() {

        const body = dashboardGrid.querySelector('[data-field="interfaces"]');

        if (!body) return;

        const graphed = new Set(
            this.shown.filter(item => item.graph).map(item => item.widget.interface || this.wanInterface)
        );

        const rows = this.interfaces.map(row => {

            const tr = document.createElement("tr");

            tr.className = "interface-row";
            tr.title = graphed.has(row.interface) ? "Go to its graph" : "Add a graph for it";
            tr.setAttribute("aria-selected", String(graphed.has(row.interface)));

            // Name with its kind (WAN, Wi-Fi, ...) underneath: fits narrow widgets.
            const name = document.createElement("td");
            const kind = document.createElement("small");

            name.className = "interface-name-cell";
            name.textContent = row.interface;
            kind.textContent = row.kind;
            name.append(" ", kind);
            tr.appendChild(name);

            [row.rx, row.tx].forEach(rate => {
                const td = document.createElement("td");
                td.className = "num";
                td.textContent = Header.formatRate(rate);
                tr.appendChild(td);
            });

            tr.addEventListener("click", () => this.openGraph(row));

            return tr;
        });

        if (!rows.length) {
            const tr = document.createElement("tr");
            tr.className = "empty-row";
            const td = document.createElement("td");
            td.colSpan = 3;
            td.textContent = "No interfaces measured yet.";
            tr.appendChild(td);
            rows.push(tr);
        }

        body.replaceChildren(...rows);

    },


    /** Scrolls to an interface's graph, or adds one after the others. */
    async openGraph(row) {

        const value = row.wan ? "" : row.interface;

        const existing = this.shown.find(item =>
            item.graph && (item.widget.interface || this.wanInterface) === row.interface);

        if (existing) {
            this.highlight(existing.element);
            return;
        }

        const widget = { id: this.newId(), type: "traffic", width: 6, hidden: false, interface: value };

        const lastGraph = this.layout.map(w => w.type).lastIndexOf("traffic");
        const layout = [...this.layout];

        layout.splice(lastGraph + 1, 0, widget);

        if (await this.changeLayout(layout)) {
            const added = this.shown.find(item => item.widget.id === widget.id);
            if (added) this.highlight(added.element);
        }

    },


    highlight(element) {

        element.scrollIntoView({ block: "nearest", behavior: "smooth" });

        element.classList.remove("widget-flash");
        void element.offsetWidth;
        element.classList.add("widget-flash");

    },


    // ------------------------------------------------------------
    // Internet check
    // ------------------------------------------------------------

    setUpStatus(item) {

        item.element.querySelector(".status-recheck").onclick = () => this.updateStatus(true);

    },


    statusWidget() {

        return dashboardGrid.querySelector(".dashboard-status");

    },


    async updateStatus(byHand = false) {

        const widget = this.statusWidget();

        if (!widget || this.statusRunning) return;

        this.statusRunning = true;

        const button = widget.querySelector(".status-recheck");

        if (byHand) {
            button.disabled = true;
            widget.querySelector(".status-result").textContent = "";
        }

        try {

            const res = await fetch("/api/internet/check");

            if (res.status === 401) {
                location.href = "/login";
                return;
            }

            if (res.ok) {
                this.lastStatus = await res.json();
                if (this.run) this.renderStatus(this.lastStatus);
            }

        } catch {

            // The next round tries again.

        } finally {

            this.statusRunning = false;
            button.disabled = false;

        }

    },


    renderStatus(data) {

        const widget = this.statusWidget();

        if (!widget) return;

        const verdict = widget.querySelector(".status-verdict");

        verdict.className = `status status-verdict ${data.online ? "online" : "offline"}`;
        verdict.textContent = data.online ? "Online" : "Problem";

        let message = data.message;

        if (data.auto_restart) {
            const at = new Date(data.auto_restart * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
            message += ` The modem's data link got stuck and was restarted automatically at ${at}.`;
        }

        widget.querySelector(".status-message").textContent = message;

        // The headline's button: a fix the router runs, or a page link.
        const actionBox = widget.querySelector(".status-action");
        const action = data.action;

        actionBox.replaceChildren();
        actionBox.classList.toggle("hidden", !action);

        if (action?.kind === "link") {

            const a = document.createElement("a");
            a.className = "secondary-btn";
            a.href = action.href;
            a.textContent = action.label;
            actionBox.appendChild(a);

        } else if (action?.kind === "fix") {

            const button = document.createElement("button");
            button.type = "button";
            button.className = "save-btn";
            button.textContent = action.label;
            button.onclick = () => this.runFix(action, button, widget.querySelector(".status-result"));
            actionBox.appendChild(button);

        }

        // Opens by itself when a problem shows up, never closes a list
        // that was opened by hand.
        const details = widget.querySelector(".status-details");

        if (!data.online && details.dataset.online !== "false") details.open = true;

        details.dataset.online = String(data.online);

        const list = widget.querySelector(".status-steps");

        list.replaceChildren(...data.steps.map(step => {

            const li = document.createElement("li");
            li.className = `status-step ${step.ok === true ? "ok" : step.ok === false ? "bad" : "skipped"}`;

            const icon = document.createElement("span");
            icon.className = "cs-icon";
            icon.setAttribute("aria-hidden", "true");
            icon.style.setProperty("--icon", `url(${step.ok === false ? list.dataset.iconBad : list.dataset.iconOk})`);

            const text = document.createElement("div");
            const label = document.createElement("strong");
            const detail = document.createElement("p");

            label.textContent = step.label;
            detail.textContent = step.detail;

            text.append(label, detail);
            li.append(icon, text);

            // Read out as "Modem: OK, Connected to ..." by screen readers.
            li.setAttribute("aria-label", `${step.label}: ${step.ok === false ? "problem" : "OK"}. ${step.detail}`);

            return li;

        }));

    },


    /** Runs a fix; its outcome goes into `result` (a status line). */
    async runFix(action, button, result) {

        if (action.fix === "restart_modem") {
            const go = await ChaosModal.confirm({
                title: "Restart the modem?",
                subtitle: "Mobile data is gone for about a minute while it restarts.",
                confirmText: "Restart modem"
            });
            if (!go) return;
        }

        if (action.fix === "resume") {
            const go = await ChaosModal.confirm({
                title: "Go over the data limit?",
                subtitle: "Devices get the internet again until the next billing period. Your carrier may charge extra or slow the connection down.",
                confirmText: "Resume anyway"
            });
            if (!go) return;
        }

        button.disabled = true;
        result.textContent = "Working...";

        try {

            const res = await fetch("/api/internet/fix", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ fix: action.fix })
            });

            const data = await res.json().catch(() => ({ message: "Unexpected server response." }));

            result.textContent = data.success ? `✓ ${data.message}` : data.message;

        } catch {

            result.textContent = "The router did not answer. Try again.";

        }

        button.disabled = false;

        // A restart takes a while: check again now and once it had time.
        this.updateStatus();
        this.updateUsage();

        if (action.fix === "restart_modem" || action.fix === "mobile_on") {
            setTimeout(() => this.run && this.updateStatus(), 45000);
        }

    },


    // ------------------------------------------------------------
    // Mobile data used
    // ------------------------------------------------------------

    /** Bytes as the carriers count them: 1 GB = 1000 MB. */
    formatBytes(bytes) {

        if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(bytes >= 1e11 ? 0 : 2)} GB`;
        if (bytes >= 1e6) return `${Math.round(bytes / 1e6)} MB`;
        if (bytes >= 1e3) return `${Math.round(bytes / 1e3)} kB`;

        return `${bytes} B`;

    },


    formatDate(iso) {

        return new Date(`${iso}T00:00:00`).toLocaleDateString([], { day: "numeric", month: "short" });

    },


    async updateUsage() {

        if (!dashboardGrid.querySelector(".dashboard-usage")) return;

        try {

            const res = await fetch("/api/data-usage");

            if (res.ok) {
                this.lastUsage = await res.json();
                if (this.run) this.renderUsage(this.lastUsage);
            }

        } catch {

            // The next round tries again.

        }

    },


    renderUsage(data) {

        const widget = dashboardGrid.querySelector(".dashboard-usage");

        if (!widget) return;

        const bar = widget.querySelector(".usage-bar");
        const fill = widget.querySelector(".usage-bar-fill");

        widget.querySelector(".usage-used").textContent = this.formatBytes(data.used);

        widget.querySelector(".usage-limit").textContent = data.limit
            ? `of ${this.formatBytes(data.limit)} · ${Math.round(data.percent)}%`
            : "this period";

        bar.classList.toggle("hidden", !data.limit);
        bar.classList.toggle("warning", data.level === "warning");
        bar.classList.toggle("limit", data.level === "limit" || data.level === "blocked");

        const percent = Math.min(100, data.percent || 0);

        fill.style.width = `${percent}%`;
        bar.setAttribute("aria-valuenow", String(Math.round(percent)));

        const parts = [];

        if (!data.counting && !data.used) {
            parts.push("Counts mobile data while a modem is connected.");
        } else {
            parts.push(`Today ${this.formatBytes(data.today)}.`);
        }

        parts.push(`Starts over on ${this.formatDate(data.period_end)} (${data.days_left} day${data.days_left === 1 ? "" : "s"}).`);

        if (data.level === "blocked") {
            parts.unshift("Limit reached: devices on your network are offline.");
        } else if (data.level === "limit") {
            parts.unshift(data.resumed ? "Over the limit (resumed by hand)." : "Limit reached.");
        } else if (data.limit && data.projected > data.limit) {
            parts.push(`At this rate about ${this.formatBytes(data.projected)} by then.`);
        }

        widget.querySelector(".usage-detail").textContent = parts.join(" ");

        widget.querySelector(".usage-action").classList.toggle("hidden", data.level !== "blocked");

        widget.querySelector(".usage-resume").onclick = event =>
            this.runFix({ fix: "resume" }, event.currentTarget, widget.querySelector(".usage-result"));

    },


    // ------------------------------------------------------------
    // Editor
    // ------------------------------------------------------------

    openEditor() {

        this.draft = this.layout.map(w => ({ ...w }));

        // Every single widget is listed, so a hidden one can come back.
        for (const type of ["summary", "status", "usage", "interfaces", "system"]) {
            if (!this.draft.some(w => w.type === type)) {
                const builtin = this.defaultLayout.find(w => w.type === type);
                this.draft.push({ ...(builtin || { id: type, type, width: 12 }), hidden: true });
            }
        }

        layoutError.classList.add("hidden");

        this.renderEditor();

        layoutModal.classList.remove("hidden");

        saveLayoutBtn.focus();

    },


    closeEditor() {

        layoutModal.classList.add("hidden");

        this.draft = null;

    },


    renderEditor() {

        const template = document.getElementById("layoutRowTemplate");

        layoutList.replaceChildren(...this.draft.map((widget, index) => {

            const row = template.content.firstElementChild.cloneNode(true);

            const show = row.querySelector(".layout-show input");

            show.checked = !widget.hidden;
            show.onchange = () => { widget.hidden = !show.checked; this.renderEditor(); };

            row.classList.toggle("is-hidden", widget.hidden);

            row.querySelector(".layout-name").textContent = this.NAMES[widget.type];

            const interfaceBox = row.querySelector(".layout-interface");

            if (widget.type === "traffic") {

                const select = interfaceBox.querySelector("select");

                ChaosSelect.enhance(select);
                this.fillSelect(select, this.interfaceOptions(widget.interface), widget.interface);

                select.onchange = () => { widget.interface = select.value; };

            } else {

                interfaceBox.remove();

            }

            const width = row.querySelector(".layout-width select");
            const widths = this.WIDTHS[widget.type].includes(widget.width)
                ? this.WIDTHS[widget.type]
                : [...this.WIDTHS[widget.type], widget.width].sort((a, b) => a - b);

            ChaosSelect.enhance(width);
            this.fillSelect(width, widths.map(w => ({ value: String(w), label: this.WIDTH_LABELS[w] })), String(widget.width));

            width.onchange = () => { widget.width = Number(width.value); };

            const move = step => {
                const [moved] = this.draft.splice(index, 1);
                this.draft.splice(index + step, 0, moved);
                this.renderEditor();
                layoutList.children[index + step]
                    ?.querySelector(step < 0 ? ".layout-up" : ".layout-down")
                    ?.focus();
            };

            const up = row.querySelector(".layout-up");
            const down = row.querySelector(".layout-down");

            up.disabled = index === 0;
            down.disabled = index === this.draft.length - 1;

            up.onclick = () => move(-1);
            down.onclick = () => move(1);

            const remove = row.querySelector(".layout-remove");

            if (widget.type === "traffic") {
                remove.onclick = () => { this.draft.splice(index, 1); this.renderEditor(); };
            } else {
                // Built-in widgets are hidden, not removed.
                remove.classList.add("layout-placeholder");
                remove.disabled = true;
            }

            return row;

        }));

    },


    addDraftGraph() {

        const graphed = new Set(this.draft.filter(w => w.type === "traffic").map(w => w.interface));

        const next = this.interfaces.find(row => !row.wan && !graphed.has(row.interface));

        this.draft.push({
            id: this.newId(),
            type: "traffic",
            width: 6,
            hidden: false,
            interface: next ? next.interface : ""
        });

        this.renderEditor();

        layoutList.lastElementChild?.scrollIntoView({ block: "nearest" });

    },


    async saveEditor() {

        if (this.draft.every(w => w.hidden)) {
            layoutError.textContent = "Show at least one widget.";
            layoutError.classList.remove("hidden");
            return;
        }

        saveLayoutBtn.disabled = true;

        const result = await this.saveLayout(this.draft);

        saveLayoutBtn.disabled = false;

        if (result !== true) {
            layoutError.textContent = result;
            layoutError.classList.remove("hidden");
            return;
        }

        this.closeEditor();
        this.render();
        this.update();

    },


    // ------------------------------------------------------------
    // Lifecycle
    // ------------------------------------------------------------

    async init() {

        // Leaving the page while the layout loads ends this run
        // (destroy() clears it): nothing renders into a page that's gone.
        const run = this.run = {};

        editDashboardBtn.onclick = () => this.openEditor();
        addGraphBtn.onclick = () => this.addDraftGraph();
        saveLayoutBtn.onclick = () => this.saveEditor();
        cancelLayoutBtn.onclick = () => this.closeEditor();
        closeLayoutModal.onclick = () => this.closeEditor();

        resetLayoutBtn.onclick = () => {
            this.draft = this.defaultLayout.map(w => ({ ...w }));
            layoutError.classList.add("hidden");
            this.renderEditor();
        };

        layoutModal.onclick = e => {
            if (e.target === layoutModal) this.closeEditor();
        };

        this.onKey = e => {
            if (e.key === "Escape" && this.draft) this.closeEditor();
        };

        document.addEventListener("keydown", this.onKey);

        // The graphs use the theme's colors: redraw at once on a switch.
        this.onTheme = () => this.shown.forEach(item => item.graph?.draw());
        window.addEventListener("themechange", this.onTheme);

        await this.loadLayout();

        if (this.run !== run) return;

        this.render();

        this.update();
        this.timer = Poll.every(1000, () => this.update());

        // Slower: the check pings, the count changes every 30 seconds.
        this.statusTimer = Poll.every(30000, () => this.updateStatus());
        this.usageTimer = Poll.every(30000, () => this.updateUsage());

    },

    destroy() {

        this.run = null;

        Poll.stop(this.timer);
        Poll.stop(this.statusTimer);
        Poll.stop(this.usageTimer);

        this.shown.forEach(item => item.graph?.destroy());
        this.shown = [];

        document.removeEventListener("keydown", this.onKey);
        window.removeEventListener("themechange", this.onTheme);

        this.draft = null;

    },


    // ------------------------------------------------------------
    // One traffic graph
    // ------------------------------------------------------------

    Graph: class {

        // Plot margins inside the SVG, in pixels (room for axis labels).
        static MARGIN = { top: 10, right: 12, bottom: 26, left: 72 };

        static WINDOW_SECONDS = 60;

        static SVG_NS = "http://www.w3.org/2000/svg";

        constructor(svg, tooltip) {

            this.svg = svg;
            this.tooltip = tooltip;

            // Samples from /api/dashboard: [{ t, rx, tx }] with rates in Mbps.
            this.history = [];

            // Index of the sample under the mouse, or null.
            this.hoverIndex = null;

            svg.addEventListener("pointermove", e => this.onPointerMove(e));
            svg.addEventListener("pointerleave", () => this.onPointerLeave());

            // Redraw at the new pixel size when the window changes.
            this.resizeObserver = new ResizeObserver(() => this.draw());
            this.resizeObserver.observe(svg);

        }

        destroy() {

            this.resizeObserver.disconnect();

        }

        // Chart colors from the design tokens (chardsoft.css), so the
        // graph follows the light and dark theme: download in the accent
        // color, upload neutral, grid lines like the hairlines.
        get colors() {

            const css = getComputedStyle(document.documentElement);
            const token = name => css.getPropertyValue(name).trim();

            return {
                rx: token("--color-accent"),
                tx: token("--color-text-muted"),
                grid: token("--color-border"),
                axis: token("--color-text-muted"),
                surface: token("--color-surface")
            };

        }

        /** A round axis maximum (1, 2, 2.5 or 5 x 10^n) above the data. */
        niceMax(value) {

            const target = Math.max(value * 1.15, 0.01);
            const power = 10 ** Math.floor(Math.log10(target));

            for (const step of [1, 2, 2.5, 5, 10]) {
                if (step * power >= target) return step * power;
            }

            return 10 * power;

        }

        /** Axis labels: round values without trailing zeros, e.g. "25 Mbps". */
        formatAxis(mbps) {

            const trim = n => String(Number(n.toFixed(2)));

            if (mbps === 0) return "0";
            if (mbps >= 1000) return `${trim(mbps / 1000)} Gbps`;
            if (mbps >= 1) return `${trim(mbps)} Mbps`;

            return `${trim(mbps * 1000)} kbps`;

        }

        el(name, attrs = {}, text = null) {

            const node = document.createElementNS(this.constructor.SVG_NS, name);

            for (const [key, value] of Object.entries(attrs)) {
                node.setAttribute(key, value);
            }

            if (text !== null) node.textContent = text;

            return node;

        }

        /** Pixel geometry shared by drawing and hovering. */
        layout() {

            const { MARGIN: m, WINDOW_SECONDS: span } = this.constructor;

            const width = this.svg.clientWidth;
            const height = this.svg.clientHeight;

            const points = this.history;
            const end = points.length ? points[points.length - 1].t : Date.now() / 1000;
            const start = end - span;

            const peak = Math.max(0, ...points.flatMap(p => [p.rx, p.tx]));
            const max = this.niceMax(peak);

            const plotW = Math.max(1, width - m.left - m.right);
            const plotH = Math.max(1, height - m.top - m.bottom);

            return {
                width, height, m, plotW, plotH, start, end, max, points,
                x: t => m.left + ((t - start) / span) * plotW,
                y: v => m.top + plotH - (v / max) * plotH
            };

        }

        draw() {

            const svg = this.svg;
            const g = this.layout();
            const colors = this.colors;

            if (g.width < 50 || g.height < 50) return;

            svg.setAttribute("viewBox", `0 0 ${g.width} ${g.height}`);
            svg.replaceChildren();

            // Horizontal grid lines with rate labels.
            const ticks = 4;

            for (let i = 0; i <= ticks; i++) {

                const value = (g.max / ticks) * i;
                const y = g.y(value);

                svg.appendChild(this.el("line", {
                    x1: g.m.left, x2: g.width - g.m.right, y1: y, y2: y,
                    stroke: colors.grid
                }));

                svg.appendChild(this.el("text", {
                    x: g.m.left - 10, y: y + 4,
                    "text-anchor": "end", class: "graph-label"
                }, this.formatAxis(value)));

            }

            // Time labels: seconds before the latest sample. Narrow
            // graphs keep only every other one.
            const marks = g.plotW < 300 ? [60, 30, 0] : [60, 45, 30, 15, 0];

            for (const ago of marks) {

                const x = g.x(g.end - ago);

                svg.appendChild(this.el("line", {
                    x1: x, x2: x, y1: g.m.top, y2: g.m.top + g.plotH,
                    stroke: colors.grid
                }));

                svg.appendChild(this.el("text", {
                    x: x, y: g.height - 6,
                    "text-anchor": ago === 60 ? "start" : ago === 0 ? "end" : "middle",
                    class: "graph-label"
                }, ago === 0 ? "now" : `-${ago}s`));

            }

            if (!g.points.length) {

                svg.appendChild(this.el("text", {
                    x: g.m.left + g.plotW / 2, y: g.m.top + g.plotH / 2,
                    "text-anchor": "middle", class: "graph-label"
                }, "No data for this interface yet"));

                this.tooltip.classList.add("hidden");

                return;
            }

            // Area + line per direction.
            const base = g.y(0);

            for (const key of ["rx", "tx"]) {

                const coords = g.points.map(p => `${g.x(p.t).toFixed(1)},${g.y(p[key]).toFixed(1)}`);
                const first = g.x(g.points[0].t).toFixed(1);
                const last = g.x(g.points[g.points.length - 1].t).toFixed(1);

                svg.appendChild(this.el("polygon", {
                    points: `${first},${base} ${coords.join(" ")} ${last},${base}`,
                    fill: colors[key],
                    "fill-opacity": key === "rx" ? .12 : .06
                }));

                svg.appendChild(this.el("polyline", {
                    points: coords.join(" "),
                    fill: "none",
                    stroke: colors[key],
                    "stroke-width": 1.5,
                    "stroke-linejoin": "round",
                    "stroke-linecap": "round"
                }));

            }

            this.drawHover(g, colors);

        }

        drawHover(g, colors) {

            const sample = this.hoverIndex !== null ? g.points[this.hoverIndex] : null;
            const tooltip = this.tooltip;

            if (!sample) {
                tooltip.classList.add("hidden");
                return;
            }

            const x = g.x(sample.t);

            this.svg.appendChild(this.el("line", {
                x1: x, x2: x, y1: g.m.top, y2: g.m.top + g.plotH,
                stroke: colors.axis, "stroke-dasharray": "3 3"
            }));

            for (const key of ["rx", "tx"]) {

                this.svg.appendChild(this.el("circle", {
                    cx: x, cy: g.y(sample[key]), r: 4.5,
                    fill: colors[key], stroke: colors.surface, "stroke-width": 2
                }));

            }

            const ago = Math.round(g.end - sample.t);
            const clock = new Date(sample.t * 1000).toLocaleTimeString();

            const line = (swatch, label, value) => {
                const row = document.createElement("div");
                const icon = document.createElement("i");
                const strong = document.createElement("strong");
                icon.className = `legend-swatch ${swatch}`;
                strong.textContent = Header.formatRate(value);
                row.append(icon, label, strong);
                return row;
            };

            const time = document.createElement("div");
            time.className = "graph-tooltip-time";
            time.textContent = `${clock} · ${ago === 0 ? "now" : `${ago}s ago`}`;

            tooltip.replaceChildren(
                time,
                line("download", "Download", sample.rx),
                line("upload", "Upload", sample.tx)
            );

            tooltip.classList.remove("hidden");

            // Next to the cursor line, flipped near the right edge.
            const box = tooltip.getBoundingClientRect();
            const left = x + 14 + box.width > g.width ? x - 14 - box.width : x + 14;

            tooltip.style.left = `${Math.max(0, left)}px`;
            tooltip.style.top = `${g.m.top + 8}px`;

        }

        onPointerMove(event) {

            const g = this.layout();

            if (!g.points.length) return;

            const rect = this.svg.getBoundingClientRect();
            const mouseX = event.clientX - rect.left;

            if (mouseX < g.m.left - 8 || mouseX > g.width - g.m.right + 8) {
                this.onPointerLeave();
                return;
            }

            // Nearest sample in time.
            let best = 0;

            g.points.forEach((p, i) => {
                if (Math.abs(g.x(p.t) - mouseX) < Math.abs(g.x(g.points[best].t) - mouseX)) best = i;
            });

            if (best !== this.hoverIndex) {
                this.hoverIndex = best;
                this.draw();
            }

        }

        onPointerLeave() {

            if (this.hoverIndex === null) return;

            this.hoverIndex = null;
            this.draw();

        }

    }

};
