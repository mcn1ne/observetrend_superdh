// ViewModel — 주제(미세 이슈) 목록. 버스트·요약·알림 판단의 단위.
// 정렬은 서버에서 이미 알림 → 확산 → 점수 순.
import { computed, ref } from 'vue'
import { api } from '../models/api.js'
import { usePolling } from './usePolling.js'

export function useTopicsViewModel() {
  const data = ref({ updated_at: null, topics: [], stats: {} })

  const { loading, error, refresh } = usePolling(async () => {
    data.value = await api.getTopics()
  }, 10000)

  const topics = computed(() => data.value.topics ?? [])
  const alerts = computed(() => topics.value.filter((t) => t.decision === 'O'))
  const bursts = computed(() => topics.value.filter((t) => t.is_burst))
  const stats = computed(() => data.value.stats ?? {})
  const updatedAt = computed(() => data.value.updated_at)
  const maxHeat = computed(() => Math.max(1, ...topics.value.map((t) => t.heat ?? 0)))

  return { topics, alerts, bursts, stats, updatedAt, maxHeat, loading, error, refresh }
}
