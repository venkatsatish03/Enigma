export type Membership = {
  property_id: string;
  property_name: string | null;
  role: string;
  permissions: string[];
};

export type UserProfile = {
  user_id: string;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  memberships: Membership[];
};

export type SessionState = {
  token: string | null;
  profile: UserProfile | null;
  propertyId: string | null;
  ready: boolean;
};

export type JsonRecord = Record<string, unknown>;
