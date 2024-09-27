import torch
import json
import pickle
from transformers.generation.logits_process import LogitsProcessorList, InfNanRemoveLogitsProcessor
from transformers_gad.grammar_utils import IncrementalGrammarConstraint
from transformers_gad.generation.gad_logits_processor import GrammarAlignedOracleLogitsProcessor
from transformers_gad.oracle.oracle_trie import Trie, TrieNode, update_oracle_trie
import os
from inference_utils import get_file, load_model_tokenizer_hf
import matplotlib.pyplot as plt
import numpy as np
import json
from tqdm import tqdm
import time
from datetime import datetime
from inference_utils import (get_file,
                             load_model_tokenizer_hf,
                             get_prompt,
                             get_grammar_file_path_by_prompt_type,
                             save_trie_to_pkl,
                             construct_trie_file,
                             construct_sygus_prompt,
                             construct_trie_file_from_folder,
                             get_prompt_in_test_folder,
                             fix_seed)

from arg_parser import ArgumentParser

def load_oracle_trie(trie_file):
    with open(trie_file, 'rb') as f:
        trie = pickle.load(f)
    return trie

def construct_gad_output_file_path(args):
    model_name = args.model_id.split("/")[-1]
    grammar_prompt_file = args.grammar_prompt_file.split("/")[-1]
    grammar_prompt_name = grammar_prompt_file.split(".")[0]
    output_file_path = os.path.join(args.output_folder,
                                    f"gad_g-{grammar_prompt_name}_{model_name}_p-{args.prompt_type}_i{args.iter}_{args.device}_sd{args.seed}_{args.dtype}.jsonl")
    output_directory = os.path.dirname(output_file_path)
    # Ensure the directory exists
    if not os.path.exists(output_directory):
        os.makedirs(output_directory)

    return output_file_path

def construct_gad_output_file_path_from_folder(args, test_filename):
    model_name = args.model_id.split("/")[-1]
    output_file_path = os.path.join(args.output_folder, f"{test_filename}")
    output_file_path = os.path.join(output_file_path, f"gad_{model_name}_i{args.iter}_{args.device}_sd{args.seed}_{args.dtype}.jsonl")
    output_directory = os.path.dirname(output_file_path)
    # Ensure the directory exists
    if not os.path.exists(output_directory):
        os.makedirs(output_directory)

    return output_file_path

@torch.inference_mode()
def inference_gad(args, model, tokenizer, prompt, grammar_str, trie):
    """
    latest version of gad test function prepared for run inference for iterations
    """
    grammar = IncrementalGrammarConstraint(grammar_str, "root", tokenizer)
    gad_oracle_processor = GrammarAlignedOracleLogitsProcessor(grammar, trie)
    inf_nan_remove_processor = InfNanRemoveLogitsProcessor()
    logits_processors = LogitsProcessorList([
        inf_nan_remove_processor,
        gad_oracle_processor,
    ])

    # Tokenize prompt into ids
    input_ids = tokenizer(
        [prompt], add_special_tokens=False, return_tensors="pt", padding=True
    )["input_ids"]
    input_ids = input_ids.to(model.device)

    # Generate sequences
    output = model.generate(
        input_ids,
        do_sample=True,
        pad_token_id=tokenizer.eos_token_id,
        eos_token_id=tokenizer.eos_token_id,
        # num_beams=args.num_beams,
        max_new_tokens=args.max_new_tokens,
        top_p=args.top_p,
        top_k=args.top_k,
        temperature=args.temperature,
        logits_processor=logits_processors,
        repetition_penalty=args.repetition_penalty,
        # early_stopping=True,
        num_return_sequences=args.num_return_sequences,
        return_dict_in_generate=True,
        output_scores=True,
    )

    input_length = 1 if model.config.is_encoder_decoder else input_ids.shape[1]
    generated_tokens = output.sequences[:, input_length:]
    acceptance_details_history = gad_oracle_processor.acceptance_details_history
    adjusted_acceptance_details_history = gad_oracle_processor.adjusted_acceptance_details_history
    generations = tokenizer.batch_decode(generated_tokens, skip_special_tokens=True)

    transition_scores = model.compute_transition_scores(
        output.sequences, output.scores, normalize_logits=True
    )

    metas = []
    sum_log_prob = 0
    for tok, score in zip(generated_tokens[0], transition_scores[0]):
        meta = {
            "token_id": int(tok),
            "token_str": tokenizer.decode(tok),
            "norm_score": float(score.cpu().numpy()),
            "prob": float(np.exp(score.cpu().numpy()))
        }
        metas.append(meta)
        sum_log_prob += float(score.cpu().numpy())
    # print(f"grammar constrained generations: {generations}")
    return generated_tokens, acceptance_details_history, adjusted_acceptance_details_history, generations, metas, sum_log_prob

@torch.inference_mode()
def initial_search_gad(args, model, tokenizer, prompt, grammar_str, trie):
    """
    latest version of gad test function prepared for run inference for iterations
    """

    # Tokenize prompt into ids
    input_ids = tokenizer(
        [prompt], add_special_tokens=False, return_tensors="pt", padding=True
    )["input_ids"]
    input_ids = input_ids.to(model.device)
    orig_input_ids = input_ids

    grammar = IncrementalGrammarConstraint(grammar_str, "root", tokenizer)
    gad_oracle_processor = GrammarAlignedOracleLogitsProcessor(grammar, trie, input_ids.size(1))
    inf_nan_remove_processor = InfNanRemoveLogitsProcessor()
    logits_processors = LogitsProcessorList([
        inf_nan_remove_processor,
        gad_oracle_processor,
    ])

    best_prefix = trie.find_best_prefix()
    if (best_prefix is not None):
        prefix = torch.Tensor(best_prefix.prefix)
        prefix = prefix.reshape([1, prefix.size(0)]).to(torch.int64).to(model.device)

        input_ids = torch.cat((input_ids, prefix), dim=1)

    # Generate sequences
    output = model.generate(
        input_ids,
        do_sample=True,
        pad_token_id=tokenizer.eos_token_id,
        eos_token_id=tokenizer.eos_token_id,
        # num_beams=args.num_beams,
        max_new_tokens=args.max_new_tokens,
        top_p=args.top_p,
        top_k=args.top_k,
        temperature=args.temperature,
        logits_processor=logits_processors,
        repetition_penalty=args.repetition_penalty,
        # early_stopping=True,
        num_return_sequences=args.num_return_sequences,
        return_dict_in_generate=True,
        output_scores=True,
    )

    input_length = 1 if model.config.is_encoder_decoder else orig_input_ids.shape[1]
    generated_tokens = output.sequences[:, input_length:]
    acceptance_details_history = gad_oracle_processor.acceptance_details_history
    adjusted_acceptance_details_history = gad_oracle_processor.adjusted_acceptance_details_history
    generations = tokenizer.batch_decode(generated_tokens, skip_special_tokens=True)

    transition_scores = model.compute_transition_scores(
        output.sequences, output.scores, normalize_logits=True
    )

    metas = []
    sum_log_prob = 0
    for tok, score in zip(generated_tokens[0], transition_scores[0]):
        meta = {
            "token_id": int(tok),
            "token_str": tokenizer.decode(tok),
            "norm_score": float(score.cpu().numpy()),
            "prob": float(np.exp(score.cpu().numpy()))
        }
        metas.append(meta)
        sum_log_prob += float(score.cpu().numpy())
    # print(f"grammar constrained generations: {generations}")
    return generated_tokens, acceptance_details_history, adjusted_acceptance_details_history, generations, metas, sum_log_prob

@torch.inference_mode()
def run_inference_gad_loading_trie(args, test_filename, model, tokenizer):
    gad_output_file_path = construct_gad_output_file_path_from_folder(args, test_filename)
    if os.path.exists(gad_output_file_path):
        with open(gad_output_file_path, 'r', encoding='utf-8') as file:
            lines = file.readlines()
            if len(lines) >= args.iter:
                print(f"Skipping {gad_output_file_path} as it already contains {args.iter} or more lines.")
                return

    # Load prompt and grammar
    test_file = os.path.join(args.grammar_folder, f"{test_filename}.ebnf")
    with open(test_file, "r") as file:
        grammar_str = file.read()

    prompt = get_prompt_in_test_folder(args, test_filename)
    grammar_prompt_file = f"{test_filename}.sl"
    grammar_constr_name = f"{test_filename}.ebnf"

    start_time = time.time()

    with open(gad_output_file_path, 'w', encoding='utf-8') as outfile:
        # trie = load_oracle_trie(trie_file) # This is oracle trie constructed from gcd
        before_trie_status = "gad_before"
        after_trie_status = "gad_after"
        adjusted_trie_before = Trie()
        adjusted_trie_after = Trie()
        for _ in tqdm(range(args.initial_search), desc="Initial Search"):
            generated_tokens, acceptance_details_history, adjusted_acceptance_details_history, generations, metas, sum_log_prob = initial_search_gad(args, model, tokenizer, prompt, grammar_str, adjusted_trie_before)
            # print(f"generated_tokens: {generated_tokens}, acceptance_details_history: {acceptance_details_history}")
            _, updated_rate = update_oracle_trie(adjusted_trie_before, generated_tokens, acceptance_details_history)
            # update_oracle_trie(adjusted_trie_before, generated_tokens, adjusted_acceptance_details_history)
            update_oracle_trie(adjusted_trie_after, generated_tokens, adjusted_acceptance_details_history)

            result = {"answer": generations,
                      "sum_log_prob": sum_log_prob,
                      "metas": metas,
                      "updated_rate": updated_rate,
                      "prompt": prompt,
                      # "prompt_type": args.prompt_type,
                      "grammar_prompt": grammar_prompt_file,
                      "grammar_constr": grammar_constr_name,
                      }
            print(f"result: {result}")

            json_record = json.dumps(result)
            outfile.write(json_record + '\n')
            outfile.flush()
            os.fsync(outfile.fileno())            

        for _ in tqdm(range(args.iter - args.initial_search), desc="Running Inference"):
            generated_tokens, acceptance_details_history, adjusted_acceptance_details_history, generations, metas, sum_log_prob = inference_gad(args, model, tokenizer, prompt, grammar_str, adjusted_trie_before)
            # print(f"generated_tokens: {generated_tokens}, acceptance_details_history: {acceptance_details_history}")
            _, updated_rate = update_oracle_trie(adjusted_trie_before, generated_tokens, acceptance_details_history)
            # update_oracle_trie(adjusted_trie_before, generated_tokens, adjusted_acceptance_details_history)
            update_oracle_trie(adjusted_trie_after, generated_tokens, adjusted_acceptance_details_history)

            result = {"answer": generations,
                      "sum_log_prob": sum_log_prob,
                      "metas": metas,
                      "updated_rate": updated_rate,
                      "prompt": prompt,
                      # "prompt_type": args.prompt_type,
                      "grammar_prompt": grammar_prompt_file,
                      "grammar_constr": grammar_constr_name,
                      }
            print(f"result: {result}")

            json_record = json.dumps(result)
            outfile.write(json_record + '\n')
            outfile.flush()
            os.fsync(outfile.fileno())

    trie_file_before = construct_trie_file_from_folder(args, test_filename, before_trie_status)
    trie_file_after = construct_trie_file_from_folder(args, test_filename, after_trie_status)
    save_trie_to_pkl(adjusted_trie_before, trie_file_before)
    print(f"GAD before trie saved to {trie_file_before}")
    save_trie_to_pkl(adjusted_trie_after, trie_file_after)
    print(f"GAD after trie saved to {trie_file_after}")
    end_time = time.time()
    print(f"GAD results saved to {gad_output_file_path}")
    print(f"Total execution time: {end_time - start_time:.2f} seconds.")


if __name__ == "__main__":
    arg_parser = ArgumentParser(version="gad")
    args = arg_parser.parse_args()

    print(f"model_id: {args.model_id}")
    print(f"repetition_penalty: {args.repetition_penalty}")
    # print(f"num_beams: {args.num_beams}")
    print(f"temperature: {args.temperature}")
    print(f"top_p: {args.top_p}")
    print(f"max_new_tokens: {args.max_new_tokens}")
    print(f"initial_search: {args.initial_search}")

    model, tokenizer = load_model_tokenizer_hf(args)
    directory = args.test_folder
    for filename in os.listdir(directory):
        if filename.endswith(".sl"):
            test_filename = filename[:-3]
            print(f"test_filename: {test_filename}")
            fix_seed(args.seed)
            run_inference_gad_loading_trie(args, test_filename, model, tokenizer)
    
    print("GAD Inference Done!")


    # output, faithful, ideal, elapsed_time = run_inference_grammar_constrained_track_scores(args)
    # result, non_inf_scores_with_index, output_sequences, elapsed_time = run_inference_grammar_constrained_track_scores(args)
    # result, non_inf_scores_with_index, output_sequences, elapsed_time = run_inference_track_scores(args)
    # model, tokenizer = load_model_tokenizer_hf(args)
    # sequences, scores, generations = inference_track_scores(args, model, tokenizer)
    # print(f"sequences: {sequences}")
    # print(f"scores: {scores}")
    # print(f"generations: {generations}")

    ### run inference_gcd_get_logits_for_oracle ###
    # (sequences,
    #         scores,
    #         generations,
    #         generated_tokens,
    #         acceptance_details_history) = inference_gcd_get_logits_for_oracle(args, model, tokenizer)

    # print(f"sequences: {sequences}")
    # print(f"scores: {scores}")
    # print(f"generations: {generations}")
    # print(f"generated_tokens: {generated_tokens}")
    # print(f"acceptance_details_history: {acceptance_details_history}")


    # ### run inference_grammar_aligned_track_full_history ###
    # (sequences,
    #  scores,
    #  generations, accepted_tokens_history, accepted_indices_history, acceptance_raw_scores_history,
    #  acceptance_logits_history,
    #  acceptance_details_history, adjusted_acceptance_detailed_history) = inference_grammar_aligned_track_full_history(args, model, tokenizer, trie)
    #
    #
    # print(f"sequences: {sequences}")
    # print(f"scores: {scores}")
    # print(f"generations: {generations}")
    # print(f"accepted_tokens_history: {accepted_tokens_history}")
    # print(f"accepted_indices_history: {accepted_indices_history}")
    # print(f"acceptance_raw_scores_history: {acceptance_raw_scores_history}")
    # print(f"acceptance_logits_history: {acceptance_logits_history}")
    # print(f"acceptance_details_history: {acceptance_details_history}")
    # print(f"adjusted_acceptance_detailed_history: {adjusted_acceptance_detailed_history}")

    # ### run gad ###
    # run_inference_gad_loading_trie(args)





