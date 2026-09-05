export interface PriceTier {
  identifier: string;
  header?: string;
  is_initial?: boolean;
  price?: {
    currency: string;
    amount: number;
  };
  items?: string[];
}

export interface BookItem {
  machine_name: string;
  title?: string;
  msrp?: number | null;
  preview?: Record<string, unknown> | null;
  image?: string | null;
  content_type?: string | null;
  tiers?: string[];
}

export interface Bundle {
  id: string;
  machine_name: string;
  tile_name?: string;
  tile_short_name?: string;
  category?: string;
  tile_stamp?: string;
  tile_logo?: string | null;
  product_url?: string;
  duration_days?: number | null;
  is_active?: boolean;
  archived_at?: string | null;
  price_tiers?: PriceTier[];
  book_list?: BookItem[];
  featured_image?: string | null;
  msrp_total?: number | null;
  verification_date?: string;
  start_date_datetime?: string;
  end_date_datetime?: string;
}

export interface BundleLifecycleEvent {
  id: string;
  bundle_id?: string | null;
  machine_name: string;
  bundle_title?: string | null;
  event_type: 'extended' | 'renewed' | 'shortened' | 'reactivated';
  observed_at: string;
  previous_start_at?: string | null;
  previous_end_at?: string | null;
  new_start_at?: string | null;
  new_end_at?: string | null;
  source_snapshot_id?: string | null;
}

export interface BundleHistoryItem {
  id: string;
  record_type: 'inactive_bundle' | 'lifecycle_event';
  machine_name: string;
  bundle_title?: string | null;
  bundle?: Bundle | null;
  event?: BundleLifecycleEvent | null;
}
