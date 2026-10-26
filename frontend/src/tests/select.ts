import { fireEvent, screen } from "@testing-library/react";

/** Exercise the real Radix picker through its keyboard interaction. */
export async function pickSelectOption(trigger: HTMLElement, name: string) {
  trigger.focus();
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  const option = await screen.findByRole("option", { name });
  option.focus();
  fireEvent.keyDown(option, { key: "Enter" });
}
