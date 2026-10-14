#!/usr/bin/env python3
"""SPC minimal experiment harness v0.1 for Google Gemini (single file, stdlib only; `google-genai` required)

Question: for the SAME behavioural requirements, does an SPC-style symbolic prompt beat
(a) long natural language, (b) short natural language, (c) the same structure with plain
English labels, (d) a JSON spec, in total token cost, correctness and compliance?

Conditions (all encode the identical requirement set; only the encoding differs):
  C0 long natural language              C1 short natural language
  C2 SPC symbols + inline definitions   C3 SAME structure, plain labels + identical definitions  (key ablation)
  C4 SPC symbols, definitions in system prompt (amortised definition cost)
  C5 JSON spec, no definitions          C6 SPC symbols, NO definitions (negative control)

Commands:
  python gemini_spc_harness.py selftest                      # validate grader + checker (no LLM)
  python gemini_spc_harness.py show-prompts                  # inspect every condition's prompt
  python gemini_spc_harness.py run --model mock --reps 3     # dry run, pipeline test only (NOT evidence)
  python gemini_spc_harness.py run --model gemini-2.5-flash --reps 10 # real run (needs GEMINI_API_KEY)
  python gemini_spc_harness.py analyze --amortize 20

SAFETY: this executes model-generated code. Run it inside a container/VM, never on a machine
with secrets. The rlimit/timeout guard below is a convenience, not a security sandbox.
"""
from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

# =============================================================================
# 1. Requirement registry. Every constraint is machine-checkable (see check_compliance).
#    `define` text is IDENTICAL for the symbol (C2/C4/C6) and label (C3) encodings.
# =============================================================================
ROLE = dict(
    sym="Ω:Coder", label="Role:Coder",
    define="act as a careful Python engineer: implement exactly the requested function and handle every edge case in the spec",
    nl_long="You are a careful Python engineer. Implement exactly the requested function and handle every edge case mentioned in the spec.",
    nl_short="Careful Python engineer; handle edge cases.",
)
CONSTRAINTS = {
    "code_only": dict(
        sym="∮", label="CodeOnly",
        define="the reply is exactly one fenced ```python block and nothing else (no text before or after it)",
        nl_long="Reply with exactly one fenced Python code block and absolutely no explanatory text before or after it.",
        nl_short="One ```python block only, no prose."),
    "no_io": dict(
        sym="⊘IO", label="NoIO",
        define="the code uses no file, network, OS or subprocess access (no open, os, sys, subprocess, socket, requests, pathlib)",
        nl_long="Do not use any file, network, operating-system or subprocess access: no open(), and do not import os, sys, subprocess, socket, requests or pathlib.",
        nl_short="No file/network/OS access."),
    "no_print": dict(
        sym="⊘PR", label="NoPrint",
        define="the code never calls print()",
        nl_long="Never call print() anywhere in the code.",
        nl_short="No print()."),
    "docstring": dict(
        sym="◇DOC", label="Docstring",
        define="the target function has a docstring",
        nl_long="The target function must have a docstring.",
        nl_short="Function needs a docstring."),
    "type_hints": dict(
        sym="◇TY", label="TypeHints",
        define="every parameter and the return value of the target function are type-annotated",
        nl_long="Every parameter and the return value of the target function must have type annotations.",
        nl_short="Full type hints."),
}
ACTIVE = list(CONSTRAINTS)

# =============================================================================
# 2. Tasks with HIDDEN tests (never shown to the model) + reference solutions.
# =============================================================================
TASKS = [
    dict(name="normalize_ws", func="normalize_whitespace", signature="normalize_whitespace(s: str) -> str",
         spec="Collapse every run of whitespace (spaces, tabs, newlines) into one space and strip leading/trailing whitespace. Empty or whitespace-only input returns ''.",
         cases=[dict(args=["  a   b\t\nc "], expect="a b c"), dict(args=[""], expect=""), dict(args=["   "], expect=""),
                dict(args=["x"], expect="x"), dict(args=["a\n\nb"], expect="a b")],
         ref='import re\n\n\ndef normalize_whitespace(s: str) -> str:\n    """Collapse whitespace runs and strip."""\n    return re.sub(r"\\s+", " ", s).strip()\n'),
    dict(name="parse_kv", func="parse_kv", signature="parse_kv(s: str) -> dict[str, str]",
         spec="Parse 'k1=v1;k2=v2' into a dict. Strip whitespace around keys and values. Skip empty segments and segments without '='. Split each segment on the FIRST '=' only. Later duplicate keys overwrite earlier ones. Empty input returns {}.",
         cases=[dict(args=["a=1;b=2"], expect={"a": "1", "b": "2"}), dict(args=[""], expect={}),
                dict(args=[" a = 1 ; ;b"], expect={"a": "1"}), dict(args=["a=b=c"], expect={"a": "b=c"}),
                dict(args=["a=1;a=2"], expect={"a": "2"})],
         ref='def parse_kv(s: str) -> dict[str, str]:\n    """Parse k=v pairs separated by semicolons."""\n    out: dict[str, str] = {}\n    for seg in s.split(";"):\n        if "=" not in seg:\n            continue\n        k, v = seg.split("=", 1)\n        out[k.strip()] = v.strip()\n    return out\n'),
    dict(name="merge_intervals", func="merge_intervals", signature="merge_intervals(intervals: list[list[int]]) -> list[list[int]]",
         spec="Merge overlapping or touching (end == next start) closed intervals. Input is a possibly unsorted list of [start, end] with start <= end. Return a sorted list of merged [start, end] lists. Empty input returns []. Do not mutate the input.",
         cases=[dict(args=[[[1, 3], [2, 6], [8, 10]]], expect=[[1, 6], [8, 10]]), dict(args=[[]], expect=[]),
                dict(args=[[[1, 4], [4, 5]]], expect=[[1, 5]]), dict(args=[[[5, 6], [1, 2]]], expect=[[1, 2], [5, 6]]),
                dict(args=[[[1, 10], [2, 3]]], expect=[[1, 10]])],
         ref='def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:\n    """Merge overlapping or touching intervals."""\n    merged: list[list[int]] = []\n    for start, end in sorted(intervals):\n        if merged and start <= merged[-1][1]:\n            merged[-1][1] = max(merged[-1][1], end)\n        else:\n            merged.append([start, end])\n    return merged\n'),
    dict(name="moving_average", func="moving_average", signature="moving_average(xs: list[float], k: int) -> list[float]",
         spec="Return the mean of each consecutive window of length k, as floats. If k <= 0 raise ValueError. If k > len(xs) return []. Empty xs returns [].",
         cases=[dict(args=[[1, 2, 3, 4], 2], expect=[1.5, 2.5, 3.5]), dict(args=[[1, 2], 3], expect=[]),
                dict(args=[[], 1], expect=[]), dict(args=[[1, 2, 3], 1], expect=[1.0, 2.0, 3.0]),
                dict(args=[[1, 2], 0], raises="ValueError"), dict(args=[[1, 2], -1], raises="ValueError")],
         ref='def moving_average(xs: list[float], k: int) -> list[float]:\n    """Mean of each length-k window."""\n    if k <= 0:\n        raise ValueError("k must be positive")\n    return [sum(xs[i:i + k]) / k for i in range(len(xs) - k + 1)]\n'),
    dict(name="ipv4", func="is_valid_ipv4", signature="is_valid_ipv4(s: str) -> bool",
         spec="Return True iff s is a dotted-decimal IPv4 address: exactly four fields, each digits only, value 0-255, no leading zeros (except '0' itself), no whitespace or signs.",
         cases=[dict(args=["192.168.0.1"], expect=True), dict(args=["256.1.1.1"], expect=False),
                dict(args=["1.1.1"], expect=False), dict(args=["01.1.1.1"], expect=False),
                dict(args=["0.0.0.0"], expect=True), dict(args=["1.1.1.1 "], expect=False),
                dict(args=["a.b.c.d"], expect=False), dict(args=[""], expect=False),
                dict(args=["1.2.3.4.5"], expect=False), dict(args=["-1.2.3.4"], expect=False)],
         ref='def is_valid_ipv4(s: str) -> bool:\n    """Strict dotted-decimal IPv4 check."""\n    parts = s.split(".")\n    if len(parts) != 4:\n        return False\n    for p in parts:\n        if not (p.isascii() and p.isdigit()):\n            return False\n        if len(p) > 1 and p[0] == "0":\n            return False\n        if int(p) > 255:\n            return False\n    return True\n'),
    dict(name="csv_rows", func="parse_csv_rows", signature="parse_csv_rows(text: str) -> list[dict[str, str]]",
         spec="The first non-blank line is the header. Return one dict per data row. Fields are comma-separated (no quoting) and whitespace-stripped. Skip blank lines and skip rows whose field count differs from the header's. Empty input or header-only input returns [].",
         cases=[dict(args=["a,b\n1,2\n3,4"], expect=[{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]), dict(args=[""], expect=[]),
                dict(args=["a,b\n1,2,3\n4,5"], expect=[{"a": "4", "b": "5"}]),
                dict(args=["a,b\n\n1,2\n"], expect=[{"a": "1", "b": "2"}]),
                dict(args=["a,b\n 1 , 2 "], expect=[{"a": "1", "b": "2"}]), dict(args=["a,b"], expect=[])],
         ref='def parse_csv_rows(text: str) -> list[dict[str, str]]:\n    """Parse simple CSV, skipping malformed rows."""\n    lines = [ln for ln in text.splitlines() if ln.strip()]\n    if not lines:\n        return []\n    header = [h.strip() for h in lines[0].split(",")]\n    rows: list[dict[str, str]] = []\n    for ln in lines[1:]:\n        fields = [f.strip() for f in ln.split(",")]\n        if len(fields) == len(header):\n            rows.append(dict(zip(header, fields)))\n    return rows\n'),
]
TASK_BY_NAME = {t["name"]: t for t in TASKS}

# =============================================================================
# 3. Prompt builders
# =============================================================================
CONDITIONS = ["C0", "C1", "C2", "C3", "C4", "C5", "C6"]


def _expr(style: str) -> str:
    sep = " ⊕ " if style == "sym" else " + "
    return sep.join([ROLE[style]] + [CONSTRAINTS[c][style] for c in ACTIVE])


def _defs(style: str) -> str:
    items = [(ROLE[style], ROLE["define"])] + [(CONSTRAINTS[c][style], CONSTRAINTS[c]["define"]) for c in ACTIVE]
    return "\n".join(f"- {k} = {d}" for k, d in items)


def _task_block(t: dict) -> str:
    return f"Task: implement the Python function `{t['signature']}`.\nSpec: {t['spec']}"


def build_prompt(cond: str, t: dict) -> tuple[str | None, str]:
    task = _task_block(t)
    if cond == "C0":
        rules = "\n".join(["- " + ROLE["nl_long"]] + ["- " + CONSTRAINTS[c]["nl_long"] for c in ACTIVE])
        return None, f"Follow all of these instructions:\n{rules}\n\n{task}"
    if cond == "C1":
        short = " ".join([ROLE["nl_short"]] + [CONSTRAINTS[c]["nl_short"] for c in ACTIVE])
        return None, f"Rules: {short}\n\n{task}"
    if cond in ("C2", "C3"):
        style = "sym" if cond == "C2" else "label"
        return None, f"Profile: {_expr(style)}\nApply every item of the Profile. Definitions:\n{_defs(style)}\n\n{task}"
    if cond == "C4":
        system = f"A request contains a 'Profile:' line. Apply every item of it. Definitions:\n{_defs('sym')}"
        return system, f"Profile: {_expr('sym')}\n\n{task}"
    if cond == "C5":
        spec = {"role": "careful python engineer", "constraints": {c: True for c in ACTIVE}}
        return None, f"Spec (JSON): {json.dumps(spec)}\nApply every item.\n\n{task}"
    if cond == "C6":
        return None, f"Profile: {_expr('sym')}\nApply every item of the Profile.\n\n{task}"
    raise ValueError(cond)


# =============================================================================
# 4. Grading (AST compliance + hidden sandbox tests)
# =============================================================================
FENCE = re.compile(r"```[a-zA-Z]*[ \t]*\n(.*?)```", re.S)
BANNED_IMPORTS = {"os", "sys", "subprocess", "socket", "requests", "pathlib", "shutil", "urllib", "http"}
BANNED_CALLS = {"open", "exec", "eval", "__import__", "compile", "input"}


def extract_code(reply: str) -> str:
    m = FENCE.findall(reply)
    return m[0] if m else reply


def check_compliance(reply: str, func: str) -> dict:
    blocks = FENCE.findall(reply)
    res = {c: False for c in ACTIVE}
    res["code_only"] = len(blocks) == 1 and FENCE.sub("", reply).strip() == ""
    code = blocks[0] if blocks else reply
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {**res, "syntax_ok": False}
    imports_ok, calls_ok, print_ok = True, True, True
    target = None
    for n in ast.walk(tree):
        if isinstance(n, ast.Import) and any(a.name.split(".")[0] in BANNED_IMPORTS for a in n.names):
            imports_ok = False
        elif isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] in BANNED_IMPORTS:
            imports_ok = False
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            if n.func.id in BANNED_CALLS:
                calls_ok = False
            if n.func.id == "print":
                print_ok = False
        elif isinstance(n, ast.FunctionDef) and n.name == func and target is None:
            target = n
    res["no_io"] = imports_ok and calls_ok
    res["no_print"] = print_ok
    if target is not None:
        res["docstring"] = bool(ast.get_docstring(target))
        a = target.args
        params = a.posonlyargs + a.args + a.kwonlyargs + ([a.vararg] if a.vararg else []) + ([a.kwarg] if a.kwarg else [])
        res["type_hints"] = all(p.annotation is not None for p in params) and target.returns is not None
    return {**res, "syntax_ok": True}


RUNNER = r'''
import sys, json, copy, io
data = json.load(sys.stdin)
real = sys.stdout
sys.stdout = io.StringIO()
def eq(a, b):
    if isinstance(b, bool): return a is b
    if isinstance(b, (int, float)) and not isinstance(a, bool) and isinstance(a, (int, float)): return abs(a - b) < 1e-9
    if isinstance(b, (list, tuple)): return isinstance(a, (list, tuple)) and len(a) == len(b) and all(eq(x, y) for x, y in zip(a, b))
    if isinstance(b, dict): return isinstance(a, dict) and a.keys() == b.keys() and all(eq(a[k], b[k]) for k in b)
    return a == b and type(a) is type(b)
ns = {"__name__": "candidate"}
try:
    exec(compile(data["code"], "candidate.py", "exec"), ns)
    fn = ns[data["func"]]
except BaseException as e:
    sys.stdout = real
    print(json.dumps({"passed": 0, "total": len(data["cases"]), "error": "load: " + repr(e)[:200]})); sys.exit(0)
passed, errs = 0, []
for i, c in enumerate(data["cases"]):
    before = copy.deepcopy(c["args"]); args = copy.deepcopy(before)
    try:
        out = fn(*args)
        ok = ("raises" not in c) and eq(out, c["expect"]) and args == before
    except BaseException as e:
        ok = c.get("raises") == type(e).__name__
    passed += bool(ok)
    if not ok: errs.append(i)
sys.stdout = real
print(json.dumps({"passed": passed, "total": len(data["cases"]), "failed_cases": errs}))
'''


def _limits() -> None:
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
    except Exception:
        pass


def run_hidden_tests(code: str, func: str, cases: list, timeout: float = 15.0) -> dict:
    payload = json.dumps({"code": code, "func": func, "cases": cases})
    try:
        with tempfile.TemporaryDirectory() as tmp:
            p = subprocess.run([sys.executable, "-I", "-c", RUNNER], input=payload, capture_output=True, text=True,
                               timeout=timeout, cwd=tmp, preexec_fn=_limits if os.name == "posix" else None)
        lines = [ln for ln in p.stdout.strip().splitlines() if ln.startswith("{")]
        if lines:
            return json.loads(lines[-1])
        return {"passed": 0, "total": len(cases), "error": "no output: " + p.stderr[-200:]}
    except subprocess.TimeoutExpired:
        return {"passed": 0, "total": len(cases), "error": "timeout"}


def grade(reply: str, task: dict) -> dict:
    t = run_hidden_tests(extract_code(reply), task["func"], task["cases"])
    comp = check_compliance(reply, task["func"])
    all_tests = t["passed"] == t["total"]
    all_comp = all(comp[c] for c in ACTIVE)
    return dict(tests_passed=t["passed"], tests_total=t["total"], all_tests=all_tests, compliance=comp,
                all_compliant=all_comp, joint=all_tests and all_comp, error=t.get("error"))


# =============================================================================
# 5. Gemini Client (`google-genai` integration)
# =============================================================================
@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    latency_s: float


class GeminiClient:
    def __init__(self, model: str, temperature: float | None):
        from google import genai  # pip install google-genai ; needs GEMINI_API_KEY
        self.client = genai.Client()
        self.model = model
        self.temperature = temperature

    def complete(self, system, user, max_tokens=1024) -> LLMResult:
        from google.genai import types
        config = types.GenerateContentConfig(max_output_tokens=max_tokens)
        if self.temperature is not None:
            config.temperature = self.temperature
        if system:
            config.system_instruction = system

        t0 = time.time()
        for attempt in range(4):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=user,
                    config=config,
                )
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
        
        text = response.text or ""
        usage = response.usage_metadata
        input_tokens = usage.prompt_token_count if usage and usage.prompt_token_count else len(user) // 4
        output_tokens = usage.candidates_token_count if usage and usage.candidates_token_count else len(text) // 4
        return LLMResult(text, input_tokens, output_tokens, time.time() - t0)

    def count(self, text: str) -> int:
        try:
            resp = self.client.models.count_tokens(model=self.model, contents=text)
            return resp.total_tokens
        except Exception:
            return len(text) // 4


class MockClient:
    temperature = None

    def complete(self, system, user, max_tokens=1024) -> LLMResult:
        func = re.search(r"function `(\w+)\(", user).group(1)
        task = next(t for t in TASKS if t["func"] == func)
        rng = random.Random(zlib.crc32((user + str(time.time_ns())).encode()))
        code = task["ref"]
        if rng.random() < 0.10:
            code = f"def {func}(*a):\n    return None\n"
        if rng.random() < 0.10:
            code = code.replace("    return", "    print('debug')\n    return", 1)
        text = f"```python\n{code}```"
        if rng.random() < 0.15:
            text = "Sure, here is the code:\n" + text
        return LLMResult(text, len((system or "") + user) // 4, len(text) // 4, 0.0)

    def count(self, text: str) -> int:
        return len(text) // 4


def make_client(model: str, temperature: float):
    if model == "mock":
        return MockClient()
    return GeminiClient(model, None if temperature < 0 else temperature)


# =============================================================================
# 6. Experiment runner
# =============================================================================
def cmd_run(a) -> None:
    client = make_client(a.model, a.temperature)
    out, done = Path(a.out), set()
    if out.exists():
        done = {(r["cond"], r["task"], r["rep"]) for r in map(json.loads, out.read_text(encoding="utf-8").splitlines())}
    conds = a.conds.split(",")
    jobs = [(c, t["name"], r) for c in conds for t in TASKS for r in range(a.reps) if (c, t["name"], r) not in done]
    random.Random(a.seed).shuffle(jobs)
    sys_tok_cache: dict = {}
    print(f"{len(jobs)} jobs ({len(done)} already done) -> {out}")
    with out.open("a", encoding="utf-8") as f:
        for i, (cond, tname, rep) in enumerate(jobs, 1):
            task = TASK_BY_NAME[tname]
            system, user = build_prompt(cond, task)
            res = client.complete(system, user)
            if system and system not in sys_tok_cache:
                sys_tok_cache[system] = client.count(system)
            row = dict(cond=cond, task=tname, rep=rep, model=a.model, temperature=a.temperature, reply=res.text,
                       input_tokens=res.input_tokens, output_tokens=res.output_tokens,
                       system_tokens=sys_tok_cache.get(system, 0) if system else 0,
                       latency_s=round(res.latency_s, 3), ts=time.time(), **grade(res.text, task))
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            if i % 10 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)}")


# =============================================================================
# 7. Analysis
# =============================================================================
def _mean(x):
    x = list(x)
    return sum(x) / len(x) if x else float("nan")


def _eff_tokens(r: dict, amortize: int) -> float:
    return r["input_tokens"] - r["system_tokens"] + r["system_tokens"] / amortize + r["output_tokens"]


def _task_vals(rows, cond, metric, amortize):
    by: dict = {}
    for r in rows:
        if r["cond"] == cond:
            by.setdefault(r["task"], []).append(_eff_tokens(r, amortize) if metric == "eff_tokens" else float(r[metric]))
    return by


def _boot(va: dict, vb: dict, B: int, rng: random.Random):
    tasks = [t for t in va if t in vb]
    point = _mean(_mean(va[t]) - _mean(vb[t]) for t in tasks)
    ms = []
    for _ in range(B):
        d = 0.0
        for _t in range(len(tasks)):
            t = tasks[rng.randrange(len(tasks))]
            xa = [va[t][rng.randrange(len(va[t]))] for _ in va[t]]
            xb = [vb[t][rng.randrange(len(vb[t]))] for _ in vb[t]]
            d += _mean(xa) - _mean(xb)
        ms.append(d / len(tasks))
    ms.sort()
    return point, ms[int(0.025 * B)], ms[int(0.975 * B) - 1]


def cmd_analyze(a) -> None:
    rows = [json.loads(l) for l in Path(a.out).read_text(encoding="utf-8").splitlines()]
    conds = [c for c in CONDITIONS if any(r["cond"] == c for r in rows)]
    n_tasks = len({r["task"] for r in rows})
    print(f"rows={len(rows)} tasks={n_tasks} model(s)={sorted({r['model'] for r in rows})}  amortize={a.amortize}")
    if rows[0]["model"] == "mock":
        print("!! MOCK DATA: pipeline test only, says nothing about SPC.")
    hdr = f"{'cond':<5}{'n':>5}{'tests':>8}{'comply':>8}{'joint':>8}{'in_tok':>9}{'out_tok':>9}{'eff_tok':>9}{'unstable':>10}"
    print("\n" + hdr)
    for c in conds:
        rs = [r for r in rows if r["cond"] == c]
        unst = []
        for t in {r["task"] for r in rs}:
            p = _mean(float(r["joint"]) for r in rs if r["task"] == t)
            unst.append(p * (1 - p))
        print(f"{c:<5}{len(rs):>5}{_mean(r['all_tests'] for r in rs):>8.2f}{_mean(r['all_compliant'] for r in rs):>8.2f}"
              f"{_mean(r['joint'] for r in rs):>8.2f}{_mean(r['input_tokens'] for r in rs):>9.0f}"
              f"{_mean(r['output_tokens'] for r in rs):>9.0f}{_mean(_eff_tokens(r, a.amortize) for r in rs):>9.0f}{_mean(unst):>10.3f}")
    pairs = [("C2", "C3"), ("C2", "C0"), ("C2", "C1"), ("C4", "C2"), ("C2", "C6"), ("C2", "C5")]
    print("\nDifferences A - B (95% two-level bootstrap CI: tasks, then repeats)")
    rng = random.Random(a.seed)
    for A, B in pairs:
        if A not in conds or B not in conds:
            continue
        for metric in ("joint", "eff_tokens"):
            va, vb = _task_vals(rows, A, metric, a.amortize), _task_vals(rows, B, metric, a.amortize)
            if len([t for t in va if t in vb]) < 2:
                continue
            m, lo, hi = _boot(va, vb, a.boots, rng)
            flag = "" if lo <= 0 <= hi else "  <- CI excludes 0"
            print(f"  {A} - {B:<3} {metric:<11} {m:>9.3f}  [{lo:>8.3f}, {hi:>8.3f}]{flag}")


# =============================================================================
# 8. Self-test and prompt inspection
# =============================================================================
def cmd_selftest(_a) -> None:
    bad = 0
    for t in TASKS:
        g = grade(f"```python\n{t['ref']}```", t)
        if not g["joint"]:
            bad += 1
            print("FAIL reference:", t["name"], g)
    broken = grade("```python\ndef f(*a):\n    return None\n```", TASKS[0])
    if broken["all_tests"]:
        bad += 1
        print("FAIL: broken solution passed tests")
    probes = {
        "code_only": "Here you go:\n```python\n" + TASKS[0]["ref"] + "```",
        "no_io": "```python\nimport os\n" + TASKS[0]["ref"] + "```",
        "no_print": "```python\n" + TASKS[0]["ref"].replace("    return", "    print(1)\n    return", 1) + "```",
        "docstring": "```python\n" + TASKS[0]["ref"].replace('    """Collapse whitespace runs and strip."""\n', "") + "```",
        "type_hints": "```python\n" + TASKS[0]["ref"].replace("(s: str) -> str", "(s)") + "```",
    }
    for c, reply in probes.items():
        comp = check_compliance(reply, TASKS[0]["func"])
        if comp[c] or not all(comp[k] for k in ACTIVE if k != c):
            bad += 1
            print("FAIL probe:", c, comp)
    print("selftest:", "OK" if not bad else f"{bad} FAILURE(S)")
    sys.exit(1 if bad else 0)


def cmd_show(a) -> None:
    client = make_client(a.model, 1.0) if a.model != "mock" else MockClient()
    t = TASKS[0]
    for c in CONDITIONS:
        system, user = build_prompt(c, t)
        n = client.count((system or "") + "\n" + user)
        print(f"\n{'=' * 24} {c}  (~{n} tokens{'; system part shared' if system else ''}) {'=' * 24}")
        if system:
            print("[SYSTEM]\n" + system + "\n[USER]")
        print(user)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "analyze", "show-prompts", "selftest"):
        p = sp.add_parser(name)
        p.add_argument("--out", default="gemini_results.jsonl")
        p.add_argument("--model", default="mock")
        p.add_argument("--temperature", type=float, default=1.0, help="-1 = omit the parameter")
        p.add_argument("--reps", type=int, default=5)
        p.add_argument("--conds", default=",".join(CONDITIONS))
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--amortize", type=int, default=20)
        p.add_argument("--boots", type=int, default=5000)
    a = ap.parse_args()
    {"run": cmd_run, "analyze": cmd_analyze, "show-prompts": cmd_show, "selftest": cmd_selftest}[a.cmd](a)


if __name__ == "__main__":
    main()
