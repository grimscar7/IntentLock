import type { ApiErrorBody } from "./types";

/** Client-side form of the shared error contract: code, message, retryable, request_id. */
export class ApiError extends Error {
  code: string;
  retryable: boolean;
  requestId: string;
  status: number;
  constructor(body: ApiErrorBody, status: number) {
    super(body.message);
    this.name = "ApiError";
    this.code = body.code;
    this.retryable = body.retryable;
    this.requestId = body.request_id;
    this.status = status;
  }
}

/** Converts anything thrown into the shared error contract so the UI renders one shape. */
export function toApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e;
  const message = e instanceof Error ? e.message : "Something went wrong.";
  return new ApiError({ code: "CLIENT_ERROR", message, retryable: true, request_id: "client" }, 0);
}
