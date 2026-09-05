import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { ref } from "vue";
import InactiveBundlesSection from "@/components/sections/InactiveBundlesSection.vue";

const mockItems = ref([
  {
    id: "inactive",
    record_type: "inactive_bundle" as const,
    machine_name: "inactive",
    bundle_title: "Archived bundle",
    bundle: {
      id: "inactive",
      machine_name: "inactive",
      tile_name: "Archived bundle",
      is_active: false,
      archived_at: "2026-09-01T00:00:00Z",
    },
    event: null,
  },
  {
    id: "event-1",
    record_type: "lifecycle_event" as const,
    machine_name: "current",
    bundle_title: "Current bundle",
    bundle: {
      id: "current",
      machine_name: "current",
      tile_name: "Current bundle",
      is_active: true,
    },
    event: {
      id: "event-1",
      machine_name: "current",
      bundle_title: "Current bundle",
      event_type: "extended" as const,
      observed_at: "2026-09-05T00:00:00Z",
      previous_end_at: "2026-09-04T00:00:00Z",
      new_end_at: "2026-09-10T00:00:00Z",
    },
  },
]);
const mockLoading = ref(false);
const mockError = ref<string | null>(null);
const mockRefresh = vi.fn();

vi.mock("@/composables/useBundleHistory", () => ({
  useBundleHistory: vi.fn(() => ({
    items: mockItems,
    loading: mockLoading,
    error: mockError,
    refresh: mockRefresh,
  })),
}));

describe("InactiveBundlesSection", () => {
  beforeEach(() => {
    mockItems.value = [mockItems.value[0], mockItems.value[1]];
    mockLoading.value = false;
    mockError.value = null;
    mockRefresh.mockReset();
  });

  it("lists inactive bundles and historical lifecycle records", () => {
    const wrapper = mount(InactiveBundlesSection);

    expect(wrapper.findAll(".bundle-item")).toHaveLength(2);
    expect(wrapper.text()).toContain("Archived bundle");
    expect(wrapper.text()).toContain("Current bundle");
  });

  it("shows an empty state when there are no history records", () => {
    mockItems.value = [];

    const wrapper = mount(InactiveBundlesSection);

    expect(wrapper.find(".empty-state").exists()).toBe(true);
  });
});
