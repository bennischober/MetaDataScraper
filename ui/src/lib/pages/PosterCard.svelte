<script lang="ts">
  import { fmtGb, type Movie } from '../api'

  /** A library card in the grid view: a typographic poster (no artwork
   * exists in the data model, so the card IS the typography — the title
   * set large on a quiet ground, facts below, state as a corner swatch). */

  let {
    movie,
    decision,
    selected = false,
    inWork = false,
    inWorkPercent = null,
    planned = false,
    onopen,
    ontoggle,
  }: {
    movie: Movie
    decision: { label: string; cls: string }
    selected?: boolean
    inWork?: boolean
    inWorkPercent?: number | null
    planned?: boolean
    onopen: () => void
    ontoggle: (checked: boolean) => void
  } = $props()
</script>

<article
  class="card"
  class:selected
  class:in-work={inWork}
  class:missing={movie.missing}
>
  <div class="poster">
    <span class="poster-title" aria-hidden="true">{movie.name}</span>
    <label class="pick">
      <input
        type="checkbox"
        checked={selected}
        onchange={(e) => ontoggle(e.currentTarget.checked)}
        aria-label="Select {movie.name}"
      />
    </label>
    {#if inWork}
      <span class="corner progress mono">IN WORK {inWorkPercent != null ? `${inWorkPercent}%` : ''}</span>
    {:else if movie.missing}
      <span class="corner missing mono">MISSING</span>
    {:else if decision.label !== 'UNTOUCHED'}
      <span class="corner {decision.cls} mono">{decision.label}</span>
    {/if}
  </div>
  <button class="meta" onclick={onopen}>
    <span class="name">{movie.name}</span>
    <span class="facts mono dim">
      {movie.resolution ? `${movie.resolution[0]}×${movie.resolution[1]}` : '—'}
      · {fmtGb(movie.size)}
      {#if movie.encode?.vmaf != null}
        · <span class="measured">VMAF {movie.encode.vmaf.toFixed(1)}</span>
      {/if}
      {#if movie.hdr}<span class="hdr">HDR</span>{/if}
    </span>
  </button>
</article>

<style>
  .card {
    display: flex;
    flex-direction: column;
    border: 1px solid var(--seam);
    border-radius: var(--radius);
    background: var(--panel);
    overflow: hidden;
    transition: border-color 120ms ease;
  }
  .card:hover { border-color: var(--ink-dim); }
  .card.selected { border-color: var(--teal); background: var(--panel-active); }
  .card.in-work { border-color: var(--amber); }
  .card.missing { opacity: 0.55; }

  .poster {
    position: relative;
    aspect-ratio: 2 / 3;
    background:
      linear-gradient(160deg, var(--poster-ground), var(--ground));
    display: flex;
    align-items: center;
    justify-content: center;
    border-bottom: 1px solid var(--row-line);
  }
  .poster-title {
    font-family: var(--font-ui);
    font-size: 17px;
    font-weight: 700;
    line-height: 1.3;
    letter-spacing: 0.3px;
    color: var(--ink-dim);
    padding: 14px;
    text-align: center;
    overflow: hidden;
    display: -webkit-box;
    -webkit-line-clamp: 4;
    line-clamp: 4;
    -webkit-box-orient: vertical;
    user-select: none;
  }
  .pick {
    position: absolute;
    top: 8px;
    left: 8px;
  }
  .pick input {
    width: 15px;
    height: 15px;
    accent-color: var(--teal);
    cursor: pointer;
  }
  .corner {
    position: absolute;
    bottom: 8px;
    right: 8px;
    font-size: 9.5px;
    letter-spacing: 1px;
    padding: 2px 6px;
    border-radius: 2px;
    background: var(--panel);
    border: 1px solid var(--seam);
  }
  .corner.done { color: var(--ok); border-color: var(--ok); }
  .corner.skip { color: var(--amber); }
  .corner.planned { color: var(--teal-ink); border-color: var(--teal); }
  .corner.progress { color: var(--amber); border-color: var(--amber); }
  .corner.missing { color: var(--red); border-style: dashed; }

  .meta {
    display: flex;
    flex-direction: column;
    gap: 3px;
    padding: 9px 10px 10px;
    background: transparent;
    border: none;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .name {
    font-weight: 600;
    font-size: 12.5px;
    line-height: 1.25;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .meta:hover .name { color: var(--teal-ink); }
  .facts { font-size: 10.5px; display: flex; flex-wrap: wrap; gap: 3px; align-items: baseline; }
  .measured { color: var(--teal-ink); }
  .hdr { color: var(--amber); border: 1px solid var(--seam); padding: 0 3px; border-radius: 2px; }
</style>
