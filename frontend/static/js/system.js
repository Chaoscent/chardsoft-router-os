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
            confirmText: "Erase and reboot",
            // Erasing everything stays a deliberate click.
            enter: false
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

        this.showUninstallCommands(info.dir);

        // Release channel: only for the installer's copy on dev/* or release/*.
        const channels = Boolean(info.managed && info.channel);

        updateChannelGroup.classList.toggle("hidden", !channels);
        updateChannelHint.classList.toggle("hidden", !channels);

        if (channels) {
            updateChannelInput.value = info.channel;
            ChardsoftSelect.refresh(updateChannelInput);
        }

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
            updateStatus.textContent = data.note || "✓ Up to date.";
            return;
        }

        const date = data.latest_date ? new Date(data.latest_date).toLocaleDateString() : "";

        const version = data.latest_version && data.latest_version !== data.version
            ? `${data.latest_version} ` : "";

        updateLatest.textContent =
            `New: ${version}${data.latest.slice(0, 7)}${date ? ` from ${date}` : ""}` +
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


    /** The uninstall commands for the folder the router is installed in. */
    showUninstallCommands(dir) {

        const folder = dir || "/opt/chardsoft-router-os";

        document.querySelectorAll("[data-uninstall-command]").forEach(pre => {
            const option = pre.dataset.uninstallCommand;
            pre.textContent = `sudo ${folder}/uninstall.sh${option ? ` ${option}` : ""}`;
        });

    },


    async copyCommand(button) {

        const pre = button.parentElement.querySelector("pre");

        try {
            await navigator.clipboard.writeText(pre.textContent);
            uninstallStatus.textContent = "✓ Copied.";
        } catch {
            // No clipboard over plain HTTP: select it for Ctrl+C.
            const range = document.createRange();
            range.selectNodeContents(pre);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
            uninstallStatus.textContent = "Selected: press Ctrl+C to copy.";
        }

    },


    async changeChannel() {

        const channel = updateChannelInput.value;
        const previous = this.updateInfo?.channel;

        const back = () => {
            updateChannelInput.value = previous;
            ChardsoftSelect.refresh(updateChannelInput);
        };

        if (!previous || channel === previous) return;

        const body = document.createElement("div");

        body.innerHTML = `
            <label class="modal-field">
                <span>Your password</span>
                <input class="setting-input" type="password" autocomplete="current-password">
            </label>
            <p class="modal-note">
                ${channel === "stable"
                    ? "From now on the router only gets finished releases. If Stable is older than the version installed now, nothing changes until Stable is newer."
                    : "From now on the router gets every change as soon as it is published. These are tested less and can break things."}
                Nothing is installed until you choose Update now.
            </p>
        `;

        const confirmed = await ChardsoftModal.confirm({
            title: channel === "stable" ? "Switch to Stable" : "Switch to Development",
            subtitle: "Change where updates come from?",
            body,
            confirmText: "Switch"
        });

        if (!confirmed) return back();

        updateChannelInput.disabled = true;
        updateStatus.textContent = "Changing the channel...";

        let data;

        try {
            const res = await fetch("/api/system/update/channel", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ channel, password: body.querySelector("input").value })
            });
            data = await res.json();
        } catch {
            data = { success: false, message: "The router did not answer." };
        }

        updateChannelInput.disabled = false;

        if (!data.success) {
            back();
            updateStatus.textContent = data.message;
            return;
        }

        await this.loadUpdate();
        await this.checkUpdate();

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


    // ------------------------------------------------------------
    // Account: two-step login, SSH, signed-in devices
    // ------------------------------------------------------------

    async postJson(url, body) {

        try {
            const res = await fetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body ?? {})
            });
            return await res.json();
        } catch {
            return { success: false, message: "The router did not answer." };
        }

    },


    /** The router password for a change, or null when cancelled. */
    async askPassword({ title, subtitle, note, confirmText }) {

        const body = document.createElement("div");

        body.innerHTML = `
            <label class="modal-field">
                <span>Your password</span>
                <input class="setting-input" type="password" autocomplete="current-password">
            </label>
        `;

        if (note) {
            const p = document.createElement("p");
            p.className = "modal-note";
            p.textContent = note;
            body.appendChild(p);
        }

        const confirmed = await ChardsoftModal.confirm({ title, subtitle, body, confirmText });

        return confirmed ? body.querySelector("input").value : null;

    },


    // Two-step login --------------------------------------------------

    async loadTwoFactor() {

        let data;

        try {
            data = await (await fetch("/api/account/2fa")).json();
        } catch {
            return;
        }

        this.twoFactor = data;

        twoFactorState.textContent = data.enabled ? "On" : "Off";
        twoFactorRecovery.textContent = data.enabled
            ? `· ${data.recovery_left} recovery code${data.recovery_left === 1 ? "" : "s"} left`
            : "";

        twoFactorToggle.textContent = data.enabled ? "Turn off" : "Turn on";
        twoFactorToggle.classList.toggle("danger-btn", data.enabled);
        twoFactorRecoveryButton.classList.toggle("hidden", !data.enabled);

    },


    showRecoveryCodes(codes, title) {

        const body = document.createElement("div");

        const note = document.createElement("p");
        note.className = "modal-note";
        note.textContent = "Each code signs you in once instead of the app code, e.g. when your phone is lost. Write them down or save them now: they are not shown again.";

        const list = document.createElement("div");
        list.className = "recovery-codes";

        codes.forEach(code => {
            const item = document.createElement("span");
            item.textContent = code;
            list.appendChild(item);
        });

        // Saved as a text file on this device (made here, nothing is sent).
        const download = document.createElement("button");
        download.type = "button";
        download.className = "secondary-btn";
        download.textContent = "Download as .txt";

        download.onclick = () => {

            const host = location.hostname || "router";
            const text = [
                "Chardsoft Router OS: recovery codes for two-step login",
                `Router: ${host}`,
                `Made: ${new Date().toLocaleString()}`,
                "",
                "Each code signs you in once instead of the code from the app.",
                "New codes from the System page make these stop working.",
                "",
                ...codes,
                ""
            ].join("\r\n");

            const link = document.createElement("a");
            link.href = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
            link.download = `chardsoft-recovery-codes-${host}.txt`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(link.href), 1000);

            download.textContent = "✓ Downloaded";

        };

        const actions = document.createElement("div");
        actions.className = "setting-row";
        actions.appendChild(download);

        body.append(list, actions, note);

        return ChardsoftModal.notice({ title, subtitle: "Your recovery codes", body, confirmText: "I saved them" });

    },


    async toggleTwoFactor() {

        twoFactorStatus.textContent = "";

        if (this.twoFactor?.enabled) {

            const password = await this.askPassword({
                title: "Turn off two-step login",
                subtitle: "Signing in needs only the password again.",
                confirmText: "Turn off"
            });

            if (password === null) return;

            const data = await this.postJson("/api/account/2fa/disable", { password });

            twoFactorStatus.textContent = data.success ? "✓ Two-step login is off." : data.message;
            this.loadTwoFactor();
            return;

        }

        const password = await this.askPassword({
            title: "Turn on two-step login",
            subtitle: "Confirm with your password first.",
            confirmText: "Continue"
        });

        if (password === null) return;

        const setup = await this.postJson("/api/account/2fa/setup", { password });

        if (!setup.success) {
            twoFactorStatus.textContent = setup.message;
            return;
        }

        let problem = "";

        // Until a code from the app works (or the user cancels).
        for (;;) {

            const body = document.createElement("div");

            // The router's own SVG (segno), never page input.
            const qr = document.createElement("div");
            qr.className = "two-factor-qr";
            qr.innerHTML = setup.qr;
            qr.querySelector("svg")?.setAttribute("role", "img");
            qr.querySelector("svg")?.setAttribute("aria-label", "QR code for the authenticator app");

            const manual = document.createElement("p");
            manual.className = "modal-note";
            manual.textContent = `No camera? Enter this key in the app: ${setup.secret.match(/.{1,4}/g).join(" ")}`;

            const field = document.createElement("label");
            field.className = "modal-field";
            field.innerHTML = `
                <span>Code from the app</span>
                <input class="setting-input" inputmode="numeric" autocomplete="one-time-code" maxlength="6">
            `;

            body.append(qr, manual, field);

            if (problem) {
                const error = document.createElement("p");
                error.className = "modal-note";
                error.textContent = problem;
                body.appendChild(error);
            }

            const confirmed = await ChardsoftModal.confirm({
                title: "Scan with your authenticator app",
                subtitle: "Then type the 6-digit code it shows.",
                body,
                confirmText: "Turn on"
            });

            if (!confirmed) {
                twoFactorStatus.textContent = "Two-step login stays off.";
                return;
            }

            const data = await this.postJson("/api/account/2fa/enable", { code: field.querySelector("input").value });

            if (data.success) {
                await this.showRecoveryCodes(data.recovery_codes, "Two-step login is on");
                twoFactorStatus.textContent = "✓ Two-step login is on.";
                this.loadTwoFactor();
                return;
            }

            problem = data.message;

        }

    },


    async newRecoveryCodes() {

        const password = await this.askPassword({
            title: "New recovery codes",
            subtitle: "The old recovery codes stop working.",
            confirmText: "Make new codes"
        });

        if (password === null) return;

        const data = await this.postJson("/api/account/2fa/recovery", { password });

        if (!data.success) {
            twoFactorStatus.textContent = data.message;
            return;
        }

        await this.showRecoveryCodes(data.recovery_codes, "New recovery codes");
        this.loadTwoFactor();

    },


    // SSH ---------------------------------------------------------------

    async loadSsh() {

        try {
            this.renderSsh(await (await fetch("/api/account/ssh")).json());
        } catch {
            sshState.textContent = "unknown";
        }

    },


    renderSsh(status) {

        this.ssh = status;

        if (!status.installed) {
            sshState.textContent = "not installed";
            sshDetails.textContent = "";
            [sshAddKey, sshPasswordToggle, sshToggle].forEach(b => b.disabled = true);
            return;
        }

        sshState.textContent = status.active ? "On" : "Off";
        sshDetails.textContent = `· port ${status.port}` + (status.active && !status.enabled ? " · not at boot" : "");

        sshPasswordState.textContent = status.password_login === null ? "unknown"
            : status.password_login ? "on" : "off (keys only)";

        sshAccount.textContent = status.account;

        sshToggle.textContent = status.active ? "Turn SSH off" : "Turn SSH on";
        sshToggle.classList.toggle("danger-btn", status.active);

        sshPasswordToggle.textContent = status.password_login === false
            ? "Turn password login on" : "Turn password login off";

        sshKeys.innerHTML = "";

        if (!status.keys.length) {
            const empty = document.createElement("li");
            empty.textContent = "No keys yet: SSH only works with the password.";
            sshKeys.appendChild(empty);
        }

        status.keys.forEach(key => {

            const item = document.createElement("li");
            const text = document.createElement("div");
            const name = document.createElement("span");
            const print = document.createElement("small");
            const remove = document.createElement("button");

            name.textContent = key.comment || key.type;
            print.textContent = `${key.type} · ${key.fingerprint}`;

            remove.className = "secondary-btn";
            remove.textContent = "Remove";
            remove.onclick = () => this.sshChange("remove-key", {
                title: `Remove the key ${key.comment || ""}`.trim(),
                subtitle: "It cannot sign in over SSH any more.",
                confirmText: "Remove"
            }, { fingerprint: key.fingerprint });

            text.append(name, print);
            item.append(text, remove);
            sshKeys.appendChild(item);

        });

    },


    /** One SSH change with the password; shows the new state. */
    async sshChange(action, dialog, extra = {}) {

        sshStatus.textContent = "";

        const password = await this.askPassword(dialog);

        if (password === null) return;

        sshStatus.textContent = "Working...";

        const data = await this.postJson("/api/account/ssh", { action, password, ...extra });

        sshStatus.textContent = data.success ? `✓ ${data.message}` : data.message;

        if (data.status) this.renderSsh(data.status);

    },


    async addSshKey() {

        sshStatus.textContent = "";

        const body = document.createElement("div");

        body.innerHTML = `
            <label class="modal-field">
                <span>Public key (the line from the .pub file)</span>
                <textarea class="setting-input" rows="4" spellcheck="false" placeholder="ssh-ed25519 AAAA... laptop"></textarea>
            </label>
            <label class="modal-field">
                <span>Your password</span>
                <input class="setting-input" type="password" autocomplete="current-password">
            </label>
        `;

        const confirmed = await ChardsoftModal.confirm({
            title: "Add an SSH key",
            subtitle: `It can sign in as ${this.ssh?.account || "the router's account"}.`,
            body,
            confirmText: "Add key"
        });

        if (!confirmed) return;

        sshStatus.textContent = "Working...";

        const data = await this.postJson("/api/account/ssh", {
            action: "add-key",
            key: body.querySelector("textarea").value,
            password: body.querySelector("input").value
        });

        sshStatus.textContent = data.success ? `✓ ${data.message}` : data.message;

        if (data.status) this.renderSsh(data.status);

    },


    toggleSsh() {

        const on = this.ssh?.active;

        return this.sshChange(on ? "disable" : "enable", on ? {
            title: "Turn SSH off",
            subtitle: "No new SSH logins, also not after a reboot.",
            note: "Open SSH sessions stay until they end. The dashboard keeps working, and you can turn SSH on here again.",
            confirmText: "Turn off"
        } : {
            title: "Turn SSH on",
            subtitle: "SSH runs now and after every reboot.",
            confirmText: "Turn on"
        });

    },


    togglePasswordLogin() {

        const off = this.ssh?.password_login !== false;

        return this.sshChange(off ? "password-off" : "password-on", off ? {
            title: "Keys only",
            subtitle: "SSH stops taking passwords; only the keys listed here get in.",
            note: "Make sure your key works first. Open SSH sessions stay.",
            confirmText: "Turn password login off"
        } : {
            title: "Turn password login on",
            subtitle: "SSH takes the account's password again, besides keys.",
            confirmText: "Turn on"
        });

    },


    // Signed-in devices ---------------------------------------------------

    async loadDevices() {

        try {
            this.devices = (await (await fetch("/api/account/devices")).json()).devices || [];
        } catch {
            return;
        }

        devicesCount.textContent = this.devices.length;
        devicesSignOutOthers.disabled = this.devices.length < 2;

    },


    ago(seconds) {

        const minutes = Math.max(0, Math.round((Date.now() / 1000 - seconds) / 60));

        if (minutes < 1) return "just now";
        if (minutes < 60) return `${minutes} min ago`;

        return `${Math.round(minutes / 60)} h ago`;

    },


    async showDevices() {

        await this.loadDevices();

        const list = document.createElement("ul");
        list.className = "device-list";

        (this.devices || []).forEach(device => {

            const item = document.createElement("li");
            const text = document.createElement("div");
            const name = document.createElement("span");
            const info = document.createElement("small");

            name.textContent = device.device + (device.current ? " (this device)" : "");
            info.textContent = `${device.address || "unknown address"} · signed in ${new Date(device.created * 1000).toLocaleString()} · active ${this.ago(device.last_seen)}`;

            text.append(name, info);
            item.appendChild(text);

            if (!device.current) {

                const button = document.createElement("button");
                button.className = "secondary-btn";
                button.textContent = "Sign out";

                button.onclick = async () => {
                    button.disabled = true;
                    const data = await this.postJson("/api/account/devices/sign-out", { id: device.id });
                    if (data.success) item.remove();
                    else { button.disabled = false; info.textContent = data.message; }
                    this.loadDevices();
                };

                item.appendChild(button);
            }

            list.appendChild(item);

        });

        await ChardsoftModal.notice({
            title: "Signed-in devices",
            subtitle: "Browsers signed in to this dashboard. Devices sign out by themselves after the session times.",
            body: list,
            confirmText: "Close"
        });

    },


    async signOutOthers() {

        const confirmed = await ChardsoftModal.confirm({
            title: "Sign out everywhere else",
            subtitle: "Every other browser has to sign in again. This one stays signed in.",
            confirmText: "Sign out the others"
        });

        if (!confirmed) return;

        const data = await this.postJson("/api/account/devices/sign-out-others");

        devicesStatus.textContent = data.success ? `✓ ${data.message}` : data.message;

        this.loadDevices();

    },


    init() {

        // Same dropdown look as every other page.
        ChardsoftSelect.enhance(pingEnabledInput);
        ChardsoftSelect.enhance(pingIntervalInput);

        this.load();

        updateCheck.onclick = () => this.checkUpdate();
        updateStart.onclick = () => this.startUpdate();
        this.showUninstallCommands(null);
        document.querySelectorAll("[data-copy-command]").forEach(button => {
            button.onclick = () => this.copyCommand(button);
        });
        ChardsoftSelect.enhance(updateChannelInput);
        updateChannelInput.addEventListener("change", () => this.changeChannel());
        this.loadUpdate();

        twoFactorToggle.onclick = () => this.toggleTwoFactor();
        twoFactorRecoveryButton.onclick = () => this.newRecoveryCodes();
        sshAddKey.onclick = () => this.addSshKey();
        sshPasswordToggle.onclick = () => this.togglePasswordLogin();
        sshToggle.onclick = () => this.toggleSsh();
        devicesShow.onclick = () => this.showDevices();
        devicesSignOutOthers.onclick = () => this.signOutOthers();

        this.loadTwoFactor();
        this.loadSsh();
        this.loadDevices();

        backupDownload.onclick = () => this.downloadBackup();
        backupChoose.onclick = () => backupFileInput.click();
        backupFileInput.onchange = () => this.readBackupFile(backupFileInput.files[0]);
        backupRestore.onclick = () => this.restoreBackup();
        backupCancel.onclick = () => this.cancelRestore();


        saveHostname.onclick = () => {
            this.save();
        };


        // Enter in the router's own password fields: on to the next one,
        // then change the password (like the login page).
        const passwordFields = [currentPasswordInput, newPasswordInput, confirmPasswordInput];

        passwordFields.forEach((input, index) => {
            input.addEventListener("keydown", e => {
                if (e.key !== "Enter" || e.isComposing) return;
                e.preventDefault();
                const next = passwordFields[index + 1];
                if (next) next.focus();
                else if (!changePassword.disabled) changePassword.click();
            });
        });


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