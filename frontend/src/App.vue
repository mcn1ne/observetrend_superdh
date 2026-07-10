<script setup>
// 앱 셸 — 사이드바 내비게이션 + 라우터 뷰
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">
        게임 게시판<span class="dot"> ·</span> 주제 분석
        <small>실시간 확산 탐지 파이프라인</small>
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
