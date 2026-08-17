// ViewModel 공통 유틸 — 주기 폴링 (마운트 시 시작, 언마운트 시 정리)
// 게임 선택이 바뀌면 모든 폴링 뷰가 즉시 다시 불러온다.
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { currentGame } from '../models/gameState.js'

export function usePolling(fetcher, intervalMs = 10000) {
  const loading = ref(true)
  const error = ref(null)
  let timer = null

  async function refresh() {
    try {
      await fetcher()
      error.value = null
    } catch (e) {
      error.value = e.message
    } finally {
      loading.value = false
    }
  }

  watch(currentGame, () => { loading.value = true; refresh() })

  onMounted(() => {
    refresh()
    timer = setInterval(refresh, intervalMs)
  })
  onUnmounted(() => clearInterval(timer))

  return { loading, error, refresh }
}
