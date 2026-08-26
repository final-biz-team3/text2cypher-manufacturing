# 0011. 대화기록 테이블 스키마

## 상태
확정 (2026-08-26)

## 한 줄 요약

> 대화기록은 `app.conversation_history` 테이블에 저장한다. `app.users`와 마찬가지로 서버 코드가 자동 생성하지 않고 수동으로 한 번 만들어둔 테이블이라, 스키마를 여기에 문서화한다.

---

## 배경 — 왜 이 결정이 필요했나

`feat/conversation-history`(PR #28)의 `backend/core/history.py`는 `app.conversation_history` 테이블에 `INSERT`/`SELECT`를 실행하지만, [0010](0010-auth-setup-and-seed-accounts.md)에서 정리한 `bootstrap_users` 제거와 같은 이유로 이 테이블도 서버 코드가 자동 생성하지 않는다 — 원격 공유 Postgres에 이미 수동으로 만들어둔 테이블이다. 코드만 봐서는 컬럼 타입(특히 `sql_result`/`graph_result`가 JSONB라는 것)을 알 수 없어서, 새 환경을 세팅하거나 리뷰어가 코드를 볼 때 스키마를 추측해야 하는 문제가 있었다. 이 문서는 그 스키마를 남긴다.

## 결정 — 무엇을 어떻게 하기로 했나

### 1. `app.conversation_history` 스키마

`app.users`와 같은 `app` 스키마 아래 둔다. `username`은 `app.users.username`을 참조하는 FK다.

```sql
CREATE TABLE IF NOT EXISTS app.conversation_history (
    id SERIAL PRIMARY KEY,
    username VARCHAR NOT NULL REFERENCES app.users(username),
    query TEXT NOT NULL,
    final_answer TEXT,
    sql_query TEXT,
    cypher_query TEXT,
    sql_result JSONB,
    graph_result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 2. `sql_result`/`graph_result`는 TEXT가 아니라 JSONB

`/chat` 응답의 `sql_result`/`graph_result`는 `{result, error, attempts, empty_reason}` 형태의 중첩 객체다. 이걸 JSONB로 저장하면 psycopg3가 조회 시 자동으로 Python dict/list로 역직렬화해주기 때문에, `list_history`가 별도로 `json.loads`를 호출하지 않아도 되고 프론트도 문자열이 아닌 객체로 바로 받는다. `save_conversation`에서 저장 전에 `json.dumps`를 거치는 건 psycopg3가 bare dict를 자동 직렬화해주지 않기 때문이다(문자열로 넘기면 Postgres가 JSONB로 암묵 캐스트한다).

### 3. 테이블 생성 (이미 완료됨 — 다시 필요할 때만 참고)

현재 공유 DB에 이미 만들어져 있어 지금 당장 실행할 절차는 아니다. DB가 재생성되는 상황이 오면 위 DDL을 그대로 실행하면 된다.

## 검토했으나 채택하지 않은 대안

**컬럼을 TEXT로 두고 애플리케이션에서 직접 `json.dumps`/`json.loads`.** JSONB를 쓰면 얻는 자동 역직렬화, 인덱싱·쿼리 가능성(예: `sql_result->>'error'` 조건 검색)을 포기하는 대신 얻는 이점이 없어서 기각했다.

## 결과 및 트레이드오프

- 새 환경에서는 [0010](0010-auth-setup-and-seed-accounts.md)의 계정 생성 절차와 마찬가지로, 이 문서의 DDL을 수동으로 한 번 실행해야 대화기록 저장이 동작한다 — 자동화돼 있지 않다.
- `username`에 FK가 걸려 있어 `app.users`에 없는 계정으로는 저장이 실패한다(현재는 로그인한 사용자만 `/chat`을 호출할 수 있어 실제로 발생하지 않는다).
