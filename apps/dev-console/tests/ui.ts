import type { Page } from '@playwright/test';

/** Exercise the native disclosure controls rather than bypassing them through DOM mutations. */
export async function openDetails(page: Page, label: string): Promise<void> {
  const details = page.locator(`details[aria-label="${label}"]`);
  if (!await details.evaluate(node => (node as HTMLDetailsElement).open)) await details.locator(':scope > summary').click();
}
export async function openAdvanced(page: Page): Promise<void> { await openDetails(page, '设置与高级工具'); }
export async function openChatAdvanced(page: Page): Promise<void> { await openDetails(page, '聊天高级选项'); }
export async function openDeveloper(page: Page): Promise<void> { await openDetails(page, '开发测试登录'); }
export async function openLegacy(page: Page): Promise<void> { await openDetails(page, '旧用户名登录'); }
