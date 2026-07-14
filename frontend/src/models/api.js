// Model 계층 — 백엔드 API 클라이언트 (MVVM의 M)
// ViewModel(composable)들은 이 모듈을 통해서만 서버와 통신한다.

import { currentGame } from './gameState.js'

const BASE = '/api'

async function request(path, options = {}) {
  // 선택된 게임을 모든 호출에 자동 주입 (미선택 시 서버가 기본 게임 처리)
  const g = currentGame.value
  if (g) path += (path.includes('?') ? '&' : '?') + `game=${encodeURIComponent(g)}`
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`API ${res.status}: ${body || res.statusText}`)
  }
  return res.json()
}

export const api = {
  // 등록된 게임 목록 (게임 선택기)
  getGames: () => request('/games'),
  // 파이프라인 루프 상태 + 슬롯 준비 상태 + 다이얼 설정
  getStatus: () => request('/status'),
  // 최신 분석 결과 (카테고리별 점수·버스트·판정 + 점수 추이)
  getCategories: () => request('/categories'),
  // 특정 카테고리의 최근 글
  getCategoryPosts: (id, hours = 24, limit = 50) =>
    request(`/categories/${id}/posts?hours=${hours}&limit=${limit}`),
  // 주제(미세 이슈) 목록 — 버스트·알림의 단위
  getTopics: () => request('/topics'),
  // 타임머신: 스크럽 인덱스 → {snapshots: [{ts, n_topics, n_alerts, n_bursts}]}
  getSnapshots: (from = '', to = '') =>
    request(`/snapshots?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`),
  // 타임머신: 특정 시각의 대시보드 전체 상태 복원 (at 이하 최신)
  getSnapshot: (at = '') => request(`/snapshot?at=${encodeURIComponent(at)}`),
  // 타임머신: 시간대 범위 집계
  getHistoryRange: (from, to) =>
    request(`/history/range?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`),
  // 주제 글 목록 — 서버측 검색·정렬·페이지네이션 → {total, total_all, page, per_page, posts}
  // hours 미지정 = 서버 기본(현재 분석 창), hours=0 = 전체 이력
  getTopicPosts: (id, { page = 1, perPage = 50, q = '', sort = 'created_at', order = 'desc', hours = null } = {}) =>
    request(`/topics/${id}/posts?page=${page}&per_page=${perPage}&q=${encodeURIComponent(q)}&sort=${sort}&order=${order}`
            + (hours === null ? '' : `&hours=${hours}`)),
  // 타임머신: 스냅샷 시점의 주제 멤버 글 복원 → {snapshot_ts, name, has_members, posts}
  getTopicPostsAt: (id, at) =>
    request(`/topics/${id}/posts-at?at=${encodeURIComponent(at)}`),
  // HDBSCAN 안전망: 카테고리 중복/누락 점검
  reclusterCheck: () => request('/maintenance/recluster-check', { method: 'POST' }),
  // 순수 코사인 유사도 묶기 (탐색용)
  cosineCluster: (threshold, hours = 24) =>
    request(`/maintenance/cosine-cluster?threshold=${threshold}&hours=${hours}`),
  // 글 단위 분석(adapters4) 결과 + 큐 상태
  getGemmaAnalyses: (hours = 24, limit = 200) =>
    request(`/gemma/analyses?hours=${hours}&limit=${limit}`),
  // 알림 판정(O) 묶음 + 발송 이력
  getAlerts: () => request('/alerts'),
  // 최근 수집 글
  getRecentPosts: (hours = 1, limit = 30) =>
    request(`/posts/recent?hours=${hours}&limit=${limit}`),
  // 알림 피드백 (좋아요 O / 나빠요 X / null 취소) — 파인튜닝 데이터 축적
  setAlertFeedback: (alertId, feedback) =>
    request(`/alerts/${alertId}/feedback`, {
      method: 'POST',
      body: JSON.stringify({ feedback }),
    }),
  // 파이프라인 1회 수동 실행
  triggerRun: () => request('/run', { method: 'POST' }),
  // 파인튜닝 4B 판단 모델 단독 테스트
  judgeTest: (text) =>
    request('/judge/test', { method: 'POST', body: JSON.stringify({ text }) }),
}
