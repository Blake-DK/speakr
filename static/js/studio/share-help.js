/**
 * "What to share", before the browser's screen-share picker: the logic, no DOM.
 *
 * Speakr's recorder ("System audio", "Microphone + system") calls
 * navigator.mediaDevices.getDisplayMedia() with audio, and the browser then
 * asks what to share (a tab, a window, the entire screen) without saying
 * which one records the sound. wrapGetDisplayMedia() puts a step of ours in
 * front: a call that asks for audio first shows our dialog (`ask`), and only
 * its Continue makes the real call. Continue runs inside the person's click
 * or key press, so the browser still sees a user gesture. Cancel is reported
 * as the browser reports a cancelled share (a NotAllowedError), so Speakr's
 * own "Screen sharing was canceled" path handles it. "Don't show this again"
 * is kept in the browser (localStorage).
 *
 * The page part is static/js/studio/main-page.js; the dialog itself is
 * static/js/studio/share-help-dialog.js. Tests: share-help.test.js.
 */

export const HIDDEN_KEY = 'studio.shareHelp.hidden';

const MARK = '__studioShareHelp';

/**
 * Which advice fits, from upstream's getAudioCapabilities()
 * (static/js/modules/utils/platform.js):
 *  - 'system': the whole computer's sound can be shared (Chrome or Edge on Windows or ChromeOS);
 *  - 'tab': only a browser tab's sound (Chrome or Edge elsewhere);
 *  - 'none': no sound from a screen share (Firefox, Safari, others).
 */
export function adviceFor(caps) {
    if (caps && caps.supportsSystemAudio) return 'system';
    if (caps && caps.supportsTabAudio) return 'tab';
    return 'none';
}

/** The string keys of the dialog's lines for an advice, in order. */
export function adviceLines(advice) {
    if (advice === 'system') return ['studio.shareHelp.systemApp', 'studio.shareHelp.systemTab', 'studio.shareHelp.noSwitch'];
    if (advice === 'tab') return ['studio.shareHelp.tabOnly', 'studio.shareHelp.tabOnlyNote', 'studio.shareHelp.noSwitch'];
    return ['studio.shareHelp.none'];
}

export function isHidden(storage) {
    try {
        return !!storage && storage.getItem(HIDDEN_KEY) === '1';
    } catch (err) {
        return false;
    }
}

export function hide(storage) {
    try {
        if (storage) storage.setItem(HIDDEN_KEY, '1');
    } catch (err) { /* private mode or storage switched off: shown again next time */ }
}

/** What a browser rejects getDisplayMedia() with when the person cancels its share dialog. */
export function cancelledError() {
    const message = 'The share was cancelled before the browser asked what to share.';
    try {
        return new DOMException(message, 'NotAllowedError');
    } catch (err) {
        const error = new Error(message);
        error.name = 'NotAllowedError';
        return error;
    }
}

function asksForAudio(constraints) {
    return !!(constraints && constraints.audio);
}

/**
 * Put our step in front of mediaDevices.getDisplayMedia.
 *
 * `ask({ proceed, cancel })` shows the dialog and later calls exactly one of
 * them, proceed from inside the click or key press that continues;
 * each takes `dontShowAgain`. Returns true when the method was wrapped:
 * false without the API (no mediaDevices, plain http) or when it is wrapped
 * already.
 */
export function wrapGetDisplayMedia(mediaDevices, { ask, storage } = {}) {
    if (!mediaDevices || typeof mediaDevices.getDisplayMedia !== 'function') return false;
    if (mediaDevices.getDisplayMedia[MARK]) return false;
    if (typeof ask !== 'function') return false;
    const original = mediaDevices.getDisplayMedia;
    let asking = false;

    const wrapped = function getDisplayMedia(constraints) {
        // Video only, the person chose not to see it again, or a second call
        // while the dialog is open: the browser's own picker, as before.
        if (!asksForAudio(constraints) || asking || isHidden(storage)) {
            return original.call(mediaDevices, constraints);
        }
        asking = true;
        return new Promise((resolve, reject) => {
            let settled = false;
            const settle = (dontShowAgain) => {
                if (settled) return false;
                settled = true;
                asking = false;
                if (dontShowAgain) hide(storage);
                return true;
            };
            const callOriginal = () => {
                let result;
                try {
                    result = original.call(mediaDevices, constraints);
                } catch (err) {
                    reject(err);
                    return;
                }
                Promise.resolve(result).then(resolve, reject);
            };
            try {
                ask({
                    proceed(dontShowAgain) {
                        if (settle(dontShowAgain)) callOriginal();
                    },
                    cancel(dontShowAgain) {
                        if (settle(dontShowAgain)) reject(cancelledError());
                    },
                });
            } catch (err) {
                // The dialog could not be shown. Still inside the person's
                // click, so the browser's picker opens as before.
                if (settle(false)) callOriginal();
            }
        });
    };
    wrapped[MARK] = true;
    wrapped.original = original;
    try {
        mediaDevices.getDisplayMedia = wrapped;
    } catch (err) {
        return false;
    }
    return mediaDevices.getDisplayMedia === wrapped;
}
