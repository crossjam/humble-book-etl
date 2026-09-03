import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { ref } from "vue";
import InactiveBundlesSection from "@/components/sections/InactiveBundlesSection.vue";

const mockBundles = ref([
  {
    id: "active",
    machine_name: "active",
    tile_name: "Current bundle",
    is_active: true,
  },
  {
    id: "inactive",
    machine_name: "inactive",
    tile_name: "Archived bundle",
    is_active: false,
    archived_at: "2026-09-01T00:00:00Z",
  },
]);
const mockLoading = ref(false);
const mockError = ref<string | null>(null);
const mockRefresh = vi.fn();

vi.mock("@/composables/useBundles", () => ({
  useBundles: vi.fn(() => ({
    bundles: mockBundles,
    loading: mockLoading,
    error: mockError,
    refresh: mockRefresh,
  })),
}));

describe("InactiveBundlesSection", () => {
  beforeEach(() => {
    mockLoading.value = false;
    mockError.value = null;
    mockRefresh.mockReset();
  });

  it("lists inactive bundles and excludes active bundles", () => {
    const wrapper = mount(InactiveBundlesSection);

    expect(wrapper.findAll(".bundle-item")).toHaveLength(1);
    expect(wrapper.text()).toContain("Archived bundle");
    expect(wrapper.text()).not.toContain("Current bundle");
  });

  it("shows an empty state when there are no inactive bundles", () => {
    mockBundles.value = [mockBundles.value[0]];

    const wrapper = mount(InactiveBundlesSection);

    expect(wrapper.find(".empty-state").exists()).toBe(true);
  });
});
