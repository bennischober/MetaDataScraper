<script lang="ts">
  import { api, type Config } from './api'

  let { onclose }: { onclose: () => void } = $props()

  let config = $state<Config | null>(null)
  let saveError = $state<string | null>(null)
  let saved = $state(false)
  let busy = $state(false)

  $effect(() => {
    api
      .config()
      .then((c) => (config = c))
      .catch((e) => (saveError = e instanceof Error ? e.message : String(e)))
    // Escape closes from anywhere while the config sheet is open
    window.addEventListener('keydown', handleKeydown)
    return () => window.removeEventListener('keydown', handleKeydown)
  })

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') onclose()
  }

  async function save() {
    if (!config) return
    busy = true
    saveError = null
    try {
      config = await api.saveConfig(config)
      saved = true
      setTimeout(() => (saved = false), 2000)
    } catch (e) {
      saveError = e instanceof Error ? e.message : String(e)
    } finally {
      busy = false
    }
  }

  function addExtension(event: Event) {
    if (!config) return
    const input = event.target as HTMLInputElement
    const value = input.value.trim().toLowerCase()
    if (!value) return
    const ext = value.startsWith('.') ? value : `.${value}`
    const extensions = config.file?.extensions ?? []
    if (!extensions.includes(ext)) {
      config = { ...config, file: { ...config.file, extensions: [...extensions, ext] } }
    }
    input.value = ''
  }

  // duplicate keys abort Svelte each-blocks => dedupe defensively
  const dedupedExtensions = $derived([...new Set(config?.file?.extensions ?? [])])

  function removeExtension(ext: string) {
    if (!config) return
    config = {
      ...config,
      file: { ...config.file, extensions: (config.file?.extensions ?? []).filter((e) => e !== ext) },
    }
  }
</script>

<div class="block" role="group" aria-label="Configuration">
  <div class="panel">
    <div class="head">
      <span class="legend">SHEET 04 — CONFIGURATION</span>
      <button class="close" onclick={onclose} aria-label="Close configuration and return to library">CLOSE ✕</button>
    </div>

    {#if !config}
      <div class="dim loading">loading…</div>
    {:else}
      {#if saveError}<div class="error mono">{saveError}</div>{/if}
      {#if saved}<div class="saved mono">saved</div>{/if}

      <div class="group">
        <div class="legend">MOVIE EXTENSIONS</div>
        <div class="ext-list">
          {#each dedupedExtensions as ext (ext)}
            <span class="ext mono">
              {ext}
              <button class="ext-x" onclick={() => removeExtension(ext)} aria-label="Remove {ext}">✕</button>
            </span>
          {/each}
          <input
            class="mono ext-input"
            placeholder="add extension + Enter"
            onkeydown={(e) => e.key === 'Enter' && addExtension(e)}
          />
        </div>
      </div>

      <div class="group">
        <div class="legend">HANDBRAKE (FIXED MODE)</div>
        <label class="field">
          <span class="dim">preset</span>
          <input class="mono" bind:value={config.handbrake!.preset} />
        </label>
        <label class="field">
          <span class="dim">encoder</span>
          <input class="mono" bind:value={config.handbrake!.encoder} />
        </label>
      </div>

      <div class="group">
        <div class="legend">BLACK BAR DETECTION</div>
        <label class="field">
          <span class="dim">min width (px)</span>
          <input class="mono" type="number" bind:value={config.ffmpeg!.black_bars!.MIN_WIDTH} />
        </label>
        <label class="field">
          <span class="dim">min height (px)</span>
          <input class="mono" type="number" bind:value={config.ffmpeg!.black_bars!.MIN_HEIGHT} />
        </label>
      </div>

      <div class="group">
        <div class="legend">OUTPUT</div>
        <label class="field">
          <span class="dim">csv separator</span>
          <input class="mono" maxlength="1" bind:value={config.file!.output!.seperator} />
        </label>
        <label class="field">
          <span class="dim">language</span>
          <input class="mono" bind:value={config.language} />
        </label>
      </div>

      <div class="foot">
        <span class="mono dim">config.json next to the database</span>
        <button class="save" onclick={save} disabled={busy}>
          {busy ? 'SAVING…' : saved ? 'SAVED ✓' : 'SAVE'}
        </button>
      </div>
    {/if}
  </div>
</div>

<style>
  .block {
    flex: 1;
    min-height: 0;
    outline: none;
  }
  .panel {
    width: min(560px, 92vw);
    max-height: 88vh;
    overflow: auto;
    background: var(--panel);
    border: 1px solid var(--ink);
    padding: 18px 22px;
  }
  .head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 14px;
  }
  .close {
    background: transparent;
    border: 1px solid var(--seam);
    color: var(--ink-dim);
    cursor: pointer;
    padding: 3px 9px;
  }
  .close:hover { color: var(--ink); border-color: var(--ink-dim); }
  .group {
    border: 1px solid var(--seam);
    background: var(--ground);
    padding: 12px 14px;
    margin-bottom: 12px;
  }
  .group .legend { display: block; margin-bottom: 10px; }
  .ext-list {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
  }
  .ext {
    background: var(--panel-active);
    border: 1px solid var(--seam);
    padding: 2px 8px;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .ext-x {
    background: transparent;
    border: none;
    color: var(--ink-faint);
    cursor: pointer;
    font-size: 10px;
  }
  .ext-x:hover { color: var(--red); }
  .ext-input {
    background: transparent;
    border: 1px dashed var(--seam);
    color: var(--ink);
    padding: 3px 8px;
    width: 150px;
  }
  .field {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
  }
  .field:last-child { margin-bottom: 0; }
  .field span { width: 120px; flex-shrink: 0; }
  .field input {
    background: var(--panel);
    border: 1px solid var(--seam);
    color: var(--ink);
    padding: 5px 9px;
    flex: 1;
  }
  .foot {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 4px;
  }
  .save {
    background: var(--teal);
    border: 1px solid var(--teal);
    color: #fff;
    padding: 6px 18px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
  }
  .save:hover:not(:disabled) { background: var(--teal-ink); }
  .save:disabled { opacity: 0.5; cursor: default; }
  .error {
    color: var(--red);
    border: 1px solid var(--red);
    padding: 6px 10px;
    margin-bottom: 10px;
    word-break: break-word;
  }
  .saved { color: var(--teal-ink); margin-bottom: 10px; }
  .loading { padding: 24px; text-align: center; }
  .dim { color: var(--ink-dim); }
</style>
