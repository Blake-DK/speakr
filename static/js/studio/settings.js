/**
 * The studio's chat settings (templates/studio/settings.html), for
 * administrators: each provider's model, whether it is switched on, its
 * live model list and a connection test. API keys are set on the server
 * only; the page learns no more than whether one is set.
 */
(function () {
    'use strict';

    const page = window.__STUDIO_PAGE || {};
    const api = window.studioApi;

    async function start() {
        await api.initUpstreamI18n(page.userLanguage);
        const { ref, reactive, onMounted } = Vue;

        Vue.createApp({
            setup() {
                const st = window.studioT;
                const names = ['claude', 'openai'];
                const loading = ref(true);
                const loadError = ref(false);
                const info = reactive({});
                const keyEnv = reactive({});
                const defaults = reactive({});
                const draft = reactive({ claude: { model: '', enabled: true }, openai: { model: '', enabled: true } });
                const models = reactive({});
                const modelErrors = reactive({});
                const loadingModels = ref(false);
                const testing = reactive({});
                const testResult = reactive({});
                const saving = ref(false);
                const saved = ref(false);
                const saveError = ref('');

                function take(data) {
                    Object.assign(info, data.providers || {});
                    Object.assign(keyEnv, data.key_env || {});
                    Object.assign(defaults, data.defaults || {});
                    for (const name of names) {
                        const p = info[name] || {};
                        draft[name].model = p.model && p.model !== defaults[name] ? p.model : '';
                        draft[name].enabled = p.enabled !== false;
                    }
                }

                async function load() {
                    const res = await api.request('GET', '/api/studio/settings');
                    loading.value = false;
                    if (res.ok && res.data) take(res.data);
                    else loadError.value = true;
                }

                async function loadModels() {
                    loadingModels.value = true;
                    const res = await api.request('GET', '/api/studio/settings/models');
                    loadingModels.value = false;
                    for (const name of names) {
                        models[name] = res.ok && res.data ? (res.data[name] || []) : [];
                        modelErrors[name] = res.ok && res.data && res.data.errors ? (res.data.errors[name] || '') : '';
                    }
                }

                async function test(name) {
                    testing[name] = true;
                    testResult[name] = null;
                    const res = await api.request('POST', '/api/studio/settings/test', { provider: name, model: draft[name].model });
                    testing[name] = false;
                    testResult[name] = res.data && typeof res.data.ok === 'boolean'
                        ? res.data
                        : { ok: false, message: (res.data && res.data.error) || st('studio.common.loadFailed') };
                }

                async function save() {
                    saving.value = true;
                    saved.value = false;
                    saveError.value = '';
                    const body = {};
                    for (const name of names) {
                        body[`${name}_model`] = draft[name].model;
                        body[`${name}_enabled`] = draft[name].enabled;
                    }
                    const res = await api.request('PUT', '/api/studio/settings', body);
                    saving.value = false;
                    if (res.ok && res.data) {
                        take(res.data);
                        saved.value = true;
                    } else {
                        saveError.value = (res.data && res.data.error) || st('studio.common.loadFailed');
                    }
                }

                onMounted(load);
                return {
                    st, names, loading, loadError, info, keyEnv, defaults, draft, models, modelErrors, loadingModels,
                    testing, testResult, saving, saved, saveError, loadModels, test, save,
                };
            },
            delimiters: ['${', '}'],
        }).mount('#app');

        api.hideLoader();
    }

    start();
})();
