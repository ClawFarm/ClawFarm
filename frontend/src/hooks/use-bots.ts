import useSWR from "swr";
import { api } from "@/lib/api";

export function useBots() {
  const { data, error, isLoading, mutate } = useSWR("bots", () => api.listBots(), {
    refreshInterval: 5000,
  });
  return { bots: data ?? [], error, isLoading, mutate };
}
