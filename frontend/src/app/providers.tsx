"use client";

import { SWRConfig } from "swr";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SWRConfig
      value={{
        onErrorRetry: (error, _key, _config, revalidate, { retryCount }) => {
          // Don't retry on 4xx (auth errors, not found, validation)
          if (error?.status >= 400 && error?.status < 500) return;
          // Cap retries at 3 (covers the ~2-3s Caddy sync window)
          if (retryCount >= 3) return;
          // Retry after 2s
          setTimeout(() => revalidate({ retryCount }), 2000);
        },
      }}
    >
      {children}
    </SWRConfig>
  );
}
