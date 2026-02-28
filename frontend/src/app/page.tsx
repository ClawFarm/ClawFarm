"use client";

import { Header } from "@/components/header";
import { CreateBotForm } from "@/components/create-bot-form";
import { BotGrid } from "@/components/bot-grid";
import { useBots } from "@/hooks/use-bots";

export default function Dashboard() {
  const { bots, isLoading, mutate } = useBots();

  return (
    <div className="min-h-screen">
      <Header />
      <CreateBotForm onCreated={mutate} />
      {isLoading ? (
        <div className="text-center text-muted-foreground py-12">Loading...</div>
      ) : (
        <BotGrid bots={bots} onAction={mutate} />
      )}
    </div>
  );
}
