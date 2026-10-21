<script lang="ts">
  /** The 11-step zone ramp as the VMAF quality scale: each step = 5 points
   * from 45 (zone 0) to 100 (zone 10). The marker sits at the current
   * target; encodes live where they measured. */
  const steps = [
    '#0c0d0f', '#16181c', '#22252b', '#30343b', '#41464f', '#565c66',
    '#6f7680', '#8b939e', '#aab2bc', '#ccd3db', '#eef1f4',
  ]

  let { targetVmaf = 95 }: { targetVmaf?: number } = $props()

  // zone index for a VMAF value (45..100 → 0..10)
  const zoneOf = (vmaf: number) => Math.max(0, Math.min(10, Math.round((vmaf - 45) / 5)))
  const targetZone = $derived(zoneOf(targetVmaf))
</script>

<div class="ramp-block" role="img" aria-label="VMAF quality scale: target zone {targetZone} of 10">
  <div class="ramp">
    {#each steps as color, i (color)}
      <div
        style="background: {color}"
        class:target={i === targetZone}
        title="VMAF {45 + i * 5}"
      ></div>
    {/each}
  </div>
  <div class="labels mono">
    <span>45</span>
    <span class="spacer"></span>
    <span class="target-label">TARGET {targetVmaf.toFixed(0)} · ZONE {targetZone}</span>
    <span class="spacer"></span>
    <span>100</span>
  </div>
</div>

<style>
  .ramp-block {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .ramp {
    display: flex;
    height: 14px;
    border: 1px solid var(--seam);
  }
  .ramp div {
    flex: 1;
  }
  .ramp div.target {
    outline: 2px solid var(--magenta);
    outline-offset: -2px;
  }
  .labels {
    display: flex;
    font-size: 10px;
    color: var(--ink-dim);
  }
  .target-label {
    color: var(--magenta);
  }
  .spacer { flex: 1; }
</style>
