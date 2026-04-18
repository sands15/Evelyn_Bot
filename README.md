# Evelyn Bot

Evelyn Bot은 디스코드에서 텍스트와 음성으로 대화하는 개인용 한국어 봇입니다. 
지향점은 단순합니다. 말을 걸면 바로 듣고, 짧게 이해하고, 최대한 지연 없이 다시 말해주는 쪽입니다.

이 저장소는 특히 디스코드 음성 수신, DAVE 복호화, 그리고 STT -> LLM -> TTS 흐름을 안정적으로 붙이는 데 초점을 맞춰 정리했습니다.

## 이 프로젝트가 하는 일

봇이 음성 채널에 들어가 있으면 사용자의 말을 직접 받아서 다음 순서로 처리합니다.

1. 디스코드 음성 패킷 수신
2. RTP / Discord voice 암호화 해제
3. DAVE inner decrypt 처리
4. 발화 단위 PCM 조립
5. `faster-whisper`로 STT
6. 깨우는 말인 `이블린`이 들어간 경우에만 LLM 호출
7. 답변을 로컬 `omnivoice-server`에 넘겨 음성으로 합성
8. 스트리밍으로 받은 PCM을 디스코드 음성 채널에 바로 재생

핵심은 중간에 쓸데없는 저장 과정을 줄여서 가능한 한 바로 다음 단계로 넘기는 구조라는 점입니다.

## 현재 파일 구성

- `main.py`
  - 봇 엔트리 포인트
  - 텍스트 응답 처리
  - 음성 입력 처리
  - STT -> LLM -> TTS 연결
- `evelyn_voice/`
  - 커스텀 Discord 음성 수신 클라이언트
  - DAVE 처리
  - UDP / gateway / sink 구현
- `start.bat`
  - 사용자용 통합 실행 진입점, 내부적으로 `evelyn_core\start.bat`를 호출
- `evelyn_core\start.bat`
  - LLM 서버 2개와 OmniVoice 서버를 띄우는 실제 통합 배치 파일
- `run_bot.bat`
  - 디스코드 봇 본체만 실행하는 배치 파일
- `.env.example`
  - 환경변수 예시

## 음성 수신이 실제로 어떻게 돌아가는지

이 프로젝트에서 가장 중요한 부분은 디스코드 음성을 받아오는 경로입니다.

일반적인 디스코드 봇은 음성 재생은 쉬워도, 음성 수신은 구현이 꽤 까다롭습니다. 특히 최근 디스코드 음성 환경에서는 DAVE 쪽 처리까지 맞아야 실제 사용자 음성이 안정적으로 풀립니다.

### 1) 음성 채널 접속

사용자가 `!들어와` 또는 `!join`을 입력하면 봇은 일반 `VoiceClient` 대신 `EvelynVoiceClient`로 접속합니다.

이 클라이언트는 음성 수신을 위해 따로 만든 커스텀 클라이언트입니다.

### 2) 음성 패킷 수신

`EvelynVoiceClient`는 디스코드 음성 UDP 패킷을 직접 받습니다.
이때 패킷 안에는 RTP 헤더, 확장 헤더, 암호화된 payload, 그리고 Discord 쪽 음성 세션 상태가 얽혀 있습니다.

### 3) outer decrypt

먼저 기본 Discord voice 암호화 레이어를 풉니다.
여기서 중요한 점은, outer decrypt가 끝난 평문 앞부분에 RTP header extension 데이터가 포함될 수 있다는 점입니다.

이걸 그대로 DAVE decrypt에 넣으면 inner decrypt가 실패합니다.
그래서 이 프로젝트에서는 decrypted RTP extension 길이만큼 앞부분을 잘라낸 뒤, 실제 DAVE payload만 inner decrypt로 넘기도록 수정했습니다.

이 부분이 음성 수신이 정상화된 가장 큰 이유입니다.

### 4) DAVE inner decrypt

outer decrypt 후 잘라낸 payload를 `dave_session`에 넘겨 inner decrypt를 수행합니다.

여기서는 다음 같은 보강이 들어가 있습니다.

- discord.py 원본 핸들러를 먼저 실행해서 세션 상태를 먼저 맞춤
- 너무 빨리 들어온 초기 DAVE 프레임은 잠깐 버퍼링했다가 재적용
- user_id 매핑 재시도
- 과한 패킷 단위 디버그 로그 제거

덕분에 발화가 들어올 때 실제 Opus payload까지 안정적으로 도달할 수 있습니다.

### 5) PCM 조립

복호화에 성공한 Opus 패킷은 디코딩되어 PCM으로 바뀌고, 발화 단위로 묶입니다.
이후 `process_member_audio()` 콜백으로 넘어갑니다.

여기서 중요한 점은, 받은 사용자 음성을 녹음 파일로 저장하지 않는다는 것입니다.

예전 디버그 버전처럼 WAV 덤프를 남기지 않고,
받은 PCM은 메모리 안에서 바로 다음 단계인 STT로 넘깁니다.

즉, 흐름은 이런 식입니다.

- Discord 음성 수신
- 복호화
- PCM 조립
- 메모리에서 바로 STT

중간 저장 파일이 없습니다.

## STT -> LLM -> TTS 흐름

### STT

음성으로 들어온 PCM은 `faster-whisper`로 바로 전달됩니다.
이때 내부적으로는 다음 정도만 수행합니다.

- 스테레오를 모노로 downmix
- 48kHz를 16kHz로 resample
- Whisper 입력으로 전달

이 변환 역시 메모리 안에서 바로 처리합니다.
사용자 음성을 `.wav`로 떨궈놓고 다시 읽는 식으로 돌리지 않습니다.

### Wake word 필터링

STT 결과에 `이블린`이 들어 있을 때만 응답합니다.
아무 말에나 반응하지 않게 해서 불필요한 LLM 호출과 TTS 재생을 줄였습니다.

또 아래 같은 보호 로직이 들어 있습니다.

- 너무 짧은 말 무시
- 최근 응답과 너무 비슷한 말 무시
- 봇이 막 말한 직후에는 잠깐 무시
- 같은 길드에서 동시 응답 방지

이런 것들은 정확도도 올리지만, 쓸데없는 처리량과 레이턴시 낭비를 줄이는 데도 도움이 됩니다.

### LLM

STT로 얻은 문장을 정리한 뒤, OpenAI 호환 `/v1/chat/completions` 엔드포인트로 보냅니다.
기본 응답 모델은 아래처럼 큰 모델을 사용합니다.

- `http://127.0.0.1:9820/v1/chat/completions`

그리고 작은 모델은 별도로 돌려서 실제 답변 대신 메모리 관리에 씁니다.

- `http://127.0.0.1:9821/v1/chat/completions`

즉 역할을 이렇게 나눴습니다.

- 큰 모델: 실제 답변 생성
- 작은 모델: 롤링 요약 갱신, 장기 기억 후보 추출, 열린 작업 정리

큰 모델은 필요할 때 OmniVoice 감정 태그도 함께 낼 수 있습니다. 현재 허용 태그는 아래뿐입니다.

- `[laughter]`
- `[sigh]`
- `[confirmation-en]`
- `[question-en]`
- `[question-ah]`
- `[question-oh]`
- `[question-ei]`
- `[question-yi]`
- `[surprise-ah]`
- `[surprise-oh]`
- `[surprise-wa]`
- `[surprise-yo]`
- `[dissatisfaction-hnn]`

이 태그들은 TTS 입력에는 유지되고, 일반 텍스트 표시에는 자동으로 제거됩니다.

또한 작은 모델의 `answer / ask / wait` 판단에 맞춰 태그 힌트도 달라집니다.

- `ask`면 질문형 태그를 우선 고려
- `wait`면 태그를 거의 쓰지 않음
- `answer`면 확인, 놀람, 가벼운 웃음 태그를 필요할 때만 사용

현재 라우팅은 이렇게 나뉩니다.

- `main_direct`: 메인 LLM 직행, sub를 기다리지 않음
- `sub_hint`: 저장돼 있던 sub 결과만 힌트로 참고
- `sub_wait`: 정말 문맥이 깊거나 애매할 때만 fresh sub 판단을 잠깐 기다림
- 음성 입력은 기본적으로 `main_direct`로 처리하고, sub는 뒤에서 raw/summary/state 저장만 갱신

로컬 서버를 쓰는 이유도 결국 레이턴시 때문입니다.
왕복이 짧고, 응답 속도를 직접 통제하기 쉽습니다.

### 장기 기억 구조

장기 기억은 단순히 이전 대화를 전부 프롬프트에 넣는 방식이 아니라, 작은 모델이 따로 관리하는 구조입니다.

현재는 길드별로 아래 파일들이 생깁니다.

- `bot_memory/guild_<id>/raw_transcript.jsonl`
- `bot_memory/guild_<id>/rolling_summary.txt`
- `bot_memory/guild_<id>/durable_facts.jsonl`
- `bot_memory/guild_<id>/open_questions.jsonl`
- `bot_memory/guild_<id>/cognitive_state.json`
- 기존 호환용 `bot_memory/guild_<id>/open_loops.jsonl`

대화가 끝나면 작은 모델이 백그라운드에서 다음 작업을 합니다.

- 최근 raw 원문 로그를 누적 저장
- 최근 대화를 짧은 요약으로 갱신
- 오래 기억할 만한 사실 추출
- 아직 확인이 필요한 질문이나 가설 정리

그리고 새 입력이 오면 작은 모델이 먼저 현재 상황을 보고 `answer`, `ask`, `wait` 중 어떤 태도가 자연스러운지 판단한 뒤, 그 상태를 큰 모델 프롬프트에 힌트로 붙입니다.
이 방식이 컨텍스트를 무작정 늘리는 것보다 훨씬 안정적이고, 작은 모델을 실제로 유용하게 쓰는 방법에 가깝습니다.

### TTS

LLM이 답변을 만들면 `main.py`가 직접 모델을 들고 합성하는 대신, 로컬 `omnivoice-server`에 요청을 보냅니다.

여기서 레이턴시를 줄이기 위해 두 가지를 같이 적용했습니다.

- 요청은 `stream=true`, `response_format=pcm`으로 보냄
- 서버가 돌려주는 24kHz mono PCM을 받아서 봇 안에서 바로 48kHz stereo PCM으로 변환해 재생함

즉, 예전처럼

- 임시 WAV 파일 생성
- `ffmpeg` 프로세스 실행
- 파일 또는 파이프를 다시 읽어서 재생

이 경로를 거치지 않습니다.

지금 출력 쪽 흐름은 이렇게 바뀌었습니다.

- 텍스트 답변 생성
- 로컬 OmniVoice 서버에 바로 TTS 요청
- PCM 스트림 수신
- 봇 안에서 바로 디스코드 재생 포맷으로 변환
- 음성 채널로 즉시 재생

중간 산출물을 파일로 남기지 않고, 별도 ffmpeg 프로세스도 쓰지 않습니다.

## 레이턴시를 줄이기 위해 신경 쓴 점

이 프로젝트는 "일단 되게 만드는 것"보다 "되면서도 답답하지 않게 만드는 것"을 중요하게 봤습니다.

현재 레이턴시를 줄이기 위해 반영된 포인트는 아래와 같습니다.

- 패킷 디버그 덤프 저장 제거
- 사용자 음성 WAV 저장 제거
- TTS 임시 파일 저장 제거
- ffmpeg subprocess 제거
- 복호화 후 바로 PCM 처리
- PCM을 메모리에서 바로 Whisper로 전달
- 로컬 LLM 서버 사용
- 로컬 OmniVoice 서버 사용
- TTS를 PCM 스트리밍으로 받아 첫 청크부터 재생 가능하게 구성
- HTTP 세션 재사용으로 요청 연결 오버헤드 감소
- wake word 기반 응답으로 불필요한 호출 감소
- 너무 짧은 발화와 짧은 잡음 인식 결과 무시
- 응답 중복 차단과 lock으로 겹치는 처리 방지
- 작은 모델을 백그라운드 메모리 관리자 역할로 분리

아직 LLM까지 토큰 스트리밍으로 끊어서 읽는 구조는 아니지만, 적어도 TTS 쪽은 파일 저장과 ffmpeg 호출을 없애고 스트리밍 재생 쪽으로 당겨서 체감 지연을 꽤 줄였고, 메모리 쪽은 작은 모델을 따로 써서 긴 대화 대응력을 보강한 상태입니다.

## 주요 명령어

- `!들어와` 또는 `!join`
  - 사용자가 있는 음성 채널로 들어감
- `!다시들어와` 또는 `!rejoin`
  - 음성 연결을 끊고 다시 붙음
- `!나가` 또는 `!leave`
  - 음성 채널에서 나감

## 실행 전 준비

### 1) Python 패키지 설치

```bash
pip install -r requirements.txt
```

추가로 환경에 따라 아래가 준비되어 있어야 합니다.

- 로컬 `omnivoice-server` 실행 환경
- CUDA 가능한 GPU 환경 권장

### 2) 환경변수

`.env.example`를 참고해서 최소한 아래는 준비해야 합니다.

- `DISCORD_BOT_TOKEN`
- `LLM_SERVER_URL`
- `OMNIVOICE_SERVER_URL`
- `OMNIVOICE_VOICE`

필요하면 아래도 조정할 수 있습니다.

- `SUMMARY_LLM_URL`
- `SUMMARY_MODEL_NAME`
- `BOT_MEMORY_DIR`
- `OMNIVOICE_LANGUAGE`
- `OMNIVOICE_STREAM`
- `OMNIVOICE_TIMEOUT_SEC`
- `STT_MODEL_NAME`
- `WAKE_WORD`
- `AUTO_JOIN_VOICE`
- `VOICE_MIN_TRANSCRIBED_LEN`
- `VOICE_MIN_AUDIO_SEC`

## 실행 방법

### 1) 서버들 실행

루트의 `start.bat`는 사용자용 진입점이고, 실제 실행 로직은 `evelyn_core\start.bat`에 있습니다.
`start.bat`는 봇 자체가 아니라, 로컬 추론 서버들을 띄우는 배치 파일입니다.

현재 구성:

- `llama-server` 9821
- `llama-server` 9820
- `omnivoice-server` 8880

실행:

```bat
start.bat
```

참고로 OmniVoice 서버는 전용 Python 환경에서 실행되도록 분리했습니다.
이유는 `qwen_tts`와 `omnivoice`가 서로 다른 `transformers` 계열을 요구할 수 있어서, 한 환경에 섞어두면 쉽게 깨지기 때문입니다.

또한 복제 음성 프로필은 아래 폴더에 저장되도록 맞춰두었습니다.

- `C:\Evelyn\omnivoice_profiles`

### 1-1) OmniVoice 복제 음성 프로필 만들기

서버가 떠 있는 상태에서 아래 스크립트로 프로필을 만들 수 있습니다.

```bat
create_omnivoice_profile.bat 내목소리 C:\path\to\ref_voice.wav "여기에 기준 문장"
```

만들어진 프로필은 OmniVoice 서버에서 `clone:내목소리` 형태로 사용할 수 있습니다.
기본 예시는 `OMNIVOICE_VOICE=clone:evelyn` 기준으로 잡아뒀으니, 실제로는 `evelyn` 프로필 이름으로 만들어두는 편이 가장 덜 헷갈립니다.

예를 들면 OpenAI 호환 요청에서:

```json
{
  "model": "omnivoice",
  "input": "안녕하세요, 테스트입니다.",
  "voice": "clone:내목소리"
}
```

처럼 호출하면 됩니다.

### 2) 봇 본체 실행

디스코드 봇은 `run_bot.bat`로 따로 실행합니다.

```bat
run_bot.bat
```

또는 직접:

```bash
py -3 main.py
```

## 사용 예시

### 텍스트 채널

- `이블린 오늘 뭐해?`
- 봇 메시지에 답장하기

### 음성 채널

1. 디스코드 음성 채널에 들어갑니다.
2. 텍스트 채널에서 `!들어와`를 입력합니다.
3. 그다음 `이블린`을 포함해서 말합니다.
   - 예: `이블린 지금 몇 시야?`
   - 예: `이블린 오늘 날씨 어때?`
4. 봇이 음성을 받아 STT -> LLM -> TTS 순서로 처리한 뒤 답합니다.

## 저장소에 포함하지 않은 것

로컬 흔적이나 개인 자원은 저장소에 넣지 않도록 정리했습니다.

예를 들면 아래 같은 것들입니다.

- 테스트 / 실험 스크립트
- 디버그 덤프 폴더
- 임시 가상환경
- `ref_voice.wav`
- `ref_text.txt`
- `pets-*.json`

## 참고

이 저장소는 개인 로컬 실행 기준으로 정리되어 있습니다.
환경에 따라 TTS 패키지 설치 방식이나 모델 경로는 조금씩 다를 수 있습니다.

그래도 핵심 구조는 같습니다.

- 음성을 받는다
- 저장하지 않고 바로 STT로 넘긴다
- 필요한 경우만 LLM에 묻는다
- 답변을 메모리에서 바로 TTS로 만들고 바로 재생한다

즉, 최대한 짧은 경로로 듣고, 이해하고, 다시 말하는 봇입니다.
