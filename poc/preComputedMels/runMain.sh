#!/bin/bash -l

#Request gpu
#SBATCH --gres=gpu:1 --constraint=volta|pascal
#SBATCH --cpus-per-task=2
#SBATCH --mem-per-cpu 20G
#SBATCH -t 4:00:00

#Check priorty and current usage of nods
#slurm s gpu,gpushort

##########ssssSBATCH --gres=gpu:v100:1
###########SssssBATCH --gres=gpu:teslap100:1
#""" #SBATCH --gres=gpu:1 --constraint=pascal|volta

module purge
module load anaconda3/latest #has cuda 10

#source activate /scratch/work/falconr1/envs/audioTagging
source activate audioTagging

cd /scratch/work/falconr1/audioTagging2019/

hostname
conda info --envs
which python
python -c 'import librosa'
python -c "import os; print(os.listdir('./data/'))"
echo "init done"


srun python3 ./poc/preComputedMels/main.py





