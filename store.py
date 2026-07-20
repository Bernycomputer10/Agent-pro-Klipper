"""Lokální úložiště definovaných maker (jednoduchý JSON soubor).

Slouží jako "pracovní verze" maker před nasazením na tiskárnu – dá se v ní
makro upravovat a opakovaně testovat, aniž by se cokoliv posílalo na Klipper,
dokud uživatel explicitně nespustí 'deploy'.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from macro import Macro

DEFAULT_STORE_PATH = Path(__file__).parent / "macros_store.json"


class MacroStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or DEFAULT_STORE_PATH
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")

    def list(self) -> list[str]:
        return sorted(self._data.keys())

    def get(self, name: str) -> Macro:
        try:
            return Macro.from_dict(self._data[name])
        except KeyError:
            raise KeyError(f"Makro '{name}' není v lokálním úložišti. Zkus 'list'.") from None

    def save(self, macro: Macro) -> None:
        self._data[macro.name] = macro.to_dict()
        self._save()

    def delete(self, name: str) -> None:
        self._data.pop(name, None)
        self._save()

    def __contains__(self, name: str) -> bool:
        return name in self._data
