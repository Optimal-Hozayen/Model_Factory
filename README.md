# Model Factory

Desktop tool (PySide6 + OpenStudio SDK) to prepare OpenStudio models for
building energy studies. It sets the weather file, the envelope
constructions, the HVAC schedules, the output variables and the measures
(infiltration, thermal bridging, window-to-wall ratio), then builds, checks
and runs the model.

Author: Mahmoud Awad (mahmoud.awad@mail.concordia.ca)

## Requirements

- Python 3.11
- OpenStudio 3.11 (CLI) and the `openstudio` Python package of the same version
- Packages in `requirements.txt` (PySide6, openstudio, matplotlib)

## Running

Open the folder in PyCharm, select a Python 3.11 interpreter, install
`requirements.txt` and run `Run.py`.

Paths (base model, measures folder, weather folder, OpenStudio executable,
output folder) are set in the Project tab and saved in `settings.json`.

## Tabs

| Tab | Content |
|---|---|
| Project | Base model, measures folder, OpenStudio executable, output folder, summary of the base model |
| Weather | EPW file, design days (.ddy next to the EPW), ASHRAE climate zone, run period |
| Constructions | Construction bundles (ordered layers) for exterior walls, interior walls, roofs, interior slabs, ground walls, ground slabs, exposed floors, windows, exterior and interior doors |
| Schedules & HVAC | Schedules loaded from a folder (CSV, JSON, OSM) and assigned per zone group to thermostat setpoints and HVAC / heating / cooling availability |
| Outputs | Output variables and meters (heating, cooling, equipment, lighting, temperatures, ...) |
| Measures | Window-to-wall ratio per facade, NIST infiltration, TBD thermal bridging, reporting measures |
| Build & Run | Builds model.osm and workflow.osw, verification table, simulation and results |

## Constructions

Bundles are saved in `Library/Bundles` (one JSON file per bundle) and can be
reloaded, imported and exported. The material library is saved in
`Library/materials.json`.

Layer order (first row = first layer):

- exterior wall, roof, exposed floor, ground surfaces: outside to inside
- interior slab: top (floor finish) to bottom (ceiling finish)
- interior wall: side A to side B

The reversed construction is created automatically for the floor side of
interior slabs and for the adjacent side of asymmetric interior walls.

Bundles are assigned to every surface of the category (and to the default
construction sets), since the surfaces of the base model have hard-assigned
constructions.

## Verification

After each build the saved model is reloaded and checked:

1. all surfaces of the category use the bundle construction
2. the layers match the bundle
3. the U-value from OpenStudio matches the bundle U-value
4. the translated EnergyPlus IDF uses the construction (deep check)

After the simulation, the constructions used by EnergyPlus (including the TBD
derated ones) are read from `eplusout.sql`.

## Schedule files

- CSV: one schedule per numeric column, one full year (8760 hourly, 17520, 35040, ...)
- JSON: `{"name": "...", "type": "Temperature", "weekday": [24 values], "saturday": [...], "sunday": [...], "holiday": [...]}`
  or `{"name": "...", "type": "OnOff", "constant": 1}`
- OSM: all schedules of another OpenStudio model

The "Write example files" button creates examples in the selected folder.

## Folder structure

```
Run.py                  entry point
Core/                   engine (Builder.py is the build pipeline)
Controller/, GUI/, Tabs/  user interface
Library/                materials and construction bundles
Projects/               saved projects (created on first save)
Runs/<name>/            model.osm, workflow.osw, manifest.json, verification.txt,
                        run/eplusout.sql, reports/, results.json, summary.csv
tests/                  engine and GUI tests
```

`summary.csv` holds the main annual results (`HeatingDemand`, `CoolingDemand`,
`TotalEnergy`, `EUI`).
