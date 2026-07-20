"""Tenký klient pro komunikaci s Moonraker HTTP API.

Pokrývá jen to, co nástroj potřebuje: zjištění stavu tiskárny, spuštění
G-code (test i skutečný RESTART) a čtení/zápis souboru v "config" rootu
Moonrakeru (kam patří i printer.cfg a naše macros.cfg).

Odkazy na použité endpointy (Moonraker HTTP API):
  GET  /printer/objects/query   – dotaz na stav objektů (např. print_stats)
  GET  /printer/objects/list    – seznam objektů aktuálně načtených Klipperem
  POST /printer/gcode/script    – spuštění libovolného G-code / makra
  GET  /server/files/config/{name}  – stažení obsahu souboru z config rootu
  POST /server/files/upload     – nahrání (a přepsání) souboru do config/gcodes rootu
"""
from __future__ import annotations

from typing import Optional

import requests


class MoonrakerError(RuntimeError):
    """Chyba vrácená Moonrakerem nebo Klipperem."""


class MoonrakerClient:
    def __init__(self, host: str, api_key: Optional[str] = None, timeout: float = 10.0):
        self.host = host.rstrip("/")
        self.timeout = timeout
        self._headers = {"X-Api-Key": api_key} if api_key else {}

    # ------------------------------------------------------------ interní
    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.host}{path}"
        try:
            resp = requests.request(method, url, headers=self._headers, timeout=self.timeout, **kwargs)
        except requests.exceptions.RequestException as exc:
            raise MoonrakerError(f"Nelze se spojit s Moonrakerem na {self.host}: {exc}") from exc

        try:
            data = resp.json()
        except ValueError:
            resp.raise_for_status()
            return {}

        if resp.status_code >= 400:
            message = (data.get("error") or {}).get("message", resp.text)
            raise MoonrakerError(f"Moonraker vrátil chybu ({resp.status_code}): {message}")
        return data

    # ------------------------------------------------------ stav tiskárny
    def get_object(self, name: str, fields_: Optional[list[str]] = None) -> dict:
        value = ",".join(fields_) if fields_ else ""
        data = self._request("GET", "/printer/objects/query", params={name: value})
        return data.get("result", {}).get("status", {}).get(name, {})

    def printer_state(self) -> str:
        """Stav dle print_stats: standby / printing / paused / complete / error / cancelled."""
        obj = self.get_object("print_stats", ["state"])
        return obj.get("state", "unknown")

    def list_macros(self) -> list[str]:
        data = self._request("GET", "/printer/objects/list")
        objects = data.get("result", {}).get("objects", [])
        prefix = "gcode_macro "
        return [o[len(prefix):] for o in objects if o.startswith(prefix)]

    # -------------------------------------------------------- spouštění gcode
    def run_gcode(self, script: str) -> str:
        data = self._request("POST", "/printer/gcode/script", params={"script": script})
        return data.get("result", "ok")

    def restart_klipper_config(self) -> None:
        """Příkaz RESTART – Klipper znovu načte printer.cfg a všechny [include] soubory
        (na rozdíl od FIRMWARE_RESTART nedochází k restartu MCU firmware)."""
        self.run_gcode("RESTART")

    # -------------------------------------------------------- práce se soubory
    def read_config_file(self, filename: str) -> Optional[str]:
        url = f"{self.host}/server/files/config/{filename}"
        try:
            resp = requests.get(url, headers=self._headers, timeout=self.timeout)
        except requests.exceptions.RequestException as exc:
            raise MoonrakerError(f"Nelze se spojit s Moonrakerem na {self.host}: {exc}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise MoonrakerError(f"Čtení souboru '{filename}' selhalo ({resp.status_code}): {resp.text}")
        return resp.text

    def write_config_file(self, filename: str, content: str) -> None:
        url = f"{self.host}/server/files/upload"
        files = {"file": (filename, content.encode("utf-8"), "text/plain")}
        data = {"root": "config"}
        try:
            resp = requests.post(url, headers=self._headers, files=files, data=data, timeout=self.timeout)
        except requests.exceptions.RequestException as exc:
            raise MoonrakerError(f"Nelze se spojit s Moonrakerem na {self.host}: {exc}") from exc
        if resp.status_code >= 400:
            raise MoonrakerError(f"Nahrání souboru '{filename}' selhalo ({resp.status_code}): {resp.text}")
