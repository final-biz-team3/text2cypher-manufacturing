# 0010. 로그인 계정 시딩 방식과 초기 환경 설정

## 상태
확정 (2026-08-26)

## 한 줄 요약

> 회원가입 화면을 두지 않고, `app.users` 테이블과 시드 계정을 수동으로 한 번 생성하는 방식을 택했다. 새 환경을 세팅하려면 아래 절차를 따르면 된다.

---

## 배경 — 왜 이 결정이 필요했나

로그인 기능(PR #27)에는 원래 서버 기동 시 `app.users` 테이블을 자동 생성하고 환경변수의 계정을 시딩하는 `bootstrap_users`가 있었다. 하지만 이 프로젝트는 4인 팀이 원격 공유 Postgres 서버 하나를 같이 쓰고, 계정도 admin/user 고정 2개뿐이라 회원가입 화면 자체가 없다. 서버가 켤 때마다 시딩 로직을 도는 건 "이미 있는 계정을 또 만들려는 시도"만 반복하는 죽은 코드가 되어, 해당 로직을 제거했다. 그 결과 "새 환경(로컬 DB, 팀원 재현 등)에서 이 테이블을 어떻게 만드는가"가 코드만 봐서는 알 수 없게 됐다 — 이 문서는 그 절차를 남긴다.

## 결정 — 무엇을 어떻게 하기로 했나

### 1. 서버 기동 시 자동 생성(`bootstrap_users`) 로직 제거

원래 `backend/main.py`의 `lifespan`에서 서버가 뜰 때마다 `app.users` 스키마·테이블을 생성하고, 환경변수(`ADMIN_USERNAME` 등)의 계정을 `ON CONFLICT DO NOTHING`으로 시딩하는 `bootstrap_users` 함수를 호출했다. 이 로직과 관련 환경변수를 전부 제거했다. 이유:

- 팀이 원격 공유 Postgres 서버 하나를 같이 쓰는데, 그 DB에는 이미 관리자가 직접 만들어 넣은 admin/user 계정이 있었다. 서버를 재기동할 때마다 "이미 있으니 건너뛴다"는 로그만 남기고 실제로는 아무 일도 안 하는 코드가 매번 실행되는 셈이었다.
- 이 프로젝트엔 회원가입 화면이 없다 — 계정을 새로 만드는 유일한 방법이 이 부트스트랩 로직뿐이었는데, 그마저도 "이미 만들어진 고정 계정 2개"를 벗어나는 용도로 쓰일 일이 없었다.
- 즉 실행될 때마다 유의미한 일을 하지 않는 코드(및 그걸 위해 `.env`/`.env.example`/`docker-compose.yml`에 남아있던 `ADMIN_USERNAME`/`ADMIN_PASSWORD`/`USER_USERNAME`/`USER_PASSWORD`)를 정리한 것이다.
- 트레이드오프: 그 대가로 "새 환경에서 이 테이블을 어떻게 만드는가"가 코드만 봐서는 더 이상 안 보이게 됐다 — 그래서 이 문서의 2~4번 절차가 필요해졌다.

### 2. `app.users` 스키마

`production.*`(AdventureWorks 비즈니스 데이터)와 분리하기 위해 별도 `app` 스키마를 쓴다.

```sql
CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE IF NOT EXISTS app.users (
    id SERIAL PRIMARY KEY,
    username VARCHAR NOT NULL UNIQUE,
    password_hash VARCHAR NOT NULL,
    role VARCHAR NOT NULL CHECK (role IN ('admin', 'user')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 3. 계정 생성 (이미 완료됨 — 다시 필요할 때만 참고)

현재 공유 DB에 admin/user 계정이 이미 생성돼 있어 지금 당장 실행할 절차는 아니다. DB가 재생성되거나 계정을 추가/재설정해야 하는 상황이 오면 아래 방식을 참고한다. 비밀번호는 반드시 `backend/core/auth.py`의 `hash_password()`(bcrypt)로 해싱해서 넣는다 — 평문 저장 금지. 예시(`backend/venv` 사용):

```python
from core.auth import hash_password
import psycopg
conn = psycopg.connect(...)  # .env의 POSTGRES_* 값 사용
conn.execute(
    "INSERT INTO app.users (username, password_hash, role) VALUES (%s, %s, %s)",
    ("admin", hash_password("원하는_비밀번호"), "admin"),
)
conn.commit()
```

같은 방식으로 `role="user"` 계정도 하나 더 만들면 된다.

### 4. `JWT_SECRET_KEY` 생성

`backend/main.py`의 `check_jwt_secret()`이 32자 미만이면 서버 기동 자체를 막는다. 로컬 개발용 문자열을 손으로 짓지 말고 CSPRNG로 생성한다.

```bash
openssl rand -hex 32
```

생성한 값을 `.env`(커밋 안 됨)의 `JWT_SECRET_KEY`에 넣는다. 배포 환경마다 별도 값을 써야 한다 — 로컬/원격이 같은 값을 공유하면 한쪽에서 유출 시 다른 쪽도 위험해진다.

### 5. 세션 유지 시간 — 리프레시 토큰 대신 만료 시간 연장

로그인 세션은 `core.auth.EXPIRE_HOURS`(현재 24시간) 하나로 JWT 만료와 쿠키 Max-Age를 같이 결정한다. 원래 12시간이었는데, 하루 작업 중 중간에 로그아웃되는 걸 막기 위해 24시간으로 늘렸다.

리프레시 토큰 방식(짧은 액세스 토큰 + 별도 갱신 토큰)도 검토했으나 채택하지 않았다: 리프레시 토큰을 제대로 구현하려면(로그아웃 시 즉시 무효화가 되려면) 서버가 리프레시 토큰을 DB에 저장해야 하는데, 그러면 지금의 무상태(stateless) JWT 구조 자체를 바꿔야 한다. 계정 2개짜리 내부 도구에서 얻는 보안 이득(탈취 노출 시간 최소화) 대비 구현·유지보수 비용이 맞지 않는다고 판단했다.

트레이드오프: 세션이 길어진 만큼 탈취된 토큰의 악용 가능 시간도 늘어난다. 로그아웃해도 서버가 기존 토큰을 무효화할 수단이 없어(무상태 구조), 만료 전까지는 이론상 계속 유효하다.

`EXPIRE_HOURS`는 `core/auth.py` 한 곳에서만 정의하고 `api/auth.py`의 쿠키 Max-Age가 그 값을 그대로 참조한다 — 이전에는 두 파일에 각각 다른 상수(`_EXPIRE_HOURS`, `_COOKIE_MAX_AGE_SECONDS`)로 중복 정의돼 있어서, 한쪽만 고치면 세션이 실제로 안 늘어나는 버그가 될 뻔했다.

## 검토했으나 채택하지 않은 대안

**서버 기동 시 자동 시딩 유지.** 팀이 DB 하나를 공유하는 지금 구조에서는 재기동할 때마다 "이미 있는 계정" 체크만 반복하는 무의미한 코드가 되고, 회원가입이 없어 시딩 로직이 실제로 새 계정을 만들 일도 없다. 여러 환경(로컬 DB 각자 사용 등)을 지원해야 하는 상황이 되면 재도입을 검토한다.

## 결과 및 트레이드오프

- 새 환경(팀원 로컬 DB, CI 등)에서는 이 문서의 절차를 수동으로 한 번 실행해야 로그인이 동작한다 — 자동화돼 있지 않다.
- 계정을 늘리거나 비밀번호를 바꾸려면 이 스크립트를 다시 실행해야 한다(관리 화면 없음).
- 회원가입 기능이 생기면 이 문서의 "계정 생성" 절차는 그 기능으로 대체된다.
