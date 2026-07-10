// ViewModel — 글 단위 분석 adapters4 (큐 상태 + 결과 스트림)
import { computed, ref } from 'vue'
import { api } from '../models/api.js'
import { usePolling } from './usePolling.js'

// adapters4 감성 7종 → 긍정/중립/부정 3계열 (구버전 행의 한글 라벨도 수용)
const POSITIVE = new Set(['positive', '긍정'])
const NEUTRAL = new Set(['neutral', '중립'])

export function sentimentGroup(s) {
  if (POSITIVE.has(s)) return '긍정'
  if (NEUTRAL.has(s)) return '중립'
  return '부정'
}

export function useGemmaViewModel() {
  const data = ref({ queue: {}, results: [] })
  const sentimentFilter = ref('') // ''=전체, '부정'/'중립'/'긍정' 계열
  const majorFilter = ref('')     // ''=전체, 일반/콘텐츠/운영/밸런스/과금/버그

  const { loading, error, refresh } = usePolling(async () => {
    data.value = await api.getGemmaAnalyses(24, 300)
  }, 10000)

  const queue = computed(() => data.value.queue ?? {})
  const results = computed(() => {
    let r = data.value.results ?? []
    if (sentimentFilter.value) r = r.filter((x) => sentimentGroup(x.sentiment) === sentimentFilter.value)
    if (majorFilter.value) r = r.filter((x) => x.major === majorFilter.value)
    return r
  })
  const negativeRatio = computed(() => {
    const all = data.value.results ?? []
    if (!all.length) return 0
    return all.filter((x) => sentimentGroup(x.sentiment) === '부정').length / all.length
  })

  return { queue, results, negativeRatio, sentimentFilter, majorFilter, loading, error, refresh }
}
