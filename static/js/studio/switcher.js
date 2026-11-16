/**
 * The project switcher on a project's page (templates/studio/project.html):
 * the project's name is a button that opens the person's projects, the most
 * recently opened first (GET /api/studio/switcher, the same order as the
 * Projects page), with a filter box once there are more than seven, and
 * "All projects" and "New project" at the bottom.
 *
 * Keyboard: ArrowDown on the name opens it; in the list, ArrowUp/ArrowDown
 * (and Home/End) move, Enter opens, Escape closes and puts the focus back on
 * the name, Tab closes. Below 640px it opens as a sheet from the bottom of
 * the screen.
 *
 * window.studioSwitcher.useSwitcher({ currentId, api, st }) returns a
 * reactive object for the page's Vue app.
 */
(function () {
    'use strict';

    const FILTER_FROM = 8;  // the filter box shows when there are more than seven projects

    function normalise(text) {
        return String(text || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
    }

    function useSwitcher(options) {
        const { ref, reactive, computed, watch, nextTick } = Vue;
        const currentId = options.currentId;
        const api = options.api;
        const st = options.st;

        const open = ref(false);
        const loaded = ref(false);
        const loading = ref(false);
        const failed = ref(false);
        const items = ref([]);
        const query = ref('');
        const active = ref(0);

        const showFilter = computed(() => items.value.length >= FILTER_FROM);

        const matches = computed(() => {
            const wanted = normalise(query.value.trim());
            if (!wanted) return items.value;
            return items.value.filter(item => normalise(item.name).includes(wanted));
        });

        // Everything the arrow keys move through: the projects, then the two links.
        const entries = computed(() => {
            const list = matches.value.map(item => ({
                key: 'p' + item.id,
                kind: 'project',
                href: item.url,
                item,
                current: item.id === currentId,
            }));
            list.push({ key: 'all', kind: 'all', href: '/studio', item: null, current: false });
            list.push({ key: 'new', kind: 'new', href: '/studio?new=1', item: null, current: false });
            return list;
        });

        const firstFooter = computed(() => entries.value.findIndex(entry => entry.kind !== 'project'));

        function firstUseful() {
            // The first project that is not the one open now; else the first entry.
            const index = entries.value.findIndex(entry => entry.kind === 'project' && !entry.current);
            return index >= 0 ? index : 0;
        }

        async function load() {
            if (loading.value) return;
            loading.value = true;
            failed.value = false;
            const res = await api.request('GET', '/api/studio/switcher');
            if (res.ok && res.data && Array.isArray(res.data.projects)) {
                items.value = res.data.projects;
                loaded.value = true;
            } else {
                failed.value = true;
            }
            loading.value = false;
            if (open.value) active.value = firstUseful();
        }

        function button() {
            return document.querySelector('[data-studio-switcher-button]');
        }

        function focusInside() {
            nextTick(() => {
                const target = document.querySelector('[data-studio-switcher-filter]')
                    || document.querySelector('[data-studio-switcher-list]');
                if (target) target.focus();
            });
        }

        function show() {
            if (open.value) return;
            open.value = true;
            query.value = '';
            active.value = firstUseful();
            if (!loaded.value) load();
            focusInside();
        }

        function close(returnFocus) {
            if (!open.value) return;
            open.value = false;
            query.value = '';
            if (returnFocus) {
                nextTick(() => {
                    const el = button();
                    if (el) el.focus();
                });
            }
        }

        function toggle() {
            if (open.value) close(true);
            else show();
        }

        function go(entry) {
            if (!entry) return;
            if (entry.current) {
                close(true);
                return;
            }
            window.location.href = entry.href;
        }

        function onEntryClick(event, entry) {
            // A click on the project open now only closes the list. Every other
            // click is the link's own: Ctrl/Cmd-click still opens a new tab.
            if (entry.current && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
                event.preventDefault();
                close(true);
            }
        }

        function onKeydown(event) {
            if (!open.value) {
                if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && event.target === button()) {
                    event.preventDefault();
                    show();
                }
                return;
            }
            const count = entries.value.length;
            switch (event.key) {
            case 'ArrowDown':
                event.preventDefault();
                active.value = (active.value + 1) % count;
                break;
            case 'ArrowUp':
                event.preventDefault();
                active.value = (active.value - 1 + count) % count;
                break;
            case 'Home':
                if (event.target && event.target.tagName === 'INPUT') return;  // the caret, in the filter box
                event.preventDefault();
                active.value = 0;
                break;
            case 'End':
                if (event.target && event.target.tagName === 'INPUT') return;
                event.preventDefault();
                active.value = count - 1;
                break;
            case 'Enter':
                if (event.target === button()) return;  // the button's own click closes it
                event.preventDefault();
                go(entries.value[active.value]);
                break;
            case 'Escape':
                event.preventDefault();
                event.stopPropagation();
                close(true);
                break;
            case 'Tab':
                close(false);
                break;
            default:
                break;
            }
        }

        function optionId(index) {
            return 'studio-switcher-option-' + index;
        }

        function entryLabel(entry) {
            if (entry.kind === 'all') return st('studio.switcher.allProjects');
            if (entry.kind === 'new') return st('studio.switcher.newProject');
            return entry.item.name;
        }

        function entryNote(entry) {
            if (entry.kind !== 'project') return '';
            if (entry.current) return st('studio.switcher.current');
            const item = entry.item;
            if (item.role === 'member') return st('studio.projects.sharedWithYou');
            if (item.role !== 'owner' && item.owner_name) return st('studio.projects.owner', { name: item.owner_name });
            if (item.shared) return st('studio.projects.shared');
            return '';
        }

        function entryIcon(entry) {
            if (entry.kind === 'all') return 'fas fa-th-large';
            if (entry.kind === 'new') return 'fas fa-plus';
            return entry.current ? 'fas fa-check' : 'fas fa-film';
        }

        watch(query, () => { active.value = firstUseful(); });
        watch(active, (index) => {
            nextTick(() => {
                const el = document.getElementById(optionId(index));
                if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
            });
        });

        return reactive({
            open, loaded, loading, failed, items, query, active, showFilter, matches, entries, firstFooter,
            load, show, close, toggle, go, onEntryClick, onKeydown, optionId, entryLabel, entryNote, entryIcon,
        });
    }

    window.studioSwitcher = { useSwitcher };
})();
