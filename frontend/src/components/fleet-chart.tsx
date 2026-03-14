"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { formatTokens } from "@/lib/format";
import type { FleetTokenChartPoint } from "@/lib/types";

const MODEL_COLORS = [
  "#3b82f6", // blue
  "#10b981", // emerald
  "#f59e0b", // amber
  "#ef4444", // red
  "#8b5cf6", // violet
  "#06b6d4", // cyan
  "#f97316", // orange
  "#ec4899", // pink
];

function shortenModel(name: string): string {
  if (name.length <= 20) return name;
  return name.slice(0, 19) + "\u2026";
}

interface TooltipData {
  x: number;
  y: number;
  ts: string;
  models: [string, number][];
  total: number;
}

export function FleetChart() {
  const { data, isLoading } = useSWR("fleet-token-chart", () => api.getFleetTokenChart(), {
    refreshInterval: 60000,
  });
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(0);

  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setContainerWidth(entry.contentRect.width);
      }
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  const { bars, allModels, maxTotal, dayLabels } = useMemo(() => {
    if (!data || data.length === 0) {
      return { bars: [] as FleetTokenChartPoint[], allModels: [] as string[], maxTotal: 0, dayLabels: [] as { index: number; label: string }[] };
    }

    const modelSet = new Set<string>();
    for (const point of data) {
      for (const model of Object.keys(point.models)) {
        modelSet.add(model);
      }
    }
    const allModels = [...modelSet];

    let maxTotal = 0;
    for (const point of data) {
      const total = Object.values(point.models).reduce((a, b) => a + b, 0);
      if (total > maxTotal) maxTotal = total;
    }

    // Day boundaries
    const dayLabels: { index: number; label: string }[] = [];
    let lastDay = "";
    for (let i = 0; i < data.length; i++) {
      const day = data[i].ts.slice(0, 10);
      if (day !== lastDay) {
        const d = new Date(data[i].ts);
        dayLabels.push({
          index: i,
          label: d.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
        });
        lastDay = day;
      }
    }

    return { bars: data, allModels, maxTotal, dayLabels };
  }, [data]);

  if (isLoading) {
    return <div className="w-full h-28 rounded-lg border border-border bg-card animate-pulse" />;
  }

  if (bars.length === 0) {
    return (
      <div className="w-full rounded-lg border border-border bg-card px-4 py-3">
        <p className="text-xs text-muted-foreground">Token usage chart will appear after data collection begins.</p>
      </div>
    );
  }

  const chartH = 100;
  const barWidth = Math.max(2, containerWidth > 0 ? Math.floor((containerWidth - 16) / bars.length) - 1 : 4);
  const gap = 1;
  const totalW = bars.length * (barWidth + gap);

  return (
    <div className="w-full rounded-lg border border-border bg-card px-2 pt-3 pb-2 space-y-1.5 relative" ref={containerRef}>
      {/* Chart area */}
      <div className="overflow-hidden">
        <svg
          width="100%"
          height={chartH}
          viewBox={`0 0 ${totalW} ${chartH}`}
          preserveAspectRatio="xMinYMin meet"
          onMouseLeave={() => setTooltip(null)}
        >
          {bars.map((point, i) => {
            const x = i * (barWidth + gap);
            let yOffset = chartH;

            return (
              <g
                key={point.ts}
                onMouseEnter={(e) => {
                  const rect = (e.currentTarget.closest("svg") as SVGSVGElement).getBoundingClientRect();
                  const entries = Object.entries(point.models).sort(([, a], [, b]) => b - a);
                  const total = entries.reduce((sum, [, v]) => sum + v, 0);
                  setTooltip({
                    x: e.clientX - rect.left,
                    y: e.clientY - rect.top,
                    ts: point.ts,
                    models: entries,
                    total,
                  });
                }}
              >
                <rect x={x} y={0} width={barWidth + gap} height={chartH} fill="transparent" />
                {allModels.map((model, mi) => {
                  const value = point.models[model] || 0;
                  if (value === 0) return null;
                  const barH = maxTotal > 0 ? (value / maxTotal) * chartH : 0;
                  yOffset -= barH;
                  return (
                    <rect
                      key={model}
                      x={x}
                      y={yOffset}
                      width={barWidth}
                      height={Math.max(barH, 0.5)}
                      fill={MODEL_COLORS[mi % MODEL_COLORS.length]}
                      rx={barWidth > 3 ? 1 : 0}
                      className="opacity-80"
                    />
                  );
                })}
              </g>
            );
          })}
        </svg>
      </div>

      {/* X-axis day labels (HTML, not SVG) */}
      <div className="relative h-4 text-[10px] text-muted-foreground tabular-nums">
        {dayLabels.map(({ index, label }) => (
          <span
            key={index}
            className="absolute whitespace-nowrap"
            style={{ left: `${(index / bars.length) * 100}%` }}
          >
            {label}
          </span>
        ))}
      </div>

      {/* Legend (HTML) */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 px-1">
        {allModels.map((model, i) => (
          <div key={model} className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span
              className="w-2.5 h-2.5 rounded-sm shrink-0"
              style={{ backgroundColor: MODEL_COLORS[i % MODEL_COLORS.length] }}
            />
            {shortenModel(model)}
          </div>
        ))}
      </div>

      {/* Tooltip */}
      {tooltip && (
        <div
          className="absolute bg-popover border border-border rounded-md px-2.5 py-1.5 text-xs shadow-lg pointer-events-none z-10"
          style={{
            left: Math.min(tooltip.x + 12, (containerWidth || 400) - 180),
            top: Math.max(tooltip.y - 20, 8),
          }}
        >
          <div className="text-muted-foreground mb-1">
            {new Date(tooltip.ts).toLocaleString(undefined, {
              month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
            })}
          </div>
          {tooltip.models.map(([model, count]) => (
            <div key={model} className="flex items-center gap-1.5">
              <span
                className="w-2 h-2 rounded-sm shrink-0"
                style={{ backgroundColor: MODEL_COLORS[allModels.indexOf(model) % MODEL_COLORS.length] }}
              />
              <span className="text-muted-foreground truncate max-w-[120px]">{shortenModel(model)}</span>
              <span className="font-medium tabular-nums ml-auto pl-2">{formatTokens(count)}</span>
            </div>
          ))}
          {tooltip.models.length > 1 && (
            <div className="border-t border-border mt-1 pt-1 font-medium tabular-nums">
              {formatTokens(tooltip.total)} total
            </div>
          )}
        </div>
      )}
    </div>
  );
}
