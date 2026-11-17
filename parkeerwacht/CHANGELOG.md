# Changelog

## 0.5.3

- Optie `go2rtc_trace`: zet het volledige RTSP-gesprek tussen go2rtc en de camera
  in het logboek, om te onderzoeken waarom een camera de verbinding sluit.
  Inloggegevens worden weggepoetst. Alleen tijdelijk aanzetten.
- Een beeld opvragen bij go2rtc geeft na 15 in plaats van 30 seconden op.

## 0.5.2

- Beveiliging tegen snel herhaald verbinden: verbreekt de camera de verbinding
  5 keer binnen 10 seconden, dan stopt de add-on de vaste verbinding en probeert
  hij het pas na 2, 4, 8, 16 en daarna elke 30 minuten opnieuw. Op 17 november
  2026 sloot de Tapo elke verbinding direct (EOF) en verbond go2rtc zo'n 20 keer
  per seconde opnieuw.
- Geen tweerichtingsaudio meer aanvragen bij de camera (`#backchannel=0`), tenzij
  `rtsp_url` zelf al een `#` met opties bevat.

## 0.5.1

- Bij het starten wacht de add-on tot go2rtc antwoordt, zodat de eerste controle
  niet mislukt met "Connection refused".

## 0.5.0

- Eigen go2rtc in de add-on, met één vaste RTSP-verbinding met de camera (nieuwe
  optie `rtsp_url`). Een controle vraagt één beeld bij go2rtc op, zodat de camera
  niet bij elke controle een nieuwe sessie krijgt. Met 0.4.0 startte Home Assistant
  elke minuut een ffmpeg-proces dat niet netjes afsloot ("Timeout while waiting of
  FFmpeg"), en na ongeveer 28 uur zat de Tapo weer vol (17 november 2026).
- go2rtc bouwt de verbinding na een storing zelf opnieuw op. Valt go2rtc zelf weg,
  dan start de add-on het opnieuw. Wachttijd tussen mislukte controles via go2rtc:
  30 s, 1 en daarna 2 minuten.
- go2rtc luistert alleen binnen de add-on. Het camera-adres staat in het logboek
  zonder gebruikersnaam en wachtwoord.
- Zonder `rtsp_url` werkt de add-on zoals 0.4.0, via Home Assistant.

## 0.4.0

- Terug naar per controle een kort camerabeeld: de add-on opent de stream, leest
  één vers beeld en sluit de verbinding direct. De vaste stream van 0.3.0 liet het
  geheugen van Home Assistant Core met ongeveer 1 % per uur oplopen, tot de Pi
  vastliep (15 november 2026). Met Parkeerwacht uit bleef het geheugen vlak.
- De oplopende wachttijd van 0.3.0 blijft: mislukt een beeld, dan volgt de
  volgende poging pas na 30 s, 1, 2, 4, 8, 16 en daarna elke 30 minuten. Zo raakt
  de camera bij een storing niet vol met nieuwe sessies. Tussendoor slaan de
  controles over zonder de camera aan te spreken.
- Het logboek toont per controle hoe lang het ophalen van het beeld duurde.

## 0.3.0

- Eén vaste verbinding met de camerastream in plaats van elke controle een nieuwe.
  Elke nieuwe stream kostte een plek op de camera. Bleven die hangen, dan zat de
  Tapo vol ("limiet bereikt voor het afspelen van video's") en kwam er pas na een
  herstart van de camera weer beeld (6 november 2026).
- Valt de stream weg, dan wacht de add-on steeds langer voor een nieuwe poging:
  30 s, 1, 2, 4, 8, 16 en daarna elke 30 minuten. Zo krijgt de camera tijd om oude
  sessies op te ruimen. Na een gelukte verbinding begint de reeks opnieuw.
- Elke mislukte verbindingspoging komt in het logboek, met de wachttijd tot de
  volgende. Een storing ziet er zo niet meer uit als een vastgelopen add-on.
- Geeft de stream steeds hetzelfde beeld, dan bouwt de add-on de verbinding opnieuw op.
- De controles zelf blijven op `interval_seconds` draaien en gebruiken het nieuwste
  beeld uit de stream; ouder dan 30 seconden telt als geen beeld.

## 0.2.1

- Is de camera niet beschikbaar in Home Assistant, dan staat er nu "camera niet
  beschikbaar" in het logboek in plaats van `'access_token'`.
- Een storing komt nog maar één keer in het logboek, plus een regel zodra de
  controles weer lukken.

## 0.2.0

- Meet nu de vrije ruimte langs de stoeprand in plaats van een vast vak. Er staan
  geen vakken op straat, dus een auto kan overal in de laadzone staan.
- De sensor is `off` (vrij) als er ten minste `min_free_m` aaneengesloten vrij is.
- Nieuw attribuut `vrije_ruimte_meter`; `dekking_procent` is vervallen.
- Nieuwe opties `tile_size_m`, `zone_start_m`, `zone_end_m` en `min_free_m`; `min_coverage` is vervallen.
- Vak tekenen op de webpagina is vervallen. De foto toont nu de laadzone en het vrije stuk.
- De meetlat hoort bij deze camerastand: verdraait de camera, dan klopt de meting niet meer.

## 0.1.2

- Elke controle gebruikt nu een vers camerabeeld via de MJPEG-stream. De snapshot
  van de TP-Link-koppeling is tot 5 minuten oud, waardoor metingen op rij vaak
  hetzelfde beeld waren.
- Nieuwe optie `core_url`.

## 0.1.1

- Pakketten vastgezet op de versies waarmee de herkenning getest is.

## 0.1.0

- Eerste versie: vrij/bezet via snapshots, MQTT-sensor, vak tekenen en foto's van wisselingen.
