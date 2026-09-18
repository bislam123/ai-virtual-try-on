export interface AuthResponse {
  access_token: string;
  token_type: string;
}

export interface UserResponse {
  id: number;
  email: string;
  plan: string;
  created_at: string;
  // Display-only -- decides whether the "Admin" nav entry shows up.
  // Every actual admin API call is independently re-authorized
  // server-side; this is never sent anywhere as proof of anything.
  is_admin: boolean;
}

export interface MessageResponse {
  message: string;
}
