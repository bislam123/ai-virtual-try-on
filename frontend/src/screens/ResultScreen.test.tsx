import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import ResultScreen from "./ResultScreen";
import { ApiError } from "../api/http";

vi.mock("../api/tryOnClient", async () => {
  const actual = await vi.importActual<typeof import("../api/tryOnClient")>("../api/tryOnClient");
  return { ...actual, fetchResultImageBlob: vi.fn(), saveTryOnResult: vi.fn() };
});

import * as tryOnClient from "../api/tryOnClient";

const mockedTryOnClient = vi.mocked(tryOnClient);

// jsdom doesn't implement real blob-URL creation for Node's native
// File/Blob (it errors reading internal state it never populated), so
// stub these two the way this environment's other object-URL consumers
// need to be tested -- not a statement about the real browser behavior.
let objectUrlCounter = 0;
beforeEach(() => {
  vi.stubGlobal(
    "URL",
    Object.assign(Object.create(URL), URL, {
      createObjectURL: vi.fn(() => `blob:mock-${++objectUrlCounter}`),
      revokeObjectURL: vi.fn(),
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
});

const noop = () => {};

function samplePersonFile(): File {
  return new File(["fake-bytes"], "person.png", { type: "image/png" });
}

describe("ResultScreen — result image now requires the owner's token, not a bare <img src>", () => {
  it("fetches the result with the signed-in viewer's token and displays it", async () => {
    const blob = new Blob(["fake-image-bytes"], { type: "image/png" });
    mockedTryOnClient.fetchResultImageBlob.mockResolvedValue(blob);

    render(
      <ResultScreen
        personImage={samplePersonFile()}
        jobId="job-123"
        saved={false}
        authToken="the-owners-token"
        onSaved={noop}
        onTryAnother={noop}
        onChangeClothing={noop}
      />,
    );

    await waitFor(() =>
      expect(mockedTryOnClient.fetchResultImageBlob).toHaveBeenCalledWith("job-123", "the-owners-token"),
    );

    const img = await screen.findByAltText("Try-on result");
    expect(img).toHaveAttribute("src", expect.stringContaining("blob:"));
  });

  it("fetches with no token for an anonymous viewer's own anonymous job", async () => {
    mockedTryOnClient.fetchResultImageBlob.mockResolvedValue(new Blob(["x"], { type: "image/png" }));

    render(
      <ResultScreen
        personImage={samplePersonFile()}
        jobId="job-456"
        saved={false}
        authToken={null}
        onSaved={noop}
        onTryAnother={noop}
        onChangeClothing={noop}
      />,
    );

    await waitFor(() => expect(mockedTryOnClient.fetchResultImageBlob).toHaveBeenCalledWith("job-456", null));
  });

  it("shows a clear error instead of a broken image when the fetch is rejected (e.g. not the owner)", async () => {
    mockedTryOnClient.fetchResultImageBlob.mockRejectedValue(
      new ApiError("You can only view your own try-on jobs.", 403),
    );

    render(
      <ResultScreen
        personImage={samplePersonFile()}
        jobId="job-789"
        saved={false}
        authToken="someone-elses-token"
        onSaved={noop}
        onTryAnother={noop}
        onChangeClothing={noop}
      />,
    );

    await waitFor(() => expect(screen.getByText("You can only view your own try-on jobs.")).toBeInTheDocument());
    expect(screen.queryByAltText("Try-on result")).not.toBeInTheDocument();
  });

  it("announces a result-load failure as an alert", async () => {
    mockedTryOnClient.fetchResultImageBlob.mockRejectedValue(new ApiError("Couldn't load your result image.", 500));

    render(
      <ResultScreen
        personImage={samplePersonFile()}
        jobId="job-999"
        saved={false}
        authToken={null}
        onSaved={noop}
        onTryAnother={noop}
        onChangeClothing={noop}
      />,
    );

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Couldn't load your result image."));
  });
});
