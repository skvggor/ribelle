# Project guidance

## Design

Keep business rules in `domain`, orchestration and ports in `application`, external
services and system commands in `infrastructure`, and terminal interaction in
`presentation`. Keep command-line parsing and dependency wiring in `cli`.
Dependencies point inward: adapters implement application ports, while the application
does not import adapters. Add an interface only where a boundary needs to be replaceable.

Use Python and test code in English, descriptive names, and path-safe output names. Avoid
unnecessary comments and dependencies. Use the latest stable version when adding packages.
FLAC output is lossless; do not delete or overwrite existing audio files.

## Testing and quality

Use the red-green-refactor loop for every behavior change: first run a test that fails for
the missing behavior, implement the smallest passing change, then refactor with tests green.
Test application behavior with fakes rather than a physical drive or live network.

Keep every changed module above 80% coverage, including branches. The suite also enforces
an 80% aggregate threshold. Run all of these before finishing:

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv audit --locked
uv run sonar
```

Never commit or push without an explicit request. Do not use untyped `Any` in tests.
