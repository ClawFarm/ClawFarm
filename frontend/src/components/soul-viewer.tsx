export function SoulViewer({ soul }: { soul: string }) {
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium">SOUL.md</h3>
      <div className="rounded bg-background border border-border p-3 overflow-auto max-h-48">
        <pre className="text-xs whitespace-pre-wrap text-muted-foreground">
          {soul || "(empty)"}
        </pre>
      </div>
    </div>
  );
}
