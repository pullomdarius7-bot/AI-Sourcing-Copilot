import type { Metadata } from "next";

export const metadata: Metadata = { title: "SourcingCopilotBot", description: "About the crawler that reads supplier websites." };

/** Linked from the crawler's user agent so website owners can see who visits and how to opt out. */
export default function BotPage() {
  return (
    <main className="mx-auto max-w-2xl space-y-4 px-5 py-16 text-sm leading-relaxed">
      <h1 className="text-2xl font-semibold">SourcingCopilotBot</h1>
      <p>
        When a business buyer on our marketplace looks for a product that no listed supplier has, we search the web for wholesalers,
        distributors and liquidators and read a few pages of their public websites (the page a search engine returned, plus contact,
        about and wholesale pages) to summarise what they sell.
      </p>
      <ul className="list-disc space-y-1 pl-5">
        <li>We honour robots.txt for the user agent <code>SourcingCopilotBot</code>.</li>
        <li>We read at most three pages per site per search, never log in, never submit forms and never download files.</li>
        <li>We only use contact details a business publishes on its own site, and we email a business at most once every 90 days.</li>
        <li>Every email has a one-click unsubscribe that stops all further emails to your address and domain.</li>
      </ul>
      <p>To stop the crawler, add to your robots.txt:</p>
      <pre className="rounded-md bg-muted p-3 text-xs">User-agent: SourcingCopilotBot{"\n"}Disallow: /</pre>
    </main>
  );
}
