"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { getProfile, readPropertyId, readToken, savePropertyId, saveToken, signIn as requestSignIn } from "@/lib/api";
import type { Membership, SessionState, UserProfile } from "@/types";

type SessionContextValue = SessionState & {
  activeMembership: Membership | null;
  signIn: (email: string, password: string) => Promise<UserProfile>;
  completeSignIn: (token: string) => Promise<UserProfile>;
  signOut: () => void;
  selectProperty: (propertyId: string) => void;
  refreshProfile: () => Promise<UserProfile>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<SessionState>({ token: null, profile: null, propertyId: null, ready: false });

  const applyProfile = useCallback((profile: UserProfile, token: string) => {
    const storedProperty = readPropertyId();
    const workspaceMemberships = profile.memberships.filter((membership) => membership.role !== "resident");
    let propertyId = workspaceMemberships.some((membership) => membership.property_id === storedProperty) ? storedProperty : null;
    if (!propertyId && workspaceMemberships.length === 1) propertyId = workspaceMemberships[0].property_id;
    savePropertyId(propertyId);
    setState({ token, profile, propertyId, ready: true });
  }, []);

  const refreshProfile = useCallback(async () => {
    const token = readToken();
    if (!token) throw new Error("Your session has ended. Sign in again.");
    const profile = await getProfile();
    applyProfile(profile, token);
    return profile;
  }, [applyProfile]);

  useEffect(() => {
    const token = readToken();
    if (!token) {
      setState({ token: null, profile: null, propertyId: null, ready: true });
      return;
    }
    getProfile()
      .then((profile) => applyProfile(profile, token))
      .catch(() => {
        saveToken(null);
        savePropertyId(null);
        setState({ token: null, profile: null, propertyId: null, ready: true });
      });
  }, [applyProfile]);

  const signIn = useCallback(async (email: string, password: string) => {
    const result = await requestSignIn(email, password);
    const profile = await getProfile();
    applyProfile(profile, result.access_token);
    return profile;
  }, [applyProfile]);

  const completeSignIn = useCallback(async (token: string) => {
    saveToken(token);
    const profile = await getProfile();
    applyProfile(profile, token);
    return profile;
  }, [applyProfile]);

  const signOut = useCallback(() => {
    saveToken(null);
    savePropertyId(null);
    setState({ token: null, profile: null, propertyId: null, ready: true });
  }, []);

  const selectProperty = useCallback((propertyId: string) => {
    savePropertyId(propertyId);
    setState((current) => ({ ...current, propertyId }));
  }, []);

  const activeMembership = state.profile?.memberships.find((membership) => membership.property_id === state.propertyId) ?? null;
  const value = useMemo(() => ({ ...state, activeMembership, signIn, completeSignIn, signOut, selectProperty, refreshProfile }), [state, activeMembership, signIn, completeSignIn, signOut, selectProperty, refreshProfile]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSession must be used inside SessionProvider");
  return context;
}
