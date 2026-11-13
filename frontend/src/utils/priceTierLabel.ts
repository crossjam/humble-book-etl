import type { PriceTier } from "@/types/bundle";

export function priceTierLabel(
  tier: PriceTier,
  locale: string,
): string | null {
  const header = tier.header?.trim();
  if (header) return header;

  const amount = tier.price?.amount;
  const currency = tier.price?.currency?.trim().toUpperCase();
  if (
    typeof amount !== "number" ||
    !Number.isFinite(amount) ||
    !currency ||
    !/^[A-Z]{3}$/.test(currency)
  ) {
    return null;
  }

  try {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency,
    }).format(amount);
  } catch {
    // Older browser/ICU data may not know a currency code. Still show useful
    // source data rather than rendering an empty tier chip.
    return `${currency} ${amount}`;
  }
}
