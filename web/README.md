# IntentLock — Frontend (`web/`)

Owner: **Sabari** (buyer + merchant UI). Next.js 15 · TypeScript · Tailwind CSS 4.

## Run it

```bash
cd web
npm install
cp .env.example .env.local   # mock mode by default
npm run dev                  # http://localhost:3000
```

With no `NEXT_PUBLIC_API_BASE_URL`, the app uses in-browser mock data (`lib/mock.ts`) that follows the agreed API contract, so the full journey can be clicked through before the backend is ready. A **Mock data** badge shows in the header while this is on.

Set `NEXT_PUBLIC_API_BASE_URL` to the FastAPI URL and `NEXT_PUBLIC_USE_MOCKS=false` to use the real backend.

## Screens

| Route | What it does | Plan ref |
|---|---|---|
| `/` | Buyer describes requirements → "What we understood" → grounded product comparison with pass / fail / unknown checks and evidence | F1, F2 display |
| `/quote/[id]` | Exact-purchase approval card → locked approval with countdown → payment area → receipt; change explanation + valid alternatives when blocked; event timeline | F3, F8, F9 |
| `/merchant` | Change price, variant, currency or return terms; each save makes a new product version | F5 |

The quote page polls every 3 s, so a merchant change made in another tab shows up as "Payment stopped" without reloading.

## Folder ownership

```
web/
  app/        routes (thin — just render the screens below)
  buyer/      Sabari — buyer screens and components
  merchant/   Sabari — merchant panel
  payments/   Hari   — PayPal checkout component (not created here)
  lib/
    types.ts  API contract types — update here first if a backend shape changes
    api.ts    typed client; switches between real backend and mocks
    errors.ts shared error contract {code, message, retryable, request_id}
    mock.ts   dev-only mock backend (not a security boundary)
```

## For Hari — plugging in PayPal checkout

The payment frame is `buyer/components/PaymentArea.tsx`. Replace the body of `CheckoutSlot` with your component:

```tsx
import PayPalCheckout from "@/payments/PayPalCheckout";
function CheckoutSlot(props: CheckoutComponentProps) {
  return <PayPalCheckout {...props} />;
}
```

Props your component receives:

| Prop | When to call |
|---|---|
| `quoteId`, `approvalId` | Identifiers only — send these to `POST /api/payments/create`; never send amounts from the browser |
| `onPaid(attemptId)` | After capture is verified and persisted server-side. The page then shows the receipt from `GET /api/payments/{attempt_id}` |
| `onCancelled()` | Buyer closed PayPal checkout |
| `onError(ApiError)` | Any shared-contract error (`APPROVAL_EXPIRED`, `APPROVAL_INVALIDATED`, `INSTRUMENT_DECLINED`, …) |
| `onPending()` | Outcome unknown (e.g. capture timeout). The UI shows "Checking payment status", never "Paid" |

## For Vishnu — contract assumptions to confirm

The screens use the shapes in `lib/types.ts`. Things not fixed in the plan that I assumed:

- `GET /api/quotes/{id}` also returns `active_approval`, `changes[]` (field, label, before, after), `requires_new_approval`, `alternatives[]` and `payment_attempt_id`.
- `POST /api/quotes` accepts an optional `product_id` to re-quote one item (used by "Get a fresh quote" and "Quote this").
- Each quote carries `explanation`, `evidence[]` (claim + catalog source ref) and `checks[]` (requirement, wanted, offered, `pass | fail | unknown`).
- Merchant panel endpoints: `GET /api/merchant/products`, `PATCH /api/merchant/products/{id}` with `{price_minor?, currency?, variant_id?, return_days?}`, and `POST /api/merchant/reset` for the demo.
- Auth: requests are sent with `credentials: "include"`; the backend binds quotes to the user.

## Checks

```bash
npm run lint
npm run build
```
