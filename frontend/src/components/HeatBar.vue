<script setup>
// 열기 점수(감쇠 가중합) 시각화 바
import { computed } from 'vue'

const props = defineProps({
  heat: { type: Number, default: 0 },
  max: { type: Number, default: 1 },
})
const pct = computed(() => Math.min(100, (props.heat / Math.max(props.max, 0.01)) * 100))
const tone = computed(() => (pct.value > 66 ? 'hot' : pct.value > 33 ? 'mid' : 'low'))
</script>

<template>
  <div class="heat">
    <div class="heat-track">
      <div class="heat-fill" :class="tone" :style="{ width: pct + '%' }" />
    </div>
    <span class="heat-num mono">{{ heat.toFixed(1) }}</span>
  </div>
</template>

<style scoped>
.heat { display: flex; align-items: center; gap: 8px; }
.heat-track {
  flex: 1;
  height: 6px;
  background: var(--bg-soft);
  border-radius: 99px;
  overflow: hidden;
  min-width: 60px;
}
.heat-fill { height: 100%; border-radius: 99px; transition: width 0.4s; }
.heat-fill.low { background: var(--accent); }
.heat-fill.mid { background: var(--warn); }
.heat-fill.hot { background: var(--danger); }
.heat-num { color: var(--text-dim); min-width: 36px; text-align: right; }
</style>
