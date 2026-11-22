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

  it("highlights warned bundles and explains the count discrepancy accessibly", () => {
    i18n.global.locale.value = "en";
    const warnedBundle: Bundle = {
      ...bundle,
      scrape_warnings: [{
        code: "total_items_overcount",
        reported: 24,
        parsed: 25,
        excess: 1,
      }],
    };
    const card = mount(BundleCard, { props: { bundle: warnedBundle } });
    const featured = mount(FeaturedBundle, { props: { bundle: warnedBundle } });

    for (const wrapper of [card, featured]) {
      const badge = wrapper.find(".data-warning");
      expect(badge.exists()).toBe(true);
      expect(badge.text()).toContain("Data warning");
      expect(badge.text()).toContain("Humble reports 24 · parsed 25");
      expect(badge.attributes("aria-label")).toContain(
        "Humble reports 24 items; 25 were parsed (1 extra).",
      );
    }
  });

  it("localizes the warning indicator and its explanation", () => {
    i18n.global.locale.value = "es";
    const warnedBundle: Bundle = {
      ...bundle,
      scrape_warnings: [{
        code: "total_items_overcount",
        reported: 24,
        parsed: 25,
        excess: 1,
      }],
    };
    const wrapper = mount(BundleCard, { props: { bundle: warnedBundle } });

    expect(wrapper.find(".data-warning").text()).toContain("Advertencia de datos");
    expect(wrapper.find(".data-warning").text()).toContain(
      "Humble indica 24 · procesados 25",
    );
    expect(wrapper.find(".data-warning").attributes("aria-label")).toContain(
      "Humble indica 24 elementos; se procesaron 25 (1 de más).",
    );
  });

  it("labels retained warning counts as last verified after an unverified scrape", () => {
    i18n.global.locale.value = "en";
    const unverifiedBundle: Bundle = {
      ...bundle,
      book_list: [{ machine_name: "one", title: "One book" }],
      scrape_warnings: [{
        code: "total_items_overcount",
        reported: 24,
        parsed: 25,
        excess: 1,
        status: "unverified",
        current_parsed: 1,
      }],
    };
    const card = mount(BundleCard, { props: { bundle: unverifiedBundle } });
    const featured = mount(FeaturedBundle, { props: { bundle: unverifiedBundle } });

    for (const wrapper of [card, featured]) {
      const badge = wrapper.find(".data-warning");
      expect(badge.text()).toContain(
        "Last verified: 24 reported · 25 parsed; now 1 parsed, source count unavailable",
      );
      expect(badge.attributes("aria-label")).toContain(
        "The current bundle has 1 parsed items",
      );
      expect(badge.attributes("aria-label")).toContain(
        "The previous discrepancy remains unverified.",
      );
    }
  });
});
