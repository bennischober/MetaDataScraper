<script lang="ts">
  /** A standalone hint mark: a small (?) that reveals its text on hover
   * and focus. For terms whose meaning cannot ride on a button (the VMAF
   * target input, the watcher toggle). */
  let { text }: { text: string } = $props()
</script>

<span class="hint-mark">
  <button type="button" aria-label={text}>
    <span aria-hidden="true">?</span>
  </button>
  <span class="tip legend">{text}</span>
</span>

<style>
  .hint-mark {
    position: relative;
    display: inline-flex;
    flex-shrink: 0;
  }
  .hint-mark button {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 15px;
    height: 15px;
    padding: 0;
    border: 1px solid var(--seam);
    border-radius: 50%;
    background: transparent;
    color: var(--ink-dim);
    font-size: 9px;
    cursor: help;
  }
  .hint-mark button:hover,
  .hint-mark button:focus-visible {
    border-color: var(--teal);
    color: var(--teal-ink);
  }
  .tip {
    position: absolute;
    bottom: calc(100% + 6px);
    left: 50%;
    transform: translateX(-50%);
    min-width: 220px;
    max-width: 300px;
    background: var(--panel-raised);
    border: 1px solid var(--seam);
    border-radius: var(--radius);
    color: var(--ink-dim);
    padding: 6px 10px;
    font-size: 10.5px;
    letter-spacing: 0.2px;
    text-transform: none;
    z-index: 70;
    opacity: 0;
    pointer-events: none;
    transition: opacity 120ms ease;
    white-space: normal;
  }
  .hint-mark:hover .tip,
  .hint-mark:focus-within .tip {
    opacity: 1;
  }
</style>
