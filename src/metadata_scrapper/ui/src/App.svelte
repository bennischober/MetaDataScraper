<script lang="ts">
  import { onMount } from 'svelte'
  import { api, type Movie, type Status, type Job } from './lib/api'
  import StatusStrip from './lib/StatusStrip.svelte'
  import ZoneRamp from './lib/ZoneRamp.svelte'
  import JobSheet from './lib/JobSheet.svelte'
  import LibraryTable from './lib/LibraryTable.svelte'

  let status: Status | null = $state(null)
  let movies: Movie[] = $state([])
  let history: Job[] = $state([])
  let error = $state<string | null>(null)
  let busy = $state(false)

  let scanPath = $state('D:\\Movies')
  let targetVmaf = $state(95.0)
  let removeBars = $state(false)

  async function refresh() {
    try {
      const [s, lib, jobs] = await Promise.all([
        api.status(),
        api.library(),
        api.jobs(20),
      ])
      status = s
      movies = lib
      history = jobs
      error = null
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    }
  }

  async function enqueue(kind: 'scan' | 'plan' | 'encode') {
    busy = true
    try {
      await api.enqueue({
        kind,
        path: scanPath,
        output_dir: kind === 'encode' ? 'D:\\Encoded' : undefined,
        quality: kind !== 'scan',
        target_vmaf: targetVmaf,
        remove_bars: removeBars,
      })
      await refresh()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      busy = false
    }
  }

  async function cancelJob(id: number | null) {
    if (id === null) return
    await api.cancel(id)
    await refresh()
  }

  onMount(() => {
    refresh()
    const timer = setInterval(refresh, 3000)
    return () => clearInterval(timer)
  })

  const runningJobs = $derived(
    history.filter((j) => j.state === 'running' || j.state === 'queued')
  )
</script>

<div class="app">
  {#if status}
    <StatusStrip {status} />
  {:else}
    <div class="strip-loading">connecting…</div>
  {/if}

  <div class="content">
    <ZoneRamp />

    {#if error}
      <div class="api-error mono">{error}</div>
    {/if}

    <!-- live job area -->
    <div class="jobs">
      {#each runningJobs as job (job.id)}
        <JobSheet {job} />
      {:else}
        <div class="idle dim">no active jobs — the queue is empty</div>
      {/each}
    </div>

    <!-- actions -->
    <div class="actions">
      <input class="mono" bind:value={scanPath} placeholder="movie folder" aria-label="Movie folder" />
      <label class="mono dim">
        VMAF <input type="number" min="70" max="100" step="0.5" bind:value={targetVmaf} />
      </label>
      <label class="mono dim">
        <input type="checkbox" bind:checked={removeBars} /> bars
      </label>
      <button onclick={() => enqueue('scan')} disabled={busy}>Scan</button>
      <button onclick={() => enqueue('plan')} disabled={busy}>Plan</button>
      <button onclick={() => enqueue('encode')} disabled={busy} class="primary">Encode</button>
      <a class="mono" href="/report" target="_blank">report ↗</a>
    </div>

    <!-- running/queued sheets with cancel -->
    {#each runningJobs as job (job.id)}
      {#if job.state === 'running' || job.state === 'queued'}
        <button class="cancel mono" onclick={() => cancelJob(job.id)}>
          cancel #{job.id}
        </button>
      {/if}
    {/each}

    <!-- history -->
    {#if history.length > 0}
      <div class="history">
        <div class="legend">RECENT JOBS</div>
        {#each history.slice(0, 5) as job (job.id)}
          <div class="history-row mono">
            <span>#{job.id}</span>
            <span>{job.kind}</span>
            <span class={job.state === 'done' ? 'ok' : job.state === 'failed' ? 'bad' : 'dim'}>
              {job.state}
            </span>
            <span class="dim">{job.error ?? ''}</span>
          </div>
        {/each}
      </div>
    {/if}

    <!-- library -->
    <LibraryTable {movies} />
  </div>
</div>

<style>
  .app {
    min-height: 100vh;
    background: var(--ground);
    color: var(--ink);
    display: flex;
    flex-direction: column;
    font-size: var(--fs-body);
  }
  .strip-loading {
    height: 52px;
    border-bottom: 1px solid var(--seam);
    display: flex;
    align-items: center;
    padding: 0 20px;
    color: var(--ink-dim);
  }
  .content {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 18px 20px;
    max-width: 1400px;
    width: 100%;
    margin: 0 auto;
  }
  .jobs { display: flex; flex-direction: column; gap: 8px; }
  .idle {
    border: 1px dashed var(--seam);
    padding: 14px;
    text-align: center;
    font-size: var(--fs-data);
  }
  .dim { color: var(--ink-dim); }
  .api-error {
    border: 1px solid var(--bad);
    color: var(--bad);
    padding: 8px 14px;
    font-size: var(--fs-data);
  }
  .actions {
    display: flex;
    gap: 10px;
    align-items: center;
    flex-wrap: wrap;
  }
  .actions input[type='text'], .actions input:not([type]) {
    background: var(--panel);
    border: 1px solid var(--seam);
    color: var(--ink);
    padding: 6px 10px;
    min-width: 280px;
  }
  .actions input[type='number'] {
    background: var(--panel);
    border: 1px solid var(--seam);
    color: var(--ink);
    width: 64px;
    padding: 6px 8px;
  }
  .actions button {
    background: var(--panel-active);
    border: 1px solid var(--seam);
    color: var(--ink);
    padding: 6px 16px;
    cursor: pointer;
    font-size: var(--fs-body);
    letter-spacing: var(--track-legend);
    text-transform: uppercase;
    font-size: var(--fs-legend);
  }
  .actions button:hover:not(:disabled) { border-color: var(--ink-dim); }
  .actions button.primary { border-color: var(--ok); color: var(--ok); }
  .actions button:disabled { opacity: 0.4; cursor: default; }
  .actions a { color: var(--magenta); text-decoration: none; }
  .cancel {
    background: transparent;
    border: 1px solid var(--seam);
    color: var(--ink-dim);
    padding: 3px 10px;
    cursor: pointer;
    align-self: flex-start;
    font-size: var(--fs-legend);
  }
  .cancel:hover { color: var(--bad); border-color: var(--bad); }
  .history {
    border: 1px solid var(--seam);
    background: var(--panel);
    padding: 10px 16px;
  }
  .history-row {
    display: flex;
    gap: 16px;
    padding: 4px 0;
    border-bottom: 1px solid var(--row-line);
    font-size: var(--fs-data);
  }
  .history-row:last-child { border-bottom: none; }
  .ok { color: var(--ok); }
  .bad { color: var(--bad); }
</style>
