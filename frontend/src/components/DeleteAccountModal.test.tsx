import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DeleteAccountModal from "./DeleteAccountModal";
import { ApiError } from "../api/http";

describe("DeleteAccountModal", () => {
  it("submits the entered password to onConfirm", async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<DeleteAccountModal onClose={vi.fn()} onConfirm={onConfirm} />);

    await user.type(screen.getByLabelText("Current password"), "my-password");
    await user.click(screen.getByRole("button", { name: "Permanently delete my account" }));

    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith("my-password"));
  });

  it("on failure, shows the error as an alert and leaves the modal open", async () => {
    const onConfirm = vi.fn().mockRejectedValue(new ApiError("Incorrect email or password.", 403));
    const user = userEvent.setup();
    render(<DeleteAccountModal onClose={vi.fn()} onConfirm={onConfirm} />);

    await user.type(screen.getByLabelText("Current password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Permanently delete my account" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Incorrect email or password."));
    expect(screen.getByLabelText("Current password")).toBeInTheDocument();
  });
});

describe("DeleteAccountModal — accessibility", () => {
  it("exposes itself as a labelled dialog, and focuses the panel on open", () => {
    render(<DeleteAccountModal onClose={vi.fn()} onConfirm={vi.fn()} />);

    const dialog = screen.getByRole("dialog", { name: "Delete account" });
    expect(dialog).toHaveFocus();
  });

  it("closes on Escape", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<DeleteAccountModal onClose={onClose} onConfirm={vi.fn()} />);

    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("the password field has an accessible label, not just a placeholder", () => {
    render(<DeleteAccountModal onClose={vi.fn()} onConfirm={vi.fn()} />);

    expect(screen.getByLabelText("Current password")).toBeInTheDocument();
  });
});
