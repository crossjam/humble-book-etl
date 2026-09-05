import { onMounted, ref } from 'vue';
import { get, isAxiosError } from '@/api/client';
import type { BundleLifecycleEvent } from '@/types/bundle';

export function useExtendedBundles() {
  const events = ref<BundleLifecycleEvent[]>([]);
  const loading = ref(true);
  const error = ref<string | null>(null);

  const refresh = async () => {
    loading.value = true;
    error.value = null;
    try {
      events.value = await get<BundleLifecycleEvent[]>(
        '/bundle-lifecycle-events?event_type=extended&limit=1000',
      );
    } catch (err) {
      if (isAxiosError(err)) {
        error.value = (err.response?.data as { detail?: string } | undefined)?.detail ?? err.message;
      } else {
        error.value = err instanceof Error ? err.message : 'Unable to load extended bundles.';
      }
      events.value = [];
    } finally {
      loading.value = false;
    }
  };

  onMounted(refresh);

  return { events, loading, error, refresh };
}
