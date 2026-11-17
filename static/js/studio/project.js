/**
 * A project's home page (templates/studio/project.html): details,
 * recordings, materials, voice references, chats (static/js/studio/chat.js)
 * and the People list.
 *
 * Recordings are uploaded through Speakr's own upload route (POST /upload)
 * with the project's folder, so Speakr's queue transcribes them as any other
 * upload; the people picker uses Speakr's own user search
 * (GET /api/users/search), which follows the site's user search setting.
 */
(function () {
    'use strict';

    const page = window.__STUDIO_PAGE || {};
    const api = window.studioApi;
    const projectId = page.projectId;
    const DONE = ['COMPLETED', 'FAILED'];
    const POLL_MS = 10000;

    async function start() {
        await api.initUpstreamI18n(page.userLanguage);

        const { ref, reactive, computed, onMounted, onBeforeUnmount, watch } = Vue;

        Vue.createApp({
            setup() {
                const st = window.studioT;
                const language = page.userLanguage || 'en';
                const tab = ref('details');
                const loading = ref(true);
                const loadError = ref(false);
                const project = ref(null);
                const flash = ref('');
                const flashError = ref('');

                function say(message, isError) {
                    flash.value = isError ? '' : message;
                    flashError.value = isError ? message : '';
                }

                function errorOf(res) {
                    return (res.data && res.data.error) || st('studio.common.loadFailed');
                }

                const can = computed(() => (project.value && project.value.can) || {});

                // The project switcher on the project's name (static/js/studio/switcher.js).
                const switcher = window.studioSwitcher.useSwitcher({ currentId: projectId, api, st });

                async function loadProject() {
                    const res = await api.request('GET', `/api/studio/projects/${projectId}`);
                    if (res.ok && res.data) {
                        project.value = res.data;
                        resetPeopleDraft();
                        return true;
                    }
                    if (res.status === 404) {
                        window.location.href = '/studio';
                    }
                    return false;
                }

                // --- details -------------------------------------------------
                const editing = ref(false);
                const saving = ref(false);
                const draft = reactive({ name: '', logline: '', notes: '', hotwords: '', initial_prompt: '' });

                function startEdit() {
                    const p = project.value;
                    Object.assign(draft, {
                        name: p.name, logline: p.logline, notes: p.notes,
                        hotwords: p.hotwords, initial_prompt: p.initial_prompt,
                    });
                    editing.value = true;
                    say('');
                }

                async function saveDetails() {
                    if (saving.value) return;
                    const body = { logline: draft.logline, notes: draft.notes };
                    if (can.value.rename) body.name = draft.name;
                    if (can.value.edit_hints) {
                        body.hotwords = draft.hotwords;
                        body.initial_prompt = draft.initial_prompt;
                    }
                    saving.value = true;
                    const res = await api.request('PUT', `/api/studio/projects/${projectId}`, body);
                    saving.value = false;
                    if (res.ok && res.data) {
                        project.value = res.data;
                        editing.value = false;
                        document.title = `${res.data.name} - Speakr`;
                        if (body.name !== undefined) switcher.load();  // the new name in the switcher too
                    } else {
                        say(errorOf(res), true);
                    }
                }

                async function deleteProject() {
                    const ok = await window.confirmDialog({
                        title: st('studio.project.details.deleteConfirmTitle'),
                        message: st('studio.project.details.deleteConfirm', { name: project.value.name }),
                        confirmLabel: st('studio.common.delete'),
                        cancelLabel: st('studio.common.cancel'),
                        danger: true,
                    });
                    if (!ok) return;
                    const res = await api.request('DELETE', `/api/studio/projects/${projectId}`);
                    if (res.ok) {
                        window.location.href = '/studio';
                    } else {
                        say(errorOf(res), true);
                    }
                }

                // --- recordings ----------------------------------------------
                const recordings = ref([]);
                // Recordings in the project the viewer cannot open: {count, owners}.
                const hiddenRecordings = ref({ count: 0, owners: [] });
                const uploads = ref([]);
                const recordingInput = ref(null);
                const recordingDrag = ref(false);
                let pollTimer = null;

                async function loadRecordings() {
                    const res = await api.request('GET', `/api/studio/projects/${projectId}/recordings`);
                    if (res.ok && res.data) {
                        recordings.value = res.data.items || [];
                        hiddenRecordings.value = res.data.hidden || { count: 0, owners: [] };
                    }
                    schedulePoll();
                }

                const hiddenRecordingsLine = computed(() => {
                    const hidden = hiddenRecordings.value;
                    if (!hidden.count) return '';
                    const params = { count: hidden.count, owners: (hidden.owners || []).join(', ') };
                    return recordings.value.length
                        ? st('studio.project.recordings.hiddenSome', params)
                        : st('studio.project.recordings.hiddenAll', params);
                });

                function schedulePoll() {
                    if (pollTimer) clearTimeout(pollTimer);
                    pollTimer = null;
                    const busy = recordings.value.some(r => !DONE.includes(r.status))
                        || uploads.value.some(u => u.state === 'uploading' || u.state === 'processing');
                    if (busy) pollTimer = setTimeout(loadRecordings, POLL_MS);
                }

                function statusLabel(status) {
                    const key = `studio.project.recordings.status.${status}`;
                    const label = st(key);
                    return label === key ? status : label;
                }

                function statusClass(status) {
                    if (status === 'COMPLETED') return 'pill-status pill-status--success';
                    if (status === 'FAILED') return 'pill-status pill-status--danger';
                    return 'pill-status pill-status--processing';
                }

                function uploadLine(item) {
                    if (item.state === 'uploading') return st('studio.project.recordings.uploading', { name: item.name, percent: api.formatPercent(item.percent) });
                    if (item.state === 'processing') return st('studio.project.recordings.processing', { name: item.name });
                    if (item.state === 'done') return st('studio.project.recordings.queued', { name: item.name });
                    return st('studio.project.recordings.failed', { name: item.name, error: item.error });
                }

                function pickRecordings() {
                    if (recordingInput.value) recordingInput.value.click();
                }

                async function folderForUpload() {
                    const res = await api.request('POST', `/api/studio/projects/${projectId}/recording-folder`);
                    if (res.ok && res.data && res.data.folder_id) return res.data.folder_id;
                    throw new Error(errorOf(res));
                }

                function sendRecording(file, folderId, item) {
                    return new Promise((resolve) => {
                        const body = new FormData();
                        body.append('file', file);
                        body.append('folder_id', String(folderId));
                        const xhr = new XMLHttpRequest();
                        xhr.upload.onprogress = (e) => {
                            if (e.lengthComputable) item.percent = Math.round((e.loaded / e.total) * 100);
                        };
                        xhr.upload.onload = () => { item.state = 'processing'; };
                        xhr.onload = () => {
                            let data = null;
                            try { data = JSON.parse(xhr.responseText); } catch (err) { data = null; }
                            if (xhr.status >= 200 && xhr.status < 300 && data && (data.id || data.join_pending)) {
                                item.state = 'done';
                            } else {
                                item.state = 'failed';
                                item.error = (data && data.error) || `HTTP ${xhr.status}`;
                            }
                            resolve();
                        };
                        xhr.onerror = () => {
                            item.state = 'failed';
                            item.error = st('studio.common.loadFailed');
                            resolve();
                        };
                        xhr.open('POST', '/upload');
                        xhr.setRequestHeader('X-CSRFToken', api.csrfToken());
                        xhr.send(body);
                    });
                }

                async function uploadRecordings(fileList) {
                    const files = Array.from(fileList || []);
                    if (!files.length) return;
                    let folderId;
                    try {
                        folderId = await folderForUpload();
                    } catch (err) {
                        say(err.message, true);
                        return;
                    }
                    const items = files.map(file => reactive({ name: file.name, percent: 0, state: 'uploading', error: '' }));
                    uploads.value = uploads.value.filter(u => u.state !== 'done').concat(items);
                    for (let i = 0; i < files.length; i += 1) {
                        await sendRecording(files[i], folderId, items[i]);
                    }
                    await loadProject();
                    await loadRecordings();
                }

                function onRecordingFiles(event) {
                    uploadRecordings(event.target.files);
                    event.target.value = '';
                }

                function onRecordingDrop(event) {
                    recordingDrag.value = false;
                    if (!can.value.add_recording) return;
                    uploadRecordings(event.dataTransfer && event.dataTransfer.files);
                }

                // --- materials -----------------------------------------------
                const materials = ref([]);
                const materialInput = ref(null);
                const materialDrag = ref(false);
                const materialUploading = ref(0);
                const materialRefused = ref([]);
                const openText = reactive({});

                async function loadMaterials() {
                    const res = await api.request('GET', `/api/studio/projects/${projectId}/materials`);
                    if (res.ok && res.data) materials.value = res.data.items || [];
                }

                function pickMaterials() {
                    if (materialInput.value) materialInput.value.click();
                }

                async function uploadMaterials(fileList) {
                    const files = Array.from(fileList || []);
                    if (!files.length) return;
                    const body = new FormData();
                    files.forEach(file => body.append('files', file));
                    materialUploading.value = files.length;
                    materialRefused.value = [];
                    const res = await api.request('POST', `/api/studio/projects/${projectId}/materials`, body);
                    materialUploading.value = 0;
                    if (res.data && Array.isArray(res.data.refused)) {
                        materialRefused.value = res.data.refused.map(r => r.error);
                    } else if (!res.ok) {
                        materialRefused.value = [errorOf(res)];
                    }
                    await loadMaterials();
                }

                function onMaterialFiles(event) {
                    uploadMaterials(event.target.files);
                    event.target.value = '';
                }

                function onMaterialDrop(event) {
                    materialDrag.value = false;
                    if (!can.value.add_material) return;
                    uploadMaterials(event.dataTransfer && event.dataTransfer.files);
                }

                function materialUrl(material, download) {
                    return `/api/studio/materials/${material.id}/file${download ? '?download=1' : ''}`;
                }

                function materialIcon(material) {
                    if (material.kind === 'pdf') return 'fas fa-file-pdf';
                    if (material.kind === 'pptx') return 'fas fa-file-powerpoint';
                    return 'fas fa-file-image';
                }

                function materialMeta(material) {
                    return st('studio.project.materials.byOn', {
                        size: api.formatSize(material.size), date: api.formatDate(material.created_at),
                    });
                }

                async function toggleMaterialText(material) {
                    const key = `m${material.id}`;
                    if (openText[key] !== undefined) {
                        delete openText[key];
                        return;
                    }
                    const res = await api.request('GET', `/api/studio/materials/${material.id}`);
                    openText[key] = res.ok && res.data ? (res.data.text || st('studio.project.materials.noText')) : errorOf(res);
                }

                async function deleteMaterial(material) {
                    const ok = await window.confirmDialog({
                        message: material.name, confirmLabel: st('studio.common.delete'),
                        cancelLabel: st('studio.common.cancel'), danger: true,
                    });
                    if (!ok) return;
                    const res = await api.request('DELETE', `/api/studio/materials/${material.id}`);
                    if (!res.ok) say(errorOf(res), true);
                    await loadMaterials();
                }

                // --- voice references ----------------------------------------
                const voiceItems = ref([]);
                const voiceRecording = ref('');
                const voiceForm = reactive({ title: '', text: '' });
                const voiceBusy = ref(false);
                const voiceError = ref('');
                const voiceFileInput = ref(null);

                const finishedRecordings = computed(() => recordings.value.filter(r => r.status === 'COMPLETED'));

                async function loadVoice() {
                    const res = await api.request('GET', `/api/studio/projects/${projectId}/voice`);
                    if (res.ok && res.data) voiceItems.value = res.data.items || [];
                }

                async function addVoice(body) {
                    if (voiceBusy.value) return false;
                    voiceBusy.value = true;
                    voiceError.value = '';
                    const res = await api.request('POST', `/api/studio/projects/${projectId}/voice`, body);
                    voiceBusy.value = false;
                    if (!res.ok) {
                        voiceError.value = errorOf(res);
                        return false;
                    }
                    await loadVoice();
                    return true;
                }

                async function addVoiceFromRecording() {
                    if (!voiceRecording.value) return;
                    if (await addVoice({ recording_id: Number(voiceRecording.value) })) voiceRecording.value = '';
                }

                async function addVoiceText() {
                    if (await addVoice({ title: voiceForm.title, text: voiceForm.text })) {
                        voiceForm.title = '';
                        voiceForm.text = '';
                    }
                }

                function pickVoiceFile() {
                    if (voiceFileInput.value) voiceFileInput.value.click();
                }

                function onVoiceFile(event) {
                    const file = event.target.files && event.target.files[0];
                    event.target.value = '';
                    if (!file) return;
                    const reader = new FileReader();
                    reader.onload = () => {
                        voiceForm.text = String(reader.result || '');
                        if (!voiceForm.title) voiceForm.title = file.name;
                    };
                    reader.readAsText(file);
                }

                function voiceSource(item) {
                    if (item.source === 'recording') {
                        return `${st('studio.project.voice.sourceRecording')}: ${item.recording_title || st('studio.project.voice.recordingHidden')}`;
                    }
                    return st('studio.project.voice.sourceText', { chars: item.chars });
                }

                async function toggleVoiceText(item) {
                    const key = `v${item.id}`;
                    if (openText[key] !== undefined) {
                        delete openText[key];
                        return;
                    }
                    const res = await api.request('GET', `/api/studio/voice/${item.id}`);
                    openText[key] = res.ok && res.data ? (res.data.text || '') : errorOf(res);
                }

                async function deleteVoice(item) {
                    const ok = await window.confirmDialog({
                        message: item.title, confirmLabel: st('studio.common.delete'),
                        cancelLabel: st('studio.common.cancel'), danger: true,
                    });
                    if (!ok) return;
                    const res = await api.request('DELETE', `/api/studio/voice/${item.id}`);
                    if (!res.ok) say(errorOf(res), true);
                    await loadVoice();
                }

                // --- people --------------------------------------------------
                const peopleDraft = ref([]);
                const peopleQuery = ref('');
                const peopleResults = ref([]);
                const peopleSearching = ref(false);
                const peopleSaving = ref(false);
                const peopleError = ref('');
                let searchTimer = null;

                function resetPeopleDraft() {
                    const p = project.value;
                    peopleDraft.value = p ? p.people.filter(x => x.role === 'member').map(x => ({ ...x, isNew: false })) : [];
                }

                const peopleChanged = computed(() => {
                    if (!project.value) return false;
                    const saved = project.value.people.filter(x => x.role === 'member').map(x => x.id).sort().join(',');
                    return saved !== peopleDraft.value.map(x => x.id).sort().join(',');
                });

                const scopeHint = computed(() => {
                    const scope = project.value && project.value.user_search_scope;
                    if (scope === 'everyone') return st('studio.project.people.scopeEveryone');
                    if (scope === 'groups') return st('studio.project.people.scopeGroups');
                    return st('studio.project.people.scopeExact');
                });

                watch(peopleQuery, (value) => {
                    if (searchTimer) clearTimeout(searchTimer);
                    const q = value.trim();
                    if (q.length < 2) {
                        peopleResults.value = [];
                        return;
                    }
                    searchTimer = setTimeout(() => searchPeople(q), 350);
                });

                async function searchPeople(q) {
                    peopleSearching.value = true;
                    const res = await api.request('GET', `/api/users/search?q=${encodeURIComponent(q)}`);
                    peopleSearching.value = false;
                    if (peopleQuery.value.trim() !== q) return;
                    const taken = new Set([project.value.owner_id, ...peopleDraft.value.map(x => x.id)]);
                    peopleResults.value = res.ok && Array.isArray(res.data) ? res.data.filter(u => !taken.has(u.id)) : [];
                }

                function addPerson(user) {
                    peopleDraft.value = peopleDraft.value.concat([{ id: user.id, username: user.username, role: 'member', isNew: true }]);
                    peopleResults.value = peopleResults.value.filter(u => u.id !== user.id);
                    peopleQuery.value = '';
                }

                function removePerson(person) {
                    peopleDraft.value = peopleDraft.value.filter(x => x.id !== person.id);
                }

                async function savePeople() {
                    if (peopleSaving.value) return;
                    const removing = project.value.people.some(x => x.role === 'member' && !peopleDraft.value.some(d => d.id === x.id));
                    if (removing) {
                        const ok = await window.confirmDialog({
                            title: st('studio.project.people.removeConfirmTitle'),
                            message: st('studio.project.people.removeConfirm'),
                            confirmLabel: st('studio.common.save'), cancelLabel: st('studio.common.cancel'), danger: true,
                        });
                        if (!ok) return;
                    }
                    peopleSaving.value = true;
                    peopleError.value = '';
                    const res = await api.request('PUT', `/api/studio/projects/${projectId}/people`, {
                        user_ids: peopleDraft.value.map(x => x.id),
                        // Speakr accepts a new person's id when the full username names them too.
                        identifiers: peopleDraft.value.filter(x => x.isNew).map(x => x.username),
                    });
                    peopleSaving.value = false;
                    if (res.ok && res.data) {
                        project.value = res.data;
                        resetPeopleDraft();
                        say(st('studio.project.people.saved'));
                        await loadRecordings();
                    } else {
                        peopleError.value = errorOf(res);
                    }
                }

                // --- chats ---------------------------------------------------
                // static/js/studio/chat.js; loaded when the Chats tab is first opened.
                const chat = window.studioChat.useChat({
                    projectId, api, st, can, voiceItems,
                    onMaterialsChanged: loadMaterials,
                    selectTab: (id) => selectTab(id),
                });

                // --- start ---------------------------------------------------
                const counts = computed(() => ({
                    recordings: recordings.value.length + (hiddenRecordings.value.count || 0),
                    materials: materials.value.length,
                    voice: voiceItems.value.length,
                    people: project.value ? project.value.people.length : 0,
                }));

                const tabs = computed(() => [
                    { id: 'details', icon: 'fas fa-info-circle', count: null },
                    { id: 'recordings', icon: 'fas fa-microphone', count: counts.value.recordings },
                    { id: 'materials', icon: 'fas fa-paperclip', count: counts.value.materials },
                    { id: 'voice', icon: 'fas fa-comment-dots', count: counts.value.voice },
                    { id: 'chats', icon: 'fas fa-comments', count: chat.ready ? chat.items.length : null },
                    { id: 'people', icon: 'fas fa-users', count: counts.value.people },
                ]);

                async function loadAll() {
                    loading.value = true;
                    loadError.value = false;
                    const ok = await loadProject();
                    if (!ok) {
                        loadError.value = true;
                        loading.value = false;
                        return;
                    }
                    // The switcher's list loads beside the rest, so it opens at once.
                    switcher.load();
                    await Promise.all([loadRecordings(), loadMaterials(), loadVoice()]);
                    loading.value = false;
                    const wanted = (window.location.hash || '').replace('#', '');
                    if (tabs.value.some(t => t.id === wanted)) tab.value = wanted;
                    // The chats' list and models load after the rest: the Recordings tab's
                    // "Discuss in chat" needs to know whether a model is available.
                    chat.init();
                }

                function selectTab(id) {
                    tab.value = id;
                    say('');
                    if (id === 'chats') chat.loadList();
                    try {
                        window.history.replaceState({}, '', `${window.location.pathname}#${id}`);
                    } catch (err) { /* best effort */ }
                }

                onMounted(loadAll);
                onBeforeUnmount(() => {
                    if (pollTimer) clearTimeout(pollTimer);
                    chat.stopTimers();
                });

                return {
                    st, api, language, tab, tabs, selectTab, loading, loadError, loadAll, project, can, flash, flashError,
                    switcher, chat,
                    editing, saving, draft, startEdit, saveDetails, deleteProject,
                    recordings, hiddenRecordings, hiddenRecordingsLine,
                    uploads, recordingInput, recordingDrag, statusLabel, statusClass, uploadLine,
                    pickRecordings, onRecordingFiles, onRecordingDrop,
                    materials, materialInput, materialDrag, materialUploading, materialRefused, openText,
                    pickMaterials, onMaterialFiles, onMaterialDrop, materialUrl, materialIcon, materialMeta,
                    toggleMaterialText, deleteMaterial,
                    voiceItems, voiceRecording, voiceForm, voiceBusy, voiceError, voiceFileInput, finishedRecordings,
                    addVoiceFromRecording, addVoiceText, pickVoiceFile, onVoiceFile, voiceSource, toggleVoiceText, deleteVoice,
                    peopleDraft, peopleQuery, peopleResults, peopleSearching, peopleSaving, peopleError, peopleChanged,
                    scopeHint, addPerson, removePerson, savePeople, resetPeopleDraft,
                    sourceUrl: page.sourceUrl || '',
                };
            },
            delimiters: ['${', '}'],
        }).mount('#app');

        api.hideLoader();
    }

    start();
})();
