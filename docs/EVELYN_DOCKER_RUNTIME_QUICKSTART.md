# Evelyn Docker Runtime Quickstart

이 문서는 이블린 Docker 런타임의 현재 실행 범위와 점검 방법을 정리한다.

## 현재 Docker 범위

현재 Compose stack은 다음 서비스를 포함한다.

- `control_page`: Control-Page, `8799`
- `bot_api`: 경량 Fast-Control Bot API, `8798`
- `discord_bot`: 실제 Discord bot loop worker
- `main_llm`: llama-server main LLM, `9820`
- `router_llm`: llama-server router LLM, `9822`
- `sub_llm`: llama-server sub LLM, `9821`
- `tts`: `k2-fsa/OmniVoice` TTS, `8880`
- `voxcpm_fallback`: opt-in 호환성·진단 profile, host `8881`
- `stt`: Qwen3-ASR STT service, `8892`
- `vision`: credential-free Vision ingress, `8891`
- `vision_runtime`: isolated SmolVLM2/Falcon-OCR GPU runtime
- `voyager`: Minecraft/Voyager HTTP service, `8765`
- `codex_gateway`: Voyager Codex Gateway HTTP service, `8787`

Discord bot loop는 `discord` profile의 `discord_bot` 서비스로 분리한다. Control-Page/Bot API는 빠른 부팅과 상태 표시를 담당하고, 실제 Discord Gateway 접속과 음성 루프는 `discord_bot`이 담당한다.

## 실행

Windows 로컬 기본 경로는 launcher를 사용한다. Host Supervisor와 화면 bridge,
TTS profile 검증, 모델 readiness까지 같은 계약으로 확인한다.

기본 OmniVoice 이미지는 `EVELYN_OMNIVOICE_SERVER_DIR` 아래의
`omnivoice_server/`만 build context로 읽는다. 환경변수가 없으면
`${USERPROFILE}/omnivoice-server`를 사용한다. 검토된 Python 파일 20개가
`docker/omnivoice_source.sha256`과 정확히 일치해야 하며, 복사 대상의 추가·변경은
build가 실패한다. 광범위한 `COPY .`는 사용하지 않고 Python glob 네 개만
이미지에 넣는다. 시작 시 `docker/omnivoice_model.sha256`으로 고정 revision의 필수
snapshot 경로 13개를 검증한다. 직접 runtime 의존성만 고정했으며 전이 wheel과 base
image digest까지 고정한 완전 재현 build로 보지는 않는다.

Hugging Face cache에는 offline revision
`c5fdb5ccb189668d56333f77ba2629f4cd7535f4`가 이미 있어야 하며 컨테이너에는
read-only로 마운트한다. Evelyn profile도 read-only다. 서버의 profile API와 validation
오류 응답은 입력 원문을 반환하지 않고 운영 로그는 합성 text, profile/config 경로와
session/turn 식별자를 기록하지 않는다. standalone TTS launcher는 고정 recipe image가
없을 때 path-safe builder로 먼저 생성한다.

```powershell
powershell -ExecutionPolicy Bypass `
  -File .\evelyn_core\runtime\launchers\start_local_background.ps1
```

Bot API, Control Page, Vision 소스 변경을 이미지에 반영할 때:

```powershell
$env:EVELYN_DOCKER_BUILD = "true"
$env:CONTROL_PAGE_AUTO_OPEN = "false" # 선택
powershell -ExecutionPolicy Bypass `
  -File .\evelyn_core\runtime\launchers\start_local_background.ps1
```

프로젝트 경로에 한글 등 non-ASCII 문자가 있으면 launcher가 사용하지 않는 임시
드라이브 문자에 프로젝트를 매핑한 뒤 요청된 allowlist 서비스 그룹만 빌드한다.
Vision 그룹은 `vision` ingress와 `vision_runtime` 두 이미지를 함께 갱신한다.
launcher는 자신이 만든 매핑임을 다시 확인한 뒤 해제하며 기존 `subst` 매핑은
재사용하거나 삭제하지 않는다.

이미지 교체 전 launcher는 기존 Bot API 컨테이너가 실제로 정지했는지 확인한다.
crash 뒤 `owner_claim.json`이 남아 있어도 이는 경고만 표시하는 진단 파일이며,
새 Bot API가 stable process-lifetime OS lock을 획득해야만 owner가 된다.

`tts`는 `pull_policy: never`라 registry에서 같은 이름의 image를 받지 않는다. 공식
launcher는 image가 없거나 `EVELYN_DOCKER_BUILD=true`로 새 build를 요청했을 때
non-ASCII 경로와 외부 named context를 검증하는 path-safe builder로 TTS image를 만든다.
Host Supervisor의 runtime repair는 build하지 않고 이미 있는 image만 재사용한다.

launcher를 거치지 않고 시작할 때는 먼저 같은 builder로 TTS image를 준비한다.

```powershell
powershell -ExecutionPolicy Bypass `
  -File .\evelyn_core\runtime\launchers\build_local_docker_images.ps1 `
  -ProjectRoot . -Services tts
```

```powershell
docker compose -f docker-compose.fast-control.yml --profile llm --profile tts --profile stt --profile vision --profile voyager up -d --no-build
```

Discord bot worker까지 포함:

```powershell
$env:DISCORD_BOT_TOKEN='<token>'
docker compose -f docker-compose.fast-control.yml --profile llm --profile tts --profile stt --profile vision --profile voyager --profile discord up -d --no-build
```

`discord_bot`만 교체:

```powershell
docker compose -f docker-compose.fast-control.yml --profile llm --profile tts --profile stt --profile vision --profile voyager --profile discord up -d --no-deps discord_bot
```

## 점검

기본 runtime check:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\check_docker_runtime.ps1
```

Discord bot까지 포함:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\check_docker_runtime.ps1 -IncludeDiscordBot
```

명시적으로 시작한 Minecraft/Codex stack까지 포함:

```powershell
powershell -ExecutionPolicy Bypass `
  -File .\tools\check_docker_runtime.ps1 `
  -IncludeMinecraftStack
```

Codex action 실제 호출까지 포함:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\check_docker_runtime.ps1 -IncludeDiscordBot -IncludeCodexAction
```

로컬 마이크/스피커 bridge까지 확인:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\check_docker_runtime.ps1 -IncludeLocalBridge
```

정상 기준:

- 모든 핵심 HTTP health가 응답한다.
- TTS health가 `status=healthy`, `ready=true`, `model_loaded=true`,
  `model_id=k2-fsa/OmniVoice`,
  `model_revision=c5fdb5ccb189668d56333f77ba2629f4cd7535f4`를 모두 만족한다.
- `controlReady`, `botReady`, `mainReady`, `routerReady`, `subReady`, `ttsReady`, `sttReady`, `chatReady`, `voiceReady`, `visionReady`가 `true`다.
- 기본 local core 검사에서는 지연 시작되는 `voyagerReady`, `codexReady`가
  경고일 수 있다.
- `-IncludeMinecraftStack` 사용 시 `voyagerReady`, `codexReady`가 모두
  `true`여야 한다.
- `-IncludeDiscordBot` 사용 시 `evelyn-discord-bot` 상태가 `running`이고 restart loop가 없어야 한다.
- `-IncludeCodexAction` 사용 시 `/codex/action`이 실제 응답까지 반환해야 한다.

## 내부 API 연결

컨테이너 간 호출은 가능한 한 Docker 내부 서비스명을 사용한다.

- Main LLM: `http://main_llm:9820/v1/chat/completions`
- Router LLM: `http://router_llm:9822/v1/chat/completions`
- Sub LLM: `http://sub_llm:9821/v1/chat/completions`
- TTS: `http://tts:8880`
- STT: `http://stt:8892`
- Vision ingress: `http://vision:8891`
- Codex Gateway: `http://codex_gateway:8787/codex/action`
- Voyager: `voyager:8765`

호스트에서 직접 확인할 때만 `127.0.0.1` published port를 사용한다.

Vision의 서비스명과 published port는 최소 ingress가 소유한다. 실제 GPU 모델
runtime인 `vision_runtime`은 외부 gateway가 없는 `vision_isolated` network에만
연결되며 host port를 publish하지 않는다. ingress는 정해진 Vision endpoint만
`vision_runtime:8891`로 전달하고 credential, model cache, runtime artifact를
마운트하지 않는다. 따라서 호출자는 기존 URL을 유지하면서 Falcon-OCR remote
code에는 인터넷이나 다른 Evelyn 서비스로 향하는 route를 주지 않는다.

## 음성 처리

`discord_bot`은 실제 Discord 음성 루프를 담당한다.

- Discord voice 접속과 수신은 `discord_bot`에서 수행한다.
- STT는 로컬 모델을 로드하지 않고 `STT_SERVICE_URL=http://stt:8892`로 원격 STT 서비스를 우선 사용한다.
- `STT_SERVICE_FALLBACK_LOCAL=false`로 두어 컨테이너 안에서 Qwen ASR을 중복 로드하지 않는다.
- TTS는 `OMNIVOICE_SERVER_URL=http://tts:8880`을 사용한다.
- 기본 합성은 `/v1/audio/speech`의 sentence streaming이다. 실험적 blockwise
  streaming은 client disconnect가 model generation까지 안전하게 취소된다는 증거가
  생길 때까지 비활성화한다.
- `voxcpm_fallback`은 host `8881`에서 수동 진단할 수 있는 호환성 서비스일 뿐이다.
  이를 시작해도 Docker client와 Windows bridge는 `tts:8880`에서 자동 reroute되지 않는다.
- 로컬모드는 Docker core와 Windows local I/O bridge로 나뉜다. Docker는 LLM/TTS/STT/Vision/Control/Bot API를 맡고, Windows bridge는 실제 마이크 캡처와 스피커 재생만 맡는다.
- 컨테이너 안에서는 Windows 화면 캡처가 불가능하므로 `discord_bot`의 `VISION_WATCH_ENABLED=false`를 유지한다.
- Windows Host Supervisor의 Host Vision Bridge가 요청별 임시 캡처, foreground
  window metadata, native OCR을 담당하고 별도 `vision` 서비스가 scene 분석을
  담당한다.
- screenshot과 OCR tile은 요청 직후 삭제된다. 정확한 글자 근거가 actionable하지
  않으면 Bot API는 Main LLM 호출 전에 화면 텍스트 주장을 거부한다.

## Codex Gateway

`codex_gateway` 컨테이너는 Linux용 `@openai/codex` CLI를 설치하고 `/health`에 `backendReady`를 노출한다.

현재 구성:

- CLI: `/usr/local/bin/codex`
- host credential directory:
  `runtime_artifacts/secrets/codex_device_home`
- container secret mount: `/run/secrets/evelyn-codex:ro`
- ephemeral CLI home: `/tmp/evelyn-codex-home`
- root filesystem: read-only
- Linux capabilities: all dropped
- custom shell backend: disabled by default

사용자의 live `.codex` 디렉터리를 직접 마운트하지 않는다. 최초 실행 전 전용 사본을
명시적으로 만든다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\evelyn_core\runtime\launchers\provision_codex_credentials.ps1
```

기본 명령은 `auth.json`만 복사한다. Gateway에 사용자 설정이 꼭 필요할 때만
`-IncludeConfig`를 사용한다.

주의: `backendReady=true`는 CLI와 전용 인증 사본이 준비됐다는 뜻이다. 실제
`/codex/action` 호출도 별도로 통과해야 한다. `lastActionReady=false`는 HTTP
서버가 살아 있어도 마지막 실제 action 실행이 실패했다는 뜻이다.

## GPU 배치

현재 Compose는 GPU UUID로 배치를 고정한다.

- RTX 3090: `main_llm`, `tts`, `stt`, `vision_runtime`
- RTX 4060 Laptop GPU: `router_llm`, `sub_llm`
- CPU only: `control_page`, `bot_api`, `discord_bot`, `vision` ingress,
  `voyager`, `codex_gateway`

운영 시 GPU 번호만 보지 말고 실제 GPU 이름도 같이 확인한다.

```powershell
nvidia-smi --query-gpu=index,name,memory.used,memory.free --format=csv,noheader
```

## 로그

```powershell
docker compose -f docker-compose.fast-control.yml --profile llm --profile tts --profile stt --profile vision --profile voyager --profile discord ps
docker logs --tail 200 evelyn-discord-bot
docker compose -f docker-compose.fast-control.yml logs --tail=120 control_page bot_api
docker compose -f docker-compose.fast-control.yml --profile voyager logs --tail=120 voyager codex_gateway
```

## 남은 판단 기준

Docker 전환 완료 판단은 다음 기준으로 한다.

- Control-Page/Bot API/Discord bot loop가 모두 Docker에서 동작한다.
- LLM/TTS/STT/Vision/Voyager/Codex Gateway가 Docker health와 실제 호출 smoke test를 통과한다.
- Codex Gateway는 `/health`뿐 아니라 `/codex/action` 실제 호출까지 통과해야 완전 완료로 본다.
- 호스트에 남는 것은 Docker Desktop, GPU driver, Docker volume/bind mount, Discord/Codex 인증 파일뿐이어야 한다.
