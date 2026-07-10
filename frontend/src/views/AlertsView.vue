<script setup>
// View — 알림: 판정 O 카테고리 + 발송 이력
import TopicCard from '../components/TopicCard.vue'
import { useAlertsViewModel } from '../viewmodels/useAlertsViewModel.js'

const vm = useAlertsViewModel()

function fmt(iso) {
  return iso ? new Date(iso).toLocaleString('ko-KR') : ''
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>알림</h1>
        <div class="sub">파인튜닝 4B가 "발송(O)"으로 판정한 카테고리와 실제 발송 이력 (카테고리별 쿨다운 적용)</div>
      </div>
    </div>

    <div v-if="vm.error.value" class="error-banner">{{ vm.error.value }}</div>

    <div class="card">
      <h2>현재 발송 판정(O) 주제</h2>
      <div v-if="vm.current.value.length" class="grid cols-2 equal">
        <TopicCard v-for="t in vm.current.value" :key="t.topic_id" :topic="t" />
      </div>
      <div v-else class="empty">현재 알림 판정된 주제가 없습니다.</div>
    </div>

    <div class="card">
      <div class="history-head">
        <h2>판정 이력 — 판정이 맞았는지 평가해주세요 (발송 O · 미발송 X 모두)</h2>
        <a class="btn" href="/api/alerts/export" download>
          ⬇ 학습용 CSV ({{ vm.feedbackCount.value }}건 평가됨)
        </a>
      </div>
      <p class="faint" style="margin-bottom: 10px;">
        👍 = 판정이 맞았다 (원 판정을 정답으로 기록) · 👎 = 판정이 틀렸다 (반대 라벨로 교정) —
        O·X 양쪽을 평가해야 학습 데이터의 균형이 잡힙니다. notify_raw.csv 형식으로 다운로드됩니다.
      </p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>평가</th><th>판정</th><th>시각</th><th>주제</th><th>규모</th><th>요약</th></tr>
          </thead>
          <tbody>
            <tr v-for="a in vm.history.value" :key="a.id">
              <td style="white-space: nowrap;">
                <!-- 소강은 기계적 종료 신호 — 판단 모델 평가(학습 데이터) 대상 아님 -->
                <template v-if="a.decision !== '소강'">
                  <button class="fb" :class="{ on: a.feedback === a.decision }"
                    title="판정이 맞았다" @click="vm.sendFeedback(a, true)">👍</button>
                  <button class="fb" :class="{ on: a.feedback && a.feedback !== a.decision }"
                    title="판정이 틀렸다 (반대로 교정)" @click="vm.sendFeedback(a, false)">👎</button>
                </template>
              </td>
              <td>
                <span class="badge" :class="a.decision === 'O' ? 'danger' : a.decision === '소강' ? 'calm' : 'dim'">
                  {{ a.decision === 'O' ? 'O 발송' : a.decision === '소강' ? '🌤 소강' : 'X 미발송' }}
                </span>
              </td>
              <td class="mono muted" style="white-space: nowrap;">{{ fmt(a.created_at) }}</td>
              <td style="white-space: nowrap;">
                <RouterLink v-if="a.topic_id" :to="`/topics/${a.topic_id}`" class="topic-link">
                  <b>{{ a.label }}</b> →
                </RouterLink>
                <b v-else :title="'주제가 삭제·병합되어 연결할 수 없습니다'">{{ a.label }}</b>
              </td>
              <td>{{ a.size }}건</td>
              <td class="muted">{{ a.summary }}</td>
            </tr>
            <tr v-if="!vm.history.value.length">
              <td colspan="6" class="empty">판정 이력이 없습니다.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.history-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }
.fb {
  background: var(--bg-soft);
  border: 1px solid var(--border);
  border-radius: 7px;
  font-size: 15px;
  padding: 3px 8px;
  cursor: pointer;
  opacity: 0.45;
  transition: opacity 0.15s, border-color 0.15s, transform 0.1s;
}
.fb + .fb { margin-left: 4px; }
.fb:hover { opacity: 0.8; }
.fb.on { opacity: 1; border-color: var(--accent); background: var(--accent-soft); transform: scale(1.08); }
.badge.calm { background: var(--accent-soft); color: var(--accent); }
.topic-link { color: var(--text); text-decoration: none; }
.topic-link:hover { color: var(--accent); text-decoration: underline; }
</style>
