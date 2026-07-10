<script setup>
// View — 주제 상세: 이 주제로 묶인 전체 글 (서버측 검색·정렬·50개 페이지네이션)
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../models/api.js'
import { usePolling } from '../viewmodels/usePolling.js'
import { useTopicsViewModel } from '../viewmodels/useTopicsViewModel.js'

const route = useRoute()
const router = useRouter()
const topicsVm = useTopicsViewModel()

const PER_PAGE = 50
const data = ref({ total: 0, page: 1, per_page: PER_PAGE, posts: [] })
const page = ref(1)
const q = ref('')          // 검색어 입력값 (디바운스 후 qApplied 로 반영)
const qApplied = ref('')
const sortKey = ref('created_at:desc') // 'created_at:desc' | 'created_at:asc' | 'title:asc'

const { loading, error, refresh } = usePolling(async () => {
  const [sort, order] = sortKey.value.split(':')
  data.value = await api.getTopicPosts(route.params.id, {
    page: page.value, perPage: PER_PAGE, q: qApplied.value, sort, order,
  })
  page.value = data.value.page // 서버가 범위를 보정하면 따라간다
}, 15000)

// 검색은 300ms 디바운스, 검색·정렬 변경 시 1페이지로
let debounce = null
watch(q, (v) => {
  clearTimeout(debounce)
  debounce = setTimeout(() => { qApplied.value = v.trim() }, 300)
})
watch([qApplied, sortKey], () => {
  if (page.value !== 1) page.value = 1 // page 워처가 refresh 를 대신 수행
  else refresh()
})
watch(page, refresh)

const totalPages = computed(() => Math.max(1, Math.ceil(data.value.total / PER_PAGE)))
const posts = computed(() => data.value.posts ?? [])

const topic = computed(() =>
  topicsVm.topics.value.find((t) => t.topic_id === Number(route.params.id)) ?? null)

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
          {{ topic?.name ?? `주제 #${route.params.id}` }}
        </h1>
        <div class="sub">
          전체 {{ data.total }}건
          <template v-if="topic">
            · 점수 {{ topic.heat.toFixed(1) }} · 최근 1h {{ topic.recent_count }}건
            <span v-if="topic.is_burst" class="badge warn">🔥 확산 중</span>
            <span v-if="topic.decision === 'O'" class="badge danger">🚨 알림</span>
          </template>
          <span v-else class="faint">(현재 분석 창 밖의 주제 — 이력 글만 표시)</span>
        </div>
      </div>
      <div class="controls">
        <input v-model="q" type="search" placeholder="제목·본문 검색" class="search" />
        <select v-model="sortKey" style="width: 120px;">
          <option value="created_at:desc">최신순</option>
          <option value="created_at:asc">오래된순</option>
          <option value="title:asc">제목순</option>
        </select>
      </div>
    </div>

    <!-- 최상위 ref는 템플릿에서 자동 언랩됨 — .value 를 붙이면 null.value 로 터진다 -->
    <div v-if="error" class="error-banner">{{ error }}</div>

    <div v-if="topic?.summary" class="card" style="margin-bottom: 14px;">
      <h2>요약 (Gemma 4)</h2>
      <p class="muted">{{ topic.summary.summary }}</p>
    </div>

    <div class="card">
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th style="width:110px;">시각</th><th>제목 (클릭 시 원 게시물)</th><th>본문</th></tr>
          </thead>
          <tbody>
            <tr v-for="p in posts" :key="p.id">
              <td class="mono muted" style="white-space: nowrap;">{{ fmt(p.created_at) }}</td>
              <td>
                <a v-if="p.url" :href="p.url" target="_blank" rel="noopener" class="post-link">{{ p.title }} ↗</a>
                <span v-else>{{ p.title }}</span>
              </td>
              <td class="muted body-cell">{{ p.body }}</td>
            </tr>
            <tr v-if="!posts.length && !loading">
              <td colspan="3" class="empty">
                {{ qApplied ? `'${qApplied}' 검색 결과가 없습니다.` : '이 주제로 묶인 글이 없습니다 (주제 구성은 매분 갱신됩니다).' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="totalPages > 1" class="pager">
        <button class="btn" :disabled="page <= 1" @click="page = 1">«</button>
        <button class="btn" :disabled="page <= 1" @click="page--">‹ 이전</button>
        <span class="mono muted">{{ page }} / {{ totalPages }}</span>
        <button class="btn" :disabled="page >= totalPages" @click="page++">다음 ›</button>
        <button class="btn" :disabled="page >= totalPages" @click="page = totalPages">»</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
h1 { display: flex; align-items: center; gap: 10px; }
.back { padding: 4px 10px; font-size: 14px; }
.controls { display: flex; gap: 8px; align-items: center; }
.search { width: 200px; }
.post-link { color: var(--text); text-decoration: none; font-weight: 500; }
.post-link:hover { color: var(--accent); text-decoration: underline; }
.body-cell { max-width: 420px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 12.5px; }
.pager { display: flex; gap: 8px; align-items: center; justify-content: center; margin-top: 12px; }
.pager .btn:disabled { opacity: 0.4; cursor: default; }
</style>
