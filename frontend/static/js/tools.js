window.Page = window.Page || {};

window.Page.tools = {

    tool: "ping",

    running: false,

    // What each tool is for, and handy targets.
    TOOLS: {
        ping: {
            hint: "Checks whether a device or server answers, and how fast. Many servers and phones ignore pings, so no answer is not always a problem.",
            quick: [["1.1.1.1", "Internet (IPv4)"], ["2606:4700:4700::1111", "Internet (IPv6)"]]
        },
        traceroute: {
            hint: "Shows every router on the way to the destination, to find where a connection gets stuck. Takes up to a minute.",
            quick: [["1.1.1.1", "Internet"]]
        },
        lookup: {
            hint: "Looks up the addresses of a name, as your devices get them from the router. An address gives its name instead.",
            quick: [["example.com", "example.com"]]
        },
        port: {
            hint: "Checks whether the router can open a connection to a port, e.g. 443 for a website or 22 for SSH.",
            quick: []
        },
        route: {
            hint: "Shows which interface and gateway the router uses for an address: handy to check a route of the Routes page.",
            quick: [["1.1.1.1", "Internet"]]
        },
        iperf: {
            hint: "Measures the speed between the router and an iperf3 server: a computer on your network running \"iperf3 -s\", or a public server. Takes 10 s. Over mobile data a test can use several hundred MB of your data.",
            quick: []
        }
    },

    // The usual port of each tool that has one.
    PORTS: { port: "443", iperf: "5201" },

    serverTimer: null,

    LEVELS: {
        good: ["online", "OK"],
        warning: ["warning", "Note"],
        bad: ["offline", "Problem"]
    },


    escape(value) {

        return ChardsoftSelect.escape(value ?? "");

    },


    async load() {

        const res = await fetch("/api/tools");

        if (res.status === 401) {
            location.href = "/login";
            return;
        }

        if (!res.ok) return;

        const data = await res.json();

        toolInterface.replaceChildren(
            new Option("Automatic (normal route)", ""),
            ...data.interfaces.map(name => new Option(name, name))
        );

        ChardsoftSelect.refresh(toolInterface);

        this.traceroute = data.traceroute;
        this.iperf = data.iperf;

        if (this.tool === "iperf") this.choose("iperf");

    },


    choose(tool) {

        this.tool = tool;

        document.querySelectorAll("[data-tool]").forEach(button => {
            button.setAttribute("aria-pressed", String(button.dataset.tool === tool));
        });

        const info = this.TOOLS[tool];

        toolHint.textContent = tool === "iperf" && this.iperf === false
            ? "iperf3 is not installed on the router. Run the installer again (System page: update)."
            : info.hint;

        // Switching between the port check and iperf3: their usual port.
        if (this.PORTS[tool] && Object.values(this.PORTS).includes(toolPort.value)) {
            toolPort.value = this.PORTS[tool];
        }

        toolPortField.classList.toggle("hidden", !this.PORTS[tool]);
        toolDirectionField.classList.toggle("hidden", tool !== "iperf");
        toolInterfaceField.classList.toggle("hidden", !["ping", "traceroute"].includes(tool));

        toolQuick.replaceChildren(...info.quick.map(([target, label]) => {

            const button = document.createElement("button");

            button.type = "button";
            button.className = "table-btn";
            button.textContent = label;
            button.onclick = () => { toolTarget.value = target; this.run(); };

            return button;

        }));

        toolQuick.classList.toggle("hidden", !info.quick.length);

    },


    async run() {

        if (this.running) return;

        const target = toolTarget.value.trim();

        if (!target) {
            toolTarget.focus();
            return;
        }

        this.running = true;
        toolRun.disabled = true;
        toolRun.textContent = this.tool === "traceroute" ? "Running (up to a minute)..."
            : this.tool === "iperf" ? "Measuring (10 s)..." : "Running...";

        let data;

        try {

            const res = await fetch("/api/tools/run", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    tool: this.tool,
                    target,
                    family: toolFamily.value,
                    interface: toolInterface.value,
                    port: toolPort.value,
                    direction: toolDirection.value
                })
            });

            if (res.status === 401) {
                location.href = "/login";
                return;
            }

            data = await res.json().catch(() => ({ ok: false, error: "Unexpected server response." }));

        } catch {

            data = { ok: false, error: "The router did not answer. Try again." };

        } finally {

            this.running = false;
            toolRun.disabled = false;
            toolRun.textContent = "Run";

        }

        this.render(data);

    },


    render(data) {

        toolResultCard.classList.remove("hidden");

        const names = { ping: "Ping", traceroute: "Traceroute", lookup: "Name lookup", port: "Port check", route: "Which way", iperf: "Speed" };

        toolResultTitle.textContent = data.target ? `${names[data.tool]}: ${data.target}` : "Result";

        if (data.error) {
            toolPill.className = "status offline";
            toolPill.textContent = "Not run";
            toolSummary.textContent = data.error;
            toolDetail.replaceChildren();
            toolRaw.textContent = "";
            toolRaw.parentElement.classList.add("hidden");
            return;
        }

        const [badge, label] = this.LEVELS[data.level] || this.LEVELS.bad;

        toolPill.className = `status ${badge}`;
        toolPill.textContent = label;
        toolSummary.textContent = data.summary;

        toolRaw.textContent = data.raw || "";
        toolRaw.parentElement.classList.toggle("hidden", !data.raw);

        toolDetail.innerHTML = this.detail(data);

    },


    /** A table that goes with the summary, where one helps. */
    detail(data) {

        const table = (head, rows) => rows.length ? `
            <div class="table-scroll">
                <table class="client-table data-table">
                    <thead><tr>${head.map(h => `<th${h.num ? ' class="num"' : ""}>${h.label || h}</th>`).join("")}</tr></thead>
                    <tbody>${rows.join("")}</tbody>
                </table>
            </div>` : "";

        if (data.tool === "ping" && data.sent) {

            const ms = value => value == null ? "—" : `${value.toFixed(1)} ms`;

            return `<div class="info-grid tool-figures">
                <div class="info-row"><span>Address</span><strong class="mono">${this.escape(data.address)}</strong></div>
                <div class="info-row"><span>Answered</span><strong>${data.received} of ${data.sent}</strong></div>
                <div class="info-row"><span>Fastest / average / slowest</span><strong>${ms(data.min)} / ${ms(data.avg)} / ${ms(data.max)}</strong></div>
            </div>`;

        }

        if (data.tool === "traceroute") {

            return table(
                [{ label: "Hop", num: true }, "Router", { label: "Time", num: true }, "Note"],
                data.hops.map(hop => `<tr>
                    <td class="num">${hop.hop}</td>
                    <td class="mono">${this.escape(hop.address) || "*"}</td>
                    <td class="num">${hop.ms == null ? "—" : `${hop.ms.toFixed(1)} ms`}</td>
                    <td><small>${this.escape([hop.note, hop.flag].filter(Boolean).join(" "))}</small></td>
                </tr>`)
            );

        }

        if (data.tool === "lookup") {

            return table(["Address", "Version"], (data.addresses || []).map(address => `<tr>
                <td class="mono">${this.escape(address)}</td>
                <td>${address.includes(":") ? "IPv6" : "IPv4"}</td>
            </tr>`));

        }

        if (data.tool === "iperf" && data.mbits != null) return this.speedFigures(data);

        if (data.tool === "route" && data.interface) {

            return `<div class="info-grid tool-figures">
                <div class="info-row"><span>Address</span><strong class="mono">${this.escape(data.address)}</strong></div>
                <div class="info-row"><span>Interface</span><strong class="mono">${this.escape(data.interface)}</strong></div>
                <div class="info-row"><span>Gateway</span><strong class="mono">${this.escape(data.gateway) || "None (directly)"}</strong></div>
                <div class="info-row"><span>Sent from</span><strong class="mono">${this.escape(data.source) || "—"}</strong></div>
                ${data.route ? `<div class="info-row"><span>Your route</span><strong><a href="#/routes">${this.escape(data.route)}</a></strong></div>` : ""}
            </div>`;

        }

        return "";

    },


    /** Speed, its spread over the seconds, retransmissions and data. */
    speedFigures(data) {

        const mbit = value => `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} Mbit/s`;
        const seconds = data.seconds || [];
        const mb = data.bytes >= 10e6 ? (data.bytes / 1e6).toFixed(0) : (data.bytes / 1e6).toFixed(1);

        return `<div class="info-grid tool-figures">
            <div class="info-row"><span>Speed</span><strong>${mbit(data.mbits)}</strong></div>
            ${seconds.length ? `<div class="info-row"><span>Slowest / fastest second</span><strong>${mbit(Math.min(...seconds))} / ${mbit(Math.max(...seconds))}</strong></div>` : ""}
            ${data.retransmits != null ? `<div class="info-row"><span>Resent packets</span><strong>${data.retransmits}</strong></div>` : ""}
            <div class="info-row"><span>Data moved</span><strong>${mb} MB${data.mobile ? " (mobile data)" : ""}</strong></div>
            ${data.local ? `<div class="info-row"><span>Between</span><strong class="mono">${this.escape(data.local)} ↔ ${this.escape(data.remote)}</strong></div>` : ""}
        </div>`;

    },


    // The router as the iperf3 server.

    async loadServer() {

        const res = await fetch("/api/tools/iperf-server");

        if (res.status === 401) {
            location.href = "/login";
            return;
        }

        if (res.ok) this.renderServer(await res.json());

    },


    async toggleServer() {

        speedServerButton.disabled = true;

        let data;

        try {

            const res = await fetch("/api/tools/iperf-server", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action: this.server?.running ? "stop" : "start" })
            });

            if (res.status === 401) {
                location.href = "/login";
                return;
            }

            data = await res.json().catch(() => ({ error: "Unexpected server response." }));

        } catch {

            data = { error: "The router did not answer. Try again." };

        } finally {

            speedServerButton.disabled = false;

        }

        if (data.error) {
            speedServerPill.className = "status offline";
            speedServerPill.textContent = "Not started";
            speedServerText.textContent = data.error;
            return;
        }

        this.renderServer(data);

    },


    renderServer(status) {

        this.server = status;

        clearTimeout(this.serverTimer);
        clearTimeout(this.captureTimer);

        speedServerButton.textContent = status.running ? "Stop server" : "Start server";
        speedServerButton.disabled = !status.available;

        const last = status.last;

        if (!status.available) {
            speedServerPill.className = "status offline";
            speedServerPill.textContent = "Not installed";
            speedServerText.textContent = "iperf3 is not installed on the router. Run the installer again (System page: update).";
        } else if (status.running) {
            const left = Math.max(0, Math.round((status.until * 1000 - Date.now()) / 60000));
            speedServerPill.className = "status warning";
            speedServerPill.textContent = "Waiting";
            speedServerText.textContent = `Waiting for a test on port ${status.port} (${left ? `about ${left} min left` : "less than a minute left"}).`;
        } else if (last) {
            const [badge, label] = this.LEVELS[last.level] || this.LEVELS.bad;
            speedServerPill.className = `status ${badge}`;
            speedServerPill.textContent = label;
            speedServerText.textContent = last.summary;
        } else {
            speedServerPill.className = "status";
            speedServerPill.textContent = "Off";
            speedServerText.textContent = "The server is off.";
        }

        // What to run on the device, per network the router is on.
        speedServerCommands.innerHTML = status.running && status.addresses.length ? `
            <div class="table-scroll">
                <table class="client-table data-table">
                    <thead><tr><th>Device on</th><th>Upload test</th><th>Download test</th></tr></thead>
                    <tbody>${status.addresses.map(a => `<tr>
                        <td class="mono">${this.escape(a.interface)}</td>
                        <td class="mono">iperf3 -c ${this.escape(a.address)}</td>
                        <td class="mono">iperf3 -c ${this.escape(a.address)} -R</td>
                    </tr>`).join("")}</tbody>
                </table>
            </div>` : "";

        speedServerResult.innerHTML = !status.running && last && last.mbits != null ? this.speedFigures(last) : "";

        // Follows the test while the router waits.
        if (status.running) this.serverTimer = setTimeout(() => this.loadServer(), 2000);

    },


    // ------------------------------------------------------------
    // Packet capture
    // ------------------------------------------------------------

    captureTimer: null,


    formatBytes(bytes) {

        if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
        if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;

        return `${bytes} bytes`;

    },


    async loadCapture() {

        let data;

        try {
            const res = await fetch("/api/tools/capture");
            if (!res.ok) return;
            data = await res.json();
        } catch {
            return;
        }

        this.renderCapture(data);

        clearTimeout(this.captureTimer);

        if (data.running) this.captureTimer = setTimeout(() => this.loadCapture(), 1000);

    },


    renderCapture(data) {

        this.capture = data;

        if (!data.available) {
            captureState.textContent = "tcpdump is not installed yet: the next update of the router adds it.";
            captureStart.disabled = true;
            return;
        }

        // The interface list, once (keeps the choice).
        if (captureInterface.options.length !== data.interfaces.length) {
            const chosen = captureInterface.value;
            captureInterface.replaceChildren(...data.interfaces.map(name =>
                new Option(name === "any" ? "All interfaces" : name, name)));
            if (chosen) captureInterface.value = chosen;
            ChardsoftSelect.refresh(captureInterface);
        }

        const where = data.interface === "any" ? "all interfaces" : data.interface;

        if (data.running) {

            const elapsed = Math.round(Date.now() / 1000 - data.started);

            captureState.textContent =
                `Capturing on ${where}: ${this.formatBytes(data.size)} of ${data.limit_mb} MB, ` +
                `${elapsed} of ${data.seconds} s.` + (data.filter ? ` Filter: ${data.filter}` : "");

        } else if (data.file) {

            const why = { time: "the time was up", size: "the size limit was reached", stopped: "you stopped it" }[data.reason] || "it ended";
            const packets = data.packets != null ? `${data.packets} packets, ` : "";
            const until = data.expires ? ` It is deleted at ${new Date(data.expires * 1000).toLocaleTimeString()}.` : "";

            captureState.textContent = `Capture of ${where} done (${why}): ${packets}${this.formatBytes(data.size)}.${until}`;

        } else if (data.error) {

            captureState.textContent = `The capture did not work: ${data.error}`;

        } else {

            captureState.textContent = data.ended ? "The capture caught no packets." : "";

        }

        captureStart.disabled = false;
        captureStart.textContent = data.running ? "Stop" : "Start capture";
        captureStart.classList.toggle("danger-btn", data.running);

        captureDownload.classList.toggle("hidden", !data.file);
        captureDelete.classList.toggle("hidden", !data.file);

    },


    async postCapture(path, body) {

        try {
            const res = await fetch(`/api/tools/capture/${path}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body ?? {})
            });
            return await res.json();
        } catch {
            return { success: false, message: "The router did not answer." };
        }

    },


    async toggleCapture() {

        captureStatus.textContent = "";

        if (this.capture?.running) {
            captureStart.disabled = true;
            const data = await this.postCapture("stop");
            this.renderCapture(data);
            return;
        }

        const body = document.createElement("div");

        body.innerHTML = `
            <label class="modal-field">
                <span>Your password</span>
                <input class="setting-input" type="password" autocomplete="current-password">
            </label>
            <p class="modal-note">
                Only capture traffic you are allowed to look at. A new capture replaces the last file.
            </p>
        `;

        const confirmed = await ChardsoftModal.confirm({
            title: "Start packet capture",
            subtitle: `On ${captureInterface.value === "any" ? "all interfaces" : captureInterface.value}.`,
            body,
            confirmText: "Start"
        });

        if (!confirmed) return;

        captureStart.disabled = true;
        captureStatus.textContent = "Starting...";

        const data = await this.postCapture("start", {
            password: body.querySelector("input").value,
            interface: captureInterface.value,
            seconds: Number(captureSeconds.value),
            limit_mb: Number(captureLimit.value),
            host: captureHost.value,
            port: capturePort.value,
            protocol: captureProtocol.value,
            filter: captureFilter.value,
            exclude_self: captureExcludeSelf.checked
        });

        captureStart.disabled = false;
        captureStatus.textContent = data.success ? "" : data.message;

        this.loadCapture();

    },


    async deleteCapture() {

        const data = await this.postCapture("delete");

        captureStatus.textContent = data.success ? "✓ Deleted." : data.message;

        this.loadCapture();

    },


    init() {

        [toolFamily, toolInterface, toolDirection].forEach(select => ChardsoftSelect.enhance(select));

        speedServerButton.onclick = () => this.toggleServer();

        [captureInterface, captureSeconds, captureLimit, captureProtocol].forEach(select => ChardsoftSelect.enhance(select));
        captureStart.onclick = () => this.toggleCapture();
        captureDownload.onclick = () => { location.href = "/api/tools/capture/download"; };
        captureDelete.onclick = () => this.deleteCapture();
        this.loadCapture();

        this.loadServer();

        document.querySelectorAll("[data-tool]").forEach(button => {
            button.onclick = () => this.choose(button.dataset.tool);
        });

        toolRun.onclick = () => this.run();

        // "#/tools?tool=route&target=10.20.0.1" (e.g. from the Routes page).
        const tool = routeParam("tool");

        this.choose(this.TOOLS[tool] ? tool : "ping");

        const target = routeParam("target");

        if (target) toolTarget.value = target;

        this.load();

        if (target) this.run();

    },


    destroy() {

        clearTimeout(this.serverTimer);

    }

};
