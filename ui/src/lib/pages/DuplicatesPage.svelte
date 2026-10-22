<script lang="ts">
  import { api, fmtGb } from '../api'

  interface DuplicateFile {
    path: string
    name: string
    category: string
    size: number
    duration: number | null
    codec: string | null
    hash: string | null
    resolution: [number, number] | null
  }

  interface DuplicateGroup {
    reason: string
    files: DuplicateFile[]
    wasted_bytes: number
  }

  let {
    duplicates = [],
    analyzing = false,
    pending = false,
    onanalyze,
    onrefresh,
    onopenmovie,
  }: {
    duplicates?: DuplicateGroup[]
    analyzing?: boolean
    pending?: boolean
    onanalyze?: () => void
    onrefresh?: () => void
    onopenmovie?: (file: DuplicateFile) => void
  } = $props()

  let confirmPath = $state<string | null>(null)
  let confirmEl: HTMLDivElement | null = $state(null)
  let confirmTrigger: HTMLElement | null = null

  $effect(() => {
    if (confirmPath) confirmEl?.focus()
  })

  function openConfirm(path: string) {
    confirmTrigger = document.activeElement as HTMLElement | null
    confirmPath = path
    deleteError = null
  }

  function closeConfirm() {
    confirmPath = null
    // restore focus to the button that opened the dialog — a keyboard
    // user must not be dropped into nowhere behind the scrim
    confirmTrigger?.focus()
    confirmTrigger = null
  }

  function handleKeydown(e: KeyboardEvent) {
    if (!confirmPath) return
    if (e.key === 'Escape') {
      closeConfirm()
      return
    }
    // keep Tab inside the alertdialog (the scrim obscures what's behind)
    if (e.key === 'Tab' && confirmEl) {
      const focusables = confirmEl.querySelectorAll<HTMLElement>('button:not(:disabled)')
      if (focusables.length < 2) return
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
  }

  const totalWasted = $derived(duplicates.reduce((sum, g) => sum + g.wasted_bytes, 0))

  let deletingPath = $state<string | null>(null)
  let deleteError = $state<string | null>(null)

  async function confirmDelete(path: string) {
    deletingPath = path
    deleteError = null
    try {
      await api.deleteToTrash(path)
      closeConfirm()
      onrefresh?.()
    } catch (e) {
      deleteError = e instanceof Error ? e.message : String(e)
    } finally {
      deletingPath = null
    }
  }
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="page page-in">
  <header class="page-head">
    <h2>Duplicates</h2>
    <p class="legend">
      {duplicates.length} GROUPS · {fmtGb(totalWasted)} WASTED
      {#if duplicates.length > 0}· DETECTION IS READ-ONLY{/if}
    </p>
    <button
      class="act rescan head-action"
      onclick={() => onanalyze?.()}
      disabled={analyzing || pending}
      title="Re-run duplicate detection on the whole library (hash-based, read-only)"
    >
      {analyzing ? 'ANALYZING…' : 'RE-ANALYZE LIBRARY'}
    </button>
  </header>

  {#if duplicates.length === 0}
    <div class="empty dim">
      no duplicates found — the library is clean
      <button class="act analyze" onclick={() => onanalyze?.()} disabled={analyzing || pending}>
        {analyzing ? 'ANALYZING…' : 'ANALYZE LIBRARY'}
      </button>
    </div>
  {:else}
    {#each duplicates as group (group.files[0].path)}
      <div class="group">
        <div class="group-head">
          <span class="chip neutral"><span class="swatch"></span>{group.reason.toUpperCase()}</span>
          <span class="mono dim">{fmtGb(group.wasted_bytes)} wasted</span>
        </div>
        {#each group.files as file (file.path)}
          <div class="file-row" class:best={group.files[0].path === file.path}>
            <div class="file-main">
              <!-- a real button into the provenance page: a duplicate is a
                   title, and the detail page carries its full record -->
              <button class="name-btn" onclick={() => onopenmovie?.(file)} title="Open the provenance page for this file">
                <span class="name">{file.name}</span>
              </button>
              <span class="mono dim path">{file.path}</span>
            </div>
            <span class="mono">{fmtGb(file.size)}</span>
            <span class="mono dim">{file.resolution?.join('×') ?? '—'}</span>
            <span class="chip {group.files[0].path === file.path ? 'done' : 'neutral'}">
              <span class="swatch"></span>{group.files[0].path === file.path ? 'KEEP (BEST)' : 'DUPLICATE'}
            </span>
            {#if group.files[0].path !== file.path}
              <button
                class="delete mono"
                onclick={() => openConfirm(file.path)}
                aria-label="Delete {file.name} (moves the file to the recycle bin)"
                title="moves the file to the recycle bin (recoverable)"
              >
                DELETE
              </button>
            {/if}
          </div>
        {/each}
      </div>
    {/each}
    <p class="note dim mono">deleting sends the file to the recycle bin (recoverable) — confirm per file; the source stays yours</p>
  {/if}
</div>

{#if confirmPath}
  <div class="confirm-overlay" role="presentation">
    <div class="confirm" role="alertdialog" aria-label="Confirm deletion" tabindex="-1" bind:this={confirmEl}>
      <div class="legend">DELETE FILE?</div>
      <div class="mono confirm-path">{confirmPath}</div>
      <div class="dim">The file is moved to the recycle bin (recoverable). The library entry stays until the next scan flags it missing.</div>
      {#if deleteError}<div class="mono bad">{deleteError}</div>{/if}
      <div class="confirm-actions">
        <button class="mono" onclick={closeConfirm}>KEEP</button>
        <button class="mono danger" onclick={() => confirmDelete(confirmPath!)} disabled={deletingPath != null}>
          {deletingPath === confirmPath ? 'MOVING…' : 'MOVE TO RECYCLE BIN'}
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .page { display: flex; flex-direction: column; gap: 12px; }
  .page-head h2 { font-size: var(--fs-page); font-weight: 700; }
  .page-head .legend { margin-top: 2px; }
  /* the page's one action lives in the head, not under nine groups */
  .page-head { position: relative; }
  .head-action { position: absolute; right: 0; top: 2px; }
  .dim { color: var(--ink-dim); }
  .empty {
    padding: 60px 20px;
    text-align: center;
    border: 1px dashed var(--seam);
    border-radius: var(--radius);
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
  }
  .act.analyze { border-color: var(--ink); color: var(--ink); }
  .rescan { align-self: flex-start; }
  .act {
    background: var(--panel-raised);
    border: 1px solid var(--seam);
    border-radius: var(--radius);
    color: var(--ink-dim);
    padding: 4px 12px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
    text-transform: uppercase;
  }
  .act:hover { color: var(--ink); border-color: var(--ink-dim); }
  .group {
    border: 1px solid var(--seam);
    border-radius: var(--radius);
    background: var(--panel);
    padding: 10px 14px;
  }
  .group-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }
  .file-row {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 6px 0;
    border-bottom: 1px solid var(--row-line);
  }
  .file-row:last-child { border-bottom: none; }
  .file-row.best { background: var(--panel-active); }
  .file-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .name-btn {
    background: transparent;
    border: none;
    padding: 0;
    color: var(--ink);
    font: inherit;
    cursor: pointer;
    text-align: left;
  }
  .name-btn:hover .name { color: var(--teal-ink); }
  .name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .path {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 10.5px;
    direction: rtl; /* the tail names the file */
    text-align: left;
  }
  .delete {
    background: transparent;
    border: 1px solid var(--seam);
    border-radius: var(--radius);
    color: var(--red);
    padding: 3px 10px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
  }
  .delete:hover { background: var(--red); color: var(--ground); border-color: var(--red); }
  .note { font-size: 11px; }

  .confirm-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 60;
  }
  .confirm {
    background: var(--panel);
    border: 1px solid var(--red);
    border-radius: var(--radius);
    padding: 16px 22px;
    max-width: 480px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .confirm-path { word-break: break-all; }
  .confirm-actions { display: flex; gap: 8px; margin-top: 6px; }
  .bad { color: var(--red); }
  .confirm-actions button {
    background: transparent;
    border: 1px solid var(--seam);
    border-radius: var(--radius);
    color: var(--ink);
    padding: 5px 12px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
  }
  .confirm-actions .danger { border-color: var(--red); color: var(--red); }
  .confirm-actions .danger:hover { background: var(--red); color: var(--ground); }
</style>
