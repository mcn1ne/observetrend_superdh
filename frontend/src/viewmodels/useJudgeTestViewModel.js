// ViewModel — 판단 모델(파인튜닝 4B) 단독 테스트
// 입력을 adapters3 학습 형식("주제___제목___요약")으로 조립해 보낸다.
import { computed, ref } from 'vue'
import { api } from '../models/api.js'

export function useJudgeTestViewModel() {
  const label = ref('')
  const title = ref('')
  const summary = ref('')
  const running = ref(false)
  const error = ref(null)
  const results = ref([]) // 최근 테스트 이력 (세션 내)

  const judgeInput = computed(() =>
    [label.value, title.value, summary.value].join('___'),
  )
  const canSubmit = computed(
    () => !running.value && (label.value || title.value || summary.value),
  )

  async function submit() {
    if (!canSubmit.value) return
    running.value = true
    error.value = null
    try {
      const res = await api.judgeTest(judgeInput.value)
      results.value.unshift({ ...res, at: new Date().toLocaleTimeString('ko-KR') })
    } catch (e) {
      error.value = e.message
    } finally {
      running.value = false
    }
  }

  return { label, title, summary, judgeInput, canSubmit, running, error, results, submit }
}
