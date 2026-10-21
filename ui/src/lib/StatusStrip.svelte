<script lang="ts">
  import type { Status } from './api'

  let { status, onwatch }: { status: Status; onwatch?: () => void } = $props()
</script>

<div class="strip">
  <span class="brand">METADATASCRAPER</span>
  <span class="mono dim">VMAF RECORD</span>
  <div class="spacer"></div>
  <button
    class="mono watch-btn"
    class:running={status.watcher === 'running'}
    onclick={() => onwatch?.()}
  >
    {status.watcher === 'running' ? '● WATCHER RUNNING' : '○ WATCHER STOPPED'}
  </button>
  <span class="mono queue-chip">QUEUE {status.queue.length}</span>
</div>

<style>
  .strip {
    height: 52px;
    border-bottom: 1px solid var(--seam);
    display: flex;
    align-items: center;
    padding: 0 20px;
    gap: 18px;
    background: var(--panel);
  }
  .brand {
    font-weight: 700;
    letter-spacing: var(--track-legend);
    font-size: 12px;
  }
  .spacer {
    flex: 1;
  }
  .queue-chip {
    background: var(--panel-active);
    border: 1px solid var(--seam);
    padding: 2px 8px;
    border-radius: 3px;
  }
  .watch-btn {
    background: transparent;
    border: none;
    cursor: pointer;
    font-size: 11px;
    padding: 2px 4px;
  }
  .watch-btn.running { color: var(--ok); }
  .watch-btn:not(.running) { color: var(--ink-dim); }
  .watch-btn:not(.running):hover { color: var(--ink); }
  .dim { color: var(--ink-dim); }
</style>
