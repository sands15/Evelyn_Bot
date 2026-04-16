# Evelyn Bot

디스코드 텍스트/음성 채널에서 한국어로 대화하는 개인용 봇입니다.

핵심 흐름은 아래처럼 단순합니다.

1. 음성 채널에 접속한다.
2. `EvelynVoiceClient`가 디스코드 음성 패킷을 수신하고 복호화한다.
3. 발화 단위 PCM을 `faster-whisper`로 STT 한다.
4. 깨운 말(`이블린`)이 포함된 경우만 LLM에 질문한다.
5. 답변을 `Qwen3TTSModel`로 음성 합성한다.
6. 생성된 WAV를 디스코드 음성 채널로 재생한다.

## 현재 구조

- `main.py`
  - 봇 엔트리 포인트
  - 디스코드 명령어 처리
  - STT -> LLM -> TTS 파이프라인 연결
- `evelyn_voice/`
  - 디스코드 음성 수신용 커스텀 클라이언트
  - DAVE inner decrypt 처리
  - UDP / gateway / sink 구현
- `start.bat`
  - Windows에서 봇 실행
- `.env.example`
  - 필요한 환경변수 예시

## 주요 기능

- 텍스트 호출: 채팅에서 `이블린`을 포함해 말하면 답변
- 음성 호출: 음성 채널에서 `이블린`을 포함해 말하면 답변
- 음성 명령어
  - `!들어와` 또는 `!join`
  - `!다시들어와` 또는 `!rejoin`
  - `!나가` 또는 `!leave`
- 자기 음성 재인식 방지
  - 응답 직후 짧은 무시 시간
  - 최근 발화 유사도 차단
  - 동시 응답 락

## DAVE 음성 수신 관련 정리

이 프로젝트의 핵심 수정점은 디스코드 음성 수신에서 DAVE inner decrypt가 안정적으로 동작하도록 맞춘 것입니다.

핵심 포인트:

- outer decrypt 이후 평문 앞에 붙는 RTP header extension 길이만큼 잘라낸 뒤 inner DAVE decrypt에 전달
- discord.py 원본 핸들러를 먼저 태워서 DAVE 세션 상태를 먼저 동기화
- 초기 제어 프레임이 너무 빨리 도착할 때를 대비해 짧은 버퍼/리플레이 처리 추가
- 과한 패킷 디버그 로그와 덤프 파일 저장 로직 제거
- 기본 동작에서 자동 녹음 파일을 남기지 않도록 변경

## 실행 전 준비

### 1) Python 패키지

기본 패키지 설치:

```bash
pip install -r requirements.txt
```

추가로 아래가 준비되어 있어야 합니다.

- `Qwen3TTSModel`을 제공하는 TTS 패키지 또는 로컬 환경
- `ffmpeg`
- GPU 환경 권장

## 2) 환경변수

`.env.example`를 참고해서 최소한 아래는 준비해야 합니다.

- `DISCORD_BOT_TOKEN`
- `LLM_SERVER_URL`
- `FFMPEG_PATH`

필요하면 아래도 조정하세요.

- `QWEN_TTS_MODEL_ID`
- `QWEN_TTS_SPEAKER`
- `STT_MODEL_NAME`
- `WAKE_WORD`

## 3) LLM 서버

`main.py`는 OpenAI 호환 `/v1/chat/completions` 엔드포인트를 기대합니다.
기본값은 아래입니다.

- `http://127.0.0.1:9820/v1/chat/completions`

즉, 로컬 또는 네트워크 내부에 LLM 서버가 먼저 떠 있어야 합니다.

## 실행 방법

### Windows

```bat
start.bat
```

또는

```bash
py -3 main.py
```

## 사용 방법

### 텍스트 채널

- `이블린 오늘 뭐해?`
- 봇 메시지에 답장하기

### 음성 채널

1. 디스코드 음성 채널에 들어간다.
2. 채팅에서 `!들어와` 입력
3. 음성으로 `이블린`을 포함해 말한다.
   - 예: `이블린 지금 몇 시야?`
4. 봇이 듣고 짧게 한국어로 답한다.

## 업로드에서 제외한 것

저장소에는 로컬 실행 흔적이나 개인 자원을 넣지 않도록 정리했습니다.

예:

- 테스트/실험 스크립트
- 디버그 덤프 폴더
- 임시 가상환경
- `ref_voice.wav`
- `ref_text.txt`
- `pets-*.json`

## 주의

- 이 저장소는 개인 로컬 실행 기준으로 정리되어 있습니다.
- TTS 패키지 이름과 설치 방식은 환경마다 다를 수 있습니다.
- `ffmpeg` 경로와 GPU 환경은 각 PC에 맞게 맞춰야 합니다.
