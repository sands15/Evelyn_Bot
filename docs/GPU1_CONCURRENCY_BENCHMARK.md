---
tags:
  - evelyn
  - gpu
  - benchmark
type: runtime-contract
last_reviewed: 2026-08-27
---

# GPU1 Qwen specialist + STT concurrency benchmark

## Current P0-4 contract

`tools/gpu1_latency_benchmark.py`의 P0-4 mode는 Fast Main 한 요청, 현행
Qwen3-14B specialist 한 요청과 1.64초 STT final 한 요청을 같은 barrier에서 시작하고 physical
GPU1을 50ms마다 관측한다. old/new STT image 각각 warmup 2회 뒤 20회를 측정하며 report schema는
`evelyn.gpu1-latency-budget.v2`다.

P0-4 실행은 자유로운 diagnostic mode와 달리 아래 값을 바꿀 수 없다.

| Measure | Gate |
| --- | ---: |
| Fast Main TTFT p95 | `<=1,000ms` |
| Qwen3-14B | timeout 0, successful p95 `<=6,000ms` |
| STT endpoint-to-final p95 | `<=1,200ms` |
| Physical GPU1 free memory | `>=2,048MiB` |
| Samples | old/new 각각 20, request/GPU error 0 |
| Candidate regression | old-image STT p95 대비 `<=10%` |

old report의 bounded raw JSON과 SHA-256을 candidate의 network 요청 전에 읽고, candidate 종료 뒤에도
같은 bytes인지 다시 확인한다. report는 clean source commit, unique Compose project, exact container/image,
physical GPU UUID, Main/Qwen/STT model과 runtime, read-only model/cache mount, STT cache revision·content,
dependency manifest를 preflight/postflight에 결속한다. 서로 다른 output과 baseline 경로만 허용한다.

report에는 aggregate timing, fixed prompt/audio hash, bounded error type와 위 identity proof만 남긴다.
request text, 모델 output, transcript, raw/base64 audio, private 경로, token과 credential은 남기지 않는다.
이 report는 진단 evidence이며 production admission, Discord와 Local Voice가 읽지 않는다.

## Exact live boundary

Live 실행은 이미 승인된 P0-4 범위 안에서도 다음 순서를 지킨다.

1. clean source와 P0-3 recovery ancestor, Docker의 원래 desired state, loopback port, fixed container 부재,
   existing image ID와 GPU baseline을 기록한다.
2. attempt마다 unique lower-case Compose project를 만들고 `main_llm`, `minecraft_llm`, `stt` 세 service만
   `--no-deps --no-build --pull never`로 시작한다. `bot_api`, Discord, microphone, speaker와 Minecraft는
   시작하지 않는다. diagnostic container 이름은 `evelyn-p04-main-llm`, `evelyn-p04-qwen-llm`,
   `evelyn-p04-stt`로 production의 기존 stopped/running container와 분리한다.
3. old image 2+20 report를 별도 output에 쓴 뒤 exact project를 내리고 GPU1 baseline 복귀를 3회 확인한다.
4. STT service만 새 recipe로 build/load한다. exact image/source/dependency/cache/health를 확인한 뒤 private
   positive 40 + negative 10 corpus, cancel/successor를 먼저 통과해야 candidate 2+20과 cold restart 3회를
   진행한다.
5. 성공 때만 새 STT image를 기준선으로 기록한다. 실패하면 old image tag로 복구하고 exact project,
   volume과 owned artifact만 정리한다. Docker Desktop은 실행 전 상태로 돌리고 production은 OFF로 둔다.

고정 container 이름이 이미 존재하거나 project에 세 service 외 container가 보이면 중단한다. global image,
volume, process를 이름 추측이나 prune으로 지우지 않는다.

## Current verification state

2026-08-27 source에는 P0-4 v2 receipt와 private corpus runner, hub-only read-only/offline STT mount,
content-free log volume, STT 전용 최소 build context, digest-pinned CUDA base, two-stage image와 direct Python
pin/package-set hash가 구현됐다. 관련 offline source tests는 통과했지만 Docker image build/load와 GPU live
측정은 아직 실행하지 않았다. P0-4 focused는 `75 passed, 29 subtests`, canonical은
`4601 passed, 22 skipped, 1409 subtests`였다. 고정 private corpus directory는 현재 없고
manifest/audio는 `0/50`이므로
합성 자료로 대체하지 않으며 full P0-4 promotion gate는 외부 입력 대기 상태다.

## Historical v1 live evidence

2026-08-16 당시 v1 runner의 1 warmup + 5 measured run은 Main GPU0, Qwen specialist+STT GPU1에서
통과했다. Fast Main TTFT p95 `422.6ms`, Qwen p95 `2,233.2ms`/timeout 0, STT final p95
`626.1ms`, GPU1 min free `10,284MiB`, peak utilization `98%`, GPU samples 102, error 0이었다.
컨테이너와 Docker Desktop을 종료하고 GPU1은 0MiB로 복귀했다.

이 historical 결과는 현재 v2의 revised STT image, 2+20 A/B, private corpus, restart 또는 승격 증거가
아니다.
