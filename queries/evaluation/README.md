# RQ/HQ text-to-query 평가

`manifest.json`은 canonical/robustness/holdout 질문, entity·route·subquery 계약, DB
snapshot 검증값과 source별 Gold 쿼리를 연결한다. Gold 쿼리와 expected
`businessRules`·`requiredOutputs`는 채점에만 사용하고 후보 생성에는 넣지 않는다.
후보와 Gold는 동일한 읽기 전용 snapshot에서 실행해 결과 hash로 비교한다. 각
subquery의 `question`, `businessRules`, `requiredOutputs`는 Gold가 검증하는 결과
책임의 기준이다. router 계획에서는 전체 결과 스키마를 반복하게 하지 않고, HYBRID
단계 사이에 전달하거나 결합하는 필드만 필수로 검사한다. 각 Gold 파일의 첫 줄에는
query ID와 담당 subquery 질문을 적고, manifest와 달라지면 테스트가 실패한다.

RQ01~RQ17과 HQ01~HQ08은 `FULLY_EVALUATED`다. RQ18~RQ20과 HQ09~HQ10은
부분 SQL/Cypher까지 채점하는 `QUERY_EVALUATED_FINAL_JOIN_PENDING`이며, 후자의
애플리케이션 계산·최종 결합은 점수에 포함하지 않는다.

## 평가 suite

- `canonical`: 동결된 RQ01~RQ20 질문·계약·Gold 20건
- `robustness`: 각 RQ의 의미·파라미터·Gold는 유지하면서 표현만 바꾼 60건
- `holdout`: 신규 방향 HQ01~HQ10 10건(SQL 6, GRAPH 2, HYBRID 2)
- `all`: 독립 업무 계약 30개(canonical 20 + holdout 10)와 같은 계약의
  표현 변형 60개를 합한 전체 90 case

robustness case ID의 `S`는 짧은 표현·문장 파편, `C`는 일상 구어체, `R`은
동의어·조건 재배치를 뜻한다. 예를 들어 `RB01-S`, `RB01-C`, `RB01-R`은 모두
RQ01 계약과 같은 파라미터·Gold로 채점된다. 단일 턴 평가이므로 RQ17과 RQ19처럼
복수 제품이나 생산 대상이 필요한 질문은 구어체에서도 제품 전체 이름을 유지한다.

`subqueries`는 평가기가 직접 실행하는 offline component 계약이다. production
`/chat`은 현재 `dependsOn`·`inputBindings`를 실행하지 않으며 평가기와 같은 DB 실행
경로를 사용하지 않는다. 따라서 이 점수는 자연어 최종 답변이나 production E2E
정확도가 아니다.

## 기본 실행

저장소 루트에서 다음 스크립트를 실행한다. 기본값은 `canonical`,
`RQ01~RQ20`, 질의당 1회이며 전달한 평가 옵션으로 필요한 기본값을 덮어쓸 수 있다.

```bash
./scripts/run-t2q-evaluation.sh
./scripts/run-t2q-evaluation.sh --runs 3
./scripts/run-t2q-evaluation.sh --routes HYBRID
./scripts/run-t2q-evaluation.sh --ids RQ16,RQ18-RQ20
./scripts/run-t2q-evaluation.sh --validate-gold
./scripts/run-t2q-evaluation.sh --suite robustness --ids RQ01-RQ20
./scripts/run-t2q-evaluation.sh --suite holdout --ids HQ01-HQ10 --validate-gold
```

wrapper가 앞에 넣는 기본 옵션은 뒤에 전달한 같은 옵션으로 덮어쓸 수 있다. 따라서
인자 없이 실행하면 계속 canonical RQ01~RQ20만 1회 실행한다. 전체 90건은 비용과
의도를 분명히 하기 위해 `--suite all --ids all`로 명시해서 실행한다.

Python은 `EVAL_PYTHON`, `backend/venv/bin/python`,
`backend/venv/Scripts/python.exe`, 시스템 `python3` 순서로 선택한다. 결과는
한국 시간과 실행 당시 커밋·작업 상태에 따라 다음 경로에 `report.md`,
`evaluation.json` 두 파일로 저장한다.

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

`--ids`는 쉼표 목록과 같은 prefix 범위를 지원한다. `RQ01-RQ20`,
`HQ01-HQ10`은 유효하지만 `RQ01-HQ10`은 유효하지 않다. robustness에서
`--ids RQ01`을 선택하면 RQ01 계약을 공유하는 S/C/R 세 case가 모두 선택된다.

## 실행 주기와 비용

기본은 항상 `--runs 1`이다. canonical은 평상시 확인에 사용하고 robustness와
holdout은 PR 마감 또는 릴리스 전에 수동 실행한다. 자동 CD gate, 별도 대시보드,
추세 DB에는 연결하지 않는다. 단일 실행은 smoke 기준선이며 성능 개선을 주장할 때는
같은 모델·snapshot에서 canonical과 현재 regression으로 사용하는 holdout을 각각
`--runs 3`으로 실행해 case별 변동을 함께 확인한다. robustness는 같은 계약의 상관된
표현 변형이므로 기본 1회를 유지한다.

현재 단계별 모델 호출 수를 기준으로 canonical 20건은 약 63회, robustness
60건은 약 189회, holdout 10건은 약 32회로 전체 90건에 약 284회가 필요하다.
전체를 `--runs 3`으로 실행하면 약 852회이므로 실행하지 않는다. 독립 계약인
canonical과 holdout만 3회 실행하면 약 285회다. 개별 실패 재현은 canonical에서
`--ids RQ12 --runs 3`, robustness는
`--suite robustness --ids RQ12 --runs 3`(해당 RQ의 S/C/R 세 case), holdout은
`--suite holdout --ids HQ07 --runs 3`처럼 suite와 ID를 함께 지정한다.

Holdout은 코드·프롬프트를 동결한 뒤 최초 capability 결과를 확인한다. HQ 질문과
Gold가 같은 저장소에 공개되어 있으므로 이 suite는 최초 실행 전의 개발용 capability
holdout이지, 접근이 차단된 blind holdout은 아니다. 그 결과를 보거나 성능을
수정했다면 기존 HQ 세트는 regression으로 전환하고, 반복 실행은 모델 변동성 확인에만
사용한다. 최종 일반화 평가는 개발자가 튜닝 중 보지 않은 신규 질의로 별도 수행한다.
RQ/HQ 계약·case·Gold 원문과 승인
snapshot의 모든 canonical/holdout Gold 결과 행 수·hash는 테스트에 고정되어
있으므로 의도적인 기준선 갱신 없이 변경할 수 없다.

## GitHub Actions에서 수동 실행

`Text-to-query Manual Evaluation`에서 `Run workflow`를 누르고 대상 PR 브랜치를
선택하면 기본값으로 canonical 20개를 1회 평가한다. workflow 입력에서 holdout과
case별 1회/3회 실행을 선택할 수 있지만 실행은 계속 수동이다. 모델 호출 전에
canonical/holdout Gold 결과 행 수·hash 통합 테스트를 실행해 승인 snapshot drift를
차단한다. 모델 결과 불일치는
리포트에 남기되 workflow를 차단하지 않고, 환경·API·DB·snapshot·Gold 오류만
실패로 처리한다.

GitHub의 `workflow_dispatch`는 workflow 파일이 기본 브랜치에 존재해야 활성화된다.
기본 브랜치에 workflow가 들어간 뒤부터 평가할 PR 브랜치를 선택해 병합 전에
수동 실행할 수 있다.

Gold와 snapshot만 점검하려면 `--validate-gold`를 사용한다. 비밀번호와 API key는
CLI 인자로 받지 않으며 `POSTGRES_*`, `NEO4J_*`, `OPENAI_API_KEY` 환경변수에서만
읽는다. 현재 승인 snapshot의 `syncRunId`와 모든 핵심 테이블·노드·관계 건수를
실행 전에 검증하며, 계산된 snapshot hash도 artifact에 기록한다. snapshot을
교체할 때는 canonical 23개와 holdout 12개 Gold 결과 행 수·hash, run ID를 함께
리뷰해야 한다.

모델 평가 artifact는 내부에 기록된 정확한 commit과 작업 상태에 대한 증거다.
후속 commit이 문서·테스트만 바꿔 production 평가 동작에 영향을 주지 않았다면 기존
모델 결과를 PR 설명에서 그 근거와 함께 참조할 수 있지만, HEAD에서는 최소한
`--validate-gold`를 다시 실행한다. 프롬프트·모델·라우터·entity 해석·query 생성·
정규화·채점 로직이 바뀌면 기존 결과를 HEAD 결과로 간주하지 않고 해당 suite를 다시
실행한다.

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
- `finalResultAccuracy`: 최종 결과 평가가 가능한 RQ01~RQ17과 HQ01~HQ08의 Gold
  결과 정확도
- `routingAccuracy`: SQL/GRAPH/HYBRID 선택 자체의 정확도
- `sqlPartialCoverage`/`sqlPartialAccuracy`,
  `graphPartialCoverage`/`graphPartialAccuracy`, `hybridSplitAccuracy`: source별
  비교 가능 범위와 그 안의 정확도, HYBRID 분할 진단 점수

반복 실행에서는 case별 `CONSISTENT_PASS`, `VARIABLE`, `CONSISTENT_FAIL`,
`INCOMPLETE`를 별도로 기록한다. `caseOutcomeConsistency`에는 전회 FAIL도 포함되므로
정답률이 아니라 모델 변동성 지표다. 기존 `caseStability`는 JSON 호환성을 위해
유지하지만 실제 의미는 전회 PASS case 비율이며, 신규 분석에는
`consistentPassCaseRate`와 `caseTrialSummary`를 사용한다.

entity가 일치하지 않아도 후속 쿼리와 Gold 비교는 계속해 복구 여부를 남긴다.
다만 `stageAccuracy.entity`와 `ENTITY_MISMATCH` 실패 사유를 기록하고,
`queryPipelinePass`는 실패로 판정한다. 비교할 때는 필수 identity와 복수 entity의
질문 등장 순서를 확인하되, 이름의 대소문자·공백과 추가 메타데이터는 허용한다.
숫자 `Id`는 정수 값으로 비교하며 문자열 변환·불리언·소수·NaN·무한대는 거부한다.

인프라 `ERROR`는 정확도 분모에서 제외하고 `evaluationCoverage`에 반영하며,
`BLOCKED_BY_DEPENDENCY`는 후속 단계 오답으로 중복 집계하지 않는다. 필수 출력
alias를 매핑하지 못한 경우도 의미 오답으로 단정하지 않고
`RESULT_CONTRACT_MISMATCH`와 의미 결과 미평가로 기록한다.

alias는 대소문자·snake/camel 표기 차이와 `totalSalesQuantity`처럼 의미가
동일한 이름만 허용한다. `startProductId`같이 문맥에 따라 의미가 달라지는
이름은 해당 subquery에서만 매핑하고, 한 subquery 안에서 두 계약 필드가
같은 alias를 공유하면 manifest 테스트가 실패한다. 필드 순서로 추측하거나
생성 SQL/Cypher 문자열을 정규식으로 채점하지 않는다.
