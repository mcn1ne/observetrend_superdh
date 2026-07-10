// ViewModel 공통 유틸 — 주기 폴링 (마운트 시 시작, 언마운트 시 정리)
import { onMounted, onUnmounted, ref } from 'vue'

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

  onMounted(() => {
    refresh()
    timer = setInterval(refresh, intervalMs)
  })
  onUnmounted(() => clearInterval(timer))

  return { loading, error, refresh }
}
