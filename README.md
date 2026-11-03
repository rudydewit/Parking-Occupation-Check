# Parking Occupation Check

Home Assistant-add-on **Parkeerwacht**: bekijkt via een camera of de parkeerplek
voor de deur vrij is, zodat je weet of de auto aan huis kan laden.

Gemaakt voor een Tapo C325WB, maar elke `camera.*`-entiteit in Home Assistant die
snapshots levert zou moeten werken.

> **Status: vroege versie.** Draait op een Raspberry Pi 4 met Home Assistant OS,
> met een herkenning van ongeveer 0,7 s per beeld en ongeveer 125 MB geheugen. Nog
> niet getest: een echt leeg vak en nachtbeelden.

## Hoe het werkt

1. Elke 60 seconden haalt de add-on een vers beeld uit de MJPEG-stream van Home
   Assistant. De gewone snapshot kan bij sommige camera's minuten oud zijn.
2. YOLOX-Tiny herkent lokaal voertuigen. Er gaan geen beelden naar buiten.
3. Bedekt een voertuig minstens 50% van het vak, dan telt de meting als bezet.
4. De status wisselt pas na 2 metingen op rij naar bezet en na 3 op rij naar vrij.
5. De status gaat via MQTT discovery naar Home Assistant.

Lukt het drie keer op rij niet om een beeld op te halen, dan wordt de sensor
*niet beschikbaar*, en dus niet onterecht *vrij*. Een vrijwel zwart beeld verandert
de status niet.

## Vereisten

- Home Assistant OS of Supervised (`aarch64` of `amd64`)
- Mosquitto broker-add-on met de MQTT-integratie
- Een camera-entiteit in Home Assistant

## Installatie

### Via deze repository

1. Ga naar **Instellingen > Add-ons > Add-on store**.
2. Kies rechtsboven **⋮ > Repositories** en voeg toe:
   `https://github.com/rudydewit/Parking-Occupation-Check`
3. Installeer **Parkeerwacht**. De eerste build op een Raspberry Pi kan even duren.
4. Controleer op het tabblad **Configuratie** de `camera_entity`.
5. Zet op het tabblad **Info** *Toon in zijbalk* en *Watchdog* aan.
6. Start de add-on en kijk in het logboek of de controles lukken.

De repository moet hiervoor openbaar zijn. Bij een privérepository gebruik je de
lokale installatie.

### Lokaal via Samba

1. Kopieer de map `parkeerwacht` naar de share `addons`
   (`smb://homeassistant.local/addons`).
2. Kies in de Add-on store **⋮ > Controleren op updates**.
3. Installeer Parkeerwacht onder **Lokale add-ons** en volg daarna stap 4 tot en met 6 hierboven.

## Vak instellen

Open **Parkeerwacht** in de zijbalk en kies **Vak opnieuw tekenen**. Klik de hoeken
aan en kies **Opslaan**. Het standaardvak hoort bij de oorspronkelijke camerapositie,
dus bij een andere camera moet je altijd zelf tekenen.

## Entiteiten

| Entiteit | Betekenis |
|---|---|
| `binary_sensor.parkeerplek_bezet` | `on` = bezet, `off` = vrij; attribuut `dekking_procent` |
| `image.parkeerplek_foto` | Foto van de laatste wissel, met vak en herkende voertuigen |

De namen volgen uit de MQTT-discovery; controleer ze na installatie.

## Instellingen

| Optie | Standaard | Betekenis |
|---|---|---|
| `camera_entity` | `camera.tapo_woning_voor_live_view` | Camera die bekeken wordt |
| `core_url` | `http://homeassistant:8123` | Intern adres van Home Assistant; pas aan bij een andere poort |
| `interval_seconds` | `60` | Tijd tussen controles (15-600) |
| `min_confidence` | `0.35` | Minimale zekerheid van de herkenning |
| `min_coverage` | `0.5` | Deel van het vak dat bedekt moet zijn |
| `confirm_occupied` | `2` | Metingen op rij voor de wissel naar bezet |
| `confirm_free` | `3` | Metingen op rij voor de wissel naar vrij |

## Privacy

De add-on bewaart maximaal 50 foto's van wisselingen in zijn eigen `/data`-map,
en de laatste foto staat retained op de MQTT-broker. Daarop kunnen de straat,
voorbijgangers en kentekens te zien zijn. Houd daar rekening mee bij het delen van
back-ups. Zet geen snapshots in deze repository; `*.jpg` staat daarom in `.gitignore`.

## Ontwikkelen

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install onnxruntime numpy pillow
```

```bash
.venv/bin/python -m unittest discover -s parkeerwacht/tests
```

De tests op echte beelden draaien alleen als je `PARKEERWACHT_MODEL` (pad naar
`yolox_tiny.onnx`) en `PARKEERWACHT_SNAPSHOTS` (kommagescheiden paden) zet. Die
verwachten een auto in het standaardvak.

Verhoog bij elke wijziging `version` in `parkeerwacht/config.yaml` en werk
`parkeerwacht/CHANGELOG.md` bij, anders ziet Home Assistant geen update.

## Model

[YOLOX-Tiny](https://github.com/Megvii-BaseDetection/YOLOX) (Apache-2.0) wordt tijdens
de Docker-build gedownload en op checksum gecontroleerd.
