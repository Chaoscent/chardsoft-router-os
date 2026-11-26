/*
 * Shared confirmation dialog (#confirmModal in base.html).
 *
 * ChardsoftModal.confirm({ title, subtitle, body, confirmText })
 * resolves to true when confirmed, false otherwise.
 * `body` is an optional DOM node shown inside the dialog.
 * Enter in a password field confirms, like a login form (with several,
 * it moves on to the next one first); `enter: false` turns that off
 * (for dialogs that erase things).
 *
 * ChardsoftModal.notice({ title, subtitle, body, confirmText }) only informs:
 * one button (OK unless confirmText says otherwise).
 */

window.ChardsoftModal = {

    confirm({ title, subtitle, body, confirmText, enter = true }) {

        return new Promise(resolve => {

            modalTitle.textContent = title;
            modalSubtitle.textContent = subtitle;

            modalBody.innerHTML = "";

            if (body) modalBody.appendChild(body);

            modalConfirm.textContent = confirmText;
            modalConfirm.disabled = false;

            modalCancel.classList.remove("hidden");

            const finish = result => {
                modalBody.onkeydown = null;
                confirmModal.classList.add("hidden");
                resolve(result);
            };

            modalBody.onkeydown = e => {

                if (!enter || e.key !== "Enter" || e.isComposing || e.target.type !== "password") return;

                e.preventDefault();

                const fields = [...modalBody.querySelectorAll('input[type="password"]')];
                const next = fields[fields.indexOf(e.target) + 1];

                if (next) next.focus();
                else if (!modalConfirm.disabled) finish(true);

            };

            modalConfirm.onclick = e => { e.preventDefault(); finish(true); };
            modalCancel.onclick = e => { e.preventDefault(); finish(false); };
            modalClose.onclick = e => { e.preventDefault(); finish(false); };
            confirmModal.onclick = e => {
                if (e.target === confirmModal) finish(false);
            };

            confirmModal.classList.remove("hidden");

            // Ready to type the password.
            modalBody.querySelector('input[type="password"]')?.focus();

        });

    },


    async notice({ title, subtitle, body, confirmText = "OK" }) {

        const done = this.confirm({ title, subtitle, body, confirmText });

        modalCancel.classList.add("hidden");

        await done;

        modalCancel.classList.remove("hidden");

    },


    close() {

        confirmModal.classList.add("hidden");

    }

};
