"use client";
import { useEffect, useRef } from "react";
import { ApiError } from "@/lib/errors";

/** Same props Sabari's PaymentArea passes. */
export type PayPalCheckoutProps = {
  quoteId: string;
  approvalId: string;
  onPaid: (attemptId: string) => void;
  onCancelled: () => void;
  onError: (error: ApiError) => void;
  onPending?: () => void;
};

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "";
const CLIENT_ID = process.env.NEXT_PUBLIC_PAYPAL_CLIENT_ID ?? ""; // public Sandbox client id only
const DEV_USER = process.env.NEXT_PUBLIC_DEV_USER_ID ?? "buyer_1";
const UNKNOWN = "PAYMENT_STATUS_UNKNOWN";

async function call(method: string, path: string, body?: unknown) {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers: { "Content-Type": "application/json", "X-User-Id": DEV_USER },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    return {
      ok: false,
      status: 0,
      data: {
        error: {
          code: "NETWORK_ERROR",
          message: "Could not reach the server.",
          details: { retryable: true },
        },
      },
    } as any;
  }
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    /* not JSON */
  }
  return { ok: res.ok, status: res.status, data };
}

function toErr(r: any): ApiError {
  const e = r.data?.error ?? {};
  return new ApiError(
    {
      code: e.code ?? "HTTP_" + r.status,
      message: e.message ?? "Request failed",
      retryable: e.details?.retryable ?? r.status >= 500,
      request_id: "client",
      details: e.details,
    } as any,
    r.status,
  );
}

function loadSdk(): Promise<any> {
  const w = window as any;
  if (w.paypal) return Promise.resolve(w.paypal);
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = `https://www.paypal.com/sdk/js?client-id=${encodeURIComponent(CLIENT_ID)}&currency=USD&intent=capture`;
    s.onload = () => resolve(w.paypal);
    s.onerror = () => reject(new Error("PayPal SDK failed to load"));
    document.head.appendChild(s);
  });
}

export function PayPalCheckout(props: PayPalCheckoutProps) {
  const boxRef = useRef<HTMLDivElement>(null);
  const propsRef = useRef(props);
  propsRef.current = props; // always use the latest callbacks

  useEffect(() => {
    let buttons: any;
    let cancelled = false;
    let attemptId: string | null = null;
    let reported = false; // avoid reporting the same error twice

    const fail = (err: ApiError) => {
      reported = true;
      propsRef.current.onError(err);
    };

    // Payment outcome unknown: tell the UI "checking", then ask the server to reconcile.
    // We never show "paid" here and never create a new payment.
    const reconcileLoop = async (id: string) => {
      propsRef.current.onPending?.();
      for (let i = 0; i < 6 && !cancelled; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const r = await call(
          "POST",
          `/api/payments/${encodeURIComponent(id)}/reconcile`,
        );
        if (r.ok && r.data?.state === "PAID")
          return propsRef.current.onPaid(id);
        if (
          r.ok &&
          r.data?.state !== UNKNOWN &&
          r.data?.state !== "CAPTURE_IN_PROGRESS"
        )
          break;
      }
    };

    loadSdk()
      .then((paypal) => {
        if (cancelled || !boxRef.current) return;
        buttons = paypal.Buttons({
          // 1) Only the approval id is sent. The server owns price, currency and merchant.
          createOrder: async () => {
            reported = false;
            const r = await call("POST", "/api/payments/create", {
              approval_id: propsRef.current.approvalId,
            });
            if (!r.ok) {
              fail(toErr(r)); // e.g. APPROVAL_INVALIDATED: PayPal popup never opens
              throw new Error(r.data?.error?.code ?? "CREATE_FAILED");
            }
            attemptId = r.data.attempt_id;
            return r.data.paypal_order_id;
          },
          // 2) Buyer approved in PayPal: server re-validates, verifies the order, then captures.
          onApprove: async () => {
            if (!attemptId)
              return fail(
                toErr({
                  status: 0,
                  data: {
                    error: {
                      code: "NO_ATTEMPT",
                      message: "Missing payment attempt.",
                    },
                  },
                }),
              );
            const r = await call(
              "POST",
              `/api/payments/${encodeURIComponent(attemptId)}/capture`,
            );
            if (r.ok && r.data?.state === "PAID")
              return propsRef.current.onPaid(attemptId);
            const code = r.data?.error?.code;
            if (code === UNKNOWN || code === "CAPTURE_IN_PROGRESS")
              return void reconcileLoop(attemptId);
            fail(toErr(r)); // INSTRUMENT_DECLINED (retryable), APPROVAL_INVALIDATED, ORDER_MISMATCH...
          },
          onCancel: () => propsRef.current.onCancelled(),
          onError: (e: unknown) => {
            if (reported) return; // already shown by createOrder/onApprove
            fail(
              toErr({
                status: 0,
                data: { error: { code: "PAYPAL_ERROR", message: String(e) } },
              }),
            );
          },
        });
        buttons.render(boxRef.current);
      })
      .catch(() =>
        fail(
          toErr({
            status: 0,
            data: {
              error: {
                code: "PAYPAL_SDK_ERROR",
                message: "Could not load PayPal.",
              },
            },
          }),
        ),
      );

    return () => {
      cancelled = true;
      try {
        buttons?.close();
      } catch {
        /* ignore */
      }
    };
  }, []);

  return <div ref={boxRef} />;
}
