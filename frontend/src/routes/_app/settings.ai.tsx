import { createFileRoute } from "@tanstack/react-router";
import { AiSettingsPanel } from "@/components/app/AiSettingsPanel";
import { PageHeader } from "@/components/app/bits";

export const Route = createFileRoute("/_app/settings/ai")({
  head: () => ({
    meta: [
      { title: "AI settings — RunGuard" },
      { name: "description", content: "Choose a local demo or model provider for future URL summaries." },
    ],
  }),
  component: AiSettingsPage,
});

function AiSettingsPage() {
  return (
    <>
      <PageHeader title="Summary settings" sub="Choose a local demo or an AI model for URL Summarizer" />
      <AiSettingsPanel />
    </>
  );
}
