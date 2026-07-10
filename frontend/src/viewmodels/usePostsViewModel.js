// ViewModel — 최근 수집 글 피드
import { ref } from 'vue'
import { api } from '../models/api.js'
import { usePolling } from './usePolling.js'

export function usePostsViewModel(hours = 1, limit = 30) {
  const posts = ref([])

  const { loading, error, refresh } = usePolling(async () => {
    posts.value = await api.getRecentPosts(hours, limit)
  }, 15000)

  return { posts, loading, error, refresh }
}
