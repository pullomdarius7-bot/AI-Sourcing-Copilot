export const CATEGORIES: Record<string, string> = {
  apparel: "Apparel",
  footwear: "Footwear",
  electronics: "Electronics",
  tools: "Tools",
  appliances: "Appliances",
  home: "Home & Kitchen",
  beauty: "Beauty & Personal Care",
  toys: "Toys",
  sports: "Sporting Goods",
  general: "General Merchandise",
};

export const CONDITIONS: Record<string, string> = {
  new: "New",
  like_new: "Like New",
  customer_return: "Customer Return",
  salvage: "Salvage",
};

export const CONDITION_COLORS: Record<string, string> = {
  new: "bg-primary",
  like_new: "bg-primary/60",
  customer_return: "bg-caution/70",
  salvage: "bg-destructive/70",
};

export const LOT_TYPES: Record<string, string> = {
  single_pallet: "Single Pallet",
  multi_pallet: "Multi-Pallet",
  truckload: "Full Truckload (FTL)",
};

export const REGIONS: Record<string, string> = {
  northeast: "Northeast",
  midwest: "Midwest",
  south: "South",
  west_coast: "West",
};

export const SHIPPING: Record<string, string> = {
  buyer_freight: "Buyer arranges freight",
  seller_freight: "Seller arranges freight",
};

export interface Seller {
  alias: string;
  label: string;
  verified: boolean;
  badges: string[];
  completed_deals: number;
  member_since: string | null;
}

export interface LotItem {
  position: number;
  title: string;
  brand: string | null;
  category: string;
  condition: string;
  upc: string | null;
  asin: string | null;
  model_number: string | null;
  quantity: number;
  unit_msrp: string | null;
  unit_price: string | null;
  source_line?: number | null;
  method?: string;
  flags?: string[];
}

export interface LotCard {
  id: string;
  title: string;
  description: string | null;
  status: string;
  lot_type: string | null;
  pallet_count: number | null;
  asking_price: string | null;
  currency: string;
  item_count: number;
  total_units: number;
  total_msrp: string | null;
  est_resale: string | null;
  est_margin_pct: string | null;
  primary_category: string | null;
  categories: string[];
  condition_mix: Record<string, number>;
  category_mix: Record<string, number>;
  region: string | null;
  ships_from: string | null;
  liftgate_required: boolean;
  dock_high_only: boolean;
  local_pickup: boolean;
  published_at: string | null;
  seller: Seller;
}

export interface AnalysisItem {
  position: number;
  title: string;
  category: string;
  condition: string;
  quantity: number;
  unit_msrp: number;
  resale_unit: number;
  resale_source: "ai" | "rules";
  velocity: string | null;
  sellable_units: number;
  revenue: number;
  fees: number;
  refurbishment: number;
  handling?: number;
  freight: number;
  cost: number;
  net_profit: number;
}

export interface Analysis {
  id: string;
  lot_id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  params: { channel: string; target_margin_pct: number; freight_cost?: number | null; handling_per_unit?: number };
  method: string | null;
  error: string | null;
  created_at: string;
  completed_at: string | null;
  result: null | {
    items: AnalysisItem[];
    totals: { units: number; msrp: number; revenue: number; fees: number; refurbishment: number; handling?: number; freight: number; cost: number; net_profit: number; roi_pct: number | null; net_margin_pct: number | null };
    max_bid: number;
    decision: "buy" | "negotiate" | "pass";
    notes: string[];
    assumptions: { channel_label: string; fee_rate_pct: number; target_margin_pct: number; freight: number; asking_price: number };
  };
}

export interface LotDetail extends LotCard {
  items: LotItem[];
  analyses: Analysis[];
  open_thread_id: string | null;
  own: boolean;
  defaults: { channel: string; target_margin_pct: number; freight_per_pallet: number; freight_truckload: number; handling_per_unit: number };
  credit_balance: number;
  ai_access: boolean;
  analysis_cost: number;
}

export interface SupplierLot {
  id: string;
  source_filename: string;
  ingest_status: "processing" | "ready" | "failed";
  ingest_method: string | null;
  ingest_error: string | null;
  ingest_flags: string[];
  status: "draft" | "published" | "sold" | "withdrawn";
  title: string;
  description: string | null;
  lot_type: string | null;
  pallet_count: number | null;
  asking_price: string | null;
  reserve_price: string | null;
  dispatch_zip: string | null;
  region: string | null;
  liftgate_required: boolean;
  dock_high_only: boolean;
  local_pickup: boolean;
  pickup_address: string | null;
  pickup_contact: string | null;
  pickup_phone: string | null;
  pickup_notes: string | null;
  item_count: number;
  total_units: number;
  total_msrp: string | null;
  est_resale: string | null;
  est_margin_pct: string | null;
  condition_mix: Record<string, number>;
  category_mix: Record<string, number>;
  open_offers: number;
  publish_problems: string[];
  created_at: string;
  published_at: string | null;
  items?: LotItem[];
  warnings?: string[];
}

export interface ThreadEvent {
  id: number;
  kind: string;
  side: "buyer" | "supplier" | "system";
  mine: boolean;
  author: string;
  offer: { price: string; shipping_terms: string; target_close_date: string; custom_terms: string | null } | null;
  body: string | null;
  flags: string[];
  created_at: string;
}

export interface ThreadSummary {
  id: string;
  lot_id: string;
  lot_title: string;
  my_side: "buyer" | "supplier";
  counterparty: Seller;
  status: "open" | "accepted" | "declined" | "withdrawn" | "closed";
  closed_reason: string | null;
  offer_by: string | null;
  offer_price: string | null;
  agreed_price: string | null;
  awaiting: "you" | "them" | null;
  last_event_at: string;
}

export interface Party {
  company: string | null;
  contact: { name: string; email: string } | null;
}

export interface Thread extends ThreadSummary {
  shipping_terms: string | null;
  target_close_date: string | null;
  custom_terms: string | null;
  revealed: null | {
    released_at: string;
    seller: Party & { pickup_address: string | null; pickup_contact: string | null; pickup_phone: string | null; pickup_notes: string | null };
    buyer: Party;
  };
  events: ThreadEvent[];
  chain_valid: boolean;
  chain_head: string | null;
  warnings: string[];
}

export function money(value: string | number | null | undefined, digits = 0) {
  if (value === null || value === undefined || value === "") return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: digits, minimumFractionDigits: digits }).format(Number(value));
}

export function pct(value: string | number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return `${Number(value).toFixed(1)}%`;
}

export function predominant(mix: Record<string, number>) {
  const total = Object.values(mix).reduce((a, b) => a + b, 0);
  const [top, units] = Object.entries(mix).sort((a, b) => b[1] - a[1])[0] ?? [];
  if (!top || !total) return "—";
  return `${Math.round((units / total) * 100)}% ${CONDITIONS[top] ?? top}`;
}

export const STATUS_LABEL: Record<string, string> = {
  open: "Open",
  accepted: "Accepted",
  declined: "Declined",
  withdrawn: "Withdrawn",
  closed: "Closed",
};

export const CLOSED_REASON: Record<string, string> = {
  below_reserve: "Below the seller's minimum",
  lot_sold: "Lot sold to another buyer",
  lot_withdrawn: "Lot withdrawn by the seller",
  declined: "Declined",
  withdrawn: "Withdrawn",
};

export function tomorrowPlus(days: number) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

export interface SourcingCandidate {
  id: string;
  kind: "marketplace" | "web";
  lot_id: string | null;
  company_name: string;
  website: string | null;
  domain: string | null;
  business_type: string | null;
  location: string | null;
  summary: string | null;
  categories: string[];
  products: { name: string; quote: string; url: string }[];
  evidence: { quote: string; url: string }[];
  moq: string | null;
  price_notes: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  contact_url: string | null;
  relevance: number;
  method: string;
  status: "new" | "added" | "dismissed";
  invite_status: string | null;
  supplier_id: string | null;
  flags: string[];
}

export interface SourcingRequest {
  id: string;
  title: string;
  description: string | null;
  category: string | null;
  condition: string | null;
  quantity: number | null;
  target_unit_price: string | null;
  region: string | null;
  needed_by: string | null;
  status: "open" | "closed";
  open_to_suppliers: boolean;
  search_web: "auto" | "always" | "never";
  web_status: "none" | "queued" | "running" | "done" | "skipped" | "failed";
  web_note: string | null;
  marketplace_matches: number;
  web_matches: number;
  invites_sent: number;
  created_at: string;
  completed_at: string | null;
}

export interface SourcingRequestDetail extends SourcingRequest {
  candidates: SourcingCandidate[];
  proposals: ThreadSummary[];
  web_search_available: boolean;
  web_search_cost: number;
  warnings: string[];
}

export interface Lead {
  id: string;
  title: string;
  description: string | null;
  category: string | null;
  condition: string | null;
  quantity: number | null;
  target_unit_price: string | null;
  region: string | null;
  needed_by: string | null;
  created_at: string;
  match: number;
  buyer: Seller;
  my_thread_id: string | null;
}

export interface PrivateLot {
  id: string;
  title: string;
  source_filename: string;
  ingest_status: "processing" | "ready" | "failed";
  ingest_error: string | null;
  asking_price: string | null;
  item_count: number;
  total_units: number;
  total_msrp: string | null;
  est_margin_pct: string | null;
  condition_mix: Record<string, number>;
  created_at: string;
  analysis: Analysis | null;
}

export const INVITE_STATUS: Record<string, string> = {
  queued: "Invited to quote",
  invited: "Invited recently",
  held: "Invitation pending",
  member: "Already on the platform",
  suppressed: "Opted out of emails",
  joined: "Joined the platform",
  no_contact: "No published email",
};

export const BUSINESS_TYPES: Record<string, string> = {
  manufacturer: "Manufacturer", wholesaler: "Wholesaler", distributor: "Distributor", liquidator: "Liquidator",
  retailer: "Retailer", marketplace: "Marketplace", other: "Business",
};
