import QuotePage from "@/buyer/QuotePage";

export default async function Page({ params }: { params: Promise<{ quoteId: string }> }) {
  const { quoteId } = await params;
  return <QuotePage quoteId={quoteId} />;
}
