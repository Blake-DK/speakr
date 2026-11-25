import { beforeAll, describe, expect, it } from 'vitest';

// teams-transcript.js is a classic script for the project page; loaded here it sets window.studioTeamsTranscript.
let parseTeamsVtt;

beforeAll(async () => {
    globalThis.window = globalThis;
    await import('./teams-transcript.js');
    ({ parseTeamsVtt } = globalThis.studioTeamsTranscript);
});

// As Teams writes it: a BOM, CRLF lines, a cue id before each timing line.
const TEAMS = '﻿WEBVTT\r\n\r\n'
    + '6f1c2a4e-9b1d-4c3e-8f2a-1d2e3f4a5b6c/12-0\r\n00:00:03.120 --> 00:00:05.400\r\n<v Rowan>Morning, everyone.</v>\r\n\r\n'
    + '6f1c2a4e-9b1d-4c3e-8f2a-1d2e3f4a5b6c/13-0\r\n00:00:05.900 --> 00:00:09.050\r\n<v KaneFinch>Morning. Shall we start with the motor &amp; the website?</v>\r\n\r\n'
    + '6f1c2a4e-9b1d-4c3e-8f2a-1d2e3f4a5b6c/14-0\r\n01:02:09.000 --> 01:02:11.500\r\n<v Rowan>Yes,\r\nlet\'s.</v>\r\n';

describe('parseTeamsVtt', () => {
    it('gives each cue its speaker, text and times in seconds', () => {
        expect(parseTeamsVtt(TEAMS)).toEqual([
            { speaker: 'Rowan', sentence: 'Morning, everyone.', start_time: 3.12, end_time: 5.4 },
            { speaker: 'KaneFinch', sentence: 'Morning. Shall we start with the motor & the website?', start_time: 5.9, end_time: 9.05 },
            { speaker: 'Rowan', sentence: "Yes, let's.", start_time: 3729, end_time: 3731.5 },
        ]);
    });

    it('keeps the speaker of the cue before for a cue without a voice tag', () => {
        const vtt = 'WEBVTT\n\n00:01.000 --> 00:02.000\n<v m1_owl>First.\n\n00:02.500 --> 00:04.000\nStill me.\n';
        expect(parseTeamsVtt(vtt).map((s) => s.speaker)).toEqual(['m1_owl', 'm1_owl']);
    });

    it('gives nothing for subtitles without speakers or for other text', () => {
        expect(parseTeamsVtt('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello.\n')).toEqual([]);
        expect(parseTeamsVtt('just some notes')).toEqual([]);
        expect(parseTeamsVtt('')).toEqual([]);
    });
});
