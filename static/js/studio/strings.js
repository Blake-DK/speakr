/**
 * Studio strings for a studio page's Vue app.
 *
 * The page embeds its strings as window.__STUDIO_I18N (src/studio/i18n.py,
 * English already filling any gap). window.studioT('studio.x.y') returns the
 * string, or the key when there is none. In templates it is exposed as st():
 * upstream's tests read every t('...') call as a key of upstream's own locale
 * files, which studio keys are not.
 */
(function () {
    'use strict';

    function lookup(table, key) {
        let node = table;
        for (const part of String(key).split('.')) {
            if (!node || typeof node !== 'object' || !(part in node)) return null;
            node = node[part];
        }
        return typeof node === 'string' ? node : null;
    }

    window.studioT = function (key, params) {
        let text = lookup(window.__STUDIO_I18N || {}, key);
        if (text === null) return key;
        if (params) {
            for (const [name, value] of Object.entries(params)) {
                text = text.split(`{{${name}}}`).join(String(value));
            }
        }
        return text;
    };
})();
