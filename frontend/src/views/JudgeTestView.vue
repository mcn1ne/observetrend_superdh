<script setup>
// View — 판단 모델(파인튜닝 4B, adapters3) 단독 테스트
import { useStatusViewModel } from '../viewmodels/useStatusViewModel.js'
import { useJudgeTestViewModel } from '../viewmodels/useJudgeTestViewModel.js'

const statusVm = useStatusViewModel()
const vm = useJudgeTestViewModel()
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>판단 모델 테스트</h1>
        <div class="sub">
          요약문을 넣어 알림 O/X 판정을 직접 확인합니다 (오탐/미탐 점검) ·
          모델: <span class="mono">{{ statusVm.status.value?.slots?.judge?.name ?? '…' }}</span>
        </div>
      </div>
    </div>

    <div class="grid cols-2" style="align-items: start;">
      <div class="card">
        <h2>입력 (adapters3 학습 형식)</h2>
        <label class="field">
          <span>주제 라벨</span>
          <input v-model="vm.label.value" type="text" placeholder="예: [운영] 결제 오류 대량 발생" />
        </label>
        <label class="field">
          <span>대표 제목</span>
          <input v-model="vm.title.value" type="text" placeholder="예: 결제했는데 다이아 안 들어옴" />
        </label>
        <label class="field">
          <span>요약</span>
          <textarea v-model="vm.summary.value" rows="4"
            placeholder="예: 최근 1시간 동안 결제 후 재화 미지급 제보가 20건 이상 몰림. 환불 요구가 늘고 있음." />
        </label>

        <div class="faint" style="margin-bottom: 12px;">
          모델 입력: <span class="mono">{{ vm.judgeInput.value }}</span>
        </div>

        <button class="btn primary" :disabled="!vm.canSubmit.value" @click="vm.submit">
          {{ vm.running.value ? '판정 중… (최초 실행 시 모델 로딩)' : '판정 요청' }}
        </button>
        <div v-if="vm.error.value" class="error-banner" style="margin-top: 12px;">{{ vm.error.value }}</div>
      </div>

      <div class="card">
        <h2>판정 결과</h2>
        <div v-if="vm.results.value.length" style="display: flex; flex-direction: column; gap: 10px;">
          <div v-for="(r, i) in vm.results.value" :key="i" class="result-row">
            <span class="badge" :class="r.decision === 'O' ? 'warn' : 'dim'" style="font-size: 14px;">
              {{ r.decision }} · {{ r.meaning }}
            </span>
            <div class="muted" style="font-size: 12.5px; margin-top: 6px;">{{ r.input }}</div>
            <div class="faint">{{ r.at }}</div>
          </div>
        </div>
        <div v-else class="empty">아직 테스트 이력이 없습니다.</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.result-row {
  background: var(--bg-soft);
  border: 1px solid var(--border);
  border-radius: 9px;
  padding: 12px 14px;
}
</style>
