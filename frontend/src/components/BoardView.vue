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
})

const alerts = computed(() => props.topics.filter((t) => t.decision === 'O'))
const bursts = computed(() => props.topics.filter((t) => t.is_burst))
const maxHeat = computed(() =>
  Math.max(1, ...props.topics.map((t) => t.heat ?? 0), ...props.categories.map((c) => c.heat ?? 0)))
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
        <TopicCard v-for="t in alerts" :key="t.topic_id" :topic="t" :max-heat="maxHeat" />
      </div>
      <div v-else class="empty">이 시점에 알림 판정된 주제가 없습니다.</div>
    </div>

    <div v-if="showActiveTopics" class="card" style="margin-top: 14px;">
      <h2>📌 활동 주제 (미세 이슈)</h2>
      <div v-if="topics.length" class="grid cols-2 equal">
        <TopicCard v-for="t in topics.slice(0, 6)" :key="t.topic_id"
          :topic="t" :max-heat="maxHeat" />
      </div>
      <div v-else class="empty">아직 주제로 묶일 만큼 모인 이슈가 없습니다.</div>
    </div>

    <div v-if="showCategoryBoard" class="card" style="margin-top: 14px;">
      <h2>카테고리 점수 보드 (대분류)</h2>
      <div v-if="categories.length" class="grid cols-2 equal">
        <CategoryCard v-for="c in categories.slice(0, 8)" :key="c.category_id"
          :category="c" :max-heat="maxHeat" />
      </div>
      <div v-else class="empty">이 시점에 카테고리 결과가 없습니다.</div>
    </div>
  </div>
</template>

<style scoped>
.alert-zone { border-color: rgba(255, 93, 115, 0.35); }
</style>
