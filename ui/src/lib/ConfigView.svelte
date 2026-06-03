<script>
  import { configStatus, settingsOpen } from './store.js';
  import { setConfig } from './ws.js';

  let elevenKey = '';
  let xplanePath = '';
  let justSaved = false;
  let initialized = false;

  // Prefill the X-Plane path once, from the backend's current value.
  $: cur = $configStatus?.current ?? {};
  $: if ($configStatus && !initialized) {
    xplanePath = cur.xplane_path ?? '';
    initialized = true;
  }

  $: checks = $configStatus?.checks ?? {};
  $: configured = $configStatus?.configured ?? false;
  const order = ['claude', 'voice', 'xplane_path', 'xplane_link'];

  function save() {
    const cfg = {};
    if (elevenKey.trim()) cfg.elevenlabs_api_key = elevenKey.trim();
    if (xplanePath.trim() && xplanePath.trim() !== cur.xplane_path) cfg.xplane_path = xplanePath.trim();
    if (Object.keys(cfg).length === 0) return;
    setConfig(cfg);
    elevenKey = '';
    justSaved = true;
    setTimeout(() => (justSaved = false), 2000);
  }

  function close() { settingsOpen.set(false); }
</script>

<div class="overlay">
  <div class="card">
    <div class="head">
      <h1>{configured ? 'Settings' : 'Welcome — let’s get you set up'}</h1>
      {#if configured}
        <button class="close" onclick={close} title="Close">✕</button>
      {/if}
    </div>

    {#if !configured}
      <p class="lead">Add your ElevenLabs key for voice. Claude uses the CLI you’re
        already signed into. You can change any of this later from Settings.</p>
    {/if}

    <!-- Doctor checklist -->
    <div class="checks">
      {#each order as key}
        {#if checks[key]}
          <div class="check" class:ok={checks[key].ok} class:bad={!checks[key].ok}>
            <span class="mark">{checks[key].ok ? '✓' : '○'}</span>
            <span class="check-label">{checks[key].label}</span>
            <span class="check-detail">{checks[key].detail}</span>
          </div>
        {/if}
      {/each}
    </div>

    <!-- Fields -->
    <label class="field">
      <span class="field-label">ElevenLabs API key {#if cur.has_elevenlabs}<em>(set — paste to replace)</em>{/if}</span>
      <input type="password" bind:value={elevenKey} placeholder={cur.has_elevenlabs ? '•••••••• stored' : 'sk_…'} spellcheck="false" autocomplete="off" />
      <span class="hint">Get one at <span class="url">elevenlabs.io</span> → Profile → API Keys.</span>
    </label>

    <label class="field">
      <span class="field-label">X-Plane install path</span>
      <input type="text" bind:value={xplanePath} placeholder="/Applications/X-Plane 12" spellcheck="false" />
      <span class="hint">Only needed if it isn’t the default Steam location. Used to find airport data (apt.dat).</span>
    </label>

    <div class="actions">
      <button class="save" onclick={save}>{justSaved ? 'Saved ✓' : 'Save'}</button>
      {#if !configured}
        <span class="note">The app starts once Claude, a voice key, and X-Plane data are all green.</span>
      {/if}
    </div>
  </div>
</div>

<style>
  .overlay {
    position: fixed; inset: 0; z-index: 50;
    display: flex; align-items: center; justify-content: center;
    background: rgba(13, 17, 23, 0.85); backdrop-filter: blur(3px);
    padding: 24px;
  }
  .card {
    width: 100%; max-width: 540px;
    background: var(--bg-panel); border: 1px solid var(--border);
    border-radius: 8px; padding: 24px;
    box-shadow: 0 12px 40px rgba(0,0,0,0.5);
    display: flex; flex-direction: column; gap: 16px;
  }
  .head { display: flex; align-items: center; justify-content: space-between; }
  h1 { font-size: 16px; font-weight: 700; color: var(--text); }
  .close {
    background: none; color: var(--text-muted); font-size: 16px;
    width: 28px; height: 28px; border-radius: var(--radius);
  }
  .close:hover { background: var(--bg-input); color: var(--text); }
  .lead { color: var(--text-muted); font-size: 12px; line-height: 1.5; }

  .checks { display: flex; flex-direction: column; gap: 6px;
    background: var(--bg-base); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px; }
  .check { display: grid; grid-template-columns: 16px 150px 1fr; align-items: baseline; gap: 8px; font-size: 12px; }
  .check .mark { font-weight: 700; }
  .check.ok  .mark { color: var(--accent-green); }
  .check.bad .mark { color: var(--accent-amber); }
  .check-label { color: var(--text); }
  .check-detail { color: var(--text-muted); }

  .field { display: flex; flex-direction: column; gap: 5px; }
  .field-label { font-size: 12px; color: var(--text); font-weight: 600; }
  .field-label em { color: var(--text-muted); font-weight: 400; font-style: normal; }
  .field input { padding: 8px 10px; font-size: 13px; }
  .hint { font-size: 11px; color: var(--text-muted); }
  .url { color: var(--accent-blue); }

  .actions { display: flex; align-items: center; gap: 12px; margin-top: 4px; }
  .save {
    background: var(--accent-blue); color: #0d1117; font-weight: 700;
    padding: 8px 18px; border-radius: var(--radius);
  }
  .save:hover { opacity: 0.9; }
  .note { font-size: 11px; color: var(--text-muted); }
</style>
