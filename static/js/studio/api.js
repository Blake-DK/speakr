/**
 * Small helpers shared by the studio pages: JSON requests with the CSRF
 * token, and sizes and dates for display.
 */
(function () {
    'use strict';

    function csrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    /**
     * fetch() with the session cookie and the CSRF token. Resolves to
     * { ok, status, data } and never rejects for an HTTP error; a network
     * error resolves to { ok: false, status: 0, data: null }.
     */
    async function request(method, url, body) {
        const options = {
            method,
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json', 'X-CSRFToken': csrfToken() },
        };
        if (body instanceof FormData) {
            options.body = body;
        } else if (body !== undefined) {
            options.headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(body);
        }
        let response;
        try {
            response = await fetch(url, options);
        } catch (err) {
            return { ok: false, status: 0, data: null };
        }
        let data = null;
        try {
            data = await response.json();
        } catch (err) {
            data = null;
        }
        return { ok: response.ok, status: response.status, data };
    }

    // Sizes, dates and percentages in the interface language, with upstream's
    // formatters (static/js/i18n.js), as every upstream page shows them.
    function formatSize(bytes) {
        if (window.i18n && window.i18n.formatFileSize) return window.i18n.formatFileSize(bytes);
        return String(bytes);
    }

    function formatDate(iso) {
        if (!iso) return '';
        const date = new Date(iso);
        if (Number.isNaN(date.getTime())) return '';
        if (window.i18n && window.i18n.formatDate) {
            return window.i18n.formatDate(date, { year: 'numeric', month: 'short', day: 'numeric' });
        }
        return date.toISOString().slice(0, 10);
    }

    function formatNumber(number) {
        if (window.i18n && window.i18n.formatNumber) return window.i18n.formatNumber(number);
        return String(number);
    }

    function formatPercent(percent) {
        if (window.i18n && window.i18n.formatPercent) return window.i18n.formatPercent(percent, { maximumFractionDigits: 0 });
        return String(Math.round(percent));
    }

    function formatDuration(seconds) {
        if (!Number.isFinite(seconds) || seconds <= 0) return '';
        const total = Math.round(seconds);
        const h = Math.floor(total / 3600);
        const m = Math.floor((total % 3600) / 60);
        const s = total % 60;
        const pad = (n) => String(n).padStart(2, '0');
        return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
    }

    function hideLoader() {
        if (window.AppLoader) window.AppLoader.hide();
    }

    async function initUpstreamI18n(language) {
        // Upstream's translations, for the shared page header (global-header.js waits for them).
        if (window.i18n && window.i18n.init) {
            try {
                await window.i18n.init(language || 'en');
            } catch (err) {
                console.error('i18n init failed', err);
            }
        }
    }

    window.studioApi = {
        csrfToken, request, formatSize, formatDate, formatNumber, formatPercent, formatDuration, hideLoader, initUpstreamI18n,
    };
})();
