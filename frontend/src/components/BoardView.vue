<script setup>
// 관제 보드 (프레젠테이셔널) — 라이브 대시보드와 타임머신 재생이 공유하는 렌더.
// 스냅샷 {topics, results(=categories), stats} 를 받아 알림·활동 주제·카테고리 보드를 그린다.
import { computed } from 'vue'
import StatCard from './StatCard.vue'
import TopicCard from './TopicCard.vue'
import CategoryCard from './CategoryCard.vue'

const props = defineProps({
  topics: { type: Array, default: () => [] },
  categories: { type: Array, default: () => [] }, // results
  stats: { type: Object, default: () => ({}) },
  windowHours: { type: Number, default: 24 },
  // 재생 모드에서는 카드 클릭 라우팅이 현재 상세와 어긋날 수 있어 숨길 수 있게
  showActiveTopics: { type: Boolean, default: true },
  showCategoryBoard: { type: Boolean, default: true },
  // 섹션 하단 "전체 보기 →" 링크 — 라이브 대시보드에서만 켠다
  // (타임머신 재생에서 라이브 탭으로 이동하면 시점이 어긋나 혼동)
  linkable: { type: Boolean, default: false },
  // 활동 주제 표시 개수 (0 = 전체 — 타임머신은 당시 주제를 전부 보여준다)
  topicLimit: { type: Number, default: 6 },
  // 스냅샷 시각 — 있으면 주제 카드 클릭이 "당시 구성" 모드로 연결됨
  snapshotAt: { type: String, default: '' },
})

const alerts = computed(() => props.topics.filter((t) => t.decision === 'O'))
const bursts = computed(() => props.topics.filter((t) => t.is_burst))
const shownTopics = computed(() =>
  props.topicLimit > 0 ? props.topics.slice(0, props.topicLimit) : props.topics)
// 게이지 기준은 계층별로 분리 — 카테고리(수백 글의 합)와 주제(수십 글)는 heat
// 스케일이 달라서 섞으면 주제 게이지가 주제 탭과 다르게(짧게) 그려진다.
const maxTopicHeat = computed(() =>
  Math.max(1, ...props.topics.map((t) => t.heat ?? 0)))
const maxCatHeat = computed(() =>
  Math.max(1, ...props.categories.map((c) => c.heat ?? 0)))
</script>

<template>
  <div>
    <div class="grid cols-4">
      <StatCard label="분석 창 내 글" :value="stats.total_posts_window ?? '—'"
        :hint="`최근 ${windowHours}시간`" />
      <StatCard label="활동 주제" :value="stats.n_topics ?? topics.length" tone="accent"
        hint="미세 이슈 단위 (알림의 기준)" />
      <StatCard label="알림 판정 (O)" :value="alerts.length" tone="danger"
        hint="4B 판단 모델 발송 판정" />
      <StatCard label="확산 중 (버스트)" :value="bursts.length" tone="warn"
        hint="평소 대비 급증한 주제" />
    </div>

    <div class="card alert-zone" style="margin-top: 14px;">
      <h2>🚨 알림 주제 (상단 고정)</h2>
      <div v-if="alerts.length" class="grid cols-2 equal">
        <TopicCard v-for="t in alerts" :key="t.topic_id" :topic="t"
          :max-heat="maxTopicHeat" :at="snapshotAt" />
      </div>
      <div v-else class="empty">이 시점에 알림 판정된 주제가 없습니다.</div>
      <RouterLink v-if="linkable" class="see-all" to="/alerts">알림 전체 보기 →</RouterLink>
    </div>

    <div v-if="showActiveTopics" class="card" style="margin-top: 14px;">
      <h2>📌 활동 주제 (미세 이슈)</h2>
      <div v-if="topics.length" class="grid cols-2 equal">
        <TopicCard v-for="t in shownTopics" :key="t.topic_id"
          :topic="t" :max-heat="maxTopicHeat" :at="snapshotAt" />
      </div>
      <div v-else class="empty">아직 주제로 묶일 만큼 모인 이슈가 없습니다.</div>
      <RouterLink v-if="linkable" class="see-all" to="/topics">
        주제 전체 보기 ({{ topics.length }}개) →</RouterLink>
    </div>

    <div v-if="showCategoryBoard" class="card" style="margin-top: 14px;">
      <h2>카테고리 점수 보드 (대분류)</h2>
      <div v-if="categories.length" class="grid cols-2 equal">
        <CategoryCard v-for="c in categories.slice(0, 8)" :key="c.category_id"
          :category="c" :max-heat="maxCatHeat" />
      </div>
      <div v-else class="empty">이 시점에 카테고리 결과가 없습니다.</div>
      <RouterLink v-if="linkable" class="see-all" to="/categories">카테고리 전체 보기 →</RouterLink>
    </div>
  </div>
</template>

<style scoped>
.alert-zone { border-color: rgba(255, 93, 115, 0.35); }
.see-all {
  display: block;
  margin-top: 12px;
  padding: 8px;
  text-align: center;
  border-radius: 8px;
  background: var(--bg-soft);
  color: var(--text-dim);
  text-decoration: none;
  font-size: 13px;
  font-weight: 600;
  transition: background 0.15s, color 0.15s;
}
.see-all:hover { background: var(--accent-soft); color: var(--accent); }
</style>
