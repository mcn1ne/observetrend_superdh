<script setup>
// View — 글 단위 분석 (adapters4): 큐 상태 + 주제문구·대분류·감성 스트림
import { useGemmaViewModel, sentimentGroup } from '../viewmodels/useGemmaViewModel.js'

const vm = useGemmaViewModel()

const GROUP_TONE = { 긍정: 'ok', 중립: 'dim', 부정: 'danger' }
const MAJORS = ['일반', '콘텐츠', '운영', '밸런스', '과금', '버그']

function fmt(iso) {
  return iso ? new Date(iso).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>Gemma 분류·분석</h1>
        <div class="sub">
          보유 파인튜닝 분류기(adapters4)가 모든 글을 단건으로 읽고
          <b>주제문구·대분류·감성</b>을 매깁니다. 주제문구는 알림 판단(파인튜닝 4B)의
          학습 입력 라벨과 같은 어휘라, 버스트 주제의 판단 입력에 그대로 쓰입니다.
          큐 기반이라 폭증에도 유실이 없습니다.
        </div>
      </div>
      <div style="display: flex; gap: 8px; align-items: center;">
        <select v-model="vm.sentimentFilter.value" style="width: 110px;">
          <option value="">감성 전체</option>
          <option value="부정">부정 계열</option>
          <option value="중립">중립만</option>
          <option value="긍정">긍정만</option>
        </select>
        <select v-model="vm.majorFilter.value" style="width: 130px;">
          <option value="">대분류 전체</option>
          <option v-for="m in MAJORS" :key="m" :value="m">{{ m }}</option>
        </select>
      </div>
    </div>

    <div v-if="vm.error.value" class="error-banner">{{ vm.error.value }}</div>

    <div class="grid cols-4">
      <div class="card stat">
        <div class="k">대기 큐</div>
        <div class="v" :class="{ warn: vm.queue.value.pending > 100 }">{{ vm.queue.value.pending ?? '—' }}</div>
        <div class="faint">폭증 시 쌓였다가 자동 소화</div>
      </div>
      <div class="card stat"><div class="k">분석 완료</div><div class="v accent">{{ vm.queue.value.done ?? '—' }}</div></div>
      <div class="card stat"><div class="k">실패 (3회 초과)</div><div class="v">{{ vm.queue.value.failed ?? '—' }}</div></div>
      <div class="card stat">
        <div class="k">부정 비율 (24h)</div>
        <div class="v" :class="{ danger: vm.negativeRatio.value > 0.6 }">{{ (vm.negativeRatio.value * 100).toFixed(0) }}%</div>
        <div class="faint">여론 온도계</div>
      </div>
    </div>

    <div class="card" style="margin-top: 14px;">
      <h2>분석 결과 (최근 24시간, 최신순)</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>시각</th><th>카테고리</th><th>대분류</th><th>감성</th><th>주제문구</th><th>제목 (원 게시물)</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in vm.results.value" :key="r.id">
              <td class="mono muted" style="white-space: nowrap;">{{ fmt(r.created_at) }}</td>
              <td style="white-space: nowrap;">
                <RouterLink v-if="r.category_id" :to="`/categories/${r.category_id}`" class="badge accent cat-link">
                  {{ r.category_name ?? '#' + r.category_id }}
                </RouterLink>
                <span v-else class="badge dim">미배정</span>
              </td>
              <td><span v-if="r.major" class="badge dim">{{ r.major }}</span><span v-else class="muted">—</span></td>
              <td>
                <span class="badge" :class="GROUP_TONE[sentimentGroup(r.sentiment)]" :title="r.sentiment">{{ r.sentiment }}</span>
              </td>
              <td>{{ r.topic_label ?? r.gist }}</td>
              <td>
                <a v-if="r.url" :href="r.url" target="_blank" rel="noopener" class="post-link">{{ r.title }} ↗</a>
                <span v-else>{{ r.title }}</span>
              </td>
            </tr>
            <tr v-if="!vm.results.value.length && !vm.loading.value">
              <td colspan="6" class="empty">조건에 맞는 분석 결과가 없습니다. 워커가 큐를 소화하는 중일 수 있습니다.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stat .k { font-size: 12px; color: var(--text-dim); font-weight: 600; margin-bottom: 6px; }
.stat .v { font-size: 26px; font-weight: 700; }
.stat .v.accent { color: var(--accent); }
.stat .v.warn { color: var(--warn); }
.stat .v.danger { color: var(--danger); }
.cat-link { text-decoration: none; }
.cat-link:hover { filter: brightness(1.2); }
.post-link { color: var(--text); text-decoration: none; }
.post-link:hover { color: var(--accent); text-decoration: underline; }
</style>
