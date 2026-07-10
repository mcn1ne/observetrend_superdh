// ViewModel — HDBSCAN 안전망 점검 (카테고리 중복/누락 제안)
import { ref } from 'vue'
import { api } from '../models/api.js'

export function useReclusterViewModel() {
  const running = ref(false)
  const result = ref(null)
  const error = ref(null)

  async function check() {
    if (running.value) return
    running.value = true
    error.value = null
    try {
      result.value = await api.reclusterCheck()
    } catch (e) {
      error.value = e.message
    } finally {
      running.value = false
    }
  }

  return { running, result, error, check }
}
