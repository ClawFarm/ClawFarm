import useSWR from "swr";
import { api } from "@/lib/api";

export function useBotDetail(name: string) {
  const { data, error, isLoading, mutate } = useSWR(
    name ? `bot-detail-${name}` : null,
    () => api.getDetail(name),
    { refreshInterval: 10000 }
  );
  return { detail: data, error, isLoading, mutate };
}
