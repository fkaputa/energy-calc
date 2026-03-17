# EA-Sim Berekeningslogica

> Overzicht van de rule-based BESS-simulatie (Battery Energy Storage System) zoals geïmplementeerd in `ea_sim.py`. Dit document is bedoeld voor energiespecialisten die de onderliggende logica willen begrijpen zonder de broncode te lezen.

---

## 1. Doel van de simulatie

De EA-Sim berekent het optimale laad- en ontlaadgedrag van een batterij (BESS) op basis van uurlijkse of kwartierlijkse verbruiks- en PV-productiedata. Het doel is te evalueren wat de impact is van een batterij op:

- **Piekvermogen** afgenomen van het net (maandpiek in kW)
- **Energiekosten** via het Belgische distributietarief
- **Zelfconsumptie** van zonne-energie

De simulatie is een deterministisch, rule-based model — geen wiskundige optimalisatie — en is vertaald vanuit de bestaande QlikView-berekeningslogica.

---

## 2. Invoerdata

| Gegeven | Beschrijving |
|---|---|
| **Verbruiksprofiel** | Tijdreeks van gemiddeld vermogen (kW) per interval (typisch 15 min) |
| **PV-productieprofiel** | Tijdreeks van PV-opwekking (kW), optioneel |
| **Batterijcapaciteit** | Opslagcapaciteit in kWh (bijv. 1.000 kWh) |
| **Batterijvermogen** | Max laad-/ontlaadvermogen in kW (standaard: capaciteit / 2) |
| **Aansluitvermogen** | Contractueel netaansluitvermogen in kW (bijv. 1.700 kW) |
| **Injectielimiet** | Max terugleververmogen in kW |
| **Strategie** | `pv_self_consumption` of `peak_shaving` |
| **Drempelwaarde** | Vermogensdrempel voor peak shaving (standaard: 70% van aansluitvermogen) |

---

## 3. Kernberekening: Net Power

De eerste stap is de berekening van het **netto vermogen** per tijdstap:

```
Net Power (kW) = Verbruik (kW) − PV-productie (kW)
```

- **Net Power > 0** → netto-afname van het net (verbruik overschrijdt PV)
- **Net Power < 0** → netto-injectie (PV-overschot)

---

## 4. Batterijstrategieën

### 4.1 Strategie: PV Self-Consumption

De eenvoudigste strategie, gericht op maximale benutting van eigen PV-productie.

**Regels per tijdstap:**

| Situatie | Actie |
|---|---|
| PV-overschot (net power < 0) | **Laden** — sla het overschot op in de batterij, begrensd door beschikbare ruimte en max laadvermogen |
| Netto-afname (net power > 0) | **Ontladen** — lever energie uit de batterij, begrensd door beschikbare energie en max ontlaadvermogen |

De State of Charge (SoC) wordt na elke stap bijgewerkt en begrensd tussen 0 en de maximale capaciteit.

### 4.2 Strategie: Peak Shaving (met look-ahead)

De geavanceerde strategie, gericht op het verlagen van de maandelijkse piekafname van het net. Deze strategie kijkt **vooruit** naar toekomstige hoogverbruiksperiodes.

#### Stap 1: Regime-detectie

Het volledige vermogensprofiel wordt opgedeeld in **regimes**:

- **Hoog regime**: periodes waar het net power de drempelwaarde overschrijdt
- **Laag regime**: periodes onder de drempelwaarde

Per regime wordt de **totale energie boven/onder de drempel** berekend. Elke tijdstap kent de energiebehoefte van het eerstvolgende regime (look-ahead).

#### Stap 2: Batterijsturing

Per tijdstap wordt een van de volgende regels toegepast (in prioriteitsvolgorde):

| # | Situatie | Actie |
|---|---|---|
| 1 | **PV-overschot** (net power < 0) | Laden vanuit PV-surplus |
| 2 | **Laag regime**, volgend regime is hoog, **SoC onvoldoende** | Pre-charging: laden vanuit het net, begrensd door de ruimte onder de drempel (om geen nieuwe piek te creëren) |
| 3 | **Laag regime**, volgend regime is hoog, **SoC voldoende** | Gedeeltelijk ontladen: het overtollige boven de verwachte behoefte wordt ontladen |
| 4 | **Hoog regime** | Ontladen om het vermogen boven de drempel af te toppen |
| 5 | **Laag regime**, geen aankomend hoog regime | Ontladen in het verbruik (restenergie benutten) |

**Toelichting**: De batterij probeert vóór een piekperiode voldoende energie op te slaan (regel 2), maar laadt nooit boven de drempelwaarde om te voorkomen dat het laden zelf een nieuwe netpiek veroorzaakt. Tijdens de piekperiode (regel 4) ontlaadt de batterij precies genoeg om het netvermogen onder de drempel te houden.

---

## 5. Grid Power (resultaat)

Na de batterijsimulatie wordt het effectieve netvermogen berekend:

```
Grid Power (kW) = Net Power (kW) + BESS Power (kW)
```

Waarbij BESS Power:
- **Positief** = batterij laadt (extra afname van net/PV)
- **Negatief** = batterij ontlaadt (minder afname van net)

---

## 6. Distributiekostenberekening

De simulatie berekent de Belgische distributiekosten volgens de **Zenne-Dijle tariefstructuur (2026)** voor drie scenario's:

| Scenario | Vermogensprofiel |
|---|---|
| **Baseline** | Alleen verbruik (geen PV, geen batterij) |
| **Met PV** | Verbruik minus PV-productie |
| **Met PV + BESS** | Verbruik minus PV, gecorrigeerd voor batterij |

### Tariefformule per maand

De maandelijkse distributiekosten bestaan uit vier componenten:

#### A. Capaciteitskosten

```
Capaciteitskosten = min(
    capaciteitsplafond × Afname,
    Aansluitvermogen × aansluitingstarief × D/365
      + Maandpiek × piektarief × D/365
      + dagelijkse capaciteitstoeslag × D
)
```

Hier wordt het **minimum** genomen van twee methodes: een kost op basis van totale afname (kWh) en een kost op basis van aansluitvermogen + gemeten maandpiek. Dit reflecteert het Belgische capaciteitstarief dat de laagste van beide aanrekent.

#### B. Variabele kosten

```
Variabele kosten = (ODV + toeslag + basiskost afname) × Afname (MWh)
```

#### C. Injectiekosten

```
Injectiekosten = injectietarief × Injectie (MWh)
```

#### D. Vaste kosten

```
Vaste kosten = vast dagtarief × aantal dagen
```

**Totaal per maand** = A + B + C + D

### Tariefparameters (standaardwaarden Zenne-Dijle 2026)

| Parameter | Waarde | Eenheid |
|---|---|---|
| Aansluitingstarief | 40.684,50 | EUR/MW/jaar |
| Piektarief | 59.856,96 | EUR/MW/jaar |
| ODV | 3,9196 | EUR/MWh |
| Toeslag | 0,3058 | EUR/MWh |
| Basiskost afname | 29,14 | EUR/MWh |
| Injectietarief | 1,751 | EUR/MWh |
| Vast dagtarief | 7,1316 | EUR/dag |
| Capaciteitsplafond | 150,8082 | EUR/MWh |
| Dagelijkse capaciteitstoeslag | 0,155 | EUR/dag |

---

## 7. Beperkingen & aandachtspunten

- **Geen round-trip efficiency in huidige BESS-loop**: de configuratie bevat een `round_trip_efficiency` parameter (standaard 88%), maar deze wordt momenteel niet toegepast in de laad-/ontlaadberekening. Alle energie die geladen wordt, is 1:1 beschikbaar bij ontlading.
- **Deterministisch model**: er is geen onzekerheid of stochastische variatie — de simulatie gebruikt perfecte voorkennis van het volledige profiel (met name bij peak shaving look-ahead).
- **Geen degradatie**: batterijveroudering of capaciteitsverlies wordt niet gemodelleerd.
- **Geen tijdsgebonden tarieven**: de huidige logica houdt geen rekening met variabele energieprijzen (day-ahead, onbalans); alleen distributienetkosten worden berekend.
- **SoC start op 0**: de batterij begint leeg bij het begin van de simulatie.

---

## 8. Overzicht dataflow

```
┌─────────────────┐     ┌──────────────────┐
│ Verbruiksprofiel │     │ PV-profiel       │
│ (kW per 15 min)  │     │ (kW per 15 min)  │
└────────┬────────┘     └────────┬─────────┘
         │                       │
         └───────────┬───────────┘
                     ▼
            ┌────────────────┐
            │  Net Power     │
            │  = Verbruik-PV │
            └───────┬────────┘
                    │
         ┌──────────┴──────────┐
         ▼                     ▼
  ┌──────────────┐     ┌──────────────────┐
  │ PV Self-     │     │ Peak Shaving     │
  │ Consumption  │     │ (regime detectie │
  │              │     │  + look-ahead)   │
  └──────┬───────┘     └────────┬─────────┘
         │                      │
         └──────────┬───────────┘
                    ▼
          ┌──────────────────┐
          │  BESS Power      │
          │  (laden/ontladen)│
          └────────┬─────────┘
                   │
                   ▼
          ┌──────────────────┐
          │  Grid Power      │
          │  = Net + BESS    │
          └────────┬─────────┘
                   │
                   ▼
       ┌───────────────────────┐
       │ Distributiekosten     │
       │ (3 scenario's:        │
       │  baseline / PV / BESS)│
       └───────────────────────┘
```
