<script lang="ts">
  import { fmtGb } from './api'

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

  let { onclose }: { onclose: () => void } = $props()

  let groups = $state<DuplicateGroup[]>([])
  let loading = $state(true)
  let error = $state<string | null>(null)
  let confirmPath = $state<string | null>(null) // delete confirmation

  $effect(() => {
    fetch('/api/duplicates')
      .then((r) => r.json())
      .then((g) => {
        groups = g
        loading = false
      })
      .catch((e) => {
        error = String(e)
        loading = false
      })
  })

  const totalWasted = $derived(groups.reduce((sum, g) => sum + g.wasted_bytes, 0))

  function bestLabel(file: DuplicateFile, group: DuplicateGroup): string {
    return group.files[0].path === file.path ? 'KEEP (best)' : 'duplicate'
  }
</script>

<div class="overlay" role="presentation" onclick={(e) => e.target === e.currentTarget && onclose()}>
  <div class="sheet">
    <div class="head">
      <span class="legend">DUPLICATES</span>
      <span class="mono dim">
        {groups.length} groups · {fmtGb(totalWasted)} wasted
      </span>
      <button class="close" onclick={onclose} aria-label="Close duplicates">X</button>
    </div>

    {#if loading}
      <div class="dim loading">loading…</div>
    {:else if error}
      <div class="mono bad">{error}</div>
    {:else if groups.length === 0}
      <div class="dim loading">no duplicates found — the library is clean</div>
    {:else}
      {#each groups as group (group.files[0].path)}
        <div class="group">
          <div class="group-head">
            <span class="chip {group.reason === 'identical' ? 'done' : 'progress'}">
              <span class="swatch"></span>{group.reason.toUpperCase()}
            </span>
            <span class="mono dim">{fmtGb(group.wasted_bytes)} wasted</span>
          </div>
          {#each group.files as file (file.path)}
            <div class="file-row">
              <div class="file-main">
                <span class="name">{file.name}</span>
                <span class="mono dim path">{file.path}</span>
              </div>
              <span class="mono">{fmtGb(file.size)}</span>
              <span class="mono dim">{file.resolution?.join('x') ?? '—'}</span>
              <span class="chip {group.files[0].path === file.path ? 'done' : 'skip'}">
                <span class="swatch"></span>{bestLabel(file, group)}
              </span>
              {#if group.files[0].path !== file.path}
                <button
                  class="delete mono"
                  onclick={() => (confirmPath = file.path)}
                  aria-label="Delete {file.name}"
                >
                  DELETE
                </button>
              {/if}
            </div>
          {/each}
        </div>
      {/each}
      <div class="note dim">
        Detection is read-only. Deleting a duplicate is irreversible — confirm
        per file; the source stays yours.
      </div>
    {/if}
  </div>
</div>

{#if confirmPath}
  <div class="confirm-overlay" role="presentation">
    <div class="confirm">
      <div class="legend">DELETE FILE?</div>
      <div class="mono confirm-path">{confirmPath}</div>
      <div class="dim">This is irreversible. The file will be deleted from disk.</div>
      <div class="confirm-actions">
        <button class="mono" onclick={() => (confirmPath = null)}>KEEP</button>
        <button
          class="mono danger"
          onclick={() => {
            if (confirmPath === null) return
            // deletion endpoint not yet implemented; surface the intent so
            // the user copies the path and deletes manually
            navigator.clipboard?.writeText(confirmPath)
            confirmPath = null
          }}
        >
          COPY PATH (delete manually)
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(12, 13, 15, 0.72);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 50;
  }
  .sheet {
    width: min(860px, 94vw);
    max-height: 88vh;
    overflow: auto;
    background: var(--panel);
    border: 1px solid var(--seam);
    padding: 18px 22px;
  }
  .head {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 14px;
  }
  .head .legend { flex: 0 0 auto; }
  .head .mono.dim { flex: 1; }
  .close {
    background: transparent;
    border: 1px solid var(--seam);
    color: var(--ink-dim);
    cursor: pointer;
    padding: 2px 8px;
  }
  .close:hover { color: var(--ink); }
  .group {
    border: 1px solid var(--seam);
    padding: 10px 14px;
    margin-bottom: 12px;
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
    gap: 12px;
    padding: 6px 0;
    border-top: 1px solid var(--row-line);
  }
  .file-main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
  }
  .name { font-weight: 600; font-size: var(--fs-body); }
  .path {
    font-size: 10px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 380px;
  }
  .delete {
    background: transparent;
    border: 1px solid var(--seam);
    color: var(--bad);
    cursor: pointer;
    padding: 3px 10px;
    font-size: 10px;
    letter-spacing: var(--track-legend);
  }
  .delete:hover { border-color: var(--bad); }
  .note { font-size: 11px; margin-top: 8px; }
  .loading { padding: 32px; text-align: center; }
  .bad { color: var(--bad); }
  .dim { color: var(--ink-dim); }
  .confirm-overlay {
    position: fixed;
    inset: 0;
    background: rgba(12, 13, 15, 0.85);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 60;
  }
  .confirm {
    width: min(480px, 92vw);
    background: var(--panel);
    border: 1px solid var(--bad);
    padding: 18px 22px;
  }
  .confirm-path {
    margin: 10px 0;
    word-break: break-all;
    color: var(--ink);
  }
  .confirm-actions {
    display: flex;
    gap: 10px;
    justify-content: flex-end;
    margin-top: 14px;
  }
  .confirm-actions button {
    background: var(--panel-active);
    border: 1px solid var(--seam);
    color: var(--ink);
    padding: 6px 14px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
  }
  .confirm-actions .danger { border-color: var(--bad); color: var(--bad); }
</style>
