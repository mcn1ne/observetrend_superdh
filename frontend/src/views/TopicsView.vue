<script setup>
// View — 주제 목록: "지금 게시판에서 돌고 있는 구체적인 이슈" 단위
import TopicCard from '../components/TopicCard.vue'
import { useTopicsViewModel } from '../viewmodels/useTopicsViewModel.js'

const vm = useTopicsViewModel()
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>주제</h1>
        <div class="sub">
          코사인 유사도로 묶은 미세 이슈 단위 (카테고리보다 구체적) ·
          {{ vm.stats.value.n_topics ?? 0 }}개 활동 중 ·
          버스트 → 요약 → 알림 판단이 이 단위로 돕니다 ·
          정렬: 알림 → 확산 → 점수
        </div>
      </div>
    </div>

    <div v-if="vm.error.value" class="error-banner">{{ vm.error.value }}</div>

    <div v-if="vm.topics.value.length" class="grid cols-2 equal">
      <TopicCard v-for="t in vm.topics.value" :key="t.topic_id"
        :topic="t" :max-heat="vm.maxHeat.value" />
    </div>
    <div v-else-if="!vm.loading.value" class="card empty">
      아직 주제로 묶일 만큼 모인 이슈가 없습니다 (최소 {{ 4 }}건 이상 유사한 글이 모여야 주제로 인정).
    </div>
  </div>
</template>
