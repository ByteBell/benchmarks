#!/bin/zsh
# Run one gitnexus arm: the per-case gitnexus MCP server only.
# MCP_CONFIG is REQUIRED — see the gitnexus branch of run_retrieval_arm.sh for why
# (every cal.com commit registers under the same name, so the registry must be
# per-case or an arm can answer from another commit's index).
#
#   MCP_CONFIG=<case>/claudecli_opus5_mcp_gitnexus/mcp_gitnexus.json \
#     ./run_gitnexus_arm.sh <case_dir> <arm_name> [prompt_file] [--fresh]
#
# Mechanism, run conditions and the spent-arm guard all live in
# run_retrieval_arm.sh — this file only picks the tool surface. Phase 2 is
# separate:  python3 score_arm.py <case_dir> <arm_name>
SURFACE=gitnexus exec "${0:A:h}/run_retrieval_arm.sh" "$@"
