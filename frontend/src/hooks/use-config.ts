import useSWR from "swr";
import { api } from "@/lib/api";

export function useConfig() {
  const { data } = useSWR("config", () => api.getConfig(), {
    revalidateOnFocus: false,
  });
  return data ?? null;
}
