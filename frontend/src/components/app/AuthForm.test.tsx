import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to }: { children: ReactNode; to: string }) => <a href={to}>{children}</a>,
}));

import { AuthForm } from "./AuthForm";

describe("auth form errors", () => {
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
});
