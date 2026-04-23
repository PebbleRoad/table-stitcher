# Test suite overview

Two layers, split by what they actually assert.

## Layers

### Unit tests — `tests/test_merger.py`, `tests/test_docling_adapter.py`

Fast (<1 s total). Feed the merger or adapter a hand-crafted input and check
the output. No real PDFs, no parser.

- `test_merger.py` — builds synthetic `TableMeta` objects in memory, exercises
  each merge-decision branch (spillover, headerless width-match, repeated
  header, orphan handling, page-gap guard, non-contiguous extraction guard)
  and verifies `stitch_split_cells` folds continuation rows correctly.
- `test_docling_adapter.py` — exercises the adapter in both directions:
  `_grid_to_dataframe` header-detection heuristics on stub docling tables,
  `_dataframe_to_docling_data` injection (incl. multi-row headers), and the
  `DoclingAdapter.inject()` flow (satellite pruning, provenance merging,
  pass-through on no-op).

### Integration tests — `tests/integration/test_fixtures.py`

Slow (~3 min). For every fixture under `tests/integration/fixtures/<category>/`,
the harness converts the PDF through `docling.DocumentConverter`, runs the
real merger, and compares the resulting `LogicalTable` list to the sibling
`expected.yaml`.

Categories map 1:1 to merger signals (e.g. `repeated-header/`,
`headerless-continuation/`, `width-drift/`, `spillover/`,
`page-gap-too-large/`). An empty category folder is a coverage gap.

## Running

```bash
# Unit tests only (fast iteration)
pytest tests/test_merger.py tests/test_docling_adapter.py

# Everything, including integration
pytest tests/

# One integration case
pytest tests/integration/ -k "study-sample"

# Verbose with full per-case outcomes
pytest tests/integration/ -v
```

## Timings

- Unit suite: under 1 s
- Integration suite: ~3 min after first run; add ~2 min on first run while
  docling downloads its layout / table-structure / OCR models into
  `~/.cache/huggingface/`. CI jobs should cache that directory.
- Per-fixture integration runtime is dominated by OCR; multi-page fixtures
  take 10–30 s each.

## Fixture layout

```
tests/integration/fixtures/
├── <category>/
│   ├── <slug>.<provenance>.pdf          # the fixture
│   └── <slug>.<provenance>.expected.yaml  # what the merger should produce
├── _synth/generate.py                   # reproducible source for .synth.pdf fixtures
└── _tools/regenerate_expected.py        # re-capture expected.yaml after merger changes
```

Provenance tags in current use: `.corp` (corporate/private document),
`.pt2` (PubTables-v2 test split), `.synth` (hand-built via reportlab).

## Adding or updating a fixture

See [`CONTRIBUTING.md`](../CONTRIBUTING.md) at the repo root for the
step-by-step workflow.

## What the integration harness asserts

Per logical table:

- `members: [...]` — which fragment indices merged into one group
- `pages: [...]` — which pages the merged table spans
- `shape: [rows, cols]` — final DataFrame shape
- `columns: [...]` — header row of the merged DataFrame
- `first_row: [...]` — first data row
- `last_row: [...]` — last data row

Interior cells are intentionally NOT asserted; first-row + last-row + shape +
columns is a structural sentinel that catches merge-decision regressions and
boundary drift without flapping on interior OCR noise. An `xfail:` field
at the top of an expected.yaml marks a known-broken case as a tripwire — it
flips to a test failure once the underlying bug is fixed, forcing the fixer
to update the fixture.

## OCR determinism

Docling picks an OCR backend at runtime based on the host (`ocrmac` on Apple
Silicon, `easyocr` / `tesserocr` / `rapidocr` otherwise). Interior cell text
can differ very slightly across backends; the `first_row` / `last_row`
assertions have been stable in practice but may occasionally flap on OCR
upgrades. If you see a flap, re-run `regenerate_expected.py` and verify the
diff is cosmetic before accepting.
