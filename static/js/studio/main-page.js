/**
 * The studio add-on on Speakr's main page (loaded by templates/studio/main_page.html).
 *
 * "What to share": before the browser asks what to share for "System audio"
 * or "Microphone + system", a short dialog says which choice records the
 * sound on this computer and browser. The advice comes from upstream's own
 * capability check (static/js/modules/utils/platform.js), the one Speakr's
 * recorder and its system-audio guide use.
 */
import { getAudioCapabilities } from '../modules/utils/platform.js';
import { adviceFor, adviceLines, wrapGetDisplayMedia } from './share-help.js';
import { showShareHelp } from './share-help-dialog.js';

function browserStorage() {
    try {
        return window.localStorage;
    } catch (err) {
        return null;  // storage switched off: the dialog shows every time
    }
}

const st = typeof window.studioT === 'function' ? window.studioT : (key) => key;

wrapGetDisplayMedia(typeof navigator !== 'undefined' ? navigator.mediaDevices : null, {
    storage: browserStorage(),
    ask({ proceed, cancel }) {
        const advice = adviceFor(getAudioCapabilities());
        showShareHelp({ advice, lines: adviceLines(advice), st, proceed, cancel });
    },
});
