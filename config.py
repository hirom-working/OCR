"""Configuration module for OCR Pipeline.

Loads settings from config.toml and provides structured access to them.
"""
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.toml"
EXAMPLE_CONFIG_PATH = Path(__file__).parent / "config.example.toml"


def _expand_local_path(path_str: str) -> Path:
    """Expand ~ in local paths."""
    return Path(path_str).expanduser()


@dataclass(frozen=True)
class LocalConfig:
    """Local client settings."""
    output_dir: Path
    work_dir: Path


@dataclass(frozen=True)
class OcrServerConfig:
    """OCR Server remote settings.

    Note: Paths are kept as strings since they are remote paths
    and will be expanded on the remote server.
    """
    host: str
    surya_dir: str
    input_dir: str
    output_dir: str


@dataclass(frozen=True)
class PipelineConfig:
    """Batch pipeline directories on the remote host.

    Note: Paths are kept as strings since they are remote paths.
    """
    input_dir: str
    output_dir: str


@dataclass(frozen=True)
class OllamaConfig:
    """Ollama LLM server settings."""
    host: str
    model: str


@dataclass(frozen=True)
class Config:
    """Main configuration object aggregating all sections."""
    local: LocalConfig
    ocr_server: OcrServerConfig
    pipeline: PipelineConfig
    ollama: OllamaConfig


def load_config() -> Config:
    """Load and parse the TOML configuration file.

    If config.toml doesn't exist, copies from config.example.toml.

    Raises:
        FileNotFoundError: If neither config.toml nor config.example.toml exists.
        ValueError: If TOML parsing fails.
    """
    if not CONFIG_PATH.exists():
        if EXAMPLE_CONFIG_PATH.exists():
            shutil.copy(EXAMPLE_CONFIG_PATH, CONFIG_PATH)
            print(f"Created {CONFIG_PATH} from {EXAMPLE_CONFIG_PATH}")
        else:
            raise FileNotFoundError(
                f"Config file {CONFIG_PATH} not found. "
                f"Please create it from {EXAMPLE_CONFIG_PATH}."
            )

    with open(CONFIG_PATH, "rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"Invalid TOML in {CONFIG_PATH}: {e}")

    return _parse_config(data)


def _parse_config(data: dict) -> Config:
    """Parse TOML data and construct the Config object."""
    local_section = data.get("local", {})
    ocr_server_section = data.get("ocr_server", {})
    pipeline_section = data.get("pipeline", {})
    ollama_section = data.get("ollama", {})

    return Config(
        local=LocalConfig(
            output_dir=_expand_local_path(local_section.get("output_dir", "~/電子図書")),
            work_dir=_expand_local_path(local_section.get("work_dir", "~/Projects/OCR/work")),
        ),
        ocr_server=OcrServerConfig(
            host=ocr_server_section.get("host", "pgx02"),
            surya_dir=ocr_server_section.get("surya_dir", "~/surya-ocr"),
            input_dir=ocr_server_section.get("input_dir", "~/ocr_watch_input"),
            output_dir=ocr_server_section.get("output_dir", "~/ocr_watch_output"),
        ),
        pipeline=PipelineConfig(
            input_dir=pipeline_section.get("input_dir", "~/ocr_pipeline_input"),
            output_dir=pipeline_section.get("output_dir", "~/ocr_pipeline_output"),
        ),
        ollama=OllamaConfig(
            host=ollama_section.get("host", "pgx01"),
            model=ollama_section.get("model", "gemma3:27b"),
        ),
    )


# Load config on module import
config = load_config()
