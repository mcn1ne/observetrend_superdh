<script setup>
// View — 순수 코사인 유사도 묶기: 임계값을 0.01 단위로 조절하며
// "유사도 ≥ 임계값이면 같은 묶음" 결과를 실시간 탐색
import { onMounted } from 'vue'
import { useCosineViewModel } from '../viewmodels/useCosineViewModel.js'

const vm = useCosineViewModel()
onMounted(vm.run)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>유사도 묶기 실험</h1>
        <div class="sub">
          최근 24시간 글을 카테고리·HDBSCAN 없이 순수 코사인 유사도로만 묶습니다.
          "두 글의 유사도 ≥ 임계값 → 같은 묶음" (연결 요소 방식).
        </div>
      </div>
    </div>

    <div class="card slider-card">
      <div class="slider-row">
        <span class="faint">느슨하게 0.50</span>
        <input type="range" min="0.50" max="0.95" step="0.01" v-model.number="vm.threshold.value" />
        <span class="faint">0.95 엄격하게</span>
        <span class="threshold mono">{{ vm.threshold.value.toFixed(2) }}</span>
        <span v-if="vm.running.value" class="badge accent">계산 중…</span>
      </div>
      <div class="faint" style="margin-top: 6px;">
        참고 (KURE-v1 기준): 최근접 유사도 중앙값 ~0.69 — 0.65 아래는 과병합,
        0.80 위는 과분리되는 경향입니다. 슬라이더를 멈추면 자동 계산됩니다.
      </div>
    </div>

    <div v-if="vm.error.value" class="error-banner" style="margin-top: 14px;">{{ vm.error.value }}</div>

    <template v-if="vm.result.value">
      <div class="grid cols-4" style="margin-top: 14px;">
        <div class="card stat"><div class="k">대상 글</div><div class="v">{{ vm.result.value.checked }}</div></div>
        <div class="card stat"><div class="k">묶음 (2건↑)</div><div class="v accent">{{ vm.result.value.n_groups }}</div></div>
        <div class="card stat"><div class="k">단독 글</div><div class="v">{{ vm.result.value.singles }}</div>
          <div class="faint">어떤 글과도 임계값 이상 유사하지 않음</div></div>
        <div class="card stat"><div class="k">임계값</div><div class="v mono">{{ vm.result.value.threshold.toFixed(2) }}</div></div>
      </div>

      <div class="card" style="margin-top: 14px;">
        <h2>묶음별 상세 (크기순, 상위 60개)</h2>
        <div class="group-list">
          <div v-for="(g, gi) in vm.result.value.groups" :key="gi" class="group-row">
            <div class="row-head">
              <b>{{ g.size }}건</b>
              <span class="comp">
                <span v-for="(n, name) in g.categories" :key="name" class="badge dim">{{ name }} {{ n }}</span>
              </span>
            </div>
            <ul class="samples">
              <li v-for="(p, i) in g.samples" :key="i">
                <a v-if="p.url" :href="p.url" target="_blank" rel="noopener" class="post-link">{{ p.title }} ↗</a>
                <span v-else>{{ p.title }}</span>
              </li>
            </ul>
          </div>
          <div v-if="!vm.result.value.groups.length" class="empty">
            이 임계값에서는 2건 이상 묶인 그룹이 없습니다. 임계값을 낮춰보세요.
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.slider-card { position: sticky; top: 12px; z-index: 5; }
.slider-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.slider-row input[type="range"] { flex: 1; min-width: 200px; accent-color: var(--accent); }
.threshold { font-size: 22px; font-weight: 700; min-width: 64px; text-align: center; }
.stat .k { font-size: 12px; color: var(--text-dim); font-weight: 600; margin-bottom: 6px; }
.stat .v { font-size: 26px; font-weight: 700; }
.stat .v.accent { color: var(--accent); }
.group-list { display: flex; flex-direction: column; gap: 14px; }
.group-row { border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; background: var(--bg-soft); }
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
