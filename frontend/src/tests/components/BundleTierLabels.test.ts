import { describe, expect, it, afterEach } from "vitest";
import { mount } from "@vue/test-utils";
import i18n from "@/i18n";
import type { Bundle } from "@/types/bundle";
import BundleCard from "@/components/cards/BundleCard.vue";
import FeaturedBundle from "@/components/cards/FeaturedBundle.vue";

const bundle: Bundle = {
  id: "bundle-id",
  machine_name: "bundle_machine",
  tile_name: "Example bundle",
  product_url: "https://www.humblebundle.com/books/example",
  end_date_datetime: "2099-12-31T00:00:00Z",
  price_tiers: [
    {
      identifier: "tier-5",
      header: null,
      price: { amount: 5, currency: "USD" },
    },
    {
      identifier: "tier-18",
      header: "  Premium tier  ",
      price: { amount: 18, currency: "USD" },
    },
    {
      identifier: "tier-30",
      header: "",
      price: { amount: 30, currency: "USD" },
    },
  ],
};

const formatCurrency = (amount: number) =>
  new Intl.NumberFormat(i18n.global.locale.value, {
    style: "currency",
    currency: "USD",
  }).format(amount);

describe("bundle tier labels", () => {
  afterEach(() => {
    i18n.global.locale.value = "es";
  });

  it("shows prices for missing featured-tier headers and preserves real headers", () => {
    const wrapper = mount(FeaturedBundle, { props: { bundle } });

    expect(wrapper.findAll(".chip").map((chip) => chip.text())).toEqual([
      formatCurrency(5),
      "Premium tier",
      formatCurrency(30),
    ]);
  });

  it("shows prices for missing bundle-card headers and preserves real headers", () => {
    const wrapper = mount(BundleCard, { props: { bundle } });

    expect(wrapper.findAll(".tiers span").map((chip) => chip.text())).toEqual([
      formatCurrency(5),
      "Premium tier",
    ]);
  });

  it("hides tier chips when neither a usable header nor price is available", () => {
    const noLabels: Bundle = {
      ...bundle,
      price_tiers: [
        { identifier: "tier-no-header", header: "   ", price: null },
        { identifier: "tier-no-price", header: null },
      ],
    };

    const featured = mount(FeaturedBundle, { props: { bundle: noLabels } });
    const card = mount(BundleCard, { props: { bundle: noLabels } });

    expect(featured.find(".chip-group").exists()).toBe(false);
    expect(card.find(".tiers").exists()).toBe(false);
  });

  it("formats fallback currency prices using the current locale", () => {
    i18n.global.locale.value = "en";
    const wrapper = mount(FeaturedBundle, { props: { bundle } });

    expect(wrapper.findAll(".chip")[0].text()).toBe(formatCurrency(5));
  });
});
