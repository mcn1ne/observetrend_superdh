// ViewModel — 영속 카테고리 (최신 분석 결과 + 점수 추이)
// 관제 규약: 알림(O) 카테고리가 항상 상단 → 그 다음 확산 중 → 점수 순
import { computed, ref } from 'vue'
import { api } from '../models/api.js'
import { usePolling } from './usePolling.js'

export function useCategoriesViewModel() {
  const data = ref({ updated_at: null, results: [], stats: {} })

  const { loading, error, refresh } = usePolling(async () => {
    data.value = await api.getCategories()
  }, 10000)

  const categories = computed(() =>
    [...(data.value.results ?? [])].sort((a, b) => {
      const pin = (c) => (c.decision === 'O' ? 2 : c.is_burst ? 1 : 0)
      if (pin(b) !== pin(a)) return pin(b) - pin(a)   // 알림 상단 고정
      return (b.heat ?? 0) - (a.heat ?? 0)
    }),
  )
  const alerts = computed(() => categories.value.filter((c) => c.decision === 'O'))
  const bursts = computed(() => categories.value.filter((c) => c.is_burst))
  const stats = computed(() => data.value.stats ?? {})
  const updatedAt = computed(() => data.value.updated_at)
  const maxHeat = computed(() =>
    Math.max(1, ...categories.value.map((c) => c.heat ?? 0)),
  )

  return { categories, alerts, bursts, stats, updatedAt, maxHeat, loading, error, refresh }
}
