window.Page = window.Page || {};

window.Page.system = {

    async load() {

        const res = await fetch("/api/dashboard");

        if (res.status === 401) {
            location.href = "/login";
            return;
        }

        const data = await res.json();

        hostnameInput.value = data.hostname;

        await this.loadSecurity();
        await this.loadPing();

    },


    async loadSecurity() {

        const res = await fetch("/api/system/security");

        if (res.status === 401) {
            location.href = "/login";
            return;
        }

        const data = await res.json();

        /*
         * Only show a value when a custom value is actually configured.
         * Otherwise the input stays empty and the placeholder shows:
         * "Default: 5 minutes" / "Default: 1 hour".
         *
         * The backend still uses the default values when no custom
         * configuration exists.
         */

        if (
            data.idle_timeout !== null &&
            data.idle_timeout !== undefined
        ) {
            idleTimeoutInput.value =
                Math.floor(data.idle_timeout / 60);
        } else {
            idleTimeoutInput.value = "";
        }

        if (
            data.absolute_timeout !== null &&
            data.absolute_timeout !== undefined
        ) {
            absoluteTimeoutInput.value =
                Math.floor(data.absolute_timeout / 60);
        } else {
            absoluteTimeoutInput.value = "";
        }

    },


    async loadPing() {

        const res = await fetch("/api/system/ping");

        if (res.status === 401) {
            location.href = "/login";
            return;
        }

        if (!res.ok) {
            pingStatus.textContent =
                "Failed to load ping settings.";
            return;
        }

        const data = await res.json();

        pingEnabledInput.value =
            data.enabled ? "true" : "false";

        pingIntervalInput.value =
            String(data.interval);

        pingDestinationInput.value =
            data.destination || "1.1.1.1";

        this.updatePingIntervalState();

    },


    updatePingIntervalState() {

        pingIntervalInput.disabled =
            pingEnabledInput.value !== "true";

        // Keep the custom dropdowns in step with the native selects.
        ChardsoftSelect.refresh(pingEnabledInput);
        ChardsoftSelect.refresh(pingIntervalInput);

    },


    async save() {

        hostnameStatus.textContent = "Saving...";

        const res = await fetch("/api/system/hostname", {

            method: "POST",

            headers: {
                "Content-Type": "application/json"
            },

            body: JSON.stringify({
                hostname: hostnameInput.value.trim()
            })

        });

        if (res.status === 401) {
            location.href = "/login";
            return;
        }

        const data = await res.json();

        if (data.success) {

            hostnameStatus.textContent =
                "✓ Hostname updated";

            if (window.Header?.update) {
                Header.update();
            }

        } else {

            hostnameStatus.textContent =
                data.message;

        }

    },


    async saveSecurity() {

        securityStatus.textContent = "Saving...";

        const idleMinutes = parseInt(
            idleTimeoutInput.value,
            10
        );

        const absoluteMinutes = parseInt(
            absoluteTimeoutInput.value,
            10
        );

        if (
            !Number.isInteger(idleMinutes) ||
            idleMinutes < 1
        ) {

            securityStatus.textContent =
                "Idle timeout must be at least 1 minute.";

            return;
        }

        if (
            !Number.isInteger(absoluteMinutes) ||
            absoluteMinutes < 1
        ) {

            securityStatus.textContent =
                "Absolute timeout must be at least 1 minute.";

            return;
        }

        if (absoluteMinutes < idleMinutes) {

            securityStatus.textContent =
                "Absolute timeout must be greater than idle timeout.";

            return;
        }

        const res = await fetch("/api/system/security", {

            method: "POST",

            headers: {
                "Content-Type": "application/json"
            },

            body: JSON.stringify({
                idle_timeout: idleMinutes * 60,
                absolute_timeout: absoluteMinutes * 60
            })

        });

        if (res.status === 401) {
            location.href = "/login";
            return;
        }

        const data = await res.json();

        if (data.success) {

            securityStatus.textContent =
                "✓ Security settings updated";

        } else {

            securityStatus.textContent =
                data.message ||
                "Failed to save settings.";

        }

    },


    async savePing() {

        pingStatus.textContent = "Saving...";

        const enabled =
            pingEnabledInput.value === "true";

        const interval =
            parseInt(
                pingIntervalInput.value,
                10
            );

        const destination =
            pingDestinationInput.value.trim();

        if (![5, 10, 30, 60].includes(interval)) {

            pingStatus.textContent =
                "Invalid ping interval.";

            return;
        }

        if (!destination) {

            pingStatus.textContent =
                "Ping destination cannot be empty.";

            return;
        }

        const res = await fetch("/api/system/ping", {

            method: "POST",

            headers: {
                "Content-Type": "application/json"
            },

            body: JSON.stringify({
                enabled: enabled,
                interval: interval,
                destination: destination
            })

        });

        if (res.status === 401) {
            location.href = "/login";
            return;
        }

        const data = await res.json();

        if (data.success) {

            pingStatus.textContent =
                "✓ Ping settings updated";

            this.updatePingIntervalState();

            if (window.Header?.loadPingConfig) {
                Header.loadPingConfig();
            }

        } else {

            pingStatus.textContent =
                data.message ||
                "Failed to save ping settings.";

        }

    },


    async changePassword() {

        passwordStatus.textContent =
            "Changing password...";

        const currentPassword =
            currentPasswordInput.value;

        const newPassword =
            newPasswordInput.value;

        const confirmPassword =
            confirmPasswordInput.value;


        if (
            !currentPassword ||
            !newPassword ||
            !confirmPassword
        ) {

            passwordStatus.textContent =
                "Please fill in all password fields.";

            return;
        }


        if (newPassword !== confirmPassword) {

            passwordStatus.textContent =
                "New passwords do not match.";

            return;
        }


        const res = await fetch(
            "/api/system/password",
            {

                method: "POST",

                headers: {
                    "Content-Type":
                        "application/json"
                },

                body: JSON.stringify({
                    current_password:
                        currentPassword,

                    new_password:
                        newPassword
                })

            }
        );


        if (res.status === 401) {
            location.href = "/login";
            return;
        }


        const data = await res.json();


        if (data.success) {

            passwordStatus.textContent =
                "✓ Password changed. Please log in again.";

            currentPasswordInput.value = "";
            newPasswordInput.value = "";
            confirmPasswordInput.value = "";

            setTimeout(() => {
                location.href = "/login";
            }, 1500);

        } else {

            passwordStatus.textContent =
                data.message ||
                "Failed to change password.";

        }

    },


    // ------------------------------------------------------------
    // Backup & Restore
    // ------------------------------------------------------------

    backupText: null,
    backupPassword: null,
    backupSummary: null,


    escape(value) {

        return ChardsoftSelect.escape(value);

    },


    /** Asks for an optional backup password; null when cancelled. */
    async askBackupPassword() {

        const body = document.createElement("div");

        body.innerHTML = `
            <label class="modal-field">
                <span>Password</span>
                <input id="backupPasswordInput" class="setting-input" type="password" autocomplete="new-password">
            </label>
            <label class="modal-field">
                <span>Repeat password</span>
                <input id="backupPasswordRepeat" class="setting-input" type="password" autocomplete="new-password">
            </label>
            <p class="modal-note">
                Recommended: the file then needs this password to restore.
                Leave both empty to download it unencrypted, with every
                password and VPN key readable in the file.
            </p>
        `;

        const confirmed = await ChardsoftModal.confirm({
            title: "Download backup",
            subtitle: "Encrypt the backup with a password (at least 8 characters).",
            body,
            confirmText: "Download"
        });

        if (!confirmed) return null;

        const password = body.querySelector("#backupPasswordInput").value;

        if (password !== body.querySelector("#backupPasswordRepeat").value) {
            backupStatus.textContent = "The passwords do not match. Nothing was downloaded.";
            return null;
        }

        if (password && password.length < 8) {
            backupStatus.textContent = "The password needs at least 8 characters. Nothing was downloaded.";
            return null;
        }

        return password;

    },


    async downloadBackup() {

        const password = await this.askBackupPassword();

        if (password === null) return;

        backupStatus.textContent = password ? "Preparing encrypted backup..." : "Preparing backup...";

        const res = await fetch("/api/backup/export", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password })
        });

        if (res.status === 401) {
            location.href = "/login";
            return;
        }

        if (!res.ok) {
            let message = "The backup could not be created.";
            try { message = (await res.json()).message || message; } catch {}
            backupStatus.textContent = message;
            return;
        }

        const disposition = res.headers.get("Content-Disposition") || "";
        const name = disposition.match(/filename="([^"]+)"/)?.[1]
            || "chardsoft-router-backup.json";

        const link = document.createElement("a");

        link.href = URL.createObjectURL(await res.blob());
        link.download = name;
        link.click();

        URL.revokeObjectURL(link.href);

        backupStatus.textContent = password
            ? `✓ Downloaded ${name} (encrypted)`
            : `✓ Downloaded ${name} (not encrypted: keep it somewhere safe)`;

    },


    async postBackup(url, body) {

        const res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        });

        if (res.status === 401) {
            location.href = "/login";
            return null;
        }

        if (res.status === 413) {
            return { success: false, message: "The file is too large to be a backup." };
        }

        try {
            return await res.json();
        } catch {
            return { success: false, message: "Unexpected server response." };
        }

    },


    /** The password of an encrypted backup; null when cancelled. */
    async askRestorePassword(fileName, retry) {

        const body = document.createElement("div");

        body.innerHTML = `
            <label class="modal-field">
                <span>Password</span>
                <input id="restorePasswordInput" class="setting-input" type="password" autocomplete="off">
            </label>
        `;

        const shown = ChardsoftModal.confirm({
            title: "Encrypted backup",
            subtitle: retry
                ? `Wrong password for ${fileName}. Try again.`
                : `${fileName} is encrypted. Enter the password it was downloaded with.`,
            body,
            confirmText: "Open"
        });

        body.querySelector("input").focus();

        const confirmed = await shown;

        return confirmed ? body.querySelector("input").value : null;

    },


    readBackupFile(file) {

        if (!file) return;

        backupFileInput.value = "";
        backupResults.classList.add("hidden");
        backupSummary.classList.add("hidden");
        backupStatus.textContent = `Reading ${file.name}...`;

        const reader = new FileReader();

        reader.onload = () => this.inspectBackup(reader.result, file.name);
        reader.onerror = () => {
            backupStatus.textContent = "The file could not be read.";
        };

        reader.readAsText(file);

    },


    async inspectBackup(text, fileName, password = null) {

        const data = await this.postBackup("/api/backup/inspect", { backup: text, password });

        if (!data) return;

        if (!data.success && data.encrypted) {

            backupStatus.textContent = data.message;

            const entered = await this.askRestorePassword(fileName, password !== null);

            if (entered !== null) this.inspectBackup(text, fileName, entered);

            return;
        }

        if (!data.success) {
            backupStatus.textContent = data.message;
            return;
        }

        this.backupText = text;
        this.backupPassword = password;
        this.backupSummary = data;

        const created = data.created
            ? new Date(data.created * 1000).toLocaleString()
            : "an unknown date";

        backupInfo.textContent =
            `${fileName}: backup of ${data.hostname || "a router"} from ${created}.`;

        backupAreas.innerHTML = "";

        data.areas.forEach(area => {

            const label = document.createElement("label");
            label.className = `check-item backup-area ${area.status}`;

            const note = {
                ok: "",
                unchanged: "same as now",
                invalid: area.message
            }[area.status];

            label.innerHTML = `
                <input type="checkbox" value="${this.escape(area.name)}"
                    ${area.status === "ok" ? "checked" : ""}
                    ${area.status === "invalid" ? "disabled" : ""}>
                <span>${this.escape(area.label)}</span>
                ${note ? `<small>${this.escape(note)}</small>` : ""}
            `;

            backupAreas.appendChild(label);

        });

        backupCerts.textContent =
            data.certificates === "ok"
                ? "Includes the OpenVPN certificate authority; restoring OpenVPN replaces this router's certificates."
                : data.certificates
                    ? `OpenVPN certificates cannot be used: ${data.certificates}`
                    : "";

        backupSummary.classList.remove("hidden");
        backupStatus.textContent = "Choose what to restore.";

    },


    async restoreBackup() {

        const areas = [...backupAreas.querySelectorAll("input:checked")]
            .map(input => input.value);

        if (!areas.length) {
            backupStatus.textContent = "Select at least one area to restore.";
            return;
        }

        const labels = this.backupSummary.areas
            .filter(a => areas.includes(a.name))
            .map(a => a.label);

        const body = document.createElement("div");

        const warnings = [];

        if (areas.includes("users")) {
            warnings.push("The login password changes to the one in the backup.");
        }

        if (areas.includes("openvpn") && this.backupSummary.certificates === "ok") {
            warnings.push("OpenVPN devices set up on this router stop working; use the profiles from the backup.");
        }

        warnings.push("Network, firewall, WiFi and similar settings wait for you to keep them, or revert automatically.");

        body.innerHTML = `
            <p class="modal-note">${labels.map(l => this.escape(l)).join(", ")}</p>
            <ul class="modal-list">
                ${warnings.map(w => `<li>${this.escape(w)}</li>`).join("")}
            </ul>
        `;

        const confirmed = await ChardsoftModal.confirm({
            title: "Restore backup",
            subtitle: "These settings are replaced with the ones from the backup:",
            body: body,
            confirmText: "Restore"
        });

        if (!confirmed) return;

        backupRestore.disabled = true;
        backupStatus.textContent = "Restoring...";

        const data = await this.postBackup("/api/backup/restore", {
            backup: this.backupText,
            password: this.backupPassword,
            areas: areas
        });

        backupRestore.disabled = false;

        if (!data) return;

        backupStatus.textContent = data.message;

        backupResults.innerHTML = "";

        (data.areas || []).forEach(area => {

            const item = document.createElement("li");
            item.className = area.success ? "ok" : "failed";
            item.textContent = `${area.success ? "✓" : "✕"} ${area.label}: ${area.message}`;
            backupResults.appendChild(item);

        });

        backupResults.classList.toggle("hidden", !(data.areas || []).length);
        backupSummary.classList.add("hidden");

        this.backupText = null;
        this.backupPassword = null;

        // Restored hostname, timeouts and ping settings show up here.
        this.load();

    },


    // ------------------------------------------------------------
    // Reboot
    // ------------------------------------------------------------

    async reboot() {

        // A reboot reverts changes that wait for confirmation.
        const pending = window.ChardsoftApply?.pending;

        const confirmed = await ChardsoftModal.confirm({
            title: "Reboot the router?",
            subtitle: pending
                ? "Unconfirmed changes (" + pending.areas.map(a => a.label).join(", ") + ") will be reverted. Devices lose their connection for about a minute."
                : "Devices lose their connection for about a minute.",
            confirmText: "Reboot"
        });

        if (!confirmed) return;

        rebootButton.disabled = true;
        rebootStatus.textContent = "Rebooting...";

        let data = {};

        try {

            const res = await fetch("/api/system/reboot", { method: "POST" });

            data = await res.json();

            if (!res.ok || !data.success) {
                rebootButton.disabled = false;
                rebootStatus.textContent = data.message || "The router could not be rebooted.";
                return;
            }

        } catch {
            // The router may already be going down.
        }

        this.waitForRouter();

    },

    // ------------------------------------------------------------
    // Web shell
    // ------------------------------------------------------------

    shellEnabled: false,

    async loadShell() {

        try {

            const res = await fetch("/api/shell/status");

            if (!res.ok) return;

            this.shellEnabled = (await res.json()).enabled;

        } catch {
            return;
        }

        shellToggle.disabled = false;
        shellToggle.textContent = this.shellEnabled ? "Turn off" : "Turn on…";
        shellToggle.classList.toggle("danger-btn", this.shellEnabled);

        if (!shellSettingStatus.dataset.keep) {
            shellSettingStatus.textContent = this.shellEnabled ? "The web shell is on." : "The web shell is off.";
        }

        delete shellSettingStatus.dataset.keep;

    },

    async toggleShell() {

        const enable = !this.shellEnabled;

        const body = document.createElement("div");

        body.innerHTML = `
            <label class="modal-field">
                <span>Your password</span>
                <input id="shellSettingPassword" class="setting-input" type="password" autocomplete="current-password">
            </label>
        `;

        const confirmed = await ChardsoftModal.confirm({
            title: enable ? "Turn on the web shell?" : "Turn off the web shell?",
            subtitle: enable
                ? "Anyone who signs in to this dashboard and knows your password can then run any command on the router."
                : "Open shells keep running until they are closed or time out.",
            body,
            confirmText: enable ? "Turn on" : "Turn off"
        });

        if (!confirmed) return;

        shellToggle.disabled = true;
        shellSettingStatus.textContent = "Saving...";

        let data = {};

        try {

            const res = await fetch("/api/shell/settings", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    enabled: enable,
                    password: body.querySelector("#shellSettingPassword").value
                })
            });

            data = await res.json();

        } catch {
            data = { message: "The router did not answer." };
        }

        shellSettingStatus.textContent = data.success
            ? (enable ? "✓ Web shell turned on" : "✓ Web shell turned off")
            : data.message || "Could not be saved.";

        shellSettingStatus.dataset.keep = "1";

        await this.loadShell();

    },


    // ------------------------------------------------------------
    // Factory reset
    // ------------------------------------------------------------

    async factoryReset() {

        const body = document.createElement("div");

        body.innerHTML = `
            <p class="modal-note">
                All settings, the admin account, VPN keys and certificates are
                erased. Wi-Fi, DHCP, DNS and VPNs turn off, then the router
                reboots and opens the setup Wi-Fi. This cannot be undone.
            </p>
            <label class="modal-field">
                <span>Your password</span>
                <input id="factoryResetPassword" class="setting-input" type="password" autocomplete="current-password">
            </label>
        `;

        const confirmed = await ChardsoftModal.confirm({
            title: "Erase everything?",
            subtitle: "Download a backup first if you may want your settings back.",
            body,
            confirmText: "Erase and reboot"
        });

        if (!confirmed) return;

        const password = body.querySelector("#factoryResetPassword").value;

        factoryResetButton.disabled = true;
        rebootButton.disabled = true;
        factoryResetStatus.textContent = "Erasing settings...";

        let data = {};

        try {

            const res = await fetch("/api/system/factory-reset", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ password })
            });

            data = await res.json();

            if (!res.ok || !data.success) {
                factoryResetButton.disabled = false;
                rebootButton.disabled = false;
                factoryResetStatus.textContent = data.message || "The factory reset could not start.";
                return;
            }

        } catch {
            // The router may already be going down.
        }

        factoryResetStatus.textContent = data.message || "Erasing settings and rebooting...";

        // Afterwards every page leads to /setup.
        this.waitForRouter(factoryResetStatus);

    },


    /** Waits until the router is gone and back, then reloads. */
    async waitForRouter(status = rebootStatus) {

        const started = Date.now();
        let wentDown = false;

        const sleep = ms => new Promise(r => setTimeout(r, ms));

        while (Date.now() - started < 5 * 60 * 1000) {

            await sleep(3000);

            const seconds = Math.round((Date.now() - started) / 1000);

            let up = false;

            try {
                const res = await fetch("/login", { cache: "no-store" });
                up = res.ok || res.redirected;
            } catch {
                up = false;
            }

            if (!up) wentDown = true;

            // Back after going down (or still up after 40 s: reloaded fast).
            if (up && (wentDown || seconds > 40)) {
                status.textContent = "The router is back. Reloading...";
                await sleep(1000);
                location.href = "/";
                return;
            }

            status.textContent = wentDown
                ? `Rebooting... waiting for the router (${seconds} s)`
                : `Rebooting... (${seconds} s)`;

        }

        status.textContent =
            "The router has not come back after 5 minutes. Check its power and network, then reload this page.";

    },


    cancelRestore() {

        this.backupText = null;
        this.backupPassword = null;
        this.backupSummary = null;

        backupSummary.classList.add("hidden");
        backupStatus.textContent = "";

    },


    // ------------------------------------------------------------
    // Updates
    // ------------------------------------------------------------

    updateTimer: null,
    updateInfo: null,


    async loadUpdate() {

        const res = await fetch("/api/system/update");

        if (!res.ok) return;

        const info = await res.json();

        this.updateInfo = info;

        updateVersion.textContent = info.commit
            ? `${info.version} (${info.commit.slice(0, 7)})`
            : info.version;

        updateBranch.textContent = info.branch ? `· branch ${info.branch}` : "";

        updateCheck.disabled = !info.managed;

        if (!info.managed) {
            updateStatus.textContent = info.reason || "";
        }

        if (info.status?.state === "running") {
            this.followUpdate();
        } else if (info.status && info.status.state !== "running") {
            this.showUpdateStatus(info.status);
        }

    },


    async checkUpdate() {

        updateCheck.disabled = true;
        updateStart.classList.add("hidden");
        updateLatest.classList.add("hidden");
        updateStatus.textContent = "Asking GitHub...";

        let data;

        try {
            const res = await fetch("/api/system/update/check", { method: "POST" });
            data = await res.json();
        } catch {
            data = { success: false, message: "The router did not answer." };
        }

        updateCheck.disabled = false;

        if (!data.success) {
            updateStatus.textContent = data.message;
            return;
        }

        if (!data.update_available) {
            updateStatus.textContent = "✓ Up to date.";
            return;
        }

        const date = data.latest_date ? new Date(data.latest_date).toLocaleDateString() : "";

        updateLatest.textContent =
            `New: ${data.latest.slice(0, 7)}${date ? ` from ${date}` : ""}` +
            (data.latest_message ? `: ${data.latest_message}` : "");

        updateLatest.classList.remove("hidden");
        updateStatus.textContent = "";

        updateStart.classList.toggle("hidden", !data.can_update);

    },


    async startUpdate() {

        const body = document.createElement("div");

        body.innerHTML = `
            <label class="modal-field">
                <span>Your password</span>
                <input id="updatePasswordInput" class="setting-input" type="password" autocomplete="current-password">
            </label>
            <p class="modal-note">
                The settings are backed up first. The dashboard restarts and
                is away for a few minutes. If the new version does not start,
                the current one comes back by itself.
            </p>
        `;

        const confirmed = await ChardsoftModal.confirm({
            title: "Update now",
            subtitle: "Install the new version from GitHub?",
            body,
            confirmText: "Update"
        });

        if (!confirmed) return;

        updateStart.disabled = true;
        updateStatus.textContent = "Starting the update...";

        let data;

        try {
            const res = await fetch("/api/system/update/start", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ password: body.querySelector("input").value })
            });
            data = await res.json();
        } catch {
            data = { success: false, message: "The router did not answer." };
        }

        updateStart.disabled = false;

        if (!data.success) {
            updateStatus.textContent = data.message;
            return;
        }

        updateStart.classList.add("hidden");
        this.followUpdate();

    },


    /** Polls the update's progress; the app restarts in the middle. */
    followUpdate() {

        clearInterval(this.updateTimer);

        updateCheck.disabled = true;

        this.updateTimer = setInterval(async () => {

            let status = null;

            try {
                const res = await fetch("/api/system/update/status");
                if (res.ok) status = (await res.json()).status;
            } catch {
                updateStatus.textContent = "Restarting... the dashboard is back in a moment.";
                return;
            }

            if (!status) return;

            this.showUpdateStatus(status);

            if (status.state !== "running") {
                clearInterval(this.updateTimer);
                updateCheck.disabled = false;
                // The version shown is the one now running.
                if (status.state === "done") setTimeout(() => location.reload(), 1500);
            }

        }, 3000);

    },


    showUpdateStatus(status) {

        const prefix = { done: "✓ ", failed: "✕ ", "rolled-back": "✕ " }[status.state] || "";

        updateStatus.textContent = prefix + (status.message || "");

    },


    init() {

        // Same dropdown look as every other page.
        ChardsoftSelect.enhance(pingEnabledInput);
        ChardsoftSelect.enhance(pingIntervalInput);

        this.load();

        updateCheck.onclick = () => this.checkUpdate();
        updateStart.onclick = () => this.startUpdate();
        this.loadUpdate();

        backupDownload.onclick = () => this.downloadBackup();
        backupChoose.onclick = () => backupFileInput.click();
        backupFileInput.onchange = () => this.readBackupFile(backupFileInput.files[0]);
        backupRestore.onclick = () => this.restoreBackup();
        backupCancel.onclick = () => this.cancelRestore();


        saveHostname.onclick = () => {
            this.save();
        };


        hostnameInput.addEventListener(
            "keydown",
            (e) => {

                if (e.key === "Enter") {
                    e.preventDefault();
                    this.save();
                }

            }
        );


        saveSecurity.onclick = () => {
            this.saveSecurity();
        };


        pingEnabledInput.addEventListener(
            "change",
            () => {
                this.updatePingIntervalState();
            }
        );


        savePing.onclick = () => {
            this.savePing();
        };


        changePassword.onclick = () => {
            this.changePassword();
        };

        rebootButton.onclick = () => this.reboot();

        factoryResetButton.onclick = () => this.factoryReset();

        shellToggle.onclick = () => this.toggleShell();

        this.loadShell();

    },


    destroy() {

        clearInterval(this.updateTimer);

    }

};