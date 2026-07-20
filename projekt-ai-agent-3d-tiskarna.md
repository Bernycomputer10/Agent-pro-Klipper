# Projekt: AI agent pro správu 3D tisku (Klipper + Home Assistant + hlasový asistent)

## 1. Přehled architektury

```
                 Internet
                    │
        [Pi Zero 2 W – gateway]  (existující: nginx reverse proxy,
              192.168.1.10        WireGuard VPN, UFW, fail2ban)
                    │
        ── vnitřní síť 192.168.1.0/24 ──
                    │
    ┌───────────────┼────────────────────────┐
    │               │                        │
[Pi 4 – 1 GB]   [Pi 5 – 8 GB]         [Pi Zero 2 W – satelit]
192.168.1.30    192.168.1.20 (stáv.)   192.168.1.40 (nový, u tiskárny)
Klipper +       Docker host:           Linux Voice Assistant
Moonraker         ├─ Home Assistant      (ESPHome protokol,
   │              │   ├─ Assist pipeline   mikrofon + reproduktor,
   └─USB─ BTT SKR  │   ├─ Moonraker (HACS)  wake word na místě)
    Mini E3 V3.0   │   └─ Spoolman (HACS)
                   ├─ Spoolman (SQLite)
                   ├─ Whisper (STT) + Piper (TTS)
                   └─ AI agent = Claude (Anthropic conv. agent)
```

**Rozdělení rolí:**
- **Pi 4 (1 GB)** – jen "real-time" vrstva: Klipper (klippy) + Moonraker. Žádné UI, žádné těžké služby. Komunikuje s reálnou deskou tiskárny přes USB nebo UART a poskytuje síťové API na portu 7125.
- **Pi 5 (8 GB)** – "mozek" celého ekosystému: Home Assistant, databáze materiálů, hlasový pipeline a samotný AI agent, který volá Moonraker/Spoolman API jako nástroje (tools).

Díky tomu, že Moonraker komunikuje po síti, agent na Pi 5 vůbec nemusí běžet na stejném zařízení jako Klipper – to je přesně tento návrh.

---

## 2. Fáze 1 – Klipper na Pi 4 (1 GB RAM)

### 2.1 Příprava OS
- Raspberry Pi OS Lite (bez desktopu), 64bit doporučeno.
- Vypnout/odinstalovat nepotřebné služby (Bluetooth, avahi pokud nepotřebuješ mDNS, swap nastavit střídmě – zram místo velkého swapfile).
- **Neinstalovat Mainsail/Fluidd lokálně** na Pi 4 – při 1 GB RAM to zbytečně žere paměť. Web UI necháš běžet centrálně (na Pi 5, případně jen přes Moonraker API voláno agentem). Pokud chceš občas i grafické UI, Mainsail lze hostovat i mimo Pi 4 a jen se přes síť napojit na `192.168.1.30:7125`.

### 2.2 Instalace Klipper + Moonraker
- Standardní cesta je **KIAUH** (Klipper Installation And Update Helper) – interaktivní skript, který nainstaluje Klipper, Moonraker i volitelně Mainsail/Fluidd, a umí i update.
- Postup: naklonovat KIAUH → spustit → zvolit instalaci Klipper → Moonraker → (Mainsail/Fluidd nepovinně, viz výše).

### 2.3 Připojení tiskárny – BTT SKR Mini E3 V3.0 (USB)
Deska je **BIGTREETECH SKR Mini E3 V3.0** – integrovaná 32bit deska (STM32G0B1). Pro ni je USB standardní a doporučené připojení k Pi, žádné UART/GPIO zapojení není potřeba.

⚠️ **Ověř před flashováním verzi na potisku desky.** V3.0 (čip STM32G0B1) a V3.0.1 (čip STM32F401) nejsou vzájemně kompatibilní na úrovni firmware. Postup níže je pro V3.0 – pokud máš V3.0.1, dej vědět, hodnoty se liší.

Na Pi 4 po připojení USB kabelem zjisti přesnou cestu:
```
ls /dev/serial/by-id/
```
Výstup bude podobný `usb-Klipper_stm32g0b1xx_XXXXXXXXXXXXXXXXXXXXXXXX-if00`. Tuto cestu použij v `printer.cfg`:
```
[mcu]
serial: /dev/serial/by-id/usb-Klipper_stm32g0b1xx_XXXXXXXXXXXXXXXXXXXXXXXX-if00
```

### 2.4 Flashování MCU firmware (SKR Mini E3 V3.0)
V adresáři Klipperu na Pi 4:
```
make menuconfig
```
Nastav:
- Micro-controller Architecture: **STM32**
- Processor model: **STM32G0B1**
- Enable extra low-level configuration options: **zapnuto** (odemkne další dvě volby)
- Bootloader offset: **8KiB bootloader**
- Clock Reference: **8 MHz**
- Communication interface: **USB (on PA11/PA12)**

Potom:
```
make
```
Vznikne `~/klipper/out/klipper.bin`. SKR Mini E3 V3.0 se needituje přes `make flash`, ale přes SD kartu:
1. `klipper.bin` zkopíruj na FAT32 SD kartu a **přejmenuj na `firmware.bin`** (bez přejmenování se firmware nenahraje).
2. SD kartu vlož do slotu na vypnuté desce.
3. Zapni desku, chvíli počkej (nahrávání firmware), pak vypni a zapni znovu.
4. Ověř na Pi: `ls /dev/serial/by-id/` by měla ukázat nové zařízení.

### 2.5 Síť a bezpečnost
- Pi 4 zůstává **jen na vnitřní síti** – žádná přímá expozice ven (na rozdíl od Pi Zero 2 W gateway). Přístup zvenčí (pokud vůbec potřeba) jde přes existující WireGuard/nginx na gateway.
- Statická IP (návrh `192.168.1.30`), firewall (UFW) povolující port 7125 jen z `192.168.1.20` (Pi 5).

---

## 3. Fáze 2 – Databáze materiálů a tiskové profily

Místo vlastní SQLite databáze (kterou jsi dřív zvažoval) dává smysl použít **Spoolman** – open-source nástroj přímo pro tohle, s nativní podporou Moonraker/Klipper a SQLite backendem (takže tvůj předchozí průzkum SQLite vs. MariaDB se dá rovnou využít).

### 3.1 Proč Spoolman
- Databáze cívek/materiálů + REST API + webové UI (QR kódy na štítky, vlastní pole).
- Automaticky odečítá spotřebovaný filament podle průběhu tisku (přes Moonraker).
- Podporuje víc tiskáren najednou (do budoucna, pokud přibude druhá tiskárna).

### 3.2 Nasazení na Pi 5
- Docker kontejner `ghcr.io/donkie/spoolman`, databáze SQLite (výchozí, žádný extra DB server netřeba).
- Napojení do stávajícího docker-compose vedle Home Assistant.

### 3.3 Propojení s Moonraker (na Pi 4)
V `moonraker.conf` na Pi 4 přidat:
```
[spoolman]
server: http://192.168.1.20:7912   # adresa Spoolman na Pi 5
```
Moonraker pak sám hlásí Spoolmanu spotřebu filamentu během tisku a agent/uživatel může přes Moonraker nastavit aktivní cívku (`server.spoolman.post_spool_id`).

### 3.4 Struktura dat a tiskové profily
- **Filament/materiál**: typ (PLA, PETG, ABS, ASA, TPU…), výrobce, barva, průměr, hustota, cena, hmotnost cívky.
- **Tiskový profil** (jako vlastní pole u materiálu nebo spárovaný slicer profil): teplota trysky, teplota podložky, retrakce, rychlost, chlazení, pressure advance.
- Doporučený postup: nový materiál → agent (nebo ty) vyplní základní údaje → počáteční profil buď ručně, nebo naimportovaný z komunitní databáze **SpoolmanDB** (github.com/Donkie/SpoolmanDB), která obsahuje předvyplněné profily desítek výrobců a lze z ní rovnou čerpat výchozí teploty.
- Agent pak při zahájení tisku: podle zvolené cívky ověří zbývající množství a nastaví/zkontroluje odpovídající teploty přes Klipper makra.

---

## 4. AI agent a hlasový asistent (na Pi 5, přes Home Assistant)

Protože už máš na Pi 5 běžící Home Assistant, nejpřirozenější je postavit agenta na jeho **Assist** pipeline místo budování vlastního frameworku od nuly.

### 4.1 Volba "mozku" agenta – rozhodnuto: cloud (Claude)
- Home Assistant má oficiální integraci **Anthropic** (Claude) jako conversation agent v Assist pipeline – dostává přístup k vybraným entitám přes Assist API a umí je ovládat/dotazovat.
- Lokální LLM (Ollama) zůstává jen jako možná budoucí druhá pipeline pro jednoduché dotazy bez potřeby spolehlivého tool-callingu – pro řízení tiskárny se nepoužije.
- Pokud by ses někdy chtěl přesunout na OpenAI namísto Anthropic, HA má i pro to nativní integraci (OpenAI Conversation) – přepnutí je otázka výměny jedné integrace v Assist pipeline, zbytek architektury (exponované entity, Moonraker/Spoolman) zůstává stejný.

### 4.2 Napojení tiskárny a materiálů na Home Assistant
- HACS integrace **Moonraker** (`marcolivierarsenault/moonraker-home-assistant`) – přidá senzory (stav tisku, teploty, progress), kameru/thumbnaily, tlačítko emergency stop a tlačítka na Klipper makra.
- HACS integrace **Spoolman** – zpřístupní stav cívek jako entity (zbývající množství, upozornění na docházející filament).
- Tyto entity se pak v Home Assistant "vystaví" (expose) agentovi – jen ty, které má skutečně ovládat/číst.

### 4.3 Hlasový vstup/výstup – rozhodnuto: satelit na Pi Zero 2 W
- STT: Whisper, TTS: Piper – běží centrálně na Pi 5 jako součást Assist pipeline (bez změny).
- Fyzicky u tiskárny bude stát **další/nový Pi Zero 2 W** (jiný kus než gateway na 192.168.1.10) jako hlasový satelit – jen zachytává mikrofon/reproduktor a wake word, samotné STT/TTS/agent řeší Pi 5.
- Aktuálně doporučený projekt pro tuto roli je **Linux Voice Assistant** (Open Home Foundation) – nástupce staršího `wyoming-satellite` (ten je už neudržovaný, ale pořád funguje jako ověřená záloha, kdyby na novějším projektu, který je ještě relativně čerstvý, něco nesedělo). LVA komunikuje s Home Assistant přes ESPHome protokol a satelit se v HA objeví automaticky přes ESPHome integraci.
- Pi Zero 2 W (512 MB RAM) je uváděný jako podporovaný hardware pro LVA, ale je to spíš na hraně – držet na něm jen Lite OS a satelitní službu, nic navíc.
- Pi Zero 2 W nemá jack ani dost USB portů na mikrofon i reproduktor zvlášť – nejjednodušší je I2S HAT, který řeší oboje najednou (např. **ReSpeaker 2-Mic Pi HAT**, případně oficiální **Satellite1** HAT).

### 4.4 Textový vstup
- Chat v Assist rozhraní Home Assistant (web dashboard i mobilní aplikace), případně napojení na existující zabezpečenou doménu (Authelia + nginx) – bez nutnosti nových otevřených portů.
- Vlastní dedikovaná Android aplikace (text i hlas) – podrobný návrh viz `navrh-android-agent-app.md`.
- Stávající ChatGPT Custom GPT most (`tiskarna.bernytech.cz`) může zůstat jako paralelní/záložní kanál, nebo ho postupně nahradit tímto lokálním řešením.

### 4.5 Bezpečnostní scope agenta
- Agentovi vystavit jen bezpečné akce: stav tisku, spuštění/pauza/zrušení, výběr cívky, teploty ke čtení. Vyhnout se plnému "spusť libovolný G-kód" přístupu bez potvrzení – halucinace modelu by mohla poslat nebezpečnou teplotu nebo pohyb.
- U rizikových akcí (spuštění tisku, změna teplot) zvážit vyžadování potvrzení; emergency stop naopak vždy dostupný bez omezení.
- Moonraker/Klipper na Pi 4 zůstává jen na vnitřní síti, žádná přímá expozice ven – v souladu s tím, jak už máš postavenou gateway na Pi Zero 2 W.

---

## 5. Navrhovaný postup nasazení

1. OS + Klipper/Moonraker na Pi 4, flash MCU, ověřit tisk (dočasně přes Mainsail odkudkoli v síti).
2. Statická IP + firewall pravidla pro Pi 4 (jen z Pi 5).
3. Spoolman kontejner na Pi 5, propojit `[spoolman]` v moonraker.conf.
4. Naplnit počáteční materiály (import ze SpoolmanDB pro rychlý start) + základní tiskové profily.
5. HACS integrace Moonraker + Spoolman do Home Assistant.
6. Assist pipeline: přidat Anthropic conversation agent, vystavit jen vybrané entity.
7. STT/TTS (Whisper/Piper na Pi 5) + Pi Zero 2 W satelit u tiskárny (Linux Voice Assistant + mikrofon/reproduktor HAT).
8. Doladit bezpečnostní scope a potvrzování rizikových akcí.
9. Volitelně: zachovat/zrušit stávající ChatGPT Custom GPT most.

## 6. Zbývá doladit
- Ověřit na potisku desky, jestli je to V3.0 nebo V3.0.1 (jiný čip, viz 2.3) – níže uvedené `make menuconfig` hodnoty platí pro V3.0.
- Vybrat konkrétní mikrofon/reproduktor HAT pro Pi Zero 2 W satelit (ReSpeaker 2-Mic vs. Satellite1 vs. jiný).
