import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useAuth } from "./useAuth";
import { ApiError } from "../api/http";
import type { UserResponse } from "../types/auth";

vi.mock("../api/authClient", async () => {
  const actual = await vi.importActual<typeof import("../api/authClient")>("../api/authClient");
  return {
    ...actual,
    login: vi.fn(),
    signup: vi.fn(),
    getMe: vi.fn(),
    deleteAccount: vi.fn(),
  };
});

import * as authClient from "../api/authClient";

const mockedAuthClient = vi.mocked(authClient);

const testUser: UserResponse = {
  id: 1,
  email: "person@example.com",
  plan: "free",
  created_at: "2026-01-01T00:00:00Z",
};

async function signedInHook() {
  mockedAuthClient.login.mockResolvedValue({ access_token: "tok-1", token_type: "bearer" });
  mockedAuthClient.getMe.mockResolvedValue(testUser);

  const { result } = renderHook(() => useAuth());
  await act(async () => {
    await result.current.login("person@example.com", "correct-password");
  });
  await waitFor(() => expect(result.current.user).not.toBeNull());
  return result;
}

beforeEach(() => {
  window.localStorage.clear();
  vi.clearAllMocks();
});

describe("useAuth().deleteAccount", () => {
  it("calls the API with the current token and password", async () => {
    const result = await signedInHook();
    mockedAuthClient.deleteAccount.mockResolvedValue(undefined);

    await act(async () => {
      await result.current.deleteAccount("my-current-password");
    });

    expect(mockedAuthClient.deleteAccount).toHaveBeenCalledWith("my-current-password", "tok-1");
  });

  it("on success, clears token and user via the same mechanism logout() uses", async () => {
    const result = await signedInHook();
    mockedAuthClient.deleteAccount.mockResolvedValue(undefined);

    await act(async () => {
      await result.current.deleteAccount("my-current-password");
    });

    expect(result.current.token).toBeNull();
    expect(result.current.user).toBeNull();
    expect(window.localStorage.getItem("aitryon_auth_token")).toBeNull();
  });

  it("on failure, leaves token and user intact and propagates the error", async () => {
    const result = await signedInHook();
    mockedAuthClient.deleteAccount.mockRejectedValue(new ApiError("Incorrect email or password.", 401));

    await act(async () => {
      await expect(result.current.deleteAccount("wrong-password")).rejects.toThrow(
        "Incorrect email or password.",
      );
    });

    expect(result.current.token).toBe("tok-1");
    expect(result.current.user).toEqual(testUser);
  });

  it("refuses to run when not signed in, without calling the API", async () => {
    const { result } = renderHook(() => useAuth());

    await expect(result.current.deleteAccount("whatever")).rejects.toThrow();
    expect(mockedAuthClient.deleteAccount).not.toHaveBeenCalled();
  });
});
