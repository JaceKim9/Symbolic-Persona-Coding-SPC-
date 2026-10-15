# SPC Experiment Harnesses

This directory contains the experiment harnesses for evaluating **Symbolic Persona Coding (SPC)** across multiple LLM providers under identical behavioural constraints[cite: 1].

---

## 1. Supported Models and API Providers
* **Claude Harness (`claud_spc_harness.py`)**: Designed for Anthropic Claude models using the official `anthropic` Python SDK.
* **Gemini Harness (`gemini_spc_harness.py`)**: Designed for Google Gemini models using the official `google-genai` SDK.
* **Grok / OpenAI-Compatible Harness (`spc_harness_grok_openai.py`)**: Designed for xAI Grok and any OpenAI-compatible endpoints using Python's built-in `urllib` (zero external dependencies for API calls).

---

## 2. Quickstart & Execution Guide

### Environment Variables
Depending on which harness you run, set the corresponding API key before execution:
```bash
export ANTHROPIC_API_KEY="your-anthropic-key"
export GEMINI_API_KEY="your-gemini-key"
export XAI_API_KEY="your-xai-key"          # For Grok
export OPENAI_API_KEY="your-openai-key"    # For OpenAI-compatible endpoints

Running Commands (Common across all scripts)
Self-test (No LLM required): Validates the AST compliance checker and hidden test grader.

Bash
python <script_name>.py selftest
Inspect Prompts: Prints out the generated prompt for each condition.

Bash
python <script_name>.py show-prompts
Dry Run / Pipeline Test: Tests the execution loop using the mock client (not real evidence).

Bash
python <script_name>.py run --model mock --reps 3
Real Evaluation Run: Executes the benchmark against a live model.

Bash
python <script_name>.py run --model <model_name> --reps 10 --out results.jsonl
Analysis: Computes performance metrics and paired bootstrap confidence intervals.

Bash
python <script_name>.py analyze --out results.jsonl --amortize 20

3. Dependencies and Version Information
Python Version: Python 3.8+ (Standard Library used for core AST parsing, subprocess sandboxing, and bootstrapping).

External Package Requirements:

Claude: pip install anthropic

Gemini: pip install google-genai

Grok/GPT: None (uses standard library urllib for raw HTTP requests).

4. Implementation Differences & Known Limitations
API Client Layer: Each script shares identical prompt construction logic (C0 through C6), AST security checks, and grading mechanisms, differing only in the client wrapper handling API communication.

Token Counting: Exact token counts depend on each provider's native tokenizer. Mock and fallback counters use a rough character length heuristic (len(text) // 4).

Sandbox Limitation: The test runner executes model-generated Python code using subprocess with resource limits (rlimit), which serves as a convenience guard rather than an airtight security sandbox. Always run real evaluations inside an isolated container or VM.

5. Self-Test vs. Real Model Evaluation
Self-Test (selftest): Programmatically verifies that reference solutions pass all hidden functional tests and that deliberate constraint-violation probes correctly fail compliance checks. It does not invoke any LLM.

Real Evaluation (run): Queries live LLM APIs across randomized condition orders and logs results to a resumable JSONL file for rigorous statistical bootstrapping.

6. Provenance & Versioning
Experimental Design: Version 0.1 pilot scale (6 core tasks, 7 encoding conditions: C0–C6).

For replication studies, record the specific Git commit hash alongside the output .jsonl result files.
