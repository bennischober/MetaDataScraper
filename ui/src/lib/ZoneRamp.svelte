<script lang="ts">
  import type { Movie } from './api'

  /** The 11-step zone ramp as the VMAF quality scale: each step = 5 points
   * from 45 (zone 0) to 100 (zone 10). Every measured encode is plotted as a
   * tick on the scale — the record of what the tool actually achieved, not
   * just what it aims for. The outlined step marks the current target. */
  const steps = [
    '#0c0d0f', '#16181c', '#22252b', '#30343b', '#41464f', '#565c66',
    '#6f7680', '#8b939e', '#aab2bc', '#ccd3db', '#eef1f4',
  ]
  const MIN = 45
  const MAX = 100
  const SCALE_MARKS = [45, 55, 65, 75, 85, 95, 100]

  let { movies = [], targetVmaf = 95 }: { movies?: Movie[]; targetVmaf?: number } = $props()

  // continuous position on the ramp for a VMAF value (45..100 → 0..100%)
  const pct = (vmaf: number) => Math.max(0, Math.min(100, ((vmaf - MIN) / (MAX - MIN)) * 100))
  const zoneIndex = (vmaf: number) => Math.max(0, Math.min(10, Math.round((vmaf - MIN) / 5)))

  const measured = $derived(
    movies.flatMap((m) => (m.encode?.vmaf != null ? [{ name: m.name, vmaf: m.encode.vmaf }] : []))
  )
  const best = $derived(measured.length ? Math.max(...measured.map((m) => m.vmaf)) : null)
  const worst = $derived(measured.length ? Math.min(...measured.map((m) => m.vmaf)) : null)
  const targetPct = $derived(pct(targetVmaf))
  const targetStep = $derived(zoneIndex(targetVmaf))
</script>

<div
  class="ramp-block"
  role="img"
  aria-label="VMAF quality scale from 45 to 100. {measured.length} movies measured{best !== null ? `, best ${best.toFixed(1)}, worst ${worst!.toFixed(1)}` : ''}. Target {targetVmaf}."
>
  {#if measured.length > 0}
    <div class="target-row">
      <span class="target-label mono" style="left: {targetPct}%">TARGET {targetVmaf.toFixed(0)}</span>
    </div>
  {/if}
  <div class="ramp">
    {#each steps as color, i (color)}
      <div class="step" style="background: {color}" class:target={i === targetStep} title="VMAF {MIN + i * 5}"></div>
    {/each}
    {#each measured as m, i (i)}
      <div class="tick" style="left: {pct(m.vmaf)}%" title="{m.name} · VMAF {m.vmaf.toFixed(1)}"></div>
    {/each}
  </div>
  <div class="scale mono">
    {#each SCALE_MARKS as v (v)}
      <span class="mark" style="left: {pct(v)}%">{v}</span>
    {/each}
  </div>
  <div class="caption legend">
    {#if measured.length === 0}
      <span>no measured encodes yet — plan or encode to fill the record</span>
      <span>TARGET {targetVmaf.toFixed(0)}</span>
    {:else}
      <span>{measured.length} MEASURED</span>
      <span>WORST {worst!.toFixed(1)} · BEST {best!.toFixed(1)} · TARGET {targetVmaf.toFixed(0)}</span>
    {/if}
  </div>
</div>

<style>
  .ramp-block {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .target-row {
    position: relative;
    height: 13px;
  }
  .target-label {
    position: absolute;
    top: 0;
    transform: translateX(-50%);
    color: var(--magenta);
    font-size: 10px;
    letter-spacing: var(--track-legend);
    white-space: nowrap;
  }
  .ramp {
    position: relative;
    display: flex;
    height: 14px;
    border: 1px solid var(--seam);
  }
  .ramp .step {
    flex: 1;
  }
  .ramp .step.target {
    outline: 2px solid var(--magenta);
    outline-offset: -2px;
  }
  .tick {
    position: absolute;
    top: -2px;
    bottom: -2px;
    width: 2px;
    transform: translateX(-50%);
    background: var(--magenta);
    opacity: 0.9;
  }
  .scale {
    position: relative;
    height: 11px;
  }
  .scale .mark {
    position: absolute;
    top: 0;
    transform: translateX(-50%);
    color: var(--ink-dim);
  }
  .scale .mark:first-child {
    transform: none;
  }
  .scale .mark:last-child {
    transform: translateX(-100%);
  }
  .caption {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
</style>
