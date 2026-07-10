<script setup>
// 주제 카드 — 이름·점수·버스트·판정·요약·미리보기. 알림 주제는 강조.
import { useRouter } from 'vue-router'
import HeatBar from './HeatBar.vue'

defineProps({
  topic: Object,
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
  <div class="card topic-card"
    :class="{ burst: topic.is_burst, alerted: topic.decision === 'O' }"
    role="link" tabindex="0"
    @click="router.push(`/topics/${topic.topic_id}`)"
    @keydown.enter="router.push(`/topics/${topic.topic_id}`)">
    <div class="head">
      <div class="title-area">
        <strong class="topic-name">{{ topic.name }}</strong>
        <span class="faint mono">#{{ topic.topic_id }}</span>
      </div>
      <div class="badges">
        <span v-if="topic.decision === 'O'" class="badge danger">🚨 알림</span>
        <span v-else-if="topic.decision === 'X'" class="badge dim">판정 X</span>
        <span v-if="topic.is_burst" class="badge warn">🔥 확산 중</span>
        <span v-if="topic.alerted" class="badge accent">📣 방금 발송</span>
      </div>
    </div>

    <div class="meta">
      <span>글 <b>{{ topic.size }}</b>건</span>
      <span>최근 1h <b>{{ topic.recent_count }}</b>건</span>
      <span>점수 <b>{{ topic.heat.toFixed(1) }}</b></span>
      <span class="faint">{{ timeAgo(topic.latest_at) }} 마지막 글</span>
    </div>

    <HeatBar :heat="topic.heat" :max="maxHeat" />

    <div v-if="topic.categories" class="cats">
      <span v-for="(n, name) in topic.categories" :key="name" class="badge dim">{{ name }} {{ n }}</span>
    </div>

    <p v-if="topic.summary" class="summary">{{ topic.summary.summary }}</p>

    <ul class="preview">
      <li v-for="(p, i) in topic.preview" :key="i">
        <a v-if="p.url" :href="p.url" target="_blank" rel="noopener"
          class="post-link" @click.stop>{{ p.title }} ↗</a>
        <span v-else>{{ p.title }}</span>
      </li>
    </ul>

    <div class="more faint">클릭하면 전체 글 목록 →</div>
  </div>
</template>

<style scoped>
.topic-card { display: flex; flex-direction: column; gap: 10px; cursor: pointer; transition: border-color 0.15s, background 0.15s; }
.topic-card:hover { background: var(--surface-hover); border-color: var(--accent); }
.topic-card.burst { border-color: rgba(245, 166, 35, 0.45); }
.topic-card.alerted { border-color: rgba(255, 93, 115, 0.55); box-shadow: inset 0 0 0 1px rgba(255, 93, 115, 0.35); }
.head { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; flex-wrap: wrap; }
.title-area { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.topic-name { font-size: 14.5px; letter-spacing: -0.01em; }
.badges { display: flex; gap: 6px; flex-wrap: wrap; }
.meta { display: flex; gap: 14px; font-size: 12.5px; color: var(--text-dim); flex-wrap: wrap; }
.meta b { color: var(--text); }
.cats { display: flex; gap: 6px; flex-wrap: wrap; }
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
