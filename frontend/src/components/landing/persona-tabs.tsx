"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const PERSONAS = [
  {
    id: "solo",
    tab: "Solo Buyers & Independent Resellers",
    points: [
      {
        title: "Zero Spreadsheet Fatigue",
        copy: "Drop in unformatted CSVs, scanned PDFs, or messy auction sheets. The engine normalizes fragmented product titles, conditions, and missing identifiers automatically.",
      },
      {
        title: "True-Net Margin Modeling",
        copy: "Move beyond gross MSRP. Account for realistic secondary market velocity, platform fees (eBay, Amazon, TikTok Shop), dimensional freight tiers, and refurbishment costs before bidding.",
      },
      {
        title: "Fast-Paced Auction Edge",
        copy: "Score full truckload and pallet manifests in under 30 seconds so you can bid decisively on closing auctions without manually looking up individual SKUs.",
      },
    ],
    testimonial:
      "I used to spend 4 hours vetting a 400-unit mixed return manifest. Now I upload the sheet and know my exact ceiling bid in two minutes.",
    cta: { label: "Start 7-Day Free Trial", href: "/register?tier=trial" },
  },
  {
    id: "team",
    tab: "Enterprise Procurement Teams",
    points: [
      {
        title: "Automated Yield & Risk Scoring",
        copy: "Screen entire wholesale catalogs against historical liquidation sell-through curves, counterfeit hazard categories, and real-world condition degradation rates.",
      },
      {
        title: "Multi-Seat Workspaces",
        copy: "Share manifest evaluations across sourcing managers, assign approval thresholds, and standardize team-wide minimum margin targets.",
      },
      {
        title: "Direct Feed & EDI Ingestion",
        copy: "Connect high-volume supplier feeds directly to your internal workflow to capture mispriced lots before they hit public broker exchanges.",
      },
    ],
    testimonial: null,
    cta: { label: "Set Up Your Team", href: "/register?role=org_buyer" },
  },
  {
    id: "supplier",
    tab: "Wholesalers & Liquidation Suppliers",
    points: [
      {
        title: "Direct Access to Cash Buyers",
        copy: "Present your truckloads, overstock lots, and customer returns directly to vetted, active buyers actively purchasing your specific categories.",
      },
      {
        title: "Automated Catalog Cleansing",
        copy: "Upload raw warehouse exports. The AI engine cleans, categorizes, and structures your manifest into clean listings buyers trust, reducing disputes and pre-sale questions.",
      },
      {
        title: "Protected Deal Flow",
        copy: "Retain control over customer relationships using structured Requests for Quote (RFQs) and verified buyer qualification metrics.",
      },
    ],
    testimonial: null,
    cta: { label: "List as a Verified Supplier", href: "/register?role=supplier" },
  },
];

export function PersonaTabs() {
  const [active, setActive] = useState(0);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const persona = PERSONAS[active];

  function onKeyDown(e: React.KeyboardEvent) {
    const move = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!move) return;
    e.preventDefault();
    const next = (active + move + PERSONAS.length) % PERSONAS.length;
    setActive(next);
    tabs.current[next]?.focus();
  }

  return (
    <div>
      <div role="tablist" aria-label="Who it's for" onKeyDown={onKeyDown} className="mx-auto grid max-w-4xl gap-1 rounded-xl border bg-card p-1 md:grid-cols-3">
        {PERSONAS.map((p, i) => (
          <button
            key={p.id}
            ref={(el) => { tabs.current[i] = el; }}
            role="tab"
            id={`persona-tab-${p.id}`}
            aria-selected={i === active}
            aria-controls={`persona-panel-${p.id}`}
            tabIndex={i === active ? 0 : -1}
            onClick={() => setActive(i)}
            className={cn(
              "rounded-lg px-4 py-2.5 text-sm font-medium transition-colors",
              i === active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-secondary",
            )}
          >
            {p.tab}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`persona-panel-${persona.id}`} aria-labelledby={`persona-tab-${persona.id}`} className="mt-8">
        <div className="grid gap-4 md:grid-cols-3">
          {persona.points.map((point) => (
            <div key={point.title} className="rounded-xl border bg-card p-6">
              <h3 className="text-base font-semibold">{point.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{point.copy}</p>
            </div>
          ))}
        </div>
        {persona.testimonial && (
          <blockquote className="mx-auto mt-6 max-w-3xl rounded-xl border-l-4 border-primary bg-primary/5 px-6 py-5 text-base font-medium leading-relaxed">
            “{persona.testimonial}”
          </blockquote>
        )}
        <div className="mt-8 text-center">
          <Button asChild size="lg"><Link href={persona.cta.href}>{persona.cta.label}</Link></Button>
        </div>
      </div>
    </div>
  );
}
