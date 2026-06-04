<script>
  import { tick } from 'svelte';
  import { messages, thinking, atcCallsign, station, pttActive, transcription, audioEnabled } from './store.js';
  import { sendTransmission, startPTT, stopPTT, requestMicPermission, newFlight } from './ws.js';

  let input = $state('');
  let chatEl;

  const stationFreq = {
    GND: 'GND', TWR: 'TWR', APP: 'APP', DEP: 'DEP', RADAR: 'RAD', FIS: 'FIS'
  };

  function fmtTime(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function transmit() {
    const text = input.trim();
    if (!text) return;
    sendTransmission(text);
    input = '';
  }

  function onKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      transmit();
    }
  }

  async function enableMic() {
    const ok = await requestMicPermission();
    if (ok) audioEnabled.set(true);
  }

  // Spacebar PTT — only when focus is not in the text input
  function onWindowKeydown(e) {
    if (e.code === 'Space' && !e.repeat && e.target.tagName !== 'TEXTAREA') {
      e.preventDefault();
      if ($audioEnabled) startPTT();
    }
  }

  function onWindowKeyup(e) {
    if (e.code === 'Space' && $audioEnabled && e.target.tagName !== 'TEXTAREA') stopPTT();
  }

  // Auto-scroll to bottom on new messages
  $effect(() => {
    void $messages;
    tick().then(() => {
      if (chatEl) chatEl.scrollTop = chatEl.scrollHeight;
    });
  });
</script>

<svelte:window onkeydown={onWindowKeydown} onkeyup={onWindowKeyup} />

<main class="radio-pane">
  <!-- Station header -->
  <div class="station-header">
    <span class="station-dot"></span>
    <span class="station-name">{$atcCallsign}</span>
    <span class="station-code">{stationFreq[$station] ?? $station}</span>
    <button class="new-flight-btn" onclick={newFlight} title="New flight — clears chat and resets session">↺</button>
  </div>

  <!-- Message history -->
  <div class="chat" bind:this={chatEl}>
    {#if $messages.length === 0}
      <div class="empty-chat">
        <div class="empty-icon gi">📻</div>
        <div class="empty-text">Awaiting first transmission…</div>
      </div>
    {/if}

    {#each $messages as msg}
      {#if msg.role === 'atc'}
        <div class="bubble atc">
          <div class="bubble-meta">
            <span class="role-label atc-label">ATC</span>
            {#if msg.model}
              <span class="model-tag">{msg.model.includes('opus') ? 'Opus' : 'Sonnet'}</span>
            {/if}
            <span class="ts">{fmtTime(msg.timestamp)}</span>
          </div>
          <div class="bubble-text">{msg.text}</div>
        </div>
      {:else if msg.role === 'pilot'}
        <div class="bubble pilot">
          <div class="bubble-meta right">
            <span class="ts">{fmtTime(msg.timestamp)}</span>
            <span class="role-label pilot-label">PILOT</span>
          </div>
          <div class="bubble-text">{msg.text}</div>
        </div>
      {:else if msg.role === 'ambient'}
        <div class="ambient-line" title="Other traffic on this frequency">
          <span class="ambient-tag">{msg.speaker === 'atc' ? 'ATC' : (msg.callsign ?? 'TFC')}</span>
          <span class="ambient-text">{msg.text}</span>
        </div>
      {:else}
        <div class="bubble system">
          <div class="bubble-text">{msg.text}</div>
        </div>
      {/if}
    {/each}

    {#if $thinking}
      <div class="thinking">
        <span class="think-dot"></span>
        <span class="think-dot"></span>
        <span class="think-dot"></span>
        <span class="think-label">Station responding…</span>
      </div>
    {/if}
  </div>

  <!-- Transcription preview (shows what STT heard before LLM fires) -->
  {#if $transcription}
    <div class="transcription-preview">
      <span class="tx-icon gi" data-tech="RX">🎙</span>
      <span class="tx-text">{$transcription}</span>
    </div>
  {/if}

  <!-- Input -->
  <div class="input-row">
    <textarea
      class="tx-input"
      placeholder="Transmit… (Enter to send)"
      bind:value={input}
      onkeydown={onKeydown}
      rows="2"
      disabled={$thinking}
    ></textarea>
    <div class="tx-btns">
      {#if $audioEnabled}
        <button
          class="ptt-btn"
          class:ptt-active={$pttActive}
          onmousedown={startPTT}
          onmouseup={stopPTT}
          onmouseleave={stopPTT}
          ontouchstart={(e) => { e.preventDefault(); startPTT(); }}
          ontouchend={stopPTT}
          disabled={$thinking}
          title="Push to Talk (or hold Space)"
        >{#if $pttActive}● REC{:else}<span class="gi">🎙</span> PTT{/if}</button>
      {:else}
        <button class="mic-btn" onclick={enableMic} title="Enable microphone"><span class="gi" data-tech="MIC">🎙</span></button>
      {/if}
      <button
        class="tx-btn"
        onclick={transmit}
        disabled={$thinking || !input.trim()}
      >▶ TX</button>
    </div>
  </div>
</main>

<style>
  .radio-pane {
    background: var(--bg-base);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    border-left: 1px solid var(--border);
    border-right: 1px solid var(--border);
  }

  .station-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 14px;
    background: var(--bg-panel);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .station-dot {
    width: 8px; height: 8px;
    background: var(--accent-green);
    border-radius: 50%;
    box-shadow: 0 0 6px var(--accent-green);
    flex-shrink: 0;
  }
  .station-name { font-weight: 700; font-size: 14px; color: var(--text); }
  .station-code {
    margin-left: auto;
    font-size: 11px;
    font-weight: 700;
    color: var(--text-dim);
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 1px 6px;
  }

  .new-flight-btn {
    font-size: 14px;
    padding: 2px 6px;
    background: none;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--text-dim);
    transition: color 0.15s, border-color 0.15s;
    line-height: 1;
  }
  .new-flight-btn:hover { color: var(--accent-amber); border-color: var(--accent-amber); }

  .chat {
    flex: 1;
    overflow-y: auto;
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    scroll-behavior: smooth;
  }

  .empty-chat {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 8px;
    color: var(--text-dim);
  }
  .empty-icon { font-size: 32px; }
  .empty-text { font-size: 12px; }

  .bubble { max-width: 85%; display: flex; flex-direction: column; gap: 4px; }
  .bubble.atc    { align-self: flex-start; }
  .bubble.pilot  { align-self: flex-end; }
  .bubble.system { align-self: center; max-width: 100%; }

  .bubble-meta {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 10px;
  }
  .bubble-meta.right { justify-content: flex-end; }

  .role-label {
    font-weight: 700;
    letter-spacing: 0.06em;
    font-size: 10px;
    padding: 1px 5px;
    border-radius: 2px;
  }
  .atc-label   { background: rgba(88,166,255,0.2); color: var(--accent-blue); }
  .pilot-label { background: rgba(63,185,80,0.2);  color: var(--accent-green); }

  .model-tag {
    font-size: 9px;
    color: var(--accent-purple);
    background: rgba(188,140,255,0.12);
    padding: 1px 4px;
    border-radius: 2px;
  }

  .ts { color: var(--text-dim); font-size: 10px; }

  .bubble-text {
    padding: 8px 10px;
    border-radius: var(--radius);
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .atc  .bubble-text { background: var(--atc-bubble);   border: 1px solid rgba(88,166,255,0.2); }
  .pilot .bubble-text { background: var(--pilot-bubble); border: 1px solid rgba(63,185,80,0.2); }
  .system .bubble-text {
    color: var(--accent-amber);
    background: rgba(210,153,34,0.1);
    border: 1px solid rgba(210,153,34,0.2);
    font-size: 11px;
    text-align: center;
    width: 100%;
  }

  /* Party-line traffic — deliberately faint, it's not addressed to you. */
  .ambient-line {
    align-self: stretch;
    display: flex;
    align-items: baseline;
    gap: 7px;
    padding: 2px 4px;
    font-size: 11px;
    color: var(--text-dim);
    opacity: 0.62;
  }
  .ambient-tag {
    flex-shrink: 0;
    font-weight: 700;
    font-size: 9px;
    letter-spacing: 0.04em;
    color: var(--text-muted);
    border: 1px solid var(--border);
    border-radius: 2px;
    padding: 0 4px;
  }
  .ambient-text { font-style: italic; line-height: 1.4; word-break: break-word; }

  .thinking {
    display: flex;
    align-items: center;
    gap: 5px;
    padding: 6px 10px;
    color: var(--text-dim);
    font-size: 12px;
  }

  .think-dot {
    width: 5px; height: 5px;
    background: var(--accent-blue);
    border-radius: 50%;
    opacity: 0.3;
    animation: pulse 1.2s ease-in-out infinite;
  }
  .think-dot:nth-child(2) { animation-delay: 0.2s; }
  .think-dot:nth-child(3) { animation-delay: 0.4s; }
  .think-label { margin-left: 4px; }

  @keyframes pulse {
    0%, 100% { opacity: 0.3; transform: scale(1); }
    50%       { opacity: 1;   transform: scale(1.3); }
  }

  .transcription-preview {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 5px 14px;
    font-size: 11px;
    color: var(--accent-amber);
    background: rgba(210,153,34,0.08);
    border-top: 1px solid rgba(210,153,34,0.15);
    flex-shrink: 0;
  }
  .tx-icon { flex-shrink: 0; }
  .tx-text  { font-style: italic; }

  .input-row {
    display: flex;
    gap: 8px;
    padding: 10px 14px;
    background: var(--bg-panel);
    border-top: 1px solid var(--border);
    flex-shrink: 0;
    align-items: flex-end;
  }

  .tx-input {
    flex: 1;
    resize: none;
    padding: 8px 10px;
    line-height: 1.4;
    border-radius: var(--radius);
  }
  .tx-input:disabled { opacity: 0.5; }

  .tx-btns {
    display: flex;
    flex-direction: column;
    gap: 4px;
    align-items: stretch;
  }

  .tx-btn {
    padding: 8px 16px;
    background: var(--accent-blue);
    color: #000;
    border-radius: var(--radius);
    font-weight: 700;
    font-size: 12px;
    letter-spacing: 0.05em;
    transition: opacity 0.15s;
    white-space: nowrap;
  }
  .tx-btn:disabled { opacity: 0.4; cursor: default; }
  .tx-btn:not(:disabled):hover { opacity: 0.85; }

  .ptt-btn {
    padding: 8px 12px;
    background: var(--bg-input);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: var(--radius);
    font-weight: 700;
    font-size: 11px;
    letter-spacing: 0.04em;
    transition: background 0.1s, border-color 0.1s;
    white-space: nowrap;
    user-select: none;
  }
  .ptt-btn:disabled { opacity: 0.4; cursor: default; }
  .ptt-btn.ptt-active {
    background: rgba(220,50,50,0.25);
    border-color: rgba(220,50,50,0.6);
    color: #ff6b6b;
    animation: ptt-pulse 0.8s ease-in-out infinite;
  }

  .mic-btn {
    padding: 8px 12px;
    background: var(--bg-input);
    border: 1px solid var(--border);
    color: var(--text-dim);
    border-radius: var(--radius);
    font-size: 14px;
    transition: opacity 0.15s;
  }
  .mic-btn:hover { opacity: 0.8; }

  @keyframes ptt-pulse {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.6; }
  }
</style>
