import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'
import CategoriesView from '../views/CategoriesView.vue'
import CategoryDetailView from '../views/CategoryDetailView.vue'
import TopicsView from '../views/TopicsView.vue'
import TopicDetailView from '../views/TopicDetailView.vue'
import AlertsView from '../views/AlertsView.vue'
import JudgeTestView from '../views/JudgeTestView.vue'
import HdbscanView from '../views/HdbscanView.vue'
import CosineView from '../views/CosineView.vue'
import GemmaView from '../views/GemmaView.vue'
import TimeMachineView from '../views/TimeMachineView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: DashboardView },
    { path: '/categories', name: 'categories', component: CategoriesView },
    { path: '/categories/:id', name: 'category-detail', component: CategoryDetailView },
    { path: '/topics', name: 'topics', component: TopicsView },
    { path: '/topics/:id', name: 'topic-detail', component: TopicDetailView },
    { path: '/clusters', redirect: '/categories' },
    { path: '/alerts', name: 'alerts', component: AlertsView },
    { path: '/judge', name: 'judge', component: JudgeTestView },
    { path: '/hdbscan', name: 'hdbscan', component: HdbscanView },
    { path: '/cosine', name: 'cosine', component: CosineView },
    { path: '/gemma', name: 'gemma', component: GemmaView },
    { path: '/timemachine', name: 'timemachine', component: TimeMachineView },
  ],
})
