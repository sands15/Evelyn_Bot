---
tags:
  - evelyn
  - inbox
type: inbox
---

# Evelyn Project Inbox

아직 검증하거나 분류하지 않은 아이디어, 질문, 관찰을 임시로 적는 곳이다.
이 문서의 내용은 현재 구현, 확정 요구사항, 승인된 설계 결정을 의미하지 않는다.

## 작성 형식

```md
### 제목

- 작성일: YYYY-MM-DD
- 종류: 아이디어 | 질문 | 버그 의심 | 사용자 경험 | 조사 필요
- 내용:
- 관련 문서 또는 코드:
- 검토 결과: 미검토
```

## 미검토 항목

없음.

## 처리 완료

### Live2D 꼬리 S자 idle wave

- 작성일: 2025-08-03
- 처리일: 2026-08-12
- 종류: 아이디어
- 내용: 이블린의 꼬리가 s자 형태로 계속 움직였으면 좋겠음
- 관련 문서 또는 코드: [참고 영상](https://www.youtube.com/watch?v=SEt9-YtG0Ro&t=1s),
  `docs/assets/evelyn-live2d.js`, `tests/ui/test_control_page_live2d_assets.py`
- 검토 결과: 구현 확인 완료. 꼬리의 7개 회전 분절이 root sine, 지연 추종,
  위상차, spring/damping으로 계속 흐르며 S자 곡선을 만든다. 실행 중인 로컬
  Control Page에서 2.2초 간격 프레임의 꼬리 위치 변화와 제공 자산의 저장소
  원본 일치를 확인했고, Live2D asset 회귀 16개가 통과했다. 중복 코드는 추가하지 않았다.
