import { beforeAll, describe, expect, it } from 'vitest';

// documents.js is a classic script for the project page; loaded here it sets window.studioDocuments.
let metaParts;

const strings = {
    'studio.project.documents.kinds.other': 'Written here',
    'studio.project.documents.kinds.chat': 'From a chat',
    'studio.project.documents.changed': 'Changed {{date}}',
    'studio.project.documents.changedBy': 'Changed {{date}} by {{name}}',
    'studio.project.documents.chars': '{{chars}} characters',
};
const st = (key, params) => (strings[key] || key).replace(/\{\{(\w+)\}\}/g, (_, name) => String((params || {})[name]));
const formatDate = (iso) => (iso ? iso.slice(0, 10) : '');
const formatNumber = (n) => String(n);

beforeAll(async () => {
    globalThis.window = globalThis;
    await import('./documents.js');
    ({ metaParts } = globalThis.studioDocuments);
});

describe('metaParts', () => {
    it('names a workflow document by its workflow and says who changed it', () => {
        const parts = metaParts({
            kind: 'pitchdeck', kind_title: 'Pitch deck', updated_at: '2026-11-17T12:00:00Z', updated_by_name: 'm1_owl', length: 1200,
        }, st, formatDate, formatNumber);
        expect(parts).toEqual(['Pitch deck', 'Changed 2026-11-17 by m1_owl', '1200 characters']);
    });

    it('says where a document without a workflow came from', () => {
        expect(metaParts({ kind: 'chat', kind_title: null, updated_at: '2026-11-17T12:00:00Z', length: 5 },
            st, formatDate, formatNumber)[0]).toBe('From a chat');
        expect(metaParts({ kind: 'other', kind_title: null, updated_at: '2026-11-17T12:00:00Z', length: 5 },
            st, formatDate, formatNumber).slice(0, 2)).toEqual(['Written here', 'Changed 2026-11-17']);
    });

    it('leaves a former member nameless', () => {
        const parts = metaParts({ kind: 'other', updated_at: '2026-11-17T12:00:00Z', updated_by_name: null, length: 0 },
            st, formatDate, formatNumber);
        expect(parts[1]).toBe('Changed 2026-11-17');
        expect(parts[2]).toBe('0 characters');
    });
});
