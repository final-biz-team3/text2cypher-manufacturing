param(
    [string]$LokiUrl = "http://127.0.0.1:3100",
    [DateTimeOffset]$BaseTime = [DateTimeOffset]::UtcNow,
    [string]$RequestPrefix = "code-demo",
    [switch]$PassThruCount
)

$ErrorActionPreference = "Stop"
$now = $BaseTime
$events = [System.Collections.Generic.List[object]]::new()

function Add-DemoEvent {
    param(
        [int]$SecondsAgo,
        [string]$RequestId,
        [string]$EventName,
        [string]$Summary,
        [string]$Category,
        [string]$Level = "INFO",
        [string]$Route = "UNKNOWN",
        [string]$Tool = "none",
        [string]$Outcome = "success",
        [hashtable]$Details = @{}
    )

    $timestamp = $now.AddSeconds(-$SecondsAgo)
    $nanoseconds = ([System.Numerics.BigInteger]$timestamp.ToUnixTimeMilliseconds() * 1000000).ToString()
    $record = [ordered]@{
        "@timestamp" = $timestamp.ToString("o")
        event_id = "demo-$([guid]::NewGuid().ToString('N'))"
        event_version = 1
        event_name = $EventName
        summary = $Summary
        event_category = $Category
        level = $Level
        service_name = "itda-backend"
        deployment_environment = "presentation-demo"
        service_version = "demo"
        request_id = $RequestId
        route = $Route
        tool = $Tool
        outcome = $Outcome
        demo_data = $true
    }
    $record["question_redacted"] = if ($RequestId -like "*-sql-repaired") {
        "제품별 재고 수량과 표준 원가를 조회해 주세요"
    } elseif ($RequestId -like "*-graph-exhausted") {
        "선택한 제품의 구성 부품과 카테고리를 조회해 주세요"
    } elseif ($RequestId -like "*-policy-blocked") {
        "제품 정보를 변경해 주세요"
    } elseif ($RequestId -like "*-graph-success") {
        "제품의 구성 부품 관계를 조회해 주세요"
    } else {
        "조회 결과를 바탕으로 답변을 만들어 주세요"
    }
    foreach ($key in $Details.Keys) {
        $record[$key] = $Details[$key]
    }
    $events.Add([pscustomobject]@{
        Timestamp = $nanoseconds
        Record = $record
    })
}

# 요청 1: GRAPH 첫 시도 성공
$requestGraph = "$RequestPrefix-graph-success"
Add-DemoEvent 840 $requestGraph "http.request.started" "POST /api/chat 요청을 시작했습니다" "http" -Details @{ method = "POST"; path = "/api/chat" }
Add-DemoEvent 838 $requestGraph "routing.completed" "질문을 GRAPH 경로로 분류했습니다" "pipeline" -Route "GRAPH" -Details @{ planned_tools = @("graph") }
Add-DemoEvent 836 $requestGraph "planning.completed" "GRAPH 실행 계획을 만들었습니다 (하위 질의 1개)" "pipeline" -Route "GRAPH" -Tool "graph" -Details @{ subquery_count = 1 }
Add-DemoEvent 832 $requestGraph "query.generated" "GRAPH 쿼리를 생성했습니다 (1/3회)" "query" -Route "GRAPH" -Tool "graph" -Details @{ attempt = 1; max_attempts = 3; generated_query = "MATCH (p:Product)-[:REQUIRES_COMPONENT]->(c:Product) WHERE p.ProductID = [VALUE] RETURN p.Name, c.Name" }
Add-DemoEvent 830 $requestGraph "tool.execution.started" "GRAPH 조회를 시작했습니다" "pipeline" -Route "GRAPH" -Tool "graph"
Add-DemoEvent 827 $requestGraph "tool.execution.completed" "GRAPH 조회가 완료됐습니다 (184ms)" "pipeline" -Route "GRAPH" -Tool "graph" -Details @{ duration_ms = 184.3; row_count = 12; generated_query = "MATCH (p:Product)-[:REQUIRES_COMPONENT]->(c:Product) WHERE p.ProductID = [VALUE] RETURN p.Name, c.Name" }
Add-DemoEvent 824 $requestGraph "query.pipeline.completed" "GRAPH 질문 처리를 첫 시도에 성공했습니다 (1.24초)" "pipeline" -Route "GRAPH" -Details @{ final_status = "first_attempt_success"; duration_ms = 1240.8; generated_query = "MATCH (p:Product)-[:REQUIRES_COMPONENT]->(c:Product) WHERE p.ProductID = [VALUE] RETURN p.Name, c.Name" }
Add-DemoEvent 823 $requestGraph "http.request.completed" "POST /api/chat 요청이 HTTP 200 상태로 완료됐습니다 (1.31초)" "http" -Route "GRAPH" -Details @{ method = "POST"; path = "/api/chat"; status_code = 200; duration_ms = 1311.4 }

# 요청 2: SQL 오류를 자기수정으로 복구
$requestRepair = "$RequestPrefix-sql-repaired"
Add-DemoEvent 540 $requestRepair "http.request.started" "POST /api/chat 요청을 시작했습니다" "http" -Details @{ method = "POST"; path = "/api/chat" }
Add-DemoEvent 538 $requestRepair "routing.completed" "질문을 SQL 경로로 분류했습니다" "pipeline" -Route "SQL" -Details @{ planned_tools = @("sql") }
Add-DemoEvent 535 $requestRepair "query.generated" "SQL 쿼리를 생성했습니다 (1/3회)" "query" -Route "SQL" -Tool "sql" -Details @{ attempt = 1; max_attempts = 3; generated_query = "SELECT p.ProductID, p.UnknownColumn FROM Production.Product AS p WHERE p.ProductID = [VALUE]" }
Add-DemoEvent 532 $requestRepair "query.attempt.completed" "SQL 쿼리 실행에 실패했습니다 (1/3회) — 현재 SQL 스키마에 존재하지 않는 컬럼을 참조했습니다." "query" -Level "WARNING" -Route "SQL" -Tool "sql" -Outcome "failure" -Details @{ attempt = 1; max_attempts = 3; issue_code = "SQL_UNDEFINED_COLUMN"; failure_reason = "현재 SQL 스키마에 존재하지 않는 컬럼을 참조했습니다."; failure_stage = "execution"; failure_category = "QUERY_INVALID"; failed_query = "SELECT p.ProductID, p.UnknownColumn FROM Production.Product AS p WHERE p.ProductID = [VALUE]"; retryable = $true }
Add-DemoEvent 530 $requestRepair "repair.decision.made" "SQL 쿼리를 수정해 다시 시도합니다 (1/3회) — 현재 SQL 스키마에 존재하지 않는 컬럼을 참조했습니다." "repair" -Route "SQL" -Tool "sql" -Outcome "failure" -Details @{ decision = "retry"; attempt = 1; max_attempts = 3; issue_code = "SQL_UNDEFINED_COLUMN"; failure_reason = "현재 SQL 스키마에 존재하지 않는 컬럼을 참조했습니다."; failed_query = "SELECT p.ProductID, p.UnknownColumn FROM Production.Product AS p WHERE p.ProductID = [VALUE]"; repair_engine = "deterministic" }
Add-DemoEvent 526 $requestRepair "query.generated" "SQL 쿼리를 생성했습니다 (2/3회)" "query" -Route "SQL" -Tool "sql" -Details @{ attempt = 2; max_attempts = 3; generated_query = "SELECT p.ProductID, p.Name, p.StandardCost FROM Production.Product AS p WHERE p.ProductID = [VALUE]" }
Add-DemoEvent 522 $requestRepair "query.attempt.completed" "SQL 쿼리 실행이 완료됐습니다 (2/3회) · 97ms" "query" -Route "SQL" -Tool "sql" -Details @{ attempt = 2; max_attempts = 3; row_count = 8; duration_ms = 96.7; generated_query = "SELECT p.ProductID, p.Name, p.StandardCost FROM Production.Product AS p WHERE p.ProductID = [VALUE]" }
Add-DemoEvent 520 $requestRepair "repair.completed" "SQL 쿼리가 자기수정으로 복구됐습니다 (2회)" "repair" -Route "SQL" -Tool "sql" -Details @{ attempt = 2; repair_engine = "rule-guided" }
Add-DemoEvent 516 $requestRepair "query.pipeline.completed" "SQL 질문 처리를 자기수정 후 성공했습니다 (2.16초)" "pipeline" -Route "SQL" -Details @{ final_status = "recovered"; duration_ms = 2158.2; generated_query = "SELECT p.ProductID, p.Name, p.StandardCost FROM Production.Product AS p WHERE p.ProductID = [VALUE]" }
Add-DemoEvent 515 $requestRepair "http.request.completed" "POST /api/chat 요청이 HTTP 200 상태로 완료됐습니다 (2.22초)" "http" -Route "SQL" -Details @{ status_code = 200; duration_ms = 2221.6 }

# 요청 3: 세 번의 자기수정 후 최종 실패
$requestFailed = "$RequestPrefix-graph-exhausted"
Add-DemoEvent 270 $requestFailed "http.request.started" "POST /api/chat 요청을 시작했습니다" "http" -Details @{ method = "POST"; path = "/api/chat" }
Add-DemoEvent 268 $requestFailed "routing.completed" "질문을 GRAPH 경로로 분류했습니다" "pipeline" -Route "GRAPH" -Details @{ planned_tools = @("graph") }
Add-DemoEvent 260 $requestFailed "query.attempt.completed" "GRAPH 쿼리 실행에 실패했습니다 (1/3회) — 생성된 Cypher의 Neo4j 문법을 해석하지 못했습니다." "query" -Level "WARNING" -Route "GRAPH" -Tool "graph" -Outcome "failure" -Details @{ attempt = 1; max_attempts = 3; issue_code = "CYPHER_SYNTAX_ERROR"; failure_reason = "생성된 Cypher의 Neo4j 문법을 해석하지 못했습니다."; failure_stage = "execution"; failure_category = "QUERY_INVALID"; failed_query = "MATCH (p:Product)-[:REQUIRES_COMPONENT]->(c:Product WHERE p.ProductID = [VALUE] RETURN p.Name"; retryable = $true }
Add-DemoEvent 252 $requestFailed "repair.decision.made" "GRAPH 쿼리를 수정해 다시 시도합니다 (1/3회) — 생성된 Cypher의 Neo4j 문법을 해석하지 못했습니다." "repair" -Route "GRAPH" -Tool "graph" -Outcome "failure" -Details @{ decision = "retry"; attempt = 1; max_attempts = 3; issue_code = "CYPHER_SYNTAX_ERROR"; failure_reason = "생성된 Cypher의 Neo4j 문법을 해석하지 못했습니다."; repair_engine = "deterministic" }
Add-DemoEvent 240 $requestFailed "query.attempt.completed" "GRAPH 쿼리 실행에 실패했습니다 (2/3회) — 필수 결과 항목을 조회 결과에서 확인하지 못했습니다." "query" -Level "WARNING" -Route "GRAPH" -Tool "graph" -Outcome "failure" -Details @{ attempt = 2; max_attempts = 3; issue_code = "CYPHER_OUTPUT_CONTRACT_FAILED"; failure_reason = "필수 결과 항목을 조회 결과에서 확인하지 못했습니다."; retryable = $true }
Add-DemoEvent 232 $requestFailed "repair.decision.made" "GRAPH 쿼리를 수정해 다시 시도합니다 (2/3회) — 필수 결과 항목을 조회 결과에서 확인하지 못했습니다." "repair" -Route "GRAPH" -Tool "graph" -Outcome "failure" -Details @{ decision = "retry"; attempt = 2; max_attempts = 3; issue_code = "CYPHER_OUTPUT_CONTRACT_FAILED"; failure_reason = "필수 결과 항목을 조회 결과에서 확인하지 못했습니다." }
Add-DemoEvent 220 $requestFailed "query.attempt.completed" "GRAPH 쿼리 실행에 실패했습니다 (3/3회) — 필수 결과 항목을 조회 결과에서 확인하지 못했습니다." "query" -Level "WARNING" -Route "GRAPH" -Tool "graph" -Outcome "failure" -Details @{ attempt = 3; max_attempts = 3; issue_code = "CYPHER_OUTPUT_CONTRACT_FAILED"; failure_reason = "필수 결과 항목을 조회 결과에서 확인하지 못했습니다."; retryable = $false }
Add-DemoEvent 215 $requestFailed "repair.exhausted" "GRAPH 쿼리 자기수정에 실패해 처리를 중단했습니다 (3/3회) — 필수 결과 항목을 조회 결과에서 확인하지 못했습니다." "repair" -Level "ERROR" -Route "GRAPH" -Tool "graph" -Outcome "failure" -Details @{ attempt = 3; max_attempts = 3; issue_code = "CYPHER_OUTPUT_CONTRACT_FAILED"; failure_reason = "필수 결과 항목을 조회 결과에서 확인하지 못했습니다."; failed_query = "MATCH (p:Product)-[:BELONGS_TO]->(c:Category) WHERE p.productCode = [VALUE] RETURN p.name" }
Add-DemoEvent 210 $requestFailed "failure.review.created" "운영자 확인이 필요한 실패를 검토 목록에 등록했습니다 — CYPHER_OUTPUT_CONTRACT_FAILED" "admin_review" -Route "GRAPH" -Tool "graph" -Details @{ review_id = 1042; issue_code = "CYPHER_OUTPUT_CONTRACT_FAILED" }
Add-DemoEvent 205 $requestFailed "query.pipeline.completed" "GRAPH 질문 처리를 자기수정 횟수를 소진해 실패했습니다 (4.88초)" "pipeline" -Level "ERROR" -Route "GRAPH" -Outcome "failure" -Details @{ final_status = "repair_exhausted"; duration_ms = 4881.9; failed_tools = @("graph"); issue_code = "CYPHER_OUTPUT_CONTRACT_FAILED"; failure_reason = "필수 결과 항목을 조회 결과에서 확인하지 못했습니다."; failed_query = "MATCH (p:Product)-[:BELONGS_TO]->(c:Category) WHERE p.productCode = [VALUE] RETURN p.name" }
Add-DemoEvent 204 $requestFailed "http.request.completed" "POST /api/chat 요청이 HTTP 200 상태로 완료됐습니다 (4.95초)" "http" -Route "GRAPH" -Outcome "success" -Details @{ method = "POST"; path = "/api/chat"; status_code = 200; duration_ms = 4950.1 }

# 요청 4: 실제 모델 관측 이벤트(성공 및 실패)
$requestModel = "$RequestPrefix-model-observability"
Add-DemoEvent 180 $requestModel "model.call.started" "routing 모델 호출을 시작했습니다" "model" -Route "UNKNOWN" -Details @{ model_purpose = "routing"; model_name = "gpt-4.1-mini" }
Add-DemoEvent 178 $requestModel "model.call.completed" "routing 모델 호출이 완료됐습니다 (842ms)" "model" -Route "UNKNOWN" -Details @{ model_purpose = "routing"; model_name = "gpt-4.1-mini"; duration_ms = 842.4; input_tokens = 1240; output_tokens = 186; cached_input_tokens = 768; cache_write_tokens = 0; reasoning_tokens = 0; total_tokens = 1426; pricing_status = "configured"; pricing_version = "v1" }
Add-DemoEvent 170 $requestModel "model.call.started" "answer 모델 호출을 시작했습니다" "model" -Route "GRAPH" -Details @{ model_purpose = "answer"; model_name = "gpt-4.1-mini" }
Add-DemoEvent 168 $requestModel "model.call.failed" "answer 모델 호출에 실패했습니다" "model" -Level "ERROR" -Route "GRAPH" -Outcome "failure" -Details @{ model_purpose = "answer"; model_name = "gpt-4.1-mini"; duration_ms = 2011.7 }

# 요청 5: 실제 요청 가드 정책 차단과 관리자 검토 흐름
$requestBlocked = "$RequestPrefix-policy-blocked"
Add-DemoEvent 145 $requestBlocked "http.request.started" "POST /api/chat 요청을 시작했습니다" "http" -Details @{ method = "POST"; path = "/api/chat" }
Add-DemoEvent 143 $requestBlocked "audit.guard.decision" "SQL 쿼리를 안전성 검사에서 차단했습니다 — WRITE_INTENT_DETECTED" "audit" -Route "SQL" -Tool "sql" -Outcome "blocked" -Details @{ decision = "BLOCK"; stage = "pre_execution"; issue_code = "WRITE_INTENT_DETECTED" }
Add-DemoEvent 141 $requestBlocked "query.pipeline.completed" "SQL 질문 처리를 안전 정책에 의해 차단됐습니다 (96ms)" "pipeline" -Level "WARNING" -Route "SQL" -Outcome "blocked" -Details @{ final_status = "policy_blocked"; duration_ms = 96.2; planned_tools = @(); executed_tools = @(); successful_tools = @(); failed_tools = @(); skipped_tools = @() }
Add-DemoEvent 140 $requestBlocked "http.request.completed" "POST /api/chat 요청이 HTTP 200 상태로 완료됐습니다 (103ms)" "http" -Route "SQL" -Details @{ method = "POST"; path = "/api/chat"; status_code = 200; duration_ms = 103.1 }

Add-DemoEvent 120 $requestFailed "admin.review.viewed" "관리자 검토 조회" "admin_review" -Route "UNKNOWN" -Details @{ review_id = 1042 }
Add-DemoEvent 110 $requestFailed "admin.review.updated" "관리자 검토 변경" "admin_review" -Route "UNKNOWN" -Details @{ review_id = 1042; status = "resolved" }

# 요청 6: HYBRID 일부 성공 — SQL 결과는 사용하고 GRAPH 의존 도구는 건너뜀
$requestHybrid = "$RequestPrefix-hybrid-partial"
Add-DemoEvent 105 $requestHybrid "http.request.started" "POST /api/chat 요청을 시작했습니다" "http" -Details @{ method = "POST"; path = "/api/chat" }
Add-DemoEvent 103 $requestHybrid "routing.completed" "질문을 HYBRID 경로로 분류했습니다" "pipeline" -Route "HYBRID" -Details @{ planned_tools = @("sql", "graph") }
Add-DemoEvent 101 $requestHybrid "planning.completed" "HYBRID 실행 계획을 만들었습니다 (하위 질의 2개)" "pipeline" -Route "HYBRID" -Details @{ subquery_count = 2; planned_tools = @("sql", "graph") }
Add-DemoEvent 98 $requestHybrid "database.query.completed" "SQL 데이터베이스 조회가 완료됐습니다 (83ms)" "database" -Route "HYBRID" -Tool "sql" -Details @{ duration_ms = 83.4; row_count = 14 }
Add-DemoEvent 95 $requestHybrid "database.query.failed" "GRAPH 데이터베이스 조회에 실패했습니다 — 연결 시간이 초과됐습니다" "database" -Level "ERROR" -Route "HYBRID" -Tool "graph" -Outcome "failure" -Details @{ duration_ms = 2012.8; issue_code = "NEO4J_TIMEOUT"; failure_reason = "연결 시간이 초과됐습니다" }
Add-DemoEvent 93 $requestHybrid "tool.execution.skipped" "GRAPH 후속 조회를 의존 도구 실패로 건너뛰었습니다" "pipeline" -Level "WARNING" -Route "HYBRID" -Tool "graph" -Outcome "skipped" -Details @{ reason = "DEPENDENCY_FAILED" }
Add-DemoEvent 90 $requestHybrid "result.composed" "SQL 결과를 사용해 부분 성공 응답을 구성했습니다" "result" -Route "HYBRID" -Details @{ final_status = "partial_success"; successful_tools = @("sql"); failed_tools = @("graph") }
Add-DemoEvent 88 $requestHybrid "query.pipeline.completed" "HYBRID 질문 처리를 부분 성공으로 완료했습니다 (3.41초)" "pipeline" -Route "HYBRID" -Details @{ final_status = "partial_success"; duration_ms = 3410.5; successful_tools = @("sql"); failed_tools = @("graph") }
Add-DemoEvent 87 $requestHybrid "http.request.completed" "POST /api/chat 요청이 HTTP 200 상태로 완료됐습니다 (3.48초)" "http" -Route "HYBRID" -Details @{ method = "POST"; path = "/api/chat"; status_code = 200; duration_ms = 3481.2 }

# 요청 7: 정상적인 빈 결과를 오류와 구분해 수용
$requestEmpty = "$RequestPrefix-empty-result"
Add-DemoEvent 80 $requestEmpty "query.attempt.completed" "SQL 쿼리 실행이 완료됐지만 결과가 비어 있습니다" "query" -Route "SQL" -Tool "sql" -Details @{ attempt = 1; max_attempts = 3; row_count = 0; duration_ms = 42.1 }
Add-DemoEvent 78 $requestEmpty "result.empty.classified" "빈 결과를 조회 조건에 맞는 데이터 없음으로 분류했습니다" "result" -Route "SQL" -Tool "sql" -Details @{ empty_reason = "NO_DATA"; row_count = 0 }
Add-DemoEvent 76 $requestEmpty "query.pipeline.completed" "SQL 질문 처리를 정상 빈 결과로 완료했습니다" "pipeline" -Route "SQL" -Details @{ final_status = "accepted_empty"; duration_ms = 318.6 }

# 요청 8: 인프라 실패와 내부 실패를 운영 장애 패널에서 구분
$requestInfra = "$RequestPrefix-infrastructure-failure"
Add-DemoEvent 68 $requestInfra "database.connection.failed" "PostgreSQL 연결 풀에서 연결을 가져오지 못했습니다" "database" -Level "ERROR" -Route "SQL" -Tool "sql" -Outcome "failure" -Details @{ issue_code = "POSTGRES_UNAVAILABLE"; failure_reason = "연결 풀 대기 시간이 초과됐습니다" }
Add-DemoEvent 66 $requestInfra "query.pipeline.completed" "SQL 질문 처리를 인프라 오류로 종료했습니다" "pipeline" -Level "ERROR" -Route "SQL" -Outcome "failure" -Details @{ final_status = "infrastructure_failure"; failed_tools = @("sql"); issue_code = "POSTGRES_UNAVAILABLE" }
Add-DemoEvent 65 $requestInfra "http.request.completed" "POST /api/chat 요청이 HTTP 503 상태로 완료됐습니다" "http" -Level "ERROR" -Route "SQL" -Outcome "failure" -Details @{ method = "POST"; path = "/api/chat"; status_code = 503; duration_ms = 1120.4 }

$requestInternal = "$RequestPrefix-internal-failure"
Add-DemoEvent 58 $requestInternal "pipeline.node.failed" "답변 생성 단계에서 예상하지 못한 오류가 발생했습니다" "pipeline" -Level "ERROR" -Route "GRAPH" -Outcome "failure" -Details @{ node = "generate_answer"; issue_code = "INTERNAL_ERROR" }
Add-DemoEvent 56 $requestInternal "query.pipeline.completed" "GRAPH 질문 처리를 내부 오류로 종료했습니다" "pipeline" -Level "ERROR" -Route "GRAPH" -Outcome "failure" -Details @{ final_status = "internal_failure"; issue_code = "INTERNAL_ERROR" }

# 차단뿐 아니라 정상 허용 결정도 감사 대시보드에서 비교할 수 있게 한다.
Add-DemoEvent 50 $requestGraph "audit.guard.decision" "GRAPH 쿼리를 안전성 검사에서 허용했습니다" "audit" -Route "GRAPH" -Tool "graph" -Details @{ decision = "ALLOW"; stage = "pre_execution"; reason = "읽기 전용 쿼리" }

$streams = $events | Group-Object {
    $record = $_.Record
    "$($record.level)|$($record.route)|$($record.tool)|$($record.event_category)"
} | ForEach-Object {
    $first = $_.Group[0].Record
    $values = [System.Collections.Generic.List[object]]::new()
    foreach ($event in ($_.Group | Sort-Object Timestamp)) {
        $values.Add([object[]]@(
            $event.Timestamp,
            ($event.Record | ConvertTo-Json -Compress -Depth 8)
        ))
    }
    [ordered]@{
        stream = [ordered]@{
            service_name = "itda-backend"
            deployment_environment = "presentation-demo"
            demo_data = "true"
            demo_batch = $RequestPrefix
            level = $first.level
            route = $first.route
            tool = $first.tool
            event_category = $first.event_category
        }
        values = $values.ToArray()
    }
}

$payload = @{ streams = @($streams) } | ConvertTo-Json -Compress -Depth 12
$payloadBytes = [Text.Encoding]::UTF8.GetBytes($payload)
$null = Invoke-RestMethod -Method Post -Uri "$LokiUrl/loki/api/v1/push" -ContentType "application/json; charset=utf-8" -Body $payloadBytes
if ($PassThruCount) {
    Write-Output $events.Count
} else {
    Write-Output "Seeded $($events.Count) clearly labeled presentation demo log events."
}
