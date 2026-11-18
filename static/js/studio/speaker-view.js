/**
 * No placeholder speaker on the main page: the rule of src/studio/speaker_view.py
 * for the transcript the page shows.
 *
 * A transcript whose segments have exactly one distinct speaker, and that
 * speaker is a generic diarization label (SPEAKER_00, upstream's rule in
 * modules/utils/speaker-labels.js), is shown without it: the page gets a copy
 * of the JSON without the speaker, so upstream's own view for a transcript
 * without speakers draws timed lines that a click still seeks to, and no chip,
 * avatar, heading, legend or "Identify speakers". The stored transcript is not
 * touched: the editors read and save the recording's own JSON.
 *
 * While such a transcript is shown, <html> carries the class
 * studio-placeholder-speakers, which static/css/studio/main-page.css uses to
 * hide the speaker parts of the segment editor. window.__STUDIO_HIDE_PLACEHOLDER_SPEAKERS
 * (templates/studio/main_page.html, from STUDIO_HIDE_PLACEHOLDER_SPEAKERS) false
 * switches it all off.
 */

import { isGenericSpeakerLabel } from '../modules/utils/speaker-labels.js';

export const SHOWN_CLASS = 'studio-placeholder-speakers';

function enabled(root) {
    return !(root && root.__STUDIO_HIDE_PLACEHOLDER_SPEAKERS === false);
}

/** True when these segments' only speaker is a placeholder to hide. */
export function hidesSpeakers(segments, root = globalThis) {
    if (!enabled(root) || !Array.isArray(segments)) return false;
    const speakers = new Set();
    for (const seg of segments) {
        if (!seg || typeof seg !== 'object') continue;
        const name = typeof seg.speaker === 'string' ? seg.speaker.trim() : seg.speaker;
        if (name) speakers.add(name);
    }
    if (speakers.size !== 1) return false;
    const only = [...speakers][0];
    return typeof only === 'string' && isGenericSpeakerLabel(only);
}

/** The transcript text as the page shows it: the same JSON without the placeholder, or the text as it is. */
export function displayedTranscript(text, root = globalThis) {
    let hide = false;
    let shown = text;
    if (typeof text === 'string' && text.trim().startsWith('[')) {
        try {
            const segments = JSON.parse(text);
            if (hidesSpeakers(segments, root)) {
                hide = true;
                shown = JSON.stringify(segments.map((seg) => {
                    if (!seg || typeof seg !== 'object') return seg;
                    const { speaker, ...rest } = seg;  // eslint-disable-line no-unused-vars
                    return rest;
                }));
            }
        } catch (e) {
            // not JSON: shown as it is
        }
    }
    const doc = root && root.document;
    if (doc && doc.documentElement) doc.documentElement.classList.toggle(SHOWN_CLASS, hide);
    return shown;
}
