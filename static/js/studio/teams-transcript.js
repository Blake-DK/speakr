/**
 * A Teams meeting transcript (.vtt, "Download as .vtt" in Teams) as Speakr's
 * transcript segments: [{ speaker, sentence, start_time, end_time }].
 *
 * Teams writes one cue per sentence, its speaker in a voice tag:
 *     00:00:03.120 --> 00:00:05.400
 *     <v Rowan>Morning, everyone.</v>
 * A cue without a voice tag keeps the speaker of the cue before it.
 * The project page (static/js/studio/project.js) saves the segments through
 * Speakr's own transcript route.
 */
(function () {
    'use strict';

    const TIMING = /^\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})/;
    const VOICE = /<v(?:\.[^\s>]*)?\s+([^>]+)>/;
    const ENTITIES = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&nbsp;': ' ', '&quot;': '"', '&#39;': "'" };

    function seconds(stamp) {
        const parts = stamp.replace(',', '.').split(':').map(Number);
        return parts.reduce((total, part) => total * 60 + part, 0);
    }

    function plain(text) {
        return text.replace(/<[^>]*>/g, '').replace(/&(?:amp|lt|gt|nbsp|quot|#39);/g, (e) => ENTITIES[e]).replace(/\s+/g, ' ').trim();
    }

    /** The segments of a Teams .vtt; [] when the file has no cue with a speaker (not a Teams transcript). */
    function parseTeamsVtt(raw) {
        const segments = [];
        let speaker = null;
        let named = false;
        const blocks = String(raw || '').replace(/^﻿/, '').split(/\r?\n\s*\r?\n/);
        for (const block of blocks) {
            const lines = block.split(/\r?\n/);
            const at = lines.findIndex((line) => TIMING.test(line));
            if (at < 0) continue;
            const [, start, end] = lines[at].match(TIMING);
            const body = lines.slice(at + 1).join(' ');
            const voice = body.match(VOICE);
            if (voice) {
                speaker = plain(voice[1]);
                named = true;
            }
            const sentence = plain(body);
            if (!sentence) continue;
            segments.push({ speaker: speaker || 'Unknown', sentence, start_time: seconds(start), end_time: seconds(end) });
        }
        return named ? segments : [];
    }

    window.studioTeamsTranscript = { parseTeamsVtt };
})();
