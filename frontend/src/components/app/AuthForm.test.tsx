import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to }: { children: ReactNode; to: string }) => <a href={to}>{children}</a>,
}));

import { AuthForm } from "./AuthForm";

describe("auth form errors", () => {
  it("does not render a native submit button before hydration", () => {
    const html = renderToString(<AuthForm mode="signup" onSubmit={vi.fn()} />);
    expect(html).toContain("Loading account form");
    expect(html).not.toContain("<form");
    expect(html).not.toContain('type="submit"');
  });

  it("shows inline validation before calling the API", () => {
    const onSubmit = vi.fn();
    render(<AuthForm mode="signup" onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a valid email address");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("displays a duplicate-account error returned by registration", async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error("An account with this email already exists."));
    render(<AuthForm mode="signup" onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "person@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "CorrectHorse123!" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already exists");
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("validates login fields before submitting and shows rejected credentials", async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error("Invalid email or password."));
    render(<AuthForm mode="login" onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a valid email address");
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "person@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Password must be at least 8 characters");
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "Incorrect123" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
  });

  it("reports an unavailable API without claiming signup succeeded", async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error("Could not reach the RunGuard API."));
    render(<AuthForm mode="signup" onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "person@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "CorrectHorse123!" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the RunGuard API");
    await waitFor(() => expect(screen.getByRole("button", { name: "Create account" })).toBeEnabled());
  });
});
