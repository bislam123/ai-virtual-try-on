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
