import Link from "next/link";
import { ArrowRight, Calculator, Check, FileSpreadsheet, Sparkles, Target, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { PersonaTabs } from "@/components/landing/persona-tabs";
import { dollars, type PublicPlan } from "@/lib/plans";

const TRIAL_HREF = "/register?tier=trial";
const SUPPLIER_HREF = "/register?role=supplier";

const COMPARISON = [
  { label: "Manifest Processing Speed", manual: "3–6 hours per truckload", platform: "Under 45 seconds" },
  { label: "Market Data Normalization", manual: "Manual ASIN / UPC lookups", platform: "Automated multi-channel catalog matching" },
  { label: "Fee & Shipping Estimation", manual: "Rough guesswork & hidden surprises", platform: "Dynamic dimensional weight & marketplace fee calculations" },
  { label: "Capital Allocation Risk", manual: "High variance on uninspected blind lots", platform: "Quantified downside risk and yield ranges" },
];

const PIPELINE = [
  { icon: FileSpreadsheet, title: "Raw Ingest", text: "CSV / XLSX / PDF" },
  { icon: Sparkles, title: "AI Normalization & SKU Enrichment", text: "Titles, conditions and identifiers cleaned and matched" },
  { icon: Calculator, title: "Dynamic Fee, Shipping & Margin Model", text: "Marketplace fees, freight and refurbishment applied" },
  { icon: Target, title: "Actionable Decision", text: "Buy / Pass / Max Bid Ceiling" },
];

type PlanCard = { name: string; price: string; suffix: string; features: string[]; cta: string; href: string; featured: boolean };

/** Prices, credits and seats come from the backend plan catalog; only the wording lives here. */
function planCards(plans: PublicPlan[]): PlanCard[] {
  const by = Object.fromEntries(plans.map((p) => [p.key, p])) as Record<PublicPlan["key"], PublicPlan>;
  const solo = by.solo_buyer_monthly;
  const team = by.enterprise_buyer_monthly;
  const supplier = by.supplier_monthly;
  const topUp = (p: PublicPlan) => `Run out? Extra analyses at ${dollars(p.topup_price_cents, true)} each, pay as you go`;
  return [
    {
      name: solo.name,
      price: dollars(solo.price_cents),
      suffix: "/ month",
      features: [
        `${solo.monthly_credits} Full Manifest Analyses per month (up to 500 lines each)`,
        "AI supplier sourcing: we search the marketplace and the web for what you need",
        "Multi-channel margin & fee calculator (eBay, Amazon, Direct)",
        "Real-time condition grade discounting",
        "Exportable deal sheets (PDF/CSV)",
        "7-day free trial (includes 10 analyses)",
        topUp(solo),
      ],
      cta: "Start 7-Day Free Trial",
      href: TRIAL_HREF,
      featured: true,
    },
    {
      name: team.name,
      price: dollars(team.price_cents),
      suffix: "/ month",
      features: [
        `${team.monthly_credits} manifest analyses per month & batch parsing`,
        `Up to ${team.seats} team seats with role-based permissions`,
        "Custom margin rules, freight cost overlays, and API export",
        "Priority AI processing queue & priority support",
        topUp(team),
      ],
      cta: "Set Up Your Team",
      href: "/register?role=org_buyer",
      featured: false,
    },
    {
      name: supplier.name,
      price: dollars(supplier.price_cents),
      suffix: "/ month",
      features: [
        "Unlimited wholesale inventory lot listings",
        "Direct placement in Buyer Sourcing Marketplace",
        "Buyer requests matched to your inventory, no per-lead fees",
        "Verified buyer RFQ management dashboard",
        "Unlimited team seats",
        `Automated catalog standardization and batch manifest cleansing (${supplier.monthly_credits} credits a month)`,
        topUp(supplier),
      ],
      cta: "List as a Verified Supplier",
      href: SUPPLIER_HREF,
      featured: false,
    },
  ];
}

const SAMPLE_LINES = [
  ["Est. resale value", "$18,420"],
  ["Marketplace fees", "− $2,395"],
  ["Freight (LTL, 6 pallets)", "− $1,140"],
  ["Refurbishment & loss", "− $1,660"],
];

function Section({ id, className, children }: { id?: string; className?: string; children: React.ReactNode }) {
  return (
    <section id={id} className={cn("scroll-mt-16 px-5 py-16 md:py-24", className)}>
      <div className="mx-auto max-w-6xl">{children}</div>
    </section>
  );
}

function SectionHeading({ eyebrow, title }: { eyebrow: string; title: string }) {
  return (
    <div className="mx-auto mb-10 max-w-2xl text-center">
      <p className="text-sm font-medium text-primary">{eyebrow}</p>
      <h2 className="mt-2 text-balance text-2xl font-semibold tracking-tight md:text-3xl">{title}</h2>
    </div>
  );
}

function SampleDealSheet() {
  return (
    <figure className="rounded-xl border bg-card p-5 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold">Mixed returns · 412 units</p>
          <p className="text-xs text-muted-foreground">truckload-manifest.xlsx</p>
        </div>
        <span className="rounded-full bg-primary px-3 py-1 text-xs font-semibold text-primary-foreground">BUY</span>
      </div>
      <dl className="mt-4 divide-y text-sm">
        {SAMPLE_LINES.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-4 py-2">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="font-mono tabular-nums">{value}</dd>
          </div>
        ))}
        <div className="flex justify-between gap-4 py-2 font-medium">
          <dt>True net before purchase</dt>
          <dd className="font-mono tabular-nums">$13,225</dd>
        </div>
      </dl>
      <div className="mt-3 rounded-lg bg-primary/10 px-4 py-3">
        <p className="text-xs font-medium text-primary">Max bid ceiling at 35% net margin</p>
        <p className="mt-0.5 font-mono text-2xl font-semibold tabular-nums text-primary">$8,596</p>
      </div>
      <figcaption className="mt-3 text-xs text-muted-foreground">Sample deal sheet with illustrative figures.</figcaption>
    </figure>
  );
}

export function Landing({ plans }: { plans: PublicPlan[] }) {
  const cards = planCards(plans);
  return (
    <div className="bg-background">
      <header className="sticky top-0 z-40 border-b bg-background/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-5">
          <Link href="/" className="text-sm font-semibold">Sourcing Copilot</Link>
          <nav aria-label="Page" className="hidden items-center gap-6 text-sm text-muted-foreground md:flex">
            <a href="#who" className="hover:text-foreground">Who it&apos;s for</a>
            <a href="#compare" className="hover:text-foreground">Compare</a>
            <a href="#how" className="hover:text-foreground">How it works</a>
            <a href="#pricing" className="hover:text-foreground">Pricing</a>
          </nav>
          <div className="flex items-center gap-2">
            <Button asChild variant="ghost" size="sm"><Link href="/login">Sign in</Link></Button>
            <Button asChild size="sm"><Link href={TRIAL_HREF}>Start free trial</Link></Button>
          </div>
        </div>
      </header>

      <main>
        {/* Hero */}
        <section className="px-5 pb-12 pt-14 md:pb-20 md:pt-20">
          <div className="mx-auto grid max-w-6xl items-center gap-12 lg:grid-cols-[1.25fr_1fr]">
            <div>
              <Badge variant="default">AI-Powered Inventory Intelligence for Modern Sourcing</Badge>
              <h1 className="mt-5 text-balance text-3xl font-semibold leading-tight tracking-tight md:text-[2.75rem] md:leading-[1.12]">
                Stop guessing on blind pallets.{" "}
                <span className="text-muted-foreground">
                  Parse wholesale manifests, calculate true net margins, and secure profitable liquidation lots in seconds.
                </span>
              </h1>
              <p className="mt-5 max-w-xl text-base text-muted-foreground md:text-lg">
                Whether you are an independent reseller evaluating your next truckload or an enterprise procurement team
                processing millions in excess inventory, our platform turns messy spreadsheets into instant buy/pass
                decisions.
              </p>
              <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:items-start">
                <div>
                  <Button asChild size="lg" className="w-full sm:w-auto">
                    <Link href={TRIAL_HREF}>Start 7-Day Free Trial <ArrowRight className="h-4 w-4" aria-hidden /></Link>
                  </Button>
                  <p className="mt-2 text-center text-xs text-muted-foreground sm:text-left">Includes 10 free manifest analyses</p>
                </div>
                <Button asChild size="lg" variant="outline" className="w-full sm:w-auto">
                  <Link href={SUPPLIER_HREF}>List as a Verified Supplier</Link>
                </Button>
              </div>
            </div>
            <SampleDealSheet />
          </div>
        </section>

        <div className="border-y bg-card px-5 py-4">
          <p className="mx-auto max-w-6xl text-center text-sm text-muted-foreground">
            Engineered for liquidation resellers, e-commerce brands, and authorized B2B wholesale distributors.
          </p>
        </div>

        {/* Personas */}
        <Section id="who">
          <SectionHeading eyebrow="Who it's for" title="Built for every side of the liquidation trade" />
          <PersonaTabs />
        </Section>

        {/* Comparison */}
        <Section id="compare" className="border-y bg-card">
          <SectionHeading eyebrow="Compare" title="Traditional manual sourcing vs. our AI platform" />
          <div className="overflow-hidden rounded-xl border">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b bg-muted">
                  <th scope="col" className="hidden p-4 font-medium text-muted-foreground md:table-cell"><span className="sr-only">Capability</span></th>
                  <th scope="col" className="p-4 font-medium text-muted-foreground">Traditional Manual Sourcing</th>
                  <th scope="col" className="bg-primary/10 p-4 font-semibold text-primary">Our AI Platform</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {COMPARISON.map((row) => (
                  <tr key={row.label} className="align-top">
                    <th scope="row" className="hidden p-4 font-medium md:table-cell">{row.label}</th>
                    <td className="p-4 text-muted-foreground">
                      <span className="mb-1 block text-xs font-medium text-foreground md:hidden">{row.label}</span>
                      <span className="flex gap-2"><X className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden />{row.manual}</span>
                    </td>
                    <td className="bg-primary/5 p-4 font-medium">
                      <span className="mb-1 block text-xs md:hidden" aria-hidden>&nbsp;</span>
                      <span className="flex gap-2"><Check className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />{row.platform}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        {/* How it works */}
        <Section id="how">
          <SectionHeading eyebrow="How it works" title="From raw manifest to a decision in four steps" />
          <ol className="grid gap-4 md:grid-cols-4">
            {PIPELINE.map((step, i) => (
              <li key={step.title} className="relative rounded-xl border bg-card p-5">
                <div className="flex items-center gap-3">
                  <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <step.icon className="h-5 w-5" aria-hidden />
                  </span>
                  <span className="font-mono text-xs text-muted-foreground">Step {i + 1}</span>
                </div>
                <h3 className="mt-4 text-sm font-semibold">{step.title}</h3>
                <p className="mt-1 text-sm text-muted-foreground">{step.text}</p>
                {i < PIPELINE.length - 1 && (
                  <ArrowRight className="absolute -right-3.5 top-1/2 z-10 hidden h-6 w-6 -translate-y-1/2 rounded-full border bg-background p-1 text-muted-foreground md:block" aria-hidden />
                )}
              </li>
            ))}
          </ol>
        </Section>

        {/* Pricing */}
        <Section id="pricing" className="border-y bg-card">
          <SectionHeading eyebrow="Pricing" title="A plan for how you buy or sell" />
          <div className="grid gap-5 lg:grid-cols-3">
            {cards.map((plan) => (
              <div
                key={plan.name}
                className={cn("flex flex-col rounded-xl border bg-background p-6", plan.featured && "border-primary ring-1 ring-primary")}
              >
                <h3 className="text-base font-semibold">{plan.name}</h3>
                <p className="mt-3 flex flex-wrap items-baseline gap-x-2">
                  <span className="text-4xl font-semibold tracking-tight">{plan.price}</span>
                  <span className="text-sm text-muted-foreground">{plan.suffix}</span>
                </p>
                <ul className="mt-6 flex-1 space-y-3 text-sm">
                  {plan.features.map((feature) => (
                    <li key={feature} className="flex gap-2">
                      <Check className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
                      {feature}
                    </li>
                  ))}
                </ul>
                <Button asChild className="mt-8 w-full" variant={plan.featured ? "default" : "outline"}>
                  <Link href={plan.href}>{plan.cta}</Link>
                </Button>
              </div>
            ))}
          </div>
        </Section>

        {/* Closing CTA */}
        <Section>
          <div className="rounded-2xl bg-foreground px-6 py-12 text-center text-background md:px-12">
            <h2 className="mx-auto max-w-2xl text-balance text-2xl font-semibold tracking-tight md:text-3xl">
              Know your ceiling bid before the auction closes.
            </h2>
            <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Button asChild size="lg"><Link href={TRIAL_HREF}>Start 7-Day Free Trial</Link></Button>
              <Button asChild size="lg" variant="outline" className="border-background/30 bg-transparent text-background hover:bg-background/10">
                <Link href={SUPPLIER_HREF}>List as a Verified Supplier</Link>
              </Button>
            </div>
            <p className="mt-3 text-xs text-background/70">Includes 10 free manifest analyses</p>
          </div>
        </Section>
      </main>

      <footer className="border-t px-5 py-8">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-3 text-sm text-muted-foreground sm:flex-row">
          <p>© {new Date().getFullYear()} Sourcing Copilot</p>
          <div className="flex gap-5">
            <a href="#pricing" className="hover:text-foreground">Pricing</a>
            <Link href="/login" className="hover:text-foreground">Sign in</Link>
            <Link href={TRIAL_HREF} className="hover:text-foreground">Start free trial</Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
