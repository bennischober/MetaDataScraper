<script lang="ts">
  /** The 11-step zone ramp: the backlit ground system of the world. */
  const steps = [
    '#0c0d0f', '#16181c', '#22252b', '#30343b', '#41464f', '#565c66',
    '#6f7680', '#8b939e', '#aab2bc', '#ccd3db', '#eef1f4',
  ]
</script>

<div class="ramp" role="presentation" aria-hidden="true">
  {#each steps as color (color)}
    <div style="background: {color}"></div>
  {/each}
</div>

<style>
  .ramp {
    display: flex;
    height: 14px;
    border: 1px solid var(--seam);
  }
  .ramp div {
    flex: 1;
  }
</style>
