# Parkeerwacht

Kijkt elke minuut via de camera hoeveel ruimte er langs de stoeprand vrij is in de
laadzone, en meldt aan Home Assistant of er een auto past. Alles draait lokaal op
de Pi; er gaan geen beelden naar buiten.

## Wat je krijgt

| Entiteit | Betekenis |
|---|---|
| `binary_sensor.parkeerplek_bezet` | `on` = bezet, `off` = vrij, `unavailable` = geen camerabeeld; attribuut `vrije_ruimte_meter` |
| `image.parkeerplek_foto` | Foto van de laatste wissel, met de laadzone in rood en het vrije stuk in groen |

De webpagina in de zijbalk toont de status, het laatste beeld en foto's van de
laatste wisselingen.

## Instellingen

| Optie | Standaard | Uitleg |
|---|---|---|
| `camera_entity` | `camera.tapo_woning_voor_live_view` | Camera in Home Assistant |
| `core_url` | `http://homeassistant:8123` | Intern adres van Home Assistant |
| `interval_seconds` | `60` | Tijd tussen twee controles |
| `min_confidence` | `0.35` | Hoe zeker het model moet zijn dat het een voertuig is |
| `tile_size_m` | `0.3` | Lengte van één stoeptegel langs de stoeprand, inclusief voeg |
| `zone_start_m` | `2.2` | Begin van de laadzone, in meter vanaf de onderrand van het beeld |
| `zone_end_m` | `12.1` | Eind van de laadzone, in dezelfde meters |
| `min_free_m` | `4.5` | Zoveel meter moet aaneengesloten vrij zijn om `vrij` te melden |
| `confirm_occupied` | `2` | Aantal metingen op rij voordat de plek bezet wordt |
| `confirm_free` | `3` | Aantal metingen op rij voordat de plek vrij wordt |

## Goed om te weten

- Een wissel duurt standaard ongeveer 2 minuten naar bezet en 3 minuten naar vrij.
  Zo tellen voorbijrijdende auto's niet mee.
- Motoren en fietsen tellen niet als bezet.
- De meetlat is bepaald uit de stoeptegels op beelden van deze camera in deze stand.
  Verdraait de camera, dan klopt de meting niet meer. De webpagina heeft daarvoor
  geen instelscherm: de waarden staan bovenin `app/parking.py`.
- Pas je `tile_size_m` aan, dan schuiven de zone-instellingen mee op straat. Kijk op
  de foto of de rode lijn nog goed ligt.
- Voorbij ongeveer 8 meter vanaf de onderrand zijn geen voegen meer te zien en is de
  afstand doorgerekend, dus minder nauwkeurig.
- Bij een te donker beeld blijft de laatste status staan. Zonder camerabeeld wordt
  de sensor `unavailable`, nooit onterecht "vrij".
- De add-on opent elke controle kort de camerastream, neemt één vers beeld en
  sluit de verbinding weer. Mislukt dat, dan volgt een nieuwe poging pas na 30 s,
  1, 2, 4, 8 en 16 minuten en daarna elke 30 minuten. Elke poging staat in het
  logboek, met een regel zodra het weer lukt.
- Een vaste, doorlopende stream (versie 0.3.0) liet het geheugen van Home
  Assistant oplopen tot de Pi vastliep. Daarom is daar weer van afgestapt.
- De laatste 50 wisselfoto's staan lokaal in de add-on en worden daarna overschreven.
  Ze tonen ook de straat en de buren, en gaan dus mee in je back-ups.

## Herkenningsmodel

[YOLOX-Tiny](https://github.com/Megvii-BaseDetection/YOLOX) (Apache-2.0), wordt bij
het bouwen van de add-on gedownload en gecontroleerd op checksum.
