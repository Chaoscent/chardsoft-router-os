/*
 * The header's bell: warnings, errors and notable events from the
 * router's event log (/api/notifications). The unread count comes with
 * the header's own refresh (Header.update -> setCount); opening the
 * list marks everything in it as read.
 */

window.Notifications = {

    count: 0,

    LEVELS: {
        error: "Problem",
        warning: "Warning",
        info: "Info"
    },


    setCount(count) {

        this.count = Number(count) || 0;

        notifyCount.textContent = this.count > 9 ? "9+" : String(this.count);
        notifyCount.classList.toggle("hidden", !this.count);

        notifyToggle.setAttribute("aria-label",
            this.count ? `Notifications, ${this.count} unread` : "Notifications");

    },


    when(seconds) {

        const ago = Date.now() / 1000 - seconds;

        if (ago < 60) return "just now";
        if (ago < 3600) return `${Math.floor(ago / 60)} min ago`;
        if (ago < 86400) return `${Math.floor(ago / 3600)} h ago`;

        return new Date(seconds * 1000).toLocaleDateString([], { day: "numeric", month: "short" });

    },


    render(items) {

        if (!items.length) {
            notifyList.innerHTML = `<li class="notify-empty">Nothing to report. Warnings and notable events show up here.</li>`;
            return;
        }

        notifyList.replaceChildren(...items.map(item => {

            const li = document.createElement("li");
            const link = document.createElement("a");

            li.className = `notify-item ${item.level}${item.unread ? " unread" : ""}`;

            link.href = item.link;
            link.innerHTML = `
                <span class="notify-level">${this.LEVELS[item.level] || "Info"}</span>
                <span class="notify-time">${this.when(item.time)}</span>
                <span class="notify-message"></span>
            `;

            link.querySelector(".notify-message").textContent = item.message;
            link.onclick = () => this.close();

            li.appendChild(link);

            return li;

        }));

    },


    async open() {

        notifyPanel.classList.remove("hidden");
        notifyToggle.setAttribute("aria-expanded", "true");

        notifyList.innerHTML = `<li class="notify-empty">Loading...</li>`;

        try {

            const res = await fetch("/api/notifications");

            if (!res.ok) throw new Error();

            const data = await res.json();

            this.render(data.items);

            // Seen: everything up to the newest entry.
            if (data.unread && data.items.length) {

                await fetch("/api/notifications/read", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ until: data.items[0].time })
                });

                this.setCount(0);

            }

        } catch {

            notifyList.innerHTML = `<li class="notify-empty">Could not load the notifications.</li>`;

        }

    },


    close() {

        notifyPanel.classList.add("hidden");
        notifyToggle.setAttribute("aria-expanded", "false");

        // The highlight of what was new goes once the list was seen.
        notifyList.querySelectorAll(".unread").forEach(item => item.classList.remove("unread"));

    },


    init() {

        notifyToggle.onclick = event => {

            event.stopPropagation();

            if (notifyPanel.classList.contains("hidden")) this.open();
            else this.close();

        };

        document.addEventListener("click", event => {
            if (!notifyPanel.classList.contains("hidden") && !notifyPanel.contains(event.target)) this.close();
        });

        document.addEventListener("keydown", event => {
            if (event.key === "Escape" && !notifyPanel.classList.contains("hidden")) {
                this.close();
                notifyToggle.focus();
            }
        });

    }

};


window.addEventListener("DOMContentLoaded", () => Notifications.init());
