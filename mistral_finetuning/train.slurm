#!/bin/bash
#SBATCH --job-name=legal-qlora
#SBATCH --output=/scratch/kpate385/legalft/logs/%x-%j.out
#SBATCH --error=/scratch/kpate385/legalft/logs/%x-%j.err
#SBATCH -N 1
#SBATCH -c 5
#SBATCH --mem=60G
#SBATCH -t 0-08:00
#SBATCH -p public
#SBATCH -q class
#SBATCH -G 1                # <-- CONFIRM the MIG request line (see note below)

module load mamba/latest
source activate legalft
export NO_AI_TRACKING=true
unset PYTHONPATH
export HF_HOME=/scratch/kpate385/hf_cache
export HF_HUB_OFFLINE=1      # model is already cached; don't phone home on a compute node

mkdir -p /scratch/kpate385/legalft/logs
cd /scratch/kpate385/LLaMA-Factory
llamafactory-cli train /scratch/kpate385/legalft/mistral_qlora_sft.yaml
