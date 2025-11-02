from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from omegaconf import DictConfig, OmegaConf

from .models import ConfigMetadata

CONFIG_PATH = Path("config/config.yaml")

STRATEGY_OPTIONS: Dict[str, List[str]] = {
    "crop_strategy": ["llm", "none"],
    "glossary_strategy": ["llm", "none"],
    "explanation_strategy": ["llm", "none"],
    "easy_read_strategy": ["llm", "none"],
    "caption_strategy": ["llm", "none"],
    "speech_strategy": ["tts", "none"],
}

BOOLEAN_FLAGS = ["clear_cache", "print_available_models"]

NOTES = {
    "label": "Used to namespace the run_output_dir; we append a job suffix to keep runs unique.",
    "pdf_path": "Injected automatically from the uploaded file.",
    "page_range": "Inclusive start/end; leave zeros to process the full document.",
}


@lru_cache(maxsize=1)
def _load_default_config() -> DictConfig:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Default config not found at {CONFIG_PATH}")
    return OmegaConf.load(CONFIG_PATH)


def get_default_config_container(resolve: bool = False) -> Dict[str, Any]:
    config = _load_default_config()
    return OmegaConf.to_container(config, resolve=resolve, enum_to_str=True)  # type: ignore[return-value]


def build_config_metadata() -> ConfigMetadata:
    defaults = get_default_config_container(resolve=False)
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
    base_container = OmegaConf.to_container(_load_default_config(), resolve=False)
    base = OmegaConf.create(base_container)
    OmegaConf.set_struct(base, True)

    cli_config = OmegaConf.create(overrides)
    merged = DictConfig(OmegaConf.merge(base, cli_config))
    return merged
