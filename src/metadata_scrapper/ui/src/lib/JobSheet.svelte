<script lang="ts">
  import type { Job } from './api'

  let { job }: { job: Job } = $props()

  const kindLabel: Record<string, string> = {
    scan: 'scan',
    plan: 'plan',
    encode: 'encode · quality mode',
  }

  // extract the running movie name from the payload
  const movieName = $derived(
    (job.payload?.path as string | undefined)?.split(/[\\/]/).pop() ?? ''
  )

  // progress: only available when the job carries a result summary
  const progress = $derived.by(() => {
    if (job.state === 'running') {
      const result = job.result as { percent?: number } | null
      return result?.percent ?? null
    }
    return null
  })
</script>

<div class="sheet" class:active={job.state === 'running'}>
  <div class="head">
    <span class="title">
      {movieName || job.kind}
      <span class="dim note">— {kindLabel[job.kind] ?? job.kind}</span>
    </span>
    <span class="mono magenta">
      {#if job.state === 'done'}DONE{/if}
      {#if job.state === 'failed'}FAILED{/if}
      {#if job.state === 'queued'}QUEUED{/if}
    </span>
  </div>
  {#if job.state === 'running'}
    <div class="trace">
      <div class="fill" style="width: {progress ?? 8}%"></div>
      {#if progress !== null}
        <span class="mono reading">{progress}%</span>
      {/if}
    </div>
  {:else if job.error}
    <div class="error mono">{job.error}</div>
  {/if}
</div>

<style>
  .sheet {
    border: 1px solid var(--seam);
    background: var(--panel);
    padding: 12px 16px;
  }
  .sheet.active {
    background: var(--panel-active);
  }
  .head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
  }
  .title {
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .note { font-size: 11px; font-weight: 400; }
  .magenta { color: var(--magenta); }
  .dim { color: var(--ink-dim); }
  .trace {
    height: 22px;
    margin-top: 8px;
    background: var(--hatch-work);
    border: 1px solid var(--seam);
    position: relative;
  }
  .fill {
    position: absolute;
    left: 0; top: 0; bottom: 0;
    background: var(--hatch-progress);
    border-right: 2px solid var(--ok);
  }
  .reading {
    position: absolute;
    right: 6px; top: 3px;
    color: var(--ink);
  }
  .error {
    margin-top: 6px;
    color: var(--bad);
    font-size: var(--fs-data);
    overflow: hidden;
    text-overflow: ellipsis;
  }
</style>
