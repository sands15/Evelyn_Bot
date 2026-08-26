---
tags:
  - evelyn
  - gpu
  - benchmark
type: runtime-contract
last_reviewed: 2026-08-21
---

# GPU1 Qwen specialist + STT concurrency benchmark

## Contract

`tools/gpu1_latency_benchmark.py` starts one request with the current fixed
938-character Fast Main system prompt, one Qwen
specialist request, and one 1.64-second STT final request from the same barrier.
While they overlap it samples physical GPU1 through `nvidia-smi` every 50 ms.
The atomic output is `evelyn.gpu1-latency-budget.v1` at
`runtime_artifacts/benchmarks/gpu1_concurrency_latest.json`.

The default release budgets are:

| Measure | Budget |
| --- | ---: |
| Fast Main TTFT p95 | <= 1,000 ms |
| Qwen specialist | 0 timeouts; successful p95 <= 6,000 ms |
| STT endpoint-to-final p95 | <= 1,200 ms |
| Physical GPU1 free memory | >= 2,048 MiB |
| Valid concurrent samples | >= 5; request/GPU sampling errors = 0 |

The report contains client TTFT plus llama.cpp `prompt_ms`, processed/cached
prompt-token counts, prefill throughput, prompt-cache hit ratio, bounded error
types, GPU aggregates/samples, fixed prompt hashes, and the audio fixture hash.
It does not contain request text,
transcripts, raw/base64 audio, user/session identifiers, or credentials.

The report is diagnostic only. Production admission, Discord, and Local Voice
do not read it and do not change behavior when it is missing, failed, or old.
Task and specialist Qwen inference retain their six-second post-admission budget.

## Approved live procedure

The first command starts or recreates GPU containers and therefore requires the
user's explicit approval. The benchmark override changes `stt` from GPU0 to
physical GPU1 and publishes Qwen on loopback port 9823 for the diagnostic
runner; it does not change normal Compose placement or production admission.

```powershell
docker compose -f docker-compose.fast-control.yml -f docker-compose.gpu1-benchmark.yml --profile llm --profile stt up -d main_llm minecraft_llm stt
.\.venv\Scripts\python.exe tools\gpu1_latency_benchmark.py
```

Exit code `0` means every configured budget passed. Exit code `2` means a
report was written but one or more budgets failed. Service/network
setup errors are recorded as content-free sample failures and also produce exit
code `2`.

## Latest live evidence

The approved procedure ran on 2026-08-16 with Main on GPU0 and Qwen
specialist plus STT on physical GPU1. One warmup and five measured iterations
produced a passing report using the then-current 1,773-character prompt and
pre-2026-08-21 server tuning:

| Measure | Observed | Budget |
| --- | ---: | ---: |
| Fast Main TTFT p95 | 422.6 ms | <= 1,000 ms |
| Qwen specialist p95 | 2,233.2 ms; 0 timeouts | <= 6,000 ms; 0 timeouts |
| STT endpoint-to-final p95 | 626.1 ms | <= 1,200 ms |
| GPU1 free memory | minimum 10,284 MiB | >= 2,048 MiB |
| GPU1 utilization | peak 98% | observed only |
| Samples/errors | 5 request sets, 102 GPU samples, 0 errors | >= 5; 0 errors |

The three test containers and Docker Desktop were then stopped, and GPU1
returned to 0 MiB used. This short fixed-input run does not establish
long-duration thermal or broader Router/Sub concurrency behavior and has no
runtime admission effect.

## Pending live A/B

The 2026-08-21 source now warms the exact voice and Fast Main prefixes, bounds
voice prompt history to eight non-system messages, omits unrequested dynamic
memory/runtime context, reuses the Fast Control HTTP session, and enables
CUDA graph optimization with explicit llama.cpp batch/ubatch/cache settings.
The source and contract tests passed offline, but the 422.6 ms result above is
not evidence for this new configuration. A new approved live run must compare
TTFT, `prompt_ms`, prefill throughput, and cache hit ratio before and after.
