/*
    Enter in a text or password field presses the card's main button, like
    a form would: the first visible save button after the field in its
    card. Other buttons (show password, new password, refresh) are never
    pressed this way. A field can name its button with data-enter="buttonId",
    or opt out with data-enter="none".
    Fields with their own Enter handling, forms and dialogs are left alone.
*/

(() => {

    const TYPES = new Set(["text", "password", "number", "email", "search", "tel", "url"]);

    const usable = button =>
        !button.disabled && button.offsetParent !== null;

    const after = (field, button) =>
        field.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING;

    function buttonFor(field) {

        const named = field.dataset.enter;

        if (named === "none") return null;

        if (named) {
            const button = document.getElementById(named);
            return button && usable(button) ? button : null;
        }

        const card = field.closest(".card");

        if (!card) return null;

        return [...card.querySelectorAll("button.save-btn")]
            .find(button => after(field, button) && usable(button)) || null;
    }

    document.addEventListener("keydown", event => {

        const field = event.target;

        if (event.key !== "Enter" || event.defaultPrevented || event.isComposing) return;
        if (event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
        if (field.tagName !== "INPUT" || !TYPES.has(field.type)) return;
        if (field.onkeydown || field.form || field.closest(".modal-overlay, dialog")) return;

        const button = buttonFor(field);

        if (!button) return;

        event.preventDefault();
        button.click();
    });

})();
