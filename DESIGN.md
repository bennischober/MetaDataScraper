---
name: MetaDataScraper Dashboard
description: The Drafting Table — a working drawing sheet for a measured movie-library workbench
colors:
  ground: "#eceef0"
  panel: "#f4f5f6"
  panel-active: "#e2f0ee"
  row-alt: "#e9ebed"
  seam: "#c9ced3"
  row-line: "#dde1e4"
  grid-line: "rgba(30, 35, 40, 0.055)"
  ink: "#1e2328"
  ink-dim: "#5b6470"
  ink-faint: "#98a1ab"
  teal: "#0f766e"
  teal-ink: "#0b5952"
  amber: "#b45309"
  red: "#b3382c"
typography:
  title-block:
    fontFamily: "IBM Plex Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "19px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "3px"
  title:
    fontFamily: "IBM Plex Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 700
    lineHeight: 1.3
  body:
    fontFamily: "IBM Plex Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.45
  legend:
    fontFamily: "IBM Plex Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "10.5px"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "1.4px"
  data:
    fontFamily: "IBM Plex Mono, Consolas, ui-monospace, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.45
  data-strong:
    fontFamily: "IBM Plex Mono, Consolas, ui-monospace, monospace"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.45
rounded:
  none: "0px"
  chip: "2px"
spacing:
  sm: "8px"
  md: "14px"
  lg: "18px"
  xl: "26px"
components:
  button-primary:
    backgroundColor: "{colors.teal}"
    textColor: "#ffffff"
    rounded: "{rounded.none}"
    padding: "5px 14px"
  button-primary-hover:
    backgroundColor: "{colors.teal-ink}"
    textColor: "#ffffff"
    rounded: "{rounded.none}"
    padding: "5px 14px"
  button-secondary:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "5px 14px"
  button-secondary-hover:
    backgroundColor: "{colors.panel-active}"
    textColor: "{colors.teal-ink}"
    rounded: "{rounded.none}"
    padding: "5px 14px"
  chip-state:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.chip}"
    padding: "2px 8px"
  input-text:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "4px 6px"
---

# Design System: MetaDataScraper Dashboard

## Overview

**Creative North Star: "The Drafting Table"**

The dashboard is a working drawing sheet, not a control panel. The library is surveyed, annotated and measured on cool paper under a faint construction grid; graphite ink carries the text, and a single schematic teal carries meaning — selection, the active tab, the primary action, the measured tick. Every value on the sheet is a measurement: mono figures with their units, labels in tracked caps like drawing annotations, hairline seams instead of card shadows. The world (locked 2026-10-21, seed `3241af8c9d381f2a`) deliberately refuses two clichés: the stacked-stat-panels dashboard arrangement and the costume of any operated instrument — no terminal chrome, no console glow, no poster wall.

State is expressed as **density**, the way a draftsman fills a region: solid fill means done, diagonal hatching means in progress, empty outline means untouched. Color participates in that system but never decorates it — teal, amber and red are budgeted for meaning only. The single allowed flourish is the annotation moment: selecting rows draws a leader-line note into the margin ("12 SELECTED → EST. −38.2 GB"), the sheet talking back to its surveyor.

The register is quiet, dense and honest. Numbers are never rounded into vagueness, absent values are an em-dash, and irreversible actions (bar cropping, duplicate deletion) interrupt with a drawn confirmation ceremony rather than a toast. The surface serves one expert user on localhost; the visual system optimizes for long, focused work sessions at readable density, not for marketing impressions.

**Key Characteristics:**

- Cool paper ground (`#eceef0`) with a 24px construction grid as the sheet's visible armature
- Graphite ink text, hairline seams, zero shadows — depth comes from tone, line weight and hatching
- One meaning-carrying accent (schematic teal) plus amber (in progress) and red (failure/destruction only)
- Two type voices: IBM Plex Sans speaks and labels, IBM Plex Mono measures (tabular numerals)
- Density-state system: solid / hatched / empty swatches and row fills
- Title-block header, domain sheet tabs, bulk action strip, margin-callout drill-down, L-corner ticks marking the active panel
- Compact data density: 12px tabular data, 10.5px tracked caps labels, 13px body

## Colors

A cool, near-neutral paper palette where nearly everything is graphite-on-paper and three accent hues carry all the meaning.

### Primary
- **Schematic Teal** (`#0f766e`): The system's one voice. Selection hatching, checkbox accents, the active tab underline, the primary action fill (white text on it), measured VMAF ticks on the evidence wall, corner ticks on the active panel, the callout's left rule, focus rings, text caret, and text selection background (at 22% alpha).
- **Teal Ink** (`#0b5952`): The contrast-safe teal for text *on paper* — active tab labels, leader-note estimates, measured VMAF values, positive saved percentages. Use this, not `--teal`, whenever teal appears as text on the sheet.

### Secondary
- **Alert Amber** (`#b45309`): The figure in progress. Running-row hatching and `IN WORK` chips, the progress trace's leading edge, the watcher/queue warning counts, the HDR tag, and the drawn warning mark on the irreversible bar-crop toggle. Amber is a status, never a decoration.

### Tertiary
- **Alarm Red** (`#b3382c`): Failed and destructive only. Failed job bands and error strips (with a faint red hatch background), delete/confirm buttons, the irreversible-work confirmation strip border and hatch.

### Neutral
- **Sheet Ground** (`#eceef0`): The page itself; `body` background and the sheet surface.
- **Panel** (`#f4f5f6`): Slightly lighter sheet stock — panels, bars, table background, chips, buttons at rest.
- **Active Panel** (`#e2f0ee`): Teal-tinted paper for the panel under work — active filter chips, hovered cells, best-keep duplicate rows, open callout rows.
- **Row Alt** (`#e9ebed`): Reserved zebra value, defined in `tokens.css` but currently unreferenced — tables separate rows with hairline rules instead.
- **Seam** (`#c9ced3`): The hairline workhorse — panel borders, chip borders, inactive button borders, divider rules, scrollbar thumb.
- **Row Line** (`#dde1e4`): Inner table rules between rows.
- **Grid Line** (`rgba(30, 35, 40, 0.055)`): The 24px construction grid under panels and the table.
- **Graphite Ink** (`#1e2328`): Primary text, structural 1–2px rules, the target arrow on the dimension line, overlay scrims (at 45–50% alpha).
- **Dim Ink** (`#5b6470`): Secondary text, legend labels, quiet states.
- **Faint Ink** (`#98a1ab`): Disabled/ghost text, placeholder, job IDs, removable-chip ✕ buttons.

### Named Rules
**The Meaning-Only Accent Rule.** Teal, amber and red encode state — selection, progress, failure. They never appear as decoration; if a color isn't telling the user something about their data or work, it isn't there.

**The Red Lock Rule.** Red is reserved for failure and irreversible destruction. Nothing else — not badges, not emphasis, not charts — may use it.

**The Teal-Ink Rule.** Teal as text on paper is always the darker `--teal-ink` (`#0b5952`), never the fill teal, so accent text stays contrast-safe.

## Typography

**Display Font:** IBM Plex Sans (self-hosted woff2, weights 400–700; fallbacks `Segoe UI, system-ui, sans-serif`)
**Body Font:** IBM Plex Sans (same family — there is no separate display face)
**Label/Mono Font:** IBM Plex Mono (self-hosted woff2, 400 and 500–600; fallbacks `Consolas, ui-monospace, monospace`)

**Character:** A drafting-office pairing. Plex Sans speaks — titles, labels, prose — in small, tightly tracked caps for annotations; Plex Mono measures — every byte, percent, CRF and VMAF score in tabular numerals so columns align like a dimension chain. Faces are self-hosted because a localhost tool must not depend on a CDN.

### Hierarchy
- **Title Block** (700, 19px, 3px letter-spacing, uppercase by content): The product name in the header title block — the drawing's stamp. The only large type on the sheet.
- **Title** (700, 15px, ~1.3): Sheet titles inside callouts and detail views.
- **Body** (400, 13px, 1.45): General UI text, callout facts, names, empty-state sentences.
- **Legend** (400, 10.5px, 1.4px letter-spacing, uppercase — `.legend`): The annotation voice: figure headings, sheet labels (`SHEET 02 — DUPLICATE GROUPS`), column headers, button text, key/value keys, the colophon. Always dim or body ink, never accent-colored except where meaning applies.
- **Data** (400, 12px, `.mono`, `font-variant-numeric: tabular-nums`): All measurements — sizes in GB, percents, resolutions, CRF/VMAF values, job IDs, elapsed times. Strong variant at 600 for emphasized values (title-block cell values, name cells).
- **Micro** (10px, Plex Sans or Mono): tick names, axis marks, small button labels where legend would overflow.

### Named Rules
**The Two Voices Rule.** Plex Sans talks, Plex Mono measures. Anything that is a number, path, or machine value is mono tabular; anything addressed to the human is Plex Sans. Never set measurements in the sans.

**The Every Number Carries Its Unit Rule.** Data is rendered with explicit units and fixed precision ("4.12 GB", "95.25", "+64.3%"), and an absent value is an em-dash (`—`), never blank or zero.

## Layout

The page is one full-width drawing sheet: a single flex column (`min-height: 100vh`, `padding: 18px 26px 30px`, `gap: 14px`) that reads top to bottom like a sheet is read:

1. **Title block** — product name + annotation line on the left, a bordered cell strip on the right (SHEET · SAVED · WATCHER · QUEUE); closed by a 2px ink rule.
2. **FIG. 1 — evidence wall** — the measured-quality dimension line.
3. **Domain tabs** — LIBRARY / DUPLICATES / JOBS with mono counts, CONFIG pinned right; sheet-tab styling.
4. **Bulk bar** — the action strip: selection controls and per-selection actions left (with the leader-note estimate), TARGET VMAF input and SCAN ALL / PLAN ALL / ENCODE ALL right.
5. **Domain sheet** — the active table or panel; the library table is the primary work area.
6. **Sources strip** — mono path chips (the drawing's source note) plus the bar-crop toggle.
7. **Live job bands** — running/failed work bands (library domain only).
8. **Colophon** — centered legend line pinned to the sheet foot.

Spacing clusters around the token steps (8/14/18/26px) plus a few in-context values: table cells `7px 12px`, header cells `8px 12px`, bars `8px 14px`, chips `2px 8px` to `3px 12px`. Density is compact but airy — hairlines do the separating, padding never crowds.

The library table: sticky header over a scroll area, checkbox column 34px, name column max 340px with ellipsis, numeric columns right-aligned, inner rules `--row-line`. The construction grid (24px, `--grid-line`) underlays the table wrap and is available via `.sheet-grid` for any panel that should read as drawing surface.

Responsive: below **760px** the sheet padding drops to 12px, the title-block cells span full width, and the bulk bar stacks vertically with actions right-aligned. There are no other breakpoints — this is a desktop workbench.

## Elevation & Depth

**No shadows — the system is flat and states it.** Depth is conveyed by four means only:

- **Tonal layering**: ground → panel → active-panel, a two-step lightness ramp plus a teal tint for "under work".
- **Line weight**: hairline 1px seams for containers, 2px ink rules for structural boundaries (title block, table head, active tab underline, callout edge).
- **Density hatching**: the four repeating-linear-gradient hatch patterns (see Shapes) fill regions to signal state.
- **Overlay scrim**: modal layers sit on `rgba(30, 35, 40, 0.45–0.5)` — callout at `z-index: 40`, deletion confirm at `z-index: 60`, sticky table header at `z-index: 1`.

### Shadow Vocabulary
None. Do not introduce box-shadows; a shadow would read as a foreign sticker on the sheet.

### Named Rules
**The Hairline Rule.** Separation is drawn, not lit. Any need for depth is answered with a seam, a tone step, or a heavier rule — never a shadow or glow.

## Shapes

The form language is sharp-cornered technical drawing. Corners are square (0px) everywhere; the single exception is the state chip at 2px. Borders are 1px hairlines (`--seam`) for containers and controls, with 2px structural rules (ink or teal) reserved for boundaries that matter.

- **Corner ticks** (`.corner-ticks`): 9px L-shapes in 2px teal drawn at the top-left and bottom-right of the active panel — the drawing marks the region being worked. Applied to the library panel frame.
- **Hatch patterns** (45° stripes, 4px on / 4px off unless noted):
  - `--hatch-work` — neutral grey hatch (`#d4d9dd`): the empty progress-trace track.
  - `--hatch-progress` — amber at 28% alpha: running rows, `IN WORK` chips, the trace fill (terminated by a 2px amber right edge).
  - `--hatch-skip` — amber at 18% alpha, vertical 90° 6px/6px: skipped-state chips (deliberately coarser so skip reads differently from progress).
  - `--hatch-select` — teal at 12% alpha: selected rows and `PLANNED` chips.
- **Drawn marks over glyphs**: the bar-crop warning is drawn with two 1.5px amber crossing strokes (a CSS X), not a unicode character — marks are constructed like linework.
- **Path truncation**: long paths use `direction: rtl` so the *tail* of the path stays visible — the folder name is the information.
- **Scrollbars**: thin, `--seam` thumb on transparent track (Webkit: 10px, darkening to faint ink on hover).

## Components

### Buttons (`.act`)
- **Shape:** sharp corners (0px), 1px border, legend voice (10.5px, 1.4px tracking, uppercase), padding `5px 14px` (`6px 16px` inside the callout).
- **Secondary (default):** panel background, 1px ink border, ink text. Hover: active-panel background, teal-ink border and text.
- **Primary:** solid teal fill, white text, teal border. Hover: teal-ink fill. This is the sheet's single loudest element — ENCODE ALL and the callout's ENCODE.
- **Quiet:** seam border, dim ink text (CLEAR, CANCEL, DISMISS). In job rows, quiet hover turns red — cancellation and dismissal are destructive-adjacent.
- **Disabled:** 40% opacity, default cursor.
- **Destructive outline (delete, confirm):** seam border with red text; hover fills red with white text.

### State Chips (`.chip`)
- **Style:** 2px-radius pill, panel background, 1px seam border, legend voice, padding `2px 8px`; a 10px square `.swatch` with 1px dim-ink border leads the label.
- **State variants:** `done` (solid teal swatch), `progress` (amber hatch), `skip` (coarse amber hatch), `untouched` (empty swatch), `failed` (solid red), `neutral` (seam fill, for group reasons like "SAME HASH"), `planned` (teal hatch, teal border). The running row swaps its decision chip for a live `IN WORK {percent}%` chip.
- **Filter chips (`.cat-chip`):** the category filter row — mono 12px, seam border, dim text; active state: teal border, active-panel fill, teal-ink text; `aria-pressed` carries the toggle state.

### Title Block
The header stamp. Left: product name in 19px/700/3px-tracking caps over a legend annotation line (library size, VMAF target). Right: a 1px-ink-bordered cell strip whose cells are separated by 1px seams — each cell is a key (9px tracked caps, dim) over a value (12px mono/sans, 600). The WATCHER cell is a live toggle button (● RUNNING in teal ink / ○ STOPPED) with an active-panel hover. The block closes with a 2px ink rule — the heaviest line on the sheet.

### Sheet Tabs
Domain tabs read as sheet tabs: transparent buttons in legend voice, dim ink; hover darkens to ink; the active tab gets panel fill, a 1px seam border (bottom hidden by a −1px margin overlap) and a 2px teal underline in teal-ink text. Counts ride along in mono; a non-zero duplicates or running-jobs count turns amber. CONFIG sits alone on the right after a flex spacer.

### Bulk Bar
The action strip above the work, always visible on the library domain: 1px ink border, panel fill, `8px 14px` padding, split left/right. Left: select-all checkbox (teal accent, 14px) with `{n} SELECTED` in mono, ENCODE SELECTED / PLAN SELECTED / CLEAR, then the **leader note** — `→ EST. −X.X GB · Y GB BASE` in teal-ink mono, entering with the leader-draw animation. Right: the TARGET input (58px mono number field, 70–100 step 0.5) and SCAN ALL / PLAN ALL / **ENCODE ALL** (primary). This strip is where bulk verbs live — never scatter actions into rows.

### Detail Callout (signature component)
The three-level drill-down rendered as a margin annotation, not a centered modal: a fixed right-anchored panel (`min(480px, 94vw)`), panel fill, **2px teal left rule**, over a graphite scrim. Level 1 is the row; level 2 is the callout's provenance facts (label/value pairs in legend + mono); level 3 is the raw measurement box — a seam-bordered sub-panel with a collapsible `LATEST ENCODE — MEASUREMENT ▲/▼` toggle. Actions (PLAN, ENCODE primary, FOLDER) sit mid-panel; the movie's job history runs as mono rows beneath. The callout is a dialog: focus moves in on open, Tab is trapped, Escape closes, focus returns to the opening row button.

### Evidence Wall (FIG. 1)
The measured-quality dimension line in a seam-bordered panel: an ink axis line, scale marks at 45/55/65/75/85/95/100, each measured encode plotted as a 1px teal tick (22px tall; 12px when its name is suppressed by the alternate-label de-overlap rule; worst and best always labeled) with its VMAF value in teal-ink mono and the movie name in 10px dim sans, and the target as a taller ink line with a ▼ TARGET label. Empty state: only the target arrow (▲, below the axis, dim) and a legend note that nothing has been measured yet. The whole figure is `role="img"` with a complete text summary in its `aria-label`.

### Job Bands & Trace
Running and failed work render as full-width bands (seam border, panel fill; failed gets a red border and red hatch). Each band: state tag (`IN WORK` amber / `FAILED #n` red) in tracked mono caps, the movie name in 600 sans, detail in dim mono, and a quiet action. The **trace** is the hatched progress bar: `--hatch-work` track, `--hatch-progress` fill with a 2px amber leading edge, and the mono reading (movie · stage · percent) overlaid right-aligned; it is `role="progressbar"` with live `aria-valuenow`.

### Inputs / Fields
Hairline seam border, panel fill, mono text, sharp corners, teal caret, faint-ink placeholder. The "add" inputs (new folder, new extension) use a **dashed** seam border — an unfinished-entry affordance. Number input for the VMAF target. Config fields: 120px dim label + mono input; groups sit in seam-bordered boxes on ground fill.

### Browser Surfaces
The chrome itself is themed: `::selection` teal at 22% alpha; input carets teal; placeholders faint ink (opacity forced to 1); thin scrollbars with seam thumbs; `:focus-visible` a 2px teal outline at 1px offset — keyboard operators are first-class.

## Do's and Don'ts

### Do:
- **Do** set every measurement in mono tabular figures with its unit and an em-dash for absent values (`4.12 GB`, `95.25`, `—`).
- **Do** use the legend voice for all labels: 10.5px, 1.4px tracking, uppercase, dim ink.
- **Do** express state as density — solid teal = done, amber hatch = in progress, coarse amber hatch = skipped, teal hatch = selected/planned, empty swatch = untouched.
- **Do** mark the active work area with L-corner ticks and the teal-tinted panel.
- **Do** keep bulk verbs in the bulk bar and per-movie verbs in the callout.
- **Do** gate irreversible work behind a per-run confirmation (`role="alertdialog"`, red hatch strip); the armed state must never persist.
- **Do** move focus into dialogs and return it to the opener; keep every control keyboard-operable.

### Don't:
- **Don't** use shadows, glows, or blur — depth is tone, line weight, and hatch only.
- **Don't** apply red to anything but failure or irreversible destruction, or amber to anything but progress/warning states.
- **Don't** round corners beyond the 2px chip radius or introduce filled/gradient surfaces beyond the four hatch patterns.
- **Don't** color decoration: teal only marks selection, active tab, primary action, and measured values.
- **Don't** dress the sheet as a terminal, console, or poster wall — no dark themes, no monospace-as-identity, no imagery.
- **Don't** persist `--remove-bars` across runs or enable destructive actions without explicit ceremony.

## Accessibility

Baseline commitments observed in the built components (single-user localhost, but keyboard operability and readable contrast are the product's stated floor):

- **Focus**: global `:focus-visible` — 2px solid teal outline, 1px offset. Checkboxes use `accent-color: var(--teal)` at 13–14px.
- **Dialogs**: the detail callout is `role="dialog"` + `aria-modal="true"` with `aria-label`, receives focus on open, traps Tab, closes on Escape, and restores focus to the triggering button. Destructive confirmations are `role="alertdialog"` with explicit `aria-label`s.
- **Live status**: API errors use `role="alert"`; the job trace is `role="progressbar"` with `aria-valuenow/min/max` and a label; the evidence wall is `role="img"` whose `aria-label` narrates the full figure (count, worst, best, target) for non-visual reading.
- **Sortable/filterable data**: table headers carry `aria-sort` (ascending/descending/none); sort controls are real buttons inside the headers; category filter chips use `aria-pressed`; the tab row is a `nav` with `aria-label="Data domains"`.
- **Labeled controls**: every icon-only control (✕ dismiss/remove buttons) has an `aria-label`; the pending-backend DELETE button explains its actual behavior in both `aria-label` and `title` ("copies the path; deletion is manual for now").
- **Contrast**: teal text on paper always uses the darker `--teal-ink` (`#0b5952`); secondary text is `--ink-dim` (`#5b6470`); accent hues (amber `#b45309`, red `#b3382c`) are deep enough for text use on paper.
- **Motion**: the single animation (leader-draw, 240ms) is wrapped in `prefers-reduced-motion: no-preference` and degrades to an appearance with no transform.
