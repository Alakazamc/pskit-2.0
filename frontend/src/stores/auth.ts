import { defineStore } from "pinia";

import { ApiError, api, type User } from "../lib/api";

export const useAuthStore = defineStore("auth", {
  state: () => ({
    user: null as User | null,
    loaded: false,
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.user),
    isAdmin: (state) => state.user?.role === "admin",
  },
  actions: {
    async loadMe() {
      try {
        this.user = await api.me();
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          this.user = null;
        } else {
          throw error;
        }
      } finally {
        this.loaded = true;
      }
    },
    async login(username: string, password: string) {
      this.user = await api.login(username, password);
      this.loaded = true;
    },
    async register(username: string, password: string) {
      this.user = await api.register(username, password);
      this.loaded = true;
    },
    async logout() {
      try {
        await api.logout();
      } finally {
        this.user = null;
        this.loaded = true;
      }
    },
  },
});
