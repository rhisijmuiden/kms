# KMS Console huisstijl

Eigen huisstijl voor de KMS Console. Het beeld is een **schild met een sleutelgat**: veilig, rustig en technisch.

## Logo

| Bestand | Gebruik |
| --- | --- |
| `logo.svg` | Woordmerk op licht |
| `logo-light.svg` | Woordmerk op donker (menu) |
| `icon.svg` | Los icoon |
| `favicon.svg` | Browsertabblad |

Het schild is altijd **Lagoon** `#0E9F8E`. Het sleutelgat neemt de achtergrondkleur over.

## Kleuren

| Naam | Hex | Gebruik |
| --- | --- | --- |
| Abyss | `#0B1F24` | Menu links, donkere vlakken, codeblokken |
| Lagoon | `#0E9F8E` | Primaire knop (tekst erop in `#04201C`), logo, actief menu-item |
| Lagoon tekst | `#0B7F72` | Links en accenten op licht (haalt 4.5:1 contrast) |
| Mint | `#7FE3D4` | Accent op donker, code |
| Ink | `#0F2027` | Tekst |
| Slate | `#52646B` | Secundaire tekst |
| Mist | `#F3F6F7` | Achtergrond |
| Amber | `#F5B83D` | Alleen waarschuwingen en updates |

Statuskleuren (`--ok`, `--warn`, `--err`) alleen voor status, altijd met tekst erbij.

## Typografie

- **Manrope** 800 voor koppen, kort en krachtig.
- **Inter** 400/500/600 voor tekst, labels en tabellen.
- **JetBrains Mono** voor scripts, sleutels en IP-adressen.

Alle fonts staan lokaal in de app (geen internet nodig in de container), met systeemfonts als terugval.

## Interface

- Adminmenu altijd links op Abyss, logo bovenaan, taalkeuze en Info-knop onderaan.
- Kaarten wit op Mist, radius 16 px, rand `#DDE5E7`, geen schaduwen behalve bij vensters.
- Eén primaire (Lagoon) knop per scherm of venster.
- Klikdoelen minimaal 44 px hoog.

Tokens: `tokens.css`.
