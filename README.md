# ADT Press Backend

FastAPI service that uploads PDFs, configures ADT Press pipeline jobs, and exposes generated artefacts.

## Getting Started

```bash
# install dependencies (the pyproject points uv at ../adt-press by default)
uv sync

# run the API
uv run uvicorn adt_press_backend.main:app --reload --host 0.0.0.0 --port 8000
```

The service exposes the same REST surface as documented in the main project (`GET /config/defaults`, `POST /jobs`, etc.).

## Local Development

- Requires Python 3.13+
- Depends on the `adt-press` package for the core pipeline; once it is published remove the `[tool.uv.sources]` override or point it at a release.
- See `docs/adt_productization_overview.md` in the main project for endpoint semantics and background job behaviour.
