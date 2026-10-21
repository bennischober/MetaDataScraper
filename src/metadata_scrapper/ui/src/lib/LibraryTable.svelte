<script lang="ts">
  import type { Movie } from './api'
  import { fmtGb, fmtSaved } from './api'

  let { movies }: { movies: Movie[] } = $props()

  // decision as density: solid=done, hatched=skipped, empty=untouched
  function decisionOf(movie: Movie): { label: string; cls: string } {
    const preset = movie.encode?.preset
    if (preset?.startsWith('skipped')) return { label: 'SKIPPED', cls: 'skip' }
    if (preset?.startsWith('VMAF') || preset) {
      return { label: 'ENCODED', cls: 'done' }
    }
    return { label: 'UNTOUCHED', cls: 'untouched' }
  }
</script>

<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th class="legend">Movie</th>
        <th class="legend">Category</th>
        <th class="legend">Resolution</th>
        <th class="legend num">Size</th>
        <th class="legend">Decision</th>
        <th class="legend num">CRF</th>
        <th class="legend num">VMAF</th>
        <th class="legend num">Saved</th>
      </tr>
    </thead>
    <tbody>
      {#each movies as movie (movie.path)}
        {@const decision = decisionOf(movie)}
        <tr>
          <td class="name">
            {movie.name}
            {#if movie.hdr}<span class="mono hdr-tag">HDR</span>{/if}
          </td>
          <td class="dim">{movie.category}</td>
          <td class="mono">{movie.resolution ? `${movie.resolution[0]}x${movie.resolution[1]}` : '—'}</td>
          <td class="mono num">{fmtGb(movie.size)}</td>
          <td>
            <span class="chip {decision.cls}">
              <span class="swatch"></span>{decision.label}
            </span>
          </td>
          <td class="mono num">{movie.encode?.crf ?? '—'}</td>
          <td class="mono num">{movie.encode?.vmaf?.toFixed(2) ?? '—'}</td>
          <td class="mono num saved" class:ok={(movie.encode?.saved_percent ?? 0) > 0}>
            {movie.encode ? fmtSaved(movie.encode.saved_percent) : '—'}
          </td>
        </tr>
      {:else}
        <tr><td colspan="8" class="empty dim">No movies scanned yet — run a scan.</td></tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  .table-wrap {
    flex: 1;
    border: 1px solid var(--seam);
    background: var(--panel);
    overflow: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
  }
  thead tr {
    border-bottom: 1px solid var(--seam);
  }
  th {
    padding: 8px 16px;
    text-align: left;
    position: sticky;
    top: 0;
    background: var(--panel);
  }
  th.num { text-align: right; }
  td {
    padding: 7px 16px;
    border-bottom: 1px solid var(--row-line);
    white-space: nowrap;
  }
  td.num { text-align: right; }
  td.name { font-weight: 600; max-width: 340px; overflow: hidden; text-overflow: ellipsis; }
  .dim { color: var(--ink-dim); }
  .saved.ok { color: var(--ok); }
  .hdr-tag {
    margin-left: 8px;
    color: var(--magenta);
    font-size: 10px;
    border: 1px solid var(--seam);
    padding: 0 4px;
    border-radius: 2px;
  }
  .empty { text-align: center; padding: 24px; }
</style>
