#!/bin/bash

GPUs=(2 3 0 1)
gpu_counter=0

# Define default values for the arguments
MODEL_ID="bigcode/starcoder2-15b"
ITER=100
MAX_NEW_TOKENS=512
PROMPT_TYPES=("bare" "completion")

# Call the Python script with the defined arguments
for PROMPT_TYPE in "${PROMPT_TYPES[@]}"; do
    gpu=${GPUs[$gpu_counter]}
    echo "Running model: $MODEL_ID, on GPU: $gpu"
    CUDA_VISIBLE_DEVICES=$gpu python run_inference_gad.py \
    --model_id "$MODEL_ID" \
    --cache_dir "/nobackup2/yf/mila/GD_caches/" \
    --num_return_sequences 1 \
    --repetition_penalty 1.0 \
    --iter $ITER \
    --temperature 1.0 \
    --top_p 1.0 \
    --top_k 0 \
    --max_new_tokens $MAX_NEW_TOKENS \
    --prompt_type "$PROMPT_TYPE" \
    --output_folder "/nobackup2/yf/mila/GD/results/" \
    --base_grammar_dir "/nobackup2/yf/mila/GD/examples/sygus/" \
    --instruct_prompt_file "/nobackup2/yf/mila/GD/prompts/pre_prompt.jsonl" \
    --grammar_prompt_file "/nobackup2/yf/mila/GD/benchmarks/comp/2018/PBE_BV_Track/PRE_100_10.sl" \
    --dtype "float32" \
    --device "cuda" &
    let "gpu_counter = (gpu_counter + 1) % ${#GPUs[@]}"
done

wait

echo "All experiments have finished."