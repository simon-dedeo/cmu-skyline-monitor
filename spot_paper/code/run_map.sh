#!/bin/bash
# Multi-start MAP fit for the elliptical pixel model (referee 2b, finding 2: the
# claimed multi-start optimize commands, checked in and executable).
# Usage: bash run_map.sh data.json  (CmdStan-format JSON from make_stan_data.py)
set -euo pipefail
DATA="${1:?usage: run_map.sh data.json}"
CMDSTAN="${CMDSTAN:-$HOME/cmdstan}"
MODEL_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$CMDSTAN" && make -s "$MODEL_DIR/spotell" >/dev/null
BEST_LP=-1e30; BEST=""
for SEED in 101 202 303 404 505 606 707 808; do
  OUT="/tmp/spotell_map_$SEED.csv"
  "$MODEL_DIR/spotell" optimize algorithm=lbfgs iter=4000 \
      data file="$DATA" random seed=$SEED output file="$OUT" >/dev/null 2>&1 || continue
  LP=$(grep -v '^#' "$OUT" | tail -1 | cut -d, -f1)
  echo "seed $SEED lp__=$LP"
  if python3 -c "exit(0 if float('$LP') > float('$BEST_LP') else 1)"; then
    BEST_LP=$LP; BEST=$OUT
  fi
done
[ -n "$BEST" ] || { echo "all starts failed"; exit 1; }
echo "best: $BEST (lp__=$BEST_LP)"
