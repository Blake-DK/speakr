/**
 * The Projects page of the studio add-on (templates/studio/projects.html).
 * Empty for now: it shows the empty state and the source code link.
 */
(function () {
    'use strict';

    const page = window.__STUDIO_PAGE || {};

    async function start() {
        // Upstream's translations, for the shared page header (global-header.js waits for them).
        if (window.i18n && window.i18n.init) {
            try {
                await window.i18n.init(page.userLanguage || 'en');
            } catch (err) {
                console.error('i18n init failed', err);
            }
        }

        Vue.createApp({
            setup() {
                return {
                    st: window.studioT,
                    sourceUrl: page.sourceUrl || '',
                };
            },
            delimiters: ['${', '}'],
        }).mount('#app');

        if (window.AppLoader) {
            window.AppLoader.hide();
        }
    }

    start();
})();
