# Sigma graph visual QA

## Scope

- Source reference: `artifacts/sigma-design-reference.png`
- Implementation capture: `artifacts/sigma-design-after.png`
- Route: `http://127.0.0.1:5174/`
- Browser: Codex in-app browser
- Desktop viewport: 1440 × 900, device scale factor 1
- Compact laptop viewport: 1024 × 768, device scale factor 1
- Data state: 642 nodes, 1,084 directed relationships, six node categories

## Comparison target

The source is a visual-direction reference rather than a pixel-identical product screen. The comparison therefore focuses on the requested graph treatment: neutral canvas, compact colored nodes, low-emphasis edges, clustered topology, sparse labels, search and legend overlays, lower-corner count and camera controls, and preservation of the surrounding application shell.

## Full-page evidence

- The graph canvas fills its card and resizes from 1,097 px wide at 1440 px to 681 px wide at 1024 px without document-level horizontal overflow.
- Search remains inside the upper-left canvas edge.
- The dynamic six-category legend remains inside the upper-right canvas edge at both tested widths.
- Count summary and camera controls remain anchored to the lower corners.
- The existing schema sidebar, result table, answer box, and query panel continue to use the product's existing layout and tokens.

## Focused component evidence

- Default nodes render as 3.4 px circles with category colors; default edge width is 0.45 px.
- Default edge labels are disabled; node labels are sparse and become explicit for hover, selection, and search focus.
- Search for `Component 2042` produced results, selected the node, moved the camera, and updated the selection count to 1.
- Selecting a search result closes the result menu; editing the query reopens it.
- Product legend filtering changed the visible count from `390 / 390` to `0 / 390`, then restored it.
- Fit restored the full graph after search focus; reset cleared search, selection, hover, and category filters.
- A full page reload followed by a new query rendered the graph again.

## QA history

### Pass 1

- Compared the source and implementation captures together.
- P2: the search-result popover remained open after selecting a result and obscured the graph on the 1024 px viewport.
- Fix: hide the result list after selection and reopen it when the query changes.
- P2: inferred path edges retained the older 1 px style.
- Fix: normalize inferred and explicit edge defaults to the same 0.45 px neutral treatment.

### Pass 2

- Repeated the source/implementation comparison at the default unselected state.
- Rechecked search selection, legend filtering, camera fit/reset, 1440 × 900 and 1024 × 768 layouts, and page reload.
- P0: none.
- P1: none.
- P2: none remaining.

## Automated verification

- `npm run typecheck`: passed
- `npm run lint`: passed
- `npm test`: 3 files, 17 tests passed
- `npm run build`: passed; Sigma chunk 193.73 kB (48.72 kB gzip)
- `npm run format:check`: passed

## Remaining visual limits

- Cluster shape is data-dependent. The QA dataset has many Product leaves, so its type distribution and cluster silhouette differ from the reference dataset.
- The existing application shell intentionally remains visible; this is not a standalone full-screen graph clone.
- Browser console capture is not exposed by the selected in-app browser API. No visible runtime error state or Vite terminal error appeared during interaction QA.

## Final result

Passed. No unresolved P0, P1, or P2 visual issues remain in the tested states.
