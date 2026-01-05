# ADT Press Backend

> A FastAPI service for orchestrating the ADT (Accessible Document Transformation) Press pipeline

## Overview

ADT Press Backend is a RESTful API service that provides a robust interface for processing PDF documents through the ADT Press pipeline. It handles file uploads, manages asynchronous job execution, and exposes generated outputs through a clean HTTP API.

### Key Features

- **Asynchronous Job Processing**: Upload PDFs and track processing status via REST endpoints
- **Flexible Configuration**: Override pipeline defaults with per-job configuration
- **Concurrent Execution**: Process multiple documents simultaneously with configurable worker pools
- **Output Management**: Direct access to generated files through static file serving
- **Plate Editing**: Edit intermediate layout data for iterative document refinement
- **Event Logging**: Complete audit trail of job lifecycle events

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Service](#running-the-service)
- [API Documentation](#api-documentation)
- [Testing](#testing)
- [Project Structure](#project-structure)
- [Assumptions and Design Decisions](#assumptions-and-design-decisions)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

Before you begin, ensure you have the following installed:

- **Python 3.13+** (required for modern type hints and language features)
- **uv** (recommended) or **pip** for package management
- **ADT Press** package (either installed or checked out locally)

---

## Installation

### 1. Clone the Repository

```bash
git clone <repository-url>
cd adt-backend
```

### 2. Install Dependencies

Using **uv** (recommended):

```bash
uv sync
```

Using **pip**:

```bash
pip install -e .
```

### 3. Verify ADT Press Configuration

The service requires access to the ADT Press `config.yaml` file. By default, it searches for:

- `../adt-press/config/config.yaml` (local development)
- Installed `adt-press` package location

If using a local checkout, ensure the ADT Press repository is in the parent directory:

```
workspace/
├── adt-backend/          # This repository
└── adt-press/           # ADT Press core pipeline
    └── config/
        └── config.yaml
```

---

## Configuration

### Environment Variables

The service can be configured via environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `OUTPUT_DIR` | Base directory for job outputs | `./output` |
| `UPLOAD_DIR` | Base directory for uploaded files | `./uploads` |
| `MAX_WORKERS` | Concurrent job processing limit | `2` |

### Pipeline Configuration

Job-specific configuration can be provided when creating jobs via the `config` parameter. See [API Documentation](#api-documentation) for details.

---

## Running the Service

### Development Mode

Run with auto-reload for development:

```bash
uv run uvicorn adt_press_backend.main:app --reload --host 0.0.0.0 --port 8000
```

Or using the shorthand:

```bash
uvicorn adt_press_backend.main:app --reload
```

### Production Mode

For production deployments, use a production ASGI server:

```bash
# Using Gunicorn with Uvicorn workers
gunicorn adt_press_backend.main:app \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000
```

### Access the Service

Once running, the service will be available at:

- **API**: http://localhost:8000
- **Interactive API Docs**: http://localhost:8000/docs
- **Alternative Docs**: http://localhost:8000/redoc

---

## API Documentation

### Endpoints

#### Health Check

```http
GET /healthz
```

Returns service health status.

#### Configuration Metadata

```http
GET /config/defaults
```

Returns default configuration values, available strategies, and parameter documentation.

#### Job Management

##### Create Job

```http
POST /jobs
Content-Type: multipart/form-data

pdf: <file>
label: "My Document" (optional)
config: '{"crop_strategy": "llm"}' (optional JSON string)
```

Creates a new processing job and returns job summary.

##### List Jobs

```http
GET /jobs
```

Returns all jobs sorted by creation time (newest first).

##### Get Job Details

```http
GET /jobs/{job_id}
```

Returns complete job information including configuration and event history.

##### Get Job Status

```http
GET /jobs/{job_id}/status
```

Returns lightweight status information (status, error, plate_available).

#### Plate Management

##### Get Plate

```http
GET /jobs/{job_id}/plate
```

Retrieves the intermediate plate.json file for editing.

##### Update Plate

```http
PUT /jobs/{job_id}/plate
Content-Type: application/json

{plate data}
```

Updates the plate.json file with user modifications.

#### Output Access

```http
GET /jobs/{job_id}/outputs/{path}
```

Serves individual output files (HTML, images, etc.).

### Example Usage

**Creating a job with cURL**:

```bash
curl -X POST http://localhost:8000/jobs \
  -F "pdf=@document.pdf" \
  -F "label=Test Document" \
  -F 'config={"crop_strategy": "llm", "page_range": [1, 10]}'
```

**Checking job status**:

```bash
curl http://localhost:8000/jobs/{job_id}/status
```

---

## Testing

### Running Tests

```bash
# Install development dependencies
uv sync --extra develop

# Run tests
pytest

# Run with coverage
pytest --cov=adt_press_backend --cov-report=html
```

### Manual Testing

Use the interactive API documentation at `/docs` to manually test endpoints with a user-friendly interface.

---

## Project Structure

```
adt-backend/
├── src/
│   └── adt_press_backend/
│       ├── __init__.py          # Package initialization and documentation
│       ├── main.py              # FastAPI application and endpoints
│       ├── job_manager.py       # Job orchestration and lifecycle management
│       ├── models.py            # Pydantic models for API contracts
│       ├── configuration.py     # Config loading and merging
│       └── utils.py             # Filesystem and string utilities
├── README.md                    # This file
├── pyproject.toml              # Project dependencies and metadata
└── uv.lock                     # Locked dependency versions
```

### Module Responsibilities

- **main.py**: HTTP routing, request validation, and endpoint handlers
- **job_manager.py**: Business logic for job creation, execution, and state management
- **models.py**: Data models for jobs, configuration, and API responses
- **configuration.py**: Configuration file discovery and override merging
- **utils.py**: Shared utilities for filesystem operations and string sanitization

---

## Assumptions and Design Decisions

### Architecture

1. **Separation of Concerns**: The backend is a thin orchestration layer that does not modify the ADT Press pipeline core. This maintains clear boundaries between API logic and document processing.

2. **Asynchronous Processing**: Jobs are executed in background threads to avoid blocking HTTP requests. Clients poll job status rather than using long-polling or websockets for simplicity.

3. **In-Memory State**: Job state is stored in memory (not persisted to a database). This is suitable for development and small-scale deployments but should be replaced with a database for production use.

4. **Filesystem-Based Storage**: Uploads and outputs are stored on the local filesystem. For production, consider using object storage (S3, GCS, etc.).

### Security

1. **CORS**: Currently configured to allow all origins (`*`) for development. **Restrict this in production** to specific frontend domains.

2. **Path Traversal Protection**: File access endpoints validate paths to prevent directory traversal attacks.

3. **File Validation**: Uploaded files are validated to ensure they are PDFs (by extension and content-type).

### Configuration

1. **Config Discovery**: The service searches multiple locations for `config.yaml` to support both local development (sibling repo) and production (installed package) scenarios.

2. **Override Merging**: User-provided configuration overrides are merged with defaults using OmegaConf's structured config mode, which prevents typos and undefined parameters.

3. **Null Handling**: Null values in configuration are removed to use defaults instead.

### Performance

1. **Worker Pool**: Default 2 concurrent workers for job processing. Adjust based on available CPU/memory resources.

2. **Chunked Uploads**: Files are streamed in 8MB chunks to handle large PDFs without excessive memory usage.

3. **Config Caching**: Default configuration is cached after first load to avoid repeated file I/O.

### Error Handling

1. **Structured Errors**: HTTP exceptions include clear error messages for client debugging.

2. **Job Failure Tracking**: Failed jobs are marked with status `FAILED` and error messages are preserved for troubleshooting.

3. **Event Logging**: All job state transitions are logged as events for audit trails.

---

## Troubleshooting

### Config File Not Found

**Error**: `FileNotFoundError: Default config.yaml could not be located`

**Solution**: Ensure the ADT Press repository is checked out as a sibling directory or the `adt-press` package is installed:

```bash
# Option 1: Clone ADT Press as sibling
cd ..
git clone <adt-press-repo-url>

# Option 2: Install adt-press package
pip install adt-press
```

### Import Errors

**Error**: `ModuleNotFoundError: No module named 'adt_press'`

**Solution**: Verify dependencies are installed:

```bash
uv sync
```

### Port Already in Use

**Error**: `[Errno 48] Address already in use`

**Solution**: Change the port or kill the process using port 8000:

```bash
# Use a different port
uvicorn adt_press_backend.main:app --port 8001

# Or find and kill the process
lsof -ti:8000 | xargs kill -9
```

### Jobs Stuck in PENDING

**Issue**: Jobs remain in `pending` status indefinitely

**Solution**: Check that:
1. The ADT Press pipeline is properly installed
2. Worker threads are not exhausted (check `MAX_WORKERS` setting)
3. Check job events for error messages: `GET /jobs/{job_id}`

---

## Contributing

Contributions are welcome! Please ensure:

1. All code follows PEP 8 style guidelines
2. New features include comprehensive docstrings
3. Tests are added for new functionality
4. The README is updated for significant changes

---

## License

See the main ADT Press project for license information.

---

## Support

For issues and questions:

- Check the [Troubleshooting](#troubleshooting) section
- Review the interactive API docs at `/docs`
- Consult `docs/adt_productization_overview.md` in the main ADT Press project

---

**Built with ❤️ for accessible document transformation**
