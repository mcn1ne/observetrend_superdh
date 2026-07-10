<script setup>
// View — 관제 대시보드: 알림 상단 고정 + 카테고리 점수 보드 + 슬롯/다이얼
import SlotBadge from '../components/SlotBadge.vue'
import BoardView from '../components/BoardView.vue'
import { useStatusViewModel } from '../viewmodels/useStatusViewModel.js'
import { useCategoriesViewModel } from '../viewmodels/useCategoriesViewModel.js'
import { useTopicsViewModel } from '../viewmodels/useTopicsViewModel.js'
import { usePostsViewModel } from '../viewmodels/usePostsViewModel.js'
import { useRunViewModel } from '../viewmodels/useRunViewModel.js'
import { useReclusterViewModel } from '../viewmodels/useReclusterViewModel.js'

const statusVm = useStatusViewModel()
const catVm = useCategoriesViewModel()
const topicsVm = useTopicsViewModel()
const postsVm = usePostsViewModel(1, 12)
const runVm = useRunViewModel()
const reclusterVm = useReclusterViewModel()

async function manualRun() {
  await runVm.run()
  await Promise.all([catVm.refresh(), topicsVm.refresh(), statusVm.refresh(), postsVm.refresh()])
}

function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleTimeString('ko-KR') : '—'
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>실시간 관제</h1>
        <div class="sub">
          <span class="live-dot" :class="{ off: !statusVm.status.value?.loop_running }" />
          {{ statusVm.status.value?.loop_running ? '파이프라인 루프 가동 중' : '루프 정지' }}
          · 마지막 분석 {{ fmtTime(catVm.updatedAt.value) }}
        </div>
      </div>
      <button class="btn primary" :disabled="runVm.running.value" @click="manualRun">
        {{ runVm.running.value ? '실행 중…' : '▶ 지금 분석 실행' }}
      </button>
    </div>

    <div v-if="statusVm.error.value" class="error-banner">서버 연결 실패: {{ statusVm.error.value }}</div>

    <BoardView
      :topics="topicsVm.topics.value"
      :categories="catVm.categories.value"
      :stats="{ total_posts_window: catVm.stats.value.total_posts_window, n_topics: topicsVm.stats.value.n_topics }"
      :window-hours="statusVm.status.value?.config?.window_hours ?? 24" />

    <div class="grid cols-2" style="margin-top: 14px; align-items: start;">
      <div class="card">
        <h2>파이프라인 슬롯 상태</h2>
        <SlotBadge v-for="s in statusVm.slots.value" :key="s.key" :slot_="s" />
      </div>

      <div>
        <div class="card">
          <h2>운영 다이얼 (08 문서)</h2>
          <table>
            <tbody>
              <tr v-for="d in statusVm.dials.value" :key="d.key">
                <td class="muted" style="white-space: nowrap;">{{ d.key }} · {{ d.label }}</td>
                <td><b>{{ d.value }}</b></td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="card">
          <h2>HDBSCAN 안전망 점검</h2>
          <p class="faint" style="margin-bottom: 10px;">
            재클러스터링으로 카테고리 중복(병합 후보)·배정 누락을 점검합니다. 하루 1회 권장.
          </p>
          <button class="btn" :disabled="reclusterVm.running.value" @click="reclusterVm.check">
            {{ reclusterVm.running.value ? '점검 중…' : '지금 점검' }}
          </button>
          <div v-if="reclusterVm.result.value" style="margin-top: 12px;">
            <div class="faint">
              {{ reclusterVm.result.value.checked }}건 점검 ·
              노이즈 {{ ((reclusterVm.result.value.noise_ratio ?? 0) * 100).toFixed(0) }}%
            </div>
            <ul v-if="reclusterVm.result.value.suggestions.length" class="suggestion-list">
              <li v-for="(s, i) in reclusterVm.result.value.suggestions" :key="i">
                <span class="badge warn">병합 검토</span> {{ s.hint }}
                <span class="faint">({{ s.cluster_size }}건 클러스터)</span>
              </li>
            </ul>
            <div v-else class="faint" style="margin-top: 6px;">제안 없음 — 배정 상태 양호</div>
          </div>
          <div v-if="reclusterVm.error.value" class="error-banner" style="margin-top: 10px;">
            {{ reclusterVm.error.value }}
          </div>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top: 14px;">
      <h2>최근 수집 글 (1시간)</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>시각</th><th>제목</th><th>임베딩</th></tr>
          </thead>
          <tbody>
            <tr v-for="p in postsVm.posts.value" :key="p.id">
              <td class="mono muted" style="white-space: nowrap;">{{ fmtTime(p.created_at) }}</td>
              <td>{{ p.title }}</td>
              <td>
                <span v-if="p.embedded" class="badge ok">완료</span>
                <span v-else class="badge dim">대기</span>
              </td>
            </tr>
            <tr v-if="!postsVm.posts.value.length">
              <td colspan="3" class="empty">아직 수집된 글이 없습니다.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.alert-zone { border-color: rgba(255, 93, 115, 0.35); }
.suggestion-list { list-style: none; margin-top: 8px; display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
</style>
