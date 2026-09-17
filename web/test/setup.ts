/**
 * Vitest setup — registers the jest-dom matchers (toBeInTheDocument, etc.).
 * Testing Library's auto-cleanup relies on framework globals being present,
 * which `globals: true` in vitest.config.ts provides.
 */
import "@testing-library/jest-dom/vitest";
