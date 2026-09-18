import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ResetPasswordScreen from "./ResetPasswordScreen";
import { ApiError } from "../api/http";

vi.mock("../api/authClient", async () => {
  const actual = await vi.importActual<typeof import("../api/authClient")>("../api/authClient");
  return { ...actual, resetPassword: vi.fn() };
});

import * as authClient from "../api/authClient";

const mockedAuthClient = vi.mocked(authClient);

describe("ResetPasswordScreen", () => {
  it("submits the token from props with the entered password, never asking the user to type the token", async () => {
    mockedAuthClient.resetPassword.mockResolvedValue({ message: "ok" });
    const user = userEvent.setup();
    render(<ResetPasswordScreen token="raw-token-from-url" onDone={vi.fn()} />);

    expect(screen.queryByDisplayValue("raw-token-from-url")).not.toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("New password"), "new-password-123");
    await user.type(screen.getByPlaceholderText("Confirm new password"), "new-password-123");
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    await waitFor(() =>
      expect(mockedAuthClient.resetPassword).toHaveBeenCalledWith("raw-token-from-url", "new-password-123"),
    );
  });

  it("rejects a mismatched confirmation without calling the API", async () => {
    const user = userEvent.setup();
    render(<ResetPasswordScreen token="raw-token" onDone={vi.fn()} />);

    await user.type(screen.getByPlaceholderText("New password"), "new-password-123");
    await user.type(screen.getByPlaceholderText("Confirm new password"), "different-password-456");
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    expect(screen.getByText("Passwords don't match.")).toBeInTheDocument();
    expect(mockedAuthClient.resetPassword).not.toHaveBeenCalled();
  });

  it("on success, shows a generic confirmation instead of the form", async () => {
    mockedAuthClient.resetPassword.mockResolvedValue({ message: "ok" });
    const user = userEvent.setup();
    render(<ResetPasswordScreen token="raw-token" onDone={vi.fn()} />);

    await user.type(screen.getByPlaceholderText("New password"), "new-password-123");
    await user.type(screen.getByPlaceholderText("Confirm new password"), "new-password-123");
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    await waitFor(() => expect(screen.getByText(/your password has been reset/i)).toBeInTheDocument());
    expect(screen.queryByPlaceholderText("New password")).not.toBeInTheDocument();
  });

  it("on an invalid/expired token, shows the backend's generic error and keeps the form usable for retry", async () => {
    mockedAuthClient.resetPassword.mockRejectedValue(
      new ApiError("This password reset link is invalid or has expired. Please request a new one.", 400),
    );
    const user = userEvent.setup();
    render(<ResetPasswordScreen token="expired-token" onDone={vi.fn()} />);

    await user.type(screen.getByPlaceholderText("New password"), "new-password-123");
    await user.type(screen.getByPlaceholderText("Confirm new password"), "new-password-123");
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    await waitFor(() => expect(screen.getByText(/invalid or has expired/i)).toBeInTheDocument());
    expect(screen.getByPlaceholderText("New password")).toBeInTheDocument();
  });

  it("'Cancel' calls onDone without submitting anything", async () => {
    const onDone = vi.fn();
    const user = userEvent.setup();
    render(<ResetPasswordScreen token="raw-token" onDone={onDone} />);

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onDone).toHaveBeenCalled();
    expect(mockedAuthClient.resetPassword).not.toHaveBeenCalled();
  });

  it("'Continue' after success calls onDone", async () => {
    mockedAuthClient.resetPassword.mockResolvedValue({ message: "ok" });
    const onDone = vi.fn();
    const user = userEvent.setup();
    render(<ResetPasswordScreen token="raw-token" onDone={onDone} />);

    await user.type(screen.getByPlaceholderText("New password"), "new-password-123");
    await user.type(screen.getByPlaceholderText("Confirm new password"), "new-password-123");
    await user.click(screen.getByRole("button", { name: "Reset password" }));
    await user.click(await screen.findByRole("button", { name: "Continue" }));

    expect(onDone).toHaveBeenCalled();
  });
});

describe("ResetPasswordScreen — accessibility", () => {
  it("both password fields have accessible labels, not just placeholders", () => {
    render(<ResetPasswordScreen token="raw-token" onDone={vi.fn()} />);

    expect(screen.getByLabelText("New password")).toBeInTheDocument();
    expect(screen.getByLabelText("Confirm new password")).toBeInTheDocument();
  });

  it("announces a mismatched-confirmation error as an alert", async () => {
    const user = userEvent.setup();
    render(<ResetPasswordScreen token="raw-token" onDone={vi.fn()} />);

    await user.type(screen.getByLabelText("New password"), "new-password-123");
    await user.type(screen.getByLabelText("Confirm new password"), "different-password-456");
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    expect(screen.getByRole("alert")).toHaveTextContent("Passwords don't match.");
  });
});
