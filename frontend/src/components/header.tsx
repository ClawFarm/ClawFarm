import Link from "next/link";

export function Header() {
  return (
    <header className="border-b border-border px-6 py-4">
      <Link href="/" className="text-xl font-bold text-primary hover:underline">
        ClawFleetManager
      </Link>
    </header>
  );
}
