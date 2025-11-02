# ADT Press Backend

FastAPI service that uploads PDFs, configures ADT Press pipeline jobs, and exposes generated artefacts.

## Getting Started

```bash
# install dependencies (assuming adt-press is published or installed from source)
uv pip install -e path/to/adt-press  # or pip install adt-press
uv pip install -e .

# run the API
uv run uvicorn adt_press_backend.main:app --reload --host 0.0.0.0 --port 8000
```

The service exposes the same REST surface as documented in the main project (`GET /config/defaults`, `POST /jobs`, etc.).

## Local Development

- Requires Python 3.10+
- Depends on the `adt-press` package for the core pipeline; install it from PyPI or via `pip install -e ../adt-press` when working in a mono-repo checkout.
- See `docs/adt_productization_overview.md` in the main project for endpoint semantics and background job behaviour.

