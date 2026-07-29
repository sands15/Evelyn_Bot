# Storage Retention Report Contract

Document status: **Current**
Last reviewed: 2026-07-29 KST

## Purpose

Host Supervisor는 Evelyn의 누적 가능 산출물을 주기적으로 검사한다. 이 작업은
항상 dry-run이며 파일을 삭제하지 않는다. Control Page는 생성된 보고서를 읽기
전용으로 보여준다.

## Artifact

경로:

`runtime_artifacts/retention/status.json`

공개 스키마:

```json
{
  "schema": "storage_retention.report.v1",
  "state": "clear|attention|error",
  "generatedAt": 0,
  "nextScanAt": 0,
  "dryRun": true,
  "automaticDeletion": false,
  "summary": {
    "scopeCount": 3,
    "errorCount": 0,
    "candidateCount": 0,
    "candidateBytes": 0
  },
  "scopes": {
    "runtimeArtifacts": {},
    "hostLogs": {},
    "voiceDebug": {}
  }
}
```

보고서에는 파일 내용, transcript, raw audio, 절대 경로, 개별 파일명이 포함되지
않는다. 범위별 추적 개수와 정리 후보 개수·바이트, 규칙별 집계만 저장한다.

## Schedule and failure behavior

- Host Supervisor 시작 직후 첫 보고서를 생성한다.
- 기본 주기는 6시간이며 `EVELYN_RETENTION_REPORT_INTERVAL_SEC`로 조정할 수 있다.
- 한 범위가 실패해도 다른 범위 결과는 유지하고 해당 범위만 `error`로 기록한다.
- 마지막 보고서가 기본 12시간보다 오래되면 API가 `stale`로 표시한다.
- Supervisor 종료 시 보고 스레드를 정상 종료한다.

## Control Page API

`GET /api/control-page/storage-retention`

응답의 `policy`는 항상 다음 경계를 명시한다.

```json
{
  "dryRunOnly": true,
  "automaticDeletion": false,
  "applyApiAvailable": false
}
```

삭제용 POST/apply API와 UI 버튼은 제공하지 않는다.

## Deletion boundary

실제 삭제는 기존 retention CLI의 명시적 `--apply` 실행으로만 가능하다. 후보가
보고되더라도 별도 검토와 사용자 승인이 없으면 apply를 실행하지 않는다.
