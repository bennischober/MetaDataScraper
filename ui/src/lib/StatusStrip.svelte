<script lang="ts">
  import type { Status } from './api'

  let { status }: { status: Status } = $props()
</script>

<div class="strip">
  <span class="brand">METADATASCRAPER</span>
  <span class="mono dim">ZONE RECORD</span>
  <div class="spacer"></div>
  <span class="mono" class:ok={status.watcher === 'running'} class:dim={status.watcher !== 'running'}>
    {status.watcher === 'running' ? '● WATCHER RUNNING' : '○ WATCHER STOPPED'}
  </span>
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
  .ok { color: var(--ok); }
  .dim { color: var(--ink-dim); }
</style>
