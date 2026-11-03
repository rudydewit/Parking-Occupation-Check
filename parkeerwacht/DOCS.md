# Parkeerwacht

Kijkt elke minuut via de camera of er een auto in het parkeervak staat en meldt
dat aan Home Assistant. Alles draait lokaal op de Pi; er gaan geen beelden naar
buiten.

## Wat je krijgt

| Entiteit | Betekenis |
|---|---|
| `binary_sensor.parkeerplek_bezet` | `on` = bezet, `off` = vrij, `unavailable` = geen camerabeeld |
| `image.parkeerplek_foto` | Foto met het vak van de laatste wissel |

De webpagina in de zijbalk toont de status, het laatste beeld en foto's van de
laatste wisselingen. Daar kun je ook het vak opnieuw tekenen: klik de hoeken aan
op de grond rondom de plek waar een auto staat.

## Instellingen

| Optie | Standaard | Uitleg |
|---|---|---|
| `camera_entity` | `camera.tapo_woning_voor_live_view` | Camera in Home Assistant |
| `core_url` | `http://homeassistant:8123` | Intern adres van Home Assistant |
| `interval_seconds` | `60` | Tijd tussen twee controles |
| `min_confidence` | `0.35` | Hoe zeker het model moet zijn dat het een voertuig is |
| `min_coverage` | `0.5` | Welk deel van het vak een voertuig moet bedekken |
| `confirm_occupied` | `2` | Aantal metingen op rij voordat de plek bezet wordt |
| `confirm_free` | `3` | Aantal metingen op rij voordat de plek vrij wordt |

## Goed om te weten

- Een wissel duurt standaard ongeveer 2 minuten naar bezet en 3 minuten naar vrij.
  Zo tellen voorbijrijdende auto's niet mee.
- Motoren en fietsen tellen niet als bezet.
- Bij een te donker beeld blijft de laatste status staan. Zonder camerabeeld wordt
  de sensor `unavailable`, nooit onterecht "vrij".
- De laatste 50 wisselfoto's staan lokaal in de add-on en worden daarna overschreven.
  Ze tonen ook de straat en de buren, en gaan dus mee in je back-ups.

## Herkenningsmodel

[YOLOX-Tiny](https://github.com/Megvii-BaseDetection/YOLOX) (Apache-2.0), wordt bij
het bouwen van de add-on gedownload en gecontroleerd op checksum.
