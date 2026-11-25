window.Page = window.Page || {};

/*
 * Apps page: the Apps Addon's catalog. Installing, removing, ... runs
 * as a job on the router; its output is shown while it runs.
 */
window.Page.apps = {

    timer: null,
    data: null,
    active: false,

    // Finished job the user closed (not shown again).
    closedJob: null,

    // Own app: "form" (an image) or "compose" (a compose file).
    customMode: "form",

    // The finished "add" job whose form was cleared.
    clearedJob: null,

    STATES: {
        running: { text: "Running", css: "online" },
        stopped: { text: "Stopped", css: "" },
        partial: { text: "Partly running", css: "" },
        error: { text: "Error", css: "offline" },
        unknown: { text: "Unknown", css: "" }
    },


    escape(value) {

        return ChardsoftSelect.escape(value);

    },


    async request(url, options = {}) {

        const res = await fetch(url, options);

        if (res.status === 401) {
            location.href = "/login";
            return null;
        }

        try {
            return { ok: res.ok, data: await res.json() };
        } catch {
            return { ok: false, data: { message: "Unexpected server response." } };
        }

    },


    post(url, body) {

        return this.request(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body ?? {})
        });

    },


    // ------------------------------------------------------------
    // Load / render
    // ------------------------------------------------------------

    async refresh() {

        const res = await this.request("/api/apps");

        // Left the page while loading.
        if (!res || !this.active) return;

        if (!res.ok) {
            appsNotice.textContent = "The apps could not be loaded.";
            appsNotice.classList.remove("hidden");
            return;
        }

        this.data = res.data;

        this.renderStatus();
        this.renderJob();
        this.renderAddon();
        this.renderCatalog();

        this.schedule();

    },


    // Fast while a job runs, slow otherwise.
    schedule() {

        clearTimeout(this.timer);

        const busy = this.jobRunning();

        this.timer = setTimeout(() => this.refresh(), busy ? 1500 : 10000);

    },


    jobRunning() {

        return Boolean(this.data?.job && !this.data.job.done);

    },


    renderStatus() {

        const { addon, apps } = this.data;

        appsAddonState.textContent = addon.installed
            ? `Installed${addon.version ? ` (${addon.version})` : ""}`
            : "Not installed";

        appsDockerState.textContent = !addon.installed
            ? "--"
            : addon.docker_running ? "Running" : "Stopped";

        appsInstalledCount.textContent = addon.installed
            ? apps.filter(a => a.installed).length
            : "--";

        appsRunningCount.textContent = addon.installed
            ? apps.filter(a => a.state === "running").length
            : "--";

    },


    renderJob() {

        const job = this.data.job;

        if (!job || (job.done && job.id === this.closedJob)) {
            appsJob.classList.add("hidden");
            return;
        }

        const label = this.jobLabel(job.action);

        appsJobTitle.textContent = job.name ? `${label} ${job.name}` : label;

        // Keep the view at the bottom unless the user scrolled up.
        const atBottom = appsJobLog.scrollHeight - appsJobLog.scrollTop - appsJobLog.clientHeight < 24;

        appsJobLog.textContent = job.lines.join("\n") || "Starting...";

        if (atBottom) appsJobLog.scrollTop = appsJobLog.scrollHeight;

        appsJobStatus.textContent = !job.done
            ? "Working... this page updates by itself."
            : job.success ? "✓ Done." : "Failed (see above).";

        appsJobClose.classList.toggle("hidden", !job.done);

        // An own app was added: the form is free for the next one.
        if (job.action === "add" && job.done && job.success && job.id !== this.clearedJob) {
            this.clearedJob = job.id;
            this.clearCustom();
        }

        const appeared = appsJob.classList.contains("hidden");

        appsJob.classList.remove("hidden");

        // The log sits below the apps: bring a new job into view once.
        if (appeared && !job.done && job.id !== this.shownJob) {
            this.shownJob = job.id;
            appsJob.scrollIntoView({ block: "nearest" });
        }

    },


    jobLabel(action) {

        return {
            add: "Adding", install: "Installing", start: "Starting", stop: "Stopping",
            restart: "Restarting", update: "Updating", remove: "Removing",
            addon: "Installing the Apps Addon"
        }[action] || action;

    },


    renderAddon() {

        const { addon } = this.data;

        appsAddon.classList.toggle("hidden", addon.installed);

        appsAddonInstall.disabled = this.jobRunning() || !addon.can_install;

        if (!addon.installed && !addon.can_install) {
            appsAddonStatus.textContent = "The addon installer (deploy/install-apps.sh) is missing.";
        }

        const problem = !addon.installed ? ""
            : addon.error ? `The App Manager reports: ${addon.error}`
            : !addon.docker_running ? "Docker is not running, so apps are stopped. Start it on the router: sudo systemctl start docker"
            : "";

        appsNotice.textContent = problem;
        appsNotice.classList.toggle("hidden", !problem);

    },


    renderCatalog() {

        const { addon, apps, base_domain } = this.data;

        appsCatalog.classList.toggle("hidden", !addon.installed);

        if (!addon.installed) return;

        appsGrid.innerHTML = "";

        if (!apps.length) {
            appsGrid.innerHTML = `<p class="setting-hint">The catalog is empty.</p>`;
        }

        apps.forEach(app => appsGrid.appendChild(this.renderApp(app)));

        customAdd.disabled = this.jobRunning();

        if (customAddress.textContent === "--") this.showCustomAddress();

        appsNamesHint.textContent =
            `Apps are reached by name, e.g. https://<app>.${base_domain}/, from every device in your network. ` +
            `Install the router's certificate (System page) and browsers trust them without warnings.`;

    },


    renderApp(app) {

        const card = document.createElement("div");
        card.className = "card app-card";

        const busy = this.jobRunning();
        const disabled = busy ? "disabled" : "";

        // The app the running job works on: its state is in between.
        const working = busy && this.data.job.app === app.id;

        const state = working
            ? { text: `${this.jobLabel(this.data.job.action)}...`, css: "" }
            : this.STATES[app.state];

        // Apps are HTTPS only (the router redirects plain HTTP). Own apps
        // may have no web page.
        const url = app.urls.https;
        const web = !app.custom || app.web;

        let actions;

        if (working) {

            actions = "";

        } else if (!app.installed) {

            actions = `<button class="save-btn" data-action="install" ${disabled}>Install</button>`;

        } else {

            const running = app.state === "running";

            actions = [
                running && web ? `<a class="save-btn app-open" href="${this.escape(url)}" target="_blank" rel="noopener">Open</a>` : "",
                running || app.state === "partial"
                    ? `<button class="table-btn" data-action="stop" ${disabled}>Stop</button>`
                    : `<button class="table-btn" data-action="start" ${disabled}>Start</button>`,
                running ? `<button class="table-btn" data-action="restart" ${disabled}>Restart</button>` : "",
                `<button class="table-btn" data-action="update" ${disabled}>Update</button>`,
                `<button class="table-btn" data-action="logs">Logs</button>`,
                `<button class="table-btn danger" data-action="remove" ${disabled}>Remove</button>`
            ].join("");

        }

        const size = app.download_mb
            ? `Download: about ${app.download_mb >= 1000 ? `${(app.download_mb / 1000).toFixed(1)} GB` : `${app.download_mb} MB`}.`
            : "";

        const ram = app.low_ram && !app.installed
            ? `This router has ${this.gigabytes(this.data.board_ram_mb)} of memory; ${this.escape(app.name)} needs ${this.gigabytes(app.min_ram_mb)} to run well. It may be slow or make the router run out of memory.`
            : "";

        card.innerHTML = `
            <div class="app-card-head">
                <div class="app-icon" aria-hidden="true">${this.escape(app.name.charAt(0))}</div>
                <div class="app-title">
                    <strong>${this.escape(app.name)}</strong>
                    <small>${this.escape(app.category)}</small>
                </div>
                ${state ? `<span class="status ${state.css}">${state.text}</span>` : ""}
            </div>

            <p class="app-description">${this.escape(app.description)}</p>

            ${app.installed && !working ? `
                <p class="app-address">
                    ${web
                        ? `<a href="${this.escape(url)}" target="_blank" rel="noopener">${this.escape(url)}</a>`
                        : "No web page."}
                </p>
                ${app.first_steps ? `<p class="setting-hint">${this.escape(app.first_steps)}</p>` : ""}
                ${app.risks?.length ? `
                    <details class="app-risks">
                        <summary>Risky settings you accepted (${app.risks.length})</summary>
                        <ul class="app-risks-list">${app.risks.map(r => `<li>${this.escape(r)}</li>`).join("")}</ul>
                    </details>` : ""}
            ` : `
                <p class="setting-hint">${this.escape(size)}</p>
                ${ram ? `<p class="app-ram-warning">${ram}</p>` : ""}
            `}

            <div class="app-actions">${actions}</div>
        `;

        card.querySelectorAll("button[data-action]").forEach(button => {
            button.onclick = () => this.act(app, button.dataset.action);
        });

        return card;

    },


    // ------------------------------------------------------------
    // Actions
    // ------------------------------------------------------------

    gigabytes(mb) {
        return mb >= 1024 ? `${+(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
    },


    async act(app, action) {

        if (action === "logs") {
            await this.showLogs(app);
            return;
        }

        let body = {};

        if (action === "remove") {

            const box = document.createElement("label");
            box.className = "check-item";
            box.innerHTML = `
                <input type="checkbox" id="appsDeleteData">
                <span>Also delete its data (files, accounts, settings)</span>
            `;

            const confirmed = await ChardsoftModal.confirm({
                title: `Remove ${app.name}`,
                subtitle: "The app stops and is removed. Without the box below, its data stays on the router for a later install.",
                body: box,
                confirmText: "Remove"
            });

            if (!confirmed) return;

            body = { delete_data: box.querySelector("input").checked };

        }

        if (action === "install" && app.low_ram) {

            const confirmed = await ChardsoftModal.confirm({
                title: `Install ${app.name} anyway?`,
                subtitle: `${app.name} needs ${this.gigabytes(app.min_ram_mb)} of memory to run well; this router has ${this.gigabytes(this.data.board_ram_mb)}. Other apps and the router itself may slow down. You can remove it again later.`,
                confirmText: "Install anyway"
            });

            if (!confirmed) return;

        }

        if (action === "update") {

            const confirmed = await ChardsoftModal.confirm({
                title: `Update ${app.name}`,
                subtitle: "Downloads the newest version and restarts the app. It is unavailable for a moment.",
                confirmText: "Update"
            });

            if (!confirmed) return;

        }

        const res = await this.post(`/api/apps/${encodeURIComponent(app.id)}/${action}`, body);

        if (!res) return;

        if (!res.data.success) {
            appsNotice.textContent = res.data.message || "Something went wrong.";
            appsNotice.classList.remove("hidden");
            return;
        }

        this.closedJob = null;

        await this.refresh();

    },


    async installAddon() {

        const confirmed = await ChardsoftModal.confirm({
            title: "Install Apps Addon",
            subtitle: "Installs Docker Engine, Docker Compose and the App Manager. This takes a few minutes and needs internet.",
            confirmText: "Install"
        });

        if (!confirmed) return;

        appsAddonStatus.textContent = "Starting...";

        const res = await this.post("/api/apps/addon/install");

        if (!res) return;

        appsAddonStatus.textContent = res.data.success ? "" : res.data.message;

        this.closedJob = null;

        await this.refresh();

    },


    async showLogs(app) {

        const res = await this.request(`/api/apps/${encodeURIComponent(app.id)}/logs`);

        if (!res) return;

        const pre = document.createElement("pre");
        pre.className = "config-preview apps-log";
        pre.textContent = res.data.success
            ? res.data.logs || "No output yet."
            : res.data.message;

        const shown = ChardsoftModal.confirm({
            title: `${app.name}: Logs`,
            subtitle: "The last 200 lines.",
            body: pre,
            confirmText: "Close"
        });

        pre.scrollTop = pre.scrollHeight;

        await shown;

    },


    // ------------------------------------------------------------
    // Own apps
    // ------------------------------------------------------------

    customId(name) {

        return String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-")
            .replace(/^-+|-+$/g, "").slice(0, 31).replace(/-+$/, "");

    },


    setCustomMode(mode) {

        this.customMode = mode;

        document.querySelectorAll("[data-custom-mode]").forEach(button => {
            button.setAttribute("aria-pressed", String(button.dataset.customMode === mode));
        });

        customForm.classList.toggle("hidden", mode !== "form");
        customCompose.classList.toggle("hidden", mode !== "compose");

        this.clearCheck();

    },


    showCustomAddress() {

        const id = this.customId(customName.value);
        const domain = this.data?.base_domain;

        customAddress.textContent = id && domain ? `https://${id}.${domain}/` : "--";

    },


    clearCheck() {

        customResult.classList.add("hidden");
        customResult.innerHTML = "";
        customStatus.textContent = "";

    },


    clearCustom() {

        [customName, customImage, customPort, customVolumes, customEnv,
         customComposeText, customService, customComposePort].forEach(input => input.value = "");

        customServices.innerHTML = "";

        this.showCustomAddress();

    },


    customPayload() {

        const name = customName.value.trim();
        const lines = text => text.split("\n").map(line => line.trim()).filter(Boolean);

        if (this.customMode === "form") {

            const env = lines(customEnv.value).map(line => {
                const at = line.indexOf("=");
                return at < 0
                    ? { key: line, value: "" }
                    : { key: line.slice(0, at).trim(), value: line.slice(at + 1) };
            });

            return {
                name,
                mode: "form",
                image: customImage.value.trim(),
                port: customPort.value.trim() || null,
                volumes: lines(customVolumes.value),
                env
            };

        }

        return {
            name,
            mode: "compose",
            compose: customComposeText.value,
            service: customService.value.trim(),
            port: customComposePort.value.trim() || null
        };

    },


    list(items) {

        return `<ul>${items.map(item => `<li>${this.escape(item)}</li>`).join("")}</ul>`;

    },


    renderCheck(check) {

        const parts = [];

        if (check.errors.length) {
            parts.push(`<div class="setup-error"><strong>This cannot be added:</strong>${this.list(check.errors)}</div>`);
        }

        if (check.warnings.length) {
            parts.push(`<div class="modal-warning"><strong>These settings could harm the router:</strong>${this.list(check.warnings)}</div>`);
        }

        if (!check.errors.length) {

            const where = check.route
                ? `Reached at https://${check.host}/ (service "${check.route.service}", port ${check.route.port}).`
                : "No web page.";

            parts.push(`<p class="setting-hint">Images: ${this.escape(check.images.join(", ") || "--")}. ${this.escape(where)}</p>`);

        }

        customResult.innerHTML = parts.join("");
        customResult.classList.remove("hidden");

        // Several services: offer them for the web page.
        customServices.innerHTML = check.services
            .map(name => `<option value="${this.escape(name)}"></option>`).join("");

    },


    async checkCustom() {

        if (!this.customId(customName.value)) {
            customStatus.textContent = "Give the app a name.";
            return null;
        }

        customCheck.disabled = customAdd.disabled = true;
        customStatus.textContent = "Checking (Docker reads the file)...";

        const res = await this.post("/api/apps/custom/check", this.customPayload());

        customCheck.disabled = false;
        customAdd.disabled = this.jobRunning();

        if (!res) return null;

        if (!res.data.success) {
            customResult.classList.add("hidden");
            customStatus.textContent = res.data.message || "The check failed.";
            return null;
        }

        const check = res.data;

        this.renderCheck(check);

        customStatus.textContent = check.errors.length
            ? "Fix the problems above, then check again."
            : check.warnings.length
                ? `${check.warnings.length} risky setting${check.warnings.length === 1 ? "" : "s"}: adding asks you to accept them.`
                : "✓ No problems found.";

        return check;

    },


    async addCustom() {

        const check = await this.checkCustom();

        if (!check || check.errors.length) return;

        const name = customName.value.trim();
        let accept = false;

        if (check.warnings.length) {

            const body = document.createElement("div");
            body.className = "apps-custom-result";
            body.innerHTML = `
                <div class="modal-warning">${this.list(check.warnings)}</div>
                <label class="check-item">
                    <input type="checkbox">
                    <span>I understand that these settings can break the router or make it reachable from outside.</span>
                </label>
            `;

            const confirmed = await ChardsoftModal.confirm({
                title: `Add ${name} with risky settings?`,
                subtitle: "The router runs the app as written. Only do this for apps you trust.",
                body,
                confirmText: "Add anyway"
            });

            if (!confirmed) return;

            if (!body.querySelector("input").checked) {
                customStatus.textContent = "Not added: tick the box to accept the risky settings.";
                return;
            }

            accept = true;

        } else {

            const confirmed = await ChardsoftModal.confirm({
                title: `Add ${name}`,
                subtitle: `Downloads ${check.images.join(", ")} and starts it${check.route ? ` at https://${check.host}/` : ""}. This can take a while.`,
                confirmText: "Add"
            });

            if (!confirmed) return;

        }

        const res = await this.post("/api/apps/custom/add", { app: this.customPayload(), accept_risks: accept });

        if (!res) return;

        if (!res.data.success) {
            customStatus.textContent = res.data.message || "Something went wrong.";
            return;
        }

        this.clearCheck();
        this.closedJob = null;

        await this.refresh();

    },


    // ------------------------------------------------------------
    // Lifecycle
    // ------------------------------------------------------------

    init() {

        this.active = true;

        appsAddonInstall.onclick = () => this.installAddon();

        document.querySelectorAll("[data-custom-mode]").forEach(button => {
            button.onclick = () => this.setCustomMode(button.dataset.customMode);
        });

        customName.oninput = () => {
            this.showCustomAddress();
            this.clearCheck();
        };
        customCheck.onclick = () => this.checkCustom();
        customAdd.onclick = () => this.addCustom();

        appsJobClose.onclick = () => {
            this.closedJob = this.data?.job?.id ?? null;
            appsJob.classList.add("hidden");
        };

        this.refresh();

    },


    destroy() {

        this.active = false;

        clearTimeout(this.timer);
        this.timer = null;

        ChardsoftModal.close();

    }

};
