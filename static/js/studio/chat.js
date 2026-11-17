/**
 * The Chats tab of a project's page (templates/studio/project.html):
 * the project's chats, a chat with a language model, the guided workflows.
 *
 * window.studioChat.useChat(options) returns one reactive object the page's
 * Vue app exposes as `chat`. Answers stream as Server-Sent Events from
 * POST /api/studio/chats/<id>/stream (and /regenerate); Stop aborts that
 * request, and the server keeps what was written so far (src/studio/chats.py).
 *
 * Model output is Markdown, shown through Speakr's own safe renderer
 * (static/js/markdown-safe.js: marked, then DOMPurify). On top of it, here:
 * images are dropped (an answer never loads anything from elsewhere) and
 * every link opens in a new tab without the page as its opener.
 */
(function () {
    'use strict';

    const PROVIDER_KEY = 'studio.chat.provider';
    const STOPPED = '\n\n*(stopped)*';

    function storageGet(key) {
        try { return window.localStorage.getItem(key); } catch (err) { return null; }
    }

    function storageSet(key, value) {
        try { window.localStorage.setItem(key, value); } catch (err) { /* storage switched off */ }
    }

    /** Split a Server-Sent Events buffer into its complete events; returns { events, rest }. */
    function parseSSE(buffer) {
        const blocks = buffer.split('\n\n');
        const rest = blocks.pop();
        const events = [];
        for (const block of blocks) {
            for (const line of block.split('\n')) {
                if (!line.startsWith('data: ')) continue;  // ": keepalive" and the like
                try { events.push(JSON.parse(line.slice(6))); } catch (err) { /* a broken line is skipped */ }
            }
        }
        return { events, rest };
    }

    /** Markdown to HTML through Speakr's safe renderer, then no images and links in a new tab. */
    function renderMarkdown(text) {
        const source = String(text || '');
        let html;
        if (typeof window.renderMarkdownSafe === 'function') {
            html = window.renderMarkdownSafe(source);
        } else {
            const div = document.createElement('div');
            div.textContent = source;
            return div.innerHTML.replace(/\n/g, '<br>');
        }
        const tpl = document.createElement('template');
        tpl.innerHTML = html;  // inert: a template's content is not rendered or loaded
        tpl.content.querySelectorAll('img, picture, video, audio, source, iframe').forEach(node => node.remove());
        tpl.content.querySelectorAll('a').forEach(a => {
            a.setAttribute('target', '_blank');
            a.setAttribute('rel', 'noopener noreferrer nofollow');
        });
        return tpl.innerHTML;
    }

    function useChat({ projectId, api, st, can, voiceItems, onMaterialsChanged, selectTab }) {
        const { reactive, nextTick } = Vue;

        const state = reactive({
            ready: false,
            config: { providers: {}, workflows: [], limits: {} },
            items: [],
            query: '',
            listLoading: false,
            view: 'list',            // on a phone: the list or the open chat
            current: null,           // the open chat (as the server describes it)
            messages: [],
            loadingChat: false,
            text: '',
            provider: storageGet(PROVIDER_KEY) || 'claude',
            webSearch: false,
            selected: [],            // attachments for the next message: { type, id, name, size?, chars? }
            attachables: { materials: [], recordings: [] },
            pickerOpen: false,
            contextOpen: false,
            excludedVoice: [],
            streaming: false,
            live: null,              // the answer being written: { content, error, thinking }
            error: '',
            dragging: false,
            uploading: 0,
            workflow: null,          // the workflow whose form is open
            workflowFields: {},
            workflowMaterials: [],
            starting: false,
            editing: null,           // { id, text } while the edit dialog is open
            copiedId: null,
        });

        let abort = null;
        let searchTimer = null;
        let busyTimer = null;
        let scrollBox = null;

        // --- helpers ------------------------------------------------------
        function errorOf(res) {
            return (res && res.data && res.data.error) || st('studio.common.loadFailed');
        }

        function providerOptions() {
            const out = [];
            for (const name of ['claude', 'openai']) {
                const p = state.config.providers[name];
                if (!p) continue;
                let label = st('studio.chat.providerOption', { label: p.label, model: p.model });
                if (!p.available) label += ' — ' + st(p.has_key ? 'studio.chat.providerOff' : 'studio.chat.providerNoKey');
                out.push({ value: name, label, disabled: !p.available });
            }
            return out;
        }

        function anyProvider() {
            return Object.values(state.config.providers).some(p => p.available);
        }

        function pickProvider() {
            const providers = state.config.providers;
            if (providers[state.provider] && providers[state.provider].available) return;
            const first = Object.keys(providers).find(name => providers[name].available);
            if (first) state.provider = first;
        }

        function setProvider(value) {
            state.provider = value;
            storageSet(PROVIDER_KEY, value);
        }

        function setScrollBox(el) {
            scrollBox = el;
        }

        function pinned() {
            return !scrollBox || scrollBox.scrollHeight - scrollBox.scrollTop - scrollBox.clientHeight < 80;
        }

        function scrollToEnd() {
            nextTick(() => { if (scrollBox) scrollBox.scrollTop = scrollBox.scrollHeight; });
        }

        function titleOf(item) {
            return (item && item.title) || st('studio.chat.untitled');
        }

        function senderLabel(message) {
            if (message.is_mine) return st('studio.chat.you');
            return message.sender_name || st('studio.chat.formerMember');
        }

        function attachedLine(message) {
            const names = (message.attachments || []).map(a => a.name).filter(Boolean);
            return names.length ? st('studio.chat.attached', { names: names.join(', ') }) : '';
        }

        // --- loading ------------------------------------------------------
        let initializing = null;

        function init() {
            if (!initializing) initializing = load();
            return initializing;
        }

        async function load() {
            const res = await api.request('GET', '/api/studio/chat-config');
            if (res.ok && res.data) {
                state.config = res.data;
                pickProvider();
            }
            state.ready = true;
            await Promise.all([loadList(), loadAttachables()]);
        }

        async function loadList() {
            state.listLoading = true;
            const q = state.query.trim();
            const res = await api.request('GET', `/api/studio/projects/${projectId}/chats${q ? `?q=${encodeURIComponent(q)}` : ''}`);
            state.listLoading = false;
            if (res.ok && res.data) state.items = res.data.items || [];
        }

        function onSearch() {
            if (searchTimer) clearTimeout(searchTimer);
            searchTimer = setTimeout(loadList, 300);
        }

        async function loadAttachables() {
            const res = await api.request('GET', `/api/studio/projects/${projectId}/attachables`);
            if (res.ok && res.data) state.attachables = res.data;
        }

        async function openChat(id) {
            state.loadingChat = true;
            state.error = '';
            const res = await api.request('GET', `/api/studio/chats/${id}`);
            state.loadingChat = false;
            if (!res.ok || !res.data) {
                state.error = errorOf(res);
                return;
            }
            state.current = res.data.chat;
            state.messages = res.data.messages || [];
            state.view = 'chat';
            if (state.config.providers[state.current.provider] && state.config.providers[state.current.provider].available) {
                state.provider = state.current.provider;
            }
            watchBusy();
            scrollToEnd();
        }

        async function reloadChat() {
            if (!state.current) return;
            const res = await api.request('GET', `/api/studio/chats/${state.current.id}`);
            if (res.ok && res.data) {
                state.current = res.data.chat;
                state.messages = res.data.messages || [];
            }
            watchBusy();
        }

        // An answer someone else (or another tab) is writing: look again until it is done.
        function watchBusy() {
            if (busyTimer) clearTimeout(busyTimer);
            busyTimer = null;
            if (state.current && state.current.busy && !state.streaming) {
                busyTimer = setTimeout(reloadChat, 3000);
            }
        }

        async function waitUntilFree() {
            for (let i = 0; i < 20; i += 1) {
                const res = await api.request('GET', `/api/studio/chats/${state.current.id}`);
                if (res.ok && res.data && !res.data.chat.busy) {
                    state.current = res.data.chat;
                    state.messages = res.data.messages || [];
                    return;
                }
                await new Promise(resolve => setTimeout(resolve, 700));
            }
            await reloadChat();
        }

        function newChat() {
            if (state.streaming) return;
            state.current = null;
            state.messages = [];
            state.error = '';
            state.webSearch = false;
            state.view = 'chat';
        }

        function backToList() {
            state.view = 'list';
            loadList();
        }

        async function createChat(body) {
            const res = await api.request('POST', `/api/studio/projects/${projectId}/chats`, body);
            if (!res.ok || !res.data) {
                state.error = errorOf(res);
                return null;
            }
            state.current = res.data;
            state.messages = [];
            state.view = 'chat';
            return res.data;
        }

        // --- streaming ----------------------------------------------------
        async function stream(url, payload) {
            state.streaming = true;
            state.error = '';
            state.live = reactive({ content: '', error: '', thinking: true });
            abort = new AbortController();
            scrollToEnd();
            let aborted = false;
            try {
                const res = await fetch(url, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream', 'X-CSRFToken': api.csrfToken() },
                    body: JSON.stringify({ ...payload, excluded_voice_ids: state.excludedVoice.slice() }),
                    signal: abort.signal,
                });
                if (!res.ok) {
                    let data = null;
                    try { data = await res.json(); } catch (err) { data = null; }
                    state.error = (data && data.error) || st('studio.common.loadFailed');
                    state.live = null;
                    return false;
                }
                const reader = res.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                for (;;) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const parsed = parseSSE(buffer);
                    buffer = parsed.rest;
                    for (const event of parsed.events) {
                        const stick = pinned();
                        if (event.type === 'text') {
                            state.live.thinking = false;
                            state.live.content += event.text;
                        } else if (event.type === 'sources') {
                            state.live.content += event.markdown || '';
                        } else if (event.type === 'error') {
                            state.live.thinking = false;
                            state.live.error = event.message;
                        }
                        if (stick) scrollToEnd();
                    }
                }
                return true;
            } catch (err) {
                if (err && err.name === 'AbortError') {
                    aborted = true;
                    if (state.live) state.live.content += STOPPED;
                } else {
                    state.error = st('studio.chat.lostConnection');
                }
                return true;
            } finally {
                abort = null;
                if (aborted || state.error) {
                    await waitUntilFree();
                } else {
                    await reloadChat();
                }
                state.live = null;
                state.streaming = false;
                scrollToEnd();
                loadList();
            }
        }

        function stop() {
            if (abort) abort.abort();
        }

        function attachmentPayload(list) {
            return list.map(a => ({ type: a.type, id: a.id }));
        }

        async function send(text, attachments, webSearch) {
            if (!state.current) {
                const made = await createChat({ kind: 'chat', provider: state.provider });
                if (!made) return false;
            }
            state.messages = state.messages.concat([{
                id: 'pending', role: 'user', content: text, is_mine: true, attachments: attachments.map(a => ({ name: a.name })),
            }]);
            const ok = await stream(`/api/studio/chats/${state.current.id}/stream`, {
                message: text, provider: state.provider, attachments: attachmentPayload(attachments), web_search: webSearch,
            });
            if (!ok) state.messages = state.messages.filter(m => m.id !== 'pending');
            return ok;
        }

        async function sendComposer() {
            const text = state.text.trim();
            if (!text || state.streaming || overLimit()) return;
            const attachments = state.selected.slice();
            state.text = '';
            state.selected = [];
            state.pickerOpen = false;
            const ok = await send(text, attachments, state.webSearch);
            if (!ok) {
                state.text = text;
                state.selected = attachments;
            }
        }

        function onComposerKeydown(event) {
            if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
                event.preventDefault();
                sendComposer();
            }
        }

        async function regenerate() {
            if (state.streaming || !state.current) return;
            const lastUser = state.messages.map(m => m.role).lastIndexOf('user');
            state.messages = state.messages.slice(0, lastUser + 1);
            await stream(`/api/studio/chats/${state.current.id}/regenerate`, {
                provider: state.provider, web_search: state.webSearch,
            });
        }

        function canRegenerate(index) {
            if (state.streaming || !state.current) return false;
            const message = state.messages[index];
            if (!message || message.role === 'user') return false;
            return state.messages.slice(index + 1).every(m => m.role !== 'user') && index === state.messages.length - 1;
        }

        function startEdit(message) {
            if (state.streaming) return;
            state.editing = { id: message.id, text: message.content };
        }

        async function saveEdit() {
            const editing = state.editing;
            if (!editing || !editing.text.trim()) return;
            const res = await api.request('PUT', `/api/studio/messages/${editing.id}`, { content: editing.text });
            if (!res.ok || !res.data) {
                state.error = errorOf(res);
                state.editing = null;
                return;
            }
            state.editing = null;
            state.current = res.data.chat;
            state.messages = res.data.messages || [];
            await stream(`/api/studio/chats/${state.current.id}/regenerate`, {
                provider: state.provider, web_search: state.webSearch,
            });
        }

        // --- the chat itself ----------------------------------------------
        async function rename(item) {
            const target = item || state.current;
            if (!target) return;
            const value = await window.promptDialog({
                title: st('studio.chat.renameTitle'), message: st('studio.chat.titleLabel'), value: target.title || '',
                confirmLabel: st('studio.common.save'), cancelLabel: st('studio.common.cancel'),
            });
            if (value === null || value === undefined) return;
            const res = await api.request('PUT', `/api/studio/chats/${target.id}`, { title: value });
            if (!res.ok) {
                state.error = errorOf(res);
                return;
            }
            if (state.current && state.current.id === target.id) state.current = res.data;
            loadList();
        }

        async function remove(item) {
            const target = item || state.current;
            if (!target) return;
            const ok = await window.confirmDialog({
                title: st('studio.chat.deleteConfirmTitle'),
                message: st('studio.chat.deleteConfirm', { title: titleOf(target) }),
                confirmLabel: st('studio.common.delete'), cancelLabel: st('studio.common.cancel'), danger: true,
            });
            if (!ok) return;
            const res = await api.request('DELETE', `/api/studio/chats/${target.id}`);
            if (!res.ok) {
                state.error = errorOf(res);
                return;
            }
            if (state.current && state.current.id === target.id) {
                state.current = null;
                state.messages = [];
                state.view = 'list';
            }
            loadList();
        }

        async function copyText(text, id) {
            try {
                await navigator.clipboard.writeText(text);
                state.copiedId = id;
                setTimeout(() => { if (state.copiedId === id) state.copiedId = null; }, 1500);
            } catch (err) { /* clipboard not allowed */ }
        }

        function copyChat() {
            const lines = state.messages.filter(m => m.role !== 'error').map(m => {
                const who = m.role === 'user' ? senderLabel(m) : (state.config.providers[m.provider] || {}).label || 'Assistant';
                return `${who}:\n${m.content}`;
            });
            copyText(lines.join('\n\n'), 'chat');
        }

        // --- attachments --------------------------------------------------
        function isSelected(type, id) {
            return state.selected.some(a => a.type === type && a.id === id);
        }

        function toggleAttach(type, item) {
            if (isSelected(type, item.id)) {
                state.selected = state.selected.filter(a => !(a.type === type && a.id === item.id));
                return;
            }
            state.selected = state.selected.concat([type === 'material'
                ? { type, id: item.id, name: item.name, size: item.size }
                : { type, id: item.id, name: item.title, chars: item.chars }]);
        }

        function selectedSize(list) {
            return (list || state.selected).reduce((sum, a) => sum + (a.type === 'material' ? (a.size || 0) : Math.min(a.chars || 0, 600000)), 0);
        }

        function overLimit(list) {
            const limits = state.config.limits || {};
            const chosen = list || state.selected;
            return (limits.max_attach_bytes && selectedSize(chosen) > limits.max_attach_bytes)
                || (limits.max_attachments && chosen.length > limits.max_attachments);
        }

        function sizeLine(list) {
            const limits = state.config.limits || {};
            const params = { size: api.formatSize(selectedSize(list)), max: api.formatSize(limits.max_attach_bytes || 0) };
            return overLimit(list) ? st('studio.chat.tooLarge', params) : st('studio.chat.selectedSize', params);
        }

        function openPicker() {
            state.pickerOpen = !state.pickerOpen;
            state.contextOpen = false;
            if (state.pickerOpen) loadAttachables();
        }

        async function onDrop(event) {
            state.dragging = false;
            const files = Array.from((event.dataTransfer && event.dataTransfer.files) || []);
            if (!files.length || !can.value.add_material) return;
            const body = new FormData();
            files.forEach(file => body.append('files', file));
            state.uploading = files.length;
            const res = await api.request('POST', `/api/studio/projects/${projectId}/materials`, body);
            state.uploading = 0;
            const stored = (res.data && res.data.stored) || [];
            const refused = (res.data && res.data.refused) || [];
            for (const m of stored) {
                state.selected = state.selected.concat([{ type: 'material', id: m.id, name: m.name, size: m.size }]);
            }
            if (refused.length) state.error = refused.map(r => r.error).join(' ');
            else if (!res.ok && !stored.length) state.error = errorOf(res);
            await loadAttachables();
            if (onMaterialsChanged) onMaterialsChanged();
        }

        // --- the context bar ----------------------------------------------
        function contextItems() {
            return (voiceItems.value || []).map(item => ({
                id: item.id,
                title: item.title,
                hidden: item.source === 'recording' && !item.recording_title,
                excluded: state.excludedVoice.includes(item.id),
            }));
        }

        function toggleContext(item) {
            if (item.hidden) return;
            state.excludedVoice = item.excluded
                ? state.excludedVoice.filter(id => id !== item.id)
                : state.excludedVoice.concat([item.id]);
        }

        function contextLabel() {
            const items = contextItems();
            if (!items.length) return st('studio.chat.context');
            const included = items.filter(i => !i.excluded && !i.hidden).length;
            return st('studio.chat.contextCount', { included, total: items.length });
        }

        function openContext() {
            state.contextOpen = !state.contextOpen;
            state.pickerOpen = false;
        }

        // --- workflows ----------------------------------------------------
        function openWorkflow(workflow) {
            state.workflow = workflow;
            state.workflowFields = Object.fromEntries(workflow.fields.map(f => [f.id, '']));
            // A workflow run wants the whole project: every material is chosen to start with.
            state.workflowMaterials = (state.attachables.materials || []).map(m => m.id);
            loadAttachables().then(() => {
                if (state.workflow === workflow) state.workflowMaterials = (state.attachables.materials || []).map(m => m.id);
            });
        }

        function toggleWorkflowMaterial(id) {
            state.workflowMaterials = state.workflowMaterials.includes(id)
                ? state.workflowMaterials.filter(x => x !== id)
                : state.workflowMaterials.concat([id]);
        }

        function workflowAttachments() {
            return (state.attachables.materials || []).filter(m => state.workflowMaterials.includes(m.id))
                .map(m => ({ type: 'material', id: m.id, name: m.name, size: m.size }));
        }

        async function startWorkflow() {
            const workflow = state.workflow;
            if (!workflow || state.starting || state.streaming) return;
            const attachments = workflowAttachments();
            if (overLimit(attachments)) return;
            state.starting = true;
            const made = await createChat({ kind: workflow.id, provider: state.provider, fields: { ...state.workflowFields } });
            state.starting = false;
            if (!made) return;
            state.workflow = null;
            state.webSearch = !!made.web_search;
            await send(made.opening_message, attachments, !!made.web_search);
        }

        // --- from the Recordings tab ---------------------------------------
        async function discussRecording(rec) {
            if (state.streaming) return;
            await init();
            if (selectTab) selectTab('chats');
            const made = await createChat({ kind: 'chat', provider: state.provider });
            if (!made) return;
            state.webSearch = false;
            const title = rec.title || ('#' + rec.id);
            await send(st('studio.chat.discussMessage', { title }), [{ type: 'recording', id: rec.id, name: title }], false);
        }

        // The HTML of an answer: always through renderMarkdown (Speakr's safe renderer), never the raw text.
        function answerHtml(message) {
            return renderMarkdown(message.content);
        }

        function liveHtml() {
            return state.live ? renderMarkdown(state.live.content) : '';
        }

        function stopTimers() {
            if (busyTimer) clearTimeout(busyTimer);
            if (searchTimer) clearTimeout(searchTimer);
            if (abort) abort.abort();
        }

        return Object.assign(state, {
            init, loadList, onSearch, openChat, newChat, backToList, sendComposer, onComposerKeydown, stop,
            regenerate, canRegenerate, startEdit, saveEdit, rename, remove, copyText, copyChat,
            providerOptions, anyProvider, setProvider, setScrollBox, titleOf, senderLabel, attachedLine,
            isSelected, toggleAttach, overLimit, sizeLine, openPicker, onDrop, loadAttachables,
            contextItems, toggleContext, contextLabel, openContext,
            openWorkflow, toggleWorkflowMaterial, startWorkflow, workflowAttachments, discussRecording,
            renderMarkdown, answerHtml, liveHtml, stopTimers,
        });
    }

    window.studioChat = { useChat, parseSSE, renderMarkdown };
})();
