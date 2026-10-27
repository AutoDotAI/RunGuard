import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api/client";
import { auth } from "@/lib/auth";
import type { AiCredentialProvider, AiProvider, AiSettingsInput } from "@/lib/api/types";

type SettingsClient = Pick<typeof api,
  "getAiProviders" | "getAiSettings" | "updateAiSettings" |
  "getAiCredentials" | "upsertAiCredential" | "deleteAiCredential">;
const DEMO_POLL_INTERVAL_MS = 1_000;
const DEMO_MAX_POLLS = 30;

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
  const credentials = useQuery({
    queryKey: ["ai-credentials"],
    queryFn: () => client.getAiCredentials(),
    retry: false,
  });
  const [choice, setChoice] = useState<AiSettingsInput | null>(null);
  const [saved, setSaved] = useState(false);
  const [demoPolls, setDemoPolls] = useState(0);
  const [keyProvider, setKeyProvider] = useState<AiCredentialProvider>("openai");
  const [keyInput, setKeyInput] = useState("");
  const [keyBusy, setKeyBusy] = useState(false);
  const [keyError, setKeyError] = useState<string | null>(null);
  const [keyNotice, setKeyNotice] = useState<string | null>(null);
  const keyValid = keyInput.length >= 8 && keyInput.length <= 512 && keyInput.trim() === keyInput;

  const demoUnavailable = settings.data?.provider_path === "local_demo" &&
    (!choice || choice.provider_path === "local_demo") &&
    (!settings.data.available || !catalog.data?.providers.some((provider) =>
      provider.provider_path === "local_demo" && provider.provider === "extractive" && provider.available));

  useEffect(() => {
    if (!demoUnavailable || catalog.error || settings.error || demoPolls >= DEMO_MAX_POLLS) return;
    const timer = window.setTimeout(() => {
      void Promise.allSettled([catalog.refetch(), settings.refetch()]).then(() => {
        setDemoPolls((count) => count + 1);
      });
    }, DEMO_POLL_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [catalog.error, catalog.refetch, demoUnavailable, demoPolls, settings.error, settings.refetch]);

  useEffect(() => {
    if (settings.data) {
      setChoice((current) => current ?? {
        provider_path: settings.data.provider_path,
        provider: settings.data.provider,
        model: settings.data.model,
      });
    }
  }, [settings.data]);

  useEffect(() => {
    if (choice || settings.data?.provider_path !== "api") return;
    const provider = settings.data.model.split("/", 1)[0];
    if (provider === "openai" || provider === "anthropic") setKeyProvider(provider);
  }, [choice, settings.data]);

  useEffect(() => auth.subscribe(() => {
    if (!auth.getSession()) setKeyInput("");
  }), []);

  const save = useMutation({
    mutationFn: (input: AiSettingsInput) => client.updateAiSettings(input),
    onSuccess: (value) => {
      queryClient.setQueryData(["ai-settings"], value);
      setSaved(true);
    },
  });

  const saveKey = async () => {
    if (!keyValid || keyBusy) return;
    setKeyBusy(true);
    setKeyError(null);
    setKeyNotice(null);
    try {
      await client.upsertAiCredential(keyProvider, { api_key: keyInput });
      setKeyInput("");
      setKeyNotice(`${keyProvider === "openai" ? "OpenAI" : "Anthropic"} key saved. Choose a model and save AI settings.`);
      await Promise.allSettled([credentials.refetch(), catalog.refetch(), settings.refetch()]);
    } catch {
      setKeyError("Could not save the API key. Check the key and try again.");
    } finally {
      setKeyBusy(false);
    }
  };

  const deleteKey = async () => {
    if (keyBusy) return;
    setKeyBusy(true);
    setKeyError(null);
    setKeyNotice(null);
    try {
      await client.deleteAiCredential(keyProvider);
      setKeyInput("");
      setKeyNotice(`${keyProvider === "openai" ? "OpenAI" : "Anthropic"} key deleted.`);
      await Promise.allSettled([credentials.refetch(), catalog.refetch(), settings.refetch()]);
    } catch {
      setKeyError("Could not delete the API key. Try again.");
    } finally {
      setKeyBusy(false);
    }
  };

  if (catalog.isPending || settings.isPending || credentials.isPending)
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
  const keyConfigured = credentials.data?.credentials.find((entry) => entry.provider === keyProvider)?.configured ?? false;
  const apiModels = selected?.provider_path === "api"
    ? selected.models.filter((model) => model.id.startsWith(`${keyProvider}/`)) : [];
  const apiModelAllowed = selected?.provider_path !== "api" ||
    (Boolean(choice?.model?.startsWith(`${keyProvider}/`)) && keyConfigured);
  const valid = Boolean(choice && selected?.available && modelAllowed && apiModelAllowed);
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
        uses no AI model. Codex and Claude use a signed-in host worker. For the API option,
        add your own OpenAI or Anthropic key below; RunGuard stores it on the server.
      </p>
      <Button type="button" variant="outline"
        disabled={catalog.isFetching || settings.isFetching}
        onClick={() => {
          setSaved(false);
          void Promise.all([catalog.refetch(), settings.refetch()]);
        }}>
        {catalog.isFetching || settings.isFetching ? "Refreshing providers…" : "Refresh providers"}
      </Button>
      {demoUnavailable && demoPolls < DEMO_MAX_POLLS && (
        <p role="status" className="rounded-md border p-4 text-sm">
          Waiting for the local demo worker to start. Availability refreshes automatically.
        </p>
      )}
      {demoUnavailable && demoPolls >= DEMO_MAX_POLLS && (
        <p role="alert" className="rounded-md border p-4 text-sm">
          The local demo worker has not connected. Check the worker service, then{" "}
          <Button type="button" variant="outline" className="ml-2" onClick={() => {
            setDemoPolls(0);
            void catalog.refetch();
            void settings.refetch();
          }}>Try again</Button>
        </p>
      )}
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
              className={`block cursor-pointer rounded-md border bg-card p-4 ${active ? "border-signal ring-2 ring-signal/30" : ""}`}
            >
              <span className="flex items-center gap-2 font-medium">
                <input
                  type="radio"
                  name="ai-provider"
                  value={providerKey(provider)}
                  checked={active}
                  disabled={save.isPending}
                  onChange={() => {
                    if (provider.provider_path !== "api") setKeyInput("");
                    setChoice({
                      provider_path: provider.provider_path,
                      provider: provider.provider,
                      model: provider.provider_path === "api"
                        ? provider.models.find((model) => model.id.startsWith(`${keyProvider}/`))?.id ?? ""
                        : provider.models[0]?.id ?? "",
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
                    : "API · your OpenAI or Anthropic key"}
              </span>
              {!provider.available && (
                <span className="mt-1 block pl-6 text-xs text-destructive">
                  Setup needed: {provider.unavailable_reason ?? "Provider is not configured."}
                </span>
              )}
            </label>
          );
        })}
      </fieldset>
      {selected?.provider_path === "backend_cli" && !selected.available && (
        <p role="status" className="rounded-md border p-4 text-sm">
          Sign in to {selected.provider === "codex" ? "Codex" : "Claude"} CLI on this machine,
          then run <code>make worker-{selected.provider}</code> in another terminal from the project folder.
          Select Refresh providers after the worker starts. The Docker worker cannot use host CLI logins.
        </p>
      )}
      {selected?.provider_path === "api" && (
        <div className="space-y-4 rounded-md border p-4">
          <div className="space-y-1.5">
            <Label htmlFor="key-provider">API provider</Label>
            <select id="key-provider" value={keyProvider} disabled={keyBusy}
              onChange={(event) => {
                const provider = event.target.value as AiCredentialProvider;
                setKeyProvider(provider);
                setKeyInput("");
                setKeyError(null);
                setKeyNotice(null);
                setChoice((current) => current?.provider_path === "api" ? {
                  ...current,
                  model: selected.models.find((model) => model.id.startsWith(`${provider}/`))?.id ?? "",
                } : current);
              }}
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm">
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
            </select>
          </div>
          {credentials.error ? (
            <p role="alert" className="text-sm text-destructive">
              Could not load API key status. <Button type="button" variant="outline" onClick={() => void credentials.refetch()}>Try again</Button>
            </p>
          ) : (
            <p className="text-sm">{keyProvider === "openai" ? "OpenAI" : "Anthropic"} key: {keyConfigured ? "configured" : "not configured"}</p>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="provider-api-key">{keyConfigured ? "Replace API key" : "API key"}</Label>
            <Input id="provider-api-key" type="password" autoComplete="off" minLength={8} maxLength={512} value={keyInput}
              disabled={keyBusy} onChange={(event) => setKeyInput(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void saveKey(); } }} />
            <p className="text-xs text-muted-foreground">The key is sent to the authenticated API and is never shown again.</p>
            {keyInput.length > 0 && !keyValid && <p role="alert" className="text-sm text-destructive">Enter a key of 8–512 characters without surrounding spaces.</p>}
          </div>
          <div className="flex gap-2">
            <Button type="button" disabled={!keyValid || keyBusy} onClick={() => void saveKey()}>
              {keyBusy ? "Working…" : keyConfigured ? "Replace key" : "Save key"}
            </Button>
            {keyConfigured && <Button type="button" variant="outline" disabled={keyBusy} onClick={() => void deleteKey()}>
              Delete key
            </Button>}
          </div>
          {keyError && <p role="alert" className="text-sm text-destructive">{keyError}</p>}
          {keyNotice && <p role="status" className="text-sm text-success">{keyNotice}</p>}
          {!keyConfigured && <p className="text-sm text-muted-foreground">Save a key before selecting this API model for runs.</p>}
          {keyConfigured && !selected.available && <p className="text-sm text-destructive">The API worker is not connected. Refresh providers after it starts.</p>}
        </div>
      )}
      {unavailableChoice && !demoUnavailable && (
        <p role="alert" className="text-sm text-destructive">
          This provider needs setup before it can run. You can configure it here or select an available provider.
        </p>
      )}
      {selected && (selected.available || selected.provider_path === "api") && (
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
            {(selected.provider_path === "api" ? apiModels : selected.models).map((model) => (
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
