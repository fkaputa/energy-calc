# Tutorial: samenwerken met de Energy Pipeline (ook met Cursor)

Deze handleiding is bedoeld voor collega’s die **niet per se programmeur zijn**, maar wel samen met AI (zoals Cursor) projecten willen voorbereiden, simulaties willen draaien en resultaten willen valideren.

---

## Voor wie is dit document?

Gebruik deze tutorial als je:

- een **nieuw klantproject** wil toevoegen;
- verbruiks- en/of PV-bestanden in de juiste map wil zetten;
- batterijconfiguraties wil aanpassen;
- de pipeline wil draaien en output wil bekijken;
- resultaten wil vergelijken met een manuele/externe simulatie;
- met Cursor gericht code wil laten aanpassen als er afwijkingen zitten;
- een **nieuwe simulatiemodule** wil opzetten zonder bestaande flows te breken.

---

## Deel 1 — Nieuw project opzetten en pipeline draaien

## 1) Maak een nieuw project in `data/projects`

Kies een duidelijke projectnaam en maak een map:

```bash
mkdir -p data/projects/mijn_project
```

Plaats daarin je verbruiksbestanden (bijv. off-take Excel, kwartierwaarden, historiek-bestanden).

### Optioneel: PV-data toevoegen

Als je ook PV wil meenemen, maak dan een `pv`-submap:

```bash
mkdir -p data/projects/mijn_project/pv
```

Plaats daar PV-profielen (bijv. PVGIS-timeseries CSV).

> De pipeline zoekt automatisch naar ondersteunde consumptieformaten in de projectmap en naar PV-bestanden in `pv/`.

---

## 2) Kies of pas de configuratie aan in `config/`

Je kan starten met `config/default.yaml` of een kopie maken, bv.:

```bash
cp config/default.yaml config/mijn_project.yaml
```

Belangrijke stukken in de config:

- `simulator`: kies `pypsa` of `ea_sim`.
- `battery:`: instellingen voor de PyPSA-batterij (kost, max vermogen, efficiëntie, ...).
- `ea_sim:`: instellingen voor rule-based simulatie (batterijcapaciteit, vermogen, strategie, distributiekosten, ...).

Typische EA Sim-aanpassingen:

- `battery_capacity_kwh`
- `battery_power_kw`
- `strategy` (`peak_shaving` of `pv_self_consumption`)
- `distribution_costs.calculator`

---

## 3) Draai de pipeline

### Basisrun (normalize + simulatie)

```bash
python -m energy_pipeline.scripts.run_pipeline data/projects/mijn_project -o output/mijn_project --config config/mijn_project.yaml
```

### Alleen normaliseren (zonder simulatie)

```bash
python -m energy_pipeline.scripts.run_pipeline data/projects/mijn_project -o output/mijn_project --config config/mijn_project.yaml --no-simulate
```

### EA Sim forceren

```bash
python -m energy_pipeline.scripts.run_pipeline data/projects/mijn_project -o output/mijn_project --config config/mijn_project.yaml --simulator ea_sim
```

### Met visualisatie (PNG + PDF)

```bash
python -m energy_pipeline.scripts.run_pipeline data/projects/mijn_project -o output/mijn_project --config config/mijn_project.yaml --visualize --viz-days 14
```

---

## 4) Begrijp de output

Na een run vind je in `output/mijn_project/` onder andere:

- `consumption_profile.csv` (genormaliseerd verbruik)
- `consumption_profile_hourly.csv`
- `pv_generation_profile.csv` (als PV aanwezig)
- `pypsa_results/` met simulatietabellen (o.a. prijzen, dispatch, SOC)
- `images/` met grafieken
- `results_report.pdf`

Voor EA Sim komt distributiekostenoutput ook mee in `pypsa_results/`.

---

## 5) Open de webpagina

Een snelle manier om projecten en resultaten te bekijken:

```bash
python manage.py migrate
python manage.py runserver
```

Open daarna: `http://localhost:8000/`

- Je project verschijnt automatisch als map onder `data/projects/`.
- Vanuit de UI kan je simulaties opnieuw starten en grafieken bekijken.

---

## Deel 2 — Output vergelijken met externe/manuele simulatie (met Cursor)

## 1) Welke output gebruik je voor vergelijking?

Voor inhoudelijke vergelijking (kosten + verbruik + batterijgedrag) gebruik je vooral:

- `output/mijn_project/pypsa_results/ea_sim_detail.csv`
- `output/mijn_project/pypsa_results/ea_sim_distribution_costs.csv` (EA Sim)
- `output/mijn_project/pypsa_results/loads-p.csv`, `buses-marginal_price.csv`, ...

Gebruik daarnaast `results_report.pdf` voor snelle visuele controle.

---

## 2) Vergelijk met een manuele of externe simulatie

Praktische workflow:

1. Exporteer je manuele of externe simulatie naar CSV/Excel.
2. Leg die naast de pipeline-output (zelfde periode, zelfde eenheden).
3. Check eerst op hoog niveau:
   - totale kWh,
   - piekvermogen,
   - totale kost,
   - maandverdeling.
4. Zoom dan in op afwijkende dagen/uren.

Tip: start met 1 maand als referentie om sneller te zien waar verschillen ontstaan.

---

## 3) Cursor gebruiken om afwijkingen te analyseren

Je kan Cursor gebruiken als “debug-assistent”:

1. Geef Cursor context:
   - relevante outputtabellen van de pipeline,
   - de manuele/externe simulatie,
   - welke KPI afwijkt.
2. Vraag Cursor om:
   - verschillen te lokaliseren per stap,
   - hypotheses te geven (resampling, tekenconventie, drempel, tariefformule, ...),
   - een concrete code-aanpassing voor te stellen.
3. Laat Cursor een kleine, gerichte patch maken.
4. Draai dezelfde case opnieuw in de pipeline en vergelijk opnieuw.

**Belangrijk:** laat Cursor expliciet motiveren *waarom* een wijziging nodig is en *welk bestand* aangepast werd.

---

## 4) Voorbeeldprompt voor Cursor

> “Vergelijk `output/mijn_project/pypsa_results/ea_sim_detail.csv` met `referentie/manuele_simulatie.csv`. Focus op piekshaving en maandkost. Toon eerst waar de eerste afwijking in tijd ontstaat, geef daarna een minimale patch in de EA Sim-logica, en leg uit welke assumptie verschilt.”

---

## Deel 3 — Nieuwe simulatievariant bouwen zonder bestaande simulaties te breken

Wanneer je een volledig nieuw type simulatie wil maken, werk dan veilig en moduleerbaar.

## 1) Vertrek van een bestaande module

Bijvoorbeeld:

- kopieer `src/energy_pipeline/simulation/ea_sim.py`
- naar `src/energy_pipeline/simulation/ea_sim_variant_x.py`

Zo blijft de bestaande simulatie stabiel voor lopende projecten.

---

## 2) Voeg een aparte runner toe

Maak een script zoals:

- `src/energy_pipeline/scripts/run_ea_sim_variant_x.py`

of breid `run_pipeline` gecontroleerd uit met een nieuwe simulator-keuze.

---

## 3) Gebruik een aparte config

Maak bij voorkeur:

- `config/variant_x.yaml`

Zo kan je variant-specifieke parameters beheren zonder `default.yaml` voor iedereen te veranderen.

---

## 4) Valideer naast bestaande simulatie

Gebruik exact dezelfde inputdata en vergelijk:

- baseline-simulatie (bestaand)
- variant-simulatie (nieuw)

Controleer verschillen in:

- netafname en injectie,
- batterijgedrag,
- pieken,
- kosten.

---

## 5) Teamafspraak (aanbevolen)

Werk met deze volgorde:

1. **Data correct** in `data/projects/<project>`.
2. **Config vastleggen** in `config/<project_of_variant>.yaml`.
3. **Pipeline-run** met duidelijke outputmap.
4. **Vergelijking** met referentie/manuele simulatie.
5. **Pas daarna codewijzigingen** doen.

Zo vermijd je dat meerdere personen tegelijk in dezelfde modules botsen.

---

## Snelle checklist

- [ ] Projectmap aangemaakt in `data/projects/`.
- [ ] Verbruiksbestanden toegevoegd.
- [ ] Eventuele PV-data in `pv/` gezet.
- [ ] Correcte config gekozen/gekopieerd.
- [ ] Pipeline gedraaid naar `output/<project>`.
- [ ] Resultaten in CSV/PDF/web UI nagekeken.
- [ ] Eventuele vergelijking met manuele simulatie uitgevoerd.
- [ ] Alleen gerichte, verklaarbare code-aanpassingen gedaan (eventueel via Cursor).

