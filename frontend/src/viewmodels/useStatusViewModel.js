// ViewModel — 시스템 상태 (슬롯 준비 상태 · 루프 · 다이얼)
import { computed, ref } from 'vue'
import { api } from '../models/api.js'
import { usePolling } from './usePolling.js'

const SLOT_LABELS = {
  collector: '수집 (02)',
  captioner: '이미지 캡셔닝',
  embedding: '① 임베딩 (03)',
  categorizer: '② 카테고리 분류 (Gemma 4)',
  clustering: 'HDBSCAN 안전망 (04)',
  summarizer: '③ 요약 (05)',
  judge: '④ 알림 판단 (06)',
}

export function useStatusViewModel() {
  const status = ref(null)

  const { loading, error, refresh } = usePolling(async () => {
    status.value = await api.getStatus()
  }, 10000)

  const slots = computed(() => {
    const s = status.value?.slots ?? {}
    return Object.entries(SLOT_LABELS).map(([key, label]) => ({
      key,
      label,
      ready: s[key]?.ready ?? false,
      backend: s[key]?.backend ?? '-',
      name: s[key]?.name ?? '-',
      fused: s[key]?.fused ?? false,
    }))
  })

  const dials = computed(() => {
    const c = status.value?.config
    if (!c) return []
    return [
      { key: 'A', label: '수집 주기', value: `${c.collect_interval_sec}초` },
      { key: 'B', label: '분석 주기', value: `${c.analyze_interval_sec}초` },
      { key: 'C', label: '분석 범위', value: `최근 ${c.window_hours}시간` },
      { key: '감쇠', label: '반감기', value: `${c.half_life_min}분` },
      { key: '버스트', label: '급증 기준', value: `평소 ×${c.burst_ratio} · 최소 ${c.min_recent}건` },
      { key: '쿨다운', label: '재알림 금지', value: `${c.cooldown_min}분 (카테고리별)` },
      { key: '분류', label: '즉시 배정 유사도', value: `${c.category_assign_sim} (미달 시 LLM)` },
    ]
  })

  return { status, slots, dials, loading, error, refresh }
}
