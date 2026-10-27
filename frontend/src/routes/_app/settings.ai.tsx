import { createFileRoute } from "@tanstack/react-router";
import { AiSettingsPanel } from "@/components/app/AiSettingsPanel";
import { PageHeader } from "@/components/app/bits";

export const Route = createFileRoute("/_app/settings/ai")({
  head: () => ({
    meta: [
      { title: "AI settings — RunGuard" },
      { name: "description", content: "Choose the provider and model for future URL summaries." },
    ],
  }),
  component: AiSettingsPage,
});

function AiSettingsPage() {
  return (
    <>
      <PageHeader title="AI settings" sub="Choose how URL Summarizer requests a model" />
      <AiSettingsPanel />
    </>
  );
}
