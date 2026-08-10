/**
 * OTLP HTTP Exporter
 */
import type { OtlpPayload } from './types.ts';

export const OTLP_ENDPOINTS = [
  'http://127.0.0.1:4318/v1/traces',
  'http://localhost:4318/v1/traces'
];

export async function exportSpan(payload: OtlpPayload): Promise<void> {
  for (const endpoint of OTLP_ENDPOINTS) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 800);
    
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload),
        signal: controller.signal
      });
      
      clearTimeout(timeout);
      
      if (response.ok) {
        return; // Success, don't try fallback
      }
      // Non-OK HTTP status: surface it so a broken/misconfigured Aspire
      // endpoint is visible. The collector must never crash pi, so this only
      // logs -- the fallback loop continues to the next endpoint.
      console.error(`[pi-statusline] OTLP export to ${endpoint} failed: HTTP ${response.status}`);
    } catch (e) {
      clearTimeout(timeout);
      // Surface the failure (never re-throw -- the collector must never crash
      // pi), then continue to the next endpoint in the fallback loop.
      const errType = e instanceof Error ? e.name : typeof e;
      const errMsg = e instanceof Error ? e.message : String(e);
      console.error(`[pi-statusline] OTLP export error: ${errType}: ${errMsg}`);
    }
  }
}
