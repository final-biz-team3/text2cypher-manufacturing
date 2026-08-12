"""
경로 A(`load.cypher` + cypher-shell) / 경로 B(`load_to_neo4j.py`, Bolt 드라이버) 중
지금 상황에 맞는 쪽을 자동으로 골라서 실행하는 디스패처.

판단 기준(docs/adr/0005-etl-batch-loading-pipeline.md 결정 7): 컴퓨터 대수가 아니라
"적재 명령이 실행되는 위치가 Neo4j 서버와 파일시스템을 공유하는가"이다. 이 스크립트는
그 판단을 다음과 같이 근사한다:
  - NEO4J_URI의 호스트가 localhost/127.0.0.1/neo4j(docker-compose 서비스명)면 로컬
    컨테이너를 가리키는 것으로 보고, `docker compose exec neo4j cypher-shell`이 실제로
    되는지 probe해서 되면 경로 A를 쓴다.
  - 그 외(원격 호스트)거나 probe에 실패하면 경로 B(`load_to_neo4j.py`)로 넘어간다.

이 근사는 완벽하지 않다 — SSH로 원격 서버에 들어가 그 안에서 이 스크립트를 실행하는
경우도 경로 A가 되어야 하는데, 그건 NEO4J_URI만 봐서는 구분이 안 된다. 그런 상황이라면
이 스크립트 대신 `load.cypher`를 직접 실행하면 된다.

사용법 (리포 루트 기준으로 실행):
    python etl/run_load.py --month 2014-05
"""

import argparse
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ETL_DIR = Path(__file__).resolve().parent
ROOT_DIR = ETL_DIR.parent
LOCAL_HOSTS = {"localhost", "127.0.0.1", "neo4j"}


def load_env() -> dict:
    env_path = ROOT_DIR / ".env"
    env = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def docker_cypher_shell_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "neo4j", "cypher-shell", "--version"],
            cwd=ROOT_DIR,
            capture_output=True,
            timeout=15,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_path_a(env: dict, month: str) -> int:
    print("경로 A 선택: docker compose exec cypher-shell -f /etl/load.cypher")
    cmd = [
        "docker", "compose", "exec", "-T", "neo4j", "cypher-shell",
        "-u", env["NEO4J_USER"], "-p", env["NEO4J_PASSWORD"],
        "-P", f'{{month: "{month}"}}',
        "-f", "/etl/load.cypher",
    ]
    sys.stdout.flush()
    return subprocess.run(cmd, cwd=ROOT_DIR).returncode


def run_path_b(month: str) -> int:
    print("경로 B 선택: python etl/load_to_neo4j.py")
    cmd = [sys.executable, str(ETL_DIR / "load_to_neo4j.py"), "--month", month]
    sys.stdout.flush()
    return subprocess.run(cmd, cwd=ROOT_DIR).returncode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", required=True, help="YYYY-MM")
    args = parser.parse_args()

    env = load_env()
    host = urlparse(env["NEO4J_URI"]).hostname or ""
    print(f"NEO4J_URI 호스트: {host}")

    if host in LOCAL_HOSTS and docker_cypher_shell_available():
        code = run_path_a(env, args.month)
    else:
        if host in LOCAL_HOSTS:
            print("로컬 호스트로 보이지만 docker compose exec cypher-shell probe 실패 -> 경로 B로 전환")
        code = run_path_b(args.month)

    sys.exit(code)


if __name__ == "__main__":
    main()
