#!/usr/bin/env bash
#SBATCH --job-name=ottawa-rt
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=12:00:00
set -euo pipefail

run_id="${1:?usage: sbatch scripts/slurm-worker.sh RUN_ID IMAGE}"
image="${2:?container image is required}"
srun --container-image="${image}" --container-mounts="$(pwd)/data:/app/data" ottawa-rt worker "${run_id}"

