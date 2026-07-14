// 전역 게임 선택 상태 — 모든 API 호출에 자동 주입된다 (api.js request 참조).
// ''(빈 값)이면 서버가 기본 게임(default_game_id)으로 처리한다.
import { ref } from 'vue'

const KEY = 'trendsys.game'

export const currentGame = ref(localStorage.getItem(KEY) || '')
export const games = ref([])          // [{id, name}] — App 마운트 시 /api/games 로 채움
export const defaultGame = ref('')

export function setGame(id) {
  currentGame.value = id || ''
  try { localStorage.setItem(KEY, currentGame.value) } catch { /* 사파리 프라이빗 등 */ }
}
