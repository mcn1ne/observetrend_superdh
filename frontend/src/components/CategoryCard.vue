<script setup>
// 카테고리 카드 — 이름·점수·추이·버스트·판정. 알림된 카테고리는 강조(상단 고정용)
// 카드 클릭 → 카테고리 상세(전체 글 목록) / 미리보기 글 클릭 → 원 게시물 새 탭
import { useRouter } from 'vue-router'
import HeatBar from './HeatBar.vue'
import Sparkline from './Sparkline.vue'

defineProps({
  category: Object,
  maxHeat: { type: Number, default: 1 },
})

const router = useRouter()

function timeAgo(iso) {
  if (!iso) return ''
  const min = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
  if (min < 1) return '방금'
  if (min < 60) return `${min}분 전`
  return `${Math.floor(min / 60)}시간 전`
}
</script>

<template>
  <div class="card category-card"
    :class="{ burst: category.is_burst, alerted: category.decision === 'O' }"
    role="link" tabindex="0"
    @click="router.push(`/categories/${category.category_id}`)"
    @keydown.enter="router.push(`/categories/${category.category_id}`)">
    <div class="head">
      <div class="title-area">
        <strong class="cat-name">{{ category.name }}</strong>
        <span class="faint mono">#{{ category.category_id }}</span>
      </div>
      <div class="badges">
        <span v-if="category.decision === 'O'" class="badge danger">🚨 알림</span>
        <span v-else-if="category.decision === 'X'" class="badge dim">판정 X</span>
        <span v-if="category.is_burst" class="badge warn">🔥 확산 중</span>
        <span v-if="category.alerted" class="badge accent">📣 방금 발송</span>
      </div>
    </div>

    <div class="score-row">
      <div class="score">
        <span class="score-num">{{ category.heat.toFixed(1) }}</span>
        <span class="faint">점수</span>
      </div>
      <Sparkline :values="category.score_history ?? []" />
      <div class="meta">
        <div>창 내 <b>{{ category.size }}</b>건 · 최근 1h <b>{{ category.recent_count }}</b>건</div>
        <div class="faint">누적 {{ category.total_count }}건 · {{ timeAgo(category.latest_at) }} 마지막 글</div>
      </div>
    </div>

    <HeatBar :heat="category.heat" :max="maxHeat" />

    <p v-if="category.summary" class="summary">{{ category.summary.summary }}</p>

    <ul class="preview">
      <li v-for="(p, i) in category.preview" :key="i">
        <a v-if="p.url" :href="p.url" target="_blank" rel="noopener"
          class="post-link" @click.stop>{{ p.title }} ↗</a>
        <span v-else>{{ p.title ?? p }}</span>
      </li>
    </ul>

    <div class="more faint">클릭하면 전체 글 목록 →</div>
  </div>
</template>

<style scoped>
.category-card { display: flex; flex-direction: column; gap: 10px; cursor: pointer; transition: border-color 0.15s, background 0.15s; }
.category-card:hover { background: var(--surface-hover); border-color: var(--accent); }
.category-card.burst { border-color: rgba(245, 166, 35, 0.45); }
.category-card.alerted { border-color: rgba(255, 93, 115, 0.55); box-shadow: inset 0 0 0 1px rgba(255, 93, 115, 0.35); }
.head { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; flex-wrap: wrap; }
.title-area { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.cat-name { font-size: 14.5px; letter-spacing: -0.01em; }
.badges { display: flex; gap: 6px; flex-wrap: wrap; }
.score-row { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.score { display: flex; align-items: baseline; gap: 6px; }
.score-num { font-size: 22px; font-weight: 700; letter-spacing: -0.02em; }
.meta { font-size: 12px; color: var(--text-dim); line-height: 1.5; }
.meta b { color: var(--text); }
.summary {
  font-size: 13px;
  color: var(--text-dim);
  background: var(--bg-soft);
  border-radius: 8px;
  padding: 10px 12px;
}
.preview { list-style: none; display: flex; flex-direction: column; gap: 4px; }
.preview li {
  font-size: 12.5px;
  color: var(--text-faint);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.preview li::before { content: "· "; }
.post-link { color: var(--text-dim); text-decoration: none; }
.post-link:hover { color: var(--accent); text-decoration: underline; }
.more { text-align: right; font-size: 11.5px; margin-top: auto; }
</style>
