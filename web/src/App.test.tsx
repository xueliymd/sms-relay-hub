import { render, screen } from "@testing-library/react";
import type { ComponentType } from "react";

async function loadApp(): Promise<ComponentType> {
  try {
    return (await import("./App")).default;
  } catch (error) {
    if (error instanceof Error && error.message.includes("App")) {
      return () => null;
    }

    throw error;
  }
}

test("renders an accessible main region", async () => {
  const App = await loadApp();

  render(<App />);

  expect(screen.getByRole("main")).toBeInTheDocument();
});
