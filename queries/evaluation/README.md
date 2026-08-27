# RQ01~RQ20 자동 평가

`manifest.json`은 canonical/robustness 질문, entity·route·subquery 계약, DB
snapshot 검증값과 source별 Gold 쿼리를 연결한다. Gold는 후보 쿼리 생성에
사용하지 않고 동일한 읽기 전용 snapshot에서 결과 hash를 만들 때만 사용한다.
각 subquery의 `question`, `businessRules`, `requiredOutputs`가 Gold가 검증하는
결과 책임의 기준이다. router 계획에서는 전체 결과 스키마를 반복하게 하지 않고,
HYBRID 단계 사이에 전달하거나 결합하는 필드만 필수로 검사한다. 각 Gold 파일의
첫 줄에는 RQ ID와 담당 subquery 질문을 적고, manifest와 달라지면 테스트가
실패하도록 해 사람이 파일만 열어도 검증 목적을 알 수 있게 한다.

RQ01~RQ17은 `FULLY_EVALUATED`, RQ18~RQ20은 부분 SQL/Cypher까지 채점하는
`QUERY_EVALUATED_FINAL_JOIN_PENDING`이다. 후자의 애플리케이션 계산·최종 결합은
점수에 포함하지 않는다.

`subqueries`는 평가기가 검증·실행하는 계약이다. production `/chat`은
`dependsOn`·`inputBindings`를 실행하지 않으며 `subqueries`를 응답에 노출하지
않는다.

## 기본 실행

저장소 루트에서 다음 스크립트를 실행한다. 기본값은 `canonical`,
`RQ01~RQ20`, 질의당 1회이며 전달한 평가 옵션으로 필요한 기본값을 덮어쓸 수 있다.

```bash
./scripts/run-t2q-evaluation.sh
./scripts/run-t2q-evaluation.sh --runs 3
./scripts/run-t2q-evaluation.sh --routes HYBRID
./scripts/run-t2q-evaluation.sh --ids RQ16,RQ18-RQ20
./scripts/run-t2q-evaluation.sh --validate-gold
```

Python은 `EVAL_PYTHON`, `backend/venv/bin/python`, 시스템 `python3` 순서로
선택한다. 결과는 한국 시간과 실행 당시 커밋·작업 상태에 따라 다음 경로에
`report.md`, `evaluation.json` 두 파일로 저장한다.

```text
artifacts/t2c-eval/YYYY-MM-DD/HHMMSS-커밋-clean|dirty/
```

`report.md`에는 실행 정보, 핵심 점수, Route별 점수, 질의별 판정을 표시한다.
`evaluation.json`은 기존 summary와 case 상세 정보를 함께 보관하며 생성 쿼리와
결과 샘플 등 정밀 진단이 필요할 때 사용한다.

## 고급 실행

출력 경로를 직접 지정하거나 manifest·모델 URL 등 전체 CLI 옵션을 제어하려면
기존 Python CLI를 사용한다.

```bash
PYTHONPATH=backend python -m evaluation \
  --suite canonical \
  --ids RQ01-RQ20 \
  --runs 1 \
  --model "$OPENAI_MODEL" \
  --output-dir artifacts/t2c-eval-custom
```

## GitHub Actions에서 수동 실행

`Text-to-query Manual Evaluation`에서 `Run workflow`를 누르고 대상 PR 브랜치를
선택하면 기본값으로 canonical 20개를 1회 평가한다. 결과 불일치는 리포트에 남기되
workflow를 차단하지 않고, 환경·API·DB·snapshot 오류만 실패로 처리한다.

GitHub의 `workflow_dispatch`는 workflow 파일이 기본 브랜치에 존재해야 활성화된다.
기본 브랜치에 workflow가 들어간 뒤부터 평가할 PR 브랜치를 선택해 병합 전에
수동 실행할 수 있다.

Gold와 snapshot만 점검하려면 `--validate-gold`를 사용한다. 비밀번호와 API key는
CLI 인자로 받지 않으며 `POSTGRES_*`, `NEO4J_*`, `OPENAI_API_KEY` 환경변수에서만
읽는다. 현재 승인 snapshot의 `syncRunId`와 모든 핵심 테이블·노드·관계 건수를
실행 전에 검증하며, 계산된 snapshot hash도 artifact에 기록한다. snapshot을
교체할 때는 Gold 결과와 run ID를 함께 리뷰해야 한다.

종료 코드는 리포트 완료 `0`, 환경·API·DB·snapshot·Gold 오류 `2`다. report
모드에서 모델의 쿼리 오답은 결과표에 기록하되 실행 자체를 실패시키지 않는다. 결과는
`report.md`, `evaluation.json`으로 남는다.

주요 점수는 서로 대체하지 않는다.

- `queryPipelineAccuracy`: entity, route, HYBRID 전달·결합 계약, 안전한 실행과
  Gold 결과를 모두 통과한 비율. 자연어 최종 답변 E2E 점수는 아니다.
- `semanticResultCoverage`: 출력 필드를 계약 필드로 정규화해 Gold와 실제 비교할
  수 있었던 비율
- `semanticResultAccuracy`: 비교 가능한 결과 중 source별 실행 결과가 Gold와
  같은 비율
- `verifiedSemanticPassRate`: 전체 평가 중 실제 Gold 일치가 확인된 비율
- `finalResultAccuracy`: 최종 결과 평가가 가능한 RQ01~RQ17의 Gold 결과 정확도
- `routingAccuracy`: SQL/GRAPH/HYBRID 선택 자체의 정확도
- `sqlPartialCoverage`/`sqlPartialAccuracy`,
  `graphPartialCoverage`/`graphPartialAccuracy`, `hybridSplitAccuracy`: source별
  비교 가능 범위와 그 안의 정확도, HYBRID 분할 진단 점수

entity가 일치하지 않아도 후속 쿼리와 Gold 비교는 계속해 복구 여부를 남긴다.
다만 `stageAccuracy.entity`와 `ENTITY_MISMATCH` 실패 사유를 기록하고,
`queryPipelinePass`는 실패로 판정한다. 비교할 때는 필수 identity와 복수 entity의
질문 등장 순서를 확인하되, 이름의 대소문자·공백과 추가 메타데이터는 허용한다.

인프라 `ERROR`는 정확도 분모에서 제외하고 `evaluationCoverage`에 반영하며,
`BLOCKED_BY_DEPENDENCY`는 후속 단계 오답으로 중복 집계하지 않는다. 필수 출력
alias를 매핑하지 못한 경우도 의미 오답으로 단정하지 않고
`RESULT_CONTRACT_MISMATCH`와 의미 결과 미평가로 기록한다.

alias는 대소문자·snake/camel 표기 차이와 `totalSalesQuantity`처럼 의미가
동일한 이름만 허용한다. `startProductId`같이 문맥에 따라 의미가 달라지는
이름은 해당 subquery에서만 매핑하고, 한 subquery 안에서 두 계약 필드가
같은 alias를 공유하면 manifest 테스트가 실패한다. 필드 순서로 추측하거나
생성 SQL/Cypher 문자열을 정규식으로 채점하지 않는다.
