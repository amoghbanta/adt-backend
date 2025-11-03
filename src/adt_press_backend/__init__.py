"""
ADT Press Backend - REST API for ADT Press Pipeline

This package provides a FastAPI-based web service that orchestrates the
ADT (Accessible Document Transformation) Press pipeline. It enables:

- PDF document uploads and validation
- Asynchronous pipeline job execution
- Job status tracking and event logging
- Configuration management with defaults and overrides
- Plate file editing for iterative document refinement

The backend is designed to be a thin orchestration layer that does not
modify the core ADT Press pipeline, maintaining separation of concerns
between the API and document processing logic.

Key Components:
    - main: FastAPI application and HTTP endpoint definitions
    - job_manager: Job lifecycle and pipeline execution coordinator
    - models: Pydantic models for request/response validation
    - configuration: Config loading and merging logic
    - utils: Filesystem and string utilities

Usage:
    Run the API server with:
        uvicorn adt_press_backend.main:app --reload --host 0.0.0.0 --port 8000

    Or use the development script:
        uv run uvicorn adt_press_backend.main:app --reload

Architecture Principles:
    - No modifications to the ADT Press pipeline core
    - Thread-safe job state management
    - Async-first API design for high concurrency
    - Configuration transparency and reproducibility
    - RESTful API conventions for client integration
"""
