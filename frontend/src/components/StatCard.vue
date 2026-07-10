<script setup>
defineProps({
  label: String,
  value: [String, Number],
  hint: String,
  tone: { type: String, default: '' }, // '', 'accent', 'warn', 'danger'
})
</script>

<template>
  <div class="stat-card card">
    <div class="stat-label">{{ label }}</div>
    <div class="stat-value" :class="tone">{{ value }}</div>
    <div v-if="hint" class="faint">{{ hint }}</div>
  </div>
</template>

<style scoped>
.stat-card { padding: 16px 18px; }
.stat-label { font-size: 12px; color: var(--text-dim); font-weight: 600; margin-bottom: 6px; }
.stat-value { font-size: 26px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.2; margin-bottom: 2px; }
.stat-value.accent { color: var(--accent); }
.stat-value.warn { color: var(--warn); }
.stat-value.danger { color: var(--danger); }
</style>
