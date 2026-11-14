#!/usr/bin/env bash
# Replay every scenario for seeds 1-5 and write runs/<scenario>_s<seed>.json, then the summary.
# usage: bash sim/run_all.sh [python]
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${1:-python}
mkdir -p runs
for sc in clean everyday outages; do
  for seed in 1 2 3 4 5; do
    "$PY" -m sim.run --scenario $sc --seed $seed --out runs/${sc}_s${seed}.json &
  done
  wait
done
"$PY" -m sim.summarise runs > runs/summary.md
