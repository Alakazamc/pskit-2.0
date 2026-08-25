import { createRouter, createWebHistory, type RouteLocationNormalized } from "vue-router";

import { useAuthStore } from "./stores/auth";
import AboutView from "./views/AboutView.vue";
import AgentView from "./views/AgentView.vue";
import DoctorView from "./views/DoctorView.vue";
import HomeView from "./views/HomeView.vue";
import LoginView from "./views/LoginView.vue";
import RegisterView from "./views/RegisterView.vue";
import TasksView from "./views/TasksView.vue";
import ToolsView from "./views/ToolsView.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: HomeView, meta: { public: true } },
    { path: "/about", name: "about", component: AboutView, meta: { public: true } },
    { path: "/login", name: "login", component: LoginView, meta: { public: true, guest: true } },
    { path: "/register", name: "register", component: RegisterView, meta: { public: true, guest: true } },
    { path: "/agent", name: "agent", component: AgentView, meta: { requiresAuth: true } },
    { path: "/tasks", name: "tasks", component: TasksView, meta: { requiresAuth: true } },
    { path: "/tools", name: "tools", component: ToolsView, meta: { requiresAuth: true } },
    { path: "/admin/doctor", name: "doctor", component: DoctorView, meta: { requiresAuth: true, admin: true } },
  ],
});

function redirectTarget(to: RouteLocationNormalized) {
  return { name: "login", query: { redirect: to.fullPath } };
}

router.beforeEach(async (to) => {
  const auth = useAuthStore();
  if (!auth.loaded) {
    await auth.loadMe();
  }
  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return redirectTarget(to);
  }
  if (to.meta.admin && !auth.isAdmin) {
    return { name: "agent" };
  }
  if (to.meta.guest && auth.isAuthenticated) {
    return { name: "agent" };
  }
  return true;
});

export default router;
