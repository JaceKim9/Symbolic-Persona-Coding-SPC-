# SPC Experiment Harnesses

Experiment harnesses for evaluating **Symbolic Persona Coding (SPC)** as an instruction-encoding format in LLM-based Python code generation. Each harness runs the same seven instruction conditions (C0–C6) against the same task set and the same grader, and differs only in the API client layer.

> **Status: v0.1 pilot, preliminary.** This repository contains no experimental results and makes no claim that SPC improves correctness, compliance, token efficiency, latency, or stability. The harnesses are implementation references for independent research teams. Verify them in your own environment before collecting confirmatory data.

---

## 1. Contents

| File | Provider | Client dependency |
|---|---|---|
| `claud_spc_harness.py` | Anthropic Claude | `anthropic` |
| `gemini_spc_harness.py` | Google Gemini | `google-genai` |
| `spc_harness_grok_openai.py` | xAI Grok and any OpenAI-compatible endpoint | none (standard-library `urllib`) |

The three files share one codebase: requirement registry, task set, prompt builders, grader, runner, and analysis are the same, and only the client class differs. They are therefore **not independent implementations**, and a defect in shared logic appears in all three.

## 2. Requirements

- **Python 3.9 or newer.** Reference solutions and the grading runner execute code that uses built-in generic annotations such as `dict[str, str]`, which Python 3.8 does not support.
- Provider SDK, only for the harness you use (see the table above):

```bash
pip install anthropic        # Claude harness
pip install google-genai     # Gemini harness
                             # Grok / OpenAI-compatible harness needs no extra package
```

- A container or VM. See [Safety](#6-safety).

## 3. Quickstart

Set the API key for the provider you will use:

```bash
export ANTHROPIC_API_KEY="..."   # Claude harness
export GEMINI_API_KEY="..."      # Gemini harness
export XAI_API_KEY="..."         # Grok (model ids starting with "grok" or "xai")
export OPENAI_API_KEY="..."      # other OpenAI-compatible endpoints
```

For the Grok/OpenAI-compatible harness, the endpoint defaults to `https://api.x.ai/v1` for model ids starting with `grok` or `xai`, and to `https://api.openai.com/v1` otherwise. Override with `XAI_BASE_URL` or `OPENAI_BASE_URL` to target another compatible server.

Commands are the same in every script (replace `<script>` with the file you are using):

```bash
python <script>.py selftest                       # validate grader and compliance checker; no LLM call
python <script>.py show-prompts                   # print the prompt of every condition for the first task
python <script>.py run --model mock --reps 3      # pipeline test with a mock client (not evidence)
python <script>.py run --model <model-id> --reps 10 --out results.jsonl   # real evaluation
python <script>.py analyze --out results.jsonl --amortize 20              # summaries and bootstrap CIs
```

`<model-id>` must be a real model identifier of the provider (for example, a Claude, Gemini, or Grok model name). Placeholders such as `openai` are not valid model ids.

Recommended order for a first session: `selftest` → `show-prompts` (inspect the prompts) → `run --model mock` → a single real call with `--reps 1` → the full run.

### Options

| Option | Default | Meaning |
|---|---|---|
| `--out` | `results.jsonl` (`gemini_results.jsonl` in the Gemini script) | Result log, appended one JSON object per run |
| `--model` | `mock` | Model id, or `mock` for the pipeline-test client |
| `--temperature` | `1.0` | Sampling temperature; `-1` omits the parameter (for models that reject it) |
| `--reps` | `5` | Repetitions per task and condition |
| `--conds` | `C0,C1,C2,C3,C4,C5,C6` | Comma-separated subset of conditions |
| `--seed` | `0` | Seed for job-order shuffling and bootstrap |
| `--amortize` | `20` | Number of requests assumed to share one C4 system prompt |
| `--boots` | `5000` | Bootstrap resamples |

Runs are shuffled and written incrementally. If a run is interrupted, rerun the same command: completed (condition, task, repetition) jobs are skipped.

## 4. Design

### Conditions

Every condition encodes the same requirement set (one role plus five checkable constraints). The task text is identical across conditions.

| Condition | Encoding | Purpose |
|---|---|---|
| C0 | Long natural-language instructions | Explicit baseline |
| C1 | Short natural-language instructions | Concise natural-language competitor |
| C2 | SPC symbols with inline definitions | Defined symbolic encoding |
| C3 | Same structure and identical definitions, plain-language labels instead of symbols | Principal comparison for notation (C2 vs C3) |
| C4 | SPC symbols, definitions in a reusable system prompt | Definition reuse and amortized cost |
| C5 | Structured JSON specification, no definitions | Structured-data representation |
| C6 | SPC symbols without definitions | Negative control for definition availability |

Audit the prompts for semantic equivalence (use `show-prompts`) before any real evaluation, especially C2 vs C3 and the C5 schema.

### Constraints (checked statically)

`code_only` (exactly one fenced code block, no text outside it), `no_io` (no file, network, OS, or subprocess access), `no_print`, `docstring`, `type_hints`.

### Tasks (hidden tests, never shown to the model)

Whitespace normalization, key-value parsing, interval merging, moving average, strict IPv4 validation, simplified CSV row parsing. Six tasks is a pilot-scale starting point and does not cover code generation broadly. Tests check output values, exception types, and whether the input was mutated.

### Recorded fields

Each result row stores the condition, task, repetition, model, temperature, raw reply, input and output tokens, estimated system-prompt tokens, latency in seconds, hidden-test counts, per-constraint compliance, a combined `joint` flag (all tests passed and all constraints satisfied), and any grader error.

### Analysis

`analyze` reports per-condition means (functional pass rate, compliance rate, `joint`, input/output/effective tokens, run-to-run instability) and differences between selected condition pairs with 95% two-level bootstrap intervals (tasks are resampled first, then repetitions within each task and condition). With only six tasks, wide intervals are expected and results must not be generalized.

## 5. Verification status of this version

| Item | Status |
|---|---|
| `selftest` (reference solutions pass, deliberate violations are caught) | Passes in all three scripts |
| Mock-client pipeline (`run --model mock`, then `analyze`) | Runs to completion in all three scripts |
| Real API call | **Record here per provider once tested:** Claude ☐ Gemini ☐ Grok/OpenAI-compatible ☐ |
| Empirical results | None |

A passing `selftest` shows only that the grader and checker behave as expected on the tested cases. It does not show that SPC is effective, that the conditions are semantically equivalent, or that the execution environment is secure.

## 6. Safety

The runner executes **model-generated code**. It uses a subprocess with a timeout and CPU/memory limits (`rlimit`, POSIX only), and the compliance checker is static AST inspection. These are convenience guards, not a security sandbox. Run real evaluations only inside an isolated container or VM, without credentials, secrets, or network access that you do not intend the generated code to have. Never commit API keys or include them in released logs.

## 7. Known limitations of v0.1

These are open issues in the current code, listed so that replication teams can account for them:

1. **Code-fence language is not verified.** `code_only` accepts any single fenced block, including a block labeled with another language or with no label.
2. **Latency is recorded but not summarized** by `analyze`; compute it from the result file.
3. **Failures are not recorded separately.** A provider error that survives the built-in retries stops the run (the run can be resumed). Empty, truncated, or blocked responses are graded as model failures, and the stop reason is not stored.
4. **Reported intervals cover `joint` and effective tokens only**; functional pass rate and compliance appear as means without intervals.
5. **Token accounting differs by client.** The Claude and Gemini scripts use provider token counting for the system prompt; the Grok/OpenAI-compatible script estimates it as `len(text) // 4`, so effective tokens for C4 mix measured and estimated values there. The Gemini fallback for missing usage data also ignores the system prompt. Prompt caching is not modeled.
6. **Reasoning/thinking models** may count internal tokens differently across providers, may reject `temperature`, and may exhaust the fixed `max_tokens=1024` before producing text. Check how your model and provider handle this before comparing token or correctness results.
7. **The `selftest` coverage differs:** the Claude script includes a probe for solutions that mutate their input or miss overlaps; the Gemini and Grok/OpenAI scripts do not yet.
8. **The static `no_io` check can be bypassed** (for example by indirect imports) and is not a security control.
9. **Single codebase.** The three harnesses are ports of one design; agreement between them is not independent confirmation.

## 8. Reproducibility record

For every evaluation, record: the repository URL and exact Git commit or release; the provider script used; the model id and version; decoding parameters and API settings; Python and dependency versions; the execution environment and isolation method; all prompt templates as actually submitted; the number of tasks, repetitions planned and completed; retry, timeout, and exclusion rules; and every local modification. Keep the raw `.jsonl` files so that all summaries can be regenerated.

## 9. Provenance

Experimental design version: 0.1 (pilot; six tasks; conditions C0–C6). The harness code was developed with AI assistance and has not been independently audited. See the accompanying paper for the full protocol, scope, and limitations.

## 10. License

See [`LICENSE`](../LICENSE) in the repository root.
