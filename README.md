# 모의투자 원장

가상의 1억 원으로 **코스피·코스닥·나스닥·중국(본토+홍콩) 각 100종목**에 투자하고, 기간이 끝나면 결과 보고서를 뽑는 웹앱입니다.

- 각자 아이디(이메일)·비밀번호로 가입하면 **자기 계좌만** 보입니다.
- 로그인 후 **시작일·종료일**을 정하고 시작합니다. 그 기간에만 매매할 수 있고, 종료일 종가로 수익률이 확정됩니다.
- **실제 주가**를 약 1시간마다 가져와 반영합니다. 해외 종목은 그 시점 **환율**로 원화 환산합니다.
- 전부 무료 서비스로 돌아갑니다: GitHub(시세 수집 + 웹 호스팅) + Supabase(로그인 + DB).

```
GitHub Actions (매시간)  ──시세──▶  Supabase (DB · 로그인)  ◀──읽기/주문──  GitHub Pages (index.html)
   scripts/fetch_prices.py            supabase/schema.sql                       학생들의 브라우저
```

---

## 배포하기 (처음 한 번, 30~40분)

### 1단계. Supabase 프로젝트 만들기
1. <https://supabase.com> 에 가입하고 **New project** 를 만듭니다. (Region은 `Northeast Asia (Seoul)` 권장, DB 비밀번호는 아무거나 길게)
2. 왼쪽 메뉴 **SQL Editor** → **New query** → 이 저장소의 `supabase/schema.sql` 내용을 **전부 복사해 붙여넣고 Run**.
   `Success. No rows returned` 가 보이면 성공입니다.
3. 왼쪽 메뉴 **Authentication → Sign In / Providers → Email** 에서 **Confirm email 을 끕니다(OFF)** → Save.
   - 이걸 켜 두면 Supabase 기본 메일 발송 한도(시간당 2통) 때문에 친구들이 가입을 못 합니다. 끄면 가입 즉시 로그인됩니다.
4. 키 두 개를 확인합니다. (**Settings → API Keys**, 또는 화면 위쪽 **Connect** 버튼)
   - **Project URL** : `https://xxxxxxxx.supabase.co`
   - **Publishable key** : `sb_publishable_...` ← 웹페이지에 넣는 공개용 키
   - **Secret key** : `sb_secret_...` ← 시세 수집기만 쓰는 비밀 키. **절대 파일에 적지 말 것**

### 2단계. GitHub 저장소 만들고 파일 올리기
1. <https://github.com> 가입 → 오른쪽 위 **+ → New repository** → 이름 예: `mock-invest`, **Public** 선택 → Create.
   (Public 이어야 GitHub Pages 와 Actions 가 무료입니다. 비밀 키는 저장소에 들어가지 않으니 안전합니다.)
2. **Add file → Upload files** 로 ZIP을 푼 폴더 안의 내용물을 끌어다 놓고 Commit 합니다.
   `index.html`, `config.js`, `README.md`, `data/`, `scripts/`, `supabase/`
3. `.github` 폴더는 숨김 폴더라 끌어다 놓기로 안 올라가는 경우가 많습니다. 직접 만듭니다:
   **Add file → Create new file** → 파일 이름 칸에 `.github/workflows/update-prices.yml` 을 그대로 입력(슬래시를 치면 폴더가 만들어짐)
   → ZIP 안의 같은 파일 내용을 복사해 붙여넣기 → Commit.

### 3단계. config.js 에 주소와 공개 키 넣기
저장소에서 `config.js` 를 열고 연필(Edit) 아이콘 → 두 줄을 1단계에서 확인한 값으로 바꾸고 Commit.
```js
SUPABASE_URL: "https://xxxxxxxx.supabase.co",
SUPABASE_KEY: "sb_publishable_........",
```

### 4단계. 비밀 키를 GitHub Secrets 에 등록
저장소 **Settings → Secrets and variables → Actions → New repository secret** 으로 두 개를 만듭니다.

| Name | Secret |
|---|---|
| `SUPABASE_URL` | `https://xxxxxxxx.supabase.co` |
| `SUPABASE_SECRET_KEY` | `sb_secret_........` |

### 5단계. 시세 첫 수집 (과거 1년치 포함)
저장소 **Actions** 탭 → (처음이면 "I understand my workflows…" 버튼으로 활성화) → 왼쪽 **update-prices** → **Run workflow** → mode 를 **backfill** 로 선택 → Run.
5~10분쯤 걸립니다. 초록색 체크가 뜨면 성공이고, 이후에는 **평일 매시 17분**에 자동으로 돕니다.

> 빨간 X 가 뜨면 그 실행을 눌러 로그 마지막 부분을 복사해 두세요. 대부분 ① Secrets 이름 오타 ② schema.sql 미실행 ③ 야후 일시 차단(잠시 뒤 재실행) 중 하나입니다.

### 6단계. 종목코드 점검 (권장, 한 번)
같은 화면에서 mode 를 **verify** 로 실행하면, 로그와 `verify-report` 파일(실행 결과 화면 아래 Artifacts)에 **내가 적은 종목명 ↔ 야후가 알려주는 종목명**이 나란히 찍힙니다.
- 나스닥 100종목은 2026-09-21 기준 실제 나스닥100 구성종목으로 확인했습니다.
- **코스피·코스닥·중국 목록은 시가총액 순위를 자동으로 받아온 것이 아니라 정리해 넣은 것**이라, 순위·구성이 실제 "정확한 상위 100"과 조금 다를 수 있고 드물게 코드가 틀렸을 수 있습니다. `NO DATA` 나 이름이 다른 줄이 있으면 아래 "종목 바꾸기"대로 고치면 됩니다.

### 7단계. 웹페이지 공개
저장소 **Settings → Pages → Build and deployment** → Source: **Deploy from a branch**, Branch: **main / (root)** → Save.
1~2분 뒤 `https://<깃허브아이디>.github.io/<저장소이름>/` 주소가 생깁니다. 이 링크를 공유하면 끝입니다.

---

## 쓰는 법
1. **회원가입**: 이메일 + 비밀번호(6자 이상) + 닉네임. (이메일이 곧 아이디입니다. 인증 메일은 가지 않습니다.)
2. **기간 정하기**: 시작일(오늘 이후)과 종료일(종강일 등)을 정하고 시작.
3. **종목 찾기** 탭에서 종목을 눌러 차트를 보고 **수량** 또는 **금액**으로 매수. 소수점 4자리까지 살 수 있어 1억을 원하는 비율로 정확히 나눌 수 있습니다.
4. **내 계좌**에서 총자산·수익률·국가별 5종목 달성 여부를, **결과 보고서**에서 국가별/종목별 성과, 주가 효과와 환율 효과, 같은 기간 지수 등락률을 확인. CSV 내려받기와 인쇄(PDF 저장)가 됩니다.

## 매매 규칙 (보고서에 그대로 적어도 됩니다)
- 주문은 **서버에 저장된 가장 최근 시세로 즉시 체결**됩니다. 시세는 약 1시간 주기 + 야후 지연(15~20분)이 있어 증권사 실시간 가격과 다를 수 있습니다.
- 장이 닫힌 시간에 주문하면 **직전 종가**로 체결됩니다.
- 현금은 원화 하나입니다. 해외 종목은 체결 시점 환율로 환전해 사고, 팔 때 그 시점 환율로 원화가 됩니다.
- **수수료·세금·배당·환전 스프레드는 반영하지 않습니다.** 액면분할은 자동 반영됩니다.
- 휴장일(예: 한국 추석 연휴, 중국 국경절 10/1~)에는 해당 시장 시세가 멈춰 있는 것이 정상입니다.
- 과거 날짜로 시작할 수는 없습니다(결과를 알고 고르는 것을 막기 위해).

---

## 관리

### 종목 바꾸기
`data/tickers.csv` 를 GitHub 에서 직접 수정하면 다음 수집 때 반영됩니다.
- `ticker` 는 **야후 파이낸스 심볼**입니다: 코스피 `005930.KS`, 코스닥 `196170.KQ`, 미국 `NVDA`, 상하이 `600519.SS`, 선전 `300750.SZ`, 홍콩 `0700.HK`(4자리)
- 줄을 지우면 목록에서 사라지고(이미 보유한 사람의 기록은 남음), 줄을 추가하면 과거 1년 일봉까지 자동으로 채웁니다.
- **코스닥→코스피 이전상장**(예: 알테오젠이 2026년 내 이전 준비 중)이 생기면 수집기가 `.KQ ↔ .KS` 를 자동으로 바꿔 조회하므로 시세는 계속 들어옵니다. 로그에 안내가 뜨면 CSV 도 고쳐 두면 깔끔합니다.

### 시세가 안 들어올 때
1. Actions 탭에서 최근 실행이 초록색인지 확인. 빨간색이면 로그 확인.
2. 야후가 비공식 접근을 막는 경우가 가끔 있습니다. 워크플로는 매번 최신 `yfinance` 를 설치하므로 보통 며칠 내 저절로 해결됩니다. 급하면 **Run workflow → all** 로 재실행.
3. 앱 오른쪽 위 **시세 갱신 시각**이 주황색이면 사흘 넘게 수집이 멈춘 것입니다.
4. GitHub 의 예약 실행은 몇 분~수십 분 늦을 수 있습니다(무료 서비스 특성).

### 알아둘 점
- GitHub 는 60일간 활동 없는 공개 저장소의 예약 작업을 멈춥니다 → 워크플로가 하루 한 번 `data/last_run.txt` 를 커밋해 방지합니다.
- Supabase 무료 프로젝트는 1주일간 요청이 없으면 일시정지됩니다 → 평일 매시간 수집이 돌아 방지됩니다. (긴 연휴 뒤 정지됐다면 대시보드에서 Restore)
- 누군가 비밀번호를 잊으면: Supabase **Authentication → Users** 에서 해당 사용자를 지우고 다시 가입하게 하거나(계좌는 새로 시작), 관리자가 비밀번호를 재설정해 줍니다.
- 학기가 끝나면 Actions 탭에서 워크플로를 **Disable** 해 두세요.

### 파일 구성
| 파일 | 역할 |
|---|---|
| `index.html` | 앱 화면 전체 (로그인·매매·보고서) |
| `config.js` | Supabase 주소와 공개 키 |
| `data/tickers.csv` | 종목 목록 (수정 가능) |
| `scripts/fetch_prices.py` | 시세·환율 수집기 |
| `.github/workflows/update-prices.yml` | 매시간 수집 예약 |
| `supabase/schema.sql` | DB 테이블·보안 규칙·체결 함수 |

### 보안 메모
- 브라우저는 가격을 보낼 수 없습니다. 체결가는 서버 함수(`execute_trade`)가 DB 시세로 정합니다.
- 계좌·보유·거래 테이블은 **본인 것만 읽기**가 되고 직접 쓰기는 막혀 있습니다(RLS). 다른 사람 계좌는 볼 수 없습니다.
- `sb_secret_...` 키는 GitHub Secrets 에만 있습니다. 혹시 유출되면 Supabase **Settings → API Keys** 에서 지우고 새로 만들어 Secrets 를 바꾸세요.
