#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_ROOT="${1:-${DATASETS_MODELS_DIR:-$REPO_ROOT/../Datasets/models}}"
ITERATION_DIR="${GS_ITERATION_DIR:-iteration_30000}"
DEST_ROOT="$REPO_ROOT/static/models"

if [[ ! -d "$SRC_ROOT" ]]; then
  echo "Source models directory not found: $SRC_ROOT" >&2
  echo "Usage: bash scripts/link_dataset_models.sh /absolute/path/to/Datasets/models" >&2
  exit 1
fi

mkdir -p "$DEST_ROOT"

linked=0
skipped=0

for model_dir in "$SRC_ROOT"/*; do
  [[ -d "$model_dir" ]] || continue

  model_id="$(basename "$model_dir")"
  src_ply="$model_dir/point_cloud/$ITERATION_DIR/point_cloud.ply"

  if [[ ! -f "$src_ply" ]]; then
    echo "[skip] $model_id: missing $src_ply"
    skipped=$((skipped + 1))
    continue
  fi

  out_dir="$DEST_ROOT/$model_id"
  out_ply="$out_dir/input.ply"
  mkdir -p "$out_dir"

  if [[ -e "$out_ply" && ! -L "$out_ply" ]]; then
    echo "[skip] $model_id: $out_ply exists and is not a symlink"
    skipped=$((skipped + 1))
    continue
  fi

  ln -sfn "$src_ply" "$out_ply"
  echo "[link] $model_id -> $out_ply"
  linked=$((linked + 1))
done

echo "Done. linked=$linked skipped=$skipped dest=$DEST_ROOT"
