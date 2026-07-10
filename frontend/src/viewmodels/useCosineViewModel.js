// ViewModel — 순수 코사인 유사도 묶기 (임계값 슬라이더 탐색)
import { ref, watch } from 'vue'
import { api } from '../models/api.js'

export function useCosineViewModel() {
  const threshold = ref(0.72)
  const running = ref(false)
  const result = ref(null)
  const error = ref(null)

  let timer = null
  let seq = 0

  async function run() {
    const my = ++seq
    running.value = true
    error.value = null
    try {
      const res = await api.cosineCluster(threshold.value)
      if (my !== seq) return // 슬라이더가 그 사이 또 움직였으면 무시
      if (res.error) {
        error.value = res.error
      } else {
        result.value = res
      }
    } catch (e) {
      if (my === seq) error.value = e.message
    } finally {
      if (my === seq) running.value = false
    }
  }

  // 슬라이더 조작 후 400ms 멈추면 자동 실행
  watch(threshold, () => {
    clearTimeout(timer)
    timer = setTimeout(run, 400)
  })

  return { threshold, running, result, error, run }
}
