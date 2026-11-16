/**
 * The Projects page of the studio add-on (templates/studio/projects.html):
 * the projects the person may see, and a new project.
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
                    return st('studio.projects.counts', {
                        recordings: project.recording_count, materials: project.material_count,
                    });
                }

                const hasProjects = computed(() => projects.value.length > 0);

                onMounted(load);

                return {
                    st, loading, loadError, projects, hasProjects, showForm, form, formError, creating,
                    nameInput, load, openForm, closeForm, create, counts,
                    sourceUrl: page.sourceUrl || '',
                };
            },
            delimiters: ['${', '}'],
        }).mount('#app');

        api.hideLoader();
    }

    start();
})();
