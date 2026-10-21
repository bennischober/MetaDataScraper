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

  let { duplicates = [], onrefresh }: { duplicates?: DuplicateGroup[]; onrefresh?: () => void } = $props()

  let confirmPath = $state<string | null>(null) // delete confirmation

  const totalWasted = $derived(duplicates.reduce((sum, g) => sum + g.wasted_bytes, 0))

  function bestLabel(file: DuplicateFile, group: DuplicateGroup): string {
    return group.files[0].path === file.path ? 'KEEP (best)' : 'duplicate'
  }

  async function copyPath(path: string) {
    await navigator.clipboard?.writeText(path)
    confirmPath = null
  }
</script>

<div class="block">
  <div class="head">
    <span class="legend">SHEET 02 — DUPLICATE GROUPS</span>
    <span class="mono dim">
      {duplicates.length} groups · {fmtGb(totalWasted)} wasted
    </span>
    <button class="act" onclick={() => onrefresh?.()}>RESCAN</button>
  </div>

  {#if duplicates.length === 0}
    <div class="empty dim">
      no duplicates found — the library is clean
    </div>
  {:else}
    {#each duplicates as group (group.files[0].path)}
      <div class="group">
        <div class="group-head">
          <span class="chip neutral">
            <span class="swatch"></span>{group.reason.toUpperCase()}
          </span>
          <span class="mono dim">{fmtGb(group.wasted_bytes)} wasted</span>
        </div>
        {#each group.files as file (file.path)}
          <div class="file-row" class:best={group.files[0].path === file.path}>
            <div class="file-main">
              <span class="name">{file.name}</span>
              <span class="mono dim path">{file.path}</span>
            </div>
            <span class="mono">{fmtGb(file.size)}</span>
            <span class="mono dim">{file.resolution?.join('×') ?? '—'}</span>
            <span class="chip {group.files[0].path === file.path ? 'done' : 'neutral'}">
              <span class="swatch"></span>{bestLabel(file, group)}
            </span>
            {#if group.files[0].path !== file.path}
              <button
                class="delete mono"
                onclick={() => (confirmPath = file.path)}
                aria-label="Delete {file.name} (copies the path; deletion is manual for now)"
                title="backend pending — copies the path for manual deletion"
              >
                DELETE
              </button>
            {/if}
          </div>
        {/each}
      </div>
    {/each}
    <div class="note dim">
      Detection is read-only. Deleting a duplicate is irreversible — confirm per
      file; the source stays yours.
    </div>
  {/if}
</div>

{#if confirmPath}
  <div class="confirm-overlay" role="presentation">
    <div class="confirm" role="alertdialog" aria-label="Confirm deletion">
      <div class="legend">DELETE FILE?</div>
      <div class="mono confirm-path">{confirmPath}</div>
      <div class="dim">This is irreversible. The file will be deleted from disk.</div>
      <div class="confirm-actions">
        <button class="mono" onclick={() => (confirmPath = null)}>KEEP</button>
        <button class="mono danger" onclick={() => copyPath(confirmPath!)}>
          COPY PATH (delete manually)
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .block {
    display: flex;
    flex-direction: column;
    gap: 10px;
    flex: 1;
    min-height: 0;
  }
  .head {
    display: flex;
    align-items: center;
    gap: 16px;
  }
  .head .legend { flex: 0 0 auto; }
  .head .mono.dim { flex: 1; }
  .act {
    background: var(--panel);
    border: 1px solid var(--seam);
    color: var(--ink-dim);
    padding: 3px 12px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
    text-transform: uppercase;
  }
  .act:hover { color: var(--ink); border-color: var(--ink-dim); }
  .empty { padding: 40px; text-align: center; }
  .group {
    border: 1px solid var(--seam);
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
  .file-main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
  }
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
    color: var(--red);
    padding: 3px 10px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
  }
  .delete:hover { background: var(--red); color: #fff; border-color: var(--red); }
  .note { font-size: 11px; padding: 4px 2px; }
  .dim { color: var(--ink-dim); }

  .confirm-overlay {
    position: fixed;
    inset: 0;
    background: rgba(30, 35, 40, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 60;
  }
  .confirm {
    background: var(--panel);
    border: 1px solid var(--red);
    padding: 16px 22px;
    max-width: 480px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .confirm-path { word-break: break-all; }
  .confirm-actions { display: flex; gap: 8px; margin-top: 6px; }
  .confirm-actions button {
    background: transparent;
    border: 1px solid var(--seam);
    color: var(--ink);
    padding: 5px 12px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
  }
  .confirm-actions .danger { border-color: var(--red); color: var(--red); }
  .confirm-actions .danger:hover { background: var(--red); color: #fff; }
</style>
