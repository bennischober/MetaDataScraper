<script lang="ts">
  import {
    api,
    fmtGb,
    fmtSeconds,
    fmtWhen,
    jobPaths,
    type Movie,
    type Job,
    type EncodeRecord,
  } from '../api'

  /** The provenance page: everything recorded about one title — source
   * facts, the latest encode with every schema-v5 metric, the COMPLETE
   * encode history (deltas between runs) with a VMAF-vs-target gauge,
   * the job audit trail, and the actions. Deep-linkable via #/movie. */

  let {
    movie,
    targetVmaf,
    onclose,
    onencode,
    onplan,
    backTo = 'library',
  }: {
    movie: Movie
    targetVmaf: number
    onclose: () => void
    onencode: (path: string) => void
    onplan: (path: string) => void
    backTo?: 'overview' | 'library'
  } = $props()

  let jobs = $state<Job[]>([])
  let encodes = $state<EncodeRecord[] | null>(null)
  let encodeBusy = $state(false)
  let revealBusy = $state(false)
  let revealError = $state<string | null>(null)
  let pageEl: HTMLElement | null = $state(null)

  $effect(() => {
    // full encode history from the dedicated endpoint (the library list
    // carries only the latest encode per title) + this movie's job trail
    api
      .movie(movie.path)
      .then((d) => (encodes = d.history))
      .catch(() => (encodes = []))
    api
      .jobs(100)
      .then((all) => (jobs = all.filter((j) => jobPaths(j).includes(movie.path))))
      .catch(() => {})
    pageEl?.focus()
  })

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') onclose()
  }

  async function act(kind: 'plan' | 'encode') {
    encodeBusy = true
    try {
      if (kind === 'encode') onencode(movie.path)
      else onplan(movie.path)
    } finally {
      encodeBusy = false
    }
  }

  function revealInExplorer() {
    if (revealBusy) return
    revealBusy = true
    api
      .reveal(movie.path)
      .catch((e) => (revealError = e instanceof Error ? e.message : String(e)))
      .finally(() => (revealBusy = false))
  }

  const encode = $derived(movie.encode)
  /** "02:41:41" → "2h 41m" — every duration carries its unit like the rest
   * of the console; the raw HH:MM:SS string stays the fallback. */
  function fmtDuration(raw: string | null): string | null {
    if (!raw) return null
    const parts = raw.split(':').map(Number)
    if (parts.length !== 3 || parts.some(Number.isNaN)) return raw
    const [h, m] = parts
    return h > 0 ? `${h}h ${m}m` : `${m}m ${parts[2]}s`
  }
  const est = $derived(
    encode?.saved_percent != null
      ? encode.saved_percent <= 0
        ? { text: `+${Math.abs(encode.saved_percent).toFixed(1)}% larger`, grew: true }
        : { text: `${encode.saved_percent.toFixed(1)}% saved`, grew: false }
      : null
  )

  /** Source bitrate in Mbps (size per second) — the honest "how fat is
   * this source" figure next to the encode's own bitrate. */
  function bitrateMbps(size: number | null | undefined, duration: string | null): string | null {
    if (!size || !duration) return null
    const parts = duration.split(':').map(Number)
    if (parts.length !== 3 || parts.some(Number.isNaN)) return null
    const seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
    if (seconds <= 0) return null
    return ((size * 8) / 1_000_000 / seconds).toFixed(1)
  }

  /** Newest first for the history table. */
  const encodesDesc = $derived(encodes ? [...encodes].reverse() : [])
  /** Deltas between consecutive encodes (newest vs its predecessor). */
  function delta(current: EncodeRecord, previous: EncodeRecord | null): string {
    if (!previous) return ''
    const parts: string[] = []
    if (previous.output_size && current.output_size) {
      const diff = current.output_size - previous.output_size
      const pct = (diff / previous.output_size) * 100
      parts.push(`${diff <= 0 ? '−' : '+'}${Math.abs(pct).toFixed(1)}% size`)
    }
    if (previous.vmaf != null && current.vmaf != null) {
      const d = current.vmaf - previous.vmaf
      parts.push(`${d >= 0 ? '+' : '−'}${Math.abs(d).toFixed(1)} VMAF`)
    }
    return parts.length ? `(${parts.join(', ')})` : ''
  }
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="page page-in" tabindex="-1" bind:this={pageEl}>
  <header class="page-head">
    <button class="nav-link back" onclick={onclose}>← {backTo === 'overview' ? 'OVERVIEW' : 'LIBRARY'}</button>
  </header>

  <!-- ══ hero: the title as the page header ══ -->
  <section class="hero">
    <div class="hero-main">
      <h2>{movie.name}</h2>
      <p class="mono dim path">{movie.path}</p>
      <div class="chips">
        <span class="chip neutral"><span class="swatch"></span>{movie.category.toUpperCase()}</span>
        {#if movie.hdr}<span class="chip neutral"><span class="swatch"></span>HDR</span>{/if}
        {#if movie.missing}<span class="chip missing"><span class="swatch"></span>MISSING SINCE {movie.missing_since?.slice(0, 10) ?? 'LAST SCAN'}</span>{/if}
      </div>
    </div>
    <div class="hero-actions">
      {#if revealError}<span class="mono bad reveal-err">{revealError}</span>{/if}
      <button class="act" onclick={() => act('plan')} disabled={encodeBusy}>PLAN</button>
      <button class="act primary" onclick={() => act('encode')} disabled={encodeBusy}>ENCODE</button>
      <button class="act quiet" onclick={revealInExplorer} disabled={revealBusy} title="Open the containing folder in the file manager">
        {revealBusy ? 'OPENING…' : 'FOLDER'}
      </button>
    </div>
  </section>

  <div class="cols">
    <!-- ══ source facts ══ -->
    <section class="panel">
      <div class="panel-head"><span class="legend">SOURCE</span></div>
      <div class="facts">
        <div class="fact"><span class="legend">RESOLUTION</span><span class="mono">{movie.resolution?.join('×') ?? '—'}</span></div>
        <div class="fact"><span class="legend">DURATION</span><span class="mono">{fmtDuration(movie.duration) ?? '-'}</span></div>
        <div class="fact"><span class="legend">CODEC</span><span class="mono">{movie.codec ?? '—'}</span></div>
        <div class="fact"><span class="legend">SIZE</span><span class="mono">{fmtGb(movie.size)}</span></div>
        {#if bitrateMbps(movie.size, movie.duration)}
          <div class="fact"><span class="legend">SOURCE BITRATE</span><span class="mono">{bitrateMbps(movie.size, movie.duration)} Mbps</span></div>
        {/if}
      </div>
    </section>

    <!-- ══ latest encode: every recorded metric ══ -->
    <section class="panel">
      <div class="panel-head"><span class="legend">LATEST ENCODE — MEASURED</span></div>
      {#if !encode}
        <div class="dim pad">not encoded yet — plan predicts, encode measures</div>
      {:else}
        <div class="facts">
          <div class="fact big">
            <span class="legend">RESULT</span>
            <span class="mono result" class:bad={est?.grew}>{est?.text ?? '—'}</span>
          </div>
          <div class="fact"><span class="legend">SIZE</span><span class="mono">{fmtGb(encode.input_size)} → {fmtGb(encode.output_size)}</span></div>
          <div class="fact">
            <span class="legend">VMAF</span>
            <span class="gauge" role="img" aria-label="VMAF {encode.vmaf?.toFixed(1)} of target {targetVmaf}">
              <span class="gauge-fill" style="width: {Math.min(100, encode.vmaf ?? 0)}%"></span>
              <span class="gauge-target" style="left: {targetVmaf}%"></span>
            </span>
            <span class="mono measured">{encode.vmaf?.toFixed(2) ?? '—'}</span>
          </div>
          <div class="fact"><span class="legend">CRF</span><span class="mono">{encode.crf ?? '—'}</span></div>
          <div class="fact"><span class="legend">PRESET</span><span class="mono">{encode.preset}</span></div>
          {#if encode.encoder_preset}
            <div class="fact"><span class="legend">ENCODER PRESET</span><span class="mono">{encode.encoder_preset}</span></div>
          {/if}
          {#if encode.encode_seconds != null}
            <div class="fact"><span class="legend">ENCODE TIME</span><span class="mono">{fmtSeconds(encode.encode_seconds)}</span></div>
          {/if}
          {#if bitrateMbps(encode.output_size, movie.duration)}
            <div class="fact"><span class="legend">OUTPUT BITRATE</span><span class="mono">{bitrateMbps(encode.output_size, movie.duration)} Mbps</span></div>
          {/if}
          {#if encode.probe_count != null}
            <div class="fact">
              <span class="legend">PROBES</span>
              <span class="mono">{encode.probe_count} · {fmtSeconds(encode.probe_seconds)}</span>
            </div>
          {/if}
          {#if encode.tool_versions}
            <div class="fact"><span class="legend">TOOLCHAIN</span><span class="mono tv">{encode.tool_versions}</span></div>
          {/if}
        </div>
      {/if}
    </section>

    <!-- ══ encode history: every run recorded, with deltas ══ -->
    <section class="panel wide">
      <div class="panel-head"><span class="legend">ENCODE HISTORY — {encodesDesc.length || 'NO'} RUN{encodesDesc.length === 1 ? '' : 'S'}</span></div>
      {#if encodes === null}
        <div class="dim pad">loading…</div>
      {:else if encodesDesc.length === 0}
        <div class="dim pad">no encode recorded — the title is untouched</div>
      {:else}
        <div class="enc-table mono">
          <div class="enc-row enc-head legend" aria-hidden="true">
            <span>WHEN</span>
            <span class="num">CRF</span>
            <span class="num">VMAF</span>
            <span class="num">SIZE</span>
            <span class="num">SAVED</span>
            <span class="num">TIME</span>
            <span class="delta"></span>
          </div>
          {#each encodesDesc as rec, i (rec.encoded_at)}
            <div class="enc-row">
              <span class="dim">{fmtWhen(rec.encoded_at)}</span>
              <span class="num">{rec.crf ?? '—'}</span>
              <span class="num" class:measured={rec.vmaf != null}>{rec.vmaf?.toFixed(1) ?? '—'}</span>
              <span class="num">{fmtGb(rec.output_size)}</span>
              <span class="num" class:bad={(rec.saved_percent ?? 0) <= 0}>{rec.saved_percent != null ? `${rec.saved_percent.toFixed(0)}%` : '—'}</span>
              <span class="num">{rec.encode_seconds != null ? fmtSeconds(rec.encode_seconds) : '—'}</span>
              <span class="delta dim">{i < encodesDesc.length - 1 ? delta(rec, encodesDesc[i + 1]) : 'first run'}</span>
            </div>
          {/each}
        </div>
      {/if}
    </section>

    <!-- ══ job audit trail for this title ══ -->
    <section class="panel wide">
      <div class="panel-head"><span class="legend">JOB TRAIL</span></div>
      {#if jobs.length === 0}
        <div class="dim pad">no jobs for this movie yet</div>
      {:else}
        {#each jobs as job (job.id)}
          <div class="hist-row mono">
            <span class="dim">#{job.id}</span>
            <span>{job.kind}</span>
            <span class={job.state === 'done' ? 'ok' : job.state === 'failed' ? 'bad' : 'dim'}>{job.state}</span>
            <span class="dim">{fmtWhen(job.finished_at ?? job.started_at)}</span>
            <span class="dim err">{job.error ?? ''}</span>
          </div>
        {/each}
      {/if}
    </section>
  </div>
</div>

<style>
  .page { display: flex; flex-direction: column; gap: 14px; outline: none; }
  .back {
    background: transparent;
    border: none;
    color: var(--teal-ink);
    cursor: pointer;
    padding: 0;
    align-self: flex-start;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
    text-transform: uppercase;
  }
  .back:hover { text-decoration: underline; }
  .dim { color: var(--ink-dim); }
  .bad { color: var(--red); }
  .ok { color: var(--teal-ink); }

  .hero {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 18px;
    flex-wrap: wrap;
  }
  .hero h2 { font-size: 22px; font-weight: 700; }
  .path { margin: 4px 0 10px; word-break: break-all; }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; }
  .hero-actions { display: flex; gap: 8px; align-items: center; }
  .reveal-err { max-width: 260px; word-break: break-word; }

  .act {
    background: var(--panel-raised);
    border: 1px solid var(--seam);
    border-radius: var(--radius);
    color: var(--ink);
    padding: 6px 16px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
    text-transform: uppercase;
  }
  .act:hover:not(:disabled) { border-color: var(--teal); color: var(--teal-ink); }
  .act.primary { background: var(--teal); border-color: var(--teal); color: var(--ground); }
  .act.quiet { border-color: var(--seam); color: var(--ink-dim); }
  .act:disabled { opacity: 0.4; cursor: default; }

  .cols {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }
  .panel {
    border: 1px solid var(--seam);
    border-radius: var(--radius);
    background: var(--panel);
  }
  .panel.wide { grid-column: 1 / -1; }
  .panel-head {
    padding: 9px 14px;
    border-bottom: 1px solid var(--row-line);
  }
  .facts { display: flex; flex-direction: column; gap: 5px; padding: 12px 14px 14px; }
  .fact { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; }
  .fact.big .result { font-size: 15px; font-weight: 600; color: var(--teal-ink); }
  .fact.big .result.bad { color: var(--red); }
  .measured { color: var(--teal-ink); font-weight: 600; }
  .tv { word-break: break-all; text-align: right; }
  .pad { padding: 16px 14px; }
  .hist-row {
    display: flex;
    gap: 14px;
    padding: 6px 14px;
    border-bottom: 1px solid var(--row-line);
    font-size: var(--fs-data);
  }
  .hist-row:last-child { border-bottom: none; }
  .err { flex: 1; overflow: hidden; text-overflow: ellipsis; }

  /* VMAF gauge: fill vs the target mark */
  .fact .gauge {
    position: relative;
    flex: 1;
    max-width: 180px;
    height: 8px;
    background: var(--ground);
    border: 1px solid var(--row-line);
    border-radius: 4px;
    overflow: hidden;
  }
  .gauge-fill {
    position: absolute;
    inset: 0 auto 0 0;
    background: var(--teal);
  }
  .gauge-target {
    position: absolute;
    top: -2px;
    bottom: -2px;
    width: 2px;
    background: var(--amber);
  }

  /* encode history table */
  .enc-table { display: flex; flex-direction: column; padding: 6px 14px 12px; }
  .enc-row {
    display: grid;
    grid-template-columns: 90px 40px 50px 80px 50px 70px 1fr;
    gap: 10px;
    align-items: center;
    padding: 4px 0;
    border-bottom: 1px solid var(--row-line);
    font-size: 11px;
  }
  .enc-row:last-child { border-bottom: none; }
  .enc-head { border-bottom: 1px solid var(--seam); color: var(--ink-faint); font-size: 10px; letter-spacing: 1px; }
  .num { text-align: right; }
  .delta { text-align: right; font-size: 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  @media (max-width: 820px) {
    .cols { grid-template-columns: 1fr; }
  }
</style>
