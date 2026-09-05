# 로컬 모니터링 스택

두 Compose 프로젝트가 백엔드 메트릭을 내부 통신으로 수집할 수 있도록 공통
네트워크를 최초 한 번 생성한다.

```bash
docker network create itda-observability
```

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

- Web: <http://127.0.0.1:8080>
- Grafana: <http://127.0.0.1:3000>
- Prometheus: <http://127.0.0.1:9090>
- Loki: <http://127.0.0.1:3100>
- Alloy: <http://127.0.0.1:12345>

Prometheus는 공통 `itda-observability` 네트워크에서 `backend:8000`의
`/internal/metrics`를 수집한다. Alloy는 Docker 소켓에서 Compose 서비스 라벨이
`backend`인 컨테이너의 표준 출력을 찾아 Loki로 전송한다. 따라서 백엔드는 Docker
Compose로 실행해야 하며, 애플리케이션 스택을 먼저 실행해야 실제 메트릭과 로그가
표시된다.
