/*
 * Chardsoft custom dropdown.
 *
 * Wraps a native <select> in the same markup the Network page uses.
 * The native select stays the source of truth for the value, so pages
 * keep reading and writing select.value as usual and call
 * ChardsoftSelect.refresh(select) after changing it from code.
 */

window.ChardsoftSelect = {

    escape(value) {

        const div = document.createElement("div");
        div.textContent = value ?? "";
        return div.innerHTML;

    },


    close(wrapper) {

        wrapper.classList.remove("open");

        wrapper
            .querySelector(".chardsoft-select-trigger")
            ?.setAttribute("aria-expanded", "false");

    },


    closeAll(except = null) {

        document
            .querySelectorAll(".chardsoft-select.open")
            .forEach(wrapper => {
                if (wrapper !== except)
                    this.close(wrapper);
            });

    },


    enhance(select) {

        if (!select || select.refreshCustom)
            return;

        const wrapper = document.createElement("div");
        wrapper.className = "chardsoft-select";

        wrapper.innerHTML = `
            <button
                type="button"
                class="chardsoft-select-trigger"
                aria-haspopup="listbox"
                aria-expanded="false">

                <span class="chardsoft-select-value"></span>

                <span class="chardsoft-select-chevron">
                    <svg width="16" height="16" viewBox="0 0 24 24"
                        fill="none" stroke="currentColor" stroke-width="2"
                        stroke-linecap="round" stroke-linejoin="round"
                        aria-hidden="true">
                        <polyline points="6 9 12 15 18 9"></polyline>
                    </svg>
                </span>

            </button>

            <div class="chardsoft-select-menu" role="listbox"></div>
        `;

        select.parentNode.insertBefore(wrapper, select);
        wrapper.appendChild(select);

        select.classList.remove("setting-input");
        select.tabIndex = -1;
        select.setAttribute("aria-hidden", "true");

        const trigger = wrapper.querySelector(".chardsoft-select-trigger");
        const value = wrapper.querySelector(".chardsoft-select-value");
        const menu = wrapper.querySelector(".chardsoft-select-menu");

        // Labels come from the option text, so values like "12h"
        // can show as "12 hours".
        const updateVisual = () => {

            value.textContent =
                select.selectedOptions[0]?.textContent.trim() || "";

            trigger.disabled = select.disabled;

            menu.querySelectorAll(".chardsoft-select-option").forEach(option => {

                const selected = option.dataset.value === select.value;

                option.classList.toggle("selected", selected);
                option.setAttribute("aria-selected", selected ? "true" : "false");

            });

        };

        // Rebuild the menu from the native options (they can change).
        select.refreshCustom = () => {

            menu.innerHTML = "";

            [...select.options].forEach(opt => {

                const option = document.createElement("button");

                option.type = "button";
                option.className = "chardsoft-select-option";
                option.dataset.value = opt.value;
                option.disabled = opt.disabled;
                option.setAttribute("role", "option");
                option.setAttribute("aria-disabled", opt.disabled ? "true" : "false");

                option.innerHTML = `
                    <span>${this.escape(opt.textContent.trim())}</span>
                    <span class="chardsoft-select-check">✓</span>
                `;

                option.addEventListener("click", event => {

                    event.preventDefault();
                    event.stopPropagation();

                    if (opt.disabled)
                        return;

                    select.value = opt.value;

                    updateVisual();

                    select.dispatchEvent(new Event("change", { bubbles: true }));

                    this.close(wrapper);

                });

                menu.appendChild(option);

            });

            updateVisual();

        };

        trigger.addEventListener("click", event => {

            event.preventDefault();
            event.stopPropagation();

            if (trigger.disabled)
                return;

            const wasOpen = wrapper.classList.contains("open");

            this.closeAll();

            if (!wasOpen) {

                wrapper.classList.add("open");
                trigger.setAttribute("aria-expanded", "true");

                // Long lists open at the current choice.
                menu.querySelector(".chardsoft-select-option.selected")
                    ?.scrollIntoView({ block: "nearest" });

            }

        });

        select.addEventListener("change", updateVisual);

        select.refreshCustom();

    },


    refresh(select) {

        select?.refreshCustom?.();

    }

};


// Close open dropdowns when clicking elsewhere.
document.addEventListener("click", event => {

    document
        .querySelectorAll(".chardsoft-select.open")
        .forEach(wrapper => {
            if (!wrapper.contains(event.target))
                ChardsoftSelect.close(wrapper);
        });

});
