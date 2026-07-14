<script setup>
// View — 관제 타임머신: 매 사이클 스냅샷을 스크럽 재생 + 시간대 범위 집계.
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { api } from '../models/api.js'
import { currentGame } from '../models/gameState.js'
import BoardView from '../components/BoardView.vue'

// ── 스크럽 재생 ────────────────────────────────────────────────────
const index = ref([])       // [{ts, n_topics, n_alerts, n_bursts}]
const cursor = ref(0)       // index 상의 현재 위치
const board = ref(null)     // 현재 커서 시점의 복원된 스냅샷
const loading = ref(false)
const error = ref(null)
const playing = ref(false)
const speed = ref(4)        // 초당 몇 프레임(스냅샷)씩 진행
let timer = null

// 조회 범위: 기본 최근 24시간. 입력칸은 로컬(KST) 벽시계, 서버 질의는 UTC로 통일.
function isoLocal(d) {
  // datetime-local 값(로컬 타임존) → UTC ISO
  return new Date(d).toISOString()
}
function toLocalInput(d) {
  // Date → datetime-local 값(로컬 벽시계 "YYYY-MM-DDTHH:mm").
  // toISOString()은 UTC라 그대로 쓰면 KST보다 9시간 어긋나므로 오프셋을 보정한다.
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}
const now = new Date()
const from = ref(toLocalInput(new Date(now.getTime() - 24 * 3600 * 1000)))
const to = ref(toLocalInput(now))

async function loadIndex() {
  loading.value = true
  try {
    const r = await api.getSnapshots(isoLocal(from.value), isoLocal(to.value))
    index.value = r.snapshots ?? []
    cursor.value = index.value.length ? index.value.length - 1 : 0
    error.value = null
    if (index.value.length) await loadAt(cursor.value)
    else board.value = null
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function loadAt(i) {
  const snap = index.value[i]
  if (!snap) return
  const data = await api.getSnapshot(snap.ts)
  board.value = data
}

const current = computed(() => index.value[cursor.value] ?? null)
const alertMarks = computed(() =>
  index.value.map((s, i) => ({ i, alert: s.n_alerts > 0, burst: s.n_bursts > 0 })))

function fmt(iso) {
  return iso ? new Date(iso).toLocaleString('ko-KR', {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }) : '—'
}

watch(cursor, (i) => { loadAt(i) })

function step(delta) {
  const next = cursor.value + delta
  if (next < 0 || next >= index.value.length) { stop(); return }
  cursor.value = next
}
function jumpToNextAlert() {
  for (let i = cursor.value + 1; i < index.value.length; i++) {
    if (index.value[i].n_alerts > 0) { cursor.value = i; return }
  }
}
function play() {
  if (!index.value.length) return
  playing.value = true
  timer = setInterval(() => step(1), 1000 / speed.value)
}
function stop() {
  playing.value = false
  if (timer) { clearInterval(timer); timer = null }
}
function togglePlay() { playing.value ? stop() : play() }
watch(speed, () => { if (playing.value) { stop(); play() } })

// ── 시간대 범위 집계 ──────────────────────────────────────────────
const range = ref(null)
const rangeLoading = ref(false)
async function loadRange() {
  rangeLoading.value = true
  try {
    range.value = await api.getHistoryRange(isoLocal(from.value), isoLocal(to.value))
  } catch (e) {
    error.value = e.message
  } finally {
    rangeLoading.value = false
  }
}

// 게임이 바뀌면 그 게임의 스냅샷 타임라인으로 다시 로드 (재생 중이면 정지)
watch(currentGame, () => { stop(); range.value = null; loadIndex() })

onMounted(loadIndex)
onUnmounted(stop)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>⏱️ 타임머신</h1>
        <div class="sub">
          매 분석 사이클의 관제 화면을 그대로 저장해 되돌려봅니다.
          <span v-if="current"> · 재생 중 <b>{{ fmt(current.ts) }}</b></span>
          <span v-else class="faint"> · 이 구간에 저장된 스냅샷이 없습니다</span>
        </div>
      </div>
    </div>

    <div v-if="error" class="error-banner">{{ error }}</div>

    <!-- 범위 선택 + 조회 -->
    <div class="card controls-card">
      <div class="range-row">
        <label>시작 (KST) <input type="datetime-local" v-model="from" /></label>
        <label>끝 (KST) <input type="datetime-local" v-model="to" /></label>
        <button class="btn primary" :disabled="loading" @click="loadIndex">
          {{ loading ? '불러오는 중…' : '이 구간 재생' }}
        </button>
        <button class="btn" :disabled="rangeLoading" @click="loadRange">
          {{ rangeLoading ? '집계 중…' : '이 구간 요약' }}
        </button>
        <span class="faint mono">{{ index.length }}개 스냅샷</span>
      </div>
    </div>

    <!-- 스크럽 타임라인 -->
    <div v-if="index.length" class="card scrubber-card">
      <div class="transport">
        <button class="btn" @click="step(-1)" :disabled="cursor <= 0" title="이전">‹</button>
        <button class="btn primary play" @click="togglePlay">{{ playing ? '⏸' : '▶' }}</button>
        <button class="btn" @click="step(1)" :disabled="cursor >= index.length - 1" title="다음">›</button>
        <button class="btn" @click="jumpToNextAlert" title="다음 알림 지점으로">⏭ 알림</button>
        <select v-model.number="speed" class="speed">
          <option :value="2">2x</option>
          <option :value="4">4x</option>
          <option :value="8">8x</option>
          <option :value="16">16x</option>
        </select>
        <span class="mono ts">{{ fmt(current?.ts) }}</span>
      </div>

      <div class="timeline">
        <input type="range" min="0" :max="index.length - 1" v-model.number="cursor" class="slider" />
        <div class="marks">
          <span v-for="m in alertMarks" :key="m.i" class="mark"
            :class="{ alert: m.alert, burst: m.burst && !m.alert }"
            :style="{ left: (index.length > 1 ? (m.i / (index.length - 1)) * 100 : 0) + '%' }" />
        </div>
      </div>
      <div class="legend faint">
        <span class="dot alert" /> 알림(O) 발생 · <span class="dot burst" /> 버스트
      </div>
    </div>

    <!-- 복원된 보드 (읽기전용) — 위 타임라인 박스와 간격을 둔다.
         당시 주제를 전부 표시(topic-limit 0), 카드 클릭은 "당시 구성" 모드로 -->
    <BoardView v-if="board" class="board-gap" :topics="board.topics"
      :categories="board.results" :stats="board.stats"
      :topic-limit="0" :snapshot-at="board.snapshot_ts ?? board.updated_at ?? ''" />
    <div v-else-if="!loading" class="card empty board-gap">
      구간을 선택하고 "이 구간 재생"을 눌러 과거 관제 화면을 불러오세요.
    </div>

    <!-- 시간대 범위 요약 -->
    <div v-if="range" class="card range-summary">
      <h2>구간 요약 — {{ fmt(range.from) }} ~ {{ fmt(range.to) }}</h2>
      <div class="grid cols-4" style="margin-bottom: 12px;">
        <div class="mini"><div class="k">스냅샷</div><div class="v">{{ range.coverage.snapshots }}</div></div>
        <div class="mini"><div class="k">알림 발생 사이클</div><div class="v danger">{{ range.coverage.alert_cycles }}</div></div>
        <div class="mini"><div class="k">버스트 사이클</div><div class="v warn">{{ range.coverage.burst_cycles }}</div></div>
        <div class="mini"><div class="k">알림 이벤트</div><div class="v">{{ range.alerts.length }}</div></div>
      </div>

      <h3>알림 이벤트</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>시각</th><th>판정</th><th>주제</th><th>규모</th><th>요약</th></tr></thead>
          <tbody>
            <tr v-for="a in range.alerts" :key="a.id">
              <td class="mono muted" style="white-space: nowrap;">{{ fmt(a.created_at) }}</td>
              <td><span class="badge" :class="a.decision === 'O' ? 'danger' : a.decision === '소강' ? 'accent' : 'dim'">{{ a.decision }}</span></td>
              <td>
                <RouterLink v-if="a.topic_id" :to="`/topics/${a.topic_id}`" class="tlink"><b>{{ a.label }}</b></RouterLink>
                <b v-else>{{ a.label }}</b>
              </td>
              <td>{{ a.size }}건</td>
              <td class="muted">{{ a.summary }}</td>
            </tr>
            <tr v-if="!range.alerts.length"><td colspan="5" class="empty">이 구간에 알림 이벤트가 없습니다.</td></tr>
          </tbody>
        </table>
      </div>

      <h3 style="margin-top: 14px;">구간 내 열기 상위 주제</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>주제</th><th>최고 heat</th><th>최대 규모</th><th>버스트</th></tr></thead>
          <tbody>
            <tr v-for="t in range.top_topics" :key="t.topic_id">
              <td>
                <RouterLink v-if="t.topic_id" :to="`/topics/${t.topic_id}`" class="tlink">{{ t.name ?? '#' + t.topic_id }}</RouterLink>
                <span v-else>{{ t.name ?? '#' + t.topic_id }}</span>
              </td>
              <td class="mono">{{ (t.peak_heat ?? 0).toFixed(1) }}</td>
              <td>{{ t.peak_size }}건</td>
              <td><span v-if="t.ever_burst" class="badge warn">🔥</span></td>
            </tr>
            <tr v-if="!range.top_topics.length"><td colspan="4" class="empty">데이터 없음.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.controls-card { margin-top: 8px; }
.range-row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.range-row label { display: flex; flex-direction: column; font-size: 12px; color: var(--text-dim); gap: 4px; }
.scrubber-card { margin-top: 12px; }
/* 복원된 보드(통계 박스 줄)를 위 타임라인 박스와 살짝 띄운다 */
.board-gap { margin-top: 14px; }
.transport { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; flex-wrap: wrap; }
.transport .play { min-width: 44px; font-size: 15px; }
.speed { width: 70px; }
.ts { margin-left: auto; color: var(--text-dim); }
.timeline { position: relative; padding: 4px 0 2px; }
.slider { width: 100%; }
.marks { position: relative; height: 10px; margin-top: 2px; }
.mark { position: absolute; top: 0; width: 2px; height: 10px; background: transparent; transform: translateX(-1px); }
.mark.alert { background: var(--danger); }
.mark.burst { background: var(--warn); }
.legend { margin-top: 6px; font-size: 11.5px; display: flex; gap: 6px; align-items: center; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 2px; }
.dot.alert { background: var(--danger); }
.dot.burst { background: var(--warn); }
.range-summary { margin-top: 14px; }
.range-summary h3 { font-size: 13px; color: var(--text-dim); margin: 6px 0; }
.mini { background: var(--bg-soft); border-radius: 8px; padding: 10px 12px; }
.mini .k { font-size: 11.5px; color: var(--text-dim); margin-bottom: 4px; }
.mini .v { font-size: 22px; font-weight: 700; }
.mini .v.danger { color: var(--danger); }
.mini .v.warn { color: var(--warn); }
.tlink { color: var(--text); text-decoration: none; }
.tlink:hover { color: var(--accent); text-decoration: underline; }
</style>
