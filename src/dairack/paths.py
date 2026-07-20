"""Filesystem ownership and XDG path discovery."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .identity import APP_NAME, LEGACY_APP_NAME, env_value

LEGACY_DATA_EXCLUSIONS = {
    "runtime-venv",
    "vendor",
    "tests",
    "asusai_textual.py",
    "dairack_textual.py",
    "__pycache__",
}


def _xdg_path(variable: str, fallback: Path) -> Path:
    value = os.environ.get(variable, "").strip()
    candidate = Path(value).expanduser() if value else fallback
    return candidate.resolve() if candidate.is_absolute() else fallback.resolve()


def _is_windows() -> bool:
    return os.name == "nt"


@dataclass(frozen=True, slots=True)
class AppPaths:
    config_dir: Path
    data_dir: Path
    cache_dir: Path
    state_dir: Path

    @classmethod
    def discover(cls, app_name: str = APP_NAME) -> "AppPaths":
        portable = env_value("HOME").strip() if app_name == APP_NAME else ""
        if portable:
            root = Path(portable).expanduser().resolve()
            return cls(
                config_dir=root / "config",
                data_dir=root / "data",
                cache_dir=root / "cache",
                state_dir=root / "state",
            )
        home = Path.home()
        if _is_windows():
            roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
            local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
            root = local / "Dairack"
            return cls(
                config_dir=roaming / "Dairack",
                data_dir=root / "Data",
                cache_dir=root / "Cache",
                state_dir=root / "State",
            )
        return cls(
            config_dir=_xdg_path("XDG_CONFIG_HOME", home / ".config") / app_name,
            data_dir=_xdg_path("XDG_DATA_HOME", home / ".local" / "share") / app_name,
            cache_dir=_xdg_path("XDG_CACHE_HOME", home / ".cache") / app_name,
            state_dir=_xdg_path("XDG_STATE_HOME", home / ".local" / "state") / app_name,
        )

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.json"

    @property
    def model_registry_file(self) -> Path:
        return self.config_dir / "models.json"

    @property
    def hardware_file(self) -> Path:
        return self.config_dir / "hardware.json"

    @property
    def compute_credentials_file(self) -> Path:
        return self.config_dir / "compute-credentials.json"

    @property
    def compute_bridge_token_file(self) -> Path:
        return self.config_dir / "compute-bridge.token"

    @property
    def history_file(self) -> Path:
        return self.data_dir / "history"

    @property
    def chats_dir(self) -> Path:
        return self.data_dir / "chats"

    @property
    def index_file(self) -> Path:
        return self.data_dir / "project-index.sqlite3"

    @property
    def checkpoints_dir(self) -> Path:
        return self.data_dir / "checkpoints"

    @property
    def update_cache_file(self) -> Path:
        return self.state_dir / "update-check.json"

    def ensure(self) -> None:
        for directory in (self.config_dir, self.data_dir, self.cache_dir, self.state_dir):
            directory.mkdir(parents=True, exist_ok=True)
            try:
                directory.chmod(0o700)
            except OSError:
                pass

    def as_dict(self) -> dict[str, str]:
        return {
            "config": str(self.config_dir),
            "data": str(self.data_dir),
            "cache": str(self.cache_dir),
            "state": str(self.state_dir),
        }


@dataclass(frozen=True, slots=True)
class StateMigration:
    moved: tuple[Path, ...] = ()
    conflicts: tuple[Path, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.moved)


def _legacy_standard_paths() -> AppPaths:
    home = Path.home()
    if _is_windows():
        roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        root = local / "AsusAI"
        return AppPaths(roaming / "AsusAI", root / "Data", root / "Cache", root / "State")
    return AppPaths(
        _xdg_path("XDG_CONFIG_HOME", home / ".config") / LEGACY_APP_NAME,
        _xdg_path("XDG_DATA_HOME", home / ".local" / "share") / LEGACY_APP_NAME,
        _xdg_path("XDG_CACHE_HOME", home / ".cache") / LEGACY_APP_NAME,
        _xdg_path("XDG_STATE_HOME", home / ".local" / "state") / LEGACY_APP_NAME,
    )


def _merge_legacy_tree(
    source: Path,
    destination: Path,
    *,
    excluded: set[str] | None = None,
) -> StateMigration:
    if not source.exists() or source.resolve() == destination.resolve():
        return StateMigration()
    moved: list[Path] = []
    conflicts: list[Path] = []
    errors: list[str] = []
    excluded = excluded or set()

    def merge(source_dir: Path, destination_dir: Path, *, top_level: bool = False) -> None:
        try:
            children = tuple(source_dir.iterdir())
        except OSError as exc:
            errors.append(f"{source_dir}: {exc}")
            return
        for child in children:
            if top_level and child.name in excluded:
                continue
            target = destination_dir / child.name
            try:
                if child.is_dir() and not child.is_symlink():
                    if target.exists() and not target.is_dir():
                        conflicts.append(target)
                        continue
                    target.mkdir(parents=True, exist_ok=True)
                    merge(child, target)
                    try:
                        child.rmdir()
                    except OSError:
                        pass
                elif target.exists() or target.is_symlink():
                    conflicts.append(target)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(child), str(target))
                    moved.append(target)
            except OSError as exc:
                errors.append(f"{child}: {exc}")

    merge(source, destination, top_level=True)
    try:
        source.rmdir()
    except OSError:
        pass
    return StateMigration(tuple(moved), tuple(conflicts), tuple(errors))


def migrate_legacy_state(
    paths: AppPaths | None = None,
    *,
    legacy_paths: AppPaths | None = None,
) -> StateMigration:
    """Import pre-rename user state once, without overwriting canonical files."""

    paths = paths or AppPaths.discover()
    if env_value("HOME").strip():
        return StateMigration()
    legacy = legacy_paths or _legacy_standard_paths()
    reports = (
        _merge_legacy_tree(legacy.config_dir, paths.config_dir),
        _merge_legacy_tree(legacy.data_dir, paths.data_dir, excluded=LEGACY_DATA_EXCLUSIONS),
        _merge_legacy_tree(legacy.cache_dir, paths.cache_dir),
        _merge_legacy_tree(legacy.state_dir, paths.state_dir),
    )
    return StateMigration(
        moved=tuple(item for report in reports for item in report.moved),
        conflicts=tuple(item for report in reports for item in report.conflicts),
        errors=tuple(item for report in reports for item in report.errors),
    )


PATHS = AppPaths.discover()
