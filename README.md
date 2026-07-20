# klipper-macro-tool

Nástroj pro tvorbu, ladění, testování a nasazení Klipper maker (`[gcode_macro]`)
přes Moonraker API. Počítá s architekturou, kde Klipper + Moonraker běží na
jednom zařízení (v projektu: Pi 4, 192.168.1.30) a tento nástroj (případně
volaný AI agentem) běží jinde v síti (v projektu: Pi 5).

## Co dělá

1. **`new`** – interaktivně vytvoří definici makra a uloží ji lokálně (do
   `macros_store.json`), zatím nic neposílá na tiskárnu.
2. **`validate`** – zkontroluje syntaxi Jinja2 šablony a základní bezpečnostní
   limity (příliš vysoké teploty, prázdné makro, neplatný název, riziko
   nekonečné rekurze).
3. **`test`** – vyrenderuje výsledný G-code (dry-run, nic se neposílá), volitelně
   s `--live` odešle přímo vyrenderovaný G-code na tiskárnu k reálnému
   otestování – **bez nutnosti makro už mít nahrané v Klipperu**.
4. **`deploy`** – teprve po úspěšné validaci zapíše makro do `macros.cfg`
   v config rootu Moonrakeru a pošle `RESTART`, aby si Klipper makro načetl.
5. **`status`** – stav tiskárny + seznam maker, která Klipper aktuálně zná.

Validace a testy jsou heuristické (regulární výrazy nad vyrenderovaným
G-code), ne plnohodnotný Klipper interpret – **rozhodně nenahrazují opatrnost
u maker, která hýbou motory nebo topí**.

## Instalace (na Pi 5, vedle AI agenta / Home Assistant)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
# uprav config.json – hlavně moonraker_host, případně api_key
```

Moonraker musí mít v `moonraker.conf` povolený zápis do config rootu
(výchozí chování) a přístup ze sítě, kde tento nástroj běží.

**Jednorázově ručně** přidej do `printer.cfg` na Pi 4 (pokud tam ještě není):

```
[include macros.cfg]
```

Nástroj `printer.cfg` záměrně needituje sám – jde o hlavní konfigurační
soubor tiskárny a jeho automatická úprava je riziko, které raději necháváme
na uživateli.

## Použití

```bash
# vytvoření makra (např. primovaci cara)
python3 cli.py new --name PRIME_LINE --description "Primovaci cara pred tiskem"
# tělo makra se zadává řádek po řádku, ukončí se prázdným řádkem, např.:
#   G92 E0
#   G1 Z{params.ZHOP|default(5)|float} F600
#   G1 E{params.LENGTH|default(30)|float} F300
#   G1 Z-{params.ZHOP|default(5)|float} F600

python3 cli.py validate PRIME_LINE
python3 cli.py test PRIME_LINE                       # dry-run
python3 cli.py test PRIME_LINE --param LENGTH=45     # dry-run s jinou hodnotou
python3 cli.py test PRIME_LINE --live                # skutečně spustí na tiskárně (tiskárna nesmí tisknout)
python3 cli.py deploy PRIME_LINE                     # zapíše do macros.cfg + RESTART
python3 cli.py status                                # stav tiskárny + načtená makra
```

Adresu Moonrakeru lze místo `config.json` zadat i přes `--host` nebo
proměnnou prostředí `MOONRAKER_HOST` (podobně `MOONRAKER_API_KEY`).

### Důležité: syntaxe parametrů

Klipper nepoužívá standardní Jinja2 zápis `{{ promenna }}`, ale jednoduché
složené závorky – `{ params.NAZEV|default(hodnota) }`. Bloky `{% if %}`,
`{% for %}` zůstávají stejné. Nástroj tuto syntaxi respektuje 1:1, takže co
projde validací zde, by mělo být syntakticky validní i pro samotný Klipper.

### Bezpečnostní chování

- `test --live` i `deploy` odmítnou pokračovat, pokud tiskárna právě tiskne
  nebo je pozastavená (dá se obejít `--force`, na vlastní riziko).
- Validace hlásí jako **chybu** (blokuje deploy/test) teploty nad
  nastavený limit (`max_extruder_temp`, `max_bed_temp` v `config.json`).
- Nasazení (`deploy`) i mazání na tiskárně (`delete --remote`) se vždy
  ptají na potvrzení, pokud nepřidáš `--yes`.

## Napojení na AI agenta

Moduly `moonraker_client.py`, `macro.py` a `store.py` jsou bez závislosti na
`argparse`/CLI, takže je agent (Home Assistant / vlastní skript) může
importovat přímo jako Python knihovnu a volat stejné funkce, které používá
CLI (`Macro`, `MacroStore`, `MoonrakerClient`), místo spouštění `cli.py` jako
podprocesu.
