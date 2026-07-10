<script setup>
// 파이프라인 단계 슬롯 상태 표시 — 준비됨 / 슬롯(미준비)
defineProps({ slot_: Object })
</script>

<template>
  <div class="slot-row">
    <div class="slot-info">
      <div class="slot-label">{{ slot_.label }}</div>
      <div class="slot-name" :title="slot_.name">{{ slot_.name }}</div>
    </div>
    <span v-if="slot_.ready" class="badge ok">● 준비됨</span>
    <span v-else class="badge warn">◌ 슬롯 (미준비)</span>
  </div>
</template>

<style scoped>
.slot-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
}
.slot-row:last-child { border-bottom: none; }
.slot-info { min-width: 0; }
.slot-label { font-weight: 600; font-size: 13px; }
.slot-name {
  color: var(--text-faint);
  font-size: 11.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 320px;
}
</style>
