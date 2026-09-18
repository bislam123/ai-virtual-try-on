import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useDialogA11y } from "./useDialogA11y";

function TestDialog({ onClose }: { onClose: () => void }) {
  const panelRef = useDialogA11y(onClose);
  return (
    <div ref={panelRef} tabIndex={-1} role="dialog" aria-label="Test dialog">
      <input placeholder="Inside field" />
    </div>
  );
}

function Harness({ open }: { open: boolean }) {
  return (
    <div>
      <button>Trigger</button>
      {open && <TestDialog onClose={vi.fn()} />}
    </div>
  );
}

describe("useDialogA11y", () => {
  it("moves focus into the dialog panel on mount", () => {
    render(<TestDialog onClose={vi.fn()} />);

    expect(screen.getByRole("dialog")).toHaveFocus();
  });

  it("calls onClose when Escape is pressed", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<TestDialog onClose={onClose} />);

    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("always calls the latest onClose, even if its identity changed after mount", async () => {
    const firstOnClose = vi.fn();
    const secondOnClose = vi.fn();
    const user = userEvent.setup();

    function Rerenderable({ onClose }: { onClose: () => void }) {
      const panelRef = useDialogA11y(onClose);
      return (
        <div ref={panelRef} tabIndex={-1} role="dialog" aria-label="Test dialog" />
      );
    }

    const { rerender } = render(<Rerenderable onClose={firstOnClose} />);
    rerender(<Rerenderable onClose={secondOnClose} />);

    await user.keyboard("{Escape}");

    expect(firstOnClose).not.toHaveBeenCalled();
    expect(secondOnClose).toHaveBeenCalledTimes(1);
  });

  it("restores focus to whatever had focus before the dialog opened, once it closes", () => {
    const { rerender } = render(<Harness open={false} />);
    const trigger = screen.getByRole("button", { name: "Trigger" });
    trigger.focus();
    expect(trigger).toHaveFocus();

    rerender(<Harness open={true} />);
    expect(screen.getByRole("dialog")).toHaveFocus();

    rerender(<Harness open={false} />);
    expect(trigger).toHaveFocus();
  });
});
