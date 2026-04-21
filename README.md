# roscamp-repo-1
# Moosinsa Project
## 프로젝트 개요
ROS2와 AI를 활용한 자율주행 로봇개발자 부트캠프 1팀 저장소. 무신사 (Moosinsa) : 자율주행 로봇과 로봇팔을 이용한 언택트 슈즈 피팅 및 매장 재고 관리 시스템

## 구조
- `apps/`     — UI 클라이언트 (PySide6, React)
- `services/` — 백엔드 서버 (API Server, AI Server, FMS)
- `src/`      — ROS2 패키지 (공통, 디바이스별)
- `docker/`   — Docker override 파일

---

## 브랜치 전략

```
main      건드리지 않음. 팀장이 검증 후 수동 merge만
develop   통합 및 테스트 브랜치. feature 작업 merge 대상
feature/  개인 기능 개발 브랜치. develop에서 따고 develop으로 merge
```

브랜치 네이밍 규칙:
```
feature/[이름]-[컴포넌트]-[기능]

예)
feature/daejaehun-api-tryon
feature/sujin-kiosk-main
feature/minho-cv-yolo
```

---

## 작업 시작부터 끝까지 Git 명령어 순서

### 1단계 — 처음 한 번만: 저장소 클론

```bash
git clone https://github.com/addinedu-roscamp-10th/roscamp-repo-1.git
cd moosinsa_project
```

---

### 2단계 — 작업 시작 전 (매번)

```bash
# develop 브랜치로 이동
git checkout develop

# 최신 상태로 업데이트 (반드시 먼저)
git pull origin develop

# 내 feature 브랜치 생성 (작업마다 새로 만들기)
git checkout -b feature/[이름]-[컴포넌트]-[기능]

# 예시
git checkout -b feature/daejaehun-api-tryon
```

---

### 3단계 — 작업 중 (수시로)

```bash
# 변경된 파일 확인
git status

# 변경 내용 확인(확인 후 q로 나오기)
git diff

# 파일 스테이징 (전체)
git add .

# 또는 특정 파일만
git add services/main_server/api_server/main.py

# 커밋 (아래 커밋 메시지 규칙 참고)
git commit -m "Feat: 시착 요청 API 엔드포인트 추가"
```

---

### 4단계 — 작업 완료 후 push 및 branch 삭제

```bash
# 내 feature 브랜치 push
git push origin feature/[이름]-[컴포넌트]-[기능]

# 예시
git push origin feature/daejaehun-api-tryon

# push하면 GitHub Actions가 자동으로 develop에 merge해줌
# 1. feature 브랜치 push 후 Actions 탭에서 초록 체크 확인
# 2. 빨간 X 뜨면 클릭해서 에러 확인 후 팀장한테 공유

# 원격 브랜치 삭제
git push origin --delete feature/daejaehun-api-tryon

# 로컬 브랜치 삭제
git branch -d feature/daejaehun-api-tryon
---

### 5단계 — 다음 작업 시작할 때

```bash
# 다시 develop으로 이동
git checkout develop

# 최신 상태로 업데이트 (다른 팀원 작업 반영)
git pull origin develop

# 새 feature 브랜치 생성
git checkout -b feature/[이름]-[다음기능]
```

---

### 충돌(conflict)이 났을 때

```bash
# develop 최신 내용을 내 브랜치에 먼저 합치기
git checkout feature/daejaehun-api-tryon
git pull origin develop

# 충돌 파일 열어서 직접 수정 후
git add .
git commit -m "Fix: develop 브랜치 충돌 해결"
git push origin feature/daejaehun-api-tryon
```

---

### 롤백이 필요할 때 (팀장만 실행)

```bash
# 커밋 이력 확인
git log --oneline -10

# 이력 남기며 되돌리기 (권장)
git revert [commit hash]
git push origin develop

# 강제 롤백 (신중하게)
git reset --hard [commit hash]
git push origin develop --force
```

---

## 커밋 메시지 규칙

규칙 출처: https://github.com/SpaceStationLab/git-commit

### 전체 포맷

```
Type: 제목 (50자 이내, 마침표 없음, 명령조, 첫 글자 대문자)

본문 (선택, 무엇을/왜 했는지, 한 줄 72자 이내)

Footer (선택, 이슈 연결)
```

### Type 종류

| Type | 설명 |
|---|---|
| `Feat` | 새로운 기능 추가 |
| `Fix` | 버그 수정 |
| `Docs` | 문서 수정 |
| `Style` | 코드 포맷, 세미콜론 등 (기능 변경 없음) |
| `Refactor` | 리팩토링 |
| `Test` | 테스트 코드 추가/수정 |
| `Chore` | 빌드, 패키지 설정 등 기타 변경 |

### 예시

```bash
# 제목만 (짧은 작업)
git commit -m "Feat: 시착 요청 API 엔드포인트 추가"
git commit -m "Fix: 키오스크 메인화면 버튼 클릭 오류 수정"
git commit -m "Docs: README 실행 방법 업데이트"
git commit -m "Chore: requirements.txt opencv 버전 업데이트"

# 본문 포함 (설명이 필요한 작업)
git commit -m "Feat: YOLO 모델 교체

기존 yolov8n → yolov11s로 교체
신발박스 인식 정확도 개선 목적"

# 이슈 연결
git commit -m "Fix: DB 연결 타임아웃 오류 수정

해결: #12"
```

### 주의사항
- 제목은 50자 이내, 마침표 없음
- 과거형 금지 → `추가했다` (X) / `추가` (O)
- 제목과 본문 사이 빈 줄 한 줄
- 본문은 how가 아닌 what/why 위주로 작성

---

## 실행 방법

### Main Server
```bash
cd services/main_server
docker compose up -d
```

### AI Server
```bash
cd services/ai_server
docker compose up -d
```

### FMS
```bash
cd services/fms
bash setup.sh
```

### UI 클라이언트
```bash
pip install -r apps/kiosk_ui/requirements.txt
pip install -r apps/admin_ui/requirements.txt
pip install -r apps/sshopy_ui/requirements.txt
cd apps/smartphone_ui && npm install
```
