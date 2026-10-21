<script lang="ts">
  /** The hint pattern: a caps label whose meaning lives in a tooltip on
   * hover AND in a visible one-line explanation on focus (keyboard users
   * get the same text). Used on action buttons where the verb alone
   * under-explains (PLAN, ENCODE, RESCAN ALL). */

  let {
    label,
    hint,
    onclick,
    disabled = false,
    variant = 'default',
  }: {
    label: string
    hint: string
    onclick?: () => void
    disabled?: boolean
    variant?: 'default' | 'primary'
  } = $props()
</script>

<span class="wrap" tabindex="-1">
  <button
    {onclick}
    {disabled}
    class:primary={variant === 'primary'}
    aria-label="{label}: {hint}"
    title={hint}
  >
    {label}
  </button>
  <span class="hint legend" role="note">{hint}</span>
</span>

<style>
  .wrap {
    position: relative;
    display: inline-flex;
  }
  button {
    background: var(--panel-raised);
    border: 1px solid var(--ink);
    border-radius: var(--radius);
    color: var(--ink);
    padding: 5px 14px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
    text-transform: uppercase;
  }
  button:hover:not(:disabled) { border-color: var(--teal); color: var(--teal-ink); }
  button.primary {
    background: var(--teal);
    border-color: var(--teal);
    color: var(--ground);
  }
  button.primary:hover:not(:disabled) { filter: brightness(1.08); color: var(--ground); }
  button:disabled { opacity: 0.4; cursor: default; }
  /* the visible hint: shows on hover and on keyboard focus-within */
  .hint {
    position: absolute;
    top: calc(100% + 6px);
    left: 0;
    min-width: 240px;
    max-width: 320px;
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
  .wrap:hover .hint,
  .wrap:focus-within .hint {
    opacity: 1;
  }
</style>
