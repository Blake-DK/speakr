/**
 * The Documents tab of a project's page (templates/studio/project.html):
 * the project's saved documents, an editor, and their downloads.
 *
 * window.studioDocuments.useDocuments(options) returns one reactive object the
 * page's Vue app exposes as `docs`. A save names the version the document was
 * opened at; when someone else saved in between, the server refuses it (409)
 * and the editor keeps the person's text (src/studio/documents.py).
 */
(function () {
    'use strict';

    const FORMATS = ['md', 'docx', 'pdf', 'pptx'];

    /** The line under a document's title: its kind, when and by whom it last changed, its length. */
    function metaParts(item, st, formatDate, formatNumber) {
        const parts = [];
        if (item.kind_title) parts.push(item.kind_title);
        else parts.push(st(`studio.project.documents.kinds.${item.kind === 'chat' ? 'chat' : 'other'}`));
        const date = formatDate(item.updated_at);
        parts.push(item.updated_by_name
            ? st('studio.project.documents.changedBy', { date, name: item.updated_by_name })
            : st('studio.project.documents.changed', { date }));
        parts.push(st('studio.project.documents.chars', { chars: formatNumber(item.length || 0) }));
        return parts;
    }

    function useDocuments({ projectId, api, st, onChanged }) {
        const { reactive } = Vue;

        const state = reactive({
            items: [],
            loaded: false,
            error: '',
            flash: '',
            editor: null,   // { id, title, body, version, saved: { title, body }, saving, error, conflict, copied }
        });

        function errorOf(res) {
            return (res && res.data && res.data.error) || st('studio.common.loadFailed');
        }

        async function load() {
            const res = await api.request('GET', `/api/studio/projects/${projectId}/documents`);
            if (res.ok && res.data) {
                state.items = res.data.items || [];
                state.loaded = true;
            }
            if (onChanged) onChanged();
        }

        function editorFrom(data) {
            return reactive({
                id: data ? data.id : null,
                title: data ? data.title : '',
                body: data ? (data.body || '') : '',
                version: data ? data.version : '',
                kindTitle: data ? data.kind_title : null,
                canDelete: data ? !!data.can_delete : false,
                saved: { title: data ? data.title : '', body: data ? (data.body || '') : '' },
                saving: false,
                error: '',
                conflict: false,
                copied: false,
            });
        }

        function openNew() {
            state.flash = '';
            state.editor = editorFrom(null);
        }

        async function openDocument(item) {
            state.flash = '';
            state.error = '';
            const res = await api.request('GET', `/api/studio/documents/${item.id}`);
            if (!res.ok || !res.data) {
                state.error = errorOf(res);
                if (res.status === 404) await load();
                return;
            }
            state.editor = editorFrom(res.data);
        }

        function dirty() {
            const e = state.editor;
            return !!e && (e.title !== e.saved.title || e.body !== e.saved.body);
        }

        async function close() {
            if (!state.editor) return;
            if (dirty()) {
                const ok = await window.confirmDialog({
                    title: st('studio.project.documents.discardConfirmTitle'),
                    message: st('studio.project.documents.discardConfirm'),
                    confirmLabel: st('studio.project.documents.discard'),
                    cancelLabel: st('studio.common.cancel'),
                    danger: true,
                });
                if (!ok) return;
            }
            state.editor = null;
        }

        async function save() {
            const e = state.editor;
            if (!e || e.saving || !e.title.trim()) return;
            e.saving = true;
            e.error = '';
            const res = e.id
                ? await api.request('PUT', `/api/studio/documents/${e.id}`, { title: e.title, body: e.body, version: e.version })
                : await api.request('POST', `/api/studio/projects/${projectId}/documents`, { title: e.title, body: e.body });
            e.saving = false;
            if (res.ok && res.data) {
                state.editor = null;
                state.flash = st('studio.project.documents.saved');
                await load();
                return;
            }
            e.error = errorOf(res);
            e.conflict = res.status === 409;
        }

        async function reloadNewer() {
            const e = state.editor;
            if (!e || !e.id) return;
            const ok = await window.confirmDialog({
                message: st('studio.project.documents.reloadNewerConfirm'),
                confirmLabel: st('studio.project.documents.reloadNewer'),
                cancelLabel: st('studio.common.cancel'),
            });
            if (!ok) return;
            const res = await api.request('GET', `/api/studio/documents/${e.id}`);
            if (res.ok && res.data) {
                state.editor = editorFrom(res.data);
            } else {
                e.error = errorOf(res);
            }
        }

        async function remove(item) {
            const target = item || (state.editor && { id: state.editor.id, title: state.editor.saved.title });
            if (!target || !target.id) return;
            const ok = await window.confirmDialog({
                title: st('studio.project.documents.deleteConfirmTitle'),
                message: st('studio.project.documents.deleteConfirm', { title: target.title }),
                confirmLabel: st('studio.common.delete'),
                cancelLabel: st('studio.common.cancel'),
                danger: true,
            });
            if (!ok) return;
            const res = await api.request('DELETE', `/api/studio/documents/${target.id}`);
            if (!res.ok) {
                const message = errorOf(res);
                if (state.editor && state.editor.id === target.id) state.editor.error = message;
                else state.error = message;
                return;
            }
            if (state.editor && state.editor.id === target.id) state.editor = null;
            await load();
        }

        function downloadUrl(id, format) {
            return `/api/studio/documents/${id}/download?format=${format}`;
        }

        async function copyBody() {
            const e = state.editor;
            if (!e) return;
            try {
                await navigator.clipboard.writeText(e.body);
                e.copied = true;
                setTimeout(() => { if (state.editor === e) e.copied = false; }, 1500);
            } catch (err) { /* clipboard not allowed */ }
        }

        function meta(item) {
            return metaParts(item, st, api.formatDate, api.formatNumber).join(' · ');
        }

        return Object.assign(state, {
            formats: FORMATS, load, openNew, openDocument, dirty, close, save, reloadNewer, remove,
            downloadUrl, copyBody, meta,
        });
    }

    window.studioDocuments = { useDocuments, metaParts };
})();
