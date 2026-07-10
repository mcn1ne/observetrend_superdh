<script setup>
// View — 카테고리 전체 목록 (알림 상단 고정 → 확산 → 점수 순)
import CategoryCard from '../components/CategoryCard.vue'
import { useCategoriesViewModel } from '../viewmodels/useCategoriesViewModel.js'

const vm = useCategoriesViewModel()
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>카테고리</h1>
        <div class="sub">
          {{ vm.stats.value.n_categories ?? 0 }}개 카테고리 (영속) ·
          창 내 {{ vm.stats.value.total_posts_window ?? 0 }}건 ·
          분석 소요 {{ vm.stats.value.duration_sec ?? 0 }}초 ·
          정렬: 알림 → 확산 → 점수
        </div>
      </div>
    </div>

    <div v-if="vm.error.value" class="error-banner">{{ vm.error.value }}</div>

    <div v-if="vm.categories.value.length" class="grid cols-2 equal">
      <CategoryCard v-for="c in vm.categories.value" :key="c.category_id"
        :category="c" :max-heat="vm.maxHeat.value" />
    </div>
    <div v-else-if="!vm.loading.value" class="card empty">
      아직 분석 결과가 없습니다. 관제 화면에서 "지금 분석 실행"을 눌러보세요.
    </div>
  </div>
</template>
