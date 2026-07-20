"""Definice makra, renderování Jinja2 šablony a základní bezpečnostní kontroly.

Poznámka: kontroly v `validate()` jsou heuristické (regulární výrazy), ne
plnohodnotný G-code parser. Cílem je odchytit nejčastější chyby (překlep
v teplotě, prázdné makro, nekonečná rekurze), ne nahradit test na reálné
tiskárně.
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import jinja2

MACRO_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# M104/M109 = teplota trysky, M140/M190 = teplota podložky
_EXTRUDER_TEMP_RE = re.compile(r"\bM10[49]\b[^\n]*?\bS\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
_BED_TEMP_RE = re.compile(r"\bM1[49]0\b[^\n]*?\bS\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
_SET_HEATER_RE = re.compile(
    r"SET_HEATER_TEMPERATURE\s+HEATER=(\S+)[^\n]*?TARGET=\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE
)
_DANGEROUS_COMMANDS = ("M112",)  # jen upozornění, ne blokace


@dataclass
class ValidationIssue:
    level: str  # "error" nebo "warning"
    message: str


class _ParamsProxy(dict):
    """Simuluje objekt `params` dostupný v Klipper makrech (params.NAZEV).

    Chybějící parametr vrací Jinja2 `Undefined` objekt (ne výjimku) – díky
    tomu funguje běžný a doporučený zápis `params.X|default(5)`, stejně jako
    ve skutečném Klipperu. Chyba se vyvolá až při skutečném použití hodnoty
    bez výchozí hodnoty (např. `{{ params.X }}` samotné).
    """

    def __getattr__(self, item: str):
        if item in self:
            return self[item]
        return jinja2.Undefined(name=f"params.{item}")


@dataclass
class Macro:
    name: str
    description: str = ""
    gcode_template: str = ""  # tělo makra (Jinja2 šablona), BEZ hlavičky [gcode_macro]
    params: dict[str, Any] = field(default_factory=dict)  # výchozí testovací hodnoty parametrů
    category: str = "obecné"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    deployed: bool = False

    # ---------------------------------------------------------------- render
    def render(self, test_params: Optional[dict[str, Any]] = None) -> str:
        """Vyrenderuje šablonu s testovacími parametry (jako by ji volal Klipper).

        Klipper nepoužívá standardní Jinja2 zápis `{{ promenna }}`, ale
        jednoduché složené závorky `{ promenna }` (bloky `{% %}` zůstávají
        stejné) – prostředí je proto nastavené se stejnými oddělovači, jaké
        používá samotný Klipper (viz klippy/extras/gcode_macro.py).
        """
        env = jinja2.Environment(
            variable_start_string="{",
            variable_end_string="}",
            block_start_string="{%",
            block_end_string="%}",
            undefined=jinja2.StrictUndefined,
        )
        values = dict(self.params)
        if test_params:
            values.update(test_params)
        template = env.from_string(self.gcode_template)
        return template.render(params=_ParamsProxy({k: str(v) for k, v in values.items()}))

    # ------------------------------------------------------------- validate
    def validate(self, max_extruder_temp: int = 280, max_bed_temp: int = 120) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        if not MACRO_NAME_RE.match(self.name):
            issues.append(ValidationIssue(
                "error",
                "Název makra musí být velkými písmeny/čísly/podtržítky a začínat písmenem "
                "(např. HEAT_SOAK, PRIME_LINE).",
            ))

        if not self.gcode_template.strip():
            issues.append(ValidationIssue("error", "Tělo makra (gcode) je prázdné."))
            return issues

        # 1) syntaxe a chybějící parametry
        try:
            rendered = self.render()
        except jinja2.exceptions.TemplateSyntaxError as exc:
            issues.append(ValidationIssue("error", f"Chyba syntaxe šablony: {exc}"))
            return issues
        except jinja2.exceptions.UndefinedError as exc:
            issues.append(ValidationIssue(
                "warning",
                f"{exc} – bez výchozí testovací hodnoty nejde makro plně zrenderovat; "
                "doplň ji v 'new'/'edit', nebo ji zadej při 'test --param'.",
            ))
            rendered = self.gcode_template  # aspoň statická kontrola nad původním textem

        # 2) bezpečnostní kontrola teplot
        for m in _EXTRUDER_TEMP_RE.finditer(rendered):
            temp = float(m.group(1))
            if temp > max_extruder_temp:
                issues.append(ValidationIssue(
                    "error", f"Teplota trysky {temp:.0f} °C překračuje bezpečný limit {max_extruder_temp} °C."
                ))
        for m in _BED_TEMP_RE.finditer(rendered):
            temp = float(m.group(1))
            if temp > max_bed_temp:
                issues.append(ValidationIssue(
                    "error", f"Teplota podložky {temp:.0f} °C překračuje bezpečný limit {max_bed_temp} °C."
                ))
        for heater, temp_str in _SET_HEATER_RE.findall(rendered):
            temp = float(temp_str)
            limit = max_bed_temp if "bed" in heater.lower() else max_extruder_temp
            if temp > limit:
                issues.append(ValidationIssue(
                    "error", f"SET_HEATER_TEMPERATURE HEATER={heater} na {temp:.0f} °C překračuje limit {limit} °C."
                ))

        # 3) rizikové příkazy – jen upozornění
        for cmd in _DANGEROUS_COMMANDS:
            if re.search(rf"\b{cmd}\b", rendered, re.IGNORECASE):
                issues.append(ValidationIssue("warning", f"Makro obsahuje příkaz {cmd} – zkontroluj, že je to záměr."))

        # 4) makro volající samo sebe (riziko nekonečné rekurze)
        if re.search(rf"\b{re.escape(self.name)}\b", self.gcode_template):
            issues.append(ValidationIssue("warning", "Tělo makra volá samo sebe – hrozí nekonečná rekurze."))

        return issues

    # ------------------------------------------------------------- to_cfg
    def to_cfg_block(self) -> str:
        """Vrátí text sekce pro macros.cfg ve formátu, který Klipper očekává."""
        lines = [f"[gcode_macro {self.name}]"]
        if self.description:
            lines.append(f"description: {self.description}")
        lines.append("gcode:")
        body_lines = [ln for ln in self.gcode_template.splitlines() if ln.strip() != ""]
        for ln in body_lines or [""]:
            lines.append(f"    {ln}")
        return "\n".join(lines) + "\n\n"  # prázdný řádek na konci odděluje sekce v macros.cfg

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Macro":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})
