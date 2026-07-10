<script setup>
// 카테고리 점수(열기) 추이 스파크라인 — 단일 시리즈 라인 + 호버 툴팁
// 색상 #5c7df2 는 다크 표면(#1a1f2e) 기준 검증 통과값 (lightness/contrast)
import { computed, ref } from 'vue'

const props = defineProps({
  values: { type: Array, default: () => [] }, // 시간순 heat 값
  width: { type: Number, default: 120 },
  height: { type: Number, default: 30 },
})

const PAD = 3
const hoverIdx = ref(null)

const points = computed(() => {
  const v = props.values
  if (v.length < 2) return []
  const max = Math.max(...v, 0.01)
  const min = Math.min(...v, 0)
  const span = Math.max(max - min, 0.01)
  return v.map((y, i) => [
    PAD + (i / (v.length - 1)) * (props.width - PAD * 2),
    props.height - PAD - ((y - min) / span) * (props.height - PAD * 2),
  ])
})

const linePath = computed(() =>
  points.value.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' '),
)
const areaPath = computed(() => {
  if (!points.value.length) return ''
  const first = points.value[0]
  const last = points.value[points.value.length - 1]
  return `${linePath.value} L${last[0].toFixed(1)},${props.height - PAD} L${first[0].toFixed(1)},${props.height - PAD} Z`
})

function onMove(e) {
  if (!points.value.length) return
  const rect = e.currentTarget.getBoundingClientRect()
  const x = ((e.clientX - rect.left) / rect.width) * props.width
  let best = 0
  points.value.forEach(([px], i) => {
    if (Math.abs(px - x) < Math.abs(points.value[best][0] - x)) best = i
  })
  hoverIdx.value = best
}
</script>

<template>
  <div v-if="points.length" class="spark" @mouseleave="hoverIdx = null">
    <svg :width="width" :height="height" :viewBox="`0 0 ${width} ${height}`"
      @mousemove="onMove" role="img" aria-label="점수 추이">
      <path :d="areaPath" fill="#5c7df2" opacity="0.13" />
      <path :d="linePath" fill="none" stroke="#5c7df2" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round" />
      <circle v-if="hoverIdx !== null" :cx="points[hoverIdx][0]" :cy="points[hoverIdx][1]"
        r="3.5" fill="#5c7df2" stroke="var(--surface)" stroke-width="2" />
    </svg>
    <div v-if="hoverIdx !== null" class="spark-tip mono"
      :style="{ left: Math.min(points[hoverIdx][0], width - 34) + 'px' }">
      {{ values[hoverIdx].toFixed(1) }}
    </div>
  </div>
  <div v-else class="faint" style="font-size: 11px;">추이 수집 중…</div>
</template>

<style scoped>
.spark { position: relative; display: inline-block; }
.spark-tip {
  position: absolute;
  top: -16px;
  transform: translateX(-50%);
  background: var(--bg-soft);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 0 5px;
  font-size: 10.5px;
  color: var(--text);
  pointer-events: none;
  white-space: nowrap;
}
</style>
