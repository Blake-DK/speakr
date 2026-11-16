import { afterEach, describe, expect, it, vi } from 'vitest';

import { getAudioCapabilities } from '../modules/utils/platform.js';
import {
    HIDDEN_KEY,
    adviceFor,
    adviceLines,
    cancelledError,
    isHidden,
    wrapGetDisplayMedia
} from './share-help.js';

const AUDIO = { video: true, audio: true, audioSelection: 'preferred' };

function memoryStorage() {
    const data = new Map();
    return {
        getItem: (key) => (data.has(key) ? data.get(key) : null),
        setItem: (key, value) => data.set(key, String(value)),
        data,
    };
}

// A stand-in for navigator.mediaDevices whose getDisplayMedia, like the
// browser's, fails unless it is called on the MediaDevices object itself.
function fakeMediaDevices(result = { id: 'stream' }) {
    const devices = {};
    const calls = [];
    devices.getDisplayMedia = function (constraints) {
        if (this !== devices) throw new TypeError('Illegal invocation');
        calls.push(constraints);
        return result instanceof Error ? Promise.reject(result) : Promise.resolve(result);
    };
    return { devices, calls };
}

// The dialog, as the tests drive it: it records each ask and lets the test answer.
function recordingAsk() {
    const asked = [];
    const ask = (answer) => { asked.push(answer); };
    return { ask, asked };
}

describe('wrapping getDisplayMedia', () => {
    it('shows the dialog first and makes the real call only on Continue, inside that call', async () => {
        const { devices, calls } = fakeMediaDevices();
        const { ask, asked } = recordingAsk();
        expect(wrapGetDisplayMedia(devices, { ask, storage: memoryStorage() })).toBe(true);

        const pending = devices.getDisplayMedia(AUDIO);
        expect(asked).toHaveLength(1);
        expect(calls).toHaveLength(0);  // the browser's picker has not been asked yet

        asked[0].proceed(false);
        // Synchronously, inside the click that continued: the browser needs the user gesture.
        expect(calls).toEqual([AUDIO]);
        await expect(pending).resolves.toEqual({ id: 'stream' });
    });

    it('reports Cancel the way a browser reports a cancelled share', async () => {
        const { devices, calls } = fakeMediaDevices();
        const { ask, asked } = recordingAsk();
        wrapGetDisplayMedia(devices, { ask, storage: memoryStorage() });

        const pending = devices.getDisplayMedia(AUDIO);
        asked[0].cancel(false);
        const error = await pending.catch((err) => err);
        expect(error.name).toBe('NotAllowedError');  // Speakr's recorder shows "Screen sharing was canceled"
        expect(calls).toHaveLength(0);
    });

    it('passes on what the browser answers, a refusal included', async () => {
        const refused = cancelledError();
        const { devices } = fakeMediaDevices(refused);
        const { ask, asked } = recordingAsk();
        wrapGetDisplayMedia(devices, { ask, storage: memoryStorage() });

        const pending = devices.getDisplayMedia(AUDIO);
        asked[0].proceed(false);
        await expect(pending).rejects.toBe(refused);
    });

    it('rejects when the browser throws at once', async () => {
        const devices = { getDisplayMedia() { throw new TypeError('not allowed here'); } };
        const { ask, asked } = recordingAsk();
        wrapGetDisplayMedia(devices, { ask, storage: memoryStorage() });

        const pending = devices.getDisplayMedia(AUDIO);
        asked[0].proceed(false);
        await expect(pending).rejects.toThrow('not allowed here');
    });

    it('answers only once, whatever the dialog calls afterwards', async () => {
        const { devices, calls } = fakeMediaDevices();
        const { ask, asked } = recordingAsk();
        wrapGetDisplayMedia(devices, { ask, storage: memoryStorage() });

        const pending = devices.getDisplayMedia(AUDIO);
        asked[0].proceed(false);
        asked[0].cancel(false);
        asked[0].proceed(false);
        await expect(pending).resolves.toEqual({ id: 'stream' });
        expect(calls).toHaveLength(1);
    });

    it('"Don\'t show this again" sends later calls straight to the browser', async () => {
        const storage = memoryStorage();
        const { devices, calls } = fakeMediaDevices();
        const { ask, asked } = recordingAsk();
        wrapGetDisplayMedia(devices, { ask, storage });

        const first = devices.getDisplayMedia(AUDIO);
        asked[0].proceed(true);
        await first;
        expect(storage.data.get(HIDDEN_KEY)).toBe('1');

        await devices.getDisplayMedia(AUDIO);
        expect(asked).toHaveLength(1);
        expect(calls).toHaveLength(2);
    });

    it('"Don\'t show this again" with Cancel is kept too', async () => {
        const storage = memoryStorage();
        const { devices } = fakeMediaDevices();
        const { ask, asked } = recordingAsk();
        wrapGetDisplayMedia(devices, { ask, storage });

        const pending = devices.getDisplayMedia(AUDIO);
        asked[0].cancel(true);
        await pending.catch(() => {});
        expect(isHidden(storage)).toBe(true);
    });

    it('shows the dialog every time when the browser keeps no storage', async () => {
        const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
        const { devices } = fakeMediaDevices();
        const { ask, asked } = recordingAsk();
        wrapGetDisplayMedia(devices, { ask, storage: broken });

        const first = devices.getDisplayMedia(AUDIO);
        asked[0].proceed(true);
        await first;
        const second = devices.getDisplayMedia(AUDIO);
        expect(asked).toHaveLength(2);
        asked[1].proceed(false);
        await second;

        wrapGetDisplayMedia(fakeMediaDevices().devices, { ask, storage: null });  // no storage at all: no error
    });

    it('leaves a share without audio to the browser alone', async () => {
        const { devices, calls } = fakeMediaDevices();
        const { ask, asked } = recordingAsk();
        wrapGetDisplayMedia(devices, { ask, storage: memoryStorage() });

        await devices.getDisplayMedia({ video: true });
        await devices.getDisplayMedia();
        expect(asked).toHaveLength(0);
        expect(calls).toHaveLength(2);
    });

    it('lets a second call through while the dialog is open', async () => {
        const { devices, calls } = fakeMediaDevices();
        const { ask, asked } = recordingAsk();
        wrapGetDisplayMedia(devices, { ask, storage: memoryStorage() });

        const first = devices.getDisplayMedia(AUDIO);
        await devices.getDisplayMedia(AUDIO);
        expect(asked).toHaveLength(1);
        expect(calls).toHaveLength(1);
        asked[0].proceed(false);
        await first;
        expect(calls).toHaveLength(2);
    });

    it('falls back to the browser\'s picker when the dialog cannot be shown', async () => {
        const { devices, calls } = fakeMediaDevices();
        wrapGetDisplayMedia(devices, { ask() { throw new Error('no document'); }, storage: memoryStorage() });

        await expect(devices.getDisplayMedia(AUDIO)).resolves.toEqual({ id: 'stream' });
        expect(calls).toHaveLength(1);
    });

    it('does nothing in a browser without the API, and wraps only once', () => {
        const { ask } = recordingAsk();
        expect(wrapGetDisplayMedia(undefined, { ask })).toBe(false);  // plain http: no navigator.mediaDevices
        expect(wrapGetDisplayMedia(null, { ask })).toBe(false);
        expect(wrapGetDisplayMedia({}, { ask })).toBe(false);  // no getDisplayMedia (phones)
        expect(wrapGetDisplayMedia({ getDisplayMedia: 'no' }, { ask })).toBe(false);

        const { devices } = fakeMediaDevices();
        expect(wrapGetDisplayMedia(devices, {})).toBe(false);  // no dialog to show
        expect(wrapGetDisplayMedia(devices, { ask })).toBe(true);
        const wrapped = devices.getDisplayMedia;
        expect(wrapGetDisplayMedia(devices, { ask })).toBe(false);
        expect(devices.getDisplayMedia).toBe(wrapped);
    });
});

describe('the advice', () => {
    afterEach(() => {
        vi.unstubAllGlobals();
    });

    const on = (userAgent, platform) => {
        vi.stubGlobal('navigator', {
            userAgent,
            platform,
            mediaDevices: { getDisplayMedia() {}, getUserMedia() {} },
        });
        return adviceFor(getAudioCapabilities());
    };

    it('follows upstream\'s own capability check', () => {
        const edgeWindows = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0';
        const chromeMac = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36';
        const firefoxWindows = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0';
        const chromeLinux = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36';
        expect(on(edgeWindows, 'Win32')).toBe('system');
        expect(on(chromeMac, 'MacIntel')).toBe('tab');
        expect(on(chromeLinux, 'Linux x86_64')).toBe('tab');
        expect(on(firefoxWindows, 'Win32')).toBe('none');
    });

    it('says what to pick for each case', () => {
        expect(adviceLines('system')).toEqual([
            'studio.shareHelp.systemApp', 'studio.shareHelp.systemTab', 'studio.shareHelp.noSwitch']);
        expect(adviceLines('tab')).toEqual([
            'studio.shareHelp.tabOnly', 'studio.shareHelp.tabOnlyNote', 'studio.shareHelp.noSwitch']);
        expect(adviceLines('none')).toEqual(['studio.shareHelp.none']);
        expect(adviceFor(null)).toBe('none');
    });
});
