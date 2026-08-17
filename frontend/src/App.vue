<script setup>
// 앱 셸 — 사이드바 내비게이션 + 게임 선택기 + 라우터 뷰
// 모바일: 사이드바를 오프캔버스 드로어로 (햄버거로 토글, 이동 시 자동 닫힘)
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from './models/api.js'
import { currentGame, games, defaultGame, setGame } from './models/gameState.js'

const menuOpen = ref(false)
const route = useRoute()
watch(() => route.fullPath, () => { menuOpen.value = false })

// 데스크톱 사이드바 접기 (모바일 드로어와 별개) — 새로고침에도 유지
const collapsed = ref(localStorage.getItem('trendsys.sidebarCollapsed') === '1')
function toggleSidebar() {
  collapsed.value = !collapsed.value
  localStorage.setItem('trendsys.sidebarCollapsed', collapsed.value ? '1' : '0')
}

onMounted(async () => {
  try {
    const r = await api.getGames()
    games.value = r.games ?? []
    defaultGame.value = r.default ?? ''
    // 저장돼 있던 게임이 등록 목록에서 사라졌으면 기본 게임으로 복귀
    if (currentGame.value && !games.value.some((g) => g.id === currentGame.value)) {
      setGame('')
    }
  } catch { /* 게임 목록 실패 시 기본 게임으로 동작 */ }
})

function onPick(e) {
  setGame(e.target.value)
}
</script>

<template>
  <div class="layout" :class="{ 'menu-open': menuOpen, collapsed }">
    <!-- 데스크톱 전용: 사이드바 접기/펴기 (모바일에선 숨김 — 상단바 햄버거 사용) -->
    <button class="sidebar-toggle" @click="toggleSidebar"
      :aria-expanded="!collapsed" :title="collapsed ? '메뉴 펼치기' : '메뉴 접기'">
      {{ collapsed ? '☰' : '⟨' }}</button>

    <!-- 모바일 전용 상단바 -->
    <header class="topbar">
      <button class="hamburger" @click="menuOpen = !menuOpen"
        :aria-expanded="menuOpen" aria-label="메뉴 열기/닫기">☰</button>
      <span class="topbar-title">게임 게시판 · 주제 분석</span>
      <select v-if="games.length" class="topbar-game" :value="currentGame"
        @change="onPick" aria-label="게임 선택" :disabled="games.length === 1">
        <option value="">{{ (games.find(g => g.id === defaultGame)?.name ?? '기본') }}</option>
        <option v-for="g in games.filter(g => g.id !== defaultGame)" :key="g.id" :value="g.id">
          {{ g.name }}
        </option>
      </select>
    </header>

    <!-- 드로어 열렸을 때 배경 (탭하면 닫힘) -->
    <div class="sidebar-backdrop" @click="menuOpen = false"></div>

    <aside class="sidebar" @click="menuOpen = false">
      <div class="brand">
        게임 게시판<span class="dot"> ·</span> 주제 분석
        <small>실시간 확산 탐지 파이프라인</small>
      </div>
      <!-- 게임 선택기: 항상 노출 (게임 1개면 현재 게임명 표시, 2개 이상이면 전환 가능) -->
      <div v-if="games.length" class="game-picker" @click.stop>
        <select :value="currentGame" @change="onPick" aria-label="게임 선택"
          :disabled="games.length === 1">
          <option value="">{{ (games.find(g => g.id === defaultGame)?.name ?? '기본 게임') }}</option>
          <option v-for="g in games.filter(g => g.id !== defaultGame)" :key="g.id" :value="g.id">
            {{ g.name }}
          </option>
        </select>
      </div>
      <RouterLink class="nav-link" to="/"><span class="icon">📊</span> 실시간 관제</RouterLink>
      <RouterLink class="nav-link" to="/topics"><span class="icon">📌</span> 주제</RouterLink>
      <RouterLink class="nav-link" to="/categories"><span class="icon">🗂️</span> 카테고리</RouterLink>
      <RouterLink class="nav-link" to="/alerts"><span class="icon">🚨</span> 알림</RouterLink>
      <RouterLink class="nav-link" to="/timemachine"><span class="icon">⏱️</span> 타임머신</RouterLink>
      <RouterLink class="nav-link" to="/judge"><span class="icon">⚖️</span> 판단 모델 테스트</RouterLink>
      <RouterLink class="nav-link" to="/hdbscan"><span class="icon">🧪</span> HDBSCAN 점검</RouterLink>
      <RouterLink class="nav-link" to="/cosine"><span class="icon">🔗</span> 유사도 묶기 실험</RouterLink>
      <RouterLink class="nav-link" to="/gemma"><span class="icon">🤖</span> Gemma 글 분석</RouterLink>
      <div class="sidebar-footer">
        Vue 3 · FastAPI · MLX<br />
        파인튜닝 4B (adapters3)
      </div>
    </aside>
    <main class="main">
      <RouterView />
    </main>
  </div>
</template>
