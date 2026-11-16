/**
 * The "What to share" dialog shown before the browser's screen-share picker
 * (the logic is in static/js/studio/share-help.js).
 *
 * Built from Speakr's modal primitives (static/css/primitives.css) as its own
 * dialogs are (static/js/app-dialog.js), so it follows the themes and sits
 * above Speakr's other dialogs. Esc and a click beside the panel cancel;
 * Enter on a button presses it, Enter elsewhere continues; Tab stays inside;
 * no key reaches the page under it; the element that had the focus gets it
 * back. Every text is set as text, never as HTML.
 */

const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])';

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
}

/**
 * Show the dialog. `lines` are string keys (share-help.js adviceLines);
 * `proceed(dontShowAgain)` is called from inside the click or key press that
 * continues, `cancel(dontShowAgain)` otherwise.
 */
export function showShareHelp({ advice, lines, st, proceed, cancel }) {
    const id = `studio-share-help-${Date.now().toString(36)}`;
    const overlay = el('div', 'modal-overlay app-dialog');
    overlay.setAttribute('data-studio-share-help', advice);
    const panel = el('div', 'modal-panel modal-panel--md app-dialog__panel');
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    panel.tabIndex = -1;

    const header = el('div', 'modal-header');
    const title = el('h3', 'modal-title', st('studio.shareHelp.title'));
    title.id = `${id}-title`;
    header.appendChild(title);
    panel.appendChild(header);
    panel.setAttribute('aria-labelledby', title.id);

    const body = el('div', 'modal-body app-dialog__body');
    body.id = `${id}-body`;
    panel.setAttribute('aria-describedby', body.id);
    if (advice !== 'none') body.appendChild(el('p', 'app-dialog__message', st('studio.shareHelp.intro')));
    const list = el('ul');
    list.style.cssText = 'margin: var(--sp-3, 0.75rem) 0 0; padding-left: 1.25rem; list-style: disc;';
    for (const key of lines) {
        const item = el('li', null, st(key));
        item.style.marginTop = '0.4rem';
        list.appendChild(item);
    }
    body.appendChild(list);
    if (advice !== 'none') {
        const notes = el('p', 't-meta');
        notes.style.marginTop = 'var(--sp-3, 0.75rem)';
        notes.textContent = `${st('studio.shareHelp.video')} ${st('studio.shareHelp.guide')}`;
        body.appendChild(notes);
    }
    const label = el('label');
    label.style.cssText = 'display: flex; align-items: center; gap: 0.5rem; margin-top: var(--sp-4, 1rem); cursor: pointer;';
    const checkbox = el('input');
    checkbox.type = 'checkbox';
    checkbox.setAttribute('data-studio-share-help-hide', '');
    label.appendChild(checkbox);
    label.appendChild(el('span', null, st('studio.shareHelp.dontShow')));
    body.appendChild(label);
    panel.appendChild(body);

    const footer = el('div', 'modal-footer');
    const cancelButton = el('button', 'btn btn--ghost', st('studio.shareHelp.cancel'));
    cancelButton.type = 'button';
    cancelButton.setAttribute('data-studio-share-help-cancel', '');
    const continueButton = el('button', 'btn btn--primary', st('studio.shareHelp.continue'));
    continueButton.type = 'button';
    continueButton.setAttribute('data-studio-share-help-continue', '');
    footer.appendChild(cancelButton);
    footer.appendChild(continueButton);
    panel.appendChild(footer);
    overlay.appendChild(panel);

    const before = document.activeElement;
    let done = false;

    const controls = () => Array.from(panel.querySelectorAll(FOCUSABLE)).filter((node) => node.getClientRects().length);

    function close() {
        if (done) return false;
        done = true;
        window.removeEventListener('keydown', onKeydown, true);
        document.removeEventListener('focusin', onFocusIn, true);
        if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
        if (before && typeof before.focus === 'function' && document.contains(before)) {
            try { before.focus({ preventScroll: true }); } catch (err) { /* gone */ }
        }
        return true;
    }

    // Both run inside the click or key press: the browser's picker needs that.
    function onContinue() {
        if (close()) proceed(checkbox.checked);
    }

    function onCancel() {
        if (close()) cancel(checkbox.checked);
    }

    function onKeydown(event) {
        event.stopPropagation();  // nothing under the dialog sees a key while it is open
        if (event.isComposing) return;
        const active = document.activeElement;
        if (event.key === 'Escape' || event.key === 'Esc') {
            event.preventDefault();
            onCancel();
        } else if (event.key === 'Tab') {
            event.preventDefault();
            const items = controls();
            if (!items.length) return;
            const index = items.indexOf(active);
            const next = index < 0 ? (event.shiftKey ? items.length - 1 : 0)
                : (index + (event.shiftKey ? -1 : 1) + items.length) % items.length;
            items[next].focus();
        } else if (event.key === 'Enter' && active !== cancelButton && active !== continueButton) {
            event.preventDefault();
            onContinue();
        }
    }

    function onFocusIn(event) {
        if (!panel.contains(event.target)) {
            event.stopPropagation();
            continueButton.focus();
        }
    }

    // A click beside the panel cancels; a press that starts in the panel and ends outside does not.
    let pressedOutside = false;
    overlay.addEventListener('mousedown', (event) => { pressedOutside = event.target === overlay; });
    overlay.addEventListener('click', (event) => { if (event.target === overlay && pressedOutside) onCancel(); });
    cancelButton.addEventListener('click', onCancel);
    continueButton.addEventListener('click', onContinue);

    window.addEventListener('keydown', onKeydown, true);
    document.addEventListener('focusin', onFocusIn, true);
    document.body.appendChild(overlay);
    continueButton.focus();
}
