import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import BreathingPacer from "../BreathingPacer";

describe("BreathingPacer Component", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("renders in initial resting state", () => {
    const { container } = render(<BreathingPacer />);

    const markDiv = container.querySelector(".breathing-mark");
    expect(markDiv).toBeInTheDocument();
    expect(markDiv).toHaveAttribute("data-phase", "rest");

    expect(screen.getByText("Pulsa iniciar para comenzar")).toBeInTheDocument();
    expect(
      screen.getByText("4 s inspirar - 2 s sostener - 6 s exhalar - 12s por ciclo")
    ).toBeInTheDocument();

    const button = screen.getByRole("button", { name: "Iniciar respiracion guiada" });
    expect(button).toBeInTheDocument();

    expect(
      screen.getByText(/Esto es una guia visual de ritmo respiratorio/i)
    ).toBeInTheDocument();
  });

  it("starts the breathing cycle when start button is clicked", () => {
    const { container } = render(<BreathingPacer />);

    const button = screen.getByRole("button", { name: "Iniciar respiracion guiada" });
    fireEvent.click(button);

    const markDiv = container.querySelector(".breathing-mark");
    expect(markDiv).toHaveAttribute("data-phase", "inhale");
    expect(screen.getByText("Inhala suave")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Detener" })).toBeInTheDocument();
  });

  it("advances through inhale -> hold -> exhale -> inhale phases on timer intervals", () => {
    const { container } = render(<BreathingPacer />);

    // Start pacer
    fireEvent.click(screen.getByRole("button", { name: "Iniciar respiracion guiada" }));

    const markDiv = container.querySelector(".breathing-mark");

    // Phase 1: Inhale (4000ms)
    expect(markDiv).toHaveAttribute("data-phase", "inhale");
    expect(screen.getByText("Inhala suave")).toBeInTheDocument();

    // Advance 4000ms -> Hold (2000ms)
    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(markDiv).toHaveAttribute("data-phase", "hold");
    expect(screen.getByText("Sosten un momento")).toBeInTheDocument();

    // Advance 2000ms -> Exhale (6000ms)
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(markDiv).toHaveAttribute("data-phase", "exhale");
    expect(screen.getByText("Exhala lento")).toBeInTheDocument();

    // Advance 6000ms -> Inhale (cycles back to start)
    act(() => {
      vi.advanceTimersByTime(6000);
    });
    expect(markDiv).toHaveAttribute("data-phase", "inhale");
    expect(screen.getByText("Inhala suave")).toBeInTheDocument();
  });

  it("stops breathing cycle and resets state when stop button is clicked mid-cycle", () => {
    const { container } = render(<BreathingPacer />);

    // Start pacer
    fireEvent.click(screen.getByRole("button", { name: "Iniciar respiracion guiada" }));

    // Advance into hold phase
    act(() => {
      vi.advanceTimersByTime(4000);
    });
    const markDiv = container.querySelector(".breathing-mark");
    expect(markDiv).toHaveAttribute("data-phase", "hold");

    // Click stop
    fireEvent.click(screen.getByRole("button", { name: "Detener" }));

    // Should reset to resting state
    expect(markDiv).toHaveAttribute("data-phase", "rest");
    expect(screen.getByText("Pulsa iniciar para comenzar")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Iniciar respiracion guiada" })
    ).toBeInTheDocument();

    // Advance timer further to ensure timer was cancelled and state doesn't change
    act(() => {
      vi.advanceTimersByTime(10000);
    });
    expect(markDiv).toHaveAttribute("data-phase", "rest");
    expect(screen.getByText("Pulsa iniciar para comenzar")).toBeInTheDocument();
  });

  it("cleans up timeout timer upon unmounting", () => {
    const { container, unmount } = render(<BreathingPacer />);

    fireEvent.click(screen.getByRole("button", { name: "Iniciar respiracion guiada" }));
    const markDiv = container.querySelector(".breathing-mark");
    expect(markDiv).toHaveAttribute("data-phase", "inhale");

    // Unmount while timer is pending
    unmount();

    // Advance timer to trigger potential state update on unmounted component
    expect(() => {
      act(() => {
        vi.advanceTimersByTime(5000);
      });
    }).not.toThrow();
  });
});
