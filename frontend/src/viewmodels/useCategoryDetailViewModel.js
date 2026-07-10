// ViewModel — 카테고리 상세 (전체 글 목록, 원문 링크 포함)
import { computed, ref } from 'vue'
import { api } from '../models/api.js'
import { usePolling } from './usePolling.js'

export function useCategoryDetailViewModel(categoryId) {
  const posts = ref([])
  const category = ref(null) // 목록 API에서 이름·점수 등 메타 확보

  const { loading, error, refresh } = usePolling(async () => {
    const [postList, cats] = await Promise.all([
      api.getCategoryPosts(categoryId, 24, 500),
      api.getCategories(),
    ])
    posts.value = postList
    category.value =
      (cats.results ?? []).find((c) => c.category_id === Number(categoryId)) ?? null
  }, 15000)

  const count = computed(() => posts.value.length)

  return { posts, category, count, loading, error, refresh }
}
