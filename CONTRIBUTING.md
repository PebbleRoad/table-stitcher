# Contributing to table-stitcher

Thanks for the interest. This document covers the dev setup and the fixture
workflow — the thing most contributions touch.

## Dev setup

```bash
git clone https://github.com/pebbleroad/table-stitcher.git
cd table-stitcher
python -m venv .venv && source .venv/bin/activate

pip install -e ".[dev]"     # 1. install the package + dev tools (ruff, pytest, build, twine, pre-commit)
pre-commit install          # 2. enable auto-lint + auto-format on every commit
pytest tests/               # 3. run the unit suite to confirm setup
```

The pre-commit hook calls the ruff installed by step 1 — local and CI
share a single ruff version, so a green pre-commit run means a green CI
lint job.

First `pytest tests/` downloads docling's models (~2 min, goes to
`~/.cache/huggingface/`). Subsequent runs are ~3 min for the full suite.

## Code style and linting

We use [ruff](https://docs.astral.sh/ruff/) for both linting and formatting.
Configuration lives in [`pyproject.toml`](pyproject.toml) under `[tool.ruff]`.

The same checks run locally (via `pre-commit`), in CI (`lint` job), and as a
release gate. A green local commit means a green CI lint job — no surprises.

```bash
ruff check .            # lint — flags real bugs (unused imports, bugbear patterns)
ruff format .           # auto-format — opinionated, no debate
ruff format --check .   # CI mode — fails if anything would be reformatted
```

If you skipped `pre-commit install` and a CI lint failure surprises you,
`ruff check --fix . && ruff format .` will resolve almost all of them.

## Project layout

```
src/table_stitcher/
├── models.py              # MultiPageConfig, TableMeta, LogicalTable
├── merger.py              # parser-agnostic merge engine
└── adapters/
    ├── README.md          # adapter protocol + per-adapter notes
    └── docling.py         # DoclingDocument ↔ TableMeta
tests/
├── test_merger.py         # unit: merger logic on synthetic metadata
├── test_docling_adapter.py  # unit: adapter extract + inject
├── README.md              # test-suite layout and timings
└── integration/
    ├── test_fixtures.py   # auto-discovers .expected.yaml, runs pipeline
    └── fixtures/
        ├── <category>/    # one folder per merge-signal category
        ├── _synth/        # reproducible synthetic-fixture generator
        └── _tools/        # regeneration utilities
```

## Adding a new fixture

Fixtures live under `tests/integration/fixtures/<category>/`. Each case is
a pair — the input PDF and an `expected.yaml` describing what the merger
should produce.

### Naming convention

```
<slug>.<provenance>.pdf
<slug>.<provenance>.expected.yaml
```

- `<slug>` — kebab-case, describes what's distinctive (`url-overflow`,
  `study-sample-7pg`, `varicose-veins-new-table-header-7pg`)
- `<provenance>` — source tag:
  - `.corp` — corporate / private document we have rights to distribute
  - `.pt2` — PubTables-v2 test-split source, bundled from page images
  - `.synth` — hand-built via `_synth/generate.py`
  - (add a new tag when you introduce a new public dataset source)

### Step-by-step

1. **Drop the PDF** into the right category folder. Pick the folder by which
   merge rule the fixture primarily exercises (`repeated-header/`,
   `headerless-continuation/`, `width-drift/`, `orphan-pair/`, …).

2. **Generate `expected.yaml`** from the current pipeline:

   ```bash
   python -m tests.integration._tools.regenerate_expected \
       tests/integration/fixtures/<category>/<slug>.<provenance>.pdf \
       --description "One-paragraph description of what makes this fixture interesting."
   ```

   The tool runs docling + the merger and captures the resulting
   `LogicalTable` list into the sibling YAML.

3. **Eyeball the output.** Open the generated YAML — does the merge outcome
   match what the PDF visually shows? Specifically check:
   - Did the fragments you expected to merge actually land in the same
     `members` list?
   - Are the `columns` reasonable, or does it look like a header-detection
     miss?
   - Does `first_row` / `last_row` match the visible first and last data
     rows of the merged table?

4. **If the outcome is wrong, decide whether the fixture is exercising a
   known bug.** If so, re-run with an `--xfail` reason:

   ```bash
   python -m tests.integration._tools.regenerate_expected \
       <pdf> \
       --xfail "Known missed-merge: ...root-cause sketch..."
   ```

   This writes a minimal structural expectation (no shape / first-row
   assertions, since those would pass on the buggy output and lock us in)
   and marks the test `xfail(strict=True)`. When the bug is fixed, the
   test will XPASS-fail, forcing the fixer to regenerate the YAML with
   `--clear-xfail`.

5. **Run the suite** to confirm the new fixture passes (or xfails as
   intended):

   ```bash
   pytest tests/integration/ -k "<slug>"
   ```

6. **Commit** the PDF + YAML together. The auto-discovery in
   `test_fixtures.py` picks it up; no code edits needed.

## Regenerating an existing fixture after a merger change

If you change merger behavior and the old `first_row` / `last_row`
assertions no longer match, regenerate:

```bash
python -m tests.integration._tools.regenerate_expected \
    tests/integration/fixtures/<category>/<slug>.<provenance>.pdf
```

Diff the YAML. If the change is *intentional* (the merger is now producing
a better merge), commit the updated YAML. If it's *unintentional*
regression, revert the merger change.

## Synthetic fixtures

`tests/integration/fixtures/_synth/generate.py` builds the `.synth.pdf`
fixtures from reportlab primitives. Regenerate them after edits:

```bash
python -m tests.integration.fixtures._synth.generate
```

Each builder is one function; add a new synthetic fixture by adding a
builder and a `(builder, out_path)` entry in `JOBS`.

## Tuning MultiPageConfig

The library's merge decisions are driven entirely by `MultiPageConfig`
(see [`src/table_stitcher/models.py`](src/table_stitcher/models.py)). If
you need different thresholds for a specific fixture, set them under
`config:` in that fixture's `expected.yaml`:

```yaml
config:
  max_page_gap: 2
  headerless_width_tolerance: 3
```

The harness passes those through to `MultiPageConfig(**config)`. Prefer
per-fixture config over changing the defaults — defaults are calibrated
against the current corpus.

## Before opening a PR

- [ ] `pytest tests/` is green (or xfails are intentional, with reasons in the YAML)
- [ ] `ruff check .` and `ruff format --check .` pass (pre-commit handles this for you)
- [ ] New fixtures have descriptions and follow the naming convention
- [ ] Merger / adapter changes come with a unit test
- [ ] User-visible changes have a [`CHANGELOG.md`](CHANGELOG.md) entry
- [ ] No vocabulary or language-specific assumptions added — keep signals structural
