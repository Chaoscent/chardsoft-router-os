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

    // Edit mode: the working copy (hidden widgets included), saved only
    // with "Done"; the list view edits the same copy.
    editing: false,
    draft: null,
    listSnapshot: null,

    // The drag in progress: { item, ghost, pointerId, ... }.
    drag: null,

    SINGLE_TYPES: ["summary", "status", "usage", "interfaces", "system", "gnss"],

    NAMES: {
        summary: "Overview (clients, download, upload)",
        status: "Internet check",
        usage: "Mobile data used",
        traffic: "Traffic graph",
        interfaces: "Interfaces",
        system: "System (CPU, RAM, uptime)",
        gnss: "Location (modem GNSS)"
    },

    TEMPLATES: {
        summary: "widgetSummary",
        status: "widgetStatus",
        usage: "widgetUsage",
        traffic: "widgetTraffic",
        interfaces: "widgetInterfaces",
        system: "widgetSystem",
        gnss: "widgetGnss"
    },

    // Columns out of 12 that make sense per widget.
    WIDTHS: {
        summary: [6, 8, 12],
        status: [4, 6, 8, 12],
        usage: [3, 4, 6, 8, 12],
        traffic: [4, 6, 8, 9, 12],
        interfaces: [3, 4, 6, 8, 12],
        system: [6, 8, 12],
        gnss: [3, 4, 6, 8, 12]
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

        for (const widget of this.editing ? this.draft : this.layout) {

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
            if (this.editing) this.addEditChrome(item);

            this.shown.push(item);

        }

        // Empty until the first answer, filled every second.
        this.interfaceKey = null;

        // A new or re-rendered widget shows the last answer at once.
        if (this.lastStatus) this.renderStatus(this.lastStatus);
        if (this.lastUsage) this.renderUsage(this.lastUsage);
        if (this.lastGnss) this.renderGnss(this.lastGnss);

        this.updateStatus();
        this.updateUsage();
        this.updateGnss();

    },


    async updateGnss() {

        if (!dashboardGrid.querySelector(".dashboard-gnss")) return;

        try {

            const res = await fetch("/api/gnss");

            if (res.ok) {
                this.lastGnss = await res.json();
                if (this.run) this.renderGnss(this.lastGnss);
            }

        } catch {

            // The next round tries again.

        }

    },


    renderGnss(data) {

        const widget = dashboardGrid.querySelector(".dashboard-gnss");

        if (!widget) return;

        const levels = {
            fix: ["online", "Position"], searching: ["warning", "Searching"], starting: ["warning", "Starting"],
            off: ["muted", "Off"], unsupported: ["muted", "Not available"], no_modem: ["muted", "No modem"]
        };

        const [badge, label] = levels[data.level] || ["muted", data.level];
        const pill = widget.querySelector(".gnss-pill");
        const p = data.position;

        pill.className = `status gnss-pill ${badge}`;
        pill.textContent = label;

        widget.querySelector(".gnss-message").textContent = data.message;

        const position = widget.querySelector(".gnss-position");

        position.classList.toggle("hidden", !p);
        position.textContent = p ? `${p.latitude.toFixed(5)}, ${p.longitude.toFixed(5)}` : "";

        const parts = [];

        if (p?.altitude != null) parts.push(`${Math.round(p.altitude)} m high`);
        if (p?.speed_kmh >= 1) parts.push(`${Math.round(p.speed_kmh)} km/h`);
        if (data.in_view) parts.push(`${data.heard} of ${data.in_view} satellites heard`);

        widget.querySelector(".gnss-detail").textContent = parts.join(" · ");

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

            // Edit mode: part of the working copy, saved with "Done".
            if (this.editing) {
                widget.interface = item.select.value;
                item.element.querySelector(".widget-edit-name").textContent = this.widgetName(widget);
                this.update();
                return;
            }

            const layout = this.layout.map(w =>
                w.id === widget.id ? { ...w, interface: item.select.value } : w);

            this.changeLayout(layout);

        };

        element.querySelector(".widget-remove").onclick = () => {

            if (this.editing) {
                this.hideWidget(widget);
                return;
            }

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

        this.renderUsageDevices(widget.querySelector(".usage-devices"), data);

        widget.querySelector(".usage-action").classList.toggle("hidden", data.level !== "blocked");

        widget.querySelector(".usage-resume").onclick = event =>
            this.runFix({ fix: "resume" }, event.currentTarget, widget.querySelector(".usage-result"));

    },


    /** The devices that used the most, and the router's own share. */
    renderUsageDevices(list, data) {

        const by = data.by_device;

        if (!by || !by.devices.length) {
            list.classList.add("hidden");
            return;
        }

        const top = Math.max(...by.devices.map(d => d.bytes), by.other || 0, 1);

        const row = (name, bytes, href, muted) => {

            const item = document.createElement("li");

            if (muted) item.className = "muted";

            item.innerHTML = `
                <span class="usage-device-name"></span>
                <span class="usage-device-bytes">${this.formatBytes(bytes)}</span>
                <span class="usage-device-bar"><span style="width:${Math.max(2, bytes / top * 100)}%"></span></span>
            `;

            const label = item.querySelector(".usage-device-name");

            if (href) {
                const link = document.createElement("a");
                link.href = href;
                link.textContent = name;
                label.appendChild(link);
            } else {
                label.textContent = name;
            }

            return item;

        };

        const rows = by.devices.map(d => row(d.name, d.bytes, "#/clients"));

        if (by.more) {
            const rest = document.createElement("li");
            rest.className = "muted usage-device-more";
            rest.innerHTML = `<a href="#/clients">${by.more} more device${by.more === 1 ? "" : "s"}</a>`;
            rows.push(rest);
        }

        if (by.other > 0 && data.used) {
            rows.push(row("The router itself and overhead", by.other, null, true));
        }

        list.replaceChildren(...rows);
        list.classList.remove("hidden");

    },


    // ------------------------------------------------------------
    // Edit mode: the layout changes on the dashboard itself
    // ------------------------------------------------------------

    /** A copy of a layout with every single widget in it, so a hidden one can come back. */
    withHiddenBuiltins(layout) {

        const draft = layout.map(w => ({ ...w }));

        for (const type of this.SINGLE_TYPES) {
            if (!draft.some(w => w.type === type)) {
                const builtin = this.defaultLayout.find(w => w.type === type);
                draft.push({ ...(builtin || { id: type, type, width: type === "gnss" ? 6 : 12 }), hidden: true });
            }
        }

        return draft;

    },


    widgetName(widget) {

        if (widget.type !== "traffic") return this.NAMES[widget.type];

        return `Traffic: ${widget.interface || `WAN${this.wanInterface ? ` (${this.wanInterface})` : ""}`}`;

    },


    allowedWidths(widget) {

        const widths = this.WIDTHS[widget.type];

        return widths.includes(widget.width) ? widths : [...widths, widget.width].sort((a, b) => a - b);

    },


    startEditing() {

        this.draft = this.withHiddenBuiltins(this.layout);
        this.editing = true;

        this.renderEditing();

        doneEditBtn.focus();

    },


    /** The page in or out of edit mode, the widgets from the working copy. */
    renderEditing() {

        dashboardGrid.closest(".dashboard").classList.toggle("is-editing", this.editing);

        editDashboardBtn.parentElement.classList.toggle("hidden", this.editing);
        dashboardEditActions.classList.toggle("hidden", !this.editing);
        dashboardTray.classList.toggle("hidden", !this.editing);

        if (this.editing) this.renderTray();

        this.render();
        this.update();

    },


    renderTray() {

        const icon = trayAddGraphBtn.querySelector(".cs-icon");

        dashboardTrayHidden.replaceChildren(...this.draft.filter(w => w.hidden).map(widget => {

            const button = document.createElement("button");

            button.type = "button";
            button.className = "secondary-btn";
            button.title = "Show it again";
            button.append(icon.cloneNode(true), this.NAMES[widget.type]);
            button.onclick = () => this.showWidget(widget);

            return button;

        }));

        // Widths follow the pointer only where the 12 columns are shown.
        dashboardTrayHint.textContent = this.canResize()
            ? "Drag a widget by its handle to move it, and its right edge to change its width. "
              + "With the keyboard: the arrow keys on the handle or on the edge."
            : "Drag a widget by its handle to move it (or the arrow keys on the handle). "
              + "This screen stacks the widgets; set their widths in the list view or on a wider screen.";

    },


    async finishEditing() {

        this.endDrag(true);

        if (this.draft.every(w => w.hidden)) {
            ChaosModal.notice({ title: "Nothing to show", subtitle: "Show at least one widget." });
            return;
        }

        doneEditBtn.disabled = true;

        const result = await this.saveLayout(this.draft);

        doneEditBtn.disabled = false;

        if (result !== true) {
            ChaosModal.notice({ title: "Dashboard not saved", subtitle: result });
            return;
        }

        this.stopEditing();

    },


    stopEditing() {

        this.endDrag(true);

        this.editing = false;
        this.draft = null;

        this.renderEditing();

        editDashboardBtn.focus();

    },


    resetDraft() {

        this.draft = this.withHiddenBuiltins(this.defaultLayout);

        this.renderEditing();

        this.announce("The default layout. Done saves it.");

    },


    showWidget(widget) {

        // Back at the end, where it is easy to find.
        this.draft = [...this.draft.filter(w => w !== widget), { ...widget, hidden: false }];

        this.renderEditing();

        const shown = this.shown.find(item => item.widget.id === widget.id);

        if (shown) {
            this.highlight(shown.element);
            shown.grip?.focus({ preventScroll: true });
        }

        this.announce(`${this.widgetName(widget)} is shown at the end.`);

    },


    hideWidget(widget) {

        // Graphs are removed; built-in widgets wait in the tray.
        this.draft = widget.type === "traffic"
            ? this.draft.filter(w => w !== widget)
            : this.draft.map(w => w === widget ? { ...w, hidden: true } : w);

        this.renderEditing();

        this.announce(`${this.widgetName(widget)} ${widget.type === "traffic" ? "removed" : "hidden"}.`);

    },


    /** Handle, name, width and hide button on a widget, and its right edge. */
    addEditChrome(item) {

        const { widget, element } = item;

        const chrome = document.getElementById("widgetEditChrome").content.cloneNode(true);

        const name = this.widgetName(widget);
        const verb = widget.type === "traffic" ? "Remove" : "Hide";

        item.grip = chrome.querySelector(".widget-grip");
        item.resize = chrome.querySelector(".widget-resize");
        item.widthLabel = chrome.querySelector(".widget-edit-width");

        chrome.querySelector(".widget-edit-name").textContent = name;

        item.grip.setAttribute("aria-label", `Move ${name}`);

        const hide = chrome.querySelector(".widget-edit-hide");

        hide.title = `${verb} this widget`;
        hide.setAttribute("aria-label", `${verb} ${name}`);
        hide.onclick = () => this.hideWidget(widget);

        item.grip.addEventListener("pointerdown", event => this.startDrag(event, item));
        item.grip.addEventListener("keydown", event => this.moveByKey(event, item));

        item.resize.setAttribute("aria-label", `Width of ${name}`);
        item.resize.addEventListener("pointerdown", event => this.startResize(event, item));
        item.resize.addEventListener("keydown", event => this.resizeByKey(event, item));

        // The content only shows the size: no link or button in it is
        // reached (by mouse or Tab), so nothing leaves the page unsaved.
        for (const child of element.children) child.inert = true;

        element.append(chrome);

        this.showWidth(item);

    },


    showWidth(item) {

        const widths = this.allowedWidths(item.widget);
        const label = this.WIDTH_LABELS[item.widget.width] || `${item.widget.width} of 12`;

        item.widthLabel.textContent = label;

        item.resize.setAttribute("aria-valuemin", String(widths[0]));
        item.resize.setAttribute("aria-valuemax", String(widths[widths.length - 1]));
        item.resize.setAttribute("aria-valuenow", String(item.widget.width));
        item.resize.setAttribute("aria-valuetext", label);

    },


    announce(text) {

        // Cleared first, so the same text is read again.
        dashboardAnnounce.textContent = "";
        requestAnimationFrame(() => { dashboardAnnounce.textContent = text; });

    },


    announceMove(item) {

        const elements = this.widgetElements();
        const place = elements.indexOf(item.element) + 1;

        this.announce(`${this.widgetName(item.widget)}: place ${place} of ${elements.length}.`);

    },


    widgetElements() {

        return [...dashboardGrid.children].filter(el => el.dataset.widget);

    },


    /** The working copy in the order on the page (hidden widgets after). */
    syncDraftOrder() {

        const byId = new Map(this.draft.map(w => [w.id, w]));
        const shown = this.widgetElements().map(el => byId.get(el.dataset.widget)).filter(Boolean);

        this.draft = [...shown, ...this.draft.filter(w => !shown.includes(w))];

    },


    /** Runs a change of the grid and slides the widgets to their new places. */
    reflow(change) {

        const elements = this.widgetElements();
        const before = new Map(elements.map(el => [el, el.getBoundingClientRect()]));

        change();

        if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;

        for (const el of elements) {

            const a = before.get(el);
            const b = el.getBoundingClientRect();
            const dx = a.left - b.left;
            const dy = a.top - b.top;

            if (dx || dy) {
                el.animate(
                    [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }],
                    { duration: 160, easing: "ease-out" }
                );
            }

        }

    },


    // Moving: the widget's place stays open (dashed) and follows the
    // pointer through the grid; a copy of it follows the pointer.

    startDrag(event, item) {

        if (event.button !== 0 || this.drag) return;

        event.preventDefault();

        const element = item.element;
        const rect = element.getBoundingClientRect();

        item.grip.setPointerCapture(event.pointerId);

        // The widget lifts only once the pointer moves: a click or a tap
        // on the handle changes nothing.
        const drag = this.drag = {
            item,
            ghost: null,
            rect,
            startX: event.clientX,
            startY: event.clientY,
            pointerId: event.pointerId,
            offsetX: event.clientX - rect.left,
            offsetY: event.clientY - rect.top,
            x: event.clientX,
            y: event.clientY,
            // Where it was: Escape puts it back.
            home: element.nextSibling,
            last: null,
            scroller: dashboardGrid.closest("main") || document.scrollingElement
        };

        drag.listeners = {
            pointermove: e => {
                if (e.pointerId !== drag.pointerId) return;
                drag.x = e.clientX;
                drag.y = e.clientY;
                this.dragMoved();
            },
            pointerup: e => { if (e.pointerId === drag.pointerId) this.endDrag(false); },
            pointercancel: e => { if (e.pointerId === drag.pointerId) this.endDrag(true); },
            lostpointercapture: e => { if (e.pointerId === drag.pointerId) this.endDrag(false); }
        };

        for (const [name, listener] of Object.entries(drag.listeners)) {
            item.grip.addEventListener(name, listener);
        }

        drag.frame = requestAnimationFrame(() => this.autoScroll());

    },


    dragMoved() {

        const drag = this.drag;

        if (!drag) return;

        if (!drag.ghost) {

            if (Math.hypot(drag.x - drag.startX, drag.y - drag.startY) < 5) return;

            const ghost = drag.ghost = drag.item.element.cloneNode(true);

            ghost.classList.add("widget-ghost");
            ghost.removeAttribute("data-widget");
            ghost.setAttribute("aria-hidden", "true");

            Object.assign(ghost.style, {
                width: `${drag.rect.width}px`,
                height: `${Math.min(drag.rect.height, 320)}px`
            });

            document.body.appendChild(ghost);

            drag.item.element.classList.add("is-dragging");

        }

        drag.ghost.style.left = `${drag.x - drag.offsetX}px`;
        drag.ghost.style.top = `${drag.y - drag.offsetY}px`;

        // After a move the widgets shift under the pointer: wait for a
        // real movement before the next one, so they never flicker.
        if (drag.last && Math.hypot(drag.x - drag.last.x, drag.y - drag.last.y) < 16) return;

        const element = drag.item.element;
        const others = this.widgetElements().filter(el => el !== element);

        if (!others.length) return;

        // The widget under the pointer, or the nearest one (in a gap).
        let target = null;
        let distance = Infinity;

        for (const other of others) {

            const r = other.getBoundingClientRect();
            const d = Math.hypot(
                Math.max(r.left - drag.x, 0, drag.x - r.right),
                Math.max(r.top - drag.y, 0, drag.y - r.bottom)
            );

            if (d < distance) {
                distance = d;
                target = other;
            }

        }

        if (distance > 80) return;

        const r = target.getBoundingClientRect();
        const grid = dashboardGrid.getBoundingClientRect();

        // A wide widget: above or below it; a narrow one: left or right.
        const after = r.width > grid.width * 0.7 || drag.y < r.top || drag.y > r.bottom
            ? drag.y > r.top + r.height / 2
            : drag.x > r.left + r.width / 2;

        const reference = after ? target.nextSibling : target;

        // Already there.
        if (reference === element || reference === element.nextSibling) return;

        this.reflow(() => dashboardGrid.insertBefore(element, reference));

        drag.last = { x: drag.x, y: drag.y };

    },


    /** Near the top or bottom edge the page scrolls while dragging. */
    autoScroll() {

        const drag = this.drag;

        if (!drag) return;

        if (!drag.ghost) {
            drag.frame = requestAnimationFrame(() => this.autoScroll());
            return;
        }

        const box = drag.scroller.getBoundingClientRect();
        const top = Math.max(box.top, 0);
        const bottom = Math.min(box.bottom, innerHeight);
        const edge = 70;

        let step = 0;

        if (drag.y < top + edge) step = -Math.ceil(16 * (1 - Math.max(0, drag.y - top) / edge));
        else if (drag.y > bottom - edge) step = Math.ceil(16 * (1 - Math.max(0, bottom - drag.y) / edge));

        if (step) {
            const before = drag.scroller.scrollTop;
            drag.scroller.scrollTop += step;
            // The widgets moved under a pointer that did not.
            if (drag.scroller.scrollTop !== before) this.dragMoved();
        }

        drag.frame = requestAnimationFrame(() => this.autoScroll());

    },


    endDrag(cancel) {

        const drag = this.drag;

        if (!drag) return;

        this.drag = null;

        cancelAnimationFrame(drag.frame);

        const { item } = drag;

        for (const [name, listener] of Object.entries(drag.listeners)) {
            item.grip.removeEventListener(name, listener);
        }

        if (item.grip.hasPointerCapture?.(drag.pointerId)) item.grip.releasePointerCapture(drag.pointerId);

        // Only a click: nothing moved.
        if (!drag.ghost) return;

        drag.ghost.remove();
        item.element.classList.remove("is-dragging");

        if (cancel && item.element.isConnected) {
            this.reflow(() => dashboardGrid.insertBefore(item.element, drag.home?.parentNode === dashboardGrid ? drag.home : null));
        }

        if (!this.draft) return;

        this.syncDraftOrder();

        if (!cancel) this.announceMove(item);

    },


    moveByKey(event, item) {

        const back = event.key === "ArrowUp" || event.key === "ArrowLeft";
        const forward = event.key === "ArrowDown" || event.key === "ArrowRight";

        if (!back && !forward) return;

        event.preventDefault();

        const element = item.element;
        const sibling = back ? element.previousElementSibling : element.nextElementSibling;

        if (!sibling?.dataset.widget) return;

        this.reflow(() => dashboardGrid.insertBefore(element, back ? sibling : sibling.nextSibling));

        this.syncDraftOrder();

        item.grip.focus();
        element.scrollIntoView({ block: "nearest" });

        this.announceMove(item);

    },


    // Width: the right edge follows the pointer and snaps to the widths
    // the widget has (quarter, third, half, ...).

    canResize() {

        // Below this the grid stacks (style.css), so widths do not show.
        return matchMedia("(min-width: 1101px)").matches;

    },


    startResize(event, item) {

        if (event.button !== 0 || this.drag || !this.canResize()) return;

        event.preventDefault();

        const handle = item.resize;
        const grid = dashboardGrid.getBoundingClientRect();
        const gap = parseFloat(getComputedStyle(dashboardGrid).columnGap) || 0;

        // Measured from where it starts: a widget that wraps to the next
        // row while it grows does not jump back and forth.
        const left = item.element.getBoundingClientRect().left;
        const column = (grid.width - 11 * gap) / 12;

        handle.setPointerCapture(event.pointerId);
        item.element.classList.add("is-resizing");

        const move = e => {

            const span = (e.clientX - left + gap) / (column + gap);
            const widths = this.allowedWidths(item.widget);

            this.setWidth(item, widths.reduce((a, b) => Math.abs(b - span) < Math.abs(a - span) ? b : a));

        };

        // pointerup and then lostpointercapture: ends once.
        let ended = false;

        const end = () => {

            if (ended) return;

            ended = true;

            handle.removeEventListener("pointermove", move);
            handle.removeEventListener("pointerup", end);
            handle.removeEventListener("pointercancel", end);
            handle.removeEventListener("lostpointercapture", end);

            item.element.classList.remove("is-resizing");

            this.announce(`${this.widgetName(item.widget)}: ${this.WIDTH_LABELS[item.widget.width]}.`);

        };

        handle.addEventListener("pointermove", move);
        handle.addEventListener("pointerup", end);
        handle.addEventListener("pointercancel", end);
        handle.addEventListener("lostpointercapture", end);

    },


    resizeByKey(event, item) {

        const widths = this.allowedWidths(item.widget);
        const index = widths.indexOf(item.widget.width);

        const next = {
            ArrowLeft: index - 1, ArrowDown: index - 1,
            ArrowRight: index + 1, ArrowUp: index + 1,
            Home: 0, End: widths.length - 1
        }[event.key];

        if (next === undefined) return;

        event.preventDefault();

        if (next < 0 || next >= widths.length) return;

        this.setWidth(item, widths[next]);

        item.resize.focus();

        this.announce(`${this.widgetName(item.widget)}: ${this.WIDTH_LABELS[item.widget.width]}.`);

    },


    setWidth(item, width) {

        if (item.widget.width === width) return;

        this.reflow(() => {
            item.widget.width = width;
            item.element.dataset.width = width;
        });

        this.showWidth(item);

    },


    // ------------------------------------------------------------
    // List view: the same working copy as rows (keyboard, phones)
    // ------------------------------------------------------------

    openEditor() {

        this.endDrag(true);

        this.listSnapshot = this.draft.map(w => ({ ...w }));

        layoutError.classList.add("hidden");

        this.renderEditor();

        layoutModal.classList.remove("hidden");

        saveLayoutBtn.focus();

    },


    isListOpen() {

        return !layoutModal.classList.contains("hidden");

    },


    /** Cancel: the working copy as it was before the list view. */
    closeEditor() {

        if (this.listSnapshot) this.draft = this.listSnapshot;

        this.listSnapshot = null;

        layoutModal.classList.add("hidden");

        if (this.editing) this.renderEditing();

        listLayoutBtn.focus();

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

        const widget = {
            id: this.newId(),
            type: "traffic",
            width: 6,
            hidden: false,
            interface: next ? next.interface : ""
        };

        this.draft.push(widget);

        if (this.isListOpen()) {
            this.renderEditor();
            layoutList.lastElementChild?.scrollIntoView({ block: "nearest" });
            return;
        }

        this.renderEditing();

        const added = this.shown.find(item => item.widget.id === widget.id);

        if (added) {
            this.highlight(added.element);
            added.grip.focus({ preventScroll: true });
        }

        this.announce(`${this.widgetName(widget)} added at the end.`);

    },


    /** Apply: the list's layout on the dashboard (still in edit mode). */
    saveEditor() {

        if (this.draft.every(w => w.hidden)) {
            layoutError.textContent = "Show at least one widget.";
            layoutError.classList.remove("hidden");
            return;
        }

        this.listSnapshot = null;

        layoutModal.classList.add("hidden");

        this.renderEditing();

        listLayoutBtn.focus();

    },


    // ------------------------------------------------------------
    // Lifecycle
    // ------------------------------------------------------------

    async init() {

        // Leaving the page while the layout loads ends this run
        // (destroy() clears it): nothing renders into a page that's gone.
        const run = this.run = {};

        this.editing = false;
        this.draft = null;

        editDashboardBtn.onclick = () => this.startEditing();
        doneEditBtn.onclick = () => this.finishEditing();
        cancelEditBtn.onclick = () => this.stopEditing();
        resetLayoutBtn.onclick = () => this.resetDraft();
        listLayoutBtn.onclick = () => this.openEditor();
        trayAddGraphBtn.onclick = () => this.addDraftGraph();

        addGraphBtn.onclick = () => this.addDraftGraph();
        saveLayoutBtn.onclick = () => this.saveEditor();
        cancelLayoutBtn.onclick = () => this.closeEditor();
        closeLayoutModal.onclick = () => this.closeEditor();

        layoutModal.onclick = e => {
            if (e.target === layoutModal) this.closeEditor();
        };

        // Escape: a drag goes back, the list view closes. Edit mode
        // itself ends only with Done or Cancel (nothing lost by a key).
        this.onKey = e => {
            if (e.key !== "Escape") return;
            if (this.drag) this.endDrag(true);
            else if (this.isListOpen()) this.closeEditor();
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
        this.gnssTimer = Poll.every(10000, () => this.updateGnss());

    },

    destroy() {

        this.run = null;

        Poll.stop(this.timer);
        Poll.stop(this.statusTimer);
        Poll.stop(this.usageTimer);
        Poll.stop(this.gnssTimer);

        this.shown.forEach(item => item.graph?.destroy());
        this.shown = [];

        document.removeEventListener("keydown", this.onKey);
        window.removeEventListener("themechange", this.onTheme);

        this.endDrag(true);

        this.editing = false;
        this.draft = null;
        this.listSnapshot = null;

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
