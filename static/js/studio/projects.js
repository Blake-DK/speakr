/**
 * The Projects page of the studio add-on (templates/studio/projects.html):
 * the projects the person may see, the most recently opened first (the
 * order of the project switcher), and a new project.
 */
(function () {
    'use strict';

    const page = window.__STUDIO_PAGE || {};
    const api = window.studioApi;

    async function start() {
        await api.initUpstreamI18n(page.userLanguage);

        const { ref, computed, onMounted, nextTick } = Vue;

        Vue.createApp({
            setup() {
                const st = window.studioT;
                const loading = ref(true);
                const loadError = ref(false);
                const projects = ref([]);
                const showForm = ref(false);
                const form = ref({ name: '', logline: '' });
                const formError = ref('');
                const creating = ref(false);
                const nameInput = ref(null);

                async function load() {
                    loading.value = true;
                    loadError.value = false;
                    const res = await api.request('GET', '/api/studio/projects');
                    if (res.ok && res.data) {
                        projects.value = res.data.projects || [];
                    } else {
                        loadError.value = true;
                    }
                    loading.value = false;
                }

                async function openForm() {
                    showForm.value = true;
                    formError.value = '';
                    await nextTick();
                    if (nameInput.value) nameInput.value.focus();
                }

                function closeForm() {
                    showForm.value = false;
                    form.value = { name: '', logline: '' };
                    formError.value = '';
                }

                async function create() {
                    if (creating.value) return;
                    if (!form.value.name.trim()) {
                        formError.value = st('studio.errors.nameRequired');
                        return;
                    }
                    creating.value = true;
                    formError.value = '';
                    const res = await api.request('POST', '/api/studio/projects', {
                        name: form.value.name, logline: form.value.logline,
                    });
                    creating.value = false;
                    if (res.ok && res.data && res.data.id) {
                        window.location.href = `/studio/projects/${res.data.id}`;
                        return;
                    }
                    formError.value = (res.data && res.data.error) || st('studio.common.loadFailed');
                }

                function counts(project) {
                    // Recordings the viewer cannot open are counted and said so,
                    // never left out (a project with some must not read "0 recordings").
                    const hidden = project.hidden_recordings || { count: 0, owners: [] };
                    const params = {
                        recordings: project.recording_count, materials: project.material_count,
                        count: hidden.count, owners: (hidden.owners || []).join(', '),
                    };
                    if (!hidden.count) return st('studio.projects.counts', params);
                    if (!project.recording_count) return st('studio.projects.countsHiddenAll', params);
                    return st('studio.projects.countsHiddenSome', params);
                }

                const hasProjects = computed(() => projects.value.length > 0);

                onMounted(() => {
                    load();
                    // "New project" in a project's switcher links here with ?new=1.
                    const params = new URLSearchParams(window.location.search);
                    if (params.has('new')) {
                        openForm();
                        try {
                            window.history.replaceState({}, '', window.location.pathname);
                        } catch (err) { /* best effort */ }
                    }
                });

                return {
                    st, loading, loadError, projects, hasProjects, showForm, form, formError, creating,
                    nameInput, load, openForm, closeForm, create, counts,
                };
            },
            delimiters: ['${', '}'],
        }).mount('#app');

        api.hideLoader();
    }

    start();
})();
