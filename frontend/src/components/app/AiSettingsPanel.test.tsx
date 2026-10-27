import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AiSettingsPanel } from "./AiSettingsPanel";
import type { AiProviderCatalog, AiSettings, AiSettingsInput, ApiClient } from "@/lib/api/types";

type SettingsClient = Pick<ApiClient, "getAiProviders" | "getAiSettings" | "updateAiSettings">;

const catalog: AiProviderCatalog = {
  providers: [
    {
      provider_path: "backend_cli",
      provider: "codex",
      label: "Codex CLI",
      available: true,
      unavailable_reason: null,
      models: [
        { id: "gpt-6", label: "GPT-6" },
        { id: "gpt-6-mini", label: "GPT-6 mini" },
      ],
    },
    {
      provider_path: "backend_cli",
      provider: "claude",
      label: "Claude CLI",
      available: false,
      unavailable_reason: "Host worker is not signed in.",
      models: [{ id: "sonnet", label: "Sonnet" }],
    },
    {
      provider_path: "api",
      provider: "litellm",
      label: "LiteLLM API",
      available: true,
      unavailable_reason: null,
      models: [{ id: "openai/gpt-6", label: "GPT-6 API" }],
    },
  ],
};
const initial: AiSettings = {
  provider_path: "backend_cli",
  provider: "codex",
  model: "gpt-6",
  available: true,
};

function show(client: SettingsClient) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AiSettingsPanel client={client} />
    </QueryClientProvider>,
  );
}

function client(overrides: Partial<SettingsClient> = {}): SettingsClient {
  return {
    getAiProviders: vi.fn().mockResolvedValue(catalog),
    getAiSettings: vi.fn().mockResolvedValue(initial),
    updateAiSettings: vi
      .fn()
      .mockImplementation(async (input: AiSettingsInput) => ({ ...input, available: true })),
    ...overrides,
  };
}

describe("AI settings", () => {
  it("refreshes a fresh demo choice automatically when the worker heartbeat arrives", async () => {
    const demo = {
      provider_path: "local_demo" as const,
      provider: "extractive" as const,
      model: "extractive-v1",
      available: false,
    };
    const unavailableCatalog: AiProviderCatalog = { providers: [{
      provider_path: "local_demo", provider: "extractive",
      label: "local demo — no AI model", available: false,
      unavailable_reason: "Local demo worker is not connected.",
      models: [{ id: "extractive-v1", label: "Extractive page excerpt" }],
    }] };
    const availableCatalog: AiProviderCatalog = { providers: [{
      ...unavailableCatalog.providers[0]!, available: true, unavailable_reason: null,
    }] };
    const api = client({
      getAiProviders: vi.fn().mockResolvedValueOnce(unavailableCatalog).mockResolvedValue(availableCatalog),
      getAiSettings: vi.fn().mockResolvedValueOnce(demo).mockResolvedValue({ ...demo, available: true }),
    });
    show(api);
    expect(await screen.findByText(/Waiting for the local demo worker to start/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /local demo — no AI model/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeDisabled();
    await waitFor(() => {
      expect(screen.getByRole("radio", { name: /local demo — no AI model/i })).toBeEnabled();
      expect(screen.getByRole("button", { name: "Save AI settings" })).toBeEnabled();
    }, { timeout: 8_000 });
    expect(screen.queryByText(/Waiting for the local demo worker to start/)).not.toBeInTheDocument();
    expect(api.getAiProviders).toHaveBeenCalledTimes(2);
    expect(api.getAiSettings).toHaveBeenCalledTimes(2);
  }, 10_000);

  it("labels the fresh local extractive choice as a no-AI demo", async () => {
    const demo = {
      provider_path: "local_demo" as const,
      provider: "extractive" as const,
      model: "extractive-v1",
      available: true,
    };
    const api = client({
      getAiProviders: vi.fn().mockResolvedValue({ providers: [{
        provider_path: demo.provider_path,
        provider: demo.provider,
        label: "local demo — no AI model",
        available: true,
        unavailable_reason: null,
        models: [{ id: demo.model, label: "Extractive page excerpt" }],
      }] }),
      getAiSettings: vi.fn().mockResolvedValue(demo),
    });
    show(api);
    expect(await screen.findByRole("radio", { name: /local demo — no AI model/i })).toBeChecked();
    expect(screen.getByText(/Deterministic page excerpt · no AI model or API key/)).toBeInTheDocument();
    expect(screen.getByLabelText("Method")).toHaveValue("extractive-v1");
    expect(screen.queryByLabelText("Model")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save AI settings" }));
    await waitFor(() => expect(api.updateAiSettings).toHaveBeenCalledWith({
      provider_path: "local_demo", provider: "extractive", model: "extractive-v1",
    }));
  });

  it("shows loading while both authenticated requests are pending", () => {
    const pending = new Promise<never>(() => {});
    show(
      client({
        getAiProviders: vi.fn().mockReturnValue(pending),
        getAiSettings: vi.fn().mockReturnValue(pending),
      }),
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading AI settings");
  });

  it("shows provider availability and blocks an unavailable choice", async () => {
    const api = client();
    show(api);
    expect(await screen.findByText(/Host worker is not signed in/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Claude CLI/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /Codex CLI/ })).toBeChecked();
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeEnabled();
    expect(screen.queryByLabelText(/API key|credential/i)).not.toBeInTheDocument();
    expect(api.updateAiSettings).not.toHaveBeenCalled();
  });

  it("refreshes a newly connected host provider without a page reload", async () => {
    const unavailable: AiProviderCatalog = { providers: catalog.providers.map((provider) =>
      provider.provider === "claude" ? provider : {
        ...provider, available: false, unavailable_reason: "Provider is not connected.",
      }) };
    const connected: AiProviderCatalog = { providers: unavailable.providers.map((provider) =>
      provider.provider === "codex" ? {
        ...provider, available: true, unavailable_reason: null,
      } : provider) };
    const api = client({
      getAiProviders: vi.fn().mockResolvedValueOnce(unavailable).mockResolvedValue(connected),
      getAiSettings: vi.fn().mockResolvedValue({ ...initial, available: false }),
    });
    show(api);
    expect(await screen.findByRole("radio", { name: /Codex CLI/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Refresh providers" }));
    await waitFor(() => expect(screen.getByRole("radio", { name: /Codex CLI/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("radio", { name: /Codex CLI/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save AI settings" }));
    await waitFor(() => expect(api.updateAiSettings).toHaveBeenCalledWith({
      provider_path: "backend_cli", provider: "codex", model: "gpt-6",
    }));
    expect(api.getAiProviders).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("radio", { name: /LiteLLM API/ })).toBeDisabled();
  });

  it("retains an unsaved model choice across provider refresh", async () => {
    const api = client();
    show(api);
    expect(await screen.findByRole("radio", { name: /Codex CLI/ })).toBeChecked();
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "gpt-6-mini" } });
    fireEvent.click(screen.getByRole("button", { name: "Refresh providers" }));
    await waitFor(() => expect(api.getAiProviders).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("radio", { name: /Codex CLI/ })).toBeChecked();
    expect(screen.getByLabelText("Model")).toHaveValue("gpt-6-mini");
    fireEvent.click(screen.getByRole("button", { name: "Save AI settings" }));
    await waitFor(() => expect(api.updateAiSettings).toHaveBeenCalledWith({
      provider_path: "backend_cli", provider: "codex", model: "gpt-6-mini",
    }));
  });

  it("shows a failed provider refresh and allows a retry", async () => {
    const api = client({
      getAiProviders: vi.fn().mockResolvedValueOnce(catalog)
        .mockRejectedValueOnce(new Error("Provider catalog unavailable"))
        .mockResolvedValue(catalog),
    });
    show(api);
    expect(await screen.findByRole("radio", { name: /Codex CLI/ })).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Refresh providers" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Provider catalog unavailable");
    expect(screen.queryByText(/AI settings saved/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("radio", { name: /Codex CLI/ })).toBeChecked();
    expect(api.getAiProviders).toHaveBeenCalledTimes(3);
  });

  it("selects an allowed API model, saves it, and reloads the persisted choice", async () => {
    let stored = initial;
    const api = client({
      getAiSettings: vi.fn().mockImplementation(async () => stored),
      updateAiSettings: vi.fn().mockImplementation(async (input: AiSettingsInput) => {
        stored = { ...input, available: true };
        return stored;
      }),
    });
    const view = show(api);
    fireEvent.click(await screen.findByRole("radio", { name: /LiteLLM API/ }));
    expect(screen.getByLabelText("Model")).toHaveValue("openai/gpt-6");
    fireEvent.click(screen.getByRole("button", { name: "Save AI settings" }));
    expect(await screen.findByText(/AI settings saved/)).toBeInTheDocument();
    expect(api.updateAiSettings).toHaveBeenCalledWith({
      provider_path: "api",
      provider: "litellm",
      model: "openai/gpt-6",
    });
    view.unmount();
    show(api);
    expect(await screen.findByRole("radio", { name: /LiteLLM API/ })).toBeChecked();
    expect(screen.getByLabelText("Model")).toHaveValue("openai/gpt-6");
  });

  it("rejects an unsupported saved model and does not send it to the API", async () => {
    const api = client({
      getAiSettings: vi.fn().mockResolvedValue({ ...initial, model: "unknown-model" }),
    });
    show(api);
    expect(await screen.findByText("Choose a supported model before saving.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeDisabled();
    expect(api.updateAiSettings).not.toHaveBeenCalled();
  });

  it("shows a saved provider that has become unavailable", async () => {
    show(
      client({
        getAiSettings: vi.fn().mockResolvedValue({
          provider_path: "backend_cli",
          provider: "claude",
          model: "sonnet",
          available: false,
        }),
      }),
    );
    expect(await screen.findByText(/Your saved provider is unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeDisabled();
  });

  it("reports load and save errors without claiming success", async () => {
    const badLoad = show(
      client({ getAiProviders: vi.fn().mockRejectedValue(new Error("Catalog offline")) }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Catalog offline");
    badLoad.unmount();
    const api = client({
      updateAiSettings: vi.fn().mockRejectedValue(new Error("Provider became unavailable")),
    });
    show(api);
    await screen.findByRole("radio", { name: /Codex CLI/ });
    fireEvent.click(screen.getByRole("button", { name: "Save AI settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Provider became unavailable");
    expect(screen.queryByText(/AI settings saved/)).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save AI settings" })).toBeEnabled(),
    );
  });
});
