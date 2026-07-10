<script setup>
// View — 카테고리 상세: 24h 창 내 전체 글 목록, 각 글은 원 게시물로 링크
import { useRoute, useRouter } from 'vue-router'
import Sparkline from '../components/Sparkline.vue'
import { useCategoryDetailViewModel } from '../viewmodels/useCategoryDetailViewModel.js'

const route = useRoute()
const router = useRouter()
const vm = useCategoryDetailViewModel(route.params.id)

function fmt(iso) {
  return iso ? new Date(iso).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>
          <button class="btn back" @click="router.back()">←</button>
          {{ vm.category.value?.name ?? `카테고리 #${route.params.id}` }}
        </h1>
        <div class="sub">
          최근 24시간 {{ vm.count.value }}건
          <template v-if="vm.category.value">
            · 점수 {{ vm.category.value.heat.toFixed(1) }}
            · 최근 1h {{ vm.category.value.recent_count }}건
            <span v-if="vm.category.value.is_burst" class="badge warn">🔥 확산 중</span>
            <span v-if="vm.category.value.decision === 'O'" class="badge danger">🚨 알림</span>
          </template>
        </div>
      </div>
      <Sparkline v-if="vm.category.value?.score_history?.length"
        :values="vm.category.value.score_history" :width="180" :height="40" />
    </div>

    <div v-if="vm.error.value" class="error-banner">{{ vm.error.value }}</div>

    <div class="card">
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th style="width:110px;">시각</th><th>제목 (클릭 시 원 게시물)</th><th>본문</th></tr>
          </thead>
          <tbody>
            <tr v-for="p in vm.posts.value" :key="p.id">
              <td class="mono muted" style="white-space: nowrap;">{{ fmt(p.created_at) }}</td>
              <td>
                <a v-if="p.url" :href="p.url" target="_blank" rel="noopener" class="post-link">
                  {{ p.title }} ↗
                </a>
                <span v-else>{{ p.title }}</span>
              </td>
              <td class="muted body-cell">{{ p.body }}</td>
            </tr>
            <tr v-if="!vm.posts.value.length && !vm.loading.value">
              <td colspan="3" class="empty">최근 24시간 내 글이 없습니다.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
h1 { display: flex; align-items: center; gap: 10px; }
.back { padding: 4px 10px; font-size: 14px; }
.post-link { color: var(--text); text-decoration: none; font-weight: 500; }
.post-link:hover { color: var(--accent); text-decoration: underline; }
.body-cell {
  max-width: 420px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12.5px;
}
</style>
