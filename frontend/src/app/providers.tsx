"use client";

import { SessionProvider } from "@/context/session";

export function Providers({ children }: { children: React.ReactNode }) {
  return <SessionProvider>{children}</SessionProvider>;
}
