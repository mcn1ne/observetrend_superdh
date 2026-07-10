// ViewModel — 알림 (판정 O 묶음 + 발송 이력)
import { computed, ref } from 'vue'
import { api } from '../models/api.js'
import { usePolling } from './usePolling.js'

export function useAlertsViewModel() {
  const data = ref({ updated_at: null, results: [], history: [] })

  const { loading, error, refresh } = usePolling(async () => {
    data.value = await api.getAlerts()
  }, 10000)

  const current = computed(() => data.value.results ?? [])
  const history = computed(() => data.value.history ?? [])

  // 피드백: correct=true → "판정이 맞았다"(원 판정 그대로가 정답 라벨),
  //         correct=false → "판정이 틀렸다"(반대 라벨로 교정).
  // 같은 버튼을 다시 누르면 평가 취소(null). feedback 값 자체가 정답 라벨(O/X).
  async function sendFeedback(alert, correct) {
    const label = correct ? alert.decision : (alert.decision === 'O' ? 'X' : 'O')
    const next = alert.feedback === label ? null : label
    await api.setAlertFeedback(alert.id, next)
    alert.feedback = next // 즉시 반영 (다음 폴링에서 서버값으로 동기화)
  }

  const feedbackCount = computed(
    () => history.value.filter((a) => a.feedback === 'O' || a.feedback === 'X').length,
  )

  return { current, history, feedbackCount, sendFeedback, loading, error, refresh }
}
