"""Konfigurace nástroje – načítání z config.json a proměnných prostředí."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.json"


@dataclass
class Config:
    # Adresa Moonrakeru běžícího na Pi 4 (Klipper host)
    moonraker_host: str = "http://192.168.1.30:7125"
    # API klíč, pokud má Moonraker zapnuté require_auth
    api_key: Optional[str] = None
    # Název souboru v "config" rootu Moonrakeru, do kterého se makra ukládají
    macros_filename: str = "macros.cfg"
    # Bezpečnostní stropy použité při validaci maker (v °C)
    max_extruder_temp: int = 280
    max_bed_temp: int = 120
    # Timeout HTTP požadavků v sekundách
    request_timeout: float = 10.0

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Config":
        path = path or DEFAULT_CONFIG_PATH
        data: dict = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))

        # Proměnné prostředí mají přednost před souborem, pokud jsou nastaveny
        if os.environ.get("MOONRAKER_HOST"):
            data["moonraker_host"] = os.environ["MOONRAKER_HOST"]
        if os.environ.get("MOONRAKER_API_KEY"):
            data["api_key"] = os.environ["MOONRAKER_API_KEY"]

        valid_keys = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in valid_keys})
