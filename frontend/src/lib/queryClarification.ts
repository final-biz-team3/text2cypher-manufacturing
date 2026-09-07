// Keep the original question and each explicit user clarification together.
// The server treats this entire string as untrusted user input on every request.
export function clarifyQuery(original: string, question: string, answer: string): string {
  return `${original}\n\n확인 질문: ${question}\n사용자 확인: ${answer.trim()}`
}
