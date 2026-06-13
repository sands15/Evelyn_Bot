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
- `tts`: OmniVoice TTS, `8880`
- `stt`: Qwen3-ASR STT service, `8892`
- `vision`: SmolVLM2 Vision service, `8891`
- `voyager`: Minecraft/Voyager HTTP service, `8765`
- `codex_gateway`: Voyager Codex Gateway HTTP service, `8787`

Discord bot loop는 `discord` profile의 `discord_bot` 서비스로 분리한다. Control-Page/Bot API는 빠른 부팅과 상태 표시를 담당하고, 실제 Discord Gateway 접속과 음성 루프는 `discord_bot`이 담당한다.

## 실행

기본 런타임:

```powershell
cd C:\Evelyn
docker compose -f docker-compose.fast-control.yml --profile llm --profile tts --profile stt --profile vision --profile voyager up -d --build
```

Discord bot worker까지 포함:

```powershell
$env:DISCORD_BOT_TOKEN='<token>'
docker compose -f docker-compose.fast-control.yml --profile llm --profile tts --profile stt --profile vision --profile voyager --profile discord up -d --build
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
- `controlReady`, `botReady`, `mainReady`, `routerReady`, `subReady`, `ttsReady`, `sttReady`, `chatReady`, `voiceReady`, `visionReady`가 `true`다.
- `voyagerReady`, `codexReady`도 현재 Compose 범위에서는 `true`여야 한다.
- `-IncludeDiscordBot` 사용 시 `evelyn-discord-bot` 상태가 `running`이고 restart loop가 없어야 한다.
- `-IncludeCodexAction` 사용 시 `/codex/action`이 실제 응답까지 반환해야 한다.

## 내부 API 연결

컨테이너 간 호출은 가능한 한 Docker 내부 서비스명을 사용한다.

- Main LLM: `http://main_llm:9820/v1/chat/completions`
- Router LLM: `http://router_llm:9822/v1/chat/completions`
- Sub LLM: `http://sub_llm:9821/v1/chat/completions`
- TTS: `http://tts:8880`
- STT: `http://stt:8892`
- Vision: `http://vision:8891`
- Codex Gateway: `http://codex_gateway:8787/codex/action`
- Voyager: `voyager:8765`

호스트에서 직접 확인할 때만 `127.0.0.1` published port를 사용한다.

## 음성 처리

`discord_bot`은 실제 Discord 음성 루프를 담당한다.

- Discord voice 접속과 수신은 `discord_bot`에서 수행한다.
- STT는 로컬 모델을 로드하지 않고 `STT_SERVICE_URL=http://stt:8892`로 원격 STT 서비스를 우선 사용한다.
- `STT_SERVICE_FALLBACK_LOCAL=false`로 두어 컨테이너 안에서 Qwen ASR을 중복 로드하지 않는다.
- TTS는 `OMNIVOICE_SERVER_URL=http://tts:8880`을 사용한다.
- 로컬모드는 Docker core와 Windows local I/O bridge로 나뉜다. Docker는 LLM/TTS/STT/Vision/Control/Bot API를 맡고, Windows bridge는 실제 마이크 캡처와 스피커 재생만 맡는다.
- 컨테이너 안에서는 Windows 화면 캡처가 불가능하므로 `discord_bot`의 `VISION_WATCH_ENABLED=false`를 유지한다. Vision 분석은 별도 `vision` 서비스가 담당한다.

## Codex Gateway

`codex_gateway` 컨테이너는 Linux용 `@openai/codex` CLI를 설치하고 `/health`에 `backendReady`를 노출한다.

현재 구성:

- CLI: `/usr/local/bin/codex`
- auth mount: `C:/Users/Admin/.codex/auth.json:/root/.codex/auth.json:ro`
- config mount: `C:/Users/Admin/.codex/config.toml:/root/.codex/config.toml:ro`

주의: `backendReady=true`는 CLI 실행 파일이 준비됐다는 뜻이다. 실제 `/codex/action` 호출은 Codex 인증 상태까지 통과해야 한다. `refresh_token_reused`가 나오면 호스트의 `C:\Users\Admin\.codex\auth.json`이 재로그인 필요한 상태다.
`lastActionReady=false`는 HTTP 서버가 살아 있어도 마지막 실제 action 실행이 실패했다는 뜻이다.

## GPU 배치

현재 Compose는 GPU UUID로 배치를 고정한다.

- RTX 3090: `main_llm`, `tts`, `stt`, `vision`
- RTX 4060 Laptop GPU: `router_llm`, `sub_llm`
- CPU only: `control_page`, `bot_api`, `discord_bot`, `voyager`, `codex_gateway`

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
