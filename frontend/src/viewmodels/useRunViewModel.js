// ViewModel — 파이프라인 수동 실행 버튼
import { ref } from 'vue'
import { api } from '../models/api.js'

export function useRunViewModel() {
  const running = ref(false)
  const lastStats = ref(null)
  const error = ref(null)

  async function run() {
    if (running.value) return
    running.value = true
    error.value = null
    try {
      const res = await api.triggerRun()
      lastStats.value = res.stats
    } catch (e) {
      error.value = e.message
    } finally {
      running.value = false
    }
  }

  return { running, lastStats, error, run }
}
