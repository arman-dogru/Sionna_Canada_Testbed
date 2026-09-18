#!/usr/bin/env bash
set -euo pipefail

run_id="${1:?usage: run-dgx-workers.sh RUN_ID [IMAGE]}"
image="${2:-ottawa-sionna-rt:latest}"
mapfile -t devices < <(nvidia-smi -L | sed -n 's/.*(UUID: \(MIG-[^)]*\)).*/\1/p')
if ((${#devices[@]} == 0)); then
  mapfile -t devices < <(nvidia-smi --query-gpu=uuid --format=csv,noheader | tr -d ' ')
fi
if ((${#devices[@]} == 0)); then
  echo "No NVIDIA GPUs or MIG devices are visible" >&2
  exit 1
fi
mkdir -p data/runs/"${run_id}"/worker-logs

for index in "${!devices[@]}"; do
  device="${devices[$index]}"
  docker run --rm --gpus "device=${device}" \
    -e CUDA_VISIBLE_DEVICES=0 \
    -v "$(pwd)/data:/app/data" \
    "${image}" worker "${run_id}" \
    >"data/runs/${run_id}/worker-logs/worker-${index}.log" 2>&1 &
done
wait
