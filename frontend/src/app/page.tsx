import type { Metadata } from "next";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { Landing } from "@/components/landing/landing";

export const metadata: Metadata = {
  title: "Sourcing Copilot | Parse liquidation manifests and know your max bid",
  description:
    "Parse wholesale manifests, calculate true net margins, and secure profitable liquidation lots in seconds. 7-day free trial with 10 manifest analyses.",
};

export default function Home() {
  // Signed-in visitors go straight to their workspace; everyone else sees the public page.
  if (cookies().get("refresh_token")) redirect("/dashboard");
  return <Landing />;
}
