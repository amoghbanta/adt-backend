"""
Configuration management for ADT Press pipeline.

This module handles loading and merging of configuration files, providing
a bridge between the ADT Press pipeline's YAML configuration and the API's
runtime needs.

Key responsibilities:
- Locating the config.yaml file from adt-press package
- Loading and caching default configuration
- Merging user overrides with defaults
- Providing configuration metadata for API clients

Configuration Strategy:
    The module searches for config.yaml in multiple locations to support
    both development (local repo checkout) and production (installed package)
    scenarios.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from omegaconf import DictConfig, OmegaConf

from .models import ConfigMetadata

# Find the current module's location for relative path resolution
_HERE = Path(__file__).resolve()

# Search up to 5 parent directories for config/config.yaml
# This supports local development with the repo checked out alongside this backend
_CANDIDATE_CONFIG_PATHS = [parent / "config/config.yaml" for parent in _HERE.parents[:5]]

# Also try to find config from installed adt_press package
try:
    import adt_press  # type: ignore

    _CANDIDATE_CONFIG_PATHS.append(Path(adt_press.__file__).resolve().parents[1] / "config/config.yaml")
except ModuleNotFoundError:
    # adt_press not installed as package; rely on local development paths
    pass

# Determine the actual config path at module load time
CONFIG_PATH = next((path for path in _CANDIDATE_CONFIG_PATHS if path.exists()), None)
if CONFIG_PATH is None:
    raise FileNotFoundError(
        "Default config.yaml could not be located. "
        "Ensure adt-press is installed as a package or checked out alongside adt-backend."
    )

# Available processing strategies for each pipeline stage
# These define the valid values for strategy configuration options
STRATEGY_OPTIONS: Dict[str, List[str]] = {
    "crop_strategy": ["llm", "none"],
    "glossary_strategy": ["llm", "none"],
    "explanation_strategy": ["llm", "none"],
    "easy_read_strategy": ["llm", "none"],
    "caption_strategy": ["llm", "none"],
    "speech_strategy": ["tts", "none"],
}

# Configuration keys that accept boolean values
BOOLEAN_FLAGS = ["clear_cache", "print_available_models"]

# Human-readable documentation for specific configuration keys
NOTES = {
    "label": "Used to namespace the run_output_dir; we append a job suffix to keep runs unique.",
    "pdf_path": "Injected automatically from the uploaded file.",
    "page_range": "Inclusive start/end; leave zeros to process the full document.",
    "regenerate_sections": "List of section IDs to regenerate from scratch (e.g., ['sec_page_5_s0']). Used via /jobs/{job_id}/regenerate endpoint.",
    "edit_sections": "Dict mapping section IDs to edit instructions (e.g., {'sec_page_5_s0': 'make title bigger'}). Used via /jobs/{job_id}/regenerate endpoint.",
}


@lru_cache(maxsize=1)
def _load_default_config() -> DictConfig:
    """
    Load and cache the default configuration from config.yaml.

    This function is cached to avoid repeated file I/O operations.
    The configuration is loaded once and reused across all requests.

    Returns:
        OmegaConf DictConfig object with hierarchical configuration

    Raises:
        FileNotFoundError: If config.yaml doesn't exist at the expected path

    Note:
        The cache is process-local. Configuration changes require a server restart.
    """
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Default config not found at {CONFIG_PATH}")
    return OmegaConf.load(CONFIG_PATH)


def get_default_config_container(resolve: bool = False) -> Dict[str, Any]:
    """
    Get the default configuration as a standard Python dictionary.

    Args:
        resolve: If True, resolve all variable interpolations in the config.
                If False, keep interpolations as-is (e.g., "${other_key}").

    Returns:
        Dictionary representation of the configuration

    Note:
        Setting resolve=True expands all OmegaConf variable references,
        which is useful for providing complete resolved values to clients.
    """
    config = _load_default_config()
    return OmegaConf.to_container(config, resolve=resolve, enum_to_str=True)  # type: ignore[return-value]


def build_config_metadata() -> ConfigMetadata:
    """
    Build configuration metadata for API clients.

    This function aggregates all configuration-related information into
    a single structured response, allowing clients to:
    - Display default values
    - Build configuration UIs with valid options
    - Show documentation for configuration keys

    Returns:
        ConfigMetadata object with defaults, strategies, and documentation

    Note:
        "dynamic" is always included as a render strategy even if not
        explicitly defined in config.yaml, as it's a built-in option.
    """
    defaults = get_default_config_container(resolve=False)
    # Include "dynamic" as a built-in render strategy option
    render_strategies = sorted(set(["dynamic", *list(defaults.get("render_strategies", {}).keys())]))
    layout_types = defaults.get("layout_types", {})

    return ConfigMetadata(
        defaults=defaults,
        strategies=STRATEGY_OPTIONS,
        render_strategies=render_strategies,
        layout_types=layout_types,
        boolean_flags=BOOLEAN_FLAGS,
        notes=NOTES,
    )


def make_runtime_config(overrides: Dict[str, Any]) -> DictConfig:
    """
    Create a runtime configuration by merging user overrides with defaults.

    This function:
    1. Loads the base configuration
    2. Creates a structured config (prevents unknown keys)
    3. Merges user-provided overrides
    4. Returns the final configuration for pipeline execution

    Args:
        overrides: User-provided configuration values to override defaults

    Returns:
        Merged DictConfig ready for pipeline execution

    Raises:
        ConfigAttributeError: If overrides contain keys not in the base config
                             (due to struct mode being enabled)

    Note:
        Setting struct=True provides protection against typos in configuration
        keys by raising errors for undefined parameters.
    """
    # Load base configuration and convert to container for manipulation
    base_container = OmegaConf.to_container(_load_default_config(), resolve=False)
    base = OmegaConf.create(base_container)

    # Enable struct mode to prevent unknown configuration keys
    OmegaConf.set_struct(base, True)

    # Create config from user overrides and merge with base
    cli_config = OmegaConf.create(overrides)
    merged = DictConfig(OmegaConf.merge(base, cli_config))
    return merged
