# Energy Consumption to PyPSA Battery Pipeline

Convert electricity consumption data from energy providers into a normalized format and run battery simulation or optimization (PyPSA or EnergyLabs rule-based EA Sim) for peak shaving, PV self-consumption, and off-peak charging.

## Features

- **Adapter framework**: Provider-specific adapters parse various input formats
- **Adams meetdata adapter**: Supports `Adams meetdata *.xlsx` (multiple sheets per year, 2 electricity meters summed, 15-min kW)
- **Belgian DSO adapter**: Supports `Afname_Elektriciteit_*.xlsx` exports (semicolon-separated, 15-min intervals)
- **Historiek dagtotalen adapter**: Supports `Historiek_afname_elektriciteit_<EAN>_*_dagtotalen.csv` (daily totals in kWh)
- **Smulders offtake adapter**: Supports Smulders offtake Excel (EAN in filename, 15-min intervals)
- **Normalized schema**: Timestamp + power_kw (kW), consumable by both simulators
- **PV generation**: With project folder input, if a `pv` subfolder exists (in the project dir or in the output dir), PV files are parsed (PVGIS timeseries CSV or same adapters as consumption), aggregated, and written as `pv_generation_profile.csv` and `pv_generation_profile_hourly.csv`. See [PV generation](#pv-generation).
- **PV projection**: If consumption covers a longer period than PV (e.g. consumption in 2025, PV data only up to 2023), the pipeline automatically projects PV by reusing the pattern of the last available year so the simulation can run; results are for simulation only and not directly comparable. See [PV projection](#pv-projection).
- **Hourly profiles**: Normalized consumption and PV (when present) are also written as `consumption_profile_hourly.csv` and `pv_generation_profile_hourly.csv` in the output dir.
- **Per-EAN temp cache**: With folder input, optional `output-dir/temp/` with `ean_quarterly_{EAN}.csv` and `ean_hourly_{EAN}.csv`; existing files are reused unless `--rebuild`.
- **Two simulators**: [PyPSA](#simulators) (cost-based optimization) and [EA Sim](#simulators) (rule-based BESS). Select via `--simulator` or `simulator:` in config.
- **PyPSA**: Single-bus network with grid (import/export), load, battery storage, and optional PV (curtailment allowed); time-of-use pricing; optimal battery sizing.
- **EA Sim**: Rule-based strategies `peak_shaving` or `pv_self_consumption`; fixed battery capacity; Belgian distribution-cost coefficients; exports PyPSA-compatible CSVs for the same visualizations.
- **Distribution cost calculators**: Selectable Belgian distribution cost formulas. `belgian_general` (analyst formula with stationary/non-stationary BESS mode and Elia discount) and `static_qv` (original simplified formula). Both run automatically during EA Sim; results include absolute (Z) and relative (G(Z) = Z/X) monthly costs.
- **Distribution cost reports**: Summary and monthly breakdown tables per calculator are included in the PDF report and exported as PNG images.
- **Web UI with interactive graphs**: Optional Django-based web interface (`projects_ui`) to browse projects, rerun simulations and inspect **linked, zoomable time series** in the browser.

## Quick start

If you already have Python and Git, this single command will:

- Create a virtual environment
- Install the package in editable mode
- Run the full pipeline on a sample project folder
- Start the web UI so you can inspect results interactively

```bash
git clone <this-repo-url> pypsa-battery && cd pypsa-battery && \
python -m venv .venv && source .venv/bin/activate && \
pip install -e . && \
python manage.py migrate && \
python -m energy_pipeline.scripts.run_pipeline data/projects/smulders -o output/smulders && \
python manage.py runserver
```

Then open `http://localhost:8000/` and click the `smulders` project to explore the interactive graphs.

---

## Installation

```bash
python -m venv .venv
source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows
pip install -e .
```

After `pip install -e .` you can run:

- `python -m energy_pipeline.scripts.run_pipeline` — parse + normalize + run simulator (PyPSA or EA Sim)
- `python -m energy_pipeline.scripts.run_pypsa` — PyPSA only (from existing consumption_profile.csv)
- `python -m energy_pipeline.scripts.run_ea_sim` — EA Sim only (from existing consumption_profile.csv)
- `python -m energy_pipeline.scripts.visualize` — plot existing output

Or use the installed commands: `run-pipeline`, `run-pypsa`, `run-ea-sim`, `visualize`.

If you want to use the **web UI**, also make sure Django is installed (it is pulled in via `pip install -e .`).

```bash
python manage.py migrate   # first time only
python manage.py runserver
```

Then open `http://localhost:8000/` in your browser.

---

## Web UI (projects explorer & interactive graphs)

The optional **web interface** provides a lightweight “projects explorer” on top of the existing CLI:

- **Projects overview** at `/`:
  - Lists all project folders under `data/projects`.
  - Shows whether a project already has output in `output/<project>`.
  - Lets you (re)run the pipeline for a project with a single click.
- **Project detail page** (visualization):
  - Shows summary KPIs, interactive **time-series graphs**, and HTML tables based on the CSV output in `output/<project>/`.
  - Uses the same underlying results as the CLI + `visualize` script; no extra computation logic is added in the UI.

### Project layout for the web UI

The web UI assumes the following convention:

- Input projects live under:
  - `data/projects/<project_name>/`
  - Optional `pv/` subfolder inside each project for PV input, as described in [PV generation](#pv-generation).
- Pipeline output goes to:
  - `output/<project_name>/`

### Creating a new project for the web UI

To add a new project that shows up in the web UI:

1. **Create an input folder** under `data/projects` (e.g. `data/projects/my_site/`) and drop your raw consumption file(s) there in one of the supported formats (Adams, Belgian DSO, Historiek, Smulders, …).
2. (Optional) Add a `pv/` subfolder under that project for PV input, as described in [PV generation](#pv-generation).
3. **Run the pipeline once** for this project so results exist for the UI:

   ```bash
   python -m energy_pipeline.scripts.run_pipeline data/projects/my_site -o output/my_site
   ```

4. **Start (or restart) the web UI**:

   ```bash
   python manage.py runserver
   ```

5. Open `http://localhost:8000/`, where `my_site` will appear in the projects list. Click it to open the detail page with interactive graphs.

When you click **“Start simulatie”** or **“Simulatie opnieuw draaien”** in the web UI, it effectively calls the same pipeline you would run via:

```bash
python -m energy_pipeline.scripts.run_pipeline data/projects/<project_name> -o output/<project_name>
```

The web UI then reloads the project detail page once results are ready.

### Interactive “CSI-style” graph exploration

On a project detail page (`/project/<name>/`) the **Grafieken** section shows a small set of sampled time-series plots (by default the first 14 days):

- Consumption / demand
- PV generation
- Generator dispatch
- Battery state of charge
- Marginal price

These graphs are built with Chart.js and support **interactive exploration**:

- **Zoom & pan on the top graph**
  - Use the mouse wheel to zoom in/out horizontally (time axis).
  - Click and drag horizontally to draw a zoom box.
  - Click and drag (no box) to pan left/right.
- **Linked time range across all graphs**
  - The **top graph acts as the master timeline**.
  - Whenever you zoom or pan on the top graph, **all other graphs automatically use the same time window**, so you can visually compare signals over the exact same period (e.g. verify if load, SOC and prices line up correctly).
- **Reset zoom**
  - Above the charts there is a small toolbar with a **“Reset zoom”** button.
  - Clicking it resets **all charts** back to the original full sampled period.

This is intended as a “CSI-style” exploration tool: start from the full year (or sampled subset), zoom down into a period of interest (week, day, hour) on the first graph, and immediately see how the other series behave over that exact time window.

## Workflow

The pipeline is split into two stages so you can run normalization and PyPSA analysis separately.

### 1. Normalize (raw → consumption_profile.csv)

Parse raw consumption data and write the normalized profile (timestamp, power_kw) to CSV. No PyPSA run.

```bash
python -m energy_pipeline.scripts.run_pipeline /path/to/consumption.xlsx --output-dir ./output --no-simulate
```

Output: `output/consumption_profile.csv`, `output/consumption_profile_hourly.csv`. If the project folder has a `pv` subfolder with supported files: `output/pv_generation_profile.csv`, `output/pv_generation_profile_hourly.csv`. With `--save-normalized` and folder input: also `output/temp/ean_quarterly_{EAN}.csv` and `output/temp/ean_hourly_{EAN}.csv` per EAN.

### 2. Simulation (consumption_profile.csv → results)

Run a simulator on existing normalized data. Use when you already have `consumption_profile.csv` (e.g. from step 1).

```bash
# PyPSA (optimization)
python -m energy_pipeline.scripts.run_pypsa ./output
# Or: python -m energy_pipeline.scripts.run_pypsa ./output/consumption_profile.csv

# EA Sim (rule-based)
python -m energy_pipeline.scripts.run_ea_sim ./output
```

Output: `output/pypsa_results/` (same CSV layout for both, so visualizations work for either).

### 3. Full pipeline (normalize + simulate in one go)

```bash
# Default simulator from config (usually pypsa)
python -m energy_pipeline.scripts.run_pipeline /path/to/consumption.xlsx --output-dir ./output

# Force EA Sim
python -m energy_pipeline.scripts.run_pipeline /path/to/consumption.xlsx -o ./output --simulator ea_sim
```

### 4. Visualize

Generate PDF and PNG plots from an output directory (with or without PyPSA results).

```bash
python -m energy_pipeline.scripts.visualize ./output --days 14
```

---

## Command reference

### `run_pipeline` — Parse, normalize, and optionally run a simulator


| Option              | Short   | Default                         | Description                                                                                                                      |
| ------------------- | ------- | ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `input`             | —       | *(required)*                    | Path to raw consumption file or project folder (folder = all files aggregated)                                                   |
| `--output-dir`      | `-o`    | `./output`                      | Directory for normalized profile and results                                                                                     |
| `--adapter`         | `-a`    | *(auto-detect)*                 | Adapter name: `adams_meetdata`, `belgian_dso`, `historiek_dagtotalen`, `pvgis_timeseries`, `smulders_offtake`                    |
| `--resample`        | `-r`    | `15min`                         | Resample rule: `15min`, `1h`, `1H`, etc.                                                                                         |
| `--config`          | `-c`    | `config/default.yaml`           | Path to config YAML                                                                                                              |
| `--simulator`       | `-s`    | *from config*                   | Simulation engine: `pypsa` or `ea_sim`. Overrides `simulator:` in config.                                                        |
| `--no-simulate`     | —       | —                               | Only normalize; do not run any simulator                                                                                         |
| `--visualize`       | `--viz` | —                               | Generate PDF and PNG after run                                                                                                   |
| `--images-dir`      | —       | *output-dir/images*             | Directory for PNG images                                                                                                         |
| `--pdf`             | —       | *output-dir/results_report.pdf* | Path for combined PDF                                                                                                            |
| `--viz-days`        | —       | `14`                            | Days to plot (0 = all)                                                                                                           |
| `--year`            | —       | —                               | Limit analysis to a specific calendar year (YYYY).                                                                               |
| `--month`           | —       | —                               | Optional: month (1–12) or comma-separated list of months (e.g. `1,2,3`). Requires `--year`.                                      |
| `--verbose`         | `-v`    | —                               | Verbose solver/simulation output                                                                                                 |
| `--save-monthly`    | —       | —                               | Also save monthly aggregated profile to `consumption_profile_monthly.csv`                                                        |
| `--save-normalized` | —       | —                               | Save per-EAN normalized profiles to `output-dir/temp/` (folder input only): `ean_quarterly_{EAN}.csv` and `ean_hourly_{EAN}.csv` |
| `--rebuild`         | —       | —                               | Ignore temp cache: re-parse all inputs and overwrite temp files (removes and recreates `temp/`)                                  |


**PV folder (folder input)**  
When `input` is a folder, the pipeline looks for a `pv` subfolder in the project dir or in `output-dir`. If present, all supported files in `pv/` are parsed (e.g. PVGIS timeseries CSV), aggregated, and written to `pv_generation_profile.csv` and `pv_generation_profile_hourly.csv`. If consumption extends beyond the PV period, PV is projected from the last available year (see [PV projection](#pv-projection)).

**Temp cache (folder input)**  
When `input` is a folder, the pipeline checks `output-dir/temp/` for existing `ean_quarterly_{EAN}.csv` (EAN = 18-digit ID from filename). If a file exists and `--rebuild` is not set, that EAN is loaded from the CSV and the original file is skipped, saving processing time. Use `--save-normalized` to write/update these files and `--rebuild` to force a full re-parse and overwrite.

**Examples**

```bash
# Normalize only (no PyPSA)
python -m energy_pipeline.scripts.run_pipeline data/file.xlsx -o ./output --no-simulate

# Full pipeline with hourly resampling
python -m energy_pipeline.scripts.run_pipeline data/file.xlsx -o ./output --resample 1h

# Force adapter and custom config
python -m energy_pipeline.scripts.run_pipeline data/file.xlsx --adapter adams_meetdata --config config/custom.yaml

# Run and visualize (14 days)
python -m energy_pipeline.scripts.run_pipeline data/file.xlsx -o ./output --visualize --viz-days 14

# Project folder: consumption files in folder root; optional pv/ subfolder for PV generation (same adapters)
# Output: consumption_profile.csv, consumption_profile_hourly.csv; if pv/ exists: pv_generation_profile.csv, pv_generation_profile_hourly.csv
python -m energy_pipeline.scripts.run_pipeline data/projects/smulders -o ./output/smulders --save-normalized --no-simulate

# Limit analysis to a specific year (e.g. 2025)
python -m energy_pipeline.scripts.run_pipeline data/projects/smulders -o ./output/smulders --year 2025

# Limit analysis to specific months within a year (e.g. Jan–Mar 2025)
python -m energy_pipeline.scripts.run_pipeline data/projects/smulders -o ./output/smulders --year 2025 --month 1,2,3

# Force re-parse and overwrite temp files
python -m energy_pipeline.scripts.run_pipeline data/projects/smulders -o ./output/smulders --save-normalized --rebuild

# Use EA Sim instead of PyPSA (rule-based)
python -m energy_pipeline.scripts.run_pipeline data/projects/smulders -o ./output/smulders --simulator ea_sim

# Normalize + PyPSA explicitly (default when simulator not set in config)
python -m energy_pipeline.scripts.run_pipeline data/projects/smulders -o ./output/smulders --simulator pypsa
```

---

### `run_pypsa` — Run PyPSA on existing normalized data


| Option         | Short   | Default                        | Description                                                         |
| -------------- | ------- | ------------------------------ | ------------------------------------------------------------------- |
| `input`        | —       | *(required)*                   | Directory containing `consumption_profile.csv`, or path to that CSV |
| `--config`     | `-c`    | `config/default.yaml`          | Path to config YAML                                                 |
| `--visualize`  | `--viz` | —                              | Generate PDF and PNG after optimization                             |
| `--images-dir` | —       | *input-dir/images*             | Directory for PNG images                                            |
| `--pdf`        | —       | *input-dir/results_report.pdf* | Path for combined PDF                                               |
| `--viz-days`   | —       | `14`                           | Days to plot (0 = all)                                              |
| `--verbose`    | `-v`    | —                              | Verbose solver output                                               |


**Examples**

```bash
# Run PyPSA on output from a previous normalize step
python -m energy_pipeline.scripts.run_pypsa ./output

# Run and visualize
python -m energy_pipeline.scripts.run_pypsa ./output --visualize --viz-days 7

# Use custom config
python -m energy_pipeline.scripts.run_pypsa ./output --config config/custom.yaml
```

**Note:** `run_pypsa` does not load `pv_generation_profile.csv`; it only uses consumption. For PV in PyPSA, run the full pipeline (`run_pipeline` with the project folder so PV is processed and passed to the optimizer).

---

### `run_ea_sim` — Run EA Sim on existing normalized data

Standalone entry point for the EnergyLabs rule-based BESS simulator. Uses `consumption_profile.csv` and optionally `pv_generation_profile.csv` from the given directory. Results are written to `pypsa_results/` in the same format as PyPSA so `visualize` works unchanged.


| Option         | Short   | Default                        | Description                                                                                 |
| -------------- | ------- | ------------------------------ | ------------------------------------------------------------------------------------------- |
| `input`        | —       | *(required)*                   | Directory containing `consumption_profile.csv` (and optionally `pv_generation_profile.csv`) |
| `--config`     | `-c`    | `config/default.yaml`          | Path to config YAML (uses `ea_sim:` section)                                                |
| `--visualize`  | `--viz` | —                              | Generate PDF and PNG after simulation                                                       |
| `--images-dir` | —       | *input_dir/images*             | Directory for PNG images                                                                    |
| `--pdf`        | —       | *input_dir/results_report.pdf* | Path for combined PDF                                                                       |
| `--viz-days`   | —       | `14`                           | Days to plot (0 = all)                                                                      |
| `--verbose`    | `-v`    | —                              | Verbose simulation output                                                                   |


**Examples**

```bash
# Run EA Sim on existing output directory
python -m energy_pipeline.scripts.run_ea_sim ./output/smulders

# With visualization
python -m energy_pipeline.scripts.run_ea_sim ./output/smulders --visualize --viz-days 7

# Custom config
python -m energy_pipeline.scripts.run_ea_sim ./output --config config/custom.yaml
```

---

### `visualize` — Plot existing pipeline output


| Option         | Short | Default                         | Description                                                      |
| -------------- | ----- | ------------------------------- | ---------------------------------------------------------------- |
| `output_dir`   | —     | `./output`                      | Directory with `consumption_profile.csv` and/or `pypsa_results/` |
| `--images-dir` | —     | *output_dir/images*             | Directory for PNG images                                         |
| `--pdf`        | —     | *output_dir/results_report.pdf* | Path for combined PDF                                            |
| `--days`       | —     | `14`                            | Days to plot (0 = all)                                           |


**Examples**

```bash
python -m energy_pipeline.scripts.visualize ./output
python -m energy_pipeline.scripts.visualize ./output --days 0 --pdf ./report.pdf
```

---

## Visualizations

With `--visualize` (run_pipeline or run_pypsa) or the standalone `visualize` script:

- **PNG images** in `output/images/`: consumption, **consumption and PV** (Verbruik en PV-generatie, if PV profile exists), grid dispatch, battery SOC, marginal price, charge/discharge
- **Distribution cost tables** (EA Sim only): summary and monthly breakdown per calculator as PNG (`dist_costs_summary_<calculator>.png`, `dist_costs_monthly_<calculator>.png`)
- **Combined PDF** at `output/results_report.pdf` (same set of plots, including consumption+PV when available, plus distribution cost tables when `ea_sim_distribution_costs.csv` exists)

Use `--viz-days N` / `--days N` to limit plots to the first N days (default 14). Use `0` to plot all data.

When `pv_generation_profile.csv` exists, an extra chart **Verbruik en PV-generatie** (consumption and PV in one graph) is generated; it includes a footnote that PV may be projected for missing years (simulation only).

---

## Simulators

Two simulation engines are available. The pipeline uses the one set by `--simulator` (run_pipeline) or by `simulator:` in the config file; default is `pypsa`.


| Simulator  | Description                                                                                                                                                                      | When to use                                                 |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| **pypsa**  | Cost-based linear optimization: grid (ToU), battery (extendable), PV (curtailment allowed). Minimizes total cost; outputs optimal battery size (MW) and dispatch.                | Sizing, cost comparison, research.                          |
| **ea_sim** | Rule-based BESS: fixed battery capacity and power; strategies `peak_shaving` or `pv_self_consumption`. Uses Belgian distribution-cost parameters. Exports PyPSA-compatible CSVs. | Quick scenarios, fixed BESS specs, distribution-cost focus. |


- **Config:** PyPSA uses top-level and `battery:` in YAML; EA Sim uses the `ea_sim:` block (see [Configuration](#configuration)).
- **Output:** Both write to `output_dir/pypsa_results/` so the same visualizations apply.

### Distribution cost calculators

EA Sim runs **all registered distribution cost calculators** on every scenario (baseline, with_pv, with_pv_bess) and exports a combined CSV with a `calculator` column. Available calculators:


| Calculator        | Description                                                                                                                                                                                                                          |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `belgian_general` | Analyst formula (March 2026). Supports stationary and non-stationary BESS. Stationary mode applies Elia 80% discount `(1 − 0.8·E)` on access and peak power costs. Non-stationary includes ODV and surcharges inside the tariff cap. |
| `static_qv`       | Original simplified 2026 formula (backward compatible). Does not distinguish stationary/non-stationary.                                                                                                                              |


**Formulas** (Z = absolute monthly cost, G(Z) = Z/X = relative cost):

- **Non-stationary**: `Z = min(150.8082·X, A·V·D/365 + Y·P·D/365 + (O+T)·X) + 29.14·X + 1.751·I + 7.2896·D`
- **Stationary**: `Z = min(150.8082·X, A·V·(1−0.8·E)·D/365 + Y·P·(1−0.8·E)·D/365) + 29.14·X + 1.751·I + 7.2896·D`

Where X = offtake (MWh), Y = peak (MW), A = access power (MW), I = injection (MWh), D = days, V = connection price, P = peak price, O = ODV, T = surcharges, E = Elia fraction.

The primary calculator is set via `distribution_costs.calculator` in the config; all registered calculators run for comparison.

---

## PV generation

### Folder and files

- **Location**: The pipeline looks for a `pv` subfolder in either the **project directory** (`data/projects/<name>/pv/`) or the **output directory** (`output/<name>/pv/`). So you can place PV files next to consumption in the project, or in the output folder (e.g. after exporting PV there).
- **Formats**: Supported formats include:
  - **PVGIS timeseries CSV**: CSV with metadata header and a row `time,P,...` (time as `YYYYMMDD:HHMM`, P in W). Detected by content; multiple files are aggregated.
  - Other adapters (Belgian DSO, Historiek, etc.) can be used if the PV data matches their format.
- **Output**: Same resolution as consumption (e.g. 15 min), plus hourly:
  - `pv_generation_profile.csv` — normalized PV (timestamp, power_kw)
  - `pv_generation_profile_hourly.csv` — hourly version  
  With `--save-monthly`: `pv_generation_profile_monthly.csv`.

### Use in simulation

- **PyPSA:** PV is added as a generator with upper bound = profile (curtailment allowed when PV exceeds load + export + charge capacity). Grid can export (`p_min_pu=-1`). Net load is balanced by PV + grid + battery.
- **EA Sim:** Uses `pv_generation_profile.csv` when present for the `pv_self_consumption` (and related) strategy.

### PV projection

PV data (e.g. from PVGIS) often ends in 2023 while consumption runs into 2025. To still run the simulation:

- The pipeline **automatically projects** PV for missing timestamps: it takes the **last full year** of PV (e.g. 2023) and reuses its (month, day, hour) pattern for any consumption timestamps beyond the PV range. So 2025 is simulated with 2023’s PV pattern.
- A message is printed: *"PV data does not cover full consumption period; projected from last available year to match (simulation only, not directly comparable)."*
- The combined visualization (Verbruik en PV-generatie) includes a footnote that PV may be projected and is for simulation only.

**Important**: Projected PV is only for running the optimization over the full consumption period. It is not a real 2025 PV series and should not be used for direct comparison with measured data.

---

## Configuration

Edit `config/default.yaml`. Structure:

### Global / PyPSA


| Key                                            | Description                                                                                                                                                  |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `simulator`                                    | `"pypsa"` or `"ea_sim"` — default engine when running `run_pipeline` (overridable by `--simulator`)                                                          |
| `resample_rule`                                | Normalization resolution: `"15min"`, `"1H"`, etc.                                                                                                            |
| `peak_start_hour`, `peak_end_hour`             | Peak window for ToU (PyPSA only)                                                                                                                             |
| `peak_price_eur_mwh`, `off_peak_price_eur_mwh` | ToU prices €/MWh (PyPSA only)                                                                                                                                |
| `grid_p_nom_mw`                                | Grid import/export limit (MW)                                                                                                                                |
| `battery`                                      | PyPSA battery block: `round_trip_efficiency`, `max_hours`, `p_nom_max_mw`, `capital_cost_eur_kwh`, `inverter_cost_eur_kw`, `lifetime_years`, `discount_rate` |


### EA Sim (`ea_sim:` block)


| Key                       | Description                                                                                                                       |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `battery_capacity_kwh`    | BESS energy capacity (kWh)                                                                                                        |
| `battery_power_kw`        | Charge/discharge power limit (kW); `null` = capacity / 2                                                                          |
| `round_trip_efficiency`   | Round-trip efficiency                                                                                                             |
| `connection_capacity_kw`  | Grid connection / contracted capacity (kVA)                                                                                       |
| `injection_limit_kw`      | Max grid injection (kW)                                                                                                           |
| `high_power_threshold_kw` | Peak-shaving threshold; `null` = 70% of connection                                                                                |
| `strategy`                | `"peak_shaving"` or `"pv_self_consumption"`                                                                                       |
| `off_peak_battery`        | If `true`: during off-peak hours charge from grid to build buffer for next day (based on previous day's peaks). Default: `false`. |
| `off_peak_start_hour`     | Hour when off-peak starts (e.g. `21` = 21:00). Default: `21`.                                                                     |
| `off_peak_end_hour`       | Hour when off-peak ends (e.g. `7` = 07:00). Default: `7`.                                                                         |
| `distribution_costs`      | Belgian distribution cost parameters (see below)                                                                                  |


### Distribution costs (`ea_sim.distribution_costs:` block)


| Key                           | Description                                                                                                                          |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `enabled`                     | Enable distribution cost calculation (`true`/`false`)                                                                                |
| `calculator`                  | Calculator to use: `"belgian_general"` (analyst formula) or `"static_qv"` (original). All calculators run for comparison regardless. |
| `is_stationary`               | `true` = stationary BESS (Elia 80% discount), `false` = non-stationary                                                               |
| `elia_fraction`               | E — Elia transmission fraction (only relevant when `is_stationary: true`)                                                            |
| `connection_price_eur_per_mw` | V — price per MW access power (€/MW/year, region-dependent)                                                                          |
| `peak_price_eur_per_mw`       | P — price per MW peak power (€/MW/year, region-dependent)                                                                            |
| `odv_eur_per_mwh`             | O — public service obligations (€/MWh, non-stationary only)                                                                          |
| `surcharge_eur_per_mwh`       | T — surcharges (€/MWh, non-stationary only)                                                                                          |
| `offtake_base_eur_per_mwh`    | Base offtake cost: taxes 12.09 + certificates 15.2 + grid losses 1.85 = 29.14                                                        |
| `injection_eur_per_mwh`       | Injection tariff (1.751 €/MWh)                                                                                                       |
| `fixed_daily_eur`             | Fixed daily costs: data mgmt 0.316 + energy fund 6.316 + admin 0.6576 = 7.2896                                                       |
| `capacity_cap_eur_per_mwh`    | Maximum tariff cap (150.8082 €/MWh, excl. taxes & green certificates)                                                                |
| `stationary_discount`         | Elia discount factor for stationary BESS (0.8 = 80%)                                                                                 |


## Project structure

```
src/energy_pipeline/
├── schema.py              # ConsumptionProfile, NormalizedConfig, from_csv
├── adapters/
│   ├── base.py            # AdapterRegistry, ConsumptionAdapter protocol
│   ├── adams_meetdata.py   # Adams meetdata Excel (2 meters summed)
│   ├── belgian_dso.py     # Belgian DSO Excel adapter
│   ├── historiek_dagtotalen.py  # Historiek dagtotalen CSV adapter
│   ├── pvgis_timeseries.py # PVGIS timeseries CSV (time, P in W)
│   └── smulders_offtake.py # Smulders offtake Excel (EAN in filename)
├── normalizer.py          # Resampling, project_pv_to_consumption_dates
├── scripts/
│   ├── run_pipeline.py    # Parse + normalize + run simulator (pypsa or ea_sim)
│   ├── run_pypsa.py       # PyPSA only (from consumption_profile.csv)
│   ├── run_ea_sim.py      # EA Sim only (from consumption_profile.csv, optional PV)
│   └── visualize.py       # Plot existing output
├── simulation/
│   ├── battery_config.py       # load_config, load_ea_sim_config, load_raw_config
│   ├── distribution_costs.py   # DistCostParams, BelgianGeneralCalculator, StaticQvCalculator, registry
│   ├── pypsa_builder.py        # build_and_optimize (PyPSA)
│   └── ea_sim.py               # EaSimConfig, simulate (rule-based BESS)
└── visualization/
    └── plots.py
config/web
└── default.yaml           # simulator, battery, ea_sim, ToU, grid
```

## Tests

```bash
pytest tests/ -v
```

