// Neo4j 드라이버 예외의 str() 표현({neo4j_code: ...} {message: ...} {gql_status: ...}
// {gql_status_description: ...})을 한글 한 줄 설명으로 바꾼다. gql_status 계열은 항상 뻔한
// 보일러플레이트라 버린다. 패턴에 안 맞으면(이미 사람이 읽을 수 있는 문구) 원본 그대로 반환한다.
const RAW_NEO4J_ERROR_PATTERN = /^\{neo4j_code:\s*([^}]+)\}\s*\{message:\s*([^}]+)\}/

// code 문자열에 아래 키워드가 포함되는지로 유형을 구분한다(순서대로 검사, 먼저 매칭되는 것을 사용).
// "로/으로" 조사가 받침 유무에 따라 달라져 label에 조사까지 포함해 둔다.
const CODE_KEYWORD_LABELS: [string, string][] = [
  ['SyntaxError', 'Cypher 문법 오류로'],
  ['TypeError', 'Cypher 타입 오류로'],
  ['Constraint', '제약 조건 위반으로'],
  ['TimedOut', '쿼리 실행 시간 초과로'],
]

const FALLBACK_LABEL = '쿼리 실행 오류로'

// message 본문에서 line/column 위치를 뽑아낸다(offset은 사용자에게 의미 없어 버림).
const LOCATION_PATTERN = /\(line (\d+), column (\d+)(?:\s*\(offset:\s*\d+\))?\)/

// message 본문 자주 나오는 몇 가지 패턴만 한글로 바꾼다. 순서대로 검사해 먼저 매칭되는 것을 쓴다.
// 매칭 안 되면 본문 번역은 생략하고(원문 영어는 노출하지 않음) 위치 정보만 남긴다.
const MESSAGE_PATTERNS: [RegExp, (m: RegExpMatchArray) => string][] = [
  [
    /^Invalid input '([^']*)':\s*expected/,
    (m) =>
      m[1] === ''
        ? '입력이 예상보다 일찍 끝났습니다'
        : `입력값 '${m[1]}'을(를) 여기서 사용할 수 없습니다`,
  ],
  [/^Variable `([^`]+)` not defined/, (m) => `변수 '${m[1]}'이(가) 정의되지 않았습니다`],
]

function translateMessageBody(message: string): string | null {
  for (const [pattern, translate] of MESSAGE_PATTERNS) {
    const match = message.match(pattern)
    if (match) return translate(match)
  }
  return null
}

export function formatQueryError(raw: string): string {
  const match = raw.match(RAW_NEO4J_ERROR_PATTERN)
  if (!match) return raw

  const [, rawCode, message] = match
  const code = rawCode.trim()
  const found = CODE_KEYWORD_LABELS.find(([keyword]) => code.includes(keyword))
  const label = found ? found[1] : FALLBACK_LABEL

  const translatedMessage = translateMessageBody(message.trim())
  const location = message.match(LOCATION_PATTERN)
  const locationText = location ? `${location[1]}번째 줄 ${location[2]}번째 열` : null

  const detail = [translatedMessage, locationText].filter(Boolean).join(', ')
  return detail ? `${label} 실패했습니다 (${detail}).` : `${label} 실패했습니다.`
}
