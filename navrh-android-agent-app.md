# Návrh: Android aplikace – klient pro AI agenta (text + hlas)

Doplňuje hlavní projekt (`projekt-ai-agent-3d-tiskarna.md`) – jde o mobilní klienta, přes který se s agentem na Pi 5 mluví/píše z telefonu, mimo hlasový satelit u tiskárny.

## 1. Technologická volba

**Capacitor + React (TypeScript)** – stejný přístup jako u tvé předchozí password generator appky (React/Material Design), takže:
- znovu použitelný GitHub Actions build workflow (build webu → `cap sync android` → gradle → APK),
- `crypto.getRandomValues()` a další web API běží přirozeně (WebView), žádné polyfilly,
- UI klidně v tom samém dark cyberpunk stylu.

Alternativa: nativní Kotlin/Jetpack Compose – o něco plynulejší hlasové UI a menší APK, ale úplně jiný stack než máš zaběhnutý. Necháváme jako možnost, pokud by Capacitor na hlas nestačil.

## 2. Jak se app dívá na agenta

```
Android app ──HTTPS── (LAN nebo WireGuard) ──▶ Home Assistant (Pi 5, .20)
                                                     │
                                              Assist / conversation agent
                                                     │
                                              Claude ──▶ Moonraker / Spoolman
```

- Backend = přímo Home Assistant REST **Conversation API**: `POST /api/conversation/process`.
- Autentizace: **Long-Lived Access Token** (vytvoří se v HA profilu uživatele), posílá se jako `Authorization: Bearer <token>`.
- Mimo domácí síť: přes existující **WireGuard** profil na telefonu – stejný bezpečnostní model jako zbytek homelabu, žádný nový otevřený port.
- Appka sama nemá žádná práva navíc – co agent smí a nesmí, řídí se tím, co je v HA exponované (viz hlavní dokument, 4.5).

## 3. Hlas – řešeno na telefonu, ne přes Assist pipeline

Telefon má vlastní kvalitní STT/TTS, takže je zbytečné streamovat audio na HA (to už dělá satelit u tiskárny). Appka:

1. Mikrofon tlačítko → **STT na telefonu** → rozpoznaný text.
2. Text (z hlasu i z klávesnice) → stejné `POST /api/conversation/process`.
3. Odpověď agenta → zobrazí se v chatu a přečte se přes **TTS na telefonu**.

Knihovny (Capacitor pluginy):
- STT: `@capawesome-team/capacitor-speech-recognition` – aktivně udržovaný, on-device rozpoznávání, detekce ticha, víc jazyků.
- TTS: `@capacitor-community/text-to-speech` – napojení na Android `TextToSpeech`.

Satelit na Pi Zero 2 W (streamuje audio do Whisper/Piper na Pi 5) a telefon (STT/TTS lokálně) jsou tedy dvě různé cesty ke stejnému conversation agentovi – to je v pořádku, nemusí být jednotné.

## 4. Obrazovky

- **Chat (hlavní)** – bubliny zpráv (uživatel/agent), textové pole dole, mikrofon tlačítko vedle něj, indikátor „poslouchám…“ / „agent odpovídá…“.
- **Nastavení** – adresa Home Assistant, access token, jazyk rozpoznávání řeči, TTS zapnuto/vypnuto.
- *Volitelně později:* karta se stavem tiskárny (teploty, průběh tisku) nahoře na chat obrazovce – čistě čtení přes stejné HA entity, žádná nová logika navíc.

## 5. Bezpečnost

- Token se **neukládá do běžných Preferences** – potřebuje úložiště podložené Android Keystore (při implementaci vybereme konkrétní plugin, je jich víc a mění se).
- Provoz jen přes HTTPS na existující zabezpečené doméně, ne holé HTTP na LAN IP, i kdyby to technicky fungovalo.

## 6. Ukázka API komunikace

Požadavek:
```
POST /api/conversation/process
Authorization: Bearer <long-lived token>
Content-Type: application/json

{
  "text": "Jaká je teplota trysky?",
  "agent_id": "conversation.claude",
  "language": "cs"
}
```

Odpověď (zkráceně):
```
{
  "conversation_id": "01JXYZ...",
  "response": {
    "response_type": "action_done",
    "speech": { "plain": { "speech": "Tryska je na 210 °C." } }
  }
}
```

`agent_id` je potřeba nastavit na konkrétní Claude conversation agent (jinak HA sáhne po výchozím lokálním) – přesnou hodnotu zjistíš v HA: **Settings → Voice assistants → tvůj Claude agent**.

## 7. Návrh struktury projektu

```
android-agent-app/
├── android/                       # nativní shell (npx cap add android)
├── src/
│   ├── screens/ChatScreen.tsx
│   ├── screens/SettingsScreen.tsx
│   ├── services/haClient.ts       # volání /api/conversation/process
│   ├── services/voice.ts          # wrapper nad STT/TTS pluginy
│   └── App.tsx
├── capacitor.config.ts
├── package.json
└── .github/workflows/build-apk.yml
```

## 8. Build přes GitHub Actions

Stejný princip jako u password generator appky: `checkout → npm ci → npm run build → npx cap sync android → gradle assembleDebug → upload APK jako artifact` (sideload na telefon stejně jako předtím).

## 9. Zbývá doladit
- Zjistit přesné `agent_id` Claude agenta v HA (objeví se po nastavení Assist pipeline).
- Vybrat konkrétní plugin pro zabezpečené uložení tokenu.
- Potvrdit, jestli chceš i kartu se stavem tiskárny na hlavní obrazovce, nebo jen čistý chat.
