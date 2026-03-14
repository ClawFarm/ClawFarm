import type { SparklinePoint } from "@/lib/types";

interface SparklineProps {
  data: SparklinePoint[];
  width?: number;
  height?: number;
  className?: string;
}

export function Sparkline({ data, width = 120, height = 32, className }: SparklineProps) {
  if (data.length < 2) {
    return <div style={{ width, height }} className={className} />;
  }

  const values = data.map((d) => d.total);
  const max = Math.max(...values, 1);
  const padding = 1;
  const innerW = width - padding * 2;
  const innerH = height - padding * 2;

  const points = values
    .map((v, i) => {
      const x = padding + (i / (values.length - 1)) * innerW;
      const y = padding + innerH - (v / max) * innerH;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      aria-label="Token usage sparkline"
    >
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="text-blue-400"
      />
    </svg>
  );
}
