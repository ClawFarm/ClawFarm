import useSWR from "swr";
import { api } from "@/lib/api";
import type { User } from "@/lib/types";

export function useAuth() {
  const { data, error, isLoading, mutate } = useSWR<User>("auth-me", () => api.getMe(), {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  });

  const logout = async () => {
    await api.logout();
    mutate(undefined, { revalidate: false });
    window.location.href = "/login";
  };

  return {
    user: data ?? null,
    isAuthenticated: !!data,
    isLoading,
    error,
    logout,
    mutate,
  };
}
