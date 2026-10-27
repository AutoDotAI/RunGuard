import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api/client";
import type { AiProvider, AiSettingsInput } from "@/lib/api/types";

type SettingsClient = Pick<typeof api, "getAiProviders" | "getAiSettings" | "updateAiSettings">;

function providerKey(provider: AiProvider): string {
  return `${provider.provider_path}:${provider.provider}`;
}

export function AiSettingsPanel({ client = api }: { client?: SettingsClient }) {
  const queryClient = useQueryClient();
  const catalog = useQuery({
    queryKey: ["ai-providers"],
    queryFn: () => client.getAiProviders(),
    retry: false,
  });
  const settings = useQuery({
    queryKey: ["ai-settings"],
    queryFn: () => client.getAiSettings(),
    retry: false,
  });
  const [choice, setChoice] = useState<AiSettingsInput | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings.data) {
      setChoice({
        provider_path: settings.data.provider_path,
        provider: settings.data.provider,
        model: settings.data.model,
      });
    }
  }, [settings.data]);

  const save = useMutation({
    mutationFn: (input: AiSettingsInput) => client.updateAiSettings(input),
    onSuccess: (value) => {
      queryClient.setQueryData(["ai-settings"], value);
      setSaved(true);
    },
  });

  if (catalog.isPending || settings.isPending)
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading AI settings…
      </p>
    );
  if (catalog.error || settings.error)
    return (
      <div
        role="alert"
        className="rounded-md border border-destructive/30 bg-danger-soft p-4 text-sm text-destructive"
      >
        Could not load AI settings. {catalog.error?.message ?? settings.error?.message}
        <Button
          type="button"
          variant="outline"
          className="ml-3"
          onClick={() => {
            void catalog.refetch();
            void settings.refetch();
          }}
        >
          Try again
        </Button>
      </div>
    );

  const providers = catalog.data?.providers ?? [];
  const selected = providers.find(
    (provider) =>
      choice &&
      provider.provider_path === choice.provider_path &&
      provider.provider === choice.provider,
  );
  const modelAllowed = selected?.models.some((model) => model.id === choice?.model) ?? false;
  const valid = Boolean(choice && selected?.available && modelAllowed);
  const unavailableChoice = choice && (!selected || !selected.available);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setSaved(false);
    if (valid && choice) save.mutate(choice);
  };

  return (
    <form onSubmit={submit} className="max-w-2xl space-y-6">
      <p className="text-sm text-muted-foreground">
        Choose how future URL summaries are created. The local demo copies a short page excerpt and
        uses no AI model. Codex and Claude use a host worker; LiteLLM uses a server configured API key.
        Credentials stay on the server or host.
      </p>
      {providers.length === 0 && (
        <p role="status" className="rounded-md border p-4 text-sm">
          No summary provider is available. Start the local worker, a host worker, or configure a
          server-side API provider.
        </p>
      )}
      <fieldset className="space-y-3">
        <legend className="text-sm font-medium">Provider path</legend>
        {providers.map((provider) => {
          const active =
            choice?.provider_path === provider.provider_path &&
            choice.provider === provider.provider;
          return (
            <label
              key={providerKey(provider)}
              className={`block rounded-md border bg-card p-4 ${provider.available ? "cursor-pointer" : "opacity-70"} ${active ? "border-signal ring-2 ring-signal/30" : ""}`}
            >
              <span className="flex items-center gap-2 font-medium">
                <input
                  type="radio"
                  name="ai-provider"
                  value={providerKey(provider)}
                  checked={active}
                  disabled={!provider.available || save.isPending}
                  onChange={() => {
                    setChoice({
                      provider_path: provider.provider_path,
                      provider: provider.provider,
                      model: provider.models[0]?.id ?? "",
                    });
                    setSaved(false);
                    save.reset();
                  }}
                />
                {provider.label}
              </span>
              <span className="mt-1 block pl-6 text-xs text-muted-foreground">
                {provider.provider_path === "local_demo"
                  ? "Deterministic page excerpt · no AI model or API key"
                  : provider.provider_path === "backend_cli"
                    ? "Backend worker · host CLI login"
                    : "API · server-side LiteLLM key"}
              </span>
              {!provider.available && (
                <span className="mt-1 block pl-6 text-xs text-destructive">
                  Unavailable: {provider.unavailable_reason ?? "Provider is not configured."}
                </span>
              )}
            </label>
          );
        })}
      </fieldset>
      {unavailableChoice && (
        <p role="alert" className="text-sm text-destructive">
          Your saved provider is unavailable. Select an available provider to continue.
        </p>
      )}
      {selected && selected.available && (
        <div className="space-y-1.5">
          <Label htmlFor="ai-model">{selected.provider_path === "local_demo" ? "Method" : "Model"}</Label>
          <select
            id="ai-model"
            value={choice?.model ?? ""}
            disabled={save.isPending}
            onChange={(event) => {
              setChoice({
                provider_path: selected.provider_path,
                provider: selected.provider,
                model: event.target.value,
              });
              setSaved(false);
              save.reset();
            }}
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm"
          >
            {!modelAllowed && <option value="">Choose a supported model</option>}
            {selected.models.map((model) => (
              <option key={model.id} value={model.id}>
                {model.label}
              </option>
            ))}
          </select>
          {!modelAllowed && (
            <p role="alert" className="text-sm text-destructive">
              Choose a supported model before saving.
            </p>
          )}
        </div>
      )}
      {save.error && (
        <p role="alert" className="text-sm text-destructive">
          Could not save AI settings. {save.error.message}
        </p>
      )}
      {saved && (
        <p role="status" className="text-sm text-success">
          AI settings saved. Future URL Summarizer runs will use this choice.
        </p>
      )}
      <Button type="submit" disabled={!valid || save.isPending}>
        {save.isPending ? "Saving…" : "Save AI settings"}
      </Button>
    </form>
  );
}
