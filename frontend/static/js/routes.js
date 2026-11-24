window.Page = window.Page || {};

window.Page.routes = {

    timer: null,

    // From /api/routes: { routes, tables: { ipv4, ipv6 }, rules: { ipv4, ipv6 } }.
    data: null,

    family: "ipv4",

    // The route being edited (its id), or null while adding.
    editing: null,

    STATES: {
        active: ["online", "Active"],
        waiting: ["warning", "Waiting"],
        off: ["muted", "Off"]
    },


    escape(value) {

        const div = document.createElement("div");
        div.textContent = value ?? "";
        return div.innerHTML;

    },


    async load() {

        const res = await fetch("/api/routes");

        if (res.status === 401) {
            location.href = "/login";
            return;
        }

        if (!res.ok) return;

        this.data = await res.json();

        this.renderRoutes();
        this.renderInterfaces();
        this.renderKernel();

    },


    // ------------------------------------------------------------
    // Your routes
    // ------------------------------------------------------------

    renderRoutes() {

        const { routes } = this.data;

        if (!routes.length) {
            routeTable.innerHTML = `<tr class="empty-row"><td colspan="7">No routes of your own yet. Add one below.</td></tr>`;
            return;
        }

        routeTable.replaceChildren(...routes.map(route => {

            const [badge, label] = this.STATES[route.state] || ["", route.state];
            const row = document.createElement("tr");

            row.innerHTML = `
                <td class="mono">${this.escape(route.destination)}</td>
                <td class="mono">${this.escape(route.gateway) || "—"}</td>
                <td class="mono">${this.escape(route.interface) || "Automatic"}</td>
                <td class="num">${route.metric}</td>
                <td><small>${this.escape(route.comment) || "—"}</small></td>
                <td>
                    <span class="status ${badge}">${label}</span>
                    ${route.detail ? `<br><small>${this.escape(route.detail)}</small>` : ""}
                </td>
                <td class="table-action">
                    <a class="table-btn" href="#/tools?tool=route&target=${encodeURIComponent(route.destination.split("/")[0])}"
                       title="Which way the router sends this traffic">Test</a>
                    <button class="table-btn route-edit" type="button">Edit</button>
                    <button class="table-btn danger route-delete" type="button">Delete</button>
                </td>
            `;

            row.querySelector(".route-edit").onclick = () => this.edit(route);
            row.querySelector(".route-delete").onclick = () => this.remove(route);

            return row;

        }));

    },


    /** Interfaces seen in the tables, plus the ones saved routes name. */
    renderInterfaces() {

        const names = new Set();

        for (const rows of Object.values(this.data.tables)) {
            rows.forEach(r => r.interface && r.interface !== "lo" && names.add(r.interface));
        }

        this.data.routes.forEach(r => r.interface && names.add(r.interface));

        const current = routeInterfaceInput.value;
        const options = [["", "Automatic (from the gateway)"], ...[...names].sort().map(n => [n, n])];

        // Only rebuild when the list changed: keeps an open dropdown open.
        const key = options.map(o => o[0]).join(",");

        if (key === this.interfaceKey) return;

        this.interfaceKey = key;

        routeInterfaceInput.replaceChildren(...options.map(([value, label]) => new Option(label, value)));
        routeInterfaceInput.value = options.some(o => o[0] === current) ? current : "";

        ChaosSelect.refresh(routeInterfaceInput);

    },


    formValues() {

        return {
            id: this.editing || `r${Date.now().toString(36)}`,
            destination: routeDestInput.value.trim(),
            gateway: routeGatewayInput.value.trim(),
            interface: routeInterfaceInput.value,
            metric: routeMetricInput.value.trim() === "" ? 0 : Number(routeMetricInput.value),
            comment: routeCommentInput.value.trim(),
            enabled: routeEnabledInput.value === "true"
        };

    },


    resetForm() {

        this.editing = null;

        [routeDestInput, routeGatewayInput, routeMetricInput, routeCommentInput].forEach(i => { i.value = ""; });

        routeInterfaceInput.value = "";
        routeEnabledInput.value = "true";

        [routeInterfaceInput, routeEnabledInput].forEach(s => ChaosSelect.refresh(s));

        routeFormTitle.textContent = "Add route";
        routeSubmit.textContent = "Add route";
        routeCancel.classList.add("hidden");

    },


    edit(route) {

        this.editing = route.id;

        routeDestInput.value = route.destination;
        routeGatewayInput.value = route.gateway;
        routeInterfaceInput.value = route.interface;
        routeMetricInput.value = String(route.metric);
        routeCommentInput.value = route.comment;
        routeEnabledInput.value = String(route.enabled);

        [routeInterfaceInput, routeEnabledInput].forEach(s => ChaosSelect.refresh(s));

        routeFormTitle.textContent = `Edit route to ${route.destination}`;
        routeSubmit.textContent = "Save route";
        routeCancel.classList.remove("hidden");
        routeStatus.textContent = "";

        routeFormCard.scrollIntoView({ block: "start", behavior: "smooth" });
        routeDestInput.focus({ preventScroll: true });

    },


    /** The saved routes without the page's extra fields. */
    savedRoutes() {

        return this.data.routes.map(({ id, destination, gateway, interface: dev, metric, comment, enabled }) =>
            ({ id, destination, gateway, interface: dev, metric, comment, enabled }));

    },


    async save(routes, status) {

        status.textContent = "Applying...";
        routeSubmit.disabled = true;

        let data;

        try {

            const res = await fetch("/api/routes/settings", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ routes })
            });

            data = await res.json().catch(() => ({ message: "Unexpected server response." }));

        } catch {

            data = { message: "The router did not answer. Try again." };

        }

        routeSubmit.disabled = false;

        status.textContent = data.success
            ? `✓ ${data.pending ? "Applied. Confirm the change in the bar at the top to keep it." : data.message}`
            : data.message || "Failed to save.";

        await this.load();

        return Boolean(data.success);

    },


    async submit() {

        const route = this.formValues();

        if (!route.destination) {
            routeStatus.textContent = "Enter the destination network, e.g. 10.20.0.0/16.";
            routeDestInput.focus();
            return;
        }

        if (!route.gateway && !route.interface) {
            routeStatus.textContent = "Enter a gateway or choose an interface.";
            routeGatewayInput.focus();
            return;
        }

        const routes = this.savedRoutes();
        const at = routes.findIndex(r => r.id === this.editing);

        if (at >= 0) routes[at] = route;
        else routes.push(route);

        if (await this.save(routes, routeStatus)) this.resetForm();

    },


    async remove(route) {

        const go = await ChaosModal.confirm({
            title: `Delete the route to ${route.destination}?`,
            subtitle: "Traffic for this network then follows the router's other routes again.",
            confirmText: "Delete"
        });

        if (!go) return;

        if (this.editing === route.id) this.resetForm();

        await this.save(this.savedRoutes().filter(r => r.id !== route.id), routeStatus);

    },


    // ------------------------------------------------------------
    // Routing tables
    // ------------------------------------------------------------

    renderKernel() {

        const rows = this.data.tables[this.family] || [];

        routeShow4.setAttribute("aria-pressed", String(this.family === "ipv4"));
        routeShow6.setAttribute("aria-pressed", String(this.family === "ipv6"));

        if (!rows.length) {
            routeKernelTable.innerHTML = `<tr class="empty-row"><td colspan="6">No ${this.family === "ipv4" ? "IPv4" : "IPv6"} routes.</td></tr>`;
        } else {

            routeKernelTable.replaceChildren(...rows.map(route => {

                const row = document.createElement("tr");

                row.classList.toggle("route-mine", route.mine);

                const kind = route.type !== "unicast" ? ` <small>(${this.escape(route.type)})</small>` : "";
                const down = route.down ? ` <small>(down)</small>` : "";

                row.innerHTML = `
                    <td class="mono">${this.escape(route.target)}${kind}</td>
                    <td class="mono">${this.escape(route.gateway) || "—"}</td>
                    <td class="mono">${this.escape(route.interface) || "—"}${down}</td>
                    <td class="num">${route.metric}</td>
                    <td>${this.escape(route.origin_label)}</td>
                    <td>${this.escape(route.table_label)}</td>
                `;

                return row;

            }));

        }

        const rules = this.data.rules[this.family] || [];

        routeRuleTable.replaceChildren(...rules.map(rule => {

            const row = document.createElement("tr");

            row.innerHTML = `
                <td class="num">${rule.pref}</td>
                <td class="mono"><small>${this.escape(rule.rule)}</small></td>
                <td>${this.escape(rule.note) || "—"}</td>
            `;

            return row;

        }));

    },


    showFamily(family) {

        this.family = family;

        if (this.data) this.renderKernel();

    },


    init() {

        [routeInterfaceInput, routeEnabledInput].forEach(s => ChaosSelect.enhance(s));

        routeSubmit.onclick = () => this.submit();
        routeCancel.onclick = () => { this.resetForm(); routeStatus.textContent = ""; };

        [routeDestInput, routeGatewayInput, routeMetricInput, routeCommentInput].forEach(input => {
            input.onkeydown = event => { if (event.key === "Enter") this.submit(); };
        });

        routeShow4.onclick = () => this.showFamily("ipv4");
        routeShow6.onclick = () => this.showFamily("ipv6");

        this.resetForm();
        this.load();

        this.timer = Poll.every(10000, () => this.load());

    },


    destroy() {

        Poll.stop(this.timer);

    }

};
