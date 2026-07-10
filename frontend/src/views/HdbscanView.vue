<script setup>
// View — HDBSCAN 안전망 결과: 임베딩만으로 다시 묶은 클러스터와
// 현재 카테고리 배정을 나란히 비교 (병합 후보·오배정 점검)
import { computed } from 'vue'
import { useReclusterViewModel } from '../viewmodels/useReclusterViewModel.js'

const vm = useReclusterViewModel()

const clusters = computed(() => vm.result.value?.clusters ?? [])

function purity(c) {
  const counts = Object.values(c.categories)
  return counts.length ? Math.max(...counts) / c.size : 1
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>HDBSCAN 점검</h1>
        <div class="sub">
          임베딩만으로 최근 24시간 글을 다시 묶어(카테고리 무시) 현재 배정과 비교합니다.
          한 클러스터에 여러 카테고리가 섞여 있으면 병합·오배정 후보입니다.
        </div>
      </div>
      <button class="btn primary" :disabled="vm.running.value" @click="vm.check">
        {{ vm.running.value ? '클러스터링 중…' : '▶ 지금 점검 실행' }}
      </button>
    </div>

    <div v-if="vm.error.value" class="error-banner">{{ vm.error.value }}</div>

    <template v-if="vm.result.value">
      <div class="grid cols-4">
        <div class="card stat"><div class="k">점검한 글</div><div class="v">{{ vm.result.value.checked }}</div></div>
        <div class="card stat"><div class="k">클러스터</div><div class="v">{{ vm.result.value.n_clusters }}</div>
          <div class="faint">min_cluster_size={{ vm.result.value.min_cluster_size }}</div></div>
        <div class="card stat"><div class="k">노이즈</div>
          <div class="v">{{ ((vm.result.value.noise_ratio ?? 0) * 100).toFixed(0) }}%</div>
          <div class="faint">{{ vm.result.value.noise_count }}건 · 30~50% 정상 (04 문서)</div></div>
        <div class="card stat"><div class="k">병합 제안</div>
          <div class="v" :class="{ warn: vm.result.value.suggestions.length }">{{ vm.result.value.suggestions.length }}</div></div>
      </div>

      <div v-if="vm.result.value.suggestions.length" class="card" style="margin-top: 14px;">
        <h2>⚠️ 병합 검토 제안</h2>
        <ul class="suggestion-list">
          <li v-for="(s, i) in vm.result.value.suggestions" :key="i">
            <span class="badge warn">병합 검토</span> {{ s.hint }}
            <span class="faint">({{ s.cluster_size }}건 클러스터)</span>
          </li>
        </ul>
      </div>

      <div class="card" style="margin-top: 14px;">
        <h2>클러스터별 상세 (크기순)</h2>
        <div class="cluster-list">
          <div v-for="c in clusters" :key="c.cluster" class="cluster-row">
            <div class="row-head">
              <span class="mono faint">#{{ c.cluster }}</span>
              <b>{{ c.size }}건</b>
              <span class="badge" :class="purity(c) >= 0.8 ? 'ok' : 'warn'">
                순도 {{ (purity(c) * 100).toFixed(0) }}%
              </span>
              <span class="comp">
                <span v-for="(n, name) in c.categories" :key="name" class="badge dim">
                  {{ name }} {{ n }}
                </span>
              </span>
            </div>
            <ul class="samples">
              <li v-for="(p, i) in c.samples" :key="i">
                <a v-if="p.url" :href="p.url" target="_blank" rel="noopener" class="post-link">{{ p.title }} ↗</a>
                <span v-else>{{ p.title }}</span>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </template>

    <div v-else-if="!vm.running.value" class="card empty">
      아직 점검을 실행하지 않았습니다. "지금 점검 실행"을 눌러주세요 (수 초 소요).
    </div>
  </div>
</template>

<style scoped>
.stat .k { font-size: 12px; color: var(--text-dim); font-weight: 600; margin-bottom: 6px; }
.stat .v { font-size: 26px; font-weight: 700; }
.stat .v.warn { color: var(--warn); }
.suggestion-list { list-style: none; display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
.cluster-list { display: flex; flex-direction: column; gap: 14px; }
.cluster-row { border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; background: var(--bg-soft); }
.row-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
.comp { display: flex; gap: 6px; flex-wrap: wrap; }
.samples { list-style: none; display: flex; flex-direction: column; gap: 3px; }
.samples li {
  font-size: 12.5px;
  color: var(--text-faint);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.samples li::before { content: "· "; }
.post-link { color: var(--text-dim); text-decoration: none; }
.post-link:hover { color: var(--accent); text-decoration: underline; }
</style>
