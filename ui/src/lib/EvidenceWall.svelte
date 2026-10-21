<script lang="ts">
  import type { Movie } from './api'

  /** FIG. 1 — the evidence wall. The VMAF scale as a dimension line:
   * measured encodes plotted as ticks with leader labels, the target as a
   * dimension arrow. The record of what the tool measured, not what it
   * hopes. */
  const MIN = 45
  const MAX = 100
  const SCALE_MARKS = [45, 55, 65, 75, 85, 95, 100]

  let { movies = [], targetVmaf = 95 }: { movies?: Movie[]; targetVmaf?: number } = $props()

  const pct = (vmaf: number) => Math.max(0, Math.min(100, ((vmaf - MIN) / (MAX - MIN)) * 100))

  const measured = $derived(
    movies
      .flatMap((m) => (m.encode?.vmaf != null ? [{ name: m.name, vmaf: m.encode.vmaf }] : []))
      .sort((a, b) => a.vmaf - b.vmaf)
  )
  // de-overlap leader labels: worst and best always carry their name;
  // middle ticks alternate so neighbors never collide
  const ticks = $derived(
    measured.map((m, i) => ({
      ...m,
      showName: i === 0 || i === measured.length - 1 || i % 2 === 0,
    }))
  )
  const best = $derived(measured.length ? measured[measured.length - 1] : null)
  const worst = $derived(measured.length ? measured[0] : null)
  const targetPct = $derived(pct(targetVmaf))
</script>

<section class="fig" class:empty={measured.length === 0} role="img" aria-label="Figure 1: VMAF quality scale from 45 to 100. {measured.length} movies measured{worst ? `, worst ${worst.vmaf.toFixed(1)}, best ${best!.vmaf.toFixed(1)}` : ''}. Target {targetVmaf}.">
  <div class="fig-head">
    <span class="legend">FIG. 1 — MEASURED QUALITY{measured.length ? ', LAST RUNS' : ''}</span>
    <span class="legend">
      {#if measured.length === 0}
        NO MEASUREMENTS YET — ENCODE IN QUALITY MODE TO PLOT
      {:else}
        {measured.length} MEASURED · WORST {worst!.vmaf.toFixed(1)} · BEST {best!.vmaf.toFixed(1)}
      {/if}
    </span>
  </div>

  <div class="dimension">
    {#if measured.length === 0}
      <div class="target empty-target" style="left: {targetPct}%">
        <span class="target-label mono below">▲ TARGET {targetVmaf.toFixed(0)}</span>
      </div>
    {:else}
      {#each ticks as t, i (t.name + i)}
        <div class="tick" style="left: {pct(t.vmaf)}%" class:muted={!t.showName} title="{t.name} · VMAF {t.vmaf.toFixed(1)}">
          <span class="tick-label mono" class:muted={!t.showName}>
            {t.vmaf.toFixed(1)}<span class="tick-name">{t.showName ? ` ${t.name}` : ''}</span>
          </span>
        </div>
      {/each}
      <div class="target" style="left: {targetPct}%">
        <span class="target-label mono">▼ TARGET {targetVmaf.toFixed(0)}</span>
      </div>
    {/if}
    <div class="axis"></div>
    <div class="marks mono">
      {#each SCALE_MARKS as v (v)}
        <span style="left: {pct(v)}%">{v}</span>
      {/each}
    </div>
  </div>
</section>

<style>
  .fig {
    border: 1px solid var(--seam);
    background: var(--panel);
    padding: 10px 16px 14px;
  }
  .fig-head {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 6px;
  }
  .dimension {
    position: relative;
    height: 56px;
  }
  .fig.empty .dimension {
    height: 40px;
  }
  .axis {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 16px;
    border-top: 1px solid var(--ink);
  }
  .tick {
    position: absolute;
    bottom: 16px;
    width: 1px;
    height: 22px;
    background: var(--teal);
    transform: translateX(-50%);
  }
  .tick.muted { height: 12px; }
  .tick-label {
    position: absolute;
    bottom: 24px;
    left: 50%;
    transform: translateX(-50%);
    color: var(--teal-ink);
    white-space: nowrap;
  }
  .tick-label.muted { color: var(--ink-dim); }
  .tick-name {
    font-family: var(--font-ui);
    font-size: 10px;
    color: var(--ink-dim);
  }
  .target {
    position: absolute;
    bottom: 16px;
    width: 1px;
    height: 30px;
    background: var(--ink);
    transform: translateX(-50%);
  }
  .target-label {
    position: absolute;
    top: -16px;
    left: 50%;
    transform: translateX(-50%);
    color: var(--ink);
    white-space: nowrap;
  }
  .target-label.below {
    top: auto;
    bottom: -14px;
    color: var(--ink-dim);
  }
  .marks {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    height: 12px;
  }
  .marks span {
    position: absolute;
    transform: translateX(-50%);
    color: var(--ink-dim);
    font-size: 10px;
  }
  .marks span:first-child { transform: none; }
  .marks span:last-child { transform: translateX(-100%); }
</style>
