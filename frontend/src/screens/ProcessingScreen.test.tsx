import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ProcessingScreen from "./ProcessingScreen";

describe("ProcessingScreen — cancel", () => {
  it("renders a Cancel button and calls onCancel when clicked", async () => {
    const onCancel = vi.fn();
    const user = userEvent.setup();
    render(<ProcessingScreen status="pending" onCancel={onCancel} />);

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("offers cancel while processing too, not just pending", () => {
    render(<ProcessingScreen status="processing" onCancel={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });
});

describe("ProcessingScreen — status announcements", () => {
  it("shows a distinct, non-percentage stage label for pending vs. processing", () => {
    const { rerender } = render(<ProcessingScreen status="pending" onCancel={vi.fn()} />);
    expect(screen.getByText("Preparing your photos...")).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument(); // never invents a percentage

    rerender(<ProcessingScreen status="processing" onCancel={vi.fn()} />);
    expect(screen.getByText("Detecting clothing...")).toBeInTheDocument();
  });

  it("marks the stage label as an aria-live region, so a screen reader hears it change", () => {
    render(<ProcessingScreen status="pending" onCancel={vi.fn()} />);

    const label = screen.getByText("Preparing your photos...");
    expect(label).toHaveAttribute("aria-live", "polite");
  });

  it("does not mark the every-second elapsed-time counter as live (that would spam a screen reader)", () => {
    render(<ProcessingScreen status="pending" onCancel={vi.fn()} />);

    const elapsed = screen.getByText(/elapsed/);
    expect(elapsed).not.toHaveAttribute("aria-live");
  });
});
