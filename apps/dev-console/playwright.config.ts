import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.spec.ts',
  timeout: 25_000,
  fullyParallel: false,
  workers: 1,
  reporter: 'line',
  use: {
    browserName: 'chromium',
    channel: 'chromium',
    headless: true,
    baseURL: 'http://127.0.0.1:8000',
    serviceWorkers: 'block', // Route fixtures never bypass interception through a worker.
    trace: 'off',
    screenshot: 'off',
    video: 'off',
  },
  // Deliberately no webServer: tests never start a second API or a dev server.
});
