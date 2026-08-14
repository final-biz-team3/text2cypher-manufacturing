import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

// Tailwind 클래스 문자열을 조건부로 합치고, 충돌하는 유틸리티 클래스는 나중 값으로 병합한다
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
