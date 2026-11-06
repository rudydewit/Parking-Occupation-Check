# Changelog

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
