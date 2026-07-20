#!/usr/bin/env python3
"""CLI nástroj pro tvorbu, ladění, testování a nasazení Klipper maker.

Typický postup použití:

    python3 cli.py new                      # vytvoří makro (interaktivně)
    python3 cli.py validate NAZEV           # statická kontrola syntaxe/bezpečnosti
    python3 cli.py test NAZEV               # dry-run – jen ukáže vyrenderovaný gcode
    python3 cli.py test NAZEV --live         # skutečně pošle gcode na tiskárnu (test)
    python3 cli.py deploy NAZEV              # nahraje makro do macros.cfg a restartuje Klipper
    python3 cli.py status                    # stav tiskárny + seznam maker načtených v Klipperu

Konfigurace (adresa Moonrakeru, API klíč, teplotní limity) se čte z
config.json vedle tohoto souboru, případně z proměnných prostředí
MOONRAKER_HOST / MOONRAKER_API_KEY, případně přes --host / --api-key.
"""
from __future__ import annotations

import argparse
import re
import sys
from typing import Optional

from config import Config
from macro import Macro, ValidationIssue
from moonraker_client import MoonrakerClient, MoonrakerError
from store import MacroStore


# --------------------------------------------------------------------------- pomocné funkce
def _print_issues(issues: list[ValidationIssue]) -> bool:
    """Vypíše nalezené problémy, vrátí True pokud mezi nimi není žádná chyba ('error')."""
    if not issues:
        print("  ✔ žádné problémy nenalezeny")
        return True
    ok = True
    for issue in issues:
        prefix = "  ✗ CHYBA" if issue.level == "error" else "  ⚠ varování"
        print(f"{prefix}: {issue.message}")
        if issue.level == "error":
            ok = False
    return ok


def _parse_param_overrides(pairs: Optional[list[str]]) -> dict:
    result = {}
    for kv in pairs or []:
        k, _, v = kv.partition("=")
        result[k] = v
    return result


def _confirm(question: str, auto_yes: bool) -> bool:
    if auto_yes:
        return True
    answer = input(f"{question} [ano/NE]: ").strip().lower()
    return answer == "ano"


def _upsert_macro_block(existing_text: str, macro: Macro) -> str:
    """Nahradí (nebo přidá) sekci [gcode_macro NAME] v textu macros.cfg."""
    pattern = re.compile(
        rf"^\[gcode_macro {re.escape(macro.name)}\].*?(?=^\[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    block = macro.to_cfg_block()
    if pattern.search(existing_text):
        return pattern.sub(block, existing_text)
    if not existing_text:
        return f"\n{block}"
    separator = "" if existing_text.endswith("\n") else "\n"
    return f"{existing_text}{separator}{block}"


def _remove_macro_block(existing_text: str, name: str) -> str:
    pattern = re.compile(rf"^\[gcode_macro {re.escape(name)}\].*?(?=^\[|\Z)", re.MULTILINE | re.DOTALL)
    return pattern.sub("", existing_text)


# --------------------------------------------------------------------------- příkazy
def cmd_new(args, store: MacroStore, cfg: Config) -> int:
    name = (args.name or input("Název makra (VELKÝMI_PISMENY): ").strip()).upper()
    if name in store and not args.force:
        print(f"Makro '{name}' už existuje. Přidej --force pro přepsání.")
        return 1

    description = args.description if args.description is not None else input("Popis: ").strip()

    print("Zadej tělo makra řádek po řádku (G-code / Klipper příkazy). Prázdný řádek ukončí zadávání:")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            break
        lines.append(line)
    gcode_template = "\n".join(lines)

    params = _parse_param_overrides(args.param)

    macro = Macro(
        name=name,
        description=description,
        gcode_template=gcode_template,
        params=params,
        category=args.category or "obecné",
    )
    store.save(macro)
    print(f"\nMakro '{name}' uloženo lokálně (zatím NE na tiskárně).")
    print(f"Další krok:  python3 cli.py validate {name}")
    return 0


def cmd_list(args, store: MacroStore, cfg: Config) -> int:
    names = store.list()
    if not names:
        print("Zatím žádná makra v lokálním úložišti. Začni: python3 cli.py new")
        return 0
    for n in names:
        m = store.get(n)
        flag = "nasazeno " if m.deployed else "lokálně  "
        print(f"  {n:<28} [{flag}] {m.description}")
    return 0


def cmd_show(args, store: MacroStore, cfg: Config) -> int:
    macro = store.get(args.name)
    print(macro.to_cfg_block())
    return 0


def cmd_validate(args, store: MacroStore, cfg: Config) -> int:
    macro = store.get(args.name)
    print(f"Validace makra {macro.name}:")
    issues = macro.validate(cfg.max_extruder_temp, cfg.max_bed_temp)
    ok = _print_issues(issues)
    return 0 if ok else 1


def cmd_test(args, store: MacroStore, cfg: Config) -> int:
    macro = store.get(args.name)
    overrides = _parse_param_overrides(args.param)

    print(f"Validace makra {macro.name}:")
    issues = macro.validate(cfg.max_extruder_temp, cfg.max_bed_temp)
    if not _print_issues(issues):
        print("Nalezeny chyby – test přerušen. Oprav makro (viz výše) a zkus to znovu.")
        return 1

    try:
        rendered = macro.render(overrides)
    except Exception as exc:
        print(f"Chyba při renderování s danými parametry: {exc}")
        return 1

    print("\n--- Vyrenderovaný G-code (dry-run) ---")
    print(rendered)
    print("---------------------------------------")

    if not args.live:
        print("Dry-run dokončen, na tiskárnu nic odesláno nebylo. Pro živý test přidej --live.")
        return 0

    client = MoonrakerClient(cfg.moonraker_host, cfg.api_key, cfg.request_timeout)
    try:
        state = client.printer_state()
    except MoonrakerError as exc:
        print(f"Nepodařilo se zjistit stav tiskárny: {exc}")
        return 1

    if state in ("printing", "paused") and not args.force:
        print(f"Tiskárna je ve stavu '{state}' – živý test odmítnut (--force pro obejití, na vlastní riziko).")
        return 1

    if not _confirm(f"Opravdu spustit tento G-code na tiskárně (stav: {state})?", args.yes):
        print("Zrušeno uživatelem.")
        return 1

    try:
        client.run_gcode(rendered)
    except MoonrakerError as exc:
        print(f"Tiskárna/Klipper vrátili chybu při provádění: {exc}")
        return 1

    print("Živý test proběhl bez chyby (Klipper odpověděl 'ok').")
    return 0


def cmd_deploy(args, store: MacroStore, cfg: Config) -> int:
    macro = store.get(args.name)

    print(f"Validace makra {macro.name}:")
    issues = macro.validate(cfg.max_extruder_temp, cfg.max_bed_temp)
    if not _print_issues(issues):
        print("Makro obsahuje chyby – nasazení přerušeno.")
        return 1

    client = MoonrakerClient(cfg.moonraker_host, cfg.api_key, cfg.request_timeout)

    try:
        state = client.printer_state()
        if state in ("printing", "paused") and not args.force:
            print(f"Tiskárna právě tiskne (stav: {state}) – nasazení přerušeno (--force pro obejití).")
            return 1
    except MoonrakerError as exc:
        print(f"Varování: nepodařilo se zjistit stav tiskárny ({exc}).")
        if not args.force:
            print("Nasazení přerušeno – použij --force, pokud chceš pokračovat i tak.")
            return 1

    try:
        existing = client.read_config_file(cfg.macros_filename) or ""
    except MoonrakerError as exc:
        print(f"Nepodařilo se přečíst '{cfg.macros_filename}' z tiskárny: {exc}")
        return 1

    updated = _upsert_macro_block(existing, macro)

    if not _confirm(f"Nahrát '{cfg.macros_filename}' na tiskárnu a restartovat Klipper (RESTART)?", args.yes):
        print("Zrušeno uživatelem.")
        return 1

    try:
        client.write_config_file(cfg.macros_filename, updated)
    except MoonrakerError as exc:
        print(f"Nahrání souboru selhalo: {exc}")
        return 1
    print(f"Soubor '{cfg.macros_filename}' nahrán na tiskárnu.")

    if not args.no_restart:
        try:
            client.restart_klipper_config()
            print("Odeslán příkaz RESTART – Klipper znovu načítá konfiguraci.")
        except MoonrakerError as exc:
            print(f"Nahráno, ale RESTART se nepodařilo odeslat: {exc}")
            print(f"Restartuj Klipper ručně (Mainsail/Fluidd, nebo gcode 'RESTART').")

    macro.deployed = True
    store.save(macro)
    print(f"Hotovo – makro '{macro.name}' je nasazené.")
    return 0


def cmd_delete(args, store: MacroStore, cfg: Config) -> int:
    if args.name not in store:
        print(f"Makro '{args.name}' v lokálním úložišti není.")
        return 1

    if args.remote:
        client = MoonrakerClient(cfg.moonraker_host, cfg.api_key, cfg.request_timeout)
        try:
            existing = client.read_config_file(cfg.macros_filename) or ""
            updated = _remove_macro_block(existing, args.name)
            client.write_config_file(cfg.macros_filename, updated)
            client.restart_klipper_config()
            print(f"Makro '{args.name}' odstraněno i z '{cfg.macros_filename}' na tiskárně, Klipper restartován.")
        except MoonrakerError as exc:
            print(f"Odstranění na tiskárně selhalo: {exc}")
            return 1

    store.delete(args.name)
    print(f"Makro '{args.name}' odstraněno z lokálního úložiště.")
    return 0


def cmd_status(args, store: MacroStore, cfg: Config) -> int:
    client = MoonrakerClient(cfg.moonraker_host, cfg.api_key, cfg.request_timeout)
    try:
        state = client.printer_state()
        remote_macros = client.list_macros()
    except MoonrakerError as exc:
        print(f"Nepodařilo se spojit s Moonrakerem na {cfg.moonraker_host}: {exc}")
        return 1

    print(f"Moonraker:          {cfg.moonraker_host}")
    print(f"Stav tiskárny:      {state}")
    print(f"Makra v Klipperu:   {', '.join(sorted(remote_macros)) or '(žádná vlastní makra)'}")
    local_names = set(store.list())
    remote_names = set(remote_macros)
    missing_locally = remote_names - local_names
    if missing_locally:
        print(f"Pozn.: na tiskárně jsou makra, která nemáš v lokálním úložišti: {', '.join(sorted(missing_locally))}")
    return 0


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tvorba, ladění, test a nasazení Klipper maker přes Moonraker API.")
    parser.add_argument("--host", help="Adresa Moonrakeru, např. http://192.168.1.30:7125")
    parser.add_argument("--api-key", help="Moonraker API klíč, pokud je vyžadován")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("new", help="Vytvořit nové makro (interaktivně)")
    p.add_argument("--name")
    p.add_argument("--description")
    p.add_argument("--category")
    p.add_argument("--param", action="append", help="výchozí testovací parametr KLIC=hodnota (lze víckrát)")
    p.add_argument("--force", action="store_true", help="přepsat, pokud makro už lokálně existuje")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("list", help="Vypsat lokálně uložená makra")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="Zobrazit výsledný .cfg blok makra")
    p.add_argument("name")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("validate", help="Zkontrolovat syntaxi a bezpečnostní limity makra")
    p.add_argument("name")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("test", help="Otestovat makro (dry-run, s --live na reálné tiskárně)")
    p.add_argument("name")
    p.add_argument("--param", action="append", help="přepsání testovacího parametru KLIC=hodnota")
    p.add_argument("--live", action="store_true", help="skutečně poslat vyrenderovaný gcode na tiskárnu")
    p.add_argument("--force", action="store_true", help="povolit --live test, i když tiskárna tiskne")
    p.add_argument("--yes", action="store_true", help="nepotvrzovat živý test interaktivně")
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("deploy", help="Nahrát makro do macros.cfg na tiskárně a restartovat Klipper")
    p.add_argument("name")
    p.add_argument("--no-restart", action="store_true", help="nahrát, ale neposílat RESTART")
    p.add_argument("--force", action="store_true", help="nasadit i když tiskárna tiskne / stav neznámý")
    p.add_argument("--yes", action="store_true", help="nepotvrzovat nasazení interaktivně")
    p.set_defaults(func=cmd_deploy)

    p = sub.add_parser("status", help="Zobrazit stav tiskárny a makra aktuálně načtená Klipperem")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("delete", help="Smazat makro lokálně (volitelně i na tiskárně)")
    p.add_argument("name")
    p.add_argument("--remote", action="store_true", help="odstranit i z macros.cfg na tiskárně + RESTART")
    p.set_defaults(func=cmd_delete)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    cfg = Config.load()
    if args.host:
        cfg.moonraker_host = args.host
    if args.api_key:
        cfg.api_key = args.api_key

    store = MacroStore()
    return args.func(args, store, cfg)


if __name__ == "__main__":
    sys.exit(main())
