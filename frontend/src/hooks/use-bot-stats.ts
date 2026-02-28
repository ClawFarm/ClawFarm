import useSWR from "swr";
import { api } from "@/lib/api";

export function useBotStats(name: string) {
  const { data, error, isLoading } = useSWR(
    name ? `bot-stats-${name}` : null,
    () => api.getStats(name),
    { refreshInterval: 10000 }
  );
  return { stats: data, error, isLoading };
}
