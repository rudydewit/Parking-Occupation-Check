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
| `rtsp_url` | leeg | RTSP-adres van de camera, met camera-account, bijvoorbeeld `rtsp://gebruiker:wachtwoord@192.168.2.50:554/stream1` |
| `camera_entity` | `camera.tapo_woning_voor_live_view` | Camera in Home Assistant, alleen gebruikt als `rtsp_url` leeg is |
| `core_url` | `http://homeassistant:8123` | Intern adres van Home Assistant |
| `interval_seconds` | `60` | Tijd tussen twee controles |
| `min_confidence` | `0.35` | Hoe zeker het model moet zijn dat het een voertuig is |
| `tile_size_m` | `0.3` | Lengte van één stoeptegel langs de stoeprand, inclusief voeg |
| `zone_start_m` | `2.2` | Begin van de laadzone, in meter vanaf de onderrand van het beeld |
| `zone_end_m` | `12.1` | Eind van de laadzone, in dezelfde meters |
| `min_free_m` | `4.5` | Zoveel meter moet aaneengesloten vrij zijn om `vrij` te melden |
| `confirm_occupied` | `2` | Aantal metingen op rij voordat de plek bezet wordt |
| `confirm_free` | `3` | Aantal metingen op rij voordat de plek vrij wordt |

## Camera-adres (`rtsp_url`)

Bij een Tapo-camera maak je in de Tapo-app een camera-account aan: camera openen,
**Instellingen > Geavanceerde instellingen > Camera-account**. Dat is hetzelfde
account dat de TP-Link-integratie in Home Assistant voor live beeld gebruikt; heb je
het daar al ingevuld, dan kun je dat hergebruiken. Het adres wordt dan
`rtsp://gebruiker:wachtwoord@IP-adres:554/stream1`. Bevat het wachtwoord tekens als
`@`, `:` of `/`, schrijf die dan als `%40`, `%3A` en `%2F`.

De add-on start daarmee een eigen go2rtc, die één vaste verbinding met de camera
openhoudt en die na een storing zelf opnieuw opbouwt. Een controle vraagt één beeld
bij go2rtc op en spreekt de camera dus niet opnieuw aan. go2rtc luistert alleen
binnen de add-on; er komen geen nieuwe poorten op je netwerk. In het logboek staat
het adres zonder gebruikersnaam en wachtwoord.

Laat je `rtsp_url` leeg, dan haalt de add-on het beeld via Home Assistant, zoals
tot en met 0.4.0. Home Assistant start dan voor elk beeld een ffmpeg-proces dat bij
de Tapo-camera niet netjes afsluit, en na ongeveer een dag zat de camera daardoor
vol (17 november 2026).

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
- Mislukt een beeld via go2rtc, dan volgt een nieuwe poging na 30 s, 1 en daarna
  elke 2 minuten. Via Home Assistant loopt de wachttijd op tot 30 minuten, zodat de
  camera niet nog voller raakt. Elke poging staat in het logboek, met een regel
  zodra het weer lukt.
- De vaste verbinding van go2rtc is iets anders dan de vaste stream van 0.3.0: die
  liet Home Assistant de video doorlopend omzetten, en daarbij liep het geheugen op
  tot de Pi vastliep. go2rtc zet alleen het ene opgevraagde beeld om, binnen de
  add-on.
- Een camerakaart op een dashboard haalt zijn beelden nog steeds via Home
  Assistant. Staat zo'n dashboard lang open, dan kan dat de camera alsnog vullen.
- De laatste 50 wisselfoto's staan lokaal in de add-on en worden daarna overschreven.
  Ze tonen ook de straat en de buren, en gaan dus mee in je back-ups.

## Herkenningsmodel

[YOLOX-Tiny](https://github.com/Megvii-BaseDetection/YOLOX) (Apache-2.0), wordt bij
het bouwen van de add-on gedownload en gecontroleerd op checksum.
