# M3b LLM Gate - Dual-Server Validation (SQL model on 8081)

Date: 2026-07-20
Hardware: RX 6700 XT 12 GB (gfx1031, RDNA2), llama.cpp HIP build `1ec44d1` at `C:\llama`.
bge embedding server stays on 8080 throughout; all tests ran with both servers up.

## DECISION: Qwen3-8B Q5_K_M (dense) - Qwen3.5 family REJECTED on this stack

The planned Qwen3.5-9B is unusable on this GPU stack (3 t/s generation), for an
architectural reason none of the milestone's fallbacks (lower quant, smaller
context, Qwen3.5-4B) can fix. Fallback is Qwen3-8B: same vendor, same size
class, dense `qwen3` architecture with mature llama.cpp support.

## Why Qwen3.5 failed: hybrid SSM ops run on CPU

Qwen3.5-9B Q5_K_M (`unsloth/Qwen3.5-9B-GGUF`) loads fine, VRAM fits
(8.9 GB total with bge), output quality was good - the very first SQL prompt
returned a correct join. But generation speed is broken:

| Config | pp512 (t/s) | tg32 (t/s) |
|---|---|---|
| `-ngl 99` (weights fully in VRAM) | 431 | **3.04** |
| `-ngl 0` (CPU only) | 70 | **3.19** |

Token generation is IDENTICAL with and without the GPU - the decode path is
CPU-bound regardless of offload. GGUF metadata explains it: `qwen35` is a
hybrid linear-attention/SSM architecture (`ssm.conv_kernel=4`,
`ssm.state_size=128`, `full_attention_interval=4` - only every 4th layer is
full attention, the rest are gated-delta/SSM layers). The SSM scan ops have no
effective HIP path in build `1ec44d1` on gfx1031, so every decoded token
round-trips through the CPU. Prefill (parallel, GEMM-heavy) still benefits
from the GPU; decode (sequential scan) does not.

At ~3 t/s a 40-token SQL answer takes 80+ s (measured wall: 83.7 s) - unusable
for interactive SQL, and worse for M4/M5 (router, judge).

Checked before rejecting the family:
- `-fit off -ngl 99 -np 1`: no change (3.1 t/s) - not the auto-fit feature.
- Whole Qwen3.5 lineup shares the architecture: `Qwen/Qwen3.5-4B` config.json
  has the same `linear_attention` + `full_attention_interval: 4`. The 4B
  fallback would inherit the same CPU-bound decode.
- Lower quant / smaller context attack VRAM pressure, which is not the
  problem (VRAM fits with 3 GB headroom).

Possible future fix, deliberately NOT chased now (timebox): a newer llama.cpp
HIP build may add SSM kernels for RDNA2; retest when `C:\llama` is next
updated.

## Qwen3-8B Q5_K_M measurements (the accepted model)

(from `unsloth/Qwen3-8B-GGUF`, dense 8.2B, `qwen3` arch)

| Check | Result |
|---|---|
| llama-bench pp512 / tg64, `-ngl 99` | 906 / 55.5 t/s (18x Qwen3.5 decode) |
| Both servers up, total VRAM | 9.19 GB of 12 GB (~2.8 GB headroom) |
| SQL prompt (1807 tok): prefill, gen, wall | 859 t/s, 53.2 t/s, 2.83 s cold |
| Warm repeat (prompt-cache prefix hit) | 0.39 s wall, only 15 tokens prefilled |
| Strict-JSON prompt parseable | yes: `{"route": "sql"}` |
| SQL prompt returns executable SQL | yes - correct join, executed read-only |
| Embed on 8080 while 8081 loaded | yes, dim=768, no VRAM crash |
| Smoke eval (eval/smoke_sql.jsonl) | 9/10 execution accuracy (threshold 7) |

Run everything above with `python analysis/llm_gate/gate.py` (both servers up).

Smoke failure diagnosis (1/10): "Which project received the largest EU
contribution" - the model answered with `title`, ground truth uses `acronym`;
same row, same money value, semantically correct SQL. Question ambiguity in
the eval, not a model or schema_docs defect. No prompt/docs change needed.

## Launch configuration (load-bearing, mirrored in src/config.py)

```
C:\llama\llama-server.exe -m data\models\Qwen3-8B-Q5_K_M.gguf -ngl 99 -fit off -np 1 -c 8192 --reasoning-budget 0 --port 8081
```

- `-ngl 99 -fit off`: full explicit offload; the build's auto-fit chooses
  silently otherwise.
- `-np 1`: one slot - the SQL path is strictly sequential, and fewer slots
  means less reserved compute/KV memory.
- `--reasoning-budget 0`: Qwen3 is a hybrid-thinking model; its chat template
  defaults to thinking mode (`init: chat template, thinking = 1` in the server
  log). Without this flag every SQL answer pays a long <think> preamble - with
  decode-bound latency that dominates wall time. Thinking is disabled
  server-side so no client can accidentally re-enable it.
- Prompt cache stays ENABLED (default) - the opposite of the embedding server
  (`--cache-ram 0`, see gpu_validation report). Every SQL call shares the same
  ~1.6k-token system prompt (schema_docs.md); the cache turns that prefill
  into a prefix hit on every call after the first.

## Addendum (M4): GPU memory-clock stuck-idle incident

During M4 the LLM server's generation speed collapsed from ~55 t/s to ~3 t/s
mid-session. Isolated with llama-bench (same command, model alone on the card,
~90 min apart): tg64 55.3 -> 3.0 t/s, pp512 906 -> 453 t/s.

Diagnosis: the GPU's VRAM memory clock was stuck in its idle P-state. The
asymmetry is the fingerprint - token generation is memory-bandwidth bound and
dropped ~18x (324 -> 18 GB/s effective, i.e. idle-clock bandwidth), while
prefill is compute bound and dropped only ~2x (core clock partially down).
Thermal or power-limit throttling would hit both proportionally; VRAM spill was
ruled out (10 GB free, model alone). On Windows + AMD RDNA2, the driver's
clock-ramp heuristic keys off graphics activity and often fails to lift the
memory P-state under pure-compute HIP load after an idle gap; sustained
inference did NOT recover it.

Fix (confirmed): disable/enable the GPU in Device Manager (or reboot). After the
reset, llama-bench returned to tg64 = 55.3 t/s immediately. NOTE: a device reset
invalidates the GPU context of any already-running llama-server - both servers
report /health ok but crash on the next inference, so they must be restarted
after a reset.

Operational takeaway: if generation is ~3 t/s, it is this stuck-clock state, not
the model or the code. Reset the GPU and relaunch both servers.
