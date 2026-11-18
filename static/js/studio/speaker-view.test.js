import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { displayedTranscript, hidesSpeakers, SHOWN_CLASS } from './speaker-view.js';

// The same cases as src/studio/speaker_view.py's tests.
const { cases } = JSON.parse(readFileSync(new URL('../../../tests/fixtures/studio_placeholder_speaker_cases.json', import.meta.url), 'utf8'));

function fakeRoot(setting) {
    const classes = new Set();
    return {
        __STUDIO_HIDE_PLACEHOLDER_SPEAKERS: setting,
        document: { documentElement: { classList: { toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)) } } },
        classes,
    };
}

const segments = (...speakers) => speakers.map((speaker, i) => ({ speaker, sentence: `Line ${i}.`, start_time: i * 2, end_time: i * 2 + 1.5 }));

describe('the placeholder speaker rule', () => {
    for (const c of cases) {
        it(c.why, () => {
            expect(hidesSpeakers(c.speakers.map((speaker) => ({ speaker, sentence: 'x' })), fakeRoot(true))).toBe(c.hidden);
        });
    }

    it('is off when the setting says false', () => {
        expect(hidesSpeakers(segments('SPEAKER_00', 'SPEAKER_00'), fakeRoot(false))).toBe(false);
    });

    it('is on when the page has no setting', () => {
        expect(hidesSpeakers(segments('SPEAKER_00'), fakeRoot(undefined))).toBe(true);
    });
});

describe('the transcript the page shows', () => {
    it('is a copy without the placeholder, with every time and sentence', () => {
        const root = fakeRoot(true);
        const stored = JSON.stringify(segments('SPEAKER_00', 'SPEAKER_00', 'SPEAKER_00'));
        const shown = JSON.parse(displayedTranscript(stored, root));
        expect(shown).toHaveLength(3);
        expect(shown.every((seg) => !('speaker' in seg))).toBe(true);
        expect(shown.map((seg) => seg.start_time)).toEqual([0, 2, 4]);
        expect(shown[1].sentence).toBe('Line 1.');
        expect(root.classes.has(SHOWN_CLASS)).toBe(true);
    });

    it('is the stored text itself for two voices, a plain text or the setting off', () => {
        const root = fakeRoot(true);
        const two = JSON.stringify(segments('SPEAKER_00', 'SPEAKER_01'));
        expect(displayedTranscript(two, root)).toBe(two);
        expect(root.classes.has(SHOWN_CLASS)).toBe(false);
        expect(displayedTranscript('A plain transcript.', root)).toBe('A plain transcript.');
        const one = JSON.stringify(segments('SPEAKER_00'));
        expect(displayedTranscript(one, fakeRoot(false))).toBe(one);
    });
});
