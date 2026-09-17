import { useState } from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AuthBar from "./AuthBar";
import { ApiError } from "../api/http";
import type { UserResponse } from "../types/auth";

const signedInUser: UserResponse = {
  id: 1,
  email: "signed-in@example.com",
  plan: "free",
  created_at: "2026-01-01T00:00:00Z",
};

const noop = async () => {};

/** AuthBar is a controlled component -- it doesn't own `user` itself, the
 * real parent (useAuth()/App.tsx) does, and clears it to null once
 * deleteAccount() resolves. This wrapper plays that same role for tests
 * that need to observe the post-success, signed-out UI state. */
function ControlledAuthBar({ onDeleteAccount }: { onDeleteAccount: (password: string) => Promise<void> }) {
  const [currentUser, setCurrentUser] = useState<UserResponse | null>(signedInUser);
  const handleDeleteAccount = async (password: string) => {
    await onDeleteAccount(password);
    setCurrentUser(null);
  };
  return (
    <AuthBar user={currentUser} onLogin={noop} onSignup={noop} onLogout={vi.fn()} onDeleteAccount={handleDeleteAccount} />
  );
}

describe("AuthBar — delete account", () => {
  it("hides the delete-account action entirely when signed out", () => {
    render(<AuthBar user={null} onLogin={noop} onSignup={noop} onLogout={vi.fn()} onDeleteAccount={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete account" })).not.toBeInTheDocument();
  });

  it("opens a confirmation dialog explaining permanence and requiring a password, without submitting anything yet", async () => {
    const onDeleteAccount = vi.fn();
    const user = userEvent.setup();
    render(
      <AuthBar user={signedInUser} onLogin={noop} onSignup={noop} onLogout={vi.fn()} onDeleteAccount={onDeleteAccount} />,
    );

    await user.click(screen.getByRole("button", { name: "Delete account" }));

    expect(screen.getByText(/permanently deletes your account/i)).toBeInTheDocument();
    expect(screen.getByText(/can't be undone/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Current password")).toBeInTheDocument();
    // Only what's needed (a password field) -- no other account details invented/exposed here.
    expect(screen.queryByText(signedInUser.email)).toBeInTheDocument(); // still visible behind the modal, not duplicated
    expect(onDeleteAccount).not.toHaveBeenCalled();
  });

  it("submits the entered password, and on success clears the modal and shows a confirmation message", async () => {
    const onDeleteAccount = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<ControlledAuthBar onDeleteAccount={onDeleteAccount} />);

    await user.click(screen.getByRole("button", { name: "Delete account" }));
    await user.type(screen.getByPlaceholderText("Current password"), "my-current-password");
    await user.click(screen.getByRole("button", { name: "Permanently delete my account" }));

    await waitFor(() => expect(onDeleteAccount).toHaveBeenCalledWith("my-current-password"));
    await waitFor(() => expect(screen.getByText("Your account has been deleted.")).toBeInTheDocument());
    expect(screen.queryByPlaceholderText("Current password")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("disables the submit button while the request is in flight", async () => {
    let resolveDelete!: () => void;
    const onDeleteAccount = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveDelete = resolve;
        }),
    );
    const user = userEvent.setup();
    render(
      <AuthBar user={signedInUser} onLogin={noop} onSignup={noop} onLogout={vi.fn()} onDeleteAccount={onDeleteAccount} />,
    );

    await user.click(screen.getByRole("button", { name: "Delete account" }));
    await user.type(screen.getByPlaceholderText("Current password"), "my-current-password");
    await user.click(screen.getByRole("button", { name: "Permanently delete my account" }));

    const submitButton = await screen.findByRole("button", { name: "Deleting..." });
    expect(submitButton).toBeDisabled();

    resolveDelete();
    await waitFor(() => expect(screen.queryByPlaceholderText("Current password")).not.toBeInTheDocument());
  });

  it("on failure (e.g. wrong password), shows the error and leaves the account fully signed in", async () => {
    // A real ApiError, matching what useAuth().deleteAccount() actually
    // rejects with -- DeleteAccountModal only shows an ApiError's own
    // message, falling back to a generic one for anything else.
    const onDeleteAccount = vi.fn().mockRejectedValue(new ApiError("Incorrect email or password.", 401));
    const onLogout = vi.fn();
    const user = userEvent.setup();
    render(
      <AuthBar user={signedInUser} onLogin={noop} onSignup={noop} onLogout={onLogout} onDeleteAccount={onDeleteAccount} />,
    );

    await user.click(screen.getByRole("button", { name: "Delete account" }));
    await user.type(screen.getByPlaceholderText("Current password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Permanently delete my account" }));

    await waitFor(() => expect(screen.getByText("Incorrect email or password.")).toBeInTheDocument());

    // Still fully signed in: email + Sign out still shown, modal still open for retry, logout never invoked.
    expect(screen.getByText(signedInUser.email)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Current password")).toBeInTheDocument();
    expect(onLogout).not.toHaveBeenCalled();
  });
});
