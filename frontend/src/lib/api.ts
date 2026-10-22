import type { JsonRecord, UserProfile } from "@/types";

const API_ROOT = "/api/backend/api/v1";
const TOKEN_KEY = "enigma.access_token";
const PROPERTY_KEY = "enigma.active_property";

export const sessionStorageKeys = { token: TOKEN_KEY, property: PROPERTY_KEY };

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export function readToken() {
  return typeof window === "undefined" ? null : window.sessionStorage.getItem(TOKEN_KEY);
}

export function readPropertyId() {
  return typeof window === "undefined" ? null : window.sessionStorage.getItem(PROPERTY_KEY);
}

export function saveToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) window.sessionStorage.setItem(TOKEN_KEY, token);
  else window.sessionStorage.removeItem(TOKEN_KEY);
}

export function savePropertyId(propertyId: string | null) {
  if (typeof window === "undefined") return;
  if (propertyId) window.sessionStorage.setItem(PROPERTY_KEY, propertyId);
  else window.sessionStorage.removeItem(PROPERTY_KEY);
}

function apiPath(path: string) {
  return `${API_ROOT}${path.startsWith("/") ? path : `/${path}`}`;
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = readToken();
  const propertyId = readPropertyId();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (propertyId) headers.set("X-Property-ID", propertyId);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(apiPath(path), { ...init, headers, cache: "no-store" });
  const contentType = response.headers.get("content-type") ?? "";
  const body = contentType.includes("application/json") ? await response.json().catch(() => null) : await response.text();
  if (!response.ok) {
    const detail = (body as JsonRecord | null)?.detail ?? body;
    const message = typeof detail === "string" ? detail : `Request failed (${response.status})`;
    throw new ApiError(message, response.status, detail);
  }
  return body as T;
}

export async function signIn(email: string, password: string) {
  const result = await apiRequest<{ access_token: string; token_type: string; expires_in: number }>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  saveToken(result.access_token);
  return result;
}

export function getProfile() {
  return apiRequest<UserProfile>("/auth/me");
}

export function defaultLanding(profile: UserProfile) {
  const workspaceMemberships = profile.memberships.filter((membership) => membership.role !== "resident");
  if (!workspaceMemberships.length) return "/tenant";
  const activeProperty = readPropertyId();
  if (workspaceMemberships.length > 1 && !workspaceMemberships.some((membership) => membership.property_id === activeProperty)) return "/select-property";
  return "/dashboard";
}

export function propertyPath(path: string, propertyId = readPropertyId()) {
  if (!propertyId) throw new Error("Choose an active property first.");
  return `/properties/${encodeURIComponent(propertyId)}${path.startsWith("/") ? path : `/${path}`}`;
}

export function queryString(values: Record<string, string | number | boolean | undefined | null>) {
  const query = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value).length) query.set(key, String(value));
  });
  const text = query.toString();
  return text ? `?${text}` : "";
}

export async function downloadCsv(path: string, filename: string) {
  const headers = new Headers();
  const token = readToken();
  const propertyId = readPropertyId();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (propertyId) headers.set("X-Property-ID", propertyId);
  const response = await fetch(apiPath(path), { headers, cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(body.detail ?? `Download failed (${response.status})`, response.status, body.detail);
  }
  const blob = await response.blob();
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(href);
}

export function displayValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.length ? `${value.length} items` : "—";
  if (typeof value === "object") return "Details";
  return String(value);
}

export function formatMoney(value: unknown, currency = "USD") {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "—";
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 2 }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(2)}`;
  }
}

export function recordId(row: JsonRecord) {
  return String(row.id ?? row.approval_id ?? row.invoice_id ?? row.payment_id ?? row.expense_id ?? row.request_id ?? row.work_order_id ?? row.finding_id ?? row.workflow_id ?? row.notification_id ?? row.transaction_id ?? row.document_id ?? "");
}
