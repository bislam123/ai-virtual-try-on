import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AuthModal from "./AuthModal";
import { ApiError } from "../api/http";

const noop = async () => {};

describe("AuthModal — forgot password", () => {
  it("starts in sign-in mode and offers a 'Forgot password?' link", () => {
    render(<AuthModal onClose={vi.fn()} onLogin={noop} onSignup={noop} onForgotPassword={noop} />);

    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Forgot password?" })).toBeInTheDocument();
  });

  it("switches to the forgot-password form, which asks for email only (no password field)", async () => {
    const user = userEvent.setup();
    render(<AuthModal onClose={vi.fn()} onLogin={noop} onSignup={noop} onForgotPassword={noop} />);

    await user.click(screen.getByRole("button", { name: "Forgot password?" }));

    expect(screen.getByRole("heading", { name: "Reset your password" })).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Email")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Password")).not.toBeInTheDocument();
  });

  it("submits the email and shows a generic confirmation, never claiming whether the account exists", async () => {
    const onForgotPassword = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<AuthModal onClose={vi.fn()} onLogin={noop} onSignup={noop} onForgotPassword={onForgotPassword} />);

    await user.click(screen.getByRole("button", { name: "Forgot password?" }));
    await user.type(screen.getByPlaceholderText("Email"), "person@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));

    await waitFor(() => expect(onForgotPassword).toHaveBeenCalledWith("person@example.com"));
    expect(screen.getByText(/if an account exists for that email/i)).toBeInTheDocument();
    // Never says "we sent an email" unconditionally, and never mentions
    // whether the account exists either way.
    expect(screen.queryByText(/does not exist/i)).not.toBeInTheDocument();
  });

  it("does not close the modal on a successful forgot-password submission (unlike login/signup)", async () => {
    const onClose = vi.fn();
    const onForgotPassword = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<AuthModal onClose={onClose} onLogin={noop} onSignup={noop} onForgotPassword={onForgotPassword} />);

    await user.click(screen.getByRole("button", { name: "Forgot password?" }));
    await user.type(screen.getByPlaceholderText("Email"), "person@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));

    await waitFor(() => expect(onForgotPassword).toHaveBeenCalled());
    expect(onClose).not.toHaveBeenCalled();
  });

  it("on failure (e.g. rate limited), shows the error and stays on the form", async () => {
    const onForgotPassword = vi.fn().mockRejectedValue(
      new ApiError("Too many attempts. Please try again in about 60 seconds.", 429),
    );
    const user = userEvent.setup();
    render(<AuthModal onClose={vi.fn()} onLogin={noop} onSignup={noop} onForgotPassword={onForgotPassword} />);

    await user.click(screen.getByRole("button", { name: "Forgot password?" }));
    await user.type(screen.getByPlaceholderText("Email"), "person@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));

    await waitFor(() => expect(screen.getByText(/too many attempts/i)).toBeInTheDocument());
    expect(screen.getByPlaceholderText("Email")).toBeInTheDocument(); // form still shown, not the confirmation
  });

  it("'Back to sign in' returns to the login form", async () => {
    const user = userEvent.setup();
    render(<AuthModal onClose={vi.fn()} onLogin={noop} onSignup={noop} onForgotPassword={noop} />);

    await user.click(screen.getByRole("button", { name: "Forgot password?" }));
    await user.click(screen.getByRole("button", { name: "Back to sign in" }));

    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("the 'Forgot password?' link is only offered from sign-in, not from create-account", async () => {
    const user = userEvent.setup();
    render(<AuthModal onClose={vi.fn()} onLogin={noop} onSignup={noop} onForgotPassword={noop} />);

    await user.click(screen.getByRole("button", { name: "New here? Create an account" }));

    expect(screen.getByRole("heading", { name: "Create account" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Forgot password?" })).not.toBeInTheDocument();
  });
});
