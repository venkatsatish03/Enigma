import type { NextRequest } from "next/server";

type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  const baseUrl = process.env.ENIGMA_API_BASE_URL ?? "http://127.0.0.1:8000";
  const destination = new URL(`${baseUrl.replace(/\/$/, "")}/${path.map(encodeURIComponent).join("/")}`);
  destination.search = request.nextUrl.search;

  const headers = new Headers();
  for (const name of ["authorization", "content-type", "x-property-id", "accept"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  try {
    const upstream = await fetch(destination, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer(),
      cache: "no-store",
    });
    const responseHeaders = new Headers();
    const contentType = upstream.headers.get("content-type");
    const disposition = upstream.headers.get("content-disposition");
    if (contentType) responseHeaders.set("content-type", contentType);
    if (disposition) responseHeaders.set("content-disposition", disposition);
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    return Response.json({ detail: "Could not reach the ENIGMA API. Check that the backend is running." }, { status: 502 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
export const DELETE = proxy;
