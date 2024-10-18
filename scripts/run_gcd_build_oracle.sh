#!/bin/bash

GPUs=(0 1 2 3 4 5 6 7)

# Define default values for the arguments
MODEL_IDS=("mistralai/Mistral-7B-Instruct-v0.2" "meta-llama/Llama-2-7b-chat-hf" "tiiuae/falcon-7b-instruct")
ITER=100
MAX_NEW_TOKENS=512
CACHE_DIR="/path/to/where/you/store/hf/models"
OUTPUT_FOLDER="results/SLIA_0506"
TEST_FOLDER="correct/SLIA"
PROMPT_FOLDER="prompts/SLIA"
TRIE_FOLDER="tries/SLIA_0506"
GRAMMAR_FOLDER="examples/sygus/SLIA"

function check_gpu_free {
    local gpu_id=$1
    local gpu_util=$(nvidia-smi -i $gpu_id --query-gpu=utilization.gpu --format=csv,noheader,nounits)
    local memory_total=$(nvidia-smi -i $gpu_id --query-gpu=memory.total --format=csv,noheader,nounits)
    local memory_used=$(nvidia-smi -i $gpu_id --query-gpu=memory.used --format=csv,noheader,nounits)
    # Calculate memory utilization percentage
    local memory_util=$(awk "BEGIN {print ($memory_used/$memory_total)*100}")
    # Check if both utilizations are below 50%
    [ $gpu_util -lt 50 ] && [ $(echo "$memory_util < 50" | bc) -eq 1 ]
}

# Call the Python script with the defined arguments
for MODEL_ID in "${MODEL_IDS[@]}"; do
    found_free_gpu=false
    while [ "$found_free_gpu" = false ]; do
        for gpu in "${GPUs[@]}"; do
            if check_gpu_free $gpu; then
                echo "GPU $gpu is free. Running model: $MODEL_ID, on GPU: $gpu"
                CUDA_VISIBLE_DEVICES=$gpu python run_inference_gcd_build_oracle_trie.py \
                --model_id "$MODEL_ID" \
                --cache_dir "$CACHE_DIR" \
                --num_return_sequences 1 \
                --repetition_penalty 1.0 \
                --iter $ITER \
                --temperature 1.0 \
                --top_p 1.0 \
                --top_k 0 \
                --max_new_tokens $MAX_NEW_TOKENS \
                --dtype "bfloat16" \
                --output_folder "$OUTPUT_FOLDER" \
                --test_folder "$TEST_FOLDER" \
                --prompt_folder "$PROMPT_FOLDER" \
                --trie_folder "$TRIE_FOLDER" \
                --grammar_folder "$GRAMMAR_FOLDER" \
                --seed 42 \
                --device "cuda" &
		sleep 60
                found_free_gpu=true
                break
            fi
        done
        if [ "$found_free_gpu" = false ]; then
            echo "All GPUs are busy. Waiting for 60 seconds..."
            sleep 60
        fi
    done
done

wait
echo "All experiments have finished."
