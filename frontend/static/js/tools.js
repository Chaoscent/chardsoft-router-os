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
        }
    },

    LEVELS: {
        good: ["online", "OK"],
        warning: ["warning", "Note"],
        bad: ["offline", "Problem"]
    },


    escape(value) {

        return ChaosSelect.escape(value ?? "");

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

        ChaosSelect.refresh(toolInterface);

        this.traceroute = data.traceroute;

    },


    choose(tool) {

        this.tool = tool;

        document.querySelectorAll("[data-tool]").forEach(button => {
            button.setAttribute("aria-pressed", String(button.dataset.tool === tool));
        });

        const info = this.TOOLS[tool];

        toolHint.textContent = info.hint;

        toolPortField.classList.toggle("hidden", tool !== "port");
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
        toolRun.textContent = this.tool === "traceroute" ? "Running (up to a minute)..." : "Running...";

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
                    port: toolPort.value
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

        const names = { ping: "Ping", traceroute: "Traceroute", lookup: "Name lookup", port: "Port check", route: "Which way" };

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


    init() {

        [toolFamily, toolInterface].forEach(select => ChaosSelect.enhance(select));

        document.querySelectorAll("[data-tool]").forEach(button => {
            button.onclick = () => this.choose(button.dataset.tool);
        });

        toolRun.onclick = () => this.run();

        [toolTarget, toolPort].forEach(input => {
            input.onkeydown = event => { if (event.key === "Enter") this.run(); };
        });

        // "#/tools?tool=route&target=10.20.0.1" (e.g. from the Routes page).
        const tool = routeParam("tool");

        this.choose(this.TOOLS[tool] ? tool : "ping");

        const target = routeParam("target");

        if (target) toolTarget.value = target;

        this.load();

        if (target) this.run();

    },


    destroy() {}

};
