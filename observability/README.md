# 로컬 모니터링 스택

데이터베이스와 백엔드는 기본 Compose 파일로 실행한다.

```bash
docker compose up -d
```

Prometheus, Loki, Alloy, Grafana는 별도 Compose 프로젝트로 실행한다.

```bash
docker compose -f docker-compose.observability.yml up -d
```

두 스택은 독립적으로 중지할 수 있다.

```bash
docker compose down
docker compose -f docker-compose.observability.yml down
```

모니터링 스택은 다음 주소를 사용한다.

- Grafana: <http://127.0.0.1:3000>
- Prometheus: <http://127.0.0.1:9090>
- Loki: <http://127.0.0.1:3100>
- Alloy: <http://127.0.0.1:12345>

Prometheus는 호스트에 공개된 `backend`의 `127.0.0.1:8000` 포트를 통해
`/internal/metrics`를 수집한다. Alloy는 Docker 소켓에서 이름이 `backend`인
컨테이너의 표준 출력을 찾아 Loki로 전송한다. 따라서 애플리케이션 스택을 먼저
실행해야 실제 메트릭과 로그가 표시된다.

## 쿼리 재시도 관측성

SQL과 Graph 모두 기존 단일 재생성 흐름을 사용한다. 생성·실행은 최대 3회,
빈 결과 재시도는 그 예산 안에서 1회다. V2 복구 엔진과
`SQL_REPAIR_ENGINE` / `CYPHER_REPAIR_ENGINE` 선택 설정은 제거되었으므로
기존 배포 환경에서도 해당 변수를 삭제한다. 메트릭의 `engine="v1"`은
대시보드 호환을 위한 고정 라벨이며 엔진 선택 기능이 아니다.

- `itda_query_attempts_total`: 검증·실행을 마친 시도마다 1회 증가.
  빈 결과도 실행 성공으로 집계하며 `issue_code="EMPTY_RESULT"`로 구분.
- `itda_repairs_total`: 재시도 후 비어 있지 않은 계약 충족 결과는 `success`,
  재시도 가능한 오류로 3회 예산을 소진하면 `failure`.
  최종 빈 결과(`NO_DATA`, `INCONCLUSIVE`)는 복구 성공으로 세지 않음.
- `itda_repair_exhausted_total`: 재시도 가능한 오류로 최대 시도 횟수에 도달할 때 증가.
  즉시 종료되는 접속 오류나 재시도 불가 가드 차단은 소진으로 세지 않음.
- `query.generated`, `query.attempt.*`, `repair.*`: 생성, 시도 결과, 재시도 결정,
  복구 성공, 예산 소진 이벤트. `failed_query`와 `generated_query`는 값이 마스킹된
  내부 관측 필드이며 `OBS_LOG_FAILED_QUERY=false`이면 저장하지 않음.

원본 DB 오류는 내부 `retry_feedback`으로 다음 생성에만 전달한다.
외부 응답과 대화 기록의 `attempts[].error`에는 안전한 문구만 남긴다.
`retryDiagnostics`는 오류 타입, SQLSTATE, 실패 단계와 함께 1-based `attempt`,
후속 시도 여부 `retryScheduled`, 최종 복구 여부 `recovered`를 기록한다.
`resultInvariantRetryCount`는 이 중 결과 불변식 위반으로 실제 후속 시도를
선택한 항목만 집계하며 마지막 시도의 위반은 제외한다.
