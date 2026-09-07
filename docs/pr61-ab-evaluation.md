# PR #61 원본과 엄격한 질의 처리 결합 A/B 평가

- A: PR #61 원본 `3e24d123ab4c66e7de7b37c18ee570de8031dbe3`.
- B: `feat/pr61-strict-query-fallback`, `18f9d9a2888f2f8c7a998b96dc559640227015c5`.
- B는 공통 요청 가드 이후 엄격한 질의 처리를 먼저 실행하고, 실패·빈 결과·잘린 결과·60초 시간 초과 시 #61 질의 처리로 전환한다. 자연어 답변 생성은 #61 구현을 사용한다.
- 기존 90개와 신규 90개를 각 버전에서 3회씩 실행했다. 네 묶음 각 270건, 총 1,080건. 각 묶음은 6개 작업으로 분할했으며 A/B를 동시에 실행했다.
- 모델 `gpt-5.6-luna`, reasoning `medium`. 동일 DB 스냅샷과 동일 질문·Gold, 실행 전후 생산 소스/manifest 해시 일치를 확인했다. 실패를 성공할 때까지 다시 돌리거나 결과를 교체하지 않았다.

## 결과

| 평가셋 | 버전 | 결과 검사 통과 | 전체 파이프라인 통과 | 답변 반환 | 실행 오류 |
|---|---|---:|---:|---:|---:|
| 기존 90개 | A | 229/270 (84.81%) | 229/270 (84.81%) | 265/270 (98.15%) | 0 |
| 기존 90개 | B | 270/270 (100.00%) | 269/270 (99.63%) | 270/270 (100.00%) | 0 |
| 신규 90개 | A | 118/270 (43.70%) | 109/270 (40.37%) | 217/270 (80.37%) | 0 |
| 신규 90개 | B | 121/270 (44.81%) | 118/270 (43.70%) | 247/270 (91.48%) | 0 |

결과 검사 통과는 평가기의 `finalResultPass`다. 기존셋의 최종 계약 없는 질문은 원천 조회 결과 일치와 합성 성공으로 판정하며, 최종 계약 있는 질문 및 신규 90개는 최종 Gold 계약으로 판정한다. 전체 파이프라인은 엔티티·계획·필수 출력 등 추가 조건도 검사한다. 답변 반환에는 차단·실패 안내가 포함되므로 정답률이 아니다. 자연어 설명 전체를 별도 채점한 결과도 아니다.

## 유형별 결과 검사 통과

| 평가셋 | 유형 | A | B |
|---|---|---:|---:|
| existing | SQL | 138/150 (92.00%) | 150/150 (100.00%) |
| existing | GRAPH | 68/78 (87.18%) | 78/78 (100.00%) |
| existing | HYBRID | 23/42 (54.76%) | 42/42 (100.00%) |
| generalization | SQL | 83/90 (92.22%) | 76/90 (84.44%) |
| generalization | GRAPH | 35/90 (38.89%) | 45/90 (50.00%) |
| generalization | HYBRID | 0/90 (0.00%) | 0/90 (0.00%) |

## B의 실제 경로

| 평가셋 | 엄격한 경로 수 / 통과 | #61 fallback 수 / 통과 | 공통 단계 등 |
|---|---:|---:|---:|
| existing | 269 / 269 | 1 / 1 | 0 |
| generalization | 110 / 77 | 70 / 44 | 90 |

- existing fallback 사유: `{"composition_failure": 1}`
- generalization fallback 사유: `{"EntityNotFoundError": 34, "empty_result": 28, "EntityAmbiguousError": 6, "composition_failure": 2}`

엄격한 경로의 내부 계약을 통과했어도 Gold와 다르면 fallback하지 않는다. 따라서 엄격한 경로의 오답 수와 fallback 이후 통과 수를 함께 보아야 한다.

## 3회 반복 안정성

| 평가셋 | 버전 | 3회 모두 결과 통과 질문 | 회차별 통과 변동 질문 |
|---|---|---:|---:|
| existing | baseline | 68/90 | 15/90 |
| existing | candidate | 90/90 | 0/90 |
| generalization | baseline | 34/90 | 9/90 |
| generalization | candidate | 29/90 | 22/90 |

## 관찰한 실패와 복구

- existing: fallback 후 결과 통과 1회, 엄격한 경로가 채택한 결과의 검사 실패 0회.
- generalization: fallback 후 결과 통과 44회, 엄격한 경로가 채택한 결과의 검사 실패 33회.

신규 GQ31의 A 실행은 `matched result shape is incomplete: displayEntities=component` 계획 오류로 3회 실패했다. 신규 GQ07에서는 B의 엄격한 경로가 출력 계약과 다른 결과를 채택한 사례가 있고, GQ13·GQ16에서는 조회 값이 Gold와 달라도 엄격한 경로에서 결과를 채택한 사례가 있다. 이처럼 실행 성공이나 내부 계약 통과만으로 질문의 모든 조건을 만족했는지 보장할 수 없다.

신규 혼합 질의 30개 × 3회는 A/B 모두 공통 요청 가드에서 데이터 변경 요청으로 오인해 차단 안내를 반환했다. 결과에 지표를 추가해 달라는 표현이 포함된 질문들로, 질의 처리 경로에 들어가지 않아 fallback으로 복구되지 않는다.

## 판단

기존 평가셋에서는 B가 41회 더 통과하고 90개 질문 모두 3회 통과해 가설의 기존셋 정확도 부분을 뒷받침했다. 신규셋은 A 118회에서 B 121회로 3회(1.11%p) 증가에 그쳤다. SQL은 83→76회로 감소했고 그래프는 35→45회로 증가했다. 신규셋의 3회 모두 통과 질문 수도 34→29개로 줄어, 범용성과 반복 안정성을 모두 확보했다고 판단하기는 어렵다.

B의 신규셋 fallback 70회 중 44회는 통과했지만, 엄격한 경로가 채택한 110회 중 33회는 정답 검사를 실패했다. 다음 개선은 질문의 조건·필수 출력 누락을 감지해 이런 오답 채택을 줄이는 것과, 공통 가드의 조회 요청 오차단을 해결하는 것이다. 이번 평가 중에는 코드를 수정하지 않았다.

## 해석상 제한

새 평가셋도 동일 AdventureWorks 도메인에서 작성됐다. 그래프·혼합 질문은 짝을 이루며 일부 표현을 공유하므로 범용성 전체나 통계적 유의성을 단정할 수 없다. 각 묶음의 병렬 실행과 경로 차이가 있어 소요 시간을 단일 사용자 응답속도로 해석하지 않는다. 이 평가는 production orchestrator 직접 호출이며 HTTP 502 발생률을 측정한 API 부하 테스트는 아니다.

## 질문별 결과 통과 횟수

| 평가셋 | ID | A / 3 | B / 3 |
|---|---|---:|---:|
| existing | HQ01 | 3 | 3 |
| existing | HQ02 | 3 | 3 |
| existing | HQ03 | 3 | 3 |
| existing | HQ04 | 3 | 3 |
| existing | HQ05 | 3 | 3 |
| existing | HQ06 | 3 | 3 |
| existing | HQ07 | 0 | 3 |
| existing | HQ08 | 3 | 3 |
| existing | HQ09 | 2 | 3 |
| existing | HQ10 | 0 | 3 |
| existing | RB01-C | 3 | 3 |
| existing | RB01-R | 3 | 3 |
| existing | RB01-S | 3 | 3 |
| existing | RB02-C | 3 | 3 |
| existing | RB02-R | 3 | 3 |
| existing | RB02-S | 3 | 3 |
| existing | RB03-C | 3 | 3 |
| existing | RB03-R | 3 | 3 |
| existing | RB03-S | 3 | 3 |
| existing | RB04-C | 3 | 3 |
| existing | RB04-R | 1 | 3 |
| existing | RB04-S | 2 | 3 |
| existing | RB05-C | 3 | 3 |
| existing | RB05-R | 3 | 3 |
| existing | RB05-S | 3 | 3 |
| existing | RB06-C | 3 | 3 |
| existing | RB06-R | 3 | 3 |
| existing | RB06-S | 2 | 3 |
| existing | RB07-C | 2 | 3 |
| existing | RB07-R | 3 | 3 |
| existing | RB07-S | 3 | 3 |
| existing | RB08-C | 3 | 3 |
| existing | RB08-R | 3 | 3 |
| existing | RB08-S | 2 | 3 |
| existing | RB09-C | 3 | 3 |
| existing | RB09-R | 3 | 3 |
| existing | RB09-S | 3 | 3 |
| existing | RB10-C | 3 | 3 |
| existing | RB10-R | 3 | 3 |
| existing | RB10-S | 3 | 3 |
| existing | RB11-C | 3 | 3 |
| existing | RB11-R | 0 | 3 |
| existing | RB11-S | 3 | 3 |
| existing | RB12-C | 3 | 3 |
| existing | RB12-R | 3 | 3 |
| existing | RB12-S | 3 | 3 |
| existing | RB13-C | 3 | 3 |
| existing | RB13-R | 1 | 3 |
| existing | RB13-S | 3 | 3 |
| existing | RB14-C | 3 | 3 |
| existing | RB14-R | 3 | 3 |
| existing | RB14-S | 3 | 3 |
| existing | RB15-C | 3 | 3 |
| existing | RB15-R | 1 | 3 |
| existing | RB15-S | 2 | 3 |
| existing | RB16-C | 3 | 3 |
| existing | RB16-R | 3 | 3 |
| existing | RB16-S | 3 | 3 |
| existing | RB17-C | 3 | 3 |
| existing | RB17-R | 3 | 3 |
| existing | RB17-S | 3 | 3 |
| existing | RB18-C | 0 | 3 |
| existing | RB18-R | 3 | 3 |
| existing | RB18-S | 1 | 3 |
| existing | RB19-C | 3 | 3 |
| existing | RB19-R | 3 | 3 |
| existing | RB19-S | 3 | 3 |
| existing | RB20-C | 0 | 3 |
| existing | RB20-R | 0 | 3 |
| existing | RB20-S | 2 | 3 |
| existing | RQ01 | 3 | 3 |
| existing | RQ02 | 3 | 3 |
| existing | RQ03 | 3 | 3 |
| existing | RQ04 | 2 | 3 |
| existing | RQ05 | 3 | 3 |
| existing | RQ06 | 3 | 3 |
| existing | RQ07 | 3 | 3 |
| existing | RQ08 | 3 | 3 |
| existing | RQ09 | 3 | 3 |
| existing | RQ10 | 3 | 3 |
| existing | RQ11 | 1 | 3 |
| existing | RQ12 | 2 | 3 |
| existing | RQ13 | 3 | 3 |
| existing | RQ14 | 3 | 3 |
| existing | RQ15 | 3 | 3 |
| existing | RQ16 | 2 | 3 |
| existing | RQ17 | 3 | 3 |
| existing | RQ18 | 3 | 3 |
| existing | RQ19 | 3 | 3 |
| existing | RQ20 | 0 | 3 |
| generalization | GQ01 | 3 | 3 |
| generalization | GQ02 | 0 | 0 |
| generalization | GQ03 | 3 | 3 |
| generalization | GQ04 | 3 | 3 |
| generalization | GQ05 | 3 | 3 |
| generalization | GQ06 | 2 | 3 |
| generalization | GQ07 | 3 | 1 |
| generalization | GQ08 | 3 | 3 |
| generalization | GQ09 | 3 | 3 |
| generalization | GQ10 | 3 | 3 |
| generalization | GQ11 | 2 | 3 |
| generalization | GQ12 | 3 | 3 |
| generalization | GQ13 | 2 | 1 |
| generalization | GQ14 | 3 | 3 |
| generalization | GQ15 | 3 | 3 |
| generalization | GQ16 | 3 | 1 |
| generalization | GQ17 | 3 | 2 |
| generalization | GQ18 | 3 | 3 |
| generalization | GQ19 | 3 | 3 |
| generalization | GQ20 | 3 | 0 |
| generalization | GQ21 | 3 | 3 |
| generalization | GQ22 | 3 | 3 |
| generalization | GQ23 | 3 | 3 |
| generalization | GQ24 | 3 | 3 |
| generalization | GQ25 | 3 | 3 |
| generalization | GQ26 | 3 | 3 |
| generalization | GQ27 | 2 | 3 |
| generalization | GQ28 | 3 | 2 |
| generalization | GQ29 | 3 | 3 |
| generalization | GQ30 | 3 | 3 |
| generalization | GQ31 | 0 | 1 |
| generalization | GQ32 | 0 | 2 |
| generalization | GQ33 | 0 | 2 |
| generalization | GQ34 | 0 | 1 |
| generalization | GQ35 | 0 | 1 |
| generalization | GQ36 | 0 | 2 |
| generalization | GQ37 | 3 | 3 |
| generalization | GQ38 | 0 | 2 |
| generalization | GQ39 | 1 | 3 |
| generalization | GQ40 | 0 | 1 |
| generalization | GQ41 | 0 | 3 |
| generalization | GQ42 | 0 | 0 |
| generalization | GQ43 | 0 | 0 |
| generalization | GQ44 | 0 | 1 |
| generalization | GQ45 | 0 | 0 |
| generalization | GQ46 | 3 | 2 |
| generalization | GQ47 | 0 | 0 |
| generalization | GQ48 | 2 | 1 |
| generalization | GQ49 | 2 | 2 |
| generalization | GQ50 | 0 | 0 |
| generalization | GQ51 | 3 | 2 |
| generalization | GQ52 | 3 | 0 |
| generalization | GQ53 | 1 | 1 |
| generalization | GQ54 | 3 | 3 |
| generalization | GQ55 | 3 | 2 |
| generalization | GQ56 | 3 | 3 |
| generalization | GQ57 | 2 | 2 |
| generalization | GQ58 | 0 | 0 |
| generalization | GQ59 | 3 | 2 |
| generalization | GQ60 | 3 | 3 |
| generalization | GQ61 | 0 | 0 |
| generalization | GQ62 | 0 | 0 |
| generalization | GQ63 | 0 | 0 |
| generalization | GQ64 | 0 | 0 |
| generalization | GQ65 | 0 | 0 |
| generalization | GQ66 | 0 | 0 |
| generalization | GQ67 | 0 | 0 |
| generalization | GQ68 | 0 | 0 |
| generalization | GQ69 | 0 | 0 |
| generalization | GQ70 | 0 | 0 |
| generalization | GQ71 | 0 | 0 |
| generalization | GQ72 | 0 | 0 |
| generalization | GQ73 | 0 | 0 |
| generalization | GQ74 | 0 | 0 |
| generalization | GQ75 | 0 | 0 |
| generalization | GQ76 | 0 | 0 |
| generalization | GQ77 | 0 | 0 |
| generalization | GQ78 | 0 | 0 |
| generalization | GQ79 | 0 | 0 |
| generalization | GQ80 | 0 | 0 |
| generalization | GQ81 | 0 | 0 |
| generalization | GQ82 | 0 | 0 |
| generalization | GQ83 | 0 | 0 |
| generalization | GQ84 | 0 | 0 |
| generalization | GQ85 | 0 | 0 |
| generalization | GQ86 | 0 | 0 |
| generalization | GQ87 | 0 | 0 |
| generalization | GQ88 | 0 | 0 |
| generalization | GQ89 | 0 | 0 |
| generalization | GQ90 | 0 | 0 |

이 PR에는 [집계 지표와 질문별 통과 횟수](evaluations/pr61-ab/scores.json)를 포함한다. 각 묶음 원본·실패 상세와 소스 고정 기록은 평가를 실행한 로컬 작업 폴더의 `artifacts/pr61-comparison/`에 보관했으며 이 PR에는 포함하지 않는다. 신규 평가셋 및 실행 어댑터도 별도 로컬 작업 폴더에 있으므로 이 PR만으로 신규셋 평가를 재실행할 수는 없다.
