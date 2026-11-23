/*
 * Regular refreshes that never pile up.
 *
 * setInterval fires on time even when the last request has not been
 * answered yet. When the router is slow (the modem reconnecting, mmcli
 * taking seconds), requests then queue up in the browser and on the
 * router until the whole dashboard stops answering. Poll waits for each
 * run to finish before it schedules the next, and pauses while the tab
 * is in the background (one immediate run when it comes back).
 *
 *     this.timer = Poll.every(3000, () => this.update());
 *     Poll.stop(this.timer);
 */

window.Poll = {

    every(ms, fn) {

        const handle = { stopped: false, timeout: null, running: false };

        const run = async () => {

            handle.timeout = null;

            if (handle.stopped) return;

            // Hidden tab: wait for it to come back (see below).
            if (document.hidden) return;

            handle.running = true;

            try {
                await fn();
            } catch (error) {
                console.error(error);
            }

            handle.running = false;

            if (!handle.stopped) handle.timeout = setTimeout(run, ms);

        };

        handle.wake = () => {

            if (handle.stopped || document.hidden || handle.running) return;

            clearTimeout(handle.timeout);
            run();

        };

        document.addEventListener("visibilitychange", handle.onVisible = () => {
            // Back in the foreground: refresh now instead of after ms.
            if (!document.hidden && handle.timeout === null && !handle.running) handle.wake();
        });

        handle.timeout = setTimeout(run, ms);

        return handle;

    },

    /** Stops a Poll handle; also accepts plain interval ids and null. */
    stop(handle) {

        if (!handle) return;

        if (typeof handle !== "object") {
            clearInterval(handle);
            return;
        }

        handle.stopped = true;
        clearTimeout(handle.timeout);
        document.removeEventListener("visibilitychange", handle.onVisible);

    }

};
