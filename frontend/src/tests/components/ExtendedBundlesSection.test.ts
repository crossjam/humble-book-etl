import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { ref } from 'vue';
import ExtendedBundlesSection from '@/components/sections/ExtendedBundlesSection.vue';

const mockEvents = ref([
  {
    id: 'event-1',
    machine_name: 'bundle-one',
    bundle_title: 'Extended bundle',
    event_type: 'extended' as const,
    observed_at: '2026-09-05T04:00:00Z',
    previous_end_at: '2026-09-04T18:00:00Z',
    new_end_at: '2026-09-18T18:00:00Z',
  },
]);
const mockLoading = ref(false);
const mockError = ref<string | null>(null);
const mockRefresh = vi.fn();

vi.mock('@/composables/useExtendedBundles', () => ({
  useExtendedBundles: vi.fn(() => ({
    events: mockEvents,
    loading: mockLoading,
    error: mockError,
    refresh: mockRefresh,
  })),
}));

describe('ExtendedBundlesSection', () => {
  beforeEach(() => {
    mockEvents.value = [mockEvents.value[0]];
    mockLoading.value = false;
    mockError.value = null;
    mockRefresh.mockReset();
  });

  it('lists extended bundles with the previous and new end dates', () => {
    const wrapper = mount(ExtendedBundlesSection);

    expect(wrapper.findAll('.event-item')).toHaveLength(1);
    expect(wrapper.text()).toContain('Extended bundle');
    expect(wrapper.text()).toContain('Fin anterior');
    expect(wrapper.text()).toContain('Nuevo fin');
  });

  it('shows an empty state when no extensions are recorded', () => {
    mockEvents.value = [];

    const wrapper = mount(ExtendedBundlesSection);

    expect(wrapper.find('.empty-state').exists()).toBe(true);
  });
});
