import { OwnerListingForm } from "@/components/owner-listing-form";
export default async function EditOwnerListingPage({ params }: { params: Promise<{ listingId: string }> }) { const { listingId } = await params; return <OwnerListingForm listingId={listingId} />; }
