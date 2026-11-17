import { beforeAll, describe, expect, it } from 'vitest';

// chat.js is a classic script for the project page; loaded here it sets window.studioChat.
let parseSSE;

beforeAll(async () => {
    globalThis.window = globalThis;
    await import('./chat.js');
    ({ parseSSE } = globalThis.studioChat);
});

describe('parseSSE', () => {
    it('reads complete events and keeps the unfinished rest', () => {
        const { events, rest } = parseSSE('data: {"type":"text","text":"Hel"}\n\ndata: {"type":"text","text":"lo"}\n\ndata: {"ty');
        expect(events).toEqual([{ type: 'text', text: 'Hel' }, { type: 'text', text: 'lo' }]);
        expect(rest).toBe('data: {"ty');
    });

    it('skips comments such as the keep-alives and a broken line', () => {
        const { events, rest } = parseSSE(': start\n\n: keepalive\n\ndata: not json\n\ndata: {"type":"done"}\n\n');
        expect(events).toEqual([{ type: 'done' }]);
        expect(rest).toBe('');
    });

    it('joins an event split over two reads', () => {
        const first = parseSSE('data: {"type":"text","te');
        expect(first.events).toEqual([]);
        const second = parseSSE(first.rest + 'xt":"one"}\n\n');
        expect(second.events).toEqual([{ type: 'text', text: 'one' }]);
    });

    it('keeps newlines and quotes inside the text', () => {
        const text = 'Line one\n\nLine "two"';
        const { events } = parseSSE(`data: ${JSON.stringify({ type: 'text', text })}\n\n`);
        expect(events[0].text).toBe(text);
    });
});
