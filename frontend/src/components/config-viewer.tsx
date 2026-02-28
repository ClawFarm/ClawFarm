export function ConfigViewer({ config }: { config: Record<string, unknown> }) {
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium">Config</h3>
      <div className="rounded-sm bg-secondary border border-border p-3 overflow-auto max-h-64">
        <pre className="text-xs whitespace-pre-wrap text-muted-foreground font-mono">
          {JSON.stringify(config, null, 2)}
        </pre>
      </div>
    </div>
  );
}
