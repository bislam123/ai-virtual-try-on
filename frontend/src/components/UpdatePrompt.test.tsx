import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import UpdatePrompt from "./UpdatePrompt";

describe("UpdatePrompt", () => {
  it("announces the update as a status region and calls onUpdate when tapped", async () => {
    const onUpdate = vi.fn();
    const user = userEvent.setup();
    render(<UpdatePrompt onUpdate={onUpdate} />);

    expect(screen.getByRole("status")).toHaveTextContent(/new version/i);

    await user.click(screen.getByRole("button", { name: "Update" }));

    expect(onUpdate).toHaveBeenCalledTimes(1);
  });
});
