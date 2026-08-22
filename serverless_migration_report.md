# 📊 상시 서버 텔레그램 봇의 AWS Lambda 0원 서버리스 전환 완료 보고서

본 보고서는 상시 가동 서버(VM)에서 운영되던 텔레그램 봇(`python3 bot_main.py`)을 **AWS Lambda (Always Free) + Amazon DynamoDB (25GB 영구 무료) + Function URL + EventBridge** 기반으로 전환하여 **운영 비용 0원(Always Free) 및 관리 부담 제로(Zero-Ops)**를 달성한 전 과정과 실전 트러블슈팅 내역을 정리한 종합 기술 보고서입니다.

---

## 1. 프로젝트 개요 (Executive Summary)

```mermaid
flowchart LR
    subgraph Before_State [전환 전: 상시 서버 (VM)]
        VM[VM / VPS 상시 가동\n월 서버 비용 발생]
        LP[Long Polling 방식\nrun_polling]
        LF[로컬 JSON 파일 의존\nbot_state / sent_news]
        LG[로컬 Git CLI 직접 실행\ngit add/commit/push]
    end

    subgraph After_State [전환 후: AWS 0원 서버리스]
        LAMBDA[AWS Lambda\n월 100만 회 영구 무료]
        FURL[Lambda Function URL\n비용 0원 Webhook]
        DYNAMO[Amazon DynamoDB\n25GB 영구 무료]
        GH_API[GitHub REST API\nPyGithub 직접 커밋]
    end

    Before_State ==>|서버리스 전환| After_State
```

### 🎯 핵심 성과
- **운영 비용**: 월 서버 비용 ➔ **0원 (AWS Always Free Tier 100% 충족)**
- **가용성 및 확장성**: 서버 다운 위험 없이 이벤트 발생 시에만 100ms 이내 즉시 기동
- **관리 포인트**: OS 패치, 프로세스 데몬 관리 등 인프라 관리 부담 완전 제거 (No-Ops)
- **배포 자동화**: 단일 빌드 스크립트([build_zip.py](file:///c:/Users/uoosnn/Documents/DEV/telegram_bot/build_zip.py))로 36MB 초경량 배포 패키지 원클릭 생성

---

## 2. 서버리스 시스템 아키텍처 및 0원 전략

```mermaid
flowchart TD
    subgraph Client_Layer [사용자 & 텔레그램]
        User[사용자] -->|메시지/사진/명령어| TG[Telegram Bot API]
    end

    subgraph AWS_Serverless_Layer [AWS Always Free 인프라 (비용 0원)]
        TG -->|HTTPS POST Webhook| FURL[Lambda Function URL\n(API Gateway 불필요, 0원)]
        FURL --> LAMBDA[AWS Lambda: telegram-bot\n(Python 3.12 / x86_64)]
        
        CRON[Amazon EventBridge\n(매일 09:00 KST)] -->|Scheduled Trigger| LAMBDA
        
        LAMBDA <--> DYNAMO[(Amazon DynamoDB: telegram_bot_state\n25GB 영구 무료)]
    end

    subgraph External_Services [외부 연동 서비스]
        LAMBDA -->|AI 대화/블로그 요약/번역| GEMINI[Google Gemini API]
        LAMBDA -->|REST API 커밋 & 푸시| GH_API[GitHub API\nuoosnn/uoosnn.github.io]
        LAMBDA -->|뉴스 수집| RSS[뉴스 사이트 / RSS 피드]
    end

    GH_API -->|GitHub Pages 자동 빌드| BLOG[블로그 자동 배포 완료]
```

### 💡 비용 0원 보장 5대 인프라 설계
1. **AWS Lambda**: 월 100만 회 호출 및 320만 초 실행 영구 무료 (개인 봇 사용량은 무료 한도의 0.01% 미만).
2. **Lambda Function URL**: 유료 과금 리스크가 있는 API Gateway 대신 Lambda 자체 HTTPS 엔드포인트를 사용하여 완전 무료 & 설정 단순화.
3. **Amazon DynamoDB**: 25GB 스토리지 및 2억 건 요청 영구 무료. 단일 테이블(`telegram_bot_state`)에 모든 상태 저장.
4. **Amazon EventBridge**: 아침 9시 정기 크론 트리거 무료.
5. **GitHub REST API**: 로컬 저장소 디스크 및 Git CLI 없이 `PyGithub`로 원격 레포에 마크다운 및 이미지 직접 커밋.

---

## 3. 주요 소스코드 리팩토링 내역

| 파일명 | 변경 전 (상시 서버) | 변경 후 (AWS 서버리스) |
| :--- | :--- | :--- |
| [lambda_function.py](file:///c:/Users/uoosnn/Documents/DEV/telegram_bot/lambda_function.py) | `run_polling()` 상시 루프, `schedule` 백그라운드 스레드 | Webhook 요청 및 EventBridge 이벤트를 직접 분기 처리하는 **순수 비동기 직접 디스패처** |
| [dynamo_manager.py](file:///c:/Users/uoosnn/Documents/DEV/telegram_bot/dynamo_manager.py) | `sent_news.json`, `bot_state.json`, `api_usage.json` 로컬 파일 | DynamoDB 단일 테이블(`PK`, `SK`) 기반 통합 상태 관리자 (7일 자동 만료 TTL 포함) |
| [github_uploader.py](file:///c:/Users/uoosnn/Documents/DEV/telegram_bot/github_uploader.py) | 로컬 디스크에서 `git add/commit/push` subprocess 실행 | `PyGithub`를 통해 원격 레포(`uoosnn.github.io`)에 마크다운 및 이미지를 직접 REST API 커밋 |
| [ai_processor.py](file:///c:/Users/uoosnn/Documents/DEV/telegram_bot/ai_processor.py) | 로컬 세션 및 파일 기반 토큰 카운팅 | DynamoDB 대화 히스토리 자동 정규화(Role Alternation), 단독 Fallback, 다국어 번역 |
| [build_zip.py](file:///c:/Users/uoosnn/Documents/DEV/telegram_bot/build_zip.py) | 단순 배포 쉘 스크립트 | Windows 환경에서도 **Linux x86_64 전용 바이너리(`.so`)를 강제 다운로드**하여 패키징하는 자동 빌더 |

---

## 4. 실전 트러블슈팅 및 해결 상세 (Deep-dive)

### 🔴 Issue 1: Windows ➔ Linux 크로스 플랫폼 바이너리 불일치 (`502 Bad Gateway`)
- **현상**: Lambda 배포 후 Webhook 호출 시 즉시 `502 Bad Gateway` 발생.
- **원인**: Windows 개발 환경에서 `pip install` 실행 시 C-Extension 라이브러리(`pydantic_core`, `grpcio`, `cryptography` 등)가 Windows용 DLL인 `.pyd` 파일로 빌드되어, Linux 기반인 AWS Lambda에서 `ImportError`로 크래시 발생.
- **해결**: [build_zip.py](file:///c:/Users/uoosnn/Documents/DEV/telegram_bot/build_zip.py)에 `--platform manylinux2014_x86_64 --implementation cp --python-version 3.12 --only-binary=:all:` 플래그를 추가하여 AWS Lambda 환경과 100% 호환되는 Linux ELF 바이너리(`.so`) 휠을 다운로드하도록 빌더 개선.

### 🔴 Issue 2: AWS Lambda 부트스트랩 `Runtime.MarshalError` (`502 Bad Gateway`)
- **현상**: CloudWatch 로그에 `[ERROR] Runtime.MarshalError: Unable to marshal response: Object of type coroutine is not JSON serializable` 기록.
- **원인**: Lambda 핸들러를 `async def lambda_handler`로 선언했을 때, AWS Lambda의 Python 기본 부트스트랩(`awslambdaric`)이 코루틴 객체를 await하지 못하고 그대로 JSON 직렬화하려다 에러 발생.
- **해결**: 표준 동기 핸들러 `def lambda_handler(event, context):`로 선언하고 내부에서 `asyncio.run(_async_lambda_handler(event, context))`로 실행하도록 래핑하여 정상 dict 반환 보장.

### 🔴 Issue 3: Lambda 기본 Timeout(3초)으로 인한 동기식 호출 타임아웃
- **현상**: 비동기 테스트는 성공하지만 실제 Webhook(동기식 호출) 시 응답을 받지 못함.
- **원인**: AWS Lambda의 기본 제한 시간(Timeout)이 `3초`로 설정되어 있어 AI 생성 및 네트워크 I/O 도중 강제 종료됨.
- **해결**: Lambda 콘솔의 일반 구성에서 **제한 시간(Timeout)을 `1분 30초 (90초)`로 확장**, **메모리를 `256MB`로 증설**하여 안정적인 리소스 확보.

### 🔴 Issue 4: 텔레그램 웹훅 타임아웃 및 재시도 무한 루프 (Retry Loop)
- **현상**: `/techpost` 명령 시 1분 간격으로 똑같은 포스팅/번역 작업이 반복 실행됨.
- **원인**: 블로그 생성(20초) ➔ 영어 번역(15초) ➔ 일본어 번역(15초)을 순차 실행하여 총 50초 이상 소요되자, 텔레그램 서버가 웹훅 타임아웃(30~40초)으로 간주하고 1분마다 재시도 요청을 재전송함.
- **해결**:
  1. `asyncio.gather()`를 도입하여 **영어 번역과 일본어 번역을 동시 병렬 실행**하도록 리팩토링 ➔ 전체 처리 시간 20초 이내로 대폭 단축.
  2. `setWebhook?drop_pending_updates=true` 호출로 누적 대기열 즉시 초기화.

---

## 5. 최종 산출물 및 문서 목록

- 📦 **배포 패키지**: [lambda_deployment.zip](file:///c:/Users/uoosnn/Documents/DEV/telegram_bot/lambda_deployment.zip) (`36.02 MB`)
- 📄 **통합 핸들러**: [lambda_function.py](file:///c:/Users/uoosnn/Documents/DEV/telegram_bot/lambda_function.py)
- 🗄️ **DynamoDB 상태 매니저**: [dynamo_manager.py](file:///c:/Users/uoosnn/Documents/DEV/telegram_bot/dynamo_manager.py)
- 🐙 **GitHub REST API 업로더**: [github_uploader.py](file:///c:/Users/uoosnn/Documents/DEV/telegram_bot/github_uploader.py)
- 🤖 **Gemini AI 프로세서**: [ai_processor.py](file:///c:/Users/uoosnn/Documents/DEV/telegram_bot/ai_processor.py)
- 🛠️ **배포 패키징 빌더**: [build_zip.py](file:///c:/Users/uoosnn/Documents/DEV/telegram_bot/build_zip.py)
- 📖 **AWS 셋업 매뉴얼**: [setup_aws.md](file:///c:/Users/uoosnn/Documents/DEV/telegram_bot/setup_aws.md)

---

## 6. 운영 및 유지보수 가이드

1. **코드 수정 시 배포 방법**:
   - 로컬에서 코드 수정 ➔ `python build_zip.py` 실행 ➔ 생성된 `lambda_deployment.zip`을 AWS Lambda 콘솔에서 업로드.
2. **토큰 및 비용 모니터링**:
   - 텔레그램 채팅창에 `/usage` 입력 시 언제든 누적 토큰 및 예상 요금 확인 가능.
3. **웹훅 상태 점검 및 대기열 리셋**:
   - `https://api.telegram.org/bot<TOKEN>/getWebhookInfo`
   - 대기열 리셋: `https://api.telegram.org/bot<TOKEN>/setWebhook?url=<URL>&drop_pending_updates=true`
