## What changed

<!-- One or two sentences. The "why" matters more than the "what" — the diff shows the what. -->

## Why

<!-- The motivation. A linked issue, a fixture that was failing, a downstream user request. -->

## Checklist

- [ ] `pytest tests/` is green locally (or xfails are intentional, with reasons in the YAML)
- [ ] `ruff check .` and `ruff format --check .` pass (pre-commit handles this for you)
- [ ] New behavior has a test (unit or fixture)
- [ ] User-visible changes are noted in [CHANGELOG.md](https://github.com/pebbleroad/table-stitcher/blob/main/CHANGELOG.md)
- [ ] No language-specific or vocabulary assumptions added — merge signals stay structural
