import useSWR from "swr";
import { api } from "@/lib/api";

export function useFleetStats() {
  const { data, isLoading } = useSWR("fleet-stats", () => api.getFleetStats(), {
    refreshInterval: 10000,
  });
  return { stats: data ?? null, isLoading };
}
